"""Tests for repo_walk primitives."""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_walk as rw


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _make_plugin(root: Path, name: str, version: str, *, skills: bool = False, adopt: bool = False) -> None:
    pdir = root / name
    _write(pdir / ".claude-plugin" / "plugin.json",
           '{\n  "name": "%s",\n  "version": "%s"\n}\n' % (name, version))
    if skills:
        canon = pdir / "skills" / name
        _write(canon / "SKILL.md", "---\nname: %s\n---\n# %s\n" % (name, name))
        if adopt:
            for h in ("source", ".claude", ".gemini"):
                d = pdir / h / "skills"
                d.mkdir(parents=True)
                (d / name).symlink_to(Path("../../skills") / name)


def _marketplace(root: Path, entries: list[tuple[str, str]]) -> None:
    plugins = ",\n".join(
        '    {"name": "%s", "version": "%s", "source": "./%s"}' % (n, v, n) for n, v in entries)
    _write(root / ".claude-plugin" / "marketplace.json",
           '{\n  "plugins": [\n%s\n  ]\n}\n' % plugins)


class TestRepoWalk(unittest.TestCase):

    def test_is_marketplace_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(rw.is_marketplace_repo(root))
            _marketplace(root, [("a", "1.0.0")])
            self.assertTrue(rw.is_marketplace_repo(root))

    def test_walk_plugins_discovers_and_reads_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.2.3", skills=True, adopt=True)
            _make_plugin(root, "beta", "0.1.0", skills=False)
            _marketplace(root, [("alpha", "1.2.3"), ("beta", "0.1.0")])
            plugins = {p.name: p for p in rw.walk_plugins(root)}
            self.assertEqual(set(plugins), {"alpha", "beta"})
            self.assertEqual(plugins["alpha"].version, "1.2.3")
            self.assertTrue(plugins["alpha"].has_skills)
            self.assertFalse(plugins["beta"].has_skills)

    def test_symlink_status_ok_when_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
            p = next(p for p in rw.walk_plugins(root) if p.name == "alpha")
            st = rw.plugin_symlink_status(p)
            self.assertTrue(st["adopted"])
            self.assertEqual(st["links"], {"source": "ok", ".claude": "ok", ".gemini": "ok"})

    def test_symlink_status_missing_and_dangling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
            # one harness symlink removed (missing); one repointed at a bogus
            # target (dangling); source stays ok.
            (root / "alpha" / ".gemini" / "skills" / "alpha").unlink()
            claude_link = root / "alpha" / ".claude" / "skills" / "alpha"
            claude_link.unlink()
            claude_link.symlink_to(Path("../../skills") / "nonexistent")
            p = next(p for p in rw.walk_plugins(root) if p.name == "alpha")
            st = rw.plugin_symlink_status(p)
            self.assertEqual(st["links"]["source"], "ok")
            self.assertEqual(st["links"][".gemini"], "missing")
            self.assertEqual(st["links"][".claude"], "dangling")  # repointed elsewhere
            self.assertTrue(st["adopted"])  # still adopted: source link ok

    def test_symlink_status_na_without_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "beta", "1.0.0", skills=False)
            p = next(p for p in rw.walk_plugins(root) if p.name == "beta")
            st = rw.plugin_symlink_status(p)
            self.assertFalse(st["adopted"])
            self.assertEqual(set(st["links"].values()), {"n/a"})

    def test_context_files_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(rw.context_files_status(root),
                             {"agents": False, "claude": False, "claude_imports_agents": False})
            _write(root / "AGENTS.md", "# repo\n")
            _write(root / "CLAUDE.md", "@AGENTS.md\n\n# overrides\n")
            self.assertEqual(rw.context_files_status(root),
                             {"agents": True, "claude": True, "claude_imports_agents": True})

    def test_plan_file_ages_flags_old_unmarked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "docs" / "superpowers" / "plans" / "old.md", "# plan\nwork\n")
            _write(root / "docs" / "superpowers" / "specs" / "done.md",
                   "---\nstatus: done\n---\n# spec\n")
            import os
            import time
            # Age is measured against `today`, so the mtime must be relative to
            # THAT date, not to the wall clock. Mixing the two made the test a
            # time bomb: it asserted a 60-day-old file and silently became a
            # 25-day-old one as real time drifted past the frozen date, then
            # started failing ~2026-08-06.
            today = date(2026, 7, 7)
            old = root / "docs" / "superpowers" / "plans" / "old.md"
            past = time.mktime(today.timetuple()) - 60 * 86400
            os.utime(old, (past, past))
            ages = {a["path"]: a for a in rw.plan_file_ages(root, today, stale_days=30)}
            self.assertTrue(any("old.md" in k and v["stale"] for k, v in ages.items()))
            # a doc with a completion marker is not listed as stale
            self.assertFalse(any("done.md" in k and v["stale"] for k, v in ages.items()))


if __name__ == "__main__":
    unittest.main()
