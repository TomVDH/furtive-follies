"""Tests for hooks/scripts/postcompact.py, the PostCompact hook.

Regression focus: the hook must gate on a real compaction summary (empty or
missing payload writes nothing), collapse multi-line summaries to a single
clipped gist line, honor the same resolve/midnight-fallback discipline as
posttooluse-vault-log.py, and fail closed on a stale breadcrumb instead of
materializing a phantom vault path.
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks" / "scripts"))
import postcompact


class _EnvHygiene(unittest.TestCase):
    """OB_VAULT from the developer's shell must never leak into these tests,
    resolve_vault consults it as step 1."""

    def setUp(self):
        self._ob_vault = os.environ.pop("OB_VAULT", None)

    def tearDown(self):
        if self._ob_vault is not None:
            os.environ["OB_VAULT"] = self._ob_vault


class _HookHarness(_EnvHygiene):
    """Shared fixture: linked project + vault with today's session note."""

    def _breadcrumb(self, project: Path, vault_path: str, slug: str = "demo") -> None:
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault_path}\nvault_name: vault\nslug: {slug}\nmode: project\n"
        )

    def _fixture(self, tmp: Path) -> tuple[Path, Path]:
        """Project breadcrumbed to a real vault; today's session note seeded.
        Returns (project, session_file)."""
        project = tmp / "code"
        vault = tmp / "vault"
        sessions = vault / "projects" / "demo" / "sessions"
        sessions.mkdir(parents=True)
        today = datetime.now().strftime("%Y-%m-%d")
        session_file = sessions / f"{today}.md"
        session_file.write_text("## Log\n")
        self._breadcrumb(project, str(vault))
        return project, session_file

    def _run_main(self, project: Path, payload) -> int:
        """Invoke main() with stdin patched to the given payload (dict is
        JSON-encoded; a str is fed raw for malformed-input tests)."""
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        stdin_before = sys.stdin
        sys.stdin = io.StringIO(raw)
        try:
            return postcompact.main()
        finally:
            sys.stdin = stdin_before
            del os.environ["CLAUDE_PROJECT_DIR"]


class TestSummaryGate(_HookHarness):

    def test_empty_summary_no_write(self):
        # Rule 3: gate on real signal. Empty string, whitespace, and a missing
        # key each mean the harness had nothing to say; the log stays clean.
        for payload in ({}, {"compaction_summary": ""}, {"compaction_summary": "   \n"}):
            with tempfile.TemporaryDirectory() as tmp:
                project, session_file = self._fixture(Path(tmp))
                before = session_file.read_text()
                rc = self._run_main(project, payload)
                self.assertEqual(rc, 0)
                self.assertEqual(session_file.read_text(), before,
                                 f"no-signal payload {payload!r} must write nothing")

    def test_appends_gist_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, session_file = self._fixture(Path(tmp))
            before = session_file.read_text()
            rc = self._run_main(
                project, {"compaction_summary": "Wired the board reseed path"})
            self.assertEqual(rc, 0)
            text = session_file.read_text()
            self.assertTrue(text.startswith(before), "existing log must be preserved")
            added = text[len(before):]
            self.assertRegex(
                added,
                r"^- \d\d:\d\d · compacted: Wired the board reseed path\n$",
                "exactly one gist line must be appended")

    def test_fallback_keys_tried_in_order(self):
        # Probe-verified key first, then the documented fallbacks. Each key
        # must work alone; compaction_summary must win when several coexist.
        for key in ("compaction_summary", "summary", "compact_summary", "message"):
            with tempfile.TemporaryDirectory() as tmp:
                project, session_file = self._fixture(Path(tmp))
                rc = self._run_main(project, {key: f"via {key}"})
                self.assertEqual(rc, 0)
                self.assertIn(f"compacted: via {key}", session_file.read_text(),
                              f"payload key {key!r} must be honored")
        with tempfile.TemporaryDirectory() as tmp:
            project, session_file = self._fixture(Path(tmp))
            self._run_main(project, {"summary": "loser", "compaction_summary": "winner"})
            text = session_file.read_text()
            self.assertIn("compacted: winner", text)
            self.assertNotIn("loser", text)


