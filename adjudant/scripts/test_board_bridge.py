"""Tests for scripts/board_bridge.py + hooks/scripts/task-ledger.py.

The ledger hook is one script wired to both TaskCreated and TaskCompleted;
it reads the event name from the payload's hook_event_name and appends one
JSONL entry to $TMPDIR/adjudant-task-ledger-{session_id}.jsonl, never reading
the file in-session. The bridge replays that ledger at session end: ids whose
latest status is not completed become tasks/{kebab-subject}.md notes (deduped
against existing slugs, schema-conformant per templates/task.md, status: todo,
description in the ## Task section), then board.ensure_board runs. Regression
focus: completed ids skipped, malformed lines skipped without crash, bridge
triggers board birth, --ensure-only works without any ledger.
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
from _vault_walk import parse_frontmatter

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

    def _write_ledger(self, entries, *, raw_lines: tuple = ()) -> Path:
        path = self.tmp / "ledger.jsonl"
        text = "".join(json.dumps(e) + "\n" for e in entries)
        for raw in raw_lines:
            text += raw + "\n"
        path.write_text(text)
        return path

    def _main(self, argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = board_bridge.main(argv)
        return rc, out.getvalue()

    def _deck(self) -> dict:
        return json.loads((self.project / "board" / "board-data.json").read_text())


class TestBridgeSurvivors(_BridgeCase):

    def test_survivor_bridged(self):
        # A created-and-never-completed id survives the session and becomes a
        # schema-conformant task note: status todo, description under ## Task.
        ledger = self._write_ledger([
            _entry("T-1", "Fix the widget", description="Make it stop rattling"),
        ])
        rc, _ = self._main(["--bridge", str(ledger), "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        note = self.project / "tasks" / "fix-the-widget.md"
        self.assertTrue(note.is_file())
        fm, body = parse_frontmatter(note.read_text())
        self.assertEqual(fm.fields.get("type"), "task")
        self.assertEqual(fm.fields.get("status"), "todo")
        # v0.16.0: membership is the path — no project: field on written notes
        self.assertNotIn("project", fm.fields)
        self.assertIn("task", fm.fields.get("tags") or [])
        # The template's trailing guidance comments must not survive into a
        # mechanically written note: the YAML parser keeps comments on
        # quoted-value lines (`code: ""  # ...`), which would poison card ids.
        self.assertNotIn("#", fm.raw)
        task_section = body.split("## Task", 1)[1].split("## Notes", 1)[0]
        self.assertIn("Make it stop rattling", task_section)

    def test_completed_skipped(self):
        # A TaskCompleted event for an id marks it completed: latest status
        # wins, so the created entry earlier in the file does not resurrect it.
        ledger = self._write_ledger([
            _entry("T-1", "Fix the widget"),
            _entry("T-1", "Fix the widget", status="completed"),
            _entry("T-2", "Write the docs"),
        ])
        rc, _ = self._main(["--bridge", str(ledger), "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertFalse((self.project / "tasks" / "fix-the-widget.md").exists())
        self.assertTrue((self.project / "tasks" / "write-the-docs.md").is_file())

    def test_slug_dedup(self):
        # An existing task-note slug is never duplicated or clobbered: the
        # note on disk is canonical, the ledger only fills gaps.
        tasks = self.project / "tasks"
        tasks.mkdir()
        existing = tasks / "fix-the-widget.md"
        existing.write_text("---\ntype: task\nstatus: doing\n---\n\n## Task\n\nhand-written\n")
        before = existing.read_text()
        ledger = self._write_ledger([
            _entry("T-1", "Fix the widget", description="from the ledger"),
        ])
        rc, _ = self._main(["--bridge", str(ledger), "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertEqual(existing.read_text(), before)
        self.assertEqual(sorted(p.name for p in tasks.glob("*.md")),
                         ["fix-the-widget.md"])

    def test_bridge_triggers_board_creation(self):
        # First survivor note is the board's birth signal: after the bridge,
        # ensure_board has scaffolded the deck with the new card in it.
        ledger = self._write_ledger([_entry("T-1", "Fix the widget")])
        rc, out = self._main(["--bridge", str(ledger), "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().splitlines()[-1], "created")
        deck = self._deck()
        self.assertIn("fix-the-widget", [c["id"] for c in deck["cards"]])

    def test_malformed_ledger_line_skipped(self):
        # One garbage line must not take down the bridge or the lines
        # around it.
        ledger = self._write_ledger(
            [_entry("T-1", "Fix the widget")],
            raw_lines=("not json {", '"a bare string"', "[1, 2]"),
        )
        rc, _ = self._main(["--bridge", str(ledger), "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertTrue((self.project / "tasks" / "fix-the-widget.md").is_file())

    def test_unsluggable_subject_skipped(self):
        # A subject with no ascii alphanumerics kebabs to nothing: no
        # phantom `.md` note, no crash.
        ledger = self._write_ledger([_entry("T-1", "???")])
        rc, _ = self._main(["--bridge", str(ledger), "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertFalse((self.project / "tasks").exists())


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

    def test_missing_ledger_is_benign(self):
        # sessionend picks the flag, but a race (ledger cleaned between the
        # check and the call) must degrade to ensure-only, never crash.
        rc, out = self._main(["--bridge", str(self.tmp / "gone.jsonl"),
                              "--project-dir", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip().splitlines()[-1], "no-tasks")
        self.assertFalse((self.project / "board").exists())


class TestKebab(unittest.TestCase):

    def test_kebab_forms(self):
        self.assertEqual(board_bridge.kebab("Fix the widget"), "fix-the-widget")
        self.assertEqual(board_bridge.kebab("  Ship v2.0 (final!)  "), "ship-v2-0-final")
        self.assertEqual(board_bridge.kebab("???"), "")

    def test_kebab_bounded(self):
        self.assertLessEqual(len(board_bridge.kebab("word " * 60)), 80)


class TestLedgerFalsyId(unittest.TestCase):
    """Finding 31: `entry.get("id") or ""` dropped a falsy-but-real id of 0,
    so that task silently never bridged."""

    def test_numeric_zero_id_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text(
                json.dumps({"id": 0, "status": "created", "subject": "zeroth"})
                + "\n"
                + json.dumps({"id": "1", "status": "created", "subject": "first"})
                + "\n")
            entries = board_bridge.read_ledger(ledger)
            self.assertIn("0", entries)
            self.assertEqual(entries["0"]["subject"], "zeroth")
            self.assertIn("1", entries)

    def test_missing_id_still_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text(json.dumps({"status": "created"}) + "\n")
            self.assertEqual(board_bridge.read_ledger(ledger), {})


if __name__ == "__main__":
    unittest.main()
