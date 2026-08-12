"""Smoke tests for the bash hooks (session-start.sh / sessionend.sh /
user-prompt-reminder.sh).

Cross-machine parity regressions: legacy `key=value` breadcrumbs, `~`-prefixed
vault paths, and CRLF breadcrumbs must resolve identically to the Python hooks
(they used to silently no-op or write to phantom `slug\r/` dirs). Context
claims must be truthful: a failed session-note write must not inject a
'created' line.
"""

import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks" / "scripts"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _run(
    script: str,
    project: Path,
    home: Path,
    *,
    stdin: str = "",
    plugin_root: bool = False,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["HOME"] = str(home)
    env["TMPDIR"] = str(home)  # keep reminder markers inside the sandbox
    env.pop("OB_VAULT", None)  # ambient override must never leak into tests
    env.pop("ADJUDANT_REMINDER_DISABLE", None)
    if plugin_root:
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    else:
        env.pop("CLAUDE_PLUGIN_ROOT", None)  # exercise the pure-bash path
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOKS / script)],
        env=env, capture_output=True, text=True,
        input=stdin if stdin else None,
        stdin=None if stdin else subprocess.DEVNULL,
        timeout=15,
    )


class TestSessionStartHook(unittest.TestCase):

    def _project(self, tmp: Path, breadcrumb: str) -> tuple[Path, Path]:
        home = tmp / "home"
        project = tmp / "code"
        vault = home / "vault"
        (vault / "projects" / "demo").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(breadcrumb.format(vault=vault))
        return project, home

    def test_voice_directive_leads_the_context_block(self):
        # The enforcement surfaces (validators, the write gate) only reach
        # files. The chat is where adjudant is actually read, and nothing was
        # setting its register: i-have-adhd ships disable-model-invocation, so
        # its rules are inert unless someone types /i-have-adhd. The hook is
        # the one thing that speaks into every session.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            r = _run("session-start.sh", project, home)
            self.assertIn("Voice", r.stdout)
            bullets = [l for l in r.stdout.splitlines() if l.startswith("- ")]
            self.assertIn("Voice", bullets[0],
                          "the directive must precede the status it governs")

    def test_voice_directive_names_the_forbidden_phrases(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            out = _run("session-start.sh", project, home).stdout
            for phrase in ("Great question", "Hope this helps"):
                self.assertIn(phrase, out)

    def test_breadcrumb_can_turn_the_voice_directive_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\nslug: demo\nvoice: off\n")
            r = _run("session-start.sh", project, home)
            self.assertNotIn("Voice", r.stdout)
            self.assertIn("Vault:", r.stdout)   # the rest of the block survives

    def test_env_var_can_turn_the_voice_directive_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            r = _run("session-start.sh", project, home,
                     extra_env={"ADJUDANT_VOICE_DISABLE": "1"})
            self.assertNotIn("Voice", r.stdout)

    def test_voice_directive_stays_within_its_token_budget(self):
        # It loads on every session, on top of a context block that already
        # costs. voice.md is capped at 600 tokens for the same reason and had
        # 7 characters of headroom; this must not quietly become a second
        # uncapped doc.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            out = _run("session-start.sh", project, home).stdout
            line = next(l for l in out.splitlines() if "Voice" in l)
            self.assertLess(len(line) // 4, 120,
                            f"voice directive is ~{len(line) // 4} tok, budget 120")

    def test_colon_breadcrumb_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault:", r.stdout)

    def test_legacy_equals_breadcrumb_resolves(self):
        # Pre-v0.4.0 `key=value` — the Python hooks accepted it, bash did not.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path={vault}\nslug=demo\n")
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault:", r.stdout)

    def test_tilde_vault_path_expands(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: ~/vault\nslug: demo\n")
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault:", r.stdout)

    def test_stale_vault_path_silently_noops(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: /nope/nowhere\nslug: demo\n")
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")  # no context block, no crash

    def test_crlf_breadcrumb_creates_no_phantom_cr_dir(self):
        # A CRLF breadcrumb (Windows-side edit / sync round-trip) used to leak
        # \r into the slug, creating a phantom `projects/demo\r/` dir while the
        # Python hooks wrote to the real `projects/demo/`.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\r\nslug: demo\r\n")
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault:", r.stdout)
            projects = home / "vault" / "projects"
            self.assertEqual([d.name for d in projects.iterdir()], ["demo"])
            self.assertTrue(
                (projects / "demo" / "sessions" / f"{date.today().isoformat()}.md").is_file())

    def test_failed_write_never_claims_creation(self):
        # Read-only sessions dir: the note can't be written — the context
        # stream must not claim 'Session note created'.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            sessions = home / "vault" / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            sessions.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x: no writes
            try:
                r = _run("session-start.sh", project, home)
                self.assertEqual(r.returncode, 0)
                self.assertIn("Vault:", r.stdout)  # context block still injected
                self.assertNotIn("Session note created", r.stdout)
                self.assertNotIn("Session note resumed", r.stdout)
            finally:
                sessions.chmod(stat.S_IRWXU)

    def test_second_start_resumes_not_truncates(self):
        # Same-day second SessionStart must resume (append), never truncate.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            r1 = _run("session-start.sh", project, home)
            self.assertIn("Session note created", r1.stdout)
            session_file = (home / "vault" / "projects" / "demo" / "sessions"
                            / f"{date.today().isoformat()}.md")
            session_file.write_text(session_file.read_text() + "- 10:00 · precious entry\n")
            r2 = _run("session-start.sh", project, home)
            self.assertIn("Session note resumed", r2.stdout)
            content = session_file.read_text()
            self.assertIn("precious entry", content)      # first note preserved
            self.assertIn("session resumed", content)

    def test_stale_path_with_vault_name_falls_back_via_resolver(self):
        # Legacy `=` breadcrumb whose absolute path is from the other machine:
        # with the plugin root available, resolve_vault's vault_name step must
        # find the vault under a standard location on THIS machine.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            vault = home / "Documents" / "MyVault"
            (vault / "projects" / "demo").mkdir(parents=True)
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text(
                "vault_path=/other-machine/vault\nvault_name=MyVault\nslug=demo\n")
            r = _run("session-start.sh", project, home, plugin_root=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("MyVault", r.stdout)
            self.assertTrue(
                (vault / "projects" / "demo" / "sessions"
                 / f"{date.today().isoformat()}.md").is_file())

    def test_ob_vault_override_in_pure_bash_mode(self):
        # Degraded (no plugin root) mode must still honor OB_VAULT — parity
        # with resolve_vault's step 1.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: /nope/nowhere\nslug: demo\n")
            override = Path(tmp) / "override-vault"
            (override / "projects" / "demo").mkdir(parents=True)
            r = _run("session-start.sh", project, home,
                     extra_env={"OB_VAULT": str(override)})
            self.assertEqual(r.returncode, 0)
            self.assertIn("override-vault", r.stdout)

    def _existing_note(self, home: Path) -> Path:
        session_file = (home / "vault" / "projects" / "demo" / "sessions"
                        / f"{date.today().isoformat()}.md")
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(
            "---\ntype: session\n---\n\n> {One-line intent. Frozen after first write.}\n\n"
            "## Log\n\n- 09:00 · session started\n")
        return session_file

    def test_compact_source_appends_no_resume_marker(self):
        # SessionStart fires on source=compact too; the precompact hook already
        # wrote a paused tombstone, so a resumed marker is pure double noise.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            note = self._existing_note(home)
            r = _run("session-start.sh", project, home,
                     stdin=json.dumps({"session_id": "s1", "source": "compact"}))
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("session resumed", note.read_text())

    def test_clear_source_appends_no_resume_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            note = self._existing_note(home)
            r = _run("session-start.sh", project, home,
                     stdin=json.dumps({"session_id": "s1", "source": "clear"}))
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("session resumed", note.read_text())

    def test_resume_source_appends_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            note = self._existing_note(home)
            r = _run("session-start.sh", project, home,
                     stdin=json.dumps({"session_id": "s1", "source": "resume"}))
            self.assertEqual(r.returncode, 0)
            self.assertIn("session resumed", note.read_text())

    def test_session_start_no_longer_nags_about_the_intent_line(self):
        # The nag moved to UserPromptSubmit: at SessionStart there is no
        # purpose to record yet, and this hook re-runs on resume and compact,
        # so it fired early and repeatedly. See test_user_prompt_reminder.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            r = _run("session-start.sh", project, home,
                     stdin='{"session_id":"sess-x","source":"startup"}')
            self.assertNotIn("Intent line is still the placeholder", r.stdout)

    def test_session_start_hands_the_session_path_to_the_per_turn_hook(self):
        # The per-turn hook cannot re-derive this without a second copy of the
        # zone-aware lookup, and two copies drift.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            _run("session-start.sh", project, home,
                 stdin='{"session_id":"sess-x","source":"startup"}')
            pointer = home / "adjudant-session-sess-x"
            self.assertTrue(pointer.is_file(), "no session pointer written")
            self.assertEqual(
                pointer.read_text().strip(),
                str(home / "vault" / "projects" / "demo" / "sessions"
                    / f"{date.today().isoformat()}.md"))

    # --- ambient board: counts line ---

    def _deck(self, home: Path, cards: list) -> Path:
        board = home / "vault" / "projects" / "demo" / "board"
        board.mkdir(parents=True, exist_ok=True)
        deck = board / "board-data.json"
        deck.write_text(json.dumps({
            "version": 1, "boardId": "demo", "title": "demo",
            "updated": "2026-07-20",
            "columns": [{"id": c, "name": c.title()} for c in
                        ("backlog", "next", "doing", "review", "done", "icebox")],
            "cards": cards,
        }))
        return deck

    def test_sessionstart_board_line(self):
        # A deck on disk yields one counts line in canonical status order
        # (todo/doing/review/blocked/done/icebox); backlog and next both
        # feed the todo slot since neither is started work.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            self._deck(home, [
                {"id": "a", "column": "backlog"},
                {"id": "b", "column": "next"},
                {"id": "c", "column": "doing"},
                {"id": "d", "column": "review"},
                {"id": "e", "column": "done"},
                {"id": "f", "column": "done"},
                {"id": "g", "column": "icebox"},
            ])
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("- Board: 2/1/1/0/2/1", r.stdout)
            self.assertNotIn("stale", r.stdout)

    def test_sessionstart_no_board_no_line(self):
        # No deck file: the block renders as before, no board line at all.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault:", r.stdout)
            self.assertNotIn("- Board:", r.stdout)

    def test_sessionstart_board_stale_flag(self):
        # Any task note with an mtime newer than the deck file flags the
        # line stale (the deck predates the tasks it should reflect).
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            deck = self._deck(home, [{"id": "a", "column": "backlog"}])
            past = time.time() - 100
            os.utime(deck, (past, past))
            tasks = home / "vault" / "projects" / "demo" / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "fix-thing.md").write_text(
                "---\ntype: task\nstatus: todo\n---\n\n## Task\n")
            r = _run("session-start.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("- Board: 1/0/0/0/0/0 · stale", r.stdout)

class TestSessionEndHook(unittest.TestCase):

    def test_stale_vault_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir()
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text(
                f"vault_path: {tmp}/gone/vault\nslug: demo\n")
            r = _run("sessionend.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertFalse((Path(tmp) / "gone").exists())

    def test_tilde_vault_appends_session_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            sessions = home / "vault" / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            session_file = sessions / f"{date.today().isoformat()}.md"
            session_file.write_text("## Log\n")
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text("vault_path: ~/vault\nslug: demo\n")
            r = _run("sessionend.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("session ended", session_file.read_text())

    def test_no_ended_marker_when_nothing_logged_since_start(self):
        # A quick open/close session must not stack "session ended" under a
        # bare "session started": the pair is pure churn.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            sessions = home / "vault" / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            session_file = sessions / f"{date.today().isoformat()}.md"
            session_file.write_text("## Log\n\n- 10:00 · session started\n")
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text("vault_path: ~/vault\nslug: demo\n")
            r = _run("sessionend.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("session ended", session_file.read_text())

    def test_ended_marker_lands_after_real_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            sessions = home / "vault" / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            session_file = sessions / f"{date.today().isoformat()}.md"
            session_file.write_text(
                "## Log\n\n- 10:00 · session started\n- 10:20 · Added: [[x]]\n")
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text("vault_path: ~/vault\nslug: demo\n")
            r = _run("sessionend.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("session ended", session_file.read_text())

    def test_no_double_ended_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            sessions = home / "vault" / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            session_file = sessions / f"{date.today().isoformat()}.md"
            session_file.write_text("## Log\n\n- 10:20 · Added: [[x]]\n- 10:30 · session ended\n")
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text("vault_path: ~/vault\nslug: demo\n")
            r = _run("sessionend.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(session_file.read_text().count("session ended"), 1)

    # --- ambient board: session-end task bridge + reseed ---

    def _linked_project(self, tmp: Path) -> tuple[Path, Path, Path]:
        """Breadcrumbed project + vault project dir. Returns
        (project, home, vault_project)."""
        home = tmp / "home"
        vault_project = home / "vault" / "projects" / "demo"
        vault_project.mkdir(parents=True)
        project = tmp / "code"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {home / 'vault'}\nslug: demo\n")
        return project, home, vault_project

    def test_sessionend_ledger_bridges_survivors(self):
        # A ledger for this session exists in TMPDIR: survivors become task
        # notes and the board is born, all from one SessionEnd.
        with tempfile.TemporaryDirectory() as tmp:
            project, home, vault_project = self._linked_project(Path(tmp))
            ledger = home / "adjudant-task-ledger-sess-bridge.jsonl"
            ledger.write_text(
                json.dumps({"id": "T-1", "subject": "Fix the widget",
                            "status": "created", "ts": "2026-07-21T10:00:00",
                            "description": "Make it stop rattling"}) + "\n"
                + json.dumps({"id": "T-2", "subject": "Old chore",
                              "status": "completed", "ts": "2026-07-21T10:05:00",
                              "description": ""}) + "\n")
            r = _run("sessionend.sh", project, home, plugin_root=True,
                     stdin=json.dumps({"session_id": "sess-bridge",
                                       "hook_event_name": "SessionEnd"}))
            self.assertEqual(r.returncode, 0)
            note = vault_project / "tasks" / "fix-the-widget.md"
            self.assertTrue(note.is_file())
            text = note.read_text()
            self.assertIn("status: todo", text)
            self.assertIn("Make it stop rattling", text)
            self.assertFalse((vault_project / "tasks" / "old-chore.md").exists())
            deck = json.loads(
                (vault_project / "board" / "board-data.json").read_text())
            self.assertIn("fix-the-widget", [c["id"] for c in deck["cards"]])

    def test_sessionend_no_ledger_reseeds_existing_board(self):
        # No ledger file for the session: an existing board still gets the
        # ensure-only reseed, picking up task notes the deck predates.
        with tempfile.TemporaryDirectory() as tmp:
            project, home, vault_project = self._linked_project(Path(tmp))
            tasks = vault_project / "tasks"
            tasks.mkdir()
            (tasks / "one-task.md").write_text(
                "---\ntype: task\nstatus: todo\n---\n\n## Task\n")
            board = vault_project / "board"
            board.mkdir()
            (board / "board-data.json").write_text(json.dumps({
                "version": 1, "boardId": "demo", "title": "Demo",
                "subtitle": "Work-order board", "updated": "2020-01-01",
                "columns": [{"id": c, "name": c.title()} for c in
                            ("backlog", "next", "doing", "review", "done", "icebox")],
                "categories": ["build"], "cards": [],
            }))
            r = _run("sessionend.sh", project, home, plugin_root=True,
                     stdin=json.dumps({"session_id": "sess-noledger",
                                       "hook_event_name": "SessionEnd"}))
            self.assertEqual(r.returncode, 0)
            deck = json.loads((board / "board-data.json").read_text())
            self.assertIn("one-task", [c["id"] for c in deck["cards"]])

    def test_sessionend_no_ledger_no_board_writes_nothing(self):
        # Neither a ledger nor a board: SessionEnd alone must not birth one
        # (birth needs a bridged survivor or an explicit ensure elsewhere).
        with tempfile.TemporaryDirectory() as tmp:
            project, home, vault_project = self._linked_project(Path(tmp))
            tasks = vault_project / "tasks"
            tasks.mkdir()
            (tasks / "one-task.md").write_text(
                "---\ntype: task\nstatus: todo\n---\n\n## Task\n")
            r = _run("sessionend.sh", project, home, plugin_root=True,
                     stdin=json.dumps({"session_id": "sess-neither",
                                       "hook_event_name": "SessionEnd"}))
            self.assertEqual(r.returncode, 0)
            self.assertFalse((vault_project / "board").exists())

    def test_midnight_straddle_appends_to_latest_note(self):
        # No note exists for *today* (session started before midnight): the
        # end marker must land in the latest existing daily note, not vanish.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            sessions = home / "vault" / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            older = sessions / "2020-01-01.md"
            newer = sessions / "2020-01-02.md"
            decoy = sessions / "abcd-ef-gh.md"  # 4-2-2 shape, not a date
            older.write_text("## Log\n")
            newer.write_text("## Log\n")
            decoy.write_text("## Not a session\n")
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text("vault_path: ~/vault\nslug: demo\n")
            r = _run("sessionend.sh", project, home)
            self.assertEqual(r.returncode, 0)
            self.assertIn("session ended", newer.read_text())
            self.assertNotIn("session ended", older.read_text())
            self.assertNotIn("session ended", decoy.read_text())  # digit glob


class TestUserPromptReminder(unittest.TestCase):

    def _payload(self, prompt: str, session_id: str = "sess-123") -> str:
        return json.dumps({"session_id": session_id, "prompt": prompt})

    def _unlinked_project(self, tmp: Path) -> tuple[Path, Path]:
        home = tmp / "home"; home.mkdir()
        project = tmp / "code"; project.mkdir()
        return project, home

    def test_fires_on_vaulty_prompt_when_unlinked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._unlinked_project(Path(tmp))
            r = _run("user-prompt-reminder.sh", project, home,
                     stdin=self._payload("note this decision in the vault"))
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault not linked", r.stdout)

    def test_silent_on_unrelated_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._unlinked_project(Path(tmp))
            r = _run("user-prompt-reminder.sh", project, home,
                     stdin=self._payload("fix the css on the landing page"))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")

    def test_silent_when_project_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._unlinked_project(Path(tmp))
            (project / ".claude").mkdir()
            (project / ".claude" / "adjudant").write_text("slug: demo\n")
            r = _run("user-prompt-reminder.sh", project, home,
                     stdin=self._payload("record this in the vault"))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")

    def test_fires_once_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._unlinked_project(Path(tmp))
            r1 = _run("user-prompt-reminder.sh", project, home,
                      stdin=self._payload("vault please", session_id="s-once"))
            self.assertIn("Vault not linked", r1.stdout)
            r2 = _run("user-prompt-reminder.sh", project, home,
                      stdin=self._payload("vault again", session_id="s-once"))
            self.assertEqual(r2.stdout, "")  # suppressed for the same session
            r3 = _run("user-prompt-reminder.sh", project, home,
                      stdin=self._payload("vault anew", session_id="s-other"))
            self.assertIn("Vault not linked", r3.stdout)  # new session fires


class TestZoneAwareness(unittest.TestCase):
    """Audit 2026-07-27 (all three agents found this independently).

    `/adjudant shelf` moves a project to projects/_fridge/<slug> or
    projects/_archive/<slug> and never touches the repo breadcrumb. The hooks
    hardcoded projects/<slug>, so the next session built a GHOST twin in the
    active zone and wrote notes there forever while the real project went
    untouched. Hooks must resolve across zones and no-op when the project
    does not exist at all.
    """

    def _shelved(self, tmp: Path, zone: str = "_fridge") -> tuple[Path, Path, Path]:
        home = tmp / "home"
        project = tmp / "code"
        vault = home / "vault"
        proot = vault / "projects" / zone / "demo"
        (proot / "sessions").mkdir(parents=True)
        (proot / "brief.md").write_text(
            "---\ntype: project\nslug: demo\nstatus: paused\n---\n\n# Demo\n")
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nslug: demo\nmode: project\n")
        return project, home, proot

    def test_session_start_writes_into_fridge_not_a_ghost(self):
        for plugin_root in (True, False):
            with self.subTest(python_shim=plugin_root):
                with tempfile.TemporaryDirectory() as tmp:
                    project, home, proot = self._shelved(Path(tmp))
                    r = _run("session-start.sh", project, home,
                             plugin_root=plugin_root)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    today = date.today().isoformat()
                    self.assertTrue(
                        (proot / "sessions" / f"{today}.md").is_file(),
                        "session note must land in the shelved project")
                    ghost = home / "vault" / "projects" / "demo"
                    self.assertFalse(ghost.exists(),
                                     "no phantom active-zone project may be created")

    def test_session_pointer_names_the_zone_aware_path(self):
        # The zone conversion once missed the intent nudge: it said
        # projects/<slug>/sessions/, a path that does not exist for a shelved
        # project. The nudge moved to the per-turn hook, so the guard moves to
        # the pointer it now reads - same bug, same shape, new address.
        with tempfile.TemporaryDirectory() as tmp:
            project, home, _ = self._shelved(Path(tmp))
            r = _run("session-start.sh", project, home, plugin_root=True,
                     stdin='{"session_id":"sess-z","source":"startup"}')
            self.assertEqual(r.returncode, 0, r.stderr)
            today = date.today().isoformat()
            pointed = (home / "adjudant-session-sess-z").read_text().strip()
            self.assertTrue(pointed.endswith(
                f"projects/_fridge/demo/sessions/{today}.md"), pointed)
            self.assertNotIn("/projects/demo/sessions/", pointed)

    def test_session_start_archive_zone(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home, proot = self._shelved(Path(tmp), zone="_archive")
            r = _run("session-start.sh", project, home, plugin_root=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            today = date.today().isoformat()
            self.assertTrue((proot / "sessions" / f"{today}.md").is_file())
            self.assertFalse((home / "vault" / "projects" / "demo").exists())

    def test_session_start_unknown_project_creates_nothing(self):
        # Breadcrumb points at a project that exists in NO zone (never
        # connected, or deleted): the hook must not materialize it.
        for plugin_root in (True, False):
            with self.subTest(python_shim=plugin_root):
                with tempfile.TemporaryDirectory() as tmp:
                    tmpp = Path(tmp)
                    home = tmpp / "home"
                    project = tmpp / "code"
                    vault = home / "vault"
                    (vault / "projects").mkdir(parents=True)
                    (project / ".claude").mkdir(parents=True)
                    (project / ".claude" / "adjudant").write_text(
                        f"vault_path: {vault}\nslug: ghosttown\nmode: project\n")
                    r = _run("session-start.sh", project, home,
                             plugin_root=plugin_root)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertFalse((vault / "projects" / "ghosttown").exists(),
                                     "unconnected project must not be created by a hook")

    def test_session_start_active_zone_still_works(self):
        # Guard against over-correction: the ordinary case must be untouched.
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            home = tmpp / "home"
            project = tmpp / "code"
            vault = home / "vault"
            proot = vault / "projects" / "demo"
            proot.mkdir(parents=True)
            (proot / "brief.md").write_text(
                "---\ntype: project\nslug: demo\nstatus: active\n---\n\n# Demo\n")
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nslug: demo\nmode: project\n")
            r = _run("session-start.sh", project, home, plugin_root=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            today = date.today().isoformat()
            self.assertTrue((proot / "sessions" / f"{today}.md").is_file())

    def test_sessionend_appends_into_fridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home, proot = self._shelved(Path(tmp))
            today = date.today().isoformat()
            note = proot / "sessions" / f"{today}.md"
            note.write_text("## Log\n")
            r = _run("sessionend.sh", project, home, plugin_root=True,
                     stdin=json.dumps({"reason": "clear"}))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("session ended", note.read_text())
            self.assertFalse((home / "vault" / "projects" / "demo").exists())


if __name__ == "__main__":
    unittest.main()
