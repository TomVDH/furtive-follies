"""Tests for hooks/scripts/posttooluse-vault-log.py — the PostToolUse hook.

Coverage focus: stdin payload parsing must fail closed (malformed JSON, missing
file_path, non-Write tools, out-of-project paths); the session-log entry must
keep its `- HH:MM · Label: [[link]]` shape with the Decision/Added split; new
files must get `source_session:` stamped from the payload's session_id; and
the stamp skip rules (session notes, _handoff, _index*, _iteration) must hold
through the hook, not just in the primitive.
"""

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "scripts" / "posttooluse-vault-log.py"

# Hyphenated filename — importlib, not a bare import. Exec runs the hook's own
# sys.path bootstrap, so _session_stamp/_vault_walk come from the real scripts/.
_spec = importlib.util.spec_from_file_location("posttooluse_vault_log", HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

SESSION_ID = "sess-abc123"
FRONTMATTER = "---\ntype: note\ncreated: 2026-01-01\n---\n\nbody\n"


class _HookHarness(unittest.TestCase):
    """Shared fixture + in-process runner. OB_VAULT from the developer's shell
    must never leak in — resolve_vault consults it as step 1."""

    def setUp(self):
        self._ob_vault = os.environ.pop("OB_VAULT", None)

    def tearDown(self):
        if self._ob_vault is not None:
            os.environ["OB_VAULT"] = self._ob_vault

    def _fixture(self, tmp: Path, *, stamp: bool = False,
                 stamp_value: str = "true") -> tuple[Path, Path, Path]:
        """Project + vault + today's session note. Returns (project,
        project_root_in_vault, session_note). stamp=True opts the breadcrumb
        into source_session stamping (v0.16.0: default is off)."""
        project = tmp / "code"
        vault = tmp / "vault"
        proot = vault / "projects" / "demo"
        (proot / "sessions").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        bc = f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n"
        if stamp:
            bc += f"stamp_source_session: {stamp_value}\n"
        (project / ".claude" / "adjudant").write_text(bc)
        today = datetime.now().strftime("%Y-%m-%d")
        session = proot / "sessions" / f"{today}.md"
        session.write_text("## Log\n")
        return project, proot, session

    def _note(self, proot: Path, rel: str, content: str = FRONTMATTER) -> Path:
        """Materialize the file the Write tool just produced — PostToolUse
        fires after the write, so the target exists when the hook runs."""
        p = proot / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def _payload(self, path: Path, *, tool: str = "Write",
                 session_id: str = SESSION_ID) -> dict:
        return {"tool_name": tool,
                "tool_input": {"file_path": str(path)},
                "session_id": session_id}

    def _run(self, project: Path, payload) -> int:
        """Run hook.main() in-process with `payload` (dict or raw str) on stdin."""
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        stdin_before = sys.stdin
        sys.stdin = io.StringIO(
            payload if isinstance(payload, str) else json.dumps(payload))
        try:
            return hook.main()
        finally:
            sys.stdin = stdin_before
            del os.environ["CLAUDE_PROJECT_DIR"]


class TestPayloadParsing(_HookHarness):

    def test_malformed_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            rc = self._run(project, "not json {{{")
            self.assertEqual(rc, 0)
            self.assertEqual(session.read_text(), "## Log\n")

    def test_missing_file_path_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            rc = self._run(project, {"tool_name": "Write", "tool_input": {},
                                     "session_id": SESSION_ID})
            self.assertEqual(rc, 0)
            self.assertEqual(session.read_text(), "## Log\n")

    def test_path_key_fallback_accepted(self):
        # Some Write payloads carry `path` instead of `file_path`.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, {"tool_name": "Write",
                                     "tool_input": {"path": str(note)},
                                     "session_id": SESSION_ID})
            self.assertEqual(rc, 0)
            self.assertIn("[[demo/notes/idea]]", session.read_text())

    def test_edit_tool_is_ignored(self):
        # Edit modifies existing files — logging it would double-count.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note, tool="Edit"))
            self.assertEqual(rc, 0)
            self.assertEqual(session.read_text(), "## Log\n")
            self.assertNotIn("source_session", note.read_text())

    def test_write_outside_project_root_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            other = proot.parent / "other" / "notes" / "x.md"
            other.parent.mkdir(parents=True)
            other.write_text(FRONTMATTER)
            rc = self._run(project, self._payload(other))
            self.assertEqual(rc, 0)
            self.assertEqual(session.read_text(), "## Log\n")
            self.assertNotIn("source_session", other.read_text())

    def test_stale_vault_fails_closed(self):
        # Cross-machine breadcrumb pointing nowhere: no log, no phantom dirs.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            phantom = Path(tmp) / "gone" / "vault"
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text(
                f"vault_path: {phantom}\nslug: demo\nmode: project\n")
            rc = self._run(project, self._payload(phantom / "projects" / "demo" / "n.md"))
            self.assertEqual(rc, 0)
            self.assertFalse(phantom.exists())


