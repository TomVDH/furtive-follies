"""Smoke tests for the bash hooks (session-start.sh / sessionend.sh /
user-prompt-reminder.sh).

Cross-machine parity regressions: legacy `key=value` breadcrumbs, `~`-prefixed
vault paths, and CRLF breadcrumbs must resolve identically to the Python hooks
(they used to silently no-op or write to phantom `slug\r/` dirs). Since v3 the
SessionStart hook creates nothing, so the resolved path is read off the session
pointer it hands the per-turn hook, and SessionEnd appends no marker, so its
vault-touching lane is the board reseed.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

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

    def test_advisor_banner_appears_when_the_knob_is_on(self):
        # v2: the advisor is opt-in, but an opted-in session must be made
        # acutely aware at start/resume - the banner is that awareness, and
        # it points at the contract the model is now under.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\nslug: demo\nadvisor: on\n")
            out = _run("session-start.sh", project, home).stdout
            self.assertIn("Advisor: on", out)
            self.assertIn("reference/advisor.md", out)

    def test_advisor_silent_when_off_or_unset(self):
        # Opt-in means opt-in: no knob, no banner, no half-presence.
        for crumb in ("vault_path: {vault}\nslug: demo\n",
                      "vault_path: {vault}\nslug: demo\nadvisor: off\n"):
            with tempfile.TemporaryDirectory() as tmp:
                project, home = self._project(Path(tmp), crumb)
                out = _run("session-start.sh", project, home).stdout
                self.assertNotIn("Advisor", out)

    def test_advisor_banner_follows_the_voice_directive(self):
        # Voice governs how everything is said, advisor what gets noticed:
        # the register comes first.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\nslug: demo\nadvisor: on\n")
            out = _run("session-start.sh", project, home).stdout
            bullets = [l for l in out.splitlines() if l.startswith("- ")]
            self.assertIn("Voice", bullets[0])
            self.assertIn("Advisor", bullets[1])

    def test_advisor_banner_stays_within_its_token_budget(self):
        # Same discipline as the voice directive: it loads every opted-in
        # session, so it must not grow one useful sentence at a time.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\nslug: demo\nadvisor: on\n")
            out = _run("session-start.sh", project, home).stdout
            line = next(l for l in out.splitlines() if "Advisor" in l)
            self.assertLess(len(line) // 4, 120,
                            f"advisor banner is ~{len(line) // 4} tok, budget 120")

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
        # Python hooks wrote to the real `projects/demo/`. v3 creates no note
        # here, so the resolved path is read off the session pointer instead —
        # same bug, same shape, new address.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\r\nslug: demo\r\n")
            r = _run("session-start.sh", project, home,
                     stdin='{"session_id":"sess-crlf","source":"startup"}')
            self.assertEqual(r.returncode, 0)
            self.assertIn("Vault:", r.stdout)
            projects = home / "vault" / "projects"
            self.assertEqual([d.name for d in projects.iterdir()], ["demo"])
            self.assertEqual(
                (home / "adjudant-session-sess-crlf").read_text().strip(),
                str(projects / "demo" / "sessions"
                    / f"{date.today().isoformat()}.md"))

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
            r = _run("session-start.sh", project, home, plugin_root=True,
                     stdin='{"session_id":"sess-fb","source":"startup"}')
            self.assertEqual(r.returncode, 0)
            self.assertIn("MyVault", r.stdout)
            self.assertEqual(
                (home / "adjudant-session-sess-fb").read_text().strip(),
                str(vault / "projects" / "demo" / "sessions"
                    / f"{date.today().isoformat()}.md"))

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

    def test_session_start_creates_no_note(self):
        # v3: a session that does no vault work leaves no trace. 76 of 261
        # notes in the real vault were start/end markers and nothing else.
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\nvault_name: vault\nslug: demo\n")
            r = _run("session-start.sh", project, home,
                     stdin=json.dumps({"session_id": "s1", "source": "startup"}))
            self.assertEqual(r.returncode, 0)
            notes = list((home / "vault" / "projects" / "demo" / "sessions").glob("*.md"))
            self.assertEqual(notes, [], f"session-start created {notes}")

    def test_session_start_appends_no_resume_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\nvault_name: vault\nslug: demo\n")
            sessions = home / "vault" / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            note = sessions / f"{date.today().isoformat()}.md"
            note.write_text("---\ntype: session\n---\n\n## Log\n\n- 09:00 · a.md written\n")
            before = note.read_text()
            _run("session-start.sh", project, home,
                 stdin=json.dumps({"session_id": "s2", "source": "resume"}))
            self.assertEqual(note.read_text(), before,
                             "session-start still writes into the note")

    # --- ambient board: counts line + suitcase pointer ---

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

    def test_sessionstart_suitcase_pointer_startup_only(self):
        # The pointer needs BOTH signals: payload source=startup AND a
        # suitcase-brief executable on PATH. PATH-injected fake for the
        # positive cases; the negative case scrubs PATH to /usr/bin:/bin
        # because a real suitcase-brief install must not leak in.
        #
        # The banner is a capability, and a build that does not declare the
        # capability is meant to print nothing. Asserting the line here would
        # fail the public build for behaving exactly as its profile says.
        import _profile
        if not any(c["probe"] == "suitcase-brief"
                   for c in _profile.capabilities()):
            self.skipTest("this build declares no suitcase capability")
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(Path(tmp), "vault_path: {vault}\nslug: demo\n")
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            fake = fake_bin / "suitcase-brief"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            with_fake = f"{fake_bin}:/usr/bin:/bin"
            without_fake = "/usr/bin:/bin"

            r1 = _run("session-start.sh", project, home,
                      stdin=json.dumps({"session_id": "s1", "source": "startup"}),
                      extra_env={"PATH": with_fake})
            self.assertEqual(r1.returncode, 0)
            pointer = [l for l in r1.stdout.splitlines() if "Suitcase detected" in l]
            self.assertEqual(len(pointer), 1)  # exactly one line, never a block
            self.assertIn("suitcase-brief", pointer[0])

            r2 = _run("session-start.sh", project, home,
                      stdin=json.dumps({"session_id": "s1", "source": "resume"}),
                      extra_env={"PATH": with_fake})
            self.assertEqual(r2.returncode, 0)
            self.assertNotIn("Suitcase detected", r2.stdout)

            r3 = _run("session-start.sh", project, home,
                      stdin=json.dumps({"session_id": "s1", "source": "startup"}),
                      extra_env={"PATH": without_fake})
            self.assertEqual(r3.returncode, 0)
            self.assertNotIn("Suitcase detected", r3.stdout)

    def test_session_start_states_the_register_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, home = self._project(
                Path(tmp), "vault_path: {vault}\nvault_name: vault\nslug: demo\n")
            r = _run("session-start.sh", project, home,
                     stdin=json.dumps({"session_id": "s1", "source": "startup"}))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.count("ASD-STE100"), 1)


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

    def test_session_end_appends_no_marker(self):
        # v3: the end marker is gone, and with it the guard that suppressed
        # exactly one of started/resumed/paused/ended when the tail was
        # already a marker. A session note records work; session end is not
        # work, so the note comes out byte-identical either way.
        for seed in ("## Log\n\n- 09:00 · a.md written\n",
                     "## Log\n\n- 10:00 · session started\n"):
            with self.subTest(seed=seed):
                with tempfile.TemporaryDirectory() as tmp:
                    home = Path(tmp) / "home"
                    sessions = home / "vault" / "projects" / "demo" / "sessions"
                    sessions.mkdir(parents=True)
                    note = sessions / f"{date.today().isoformat()}.md"
                    note.write_text(seed)
                    project = Path(tmp) / "code"
                    (project / ".claude").mkdir(parents=True)
                    (project / ".claude" / "adjudant").write_text(
                        "vault_path: ~/vault\nslug: demo\n")
                    r = _run("sessionend.sh", project, home,
                             stdin=json.dumps({"session_id": "s1"}))
                    self.assertEqual(r.returncode, 0)
                    self.assertEqual(note.read_text(), seed)

    # --- ambient board: session-end reseed ---

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

    def test_sessionend_no_ledger_reseeds_existing_board(self):
        # An existing board gets the ensure-only reseed at session end,
        # picking up task notes the deck predates. Since v3 that is the
        # hook's only board work: no ledger is ever replayed into tasks/.
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
        # Task notes but no board: SessionEnd alone must not birth one
        # (birth needs an explicit ensure elsewhere).
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

    def test_session_start_resolves_into_fridge_not_a_ghost(self):
        # v3 creates no note here, so the resolved path is read off the session
        # pointer. Both modes: the pure-bash zone walk must agree with the shim.
        for plugin_root in (True, False):
            with self.subTest(python_shim=plugin_root):
                with tempfile.TemporaryDirectory() as tmp:
                    project, home, proot = self._shelved(Path(tmp))
                    r = _run("session-start.sh", project, home,
                             plugin_root=plugin_root,
                             stdin='{"session_id":"sess-f","source":"startup"}')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    today = date.today().isoformat()
                    self.assertEqual(
                        (home / "adjudant-session-sess-f").read_text().strip(),
                        str(proot / "sessions" / f"{today}.md"),
                        "the resolved path must name the shelved project")
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
            r = _run("session-start.sh", project, home, plugin_root=True,
                     stdin='{"session_id":"sess-a","source":"startup"}')
            self.assertEqual(r.returncode, 0, r.stderr)
            today = date.today().isoformat()
            self.assertEqual(
                (home / "adjudant-session-sess-a").read_text().strip(),
                str(proot / "sessions" / f"{today}.md"))
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
            r = _run("session-start.sh", project, home, plugin_root=True,
                     stdin='{"session_id":"sess-act","source":"startup"}')
            self.assertEqual(r.returncode, 0, r.stderr)
            today = date.today().isoformat()
            self.assertEqual(
                (home / "adjudant-session-sess-act").read_text().strip(),
                str(proot / "sessions" / f"{today}.md"))

    def test_sessionend_reseeds_the_board_in_the_fridge(self):
        # v3 sessionend writes no marker, so the ghost-twin guard rides on the
        # lane that still reaches the vault: the board reseed.
        with tempfile.TemporaryDirectory() as tmp:
            project, home, proot = self._shelved(Path(tmp))
            (proot / "tasks").mkdir()
            (proot / "tasks" / "one-task.md").write_text(
                "---\ntype: task\nstatus: todo\n---\n\n## Task\n")
            board = proot / "board"
            board.mkdir()
            (board / "board-data.json").write_text(json.dumps({
                "version": 1, "boardId": "demo", "title": "Demo",
                "subtitle": "Work-order board", "updated": "2020-01-01",
                "columns": [{"id": c, "name": c.title()} for c in
                            ("backlog", "next", "doing", "review", "done", "icebox")],
                "categories": ["build"], "cards": [],
            }))
            r = _run("sessionend.sh", project, home, plugin_root=True,
                     stdin=json.dumps({"reason": "clear"}))
            self.assertEqual(r.returncode, 0, r.stderr)
            deck = json.loads((board / "board-data.json").read_text())
            self.assertIn("one-task", [c["id"] for c in deck["cards"]])
            self.assertFalse((home / "vault" / "projects" / "demo").exists())


class TestZoneWalkCoversTheFourFolders(unittest.TestCase):
    """Both shell hooks carry their own copy of find_project_dir, because a
    python shim would cost a subprocess on a hook that fires every session.
    Two copies drift, so this test reads both."""

    HOOKS = Path(__file__).resolve().parent.parent / "hooks" / "scripts"

    def test_both_hooks_list_all_four_folders(self):
        for name in ("session-start.sh", "sessionend.sh"):
            text = (self.HOOKS / name).read_text()
            self.assertIn('local zones="active paused finished archive"', text,
                          f"{name} does not probe the four lifecycle folders")

    def test_both_hooks_still_probe_the_legacy_shapes(self):
        for name in ("session-start.sh", "sessionend.sh"):
            text = (self.HOOKS / name).read_text()
            self.assertIn('local legacy="_fridge _archive"', text, name)
            self.assertIn('cands="$cands $vault/projects/$slug"', text,
                          f"{name} dropped the untriaged shape")

    def test_the_bare_shape_is_probed_after_the_named_folders(self):
        # A migrated project must beat a twin left behind by an interrupted
        # move, so order in the candidate list is load-bearing.
        for name in ("session-start.sh", "sessionend.sh"):
            text = (self.HOOKS / name).read_text()
            self.assertLess(text.index('for c in $zones;'),
                            text.index('cands="$cands $vault/projects/$slug"'),
                            name)

    def test_python_hook_fallbacks_list_all_four_folders(self):
        # The degraded resolver only runs once `_vault_walk` fails to import,
        # which never happens organically while this repo's own suite runs
        # (the module is always on sys.path). Forcing that import failure and
        # calling the fallback for real — rather than grepping the source for
        # a literal shape — is what actually catches a resolver that silently
        # drops a folder: a DRY rewrite (`for z in (...)`) is correct and
        # never spells any zone name out next to `"projects" /` in the source
        # text, which a substring check alone cannot tell apart from a
        # resolver that dropped paused/finished entirely.
        for name in ("posttooluse-vault-log.py", "posttooluse-commit-log.py"):
            path = self.HOOKS / name
            mod_name = "_degraded_" + name.replace("-", "_").replace(".", "_")
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            with mock.patch.dict(sys.modules, {"_vault_walk": None}):
                spec.loader.exec_module(mod)
                if hasattr(mod, "_bootstrap"):
                    mod._bootstrap()  # vault-log.py defers the import to here
            self.assertFalse(getattr(mod, "_RESOLVER", None),
                             f"{name}: _vault_walk import unexpectedly "
                             f"succeeded, so this never reached the fallback")
            with tempfile.TemporaryDirectory() as tmp:
                vault = Path(tmp)
                for zone in ("active", "paused", "finished", "archive"):
                    d = vault / "projects" / zone / f"p-{zone}"
                    d.mkdir(parents=True)
                    (d / "brief.md").write_text("---\ntype: project\n---\n")
                for zone in ("active", "paused", "finished", "archive"):
                    found = mod.find_project_dir(vault, f"p-{zone}")
                    self.assertEqual(
                        found, vault / "projects" / zone / f"p-{zone}",
                        f"{name}'s degraded resolver misses {zone}/")
                legacy = vault / "projects" / "old-shape"
                legacy.mkdir(parents=True)
                (legacy / "brief.md").write_text("---\ntype: project\n---\n")
                self.assertEqual(
                    mod.find_project_dir(vault, "old-shape"), legacy,
                    f"{name}'s degraded resolver dropped the untriaged shape")


if __name__ == "__main__":
    unittest.main()
