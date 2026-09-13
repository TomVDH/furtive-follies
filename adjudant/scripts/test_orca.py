"""Tests for adjudant/scripts/orca.py — the ORCA engine."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import orca


class TestOrcaRun(unittest.TestCase):

    def test_scaffold_creates_structure(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            result = orca.run_orca(root)
            self.assertEqual(result["action"], "run")
            self.assertIn("run_id", result)
            self.assertEqual(len(result["lanes"]), 7)
            orca_dir = root / orca.ORCA_ROOT
            self.assertTrue((orca_dir / "ORCA.md").is_file())
            self.assertTrue((orca_dir / "briefs" / "orca-chair.md").is_file())
            self.assertTrue((orca_dir / "briefs" / "orca-probe.md").is_file())
            self.assertTrue((orca_dir / "briefs" / "orca-g-review.md").is_file())

    def test_scaffold_is_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            r1 = orca.run_orca(root)
            r2 = orca.run_orca(root)
            self.assertGreater(len(r1["created"]), 0)
            self.assertEqual(len(r2["created"]), 0)

    def test_custom_lanes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            result = orca.run_orca(root, lanes=("x", "y"))
            self.assertEqual(result["lanes"], ["x", "y"])
            self.assertTrue(
                (root / orca.ORCA_ROOT / "briefs" / "orca-x-review.md").is_file())
            self.assertTrue(
                (root / orca.ORCA_ROOT / "briefs" / "orca-y-review.md").is_file())

    def test_current_pointer(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            result = orca.run_orca(root)
            current = root / orca.ORCA_ROOT / "runs" / "CURRENT"
            self.assertTrue(current.exists())


class TestOrcaSeats(unittest.TestCase):

    def test_seats_after_run(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            orca.run_orca(root)
            result = orca.seats_orca(root)
            self.assertEqual(result["action"], "seats")
            self.assertGreater(len(result["seats"]), 0)
            self.assertNotIn("missing", result)

    def test_seats_without_run(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            result = orca.seats_orca(root)
            self.assertIn("error", result)


class TestOrcaStatus(unittest.TestCase):

    def test_status_all_outstanding(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            orca.run_orca(root)
            result = orca.status_orca(root)
            self.assertEqual(result["action"], "status")
            self.assertGreater(len(result["outstanding"]), 0)
            self.assertFalse(result["complete"])

    def test_status_after_reports(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            r = orca.run_orca(root)
            run_dir = Path(r["orca_dir"]) / "runs" / r["run_id"]
            for lane in r["lanes"]:
                (run_dir / f"seat-{lane}.md").write_text(
                    "---\ntype: doc\n---\n\n# Report\n")
            result = orca.status_orca(root)
            self.assertEqual(len(result["outstanding"]), 0)
            self.assertTrue(result["complete"])

    def test_status_without_run(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            result = orca.status_orca(root)
            self.assertIn("error", result)


class TestOrcaClose(unittest.TestCase):

    def test_close_writes_chair_report(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            r = orca.run_orca(root)
            run_dir = Path(r["orca_dir"]) / "runs" / r["run_id"]
            for lane in ("a", "b"):
                (run_dir / f"seat-{lane}.md").write_text(
                    "---\ntype: doc\n---\n\n# Report\n\n"
                    "- **File**: foo.py\n")
            result = orca.close_orca(root)
            self.assertEqual(result["action"], "close")
            self.assertEqual(result["total_findings"], 2)
            self.assertTrue(Path(result["chair_report"]).is_file())

    def test_close_without_run(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            result = orca.close_orca(root)
            self.assertIn("error", result)


class TestOrcaCLI(unittest.TestCase):

    def test_cli_run(self):
        with tempfile.TemporaryDirectory() as t:
            rc = orca.main(["--run", "--project-root", t])
            self.assertEqual(rc, 0)

    def test_cli_seats(self):
        with tempfile.TemporaryDirectory() as t:
            orca.main(["--run", "--project-root", t])
            rc = orca.main(["--seats", "--project-root", t])
            self.assertEqual(rc, 0)

    def test_cli_status(self):
        with tempfile.TemporaryDirectory() as t:
            orca.main(["--run", "--project-root", t])
            rc = orca.main(["--status", "--project-root", t])
            self.assertEqual(rc, 0)

    def test_cli_close(self):
        with tempfile.TemporaryDirectory() as t:
            orca.main(["--run", "--project-root", t])
            rc = orca.main(["--close", "--project-root", t])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