class TestSessionLogFormat(_HookHarness):

    def test_added_entry_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertRegex(
                session.read_text(),
                r"(?m)^- \d{2}:\d{2} · Added: \[\[demo/notes/idea\]\]$")

    def test_decision_label_for_decisions_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            note = self._note(proot, "decisions/0001-pick-x.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertRegex(
                session.read_text(),
                r"(?m)^- \d{2}:\d{2} · Decision: \[\[demo/decisions/0001-pick-x\]\]$")

    def test_nested_path_link_keeps_full_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            note = self._note(proot, "docs/sub/deep.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertIn("Added: [[demo/docs/sub/deep]]",
                          session.read_text())

    def test_midnight_straddle_appends_to_latest_note(self):
        # No note for today (session started before midnight): the entry must
        # land in the latest dated note — and the digit glob must not be fooled
        # by a 4-2-2-shaped non-date filename.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            session.unlink()
            # Yesterday and the day before. The straddle has a floor now, so
            # the older note is doubly ineligible; the assertion under test is
            # still "the latest eligible note wins, and the decoy never does".
            _y = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            _d = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
            latest = proot / "sessions" / f"{_y}.md"
            latest.write_text("## Log\n")
            (proot / "sessions" / f"{_d}.md").write_text("## Log\n")
            decoy = proot / "sessions" / "abcd-ef-gh.md"
            decoy.write_text("## Not a session\n")
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertIn("[[demo/notes/idea]]", latest.read_text())
            self.assertNotIn("idea", decoy.read_text())

    def test_no_session_note_still_stamps(self):
        # Job independence: a missing session log must not block job 2.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp), stamp=True)
            session.unlink()
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertIn(f"source_session: {SESSION_ID}", note.read_text())


class TestSourceSessionStamp(_HookHarness):

    def test_new_note_gets_stamped_in_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp), stamp=True)
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            fm = note.read_text().split("---\n")[1]
            self.assertIn(f"source_session: {SESSION_ID}", fm)

    def test_blank_session_id_logs_but_does_not_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp), stamp=True)
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note, session_id="  "))
            self.assertEqual(rc, 0)
            self.assertIn("[[demo/notes/idea]]", session.read_text())
            self.assertNotIn("source_session", note.read_text())

    def test_missing_session_id_key_logs_but_does_not_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp), stamp=True)
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, {"tool_name": "Write",
                                     "tool_input": {"file_path": str(note)}})
            self.assertEqual(rc, 0)
            self.assertIn("[[demo/notes/idea]]", session.read_text())
            self.assertNotIn("source_session", note.read_text())


class TestZoneAwareness(_HookHarness):
    """Audit 2026-07-27: shelf moves projects to _fridge/ and _archive/ without
    touching the breadcrumb, so a hardcoded projects/<slug> silently dropped
    every write to a shelved project."""

    def _shelved(self, tmp: Path, zone: str = "_fridge"):
        project = tmp / "code"
        vault = tmp / "vault"
        proot = vault / "projects" / zone / "demo"
        (proot / "sessions").mkdir(parents=True)
        (proot / "brief.md").write_text(
            "---\ntype: project\nslug: demo\nstatus: paused\n---\n\n# Demo\n")
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        today = datetime.now().strftime("%Y-%m-%d")
        session = proot / "sessions" / f"{today}.md"
        session.write_text("## Log\n")
        return project, proot, session

    def test_write_into_fridge_project_is_logged(self):
        for zone in ("_fridge", "_archive"):
            with self.subTest(zone=zone):
                with tempfile.TemporaryDirectory() as tmp:
                    project, proot, session = self._shelved(Path(tmp), zone)
                    note = self._note(proot, "notes/idea.md")
                    rc = self._run(project, self._payload(note))
                    self.assertEqual(rc, 0)
                    self.assertIn("notes/idea", session.read_text())
                    self.assertFalse((Path(tmp) / "vault" / "projects" / "demo").exists())

    def test_unknown_project_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, proot, session = self._fixture(root)
            (project / ".claude" / "adjudant").write_text(
                f"vault_path: {root / 'vault'}\nvault_name: vault\n"
                "slug: ghosttown\nmode: project\n")
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertFalse((root / "vault" / "projects" / "ghosttown").exists())


