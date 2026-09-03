"""Tests for adjudant/scripts/_scratch.py — the out-of-vault scratch root.

The whole point of this module is that adjudant's working files stop landing
inside the vault it is cleaning. The first test is the one that matters.
"""

import os
import tempfile
import unittest
from pathlib import Path

from _scratch import BACKUP_KEEP, prune_backups, scratch_dir


class TestScratchDir(unittest.TestCase):

    def test_scratch_is_never_inside_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "vault" / "projects" / "demo"
            project.mkdir(parents=True)
            for kind in ("clean-preview", "clean-backup"):
                got = scratch_dir(project, kind)
                self.assertNotIn(project, got.parents)
                self.assertNotEqual(got, project)

    def test_honours_TMPDIR(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            old = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = tmp
            try:
                got = scratch_dir(project, "clean-preview")
                self.assertTrue(str(got).startswith(tmp))
            finally:
                if old is None:
                    os.environ.pop("TMPDIR", None)
                else:
                    os.environ["TMPDIR"] = old

    def test_different_kinds_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            a = scratch_dir(project, "clean-preview")
            b = scratch_dir(project, "clean-backup")
            self.assertNotEqual(a, b)

    def test_hostile_project_name_cannot_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "a b/../../etc"
            got = scratch_dir(project, "clean-preview")
            self.assertNotIn("..", got.parts)

    def test_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            project.mkdir()
            got = scratch_dir(project, "clean-preview")
            self.assertFalse(got.exists())


class TestPruneBackups(unittest.TestCase):

    def _make(self, root: Path, n: int) -> list[Path]:
        made = []
        for i in range(n):
            d = root / f"2026090{i}T000000Z-x"
            d.mkdir(parents=True)
            (d / "f.txt").write_text("x")
            made.append(d)
        return made

    def test_keeps_newest_and_removes_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "backups"
            made = self._make(root, 8)
            prune_backups(root, keep=3)
            left = sorted(d.name for d in root.iterdir())
            self.assertEqual(len(left), 3)
            self.assertEqual(left, sorted(d.name for d in made[-3:]))

    def test_under_the_cap_removes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "backups"
            self._make(root, 2)
            prune_backups(root, keep=5)
            self.assertEqual(len(list(root.iterdir())), 2)

    def test_missing_root_is_benign(self):
        with tempfile.TemporaryDirectory() as tmp:
            prune_backups(Path(tmp) / "nope", keep=5)  # must not raise

    def test_default_cap_is_five(self):
        self.assertEqual(BACKUP_KEEP, 5)


if __name__ == "__main__":
    unittest.main()


class TestTwoProjectsNamedTheSameDoNotShareScratch(unittest.TestCase):
    """scratch_dir keyed only on project_dir.name, so every project called
    `demo` shared one tree under $TMPDIR/adjudant/demo/.

    In tests that showed up as pollution across runs: a leftover directory from
    an earlier run made test_creates_nothing fail with a clean tree and no code
    change. In production it is worse. Two vaults each holding a project named
    `demo` would share a preview and a backup root, so one project's apply
    could read the other's preview, and one project's rotation could delete the
    other's only pre-change backup.
    """

    def test_same_name_different_paths_get_different_scratch(self):
        with tempfile.TemporaryDirectory() as t:
            a = Path(t) / "vault-a" / "projects" / "demo"
            b = Path(t) / "vault-b" / "projects" / "demo"
            a.mkdir(parents=True); b.mkdir(parents=True)
            self.assertNotEqual(scratch_dir(a, "clean-preview"),
                                scratch_dir(b, "clean-preview"),
                                "two different projects share one scratch tree")

    def test_the_same_project_is_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "vault" / "projects" / "demo"
            p.mkdir(parents=True)
            self.assertEqual(scratch_dir(p, "clean-preview"),
                             scratch_dir(p, "clean-preview"))

    def test_the_readable_name_survives_in_the_path(self):
        # Whoever finds this directory should be able to tell whose it is.
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "vault" / "projects" / "acme-web"
            p.mkdir(parents=True)
            self.assertIn("acme-web", str(scratch_dir(p, "clean-preview")))
