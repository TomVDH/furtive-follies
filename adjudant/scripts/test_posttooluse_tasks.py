"""Tests for the tasks/ branch of hooks/scripts/posttooluse-vault-log.py.

A task-note change (Write OR Edit under {vault}/projects/{slug}/tasks/)
must nudge the board: invoke `board_bridge.py --ensure-only` in a capped
subprocess (3s), fire-and-forget, failures swallowed. The pre-existing
session-log job keeps its explicit Write-only guard: Edit payloads never
append a log entry, and a Write under tasks/ still logs exactly as before.

board_bridge.py is built in a parallel lane, so the subprocess call is
mocked here; the assertions pin the argv contract instead (python3, path
ending board_bridge.py, --ensure-only, --project-dir value).
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
HOOK = SCRIPTS.parent / "hooks" / "scripts" / "posttooluse-vault-log.py"

# Hyphenated filename: load via importlib, same interpreter (main invoked
# in-process with stdin patched, mirroring test_commit_log's approach).
_spec = importlib.util.spec_from_file_location("posttooluse_vault_log", HOOK)
vault_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vault_log)


class _TasksHookCase(unittest.TestCase):
    """Temp project + vault + breadcrumb + session note, OB_VAULT hygiene.

    vault_name in the breadcrumb is deliberately implausible so the
    resolve_vault name-candidate scan can never land on a real vault on the
    developer's machine when a test deletes the temp vault.
    """

    def setUp(self):
        self._ob_vault = os.environ.pop("OB_VAULT", None)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.project = tmp / "code"
        self.vault = tmp / "vault"
        self.project_root = self.vault / "projects" / "demo"
        (self.project_root / "sessions").mkdir(parents=True)
        (self.project_root / "tasks").mkdir()
        self.session_note = self.project_root / "sessions" / "2020-01-02.md"
        self.session_note.write_text("## Log\n")
        (self.project / ".claude").mkdir(parents=True)
        (self.project / ".claude" / "adjudant").write_text(
            f"vault_path: {self.vault}\n"
            "vault_name: tasks-hook-test-vault-7c3e\n"
            "slug: demo\nmode: project\n")

    def tearDown(self):
        self._tmp.cleanup()
        if self._ob_vault is not None:
            os.environ["OB_VAULT"] = self._ob_vault

    def _run(self, payload) -> int:
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.project)
        stdin_before = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            return vault_log.main()
        finally:
            sys.stdin = stdin_before
            del os.environ["CLAUDE_PROJECT_DIR"]

    @staticmethod
    def _payload(file_path: Path, *, tool_name: str) -> dict:
        tool_input = {"file_path": str(file_path)}
        if tool_name == "Edit":
            tool_input.update({"old_string": "a", "new_string": "b"})
        return {
            "session_id": "abc123",
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }

    def _run_mocked(self, payload):
        """Drive main() with subprocess.run mocked out; return (rc, mock)."""
        with mock.patch.object(vault_log.subprocess, "run") as run:
            rc = self._run(payload)
        return rc, run

    def _assert_ensure_argv(self, run) -> None:
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "python3")
        self.assertTrue(str(argv[1]).endswith("board_bridge.py"),
                        f"argv[1] must be the bridge path, got {argv[1]!r}")
        self.assertIn("--ensure-only", argv)
        i = argv.index("--project-dir")
        self.assertEqual(argv[i + 1], str(self.project))
        self.assertEqual(run.call_args.kwargs.get("timeout"), 3)


class TestTasksBranchFires(_TasksHookCase):

    def test_write_under_tasks_triggers_ensure(self):
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, run = self._run_mocked(self._payload(task_note, tool_name="Write"))
        self.assertEqual(rc, 0)
        self._assert_ensure_argv(run)

    def test_edit_under_tasks_triggers_ensure(self):
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, run = self._run_mocked(self._payload(task_note, tool_name="Edit"))
        self.assertEqual(rc, 0)
        self._assert_ensure_argv(run)

    def test_write_under_tasks_still_session_logged(self):
        # The pre-existing session-log job must keep firing on Write: the
        # tasks branch is additive, not a replacement.
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, run = self._run_mocked(self._payload(task_note, tool_name="Write"))
        self.assertEqual(rc, 0)
        self.assertRegex(self.session_note.read_text(),
                         r"- \d{2}:\d{2} · Added: \[\[projects/demo/tasks/refactor-auth\.md\]\]")


class TestTasksBranchGates(_TasksHookCase):

    def test_edit_elsewhere_no_ensure(self):
        note = self.project_root / "notes" / "scratch.md"
        rc, run = self._run_mocked(self._payload(note, tool_name="Edit"))
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_edit_outside_vault_no_ensure(self):
        rc, run = self._run_mocked(
            self._payload(self.project / "src" / "main.py", tool_name="Edit"))
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_session_log_ignores_edit(self):
        # Edit under tasks/ fires the ensure branch and nothing else: the
        # session-log job stays Write-only.
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, run = self._run_mocked(self._payload(task_note, tool_name="Edit"))
        self.assertEqual(rc, 0)
        run.assert_called_once()
        self.assertEqual(self.session_note.read_text(), "## Log\n")


class TestFailuresSwallowed(_TasksHookCase):

    def test_subprocess_failure_never_blocks(self):
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        with mock.patch.object(vault_log.subprocess, "run",
                               side_effect=OSError("bridge missing")):
            rc = self._run(self._payload(task_note, tool_name="Write"))
        self.assertEqual(rc, 0)
        # Job 1 must still run after a swallowed job-0 failure.
        self.assertIn("· Added:", self.session_note.read_text())

    def test_timeout_never_blocks(self):
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        with mock.patch.object(
                vault_log.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="board_bridge", timeout=3)):
            rc = self._run(self._payload(task_note, tool_name="Edit"))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