class TestGistShape(_HookHarness):

    def test_gist_single_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, session_file = self._fixture(Path(tmp))
            before = session_file.read_text()
            self._run_main(project, {
                "compaction_summary": "fixed parser\nadded tests\n\nshipped  docs\n"})
            added = session_file.read_text()[len(before):]
            self.assertIn("compacted: fixed parser added tests shipped docs\n", added)
            self.assertEqual(added.count("\n"), 1,
                             "multi-line summary must collapse to one log line")

    def test_gist_clipped_to_160_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, session_file = self._fixture(Path(tmp))
            self._run_main(project, {"compaction_summary": "x" * 400})
            match = re.search(r"compacted: (x+)", session_file.read_text())
            self.assertIsNotNone(match)
            self.assertEqual(len(match.group(1)), 160)


class TestFailClosed(_HookHarness):

    def test_stale_breadcrumb_fail_closed(self):
        # Stale/cross-machine vault_path: nothing is written, nothing is
        # materialized, exit stays 0.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            phantom = Path(tmp) / "gone" / "vault"  # does not exist
            self._breadcrumb(project, str(phantom))
            rc = self._run_main(project, {"compaction_summary": "real content"})
            self.assertEqual(rc, 0)
            self.assertFalse(phantom.exists(),
                             "stale vault path must NOT be materialized by the hook")

    def test_no_session_note_writes_nothing(self):
        # Valid vault but no daily note yet: the hook must not invent one.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            vault = Path(tmp) / "vault"
            sessions = vault / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            self._breadcrumb(project, str(vault))
            rc = self._run_main(project, {"compaction_summary": "real content"})
            self.assertEqual(rc, 0)
            self.assertEqual(list(sessions.iterdir()), [],
                             "hook must never create a session note")

    def test_malformed_stdin_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, session_file = self._fixture(Path(tmp))
            before = session_file.read_text()
            rc = self._run_main(project, "not json {")
            self.assertEqual(rc, 0)
            self.assertEqual(session_file.read_text(), before)


class TestMidnightStraddle(_HookHarness):

    def test_gist_lands_in_latest_note(self):
        # No note for today (session started before midnight): the gist must
        # land in the latest existing daily note, never a lookalike decoy.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            vault = Path(tmp) / "vault"
            sessions = vault / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            latest = sessions / "2020-01-02.md"
            latest.write_text("## Log\n")
            (sessions / "2020-01-01.md").write_text("## Log\n")
            decoy = sessions / "abcd-ef-gh.md"  # 4-2-2 shape, not a date
            decoy.write_text("## Not a session\n")
            self._breadcrumb(project, str(vault))
            rc = self._run_main(project, {"compaction_summary": "straddled midnight"})
            self.assertEqual(rc, 0)
            self.assertIn("compacted: straddled midnight", latest.read_text())
            self.assertNotIn("compacted", decoy.read_text())  # digit glob, not ?

    def test_gist_skips_future_dated_note(self):
        # Finding 19: an unbounded latest-glob let a future-dated note
        # (clock skew, restored backup) absorb every straddle append.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            vault = Path(tmp) / "vault"
            sessions = vault / "projects" / "demo" / "sessions"
            sessions.mkdir(parents=True)
            latest_real = sessions / "2020-01-02.md"
            latest_real.write_text("## Log\n")
            future = sessions / "2029-12-31.md"
            future.write_text("## Log\n")
            self._breadcrumb(project, str(vault))
            rc = self._run_main(project, {"compaction_summary": "straddled midnight"})
            self.assertEqual(rc, 0)
            self.assertIn("compacted: straddled midnight", latest_real.read_text())
            self.assertNotIn("compacted", future.read_text())


