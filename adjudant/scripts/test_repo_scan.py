"""Tests for repo_scan detectors + run_scan."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_scan as rs
import token_budget as tb
from test_repo_walk import _make_plugin, _marketplace, _write


class TestRepoScan(unittest.TestCase):

    def test_clean_repo_zero_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
            _marketplace(root, [("alpha", "1.0.0")])
            _write(root / "AGENTS.md", "# r\n")
            _write(root / "CLAUDE.md", "@AGENTS.md\n")
            report = rs.run_scan(root, today=date(2026, 7, 7))
            self.assertEqual(report["summary"]["drift_items"], 0)

    def test_version_mismatch_counts_as_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.1", skills=False)
            _marketplace(root, [("alpha", "1.0.0")])  # registry behind
            report = rs.run_scan(root, today=date(2026, 7, 7))
            self.assertTrue(report["version_coherence"]["mismatches"])
            self.assertGreaterEqual(report["summary"]["drift_items"], 1)

    def test_broken_symlink_on_adopted_plugin_is_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
            _marketplace(root, [("alpha", "1.0.0")])
            (root / "alpha" / ".gemini" / "skills" / "alpha").unlink()  # missing
            report = rs.run_scan(root, today=date(2026, 7, 7))
            self.assertGreaterEqual(report["summary"]["drift_items"], 1)
            self.assertTrue(report["symlink_integrity"]["issues"])

    def test_skillless_plugin_not_symlink_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "beta", "1.0.0", skills=False)
            _marketplace(root, [("beta", "1.0.0")])
            report = rs.run_scan(root, today=date(2026, 7, 7))
            self.assertEqual(report["symlink_integrity"]["issues"], [])

    def test_registration_gap_is_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=False)
            _make_plugin(root, "ghost", "1.0.0", skills=False)  # not in marketplace
            _marketplace(root, [("alpha", "1.0.0")])
            report = rs.run_scan(root, today=date(2026, 7, 7))
            self.assertIn("ghost", str(report["registration"]))
            self.assertGreaterEqual(report["summary"]["drift_items"], 1)

    def test_context_files_informational_not_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=False)
            _marketplace(root, [("alpha", "1.0.0")])
            _write(root / "AGENTS.md", "# r\n")
            _write(root / "CLAUDE.md", "@AGENTS.md\n")
            # plugin has no per-plugin AGENTS/CLAUDE — must NOT add drift
            report = rs.run_scan(root, today=date(2026, 7, 7))
            self.assertEqual(report["summary"]["drift_items"], 0)

    def test_report_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=True, adopt=True)
            _marketplace(root, [("alpha", "1.0.0")])
            report = rs.run_scan(root, today=date(2026, 7, 7))
            json.loads(json.dumps(report, default=str))


class TestRepoScanTokenBudget(unittest.TestCase):
    """v0.17.0 wired token_budget.py into the repo scan. reference/check.md
    instructs the model to render the block, so its absence is a silent
    contract break: `check repo` renders nothing and nobody notices."""

    def _repo(self, root: Path, *, skill_body: str = "x" * 400) -> None:
        """A repo with its OWN skill surface: one plugin, one skill, a SKILL.md
        and one reference file of known size."""
        _make_plugin(root, "alpha", "1.0.0", skills=True)
        _marketplace(root, [("alpha", "1.0.0")])
        canon = root / "alpha" / "skills" / "alpha"
        _write(canon / "SKILL.md", skill_body)
        _write(canon / "reference" / "guide.md", "y" * 800)

    def test_run_scan_carries_the_token_budget_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report = rs.run_scan(root, today=date(2026, 7, 7))
            block = report["token_budget"]
            self.assertEqual(set(block), {"surfaces", "total", "over_count"})
            self.assertGreater(block["total"], 0)

    def test_block_reports_the_scanned_repos_surfaces_not_adjudants(self):
        # The block used to be anchored at <install>/skills/adjudant no matter
        # what --project-dir said, so scanning anyone else's repo reported this
        # plugin's context cost as theirs. The numbers must come from `root`.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            block = rs.run_scan(root, today=date(2026, 7, 7))["token_budget"]
            files = {s["file"] for s in block["surfaces"]}
            self.assertEqual(
                files,
                {"alpha/skills/alpha/SKILL.md",
                 "alpha/skills/alpha/reference/guide.md"},
                "only the scanned repo's own surfaces may appear")
            self.assertNotIn("reference/vault-standards.md", files,
                             "adjudant's own reference set must not leak in")
            # 400 bytes // 4 + 800 bytes // 4, exactly the fixture.
            self.assertEqual(block["total"], 300)

    def test_total_tracks_the_scanned_repo(self):
        # Control: change the fixture, the number changes with it. Pins that
        # the report is measuring `root` and not something constant.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root, skill_body="x" * 4000)
            block = rs.run_scan(root, today=date(2026, 7, 7))["token_budget"]
            self.assertEqual(block["total"], 1200)

    def test_every_skill_in_the_repo_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            _make_plugin(root, "beta", "1.0.0", skills=True)
            _write(root / "beta" / "skills" / "beta" / "SKILL.md", "z" * 40)
            block = rs.run_scan(root, today=date(2026, 7, 7))["token_budget"]
            files = {s["file"] for s in block["surfaces"]}
            self.assertIn("beta/skills/beta/SKILL.md", files)
            self.assertIn("alpha/skills/alpha/SKILL.md", files)

    def test_repo_without_skills_reports_an_empty_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_plugin(root, "alpha", "1.0.0", skills=False)
            _marketplace(root, [("alpha", "1.0.0")])
            block = rs.run_scan(root, today=date(2026, 7, 7))["token_budget"]
            self.assertEqual(block["surfaces"], [])
            self.assertEqual(block["total"], 0)
            self.assertEqual(block["over_count"], 0)

    def test_each_surface_carries_the_keys_check_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report = rs.run_scan(root, today=date(2026, 7, 7))
            for s in report["token_budget"]["surfaces"]:
                self.assertEqual(set(s), {"file", "tokens", "budget", "over"})

    def test_declared_budgets_reach_the_report(self):
        # The budget lookup stays SKILL-relative, so a declared limit still
        # finds its surface wherever the repo sits on disk. Also a canary on
        # BUDGETS itself: a test that mutates the module-level dict without
        # restoring it turns this red.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            skill = [s for s in rs.run_scan(root, today=date(2026, 7, 7))
                     ["token_budget"]["surfaces"]
                     if s["file"].endswith("SKILL.md")][0]
            self.assertEqual(skill["budget"], tb.BUDGETS["SKILL.md"])
            self.assertFalse(skill["over"])

    def test_adjudants_own_repo_still_measures_adjudant(self):
        # The regression must not swing the other way: scanning THIS repo has
        # to find this plugin's surfaces, budgets attached.
        repo_root = Path(rs.__file__).resolve().parent.parent.parent
        block = rs.token_budget_for_repo(repo_root)
        by_file = {s["file"]: s for s in block["surfaces"]}
        self.assertIn("adjudant/skills/adjudant/SKILL.md", by_file)
        standards = by_file["adjudant/skills/adjudant/reference/vault-standards.md"]
        self.assertEqual(standards["budget"],
                         tb.BUDGETS["reference/vault-standards.md"])
        self.assertFalse(standards["over"])

    def test_token_budget_survives_json_serialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report = rs.run_scan(root, today=date(2026, 7, 7))
            round_tripped = json.loads(json.dumps(report, default=str))
            self.assertIn("token_budget", round_tripped)


class TestRepoScanCost(unittest.TestCase):

    def _project(self, root: Path) -> None:
        _write(root / "README.md", "# repo\n" + "x" * 4000)
        _write(root / "scripts" / "helper.py", "# helper\nprint('hi')\n")
        _write(root / "data" / "config.json", "{}\n")

    def test_estimate_only_is_cost_only_and_stat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = rs.cli_main(["--project-dir", str(root), "--estimate-only"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(set(payload), {"cost"})
            self.assertEqual(payload["cost"]["files"], 3)

    def test_normal_run_includes_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = rs.cli_main(["--project-dir", str(root), "--today", "2026-07-07"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("cost", payload)
            self.assertIn("summary", payload)


if __name__ == "__main__":
    unittest.main()