class TestSlugGuard(_HookHarness):
    """Audit 2026-07-27: the breadcrumb is repo-committed, so a cloned repo can
    carry a traversal slug. The hook must refuse it before building a path.

    Every fixture here MATERIALIZES the directory the bad slug resolves to and
    fills it with a shape find_project_dir accepts (brief.md plus today's
    session note). The zone check therefore succeeds and everything downstream
    of the slug guard is live, so the guard is the only thing standing between
    the hook and a write outside the vault. Neuter it and these tests fail,
    which is the whole point: an earlier version of this class pointed the
    traversal at a path that did not exist, so the hook bailed at the zone
    check and the tests passed with the guard removed.
    """

    def _decoy(self, tmp: Path, slug: str):
        """Breadcrumb carrying `slug`, plus a real project dir where it lands.

        Returns (project, vault_session, decoy_session, decoy_note). The decoy
        path is built by the same `vault/projects/<slug>` join the hook would
        perform, so `..` segments resolve exactly where a neutered guard would
        send the write.
        """
        project, proot, vault_session = self._fixture(tmp)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {tmp / 'vault'}\nslug: {slug}\nmode: project\n")
        decoy = tmp / "vault" / "projects" / slug
        (decoy / "sessions").mkdir(parents=True)
        (decoy / "brief.md").write_text(
            "---\ntype: project\nslug: decoy\nstatus: active\n---\n\n# Decoy\n")
        today = datetime.now().strftime("%Y-%m-%d")
        decoy_session = decoy / "sessions" / f"{today}.md"
        decoy_session.write_text("## Log\n")
        return project, vault_session, decoy_session, self._note(decoy, "notes/idea.md")

    def test_traversal_slug_is_refused(self):
        # `../../escaped` climbs out of projects/ AND out of the vault: the
        # decoy lands next to the vault, in the tmp root.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, vault_session, decoy_session, note = self._decoy(
                root, "../../escaped")
            self.assertTrue((root / "escaped" / "brief.md").is_file(),
                            "fixture must place a live project OUTSIDE the vault")
            self.assertEqual(self._run(project, self._payload(note)), 0)
            self.assertEqual(decoy_session.read_text(), "## Log\n",
                             "a traversal slug must never reach a write")
            self.assertEqual(vault_session.read_text(), "## Log\n")

    def test_metachar_slug_is_refused(self):
        for bad in ("has space", "UPPER", "back`tick", "-leading", "a/b"):
            with self.subTest(slug=bad):
                with tempfile.TemporaryDirectory() as tmp:
                    project, vault_session, decoy_session, note = self._decoy(
                        Path(tmp), bad)
                    self.assertEqual(self._run(project, self._payload(note)), 0)
                    self.assertEqual(decoy_session.read_text(), "## Log\n")
                    self.assertEqual(vault_session.read_text(), "## Log\n")

    def test_decoy_fixture_is_live_for_a_safe_slug(self):
        # Control: the same fixture with a kebab-case slug DOES get written to.
        # Without it, a decoy that silently failed to resolve would make the
        # two tests above pass for the wrong reason all over again.
        with tempfile.TemporaryDirectory() as tmp:
            project, vault_session, decoy_session, note = self._decoy(
                Path(tmp), "decoy-project")
            self.assertEqual(self._run(project, self._payload(note)), 0)
            self.assertIn("[[decoy-project/notes/idea]]",
                          decoy_session.read_text())

    def test_valid_slug_still_logs(self):
        # Guard must not break the happy path.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            note = self._note(proot, "notes/idea.md")
            self.assertEqual(self._run(project, self._payload(note)), 0)
            self.assertIn("[[demo/notes/idea]]", session.read_text())


