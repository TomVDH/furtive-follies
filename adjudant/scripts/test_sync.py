"""Tests for adjudant/scripts/sync.py.

(PreCompact hook tests live in test_precompact.py. The hook's Gemini harvest
was removed in v0.7.0 — the hook is mechanical-only — so the harvest tests that
used to live here are gone with it.)
"""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sync import (
    cli_main as sync_cli,
    find_remember_source,
    mirror_handoff,
    refresh_brief_updated,
    refresh_projects_index_row,
    run_sync,
)


def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _connected_setup(tmp: Path, slug: str = "p") -> tuple[Path, Path]:
    """Create a code project + connected vault."""
    proj = tmp / "code"; proj.mkdir()
    vault = tmp / "vault"; vault.mkdir()
    (vault / "Home.md").write_text("---\ntype: vault-home\n---\n")
    (vault / "projects").mkdir()
    (vault / "projects" / slug).mkdir()
    _w(vault / "projects" / slug / "brief.md",
       f"---\ntype: project\nproject_type: coding\nslug: {slug}\nstatus: active\nupdated: 2026-05-01\n---\n\n# Test\n")
    (vault / "projects" / slug / "sessions").mkdir()
    _w(proj / ".claude" / "adjudant",
       f"vault_path: {vault}\nvault_name: vault\nslug: {slug}\nmode: project\n")
    return proj, vault


# ============================================================
# Brief refresh
# ============================================================


class TestRefreshBriefUpdated(unittest.TestCase):

    def test_bumps_existing_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("---\nupdated: 2026-05-01\n---\nbody")
            r = refresh_brief_updated(brief, "2026-05-27")
            self.assertEqual(r, "bumped")
            self.assertIn("updated: 2026-05-27", brief.read_text())

    def test_unchanged_when_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text("---\nupdated: 2026-05-27\n---\nbody")
            r = refresh_brief_updated(brief, "2026-05-27")
            self.assertEqual(r, "unchanged")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(refresh_brief_updated(Path(tmp) / "nope.md", "2026-05-27"), "missing")


# ============================================================
# Handoff mirror
# ============================================================


class TestFindRememberSource(unittest.TestCase):

    def test_prefers_remember_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            _w(proj / ".remember" / "remember.md", "canonical")
            _w(proj / ".remember" / "now.md", "fallback")
            self.assertEqual(find_remember_source(proj).name, "remember.md")

    def test_falls_back_to_now_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            _w(proj / ".remember" / "now.md", "fallback")
            self.assertEqual(find_remember_source(proj).name, "now.md")

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_remember_source(Path(tmp)))


