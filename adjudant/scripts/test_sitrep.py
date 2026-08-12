"""Tests for adjudant/scripts/sitrep.py."""

import contextlib
import io
import json as _json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sitrep import (
    cli_main as sitrep_cli, run_sitrep, _next_step,
    _repo_brief, _server_brief,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestNextStep(unittest.TestCase):

    def test_next_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "_handoff.md", "---\nupdated: 2026-07-02\n---\n\nNEXT: wire up the sitrep verb\n")
            self.assertEqual(_next_step(root), "wire up the sitrep verb")

    def test_next_absent_when_no_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_next_step(Path(tmp)))

    def test_next_none_when_no_next_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "_handoff.md", "---\nupdated: 2026-07-02\n---\n\nJust some prose, no marker.\n")
            self.assertIsNone(_next_step(root))


class TestRunSitrep(unittest.TestCase):

    def _project(self, root: Path) -> None:
        _write(root / "brief.md",
               "---\ntype: project\nslug: demo\nproject_type: coding\nstatus: active\n---\n\n# Demo Project\n\nBody.")
        _write(root / "_handoff.md", "---\nupdated: 2026-07-02\n---\n\nNEXT: ship v0.10.0\n")
        _write(root / "sessions" / "2026-07-01.md", "# session\n")
        _write(root / "decisions" / "2026-06-30-pick-approach.md", "# decision\n")
        _write(root / "notes" / "idea.md", "# note\n")

    def test_populated_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            _write(root / ".remember" / "today-2026-07-02.md", "- 09:00 · started work\n- 10:30 · wrote code\n")
            now = datetime(2026, 7, 2, 11, 0)  # 30m after last activity
            rep = run_sitrep(root, vault_path=Path("/vault/demo"), now=now)

            self.assertEqual(rep["purpose"], "Demo Project")
            self.assertEqual(rep["vault_path"], "/vault/demo")
            self.assertEqual(rep["next_step"], "ship v0.10.0")
            self.assertEqual(rep["whats_done"]["last_session"], "2026-07-01")
            self.assertEqual(rep["whats_done"]["last_decision"], "2026-06-30")
            self.assertEqual(rep["whats_done"]["counts"]["notes"], 1)
            self.assertEqual(rep["whats_done"]["total_files"], 3)  # session+decision+note
            # 30 minutes → green light
            self.assertEqual(rep["freshness"]["light"], "\U0001f7e2")
            self.assertEqual(rep["freshness"]["age"], "30m")

    def test_stale_activity_turns_light_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            _write(root / ".remember" / "today-2026-07-01.md", "- 09:00 · old work\n")
            now = datetime(2026, 7, 2, 18, 0)  # >8h later
            rep = run_sitrep(root, now=now)
            self.assertEqual(rep["freshness"]["light"], "\U0001f534")  # red

    def test_missing_handoff_yields_null_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md", "---\ntype: project\nslug: demo\n---\n\n# Demo\n")
            now = datetime(2026, 7, 2, 12, 0)
            rep = run_sitrep(root, now=now)
            self.assertIsNone(rep["next_step"])

    def test_breadcrumb_flow_reads_remember_from_code_root(self):
        # In the real flow the vault project dir and the code root are DIFFERENT
        # directories: .remember/ lives at the code root only. Freshness must
        # come from there, not from the vault dir (regression: always-⚪ bug).
        with tempfile.TemporaryDirectory() as tmp:
            vault_proj = Path(tmp) / "vault" / "projects" / "demo"
            code_root = Path(tmp) / "code"
            self._project(vault_proj)
            _write(code_root / ".remember" / "today-2026-07-02.md", "- 10:30 · wrote code\n")
            now = datetime(2026, 7, 2, 11, 0)
            rep = run_sitrep(vault_proj, now=now, code_root=code_root)
            self.assertEqual(rep["freshness"]["light"], "\U0001f7e2")
            self.assertEqual(rep["freshness"]["age"], "30m")

    def test_status_block_with_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md",
                "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
            (root / "sessions").mkdir()
            (root / "sessions" / "2026-01-01.md").write_text("---\ntype: session\n---\n")
            now = datetime(2026, 7, 2, 12, 0)
            rep = run_sitrep(root, now=now)
            self.assertEqual(rep["status"]["declared"], "active")
            self.assertEqual(rep["status"]["suggested"], "stale")
            self.assertIn("zone", rep["status"])
            self.assertIn("zone_matches", rep["status"])

    def test_empty_project_no_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 7, 2, 12, 0)
            rep = run_sitrep(root, now=now)
            self.assertFalse(rep["project"]["present"])
            self.assertIsNone(rep["purpose"])
            self.assertIsNone(rep["were_doing"])
            self.assertEqual(rep["freshness"]["light"], "⚪")  # white — age unknown
            self.assertEqual(rep["whats_done"]["counts"], {})


