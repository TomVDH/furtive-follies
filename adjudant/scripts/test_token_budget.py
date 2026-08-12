"""Tests for token_budget.py: report-only context-cost accounting."""

import sys
import tempfile
import unittest
from pathlib import Path

import token_budget as tb


class TestReport(unittest.TestCase):
    """BUDGETS is production state, module-level and shared by the whole test
    process (repo_scan.run_scan reads it through token_budget_report). A test
    that pops "SKILL.md" and never puts it back deletes a REAL default entry
    for every test that runs after it, which is why setUp/tearDown snapshot
    and restore the whole dict rather than any single key."""

    def setUp(self):
        self._budgets = dict(tb.BUDGETS)

    def tearDown(self):
        tb.BUDGETS.clear()
        tb.BUDGETS.update(self._budgets)

    def _skill(self, tmp: Path) -> Path:
        root = tmp / "adjudant"
        (root / "reference").mkdir(parents=True)
        (root / "SKILL.md").write_text("x" * 4000)          # ~1000 tok
        (root / "reference" / "sync.md").write_text("y" * 400)   # ~100 tok
        return root

    def test_counts_tokens_per_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._skill(Path(tmp))
            rep = tb.report(root)
            by = {s["file"]: s["tokens"] for s in rep["surfaces"]}
            self.assertEqual(by["SKILL.md"], 1000)
            self.assertEqual(by["reference/sync.md"], 100)
            self.assertEqual(rep["total"], 1100)

    def test_flags_over_budget_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._skill(Path(tmp))
            tb.BUDGETS["SKILL.md"] = 500          # deliberately low; tearDown restores
            rep = tb.report(root)
            skill = [s for s in rep["surfaces"] if s["file"] == "SKILL.md"][0]
            self.assertTrue(skill["over"])
            self.assertEqual(rep["over_count"], 1)

    def test_undeclared_surface_has_no_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._skill(Path(tmp))
            rep = tb.report(root)
            sync = [s for s in rep["surfaces"] if s["file"] == "reference/sync.md"][0]
            self.assertIsNone(sync["budget"])
            self.assertFalse(sync["over"])

    def test_missing_skill_root_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rep = tb.report(Path(tmp) / "nope")
            self.assertEqual(rep["surfaces"], [])
            self.assertEqual(rep["total"], 0)


class TestSharedStateHygiene(unittest.TestCase):
    """The over-budget test has to override a BUDGETS entry. It must put back
    exactly what it found: `pop()` deletes a REAL default and every later
    reader in the process (repo_scan.run_scan goes through
    token_budget_report) then sees a hole where production declares a limit."""

    def test_the_override_test_restores_what_it_found(self):
        # Order-independent by construction: run that one test here and look
        # at the dict afterwards, rather than hoping method ordering puts a
        # bare assertion in the right place.
        before = dict(tb.BUDGETS)
        self.assertIn("SKILL.md", before,
                      "something earlier in this process already ate the default")
        suite = unittest.TestLoader().loadTestsFromName(
            "TestReport.test_flags_over_budget_without_failing",
            sys.modules[__name__])
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(result.failures + result.errors, [])
        self.assertEqual(tb.BUDGETS, before,
                         "the override must be restored, never popped")

    def test_shipped_defaults_are_intact(self):
        self.assertEqual(tb.BUDGETS["SKILL.md"], 2000)
        self.assertEqual(tb.BUDGETS["reference/vault-standards.md"], 2500)
        self.assertEqual(tb.BUDGETS["reference/voice.md"], 600)


if __name__ == "__main__":
    unittest.main()
