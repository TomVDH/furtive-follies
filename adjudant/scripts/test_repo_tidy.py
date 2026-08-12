"""Tests for repo_tidy symlink repair (preview -> apply, idempotent)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_tidy as rt
from test_repo_walk import _make_plugin


class TestRepoTidy(unittest.TestCase):

    def _adopted_with_missing_link(self, root: Path) -> Path:
        _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
        (root / "alpha" / ".gemini" / "skills" / "alpha").unlink()  # missing
        return root

    def test_detect_finds_missing_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._adopted_with_missing_link(Path(tmp))
            reps = rt.detect_repairs(root)
            self.assertEqual(len(reps), 1)
            self.assertEqual(reps[0]["harness"], ".gemini")

    def test_clean_repo_no_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
            self.assertEqual(rt.detect_repairs(root), [])

    def test_non_adopted_plugin_not_repaired(self):
        # skills present but ZERO harness symlinks -> not adopted -> left alone
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=False)
            self.assertEqual(rt.detect_repairs(root), [])

    def test_preview_then_apply_repairs_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._adopted_with_missing_link(Path(tmp))
            preview = rt.write_preview(root, rt.detect_repairs(root))
            self.assertTrue((preview / "summary.md").is_file())
            self.assertTrue((preview / "changes.json").is_file())
            self.assertTrue((preview / "files").is_dir())
            # live still broken before apply
            self.assertFalse((root / "alpha" / ".gemini" / "skills" / "alpha").is_symlink())
            backup = rt.apply_preview(root)
            link = root / "alpha" / ".gemini" / "skills" / "alpha"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), (root / "alpha" / "skills" / "alpha").resolve())
            self.assertTrue(backup.is_dir())
            self.assertFalse((root / rt.PREVIEW_DIR_NAME).exists())  # preview consumed

    def test_idempotent_second_detect_empty_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._adopted_with_missing_link(Path(tmp))
            rt.write_preview(root, rt.detect_repairs(root))
            rt.apply_preview(root)
            self.assertEqual(rt.detect_repairs(root), [])


class TestRepoTidyDestructiveGuards(unittest.TestCase):
    """Audit 2026-07-27 finding 14: a non-symlink at a harness link path was
    unlinked and symlinked over, while the `.legacy` record held only
    metadata — so a real file's content was destroyed outright, and a real
    directory crashed apply mid-loop."""

    def _with_real_file_at_link(self, root: Path) -> Path:
        _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
        link = root / "alpha" / ".gemini" / "skills" / "alpha"
        link.unlink()
        link.write_text("PRECIOUS hand-written content\n")
        return link

    def test_real_file_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = self._with_real_file_at_link(root)
            rt.write_preview(root, rt.detect_repairs(root))
            backup = rt.apply_preview(root)
            saved = list(Path(backup).glob("*.content.legacy"))
            self.assertTrue(saved, "a real file's CONTENT must be backed up")
            self.assertIn("PRECIOUS hand-written content", saved[0].read_text())
            self.assertTrue(link.is_symlink(), "the repair still happens")

    def _with_real_dir_at_link(self, root: Path) -> Path:
        _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
        link = root / "alpha" / ".gemini" / "skills" / "alpha"
        link.unlink()
        link.mkdir()
        (link / "keep.md").write_text("do not lose me\n")
        return link

    def test_real_directory_is_refused_not_destroyed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = self._with_real_dir_at_link(root)
            rt.write_preview(root, rt.detect_repairs(root))
            backup = Path(rt.apply_preview(root))
            self.assertTrue(link.is_dir() and not link.is_symlink(),
                            "a real directory must be left alone")
            self.assertEqual((link / "keep.md").read_text(), "do not lose me\n")
            self.assertTrue((backup / "SKIPPED.txt").is_file(),
                            "the skip must be recorded, never silent")
            self.assertIn("alpha/.gemini/skills/alpha",
                          (backup / "SKIPPED.txt").read_text())

    def test_directory_is_refused_up_front_not_by_a_failing_unlink(self):
        # The outcome above survives the guard's deletion: unlink() on a
        # directory raises OSError, that except branch also appends to
        # `skipped`, and the directory ends up intact either way. So the test
        # above cannot pin the guard on its own.
        #
        # What only the EXPLICIT guard does is refuse BEFORE the backup record
        # is written. Delete `if link.is_dir() and not link.is_symlink()` and a
        # stray `<stem>.legacy` appears in the backup for a link that was never
        # touched: a repair receipt for a repair that did not happen.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._with_real_dir_at_link(root)
            rt.write_preview(root, rt.detect_repairs(root))
            backup = Path(rt.apply_preview(root))
            self.assertEqual(
                sorted(p.name for p in backup.iterdir()), ["SKIPPED.txt"],
                "a refused link must leave no backup record behind")


if __name__ == "__main__":
    unittest.main()