class TestZoneAwareness(_HookHarness):
    """Audit 2026-07-27: hooks hardcoded projects/<slug> while shelf moves
    projects to _fridge/ and _archive/."""

    def test_gist_lands_in_shelved_project(self):
        for zone in ("_fridge", "_archive"):
            with self.subTest(zone=zone):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    project = root / "code"
                    vault = root / "vault"
                    sessions = vault / "projects" / zone / "demo" / "sessions"
                    sessions.mkdir(parents=True)
                    (sessions.parent / "brief.md").write_text(
                        "---\ntype: project\nslug: demo\n---\n\n# Demo\n")
                    today = datetime.now().strftime("%Y-%m-%d")
                    note = sessions / f"{today}.md"
                    note.write_text("## Log\n")
                    self._breadcrumb(project, str(vault))
                    rc = self._run_main(
                        project, {"compaction_summary": "did the thing"})
                    self.assertEqual(rc, 0)
                    self.assertIn("compacted: did the thing", note.read_text())
                    self.assertFalse((vault / "projects" / "demo").exists())

    def test_unknown_project_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "code"
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            self._breadcrumb(project, str(vault))
            rc = self._run_main(project, {"compaction_summary": "orphan"})
            self.assertEqual(rc, 0)
            self.assertFalse((vault / "projects" / "demo").exists())


class TestSlugGuard(_HookHarness):
    """The breadcrumb is repo-committed, so a cloned repo can carry a traversal
    slug. The hook must refuse it before joining it into a path.

    Each fixture MATERIALIZES the directory the bad slug resolves to, with the
    shape find_project_dir accepts (brief.md plus a dated session note), so the
    zone lookup succeeds and everything downstream of the slug guard is live.
    The guard is then the only thing between the hook and an append to a file
    outside the vault.
    """

    def _decoy(self, tmp: Path, slug: str) -> tuple[Path, Path]:
        """Project breadcrumbed to `slug`, plus a live project where it lands.

        The join is the same `vault/projects/<slug>` find_project_dir performs,
        so `..` segments resolve exactly where a neutered guard would send the
        append. Returns (project, decoy_session_note).
        """
        project = tmp / "code"
        vault = tmp / "vault"
        (vault / "projects").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: postcompact-slug-test-vault-8b2d\n"
            f"slug: {slug}\nmode: project\n")
        decoy = vault / "projects" / slug
        (decoy / "sessions").mkdir(parents=True, exist_ok=True)
        (decoy / "brief.md").write_text(
            "---\ntype: project\nslug: decoy\n---\n\n# Decoy\n")
        note = decoy / "sessions" / f"{datetime.now():%Y-%m-%d}.md"
        note.write_text("## Log\n")
        return project, note

    def test_traversal_slug_appends_nothing(self):
        # `../../escaped` climbs out of projects/ AND out of the vault: the
        # decoy lands next to the vault, in the tmp root.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, note = self._decoy(root, "../../escaped")
            self.assertTrue((root / "escaped" / "brief.md").is_file(),
                            "fixture must place a live project OUTSIDE the vault")
            rc = self._run_main(project, {"compaction_summary": "escaped gist"})
            self.assertEqual(rc, 0)
            self.assertEqual(note.read_text(), "## Log\n",
                             "a traversal slug must never reach a write")

    def test_metachar_slug_appends_nothing(self):
        for bad in ("has space", "UPPER", "back`tick", "-leading"):
            with self.subTest(slug=bad):
                with tempfile.TemporaryDirectory() as tmp:
                    project, note = self._decoy(Path(tmp), bad)
                    rc = self._run_main(project, {"compaction_summary": "nope"})
                    self.assertEqual(rc, 0)
                    self.assertEqual(note.read_text(), "## Log\n")

    def test_decoy_fixture_is_live_for_a_safe_slug(self):
        # Control: the same fixture with a kebab-case slug DOES get appended
        # to. Without it, a decoy that silently failed to resolve would make
        # the two tests above pass for the wrong reason all over again.
        with tempfile.TemporaryDirectory() as tmp:
            project, note = self._decoy(Path(tmp), "decoy-project")
            rc = self._run_main(project, {"compaction_summary": "landed gist"})
            self.assertEqual(rc, 0)
            self.assertIn("compacted: landed gist", note.read_text())


if __name__ == "__main__":
    unittest.main()
