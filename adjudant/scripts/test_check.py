"""Tests for adjudant/scripts/check.py."""

import contextlib
import io
import json as _json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from check import cli_main as check_cli, run_check, _read_brief, _folder_counts, _most_recent_dated, _handoff_info, _latest_dream_signal, _board_status


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestReadBrief(unittest.TestCase):

    def test_brief_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md",
                "---\ntype: project\nslug: test\nproject_type: coding\nstatus: active\n---\n\n# Test Project\n\nBody.")
            brief = _read_brief(root)
            self.assertTrue(brief["present"])
            self.assertEqual(brief["slug"], "test")
            self.assertEqual(brief["project_type"], "coding")
            self.assertEqual(brief["status"], "active")
            self.assertEqual(brief["title"], "Test Project")

    def test_brief_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_read_brief(Path(tmp))["present"])


class TestFolderCounts(unittest.TestCase):

    def test_counts_non_index_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "decisions").mkdir()
            (root / "decisions" / "_index.md").write_text("# idx")
            (root / "decisions" / "2026-05-26-a.md").write_text("a")
            (root / "decisions" / "2026-05-27-b.md").write_text("b")
            counts = _folder_counts(root)
            self.assertEqual(counts["decisions"], 2)

    def test_missing_folder_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            counts = _folder_counts(Path(tmp))
            self.assertEqual(counts, {})


class TestMostRecentDated(unittest.TestCase):

    def test_finds_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ["2026-05-26", "2026-05-28", "2026-05-27"]:
                (root / f"{d}.md").write_text("x")
            self.assertEqual(_most_recent_dated(root), "2026-05-28")

    def test_empty_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_most_recent_dated(Path(tmp)))

    def test_ignores_non_dated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "no-date.md").write_text("x")
            (root / "2026-05-26.md").write_text("y")
            self.assertEqual(_most_recent_dated(root), "2026-05-26")


class TestHandoffInfo(unittest.TestCase):

    def test_handoff_present_with_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "_handoff.md", "---\ntype: handoff\nupdated: 2026-05-25\n---\n\nbody")
            info = _handoff_info(root)
            self.assertTrue(info["present"])
            self.assertEqual(info["updated"], "2026-05-25")
            self.assertIsInstance(info["stale_hours"], float)

    def test_handoff_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_handoff_info(Path(tmp))["present"])


class TestLatestDreamSignal(unittest.TestCase):

    def test_picks_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dreams").mkdir()
            (root / "dreams" / "2026-05-20.md").write_text("# old")
            (root / "dreams" / "2026-05-26.md").write_text("# new\n**90 drift items**")
            sig = _latest_dream_signal(root)
            self.assertEqual(sig["date"], "2026-05-26")
            self.assertEqual(sig["drift_items"], 90)

    def test_no_dreams_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_latest_dream_signal(Path(tmp))["present"])

    def test_matches_real_dream_report_filename(self):
        # The dream verb writes {YYYY-MM-DD}-dream.md (reference/dream.md §Phase 3);
        # regression: the old regex only accepted bare {YYYY-MM-DD}.md.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dreams").mkdir()
            (root / "dreams" / "2026-06-30-dream.md").write_text("# report\n**12 drift items**")
            sig = _latest_dream_signal(root)
            self.assertTrue(sig["present"])
            self.assertEqual(sig["date"], "2026-06-30")
            self.assertEqual(sig["drift_items"], 12)


class TestRunCheck(unittest.TestCase):

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md",
                "---\ntype: project\nslug: test\nproject_type: coding\nstatus: active\n---\n\n# Test\n")
            _write(root / "_handoff.md", "---\ntype: handoff\nupdated: 2026-05-25\n---\nbody")
            (root / "decisions").mkdir()
            (root / "decisions" / "2026-05-26-a.md").write_text("---\ntype: decision\n---\n")
            (root / "sessions").mkdir()
            (root / "sessions" / "2026-05-26.md").write_text("---\ntype: session\n---\n")
            report = run_check(root)
            self.assertEqual(report["project"]["slug"], "test")
            self.assertEqual(report["counts"]["decisions"], 1)
            self.assertEqual(report["recent"]["last_decision"], "2026-05-26")
            self.assertTrue(report["handoff"]["present"])

    def test_status_block_with_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md",
                "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
            (root / "sessions").mkdir()
            (root / "sessions" / "2026-01-01.md").write_text("---\ntype: session\n---\n")
            report = run_check(root)
            self.assertEqual(report["status"]["declared"], "active")
            self.assertEqual(report["status"]["suggested"], "stale")
            self.assertIn("zone", report["status"])
            self.assertIn("zone_matches", report["status"])


