"""Tests for scripts/bump_plugin_version.py — one-shot version lockstep."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bump_plugin_version import bump, _set_json_version


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _make_tree(root: Path, plugin: str = "demo", version: str = "0.1.0") -> None:
    """Minimal marketplace layout with all four lockstep files at `version`."""
    _w(root / plugin / ".claude-plugin" / "plugin.json",
       json.dumps({"name": plugin, "version": version, "keywords": ["a", "b"]}, indent=2) + "\n")
    _w(root / plugin / "scripts" / "command-metadata.json",
       json.dumps({"name": plugin, "version": version, "verbs": []}, indent=2) + "\n")
    _w(root / plugin / "skills" / plugin / "SKILL.md",
       f"---\nname: {plugin}\nversion: {version}\nuser-invocable: true\n---\n\n# {plugin}\n")
    _w(root / ".claude-plugin" / "marketplace.json",
       json.dumps({"plugins": [{"name": plugin, "version": version, "source": f"./{plugin}"}]}, indent=2) + "\n")


class TestSemverValidation(unittest.TestCase):

    def test_rejects_bad_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root)
            for bad in ("1.2", "v1.2.3", "1.2.3.4", "abc", "1.2.x"):
                with self.assertRaises(ValueError, msg=bad):
                    bump("demo", bad, root=root)

    def test_accepts_semver_with_prerelease(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root)
            changed = bump("demo", "1.2.3-rc.1", root=root)
            self.assertTrue(changed)


class TestBump(unittest.TestCase):

    def test_writes_all_lockstep_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, version="0.1.0")
            changed = bump("demo", "0.2.0", root=root)
            self.assertEqual(len(changed), 4)
            self.assertEqual(json.loads((root / "demo" / ".claude-plugin" / "plugin.json").read_text())["version"], "0.2.0")
            self.assertEqual(json.loads((root / "demo" / "scripts" / "command-metadata.json").read_text())["version"], "0.2.0")
            self.assertIn("version: 0.2.0", (root / "demo" / "skills" / "demo" / "SKILL.md").read_text())
            mk = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
            self.assertEqual(mk["plugins"][0]["version"], "0.2.0")

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, version="0.1.0")
            bump("demo", "0.2.0", root=root)
            self.assertEqual(bump("demo", "0.2.0", root=root), [])  # second call is a no-op

    def test_round_trip_preserves_key_order_and_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, version="0.1.0")
            before = json.loads((root / "demo" / ".claude-plugin" / "plugin.json").read_text())
            bump("demo", "0.2.0", root=root)
            after = json.loads((root / "demo" / ".claude-plugin" / "plugin.json").read_text())
            self.assertEqual(list(before.keys()), list(after.keys()))  # order intact
            self.assertEqual(after["name"], before["name"])
            self.assertEqual(after["keywords"], before["keywords"])  # untouched field survives

    def test_unknown_plugin_in_marketplace_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, plugin="demo")
            # plugin dir exists but is not listed in marketplace.json
            (root / "orphan" / ".claude-plugin").mkdir(parents=True)
            (root / "orphan" / ".claude-plugin" / "plugin.json").write_text('{"name": "orphan", "version": "0.1.0"}\n')
            with self.assertRaises(KeyError):
                bump("orphan", "0.2.0", root=root)

    def test_unknown_plugin_leaves_all_files_untouched(self):
        # Regression: the marketplace lookup used to run AFTER the plugin files
        # were written, so a KeyError left partial lockstep state behind.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, plugin="demo")
            (root / "orphan" / ".claude-plugin").mkdir(parents=True)
            pj = root / "orphan" / ".claude-plugin" / "plugin.json"
            pj.write_text('{"name": "orphan", "version": "0.1.0"}\n')
            before = pj.read_text()
            with self.assertRaises(KeyError):
                bump("orphan", "0.2.0", root=root)
            self.assertEqual(pj.read_text(), before, "no file may be written on failure")

    def test_missing_plugin_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root)
            with self.assertRaises(KeyError):
                bump("nonexistent", "0.2.0", root=root)


class TestSkillMdCoverage(unittest.TestCase):

    def test_bumps_skill_dir_not_named_after_plugin(self):
        # cabinet-of-imd's skill dir is crew-roster — the old hard-coded
        # skills/<plugin>/SKILL.md path silently missed it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, plugin="demo", version="0.1.0")
            other = root / "demo" / "skills" / "other-skill" / "SKILL.md"
            other.parent.mkdir(parents=True)
            other.write_text("---\nname: other-skill\nversion: 0.1.0\n---\n\n# other\n")
            changed = bump("demo", "0.2.0", root=root)
            self.assertIn("version: 0.2.0", other.read_text())
            self.assertIn(str(other.relative_to(root)), changed)

    def test_skill_without_frontmatter_version_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, plugin="demo", version="0.1.0")
            nover = root / "demo" / "skills" / "no-version" / "SKILL.md"
            nover.parent.mkdir(parents=True)
            before = "---\nname: no-version\n---\n\n# nv\n"
            nover.write_text(before)
            bump("demo", "0.2.0", root=root)
            self.assertEqual(nover.read_text(), before)

    def test_body_version_line_never_rewritten(self):
        # Frontmatter-scoped: a prose line starting `version:` stays intact.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_tree(root, plugin="demo", version="0.1.0")
            skill = root / "demo" / "skills" / "demo" / "SKILL.md"
            skill.write_text(
                "---\nname: demo\nversion: 0.1.0\n---\n\n# demo\n\nversion: 9.9.9 in prose\n")
            bump("demo", "0.2.0", root=root)
            text = skill.read_text()
            self.assertIn("version: 0.2.0", text.split("---")[1])  # frontmatter bumped
            self.assertIn("version: 9.9.9 in prose", text)          # body untouched


class TestSetJsonVersion(unittest.TestCase):

    def test_absent_file_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_set_json_version(Path(tmp) / "nope.json", "1.0.0"))


if __name__ == "__main__":
    unittest.main()
