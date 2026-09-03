"""Tests for scripts/board_bridge.py + hooks/scripts/task-ledger.py.

The ledger hook is one script wired to both TaskCreated and TaskCompleted;
it reads the event name from the payload's hook_event_name and appends one
JSONL entry to $TMPDIR/adjudant-task-ledger-{session_id}.jsonl, never reading
the file in-session. Since v3 nothing replays that ledger into the vault: the
statusline reads it and it dies with the TMPDIR. The bridge is now an
ensure-board pass and nothing else. Regression focus: the ledger never
manufactures task notes, --ensure-only births the board from notes that
already exist, and the hook itself still logs cleanly.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import board_bridge

SCRIPTS = Path(__file__).resolve().parent
HOOK = SCRIPTS.parent / "hooks" / "scripts" / "task-ledger.py"

# Hyphenated filename: load via importlib, same interpreter (main invoked
# in-process with stdin patched, mirroring test_commit_log's approach).
_spec = importlib.util.spec_from_file_location("task_ledger", HOOK)
task_ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(task_ledger)


def _entry(tid: str, subject: str, *, status: str = "created",
           description: str = "") -> dict:
    return {"id": tid, "subject": subject, "status": status,
            "ts": "2026-07-21T10:00:00", "description": description}


class TestTaskLedger(unittest.TestCase):
    """The TaskCreated/TaskCompleted hook: append-only JSONL in TMPDIR."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._tmpdir_before = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = str(self.tmp)

    def tearDown(self):
        if self._tmpdir_before is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = self._tmpdir_before
        self._tmp.cleanup()

    def _run(self, payload) -> int:
        """Invoke main() with stdin patched (dict is JSON-encoded; a str is
        fed raw for malformed-input tests)."""
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        stdin_before = sys.stdin
        sys.stdin = io.StringIO(raw)
        try:
            return task_ledger.main()
        finally:
            sys.stdin = stdin_before

    @staticmethod
    def _payload(event: str = "TaskCreated", **over) -> dict:
        p = {
            "hook_event_name": event,
            "session_id": "sess-1",
            "task_id": "T-1",
            "task_subject": "Fix the widget",
            "task_description": "Make it stop rattling",
        }
        p.update(over)
        return p

    def _ledger(self, sid: str = "sess-1") -> Path:
        return self.tmp / f"adjudant-task-ledger-{sid}.jsonl"

    def test_created_event_appends_entry(self):
        rc = self._run(self._payload("TaskCreated"))
        self.assertEqual(rc, 0)
        lines = self._ledger().read_text().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["id"], "T-1")
        self.assertEqual(entry["subject"], "Fix the widget")
        self.assertEqual(entry["status"], "created")
        self.assertEqual(entry["description"], "Make it stop rattling")
        self.assertTrue(entry["ts"])

    def test_completed_event_marks_completed(self):
        rc = self._run(self._payload("TaskCompleted"))
        self.assertEqual(rc, 0)
        entry = json.loads(self._ledger().read_text().splitlines()[0])
        self.assertEqual(entry["status"], "completed")

    def test_both_events_append_to_one_file(self):
        # One script wired to both events: the pair lands in the same ledger,
        # in fire order, so the bridge can take latest-status-per-id.
        self._run(self._payload("TaskCreated"))
        self._run(self._payload("TaskCompleted"))
        lines = self._ledger().read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["status"], "created")
        self.assertEqual(json.loads(lines[1])["status"], "completed")

    def test_unrelated_event_no_write(self):
        rc = self._run(self._payload("PostToolUse"))
        self.assertEqual(rc, 0)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_missing_ids_no_write(self):
        # No session_id means no ledger path; no task_id means no key to
        # bridge on. Both gate the write entirely.
        for over in ({"session_id": ""}, {"task_id": ""}):
            rc = self._run(self._payload(**over))
            self.assertEqual(rc, 0)
            self.assertEqual(list(self.tmp.iterdir()), [],
                             f"payload override {over!r} must write nothing")

    def test_hostile_session_id_no_write(self):
        # A session_id with path separators must never steer the ledger
        # outside TMPDIR (or anywhere at all).
        rc = self._run(self._payload(session_id="../evil"))
        self.assertEqual(rc, 0)
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertFalse((self.tmp.parent / "adjudant-task-ledger-evil.jsonl").exists())

    def test_malformed_stdin_exits_zero(self):
        rc = self._run("not json {")
        self.assertEqual(rc, 0)
        self.assertEqual(list(self.tmp.iterdir()), [])


class _BridgeCase(unittest.TestCase):
    """Temp vault project + ledger file helpers, stdout/stderr captured."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = self.tmp / "vault" / "projects" / "demo"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_ledger(self, entries) -> Path:
        path = self.tmp / "ledger.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return path

    def _main(self, argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = board_bridge.main(argv)
        return rc, out.getvalue()

    def _deck(self) -> dict:
        return json.loads((self.project / "board" / "board-data.json").read_text())


class TestLedgerNeverBecomesVaultNotes(_BridgeCase):
    """v3: an unfinished harness todo is not a vault note. Every todo that
    never emitted a completion event used to become a permanent markdown file
    at session end, which is why tasks/ accumulated without limit."""

    def test_bridge_flag_is_gone(self):
        ledger = self._write_ledger([_entry("T-1", "Fix the widget")])
        with self.assertRaises(SystemExit):
            self._main(["--bridge", str(ledger), "--project-dir", str(self.project)])

    def test_ensure_only_writes_no_task_notes(self):
        (self.project / "tasks").mkdir(parents=True, exist_ok=True)
        rc, _ = self._main(["--ensure-only", "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertEqual(list((self.project / "tasks").glob("*.md")), [])

    def test_ensure_only_still_births_the_board_from_existing_tasks(self):
        tasks = self.project / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "real-card.md").write_text(
            "---\ntype: task\nstatus: doing\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n\n# Real card\n")
        rc, _ = self._main(["--ensure-only", "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertTrue((self.project / "board" / "board-data.json").is_file())
        ids = [c["id"] for c in self._deck()["cards"]]
        self.assertIn("real-card", ids)


class TestEnsureOnly(_BridgeCase):

    def test_ensure_only_births_board_from_tasks(self):
        # No ledger anywhere: --ensure-only is a pure ensure_board pass, so
        # an existing task note still births the board and no task notes
        # are invented.
        tasks = self.project / "tasks"
        tasks.mkdir()
        (tasks / "one-task.md").write_text(
            "---\ntype: task\nstatus: todo\n---\n\n## Task\n")
        rc, out = self._main(["--ensure-only", "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().splitlines()[-1], "created")
        self.assertIn("one-task", [c["id"] for c in self._deck()["cards"]])
        self.assertEqual(sorted(p.name for p in tasks.glob("*.md")), ["one-task.md"])


class TestKebab(unittest.TestCase):

    def test_kebab_forms(self):
        self.assertEqual(board_bridge.kebab("Fix the widget"), "fix-the-widget")
        self.assertEqual(board_bridge.kebab("  Ship v2.0 (final!)  "), "ship-v2-0-final")
        self.assertEqual(board_bridge.kebab("???"), "")

    def test_kebab_bounded(self):
        self.assertLessEqual(len(board_bridge.kebab("word " * 60)), 80)


if __name__ == "__main__":
    unittest.main()