class TestBoardStatus(unittest.TestCase):

    def _deck(self, root: Path, cards, columns=None, updated="2026-07-20") -> Path:
        deck = {
            "version": 1,
            "boardId": root.name,
            "title": "T",
            "subtitle": "Work-order board",
            "updated": updated,
            "columns": columns or [
                {"id": "backlog", "name": "Backlog"},
                {"id": "doing", "name": "Doing"},
                {"id": "done", "name": "Done"},
            ],
            "categories": ["build"],
            "cards": cards,
        }
        path = root / "board" / "board-data.json"
        _write(path, _json.dumps(deck))
        return path

    def test_board_absent_present_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(_board_status(root)["present"])
            report = run_check(root)  # no crash; the block is in the report
            self.assertFalse(report["board"]["present"])

    def test_board_present_counts_deck_columns(self):
        # Columns are counted per deck column id (custom lanes included),
        # never per a hardcoded status list; empty lanes still show as 0.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._deck(
                root,
                [
                    {"id": "a", "title": "A", "column": "backlog"},
                    {"id": "b", "title": "B", "column": "doing"},
                    {"id": "c", "title": "C", "column": "doing"},
                    {"id": "d", "title": "D", "column": "shipping"},
                ],
                columns=[
                    {"id": "backlog", "name": "Backlog"},
                    {"id": "doing", "name": "Doing"},
                    {"id": "done", "name": "Done"},
                    {"id": "shipping", "name": "Shipping"},
                ],
            )
            board = _board_status(root)
            self.assertTrue(board["present"])
            self.assertEqual(
                board["columns"],
                {"backlog": 1, "doing": 2, "done": 0, "shipping": 1})
            self.assertEqual(board["updated"], "2026-07-20")
            self.assertFalse(board["stale"])

    def test_board_stale_when_task_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deck_path = self._deck(root, [{"id": "a", "title": "A", "column": "backlog"}])
            task = root / "tasks" / "fix-the-thing.md"
            _write(task, "---\ntype: task\nstatus: todo\n---\n\n# Fix the thing\n")
            base = deck_path.stat().st_mtime
            os.utime(deck_path, (base, base))
            os.utime(task, (base + 60, base + 60))
            self.assertTrue(_board_status(root)["stale"])

    def test_board_fresh_when_deck_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deck_path = self._deck(root, [{"id": "a", "title": "A", "column": "backlog"}])
            task = root / "tasks" / "fix-the-thing.md"
            _write(task, "---\ntype: task\nstatus: todo\n---\n\n# Fix the thing\n")
            base = deck_path.stat().st_mtime
            os.utime(deck_path, (base, base))
            os.utime(task, (base - 60, base - 60))
            self.assertFalse(_board_status(root)["stale"])

    def test_board_unreadable_deck_present_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "board" / "board-data.json", "{not json")
            self.assertFalse(_board_status(root)["present"])


class TestCheckCost(unittest.TestCase):

    def _project(self, root: Path) -> None:
        _write(root / "brief.md",
            "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
        _write(root / "notes" / "a.md", "x" * 4000)

    def test_estimate_only_prints_cost_block_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = check_cli(["--project-dir", str(root), "--estimate-only"])
            self.assertEqual(rc, 0)
            payload = _json.loads(buf.getvalue())
            self.assertEqual(set(payload), {"cost"})
            self.assertEqual(
                set(payload["cost"]),
                {"est_read_tokens", "files", "bytes", "threshold", "warn"})
            self.assertEqual(payload["cost"]["files"], 2)

    def test_normal_run_includes_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = check_cli(["--project-dir", str(root)])
            self.assertEqual(rc, 0)
            payload = _json.loads(buf.getvalue())
            self.assertIn("cost", payload)
            self.assertIn("project", payload)


_CLEAN_BRIEF = (
    "---\ntype: project\nproject_type: coding\nslug: t\naliases: [t]\n"
    "status: active\ncreated: 2026-01-01\nupdated: 2026-01-01\ntags:\n  - project\n---\n\n# T\n")
_CLEAN_DECISION = (
    "---\ntype: decision\nstatus: active\ndate: 2026-07-27\ntags:\n  - decision\n---\n\nD\n")


class TestSchemaSection(unittest.TestCase):

    def test_clean_project_zero_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md", _CLEAN_BRIEF)
            _write(root / "decisions" / "2026-07-27-a.md", _CLEAN_DECISION)
            report = run_check(root)
            self.assertIn("schema", report)
            self.assertEqual(report["schema"]["flagged"], 0)
            self.assertEqual(report["schema"]["checked"], 2)

    def test_drift_counted_with_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md", _CLEAN_BRIEF)
            _write(root / "notes" / "n.md",
                   "---\ntype: note\nproject: \"[[projects/t/brief|t]]\"\n"
                   "created: 2026-01-01\nupdated: 2026-01-01\ntags:\n  - note\n---\nN\n")
            _write(root / "decisions" / "2026-07-27-a.md",
                   _CLEAN_DECISION.replace("status: active", "status: accepted"))
            report = run_check(root)
            self.assertEqual(report["schema"]["flagged"], 2)
            self.assertEqual(report["schema"]["counts"]["unknown_fields"], 1)
            self.assertEqual(report["schema"]["counts"]["status_invalid"], 1)
            files = [s["file"] for s in report["schema"]["samples"]]
            self.assertIn("notes/n.md", files)

    def test_report_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md", _CLEAN_BRIEF)
            report = run_check(root)
            _json.dumps(report)


class TestFreshnessSection(unittest.TestCase):
    """v0.22.0: run_check carries the freshness report (single walk shared
    with the schema section)."""

    def test_run_check_reports_freshness(self):
        from datetime import date
        with tempfile.TemporaryDirectory() as tmp:
            proot = Path(tmp)
            (proot / "notes").mkdir(parents=True)
            (proot / "notes" / "old.md").write_text(
                "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
                "tags:\n  - note\nvalid_until: 2026-01-15\n---\nbody\n")
            out = run_check(proot, today=date(2026, 7, 30))
            self.assertIn("freshness", out)
            self.assertEqual(len(out["freshness"]["expired"]), 1)
            self.assertEqual(out["freshness"]["counts"]["valid_until"], 1)


if __name__ == "__main__":
    unittest.main()