class TestStampGate(_HookHarness):
    """v0.16.0: stamping is breadcrumb opt-in, default off."""

    def test_stamp_default_off(self):
        # No stamp_source_session key: job 1 logs, job 2 never fires.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertIn("[[demo/notes/idea]]", session.read_text())
            self.assertNotIn("source_session", note.read_text())

    def test_stamp_opt_in_spellings(self):
        for value in ("true", "1", "yes", "on", "TRUE"):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as tmp:
                    project, proot, session = self._fixture(
                        Path(tmp), stamp=True, stamp_value=value)
                    note = self._note(proot, "notes/idea.md")
                    rc = self._run(project, self._payload(note))
                    self.assertEqual(rc, 0)
                    self.assertIn(f"source_session: {SESSION_ID}", note.read_text())

    def test_stamp_garbage_value_off(self):
        for value in ("banana", "false", "0", "off", ""):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as tmp:
                    project, proot, session = self._fixture(
                        Path(tmp), stamp=True, stamp_value=value)
                    note = self._note(proot, "notes/idea.md")
                    rc = self._run(project, self._payload(note))
                    self.assertEqual(rc, 0)
                    self.assertNotIn("source_session", note.read_text())


class TestStampSkipRules(_HookHarness):

    def test_session_note_write_is_not_stamped(self):
        # Session notes accumulate session_id (list) via SessionStart — the
        # PostToolUse pass must not also pin a scalar source_session on them.
        # stamp=True: the skip rule must hold even when the gate is open.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp), stamp=True)
            session.write_text("---\ntype: session\ndate: 2026-01-01\n---\n\n## Log\n")
            rc = self._run(project, self._payload(session))
            self.assertEqual(rc, 0)
            self.assertNotIn("source_session", session.read_text())

    def test_system_files_are_never_stamped(self):
        # _handoff / _index / _index-* / _iteration are system-managed —
        # "which conversation authored this" makes no sense there.
        # stamp=True: the skip rule must hold even when the gate is open.
        for name in ("_handoff.md", "_index.md", "_index-decisions.md", "_iteration.md"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    project, proot, session = self._fixture(Path(tmp), stamp=True)
                    target = self._note(proot, name)
                    rc = self._run(project, self._payload(target))
                    self.assertEqual(rc, 0)
                    self.assertNotIn("source_session", target.read_text())
                    # The log entry is still appended — skip rules gate only
                    # the stamp, not job 1.
                    self.assertIn(f"[[demo/{name[:-3]}]]", session.read_text())


class TestLoggedLinkResolves(_HookHarness):
    """The link the hook writes must resolve in the vault index.

    Every earlier test here compares the log line to a literal string, which
    says the hook is consistent with itself and nothing about whether a reader
    following the link lands on the file. Until v3 the answer was "sometimes":
    the index matched bare basenames, so a link naming the wrong project still
    read as healthy.
    """

    def _zoned_fixture(self, tmp: Path, zone: str) -> tuple[Path, Path, Path, Path]:
        project = tmp / "code"
        vault = tmp / "vault"
        proot = vault / "projects" / zone / "demo" if zone else vault / "projects" / "demo"
        (proot / "sessions").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        today = datetime.now().strftime("%Y-%m-%d")
        session = proot / "sessions" / f"{today}.md"
        session.write_text("## Log\n")
        return project, vault, proot, session

    def _logged_target(self, session: Path) -> str:
        m = re.search(r"\[\[([^\]|]+)", session.read_text())
        self.assertIsNotNone(m, f"no wikilink in the log: {session.read_text()!r}")
        return m.group(1)

    def test_the_logged_link_resolves_from_every_lifecycle_folder(self):
        from _vault_walk import build_vault_index, resolve_wikilink
        for zone in ("active", "paused", "finished", "archive", ""):
            with self.subTest(zone=zone or "pre-v3 unzoned"):
                with tempfile.TemporaryDirectory() as tmp:
                    project, vault, proot, session = self._zoned_fixture(
                        Path(tmp), zone)
                    note = self._note(proot, "notes/idea.md")
                    self.assertEqual(self._run(project, self._payload(note)), 0)
                    target = self._logged_target(session)
                    self.assertTrue(
                        resolve_wikilink(target, build_vault_index(vault)),
                        f"the hook logged [[{target}]], which resolves to nothing")

    def test_the_logged_link_never_names_the_lifecycle_folder(self):
        # A link carrying the folder breaks the moment the project moves,
        # which is the whole reason link() refuses to write one.
        for zone in ("active", "paused", "finished", "archive"):
            with self.subTest(zone=zone):
                with tempfile.TemporaryDirectory() as tmp:
                    project, vault, proot, session = self._zoned_fixture(
                        Path(tmp), zone)
                    self._run(project, self._payload(
                        self._note(proot, "notes/idea.md")))
                    target = self._logged_target(session)
                    self.assertFalse(target.startswith(("projects/", zone + "/")),
                                     f"log line carries the lifecycle folder: {target}")


class TestHookProcess(_HookHarness):
    """End-to-end through the __main__ guard: real stdin, real imports."""

    def _run_proc(self, project: Path, stdin: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env.pop("OB_VAULT", None)
        return subprocess.run(
            [sys.executable, str(HOOK)],
            env=env, capture_output=True, text=True, input=stdin, timeout=15)

    def test_end_to_end_write_logs_and_stamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp), stamp=True)
            note = self._note(proot, "notes/idea.md")
            r = self._run_proc(project, json.dumps(self._payload(note)))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(
                session.read_text(),
                r"(?m)^- \d{2}:\d{2} · Added: \[\[demo/notes/idea\]\]$")
            self.assertIn(f"source_session: {SESSION_ID}", note.read_text())

    def test_garbage_stdin_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            r = self._run_proc(project, "\x00garbage\nnot json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(session.read_text(), "## Log\n")


class TestLazySessionNote(_HookHarness):
    """v3: the note is born on the first real write, not on session open.

    `_fixture` seeds today's note, which is exactly what must NOT exist here,
    so these tests build a bare project of their own."""

    def _bare(self, tmp: Path) -> tuple[Path, Path]:
        """Project + vault with NO sessions dir and no note. Returns
        (project, project_root_in_vault)."""
        project = tmp / "code"
        vault = tmp / "vault"
        proot = vault / "projects" / "demo"
        (proot / "notes").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        return project, proot

    def test_first_write_creates_the_note(self):
        # The note appears exactly when there is something to record in it.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._bare(Path(tmp))
            self.assertFalse((proot / "sessions").exists())
            note_path = self._note(proot, "notes/a.md")
            rc = self._run(project, self._payload(note_path))
            self.assertEqual(rc, 0)
            session = proot / "sessions" / f"{date.today().isoformat()}.md"
            self.assertTrue(session.is_file())
            text = session.read_text()
            self.assertIn("type: session", text)
            self.assertIn("## Log", text)
            self.assertIn("notes/a]]", text)
            # The UserPromptSubmit nudge greps the note for this exact string;
            # SessionStart used to write it, so creation carries it here now.
            self.assertIn("{One-line intent. Frozen after first write.}", text)

    def test_second_write_appends_and_does_not_recreate(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._bare(Path(tmp))
            for name in ("notes/a.md", "notes/b.md"):
                self._run(project, self._payload(self._note(proot, name)))
            session = proot / "sessions" / f"{date.today().isoformat()}.md"
            text = session.read_text()
            self.assertEqual(text.count("type: session"), 1)
            self.assertIn("notes/a]]", text)
            self.assertIn("notes/b]]", text)


class TestFutureSessionFallback(_HookHarness):
    """Finding 19: the midnight-straddle fallback took the lexically-latest
    dated note unbounded, so a future-dated note absorbed every append."""

    def test_log_entry_skips_future_dated_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, session = self._fixture(Path(tmp))
            session.unlink()                       # no note for today
            # Yesterday: an eligible past note. The assertion under test is
            # that the FUTURE note is skipped, which is unchanged.
            _y = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            past = proot / "sessions" / f"{_y}.md"
            past.write_text("## Log\n")
            future = proot / "sessions" / "2029-12-31.md"
            future.write_text("## Log\n")
            note = self._note(proot, "notes/idea.md")
            rc = self._run(project, self._payload(note))
            self.assertEqual(rc, 0)
            self.assertIn("[[demo/notes/idea]]", past.read_text())
            self.assertNotIn("idea", future.read_text())


class TestHookCostAndWiring(unittest.TestCase):
    """Finding 21: the unlinked no-op path must not pay the 1100-line
    _vault_walk import (it ran on every Write/Edit machine-wide, measured
    36.5 ms vs 18.8 ms bare), and the hook must register async like its
    PostToolUse siblings. Finding 22 discipline: stdin drains before any
    early exit, or a large payload EPIPEs the harness writer."""

    def _env_without_project_dir(self):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.pop("OB_VAULT", None)
        return env

    def test_unlinked_noop_path_skips_heavy_imports(self):
        # -X importtime prints every imported module to stderr; an unlinked
        # run must never touch _vault_walk or _session_stamp.
        proc = subprocess.run(
            [sys.executable, "-X", "importtime", str(HOOK)],
            input="{}", capture_output=True, text=True,
            env=self._env_without_project_dir(), timeout=30)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("_vault_walk", proc.stderr)
        self.assertNotIn("_session_stamp", proc.stderr)

    def test_stdin_fully_consumed_on_unlinked_noop(self):
        # 8 MB payload, no breadcrumb: the hook must drain stdin before its
        # early exit or this write raises BrokenPipeError.
        big = json.dumps({"tool_name": "Write",
                          "tool_input": {"content": "x" * 8_000_000}})
        proc = subprocess.Popen(
            [sys.executable, str(HOOK)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=self._env_without_project_dir())
        wrote_all = True
        try:
            proc.stdin.write(big.encode())
            proc.stdin.close()
        except BrokenPipeError:
            wrote_all = False
        rc = proc.wait(timeout=30)
        self.assertTrue(wrote_all, "hook exited before draining stdin (EPIPE)")
        self.assertEqual(rc, 0)

    def test_hooks_json_registers_vault_log_async(self):
        hooks_file = HOOK.parents[1] / "hooks.json"
        entries = []

        def walk(obj):
            if isinstance(obj, dict):
                if "command" in obj and "posttooluse-vault-log.py" in str(obj.get("command")):
                    entries.append(obj)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        walk(json.loads(hooks_file.read_text()))
        self.assertEqual(len(entries), 1)
        self.assertIs(entries[0].get("async"), True,
                      "vault-log is the only sync PostToolUse hook: it blocks "
                      "every Write/Edit machine-wide")


if __name__ == "__main__":
    unittest.main()


class TestStraddleIsBounded(_HookHarness):
    """An adversarial prover found this after plan 1 landed.

    The midnight-straddle fallback exists for a session that starts 23:40 and
    ends 00:10, but its guard is `cand.stem <= today` with no lower bound, so
    it reaches back arbitrarily far. Eager session-note creation used to mask
    it: SessionStart always made today's note, so the fallback never fired in
    practice. Lazy creation (Task 6) removed the mask, and a vault whose newest
    session note is months old silently absorbs today's work into it.
    """

    def _aged(self, tmp: Path, days: int) -> tuple[Path, Path, str]:
        """Fixture with ONE session note `days` old and none for today."""
        project = tmp / "code"
        vault = tmp / "vault"
        proot = vault / "projects" / "demo"
        (proot / "sessions").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        old = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        (proot / "sessions" / f"{old}.md").write_text(
            "---\ntype: session\n---\n\n## Log\n\n- 09:00 · earlier work\n")
        return project, proot, old

    def test_a_months_old_note_does_not_absorb_todays_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, old = self._aged(Path(tmp), days=60)
            self._run(project, self._payload(self._note(proot, "notes/a.md")))
            today = datetime.now().strftime("%Y-%m-%d")
            self.assertTrue((proot / "sessions" / f"{today}.md").is_file(),
                            "today's note was never created")
            self.assertNotIn("a.md", (proot / "sessions" / f"{old}.md").read_text(),
                             f"today's work was filed into a {old} note")

    def test_yesterday_still_straddles(self):
        # The real case the fallback exists for must keep working.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot, old = self._aged(Path(tmp), days=1)
            self._run(project, self._payload(self._note(proot, "notes/b.md")))
            today = datetime.now().strftime("%Y-%m-%d")
            self.assertIn("notes/b]]", (proot / "sessions" / f"{old}.md").read_text(),
                          "a genuine midnight straddle stopped working")
            self.assertFalse((proot / "sessions" / f"{today}.md").exists(),
                             "straddling should append, not create a second note")