class TestMirrorHandoff(unittest.TestCase):

    def test_creates_handoff_from_remember(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            _w(proj / ".remember" / "now.md", "current state\n")
            handoff = Path(tmp) / "_handoff.md"
            r = mirror_handoff(proj, handoff, "p", "2026-05-27")
            self.assertEqual(r, "mirrored")
            content = handoff.read_text()
            self.assertIn("type: handoff", content)
            self.assertIn("updated: 2026-05-27", content)
            self.assertIn("current state", content)

    def test_no_source_when_remember_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            handoff = Path(tmp) / "_handoff.md"
            r = mirror_handoff(proj, handoff, "p", "2026-05-27")
            self.assertEqual(r, "no-source")

    def test_preserves_existing_handoff_frontmatter(self):
        """reference/sync.md: 'preserve handoff frontmatter'. Custom fields and
        the template's extras must survive a re-mirror; only updated: bumps."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            _w(proj / ".remember" / "now.md", "new state\n")
            handoff = Path(tmp) / "_handoff.md"
            handoff.write_text(
                "---\ntype: handoff\nproject: \"[[projects/p/brief|p]]\"\n"
                "created: 2026-01-01\nsource: now\ncodename: falcon\n"
                "updated: 2026-05-01\ntags:\n  - handoff\n---\n\n# old\n\nold body\n"
            )
            r = mirror_handoff(proj, handoff, "p", "2026-05-27")
            self.assertEqual(r, "mirrored")
            content = handoff.read_text()
            self.assertIn("created: 2026-01-01", content)   # custom field survives
            self.assertIn("codename: falcon", content)      # custom field survives
            self.assertIn("updated: 2026-05-27", content)   # bumped
            self.assertNotIn("updated: 2026-05-01", content)
            self.assertIn("new state", content)              # body regenerated

    def test_adds_updated_when_existing_frontmatter_lacks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            _w(proj / ".remember" / "now.md", "state\n")
            handoff = Path(tmp) / "_handoff.md"
            handoff.write_text("---\ntype: handoff\n---\n\nbody\n")
            mirror_handoff(proj, handoff, "p", "2026-05-27")
            self.assertIn("updated: 2026-05-27", handoff.read_text())

    def test_includes_freshness_header(self):
        """Parity with the PreCompact hook: the verb writes the freshness block."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            _w(proj / ".remember" / "remember.md", "NEXT: finish the thing\n\nstate body\n")
            handoff = Path(tmp) / "_handoff.md"
            mirror_handoff(proj, handoff, "p", "2026-05-27")
            content = handoff.read_text()
            self.assertIn("handoff age:", content)
            self.assertIn("NEXT: finish the thing", content)
            self.assertIn("state body", content)

    def test_empty_source_never_wipes_existing_handoff(self):
        # Regression: the remember plugin leaves now.md empty at rest after
        # rotation; the mirror overwrote a populated handoff with nothing.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            _w(proj / ".remember" / "now.md", "")
            handoff = Path(tmp) / "_handoff.md"
            before = ("---\ntype: handoff\nupdated: 2026-05-01\n---\n\n"
                      "# Handoff: p\n\nprecious context\nNEXT: keep this\n")
            handoff.write_text(before)
            r = mirror_handoff(proj, handoff, "p", "2026-05-27")
            self.assertEqual(r, "source-empty")
            self.assertEqual(handoff.read_text(), before)

    def test_whitespace_source_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            _w(proj / ".remember" / "now.md", "\n   \n\t\n")
            handoff = Path(tmp) / "_handoff.md"
            r = mirror_handoff(proj, handoff, "p", "2026-05-27")
            self.assertEqual(r, "source-empty")
            self.assertFalse(handoff.exists())

    def test_mirror_line_carries_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "code"; proj.mkdir()
            _w(proj / ".remember" / "now.md", "state\n")
            handoff = Path(tmp) / "_handoff.md"
            mirror_handoff(proj, handoff, "p", "2026-05-27",
                           now=datetime(2026, 5, 27, 9, 30))
            self.assertIn("on 2026-05-27 09:30.", handoff.read_text())


class TestWriterParity(unittest.TestCase):
    """sync.mirror_handoff and the PreCompact hook's sync_handoff must emit
    byte-identical handoffs from identical inputs. The two writers drifted
    (heading form, mirror-line time, source: field) and churned the file on
    every alternation."""

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        proj = root / "code"; proj.mkdir(parents=True)
        _w(proj / ".remember" / "now.md", "live state\n\nNEXT: carry on\n")
        _w(proj / ".remember" / "today-2026-06-01.md", "- 13:30 worked\n")
        vault = root / "vault"
        _w(vault / "projects" / "p" / "sessions" / "2026-06-01.md",
           "- 12:00 · Added: [[x]]\n")
        return proj, vault

    def test_sync_and_hook_write_identical_handoffs(self):
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks" / "scripts"))
        import precompact as hook
        now = datetime(2026, 6, 1, 14, 0)
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "a"
            root_b = Path(tmp) / "b"
            proj_a, vault_a = self._fixture(root_a)
            proj_b, vault_b = self._fixture(root_b)

            handoff_a = vault_a / "projects" / "p" / "_handoff.md"
            r = mirror_handoff(proj_a, handoff_a, "p", "2026-06-01", now=now)
            self.assertEqual(r, "mirrored")

            # sync_handoff takes the RESOLVED project root since the
            # 2026-07-28 zone-awareness fix (main() resolves it via
            # find_project_dir), not the vault root.
            hook.sync_handoff(proj_b, vault_b / "projects" / "p", "p",
                              "2026-06-01", "14:00", now)
            handoff_b = vault_b / "projects" / "p" / "_handoff.md"
            self.assertTrue(handoff_b.is_file())

            self.assertEqual(handoff_a.read_text(), handoff_b.read_text())


# ============================================================
# Projects index row refresh
# ============================================================


class TestRefreshProjectsIndexRow(unittest.TestCase):

    def test_updates_row_with_current_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, vault = _connected_setup(Path(tmp), "p")
            # Add a session + a decision
            _w(vault / "projects" / "p" / "sessions" / "2026-05-27.md", "---\ntype: session\n---\n")
            (vault / "projects" / "p" / "decisions").mkdir()
            _w(vault / "projects" / "p" / "decisions" / "2026-05-27-x.md", "---\ntype: decision\n---\n")
            r = refresh_projects_index_row(vault, "p")
            self.assertIn(r, ("inserted", "created-index", "updated"))
            text = (vault / "projects" / "_index.md").read_text()
            self.assertIn("p/brief", text)
            self.assertIn("2026-05-27", text)


# ============================================================
# End-to-end run_sync
# ============================================================


class TestRunSyncEndToEnd(unittest.TestCase):

    def test_full_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, vault = _connected_setup(Path(tmp), "p")
            _w(proj / ".remember" / "now.md", "live state body\n")
            summary = run_sync(proj)
            # All three steps should produce useful outputs
            self.assertEqual(summary["slug"], "p")
            self.assertEqual(summary["steps"]["brief_refresh"], "bumped")
            self.assertEqual(summary["steps"]["handoff_mirror"], "mirrored")
            self.assertIn(summary["steps"]["projects_index_row"], ("inserted", "created-index", "updated"))
            # Handoff actually exists with the body
            handoff_content = (vault / "projects" / "p" / "_handoff.md").read_text()
            self.assertIn("live state body", handoff_content)

    def test_unconnected_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No breadcrumb
            with self.assertRaises(RuntimeError):
                run_sync(Path(tmp))


class TestStatusVocabularyGuard(unittest.TestCase):

    def _fixture(self, tmp: str, status: str, zone: str = "") -> Path:
        root = Path(tmp)
        vault = root / "vault"
        proj = vault / "projects" / zone / "p" if zone else vault / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "brief.md").write_text(
            f"---\ntype: project\nslug: p\nproject_type: coding\nstatus: {status}\n---\n\n# P\n")
        code = root / "code"
        (code / ".claude").mkdir(parents=True)
        (code / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: p\nmode: project\n")
        return code

    def test_off_vocabulary_status_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._fixture(tmp, "paused")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = sync_cli(["--project-dir", str(code)])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            self.assertTrue(any("paused" in w for w in summary.get("warnings", [])),
                            summary)

    def test_fridged_project_row_still_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._fixture(tmp, "fridge", zone="_fridge")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = sync_cli(["--project-dir", str(code)])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            self.assertNotEqual(summary["steps"]["projects_index_row"], "project-missing")


class TestTraversalSlugBreadcrumb(unittest.TestCase):
    """Verb-level proof for the repo-committed-slug hole. A primitive-only test
    is exactly what let the hole survive the v0.18.0 hardening pass: hooks were
    gated, verbs were not. sync is a WRITE verb reached through
    resolve_project_from_cwd, so it demonstrates the actual damage: before the
    gate it bumped `updated:` inside a brief that lived outside the vault.
    """

    @staticmethod
    def _snapshot(root: Path, skip: Path) -> dict[str, bytes]:
        """Byte-exact census of everything under `root` except the `skip`
        subtree. Any file created, deleted, or rewritten shows up as a diff."""
        out: dict[str, bytes] = {}
        for p in sorted(root.rglob("*")):
            if p == skip or skip in p.parents:
                continue
            out[str(p)] = p.read_bytes() if p.is_file() else b"<dir>"
        return out

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        vault = root / "vault"
        (vault / "projects").mkdir(parents=True)
        (vault / "Home.md").write_text("---\ntype: vault-home\n---\n")
        # `{vault}/projects/../../escaped`, a real project-shaped dir that is
        # a SIBLING of the vault, not inside it.
        escaped = root / "escaped"
        _w(escaped / "brief.md",
           "---\ntype: project\nproject_type: coding\nslug: escaped\n"
           "status: active\nupdated: 2026-01-01\n---\n\n# Not the vault's\n")
        code = root / "code"
        _w(code / ".claude" / "adjudant",
           f"vault_path: {vault}\nvault_name: vault\n"
           f"slug: ../../escaped\nmode: project\n")
        return code, vault, escaped

    def test_sync_writes_nothing_outside_the_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, vault, escaped = self._fixture(root)
            before = self._snapshot(root, skip=vault)

            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = sync_cli(["--project-dir", str(code)])

            self.assertNotEqual(rc, 0, "sync accepted a traversal slug")
            self.assertEqual(self._snapshot(root, skip=vault), before,
                             "sync wrote outside the vault")
            self.assertIn("updated: 2026-01-01",
                          (escaped / "brief.md").read_text(),
                          "the out-of-vault brief was rewritten")

    def test_sync_refusal_explains_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _vault, _escaped = self._fixture(Path(tmp))
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                rc = sync_cli(["--project-dir", str(code)])
            self.assertEqual(rc, 1)
            msg = err.getvalue()
            self.assertIn("error:", msg)
            self.assertIn("../../escaped", msg)
            self.assertIn("connect", msg)


class TestNonUtf8BriefSafety(unittest.TestCase):
    """Fix wave 1 finding 3: the strict-decode remediation applied to shelf and
    tidy missed sync, on the SAME file shelf was fixed for.
    refresh_brief_updated read brief.md with errors="replace" and wrote the
    decoded text straight back, so one latin-1 byte became a permanent U+FFFD
    on the next sync.
    """

    _BRIEF = ("---\ntype: project\nslug: p\nproject_type: coding\n"
              "status: active\nupdated: 2026-05-01\n---\n\n# P\n\nCaf")

    def _latin1_brief(self, path: Path) -> bytes:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = self._BRIEF.encode() + b"\xe9" + " noir\n".encode()
        path.write_bytes(raw)
        return raw

    def test_undecodable_brief_is_left_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            before = self._latin1_brief(brief)
            result = refresh_brief_updated(brief, "2026-05-27")
            after = brief.read_bytes()
            self.assertNotIn("�".encode(), after,
                             "sync must never bake U+FFFD into a vault brief")
            self.assertEqual(before, after,
                             "an undecodable brief must survive byte-identical")
            self.assertEqual(result, "skipped-undecodable",
                             "the skip must be reported, never silent")

    def test_utf8_brief_still_bumps(self):
        # Guard against over-correction: an ordinary accented brief is valid
        # UTF-8 and must still be rewritten.
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "brief.md"
            brief.write_text(self._BRIEF + "é noir\n", encoding="utf-8")
            self.assertEqual(refresh_brief_updated(brief, "2026-05-27"), "bumped")
            text = brief.read_text()
            self.assertIn("updated: 2026-05-27", text)
            self.assertIn("Café noir", text)

    def test_full_sync_reports_the_skip_and_preserves_the_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            brief = vault / "projects" / "p" / "brief.md"
            before = self._latin1_brief(brief)
            code = root / "code"
            (code / ".claude").mkdir(parents=True)
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\nslug: p\nmode: project\n")
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = sync_cli(["--project-dir", str(code)])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            self.assertEqual(summary["steps"]["brief_refresh"], "skipped-undecodable")
            self.assertEqual(brief.read_bytes(), before,
                             "a whole sync must leave the undecodable brief intact")
            self.assertTrue(
                any("UTF-8" in w for w in summary.get("warnings", [])),
                f"the un-bumped `updated:` field must warn: {summary}")


if __name__ == "__main__":
    unittest.main()
