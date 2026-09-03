"""Tests for hooks/scripts/postcompact.py, the PostCompact hook.

Since v3 the hook writes nothing: it drains stdin and returns 0. It used to
append `- HH:MM · compacted: {gist}` to the session log, where the gist was
the harness summary clipped at 160 chars, so the vault filled with sentence
fragments of raw model reasoning.

Regression focus is therefore one claim, asserted from every angle the old
writer could have reached the disk from: no payload, breadcrumb or slug makes
this hook touch a file. The fail-closed and traversal cases are kept for the
day someone gives it a write path again.
"""

import io
import json
import os
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


class TestNoCompactionMarkers(_HookHarness):

    def test_compaction_appends_nothing(self):
        # 34 files in the real vault carry truncated model reasoning from this
        # hook ("· compacted: <analysis> Let me chronologically work through…").
        # A compaction is not project work.
        with tempfile.TemporaryDirectory() as tmp:
            project, note = self._fixture(Path(tmp))
            note.write_text(
                "---\ntype: session\n---\n\n## Log\n\n- 09:00 · a.md written\n")
            before = note.read_text()
            rc = self._run_main(project, {"summary": "a long compaction summary"})
            self.assertEqual(rc, 0)
            self.assertEqual(note.read_text(), before)


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


class TestZoneAwareness(_HookHarness):
    """Audit 2026-07-27: hooks hardcoded projects/<slug> while shelf moves
    projects to _fridge/ and _archive/."""

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
    shape find_project_dir accepts (brief.md plus a dated session note), so a
    hook that ever regains a write path lands there and these tests catch it.
    The live control that used to prove the fixture resolves went with the
    write itself: there is no longer any input that makes this hook append.
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

if __name__ == "__main__":
    unittest.main()