class TestSitrepBoard(unittest.TestCase):

    def _deck(self, root: Path, cards) -> Path:
        deck = {
            "version": 1,
            "boardId": root.name,
            "title": "T",
            "subtitle": "Work-order board",
            "updated": "2026-07-20",
            "columns": [
                {"id": "backlog", "name": "Backlog"},
                {"id": "next", "name": "Next"},
                {"id": "doing", "name": "Doing"},
                {"id": "review", "name": "Review"},
                {"id": "done", "name": "Done"},
                {"id": "icebox", "name": "Icebox"},
            ],
            "categories": ["build"],
            "cards": cards,
        }
        path = root / "board" / "board-data.json"
        _write(path, _json.dumps(deck))
        return path

    def test_board_line_rendered(self):
        # open = every column except done and icebox; doing = the doing column.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md", "---\ntype: project\nslug: demo\n---\n\n# Demo\n")
            self._deck(root, [
                {"id": "a", "title": "A", "column": "backlog"},
                {"id": "b", "title": "B", "column": "doing"},
                {"id": "c", "title": "C", "column": "done"},
                {"id": "d", "title": "D", "column": "icebox"},
            ])
            rep = run_sitrep(root, now=datetime(2026, 7, 21, 12, 0))
            self.assertTrue(rep["board"]["present"])
            self.assertEqual(rep["board"]["open"], 2)
            self.assertEqual(rep["board"]["doing"], 1)
            self.assertEqual(rep["board"]["line"], "Board: 2 open (1 in motion)")

    def test_board_line_stale_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md", "---\ntype: project\nslug: demo\n---\n\n# Demo\n")
            deck_path = self._deck(root, [{"id": "a", "title": "A", "column": "doing"}])
            task = root / "tasks" / "a.md"
            _write(task, "---\ntype: task\nstatus: doing\n---\n\n# A\n")
            base = deck_path.stat().st_mtime
            os.utime(deck_path, (base, base))
            os.utime(task, (base + 60, base + 60))
            rep = run_sitrep(root, now=datetime(2026, 7, 21, 12, 0))
            self.assertEqual(rep["board"]["line"], "Board: 1 open (1 in motion), stale")

    def test_board_absent_no_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "brief.md", "---\ntype: project\nslug: demo\n---\n\n# Demo\n")
            rep = run_sitrep(root, now=datetime(2026, 7, 21, 12, 0))
            self.assertFalse(rep["board"]["present"])
            self.assertNotIn("line", rep["board"])


class TestSitrepCost(unittest.TestCase):

    def test_estimate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brief.md").write_text(
                "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sitrep_cli(["--project-dir", str(root), "--estimate-only"])
            self.assertEqual(rc, 0)
            payload = _json.loads(buf.getvalue())
            self.assertEqual(set(payload), {"cost"})

    def test_normal_run_includes_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brief.md").write_text(
                "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sitrep_cli(["--project-dir", str(root)])
            self.assertEqual(rc, 0)
            self.assertIn("cost", _json.loads(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()


class TestRepoBrief(unittest.TestCase):
    """Git state must degrade to {present: False}, never raise — orientation
    that can crash is worse than orientation that says 'unknown'."""

    def test_absent_without_code_root(self):
        self.assertFalse(_repo_brief(None)["present"])

    def test_absent_when_not_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_repo_brief(Path(tmp))["present"])

    def test_reads_a_real_repo(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            run = lambda *a: subprocess.run(["git", "-C", str(root), *a],
                                            capture_output=True, env=env)
            run("init", "-q", "-b", "main")
            _write(root / "a.txt", "one\n")
            run("add", "-A"); run("commit", "-qm", "first commit")
            info = _repo_brief(root)
            self.assertTrue(info["present"])
            self.assertEqual(info["branch"], "main")
            self.assertFalse(info["detached"])
            self.assertEqual(info["dirty"], 0)
            self.assertEqual(info["head"]["subject"], "first commit")
            self.assertEqual(len(info["recent"]), 1)
            # an untracked file counts as dirty
            _write(root / "b.txt", "two\n")
            self.assertEqual(_repo_brief(root)["dirty"], 1)


class TestServerBrief(unittest.TestCase):
    """launch.json drives the probe; a down server is an answer, not an error."""

    def test_absent_without_code_root(self):
        self.assertFalse(_server_brief(None)["present"])

    def test_absent_without_launch_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_server_brief(Path(tmp))["present"])

    def test_unreadable_launch_json_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / ".claude" / "launch.json", "{not json")
            out = _server_brief(root)
            self.assertFalse(out["present"])

    def test_down_server_reports_false_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # port 1 is reserved and never listening
            _write(root / ".claude" / "launch.json",
                   '{"configurations":[{"name":"x","port":1}]}')
            out = _server_brief(root)
            self.assertTrue(out["present"])
            self.assertEqual(len(out["servers"]), 1)
            self.assertFalse(out["servers"][0]["up"])

    def test_entries_without_a_port_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / ".claude" / "launch.json",
                   '{"configurations":[{"name":"noport"},{"name":"bad","port":"5184"}]}')
            self.assertFalse(_server_brief(root)["present"])
