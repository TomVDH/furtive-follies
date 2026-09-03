"""Tests for the tasks/ path of hooks/scripts/posttooluse-vault-log.py.

A task-note change must NOT nudge the board. Until v3 a job 0 in this hook
fired `board_bridge.py --ensure-only` on any Write OR Edit under
{vault}/projects/{slug}/tasks/, and `ensure_board` then scaffolded
board-data.json, board.html and a lock file — three files nobody asked for,
against six intentional writes. `board` is opt-in: a deck is born by running
`/adjudant board`, and by nothing else.

So this file now pins the absence. Three tests here used to assert the branch
fired and were deleted with it: TestTasksBranchFires.{test_write_under_tasks
_triggers_ensure, test_edit_under_tasks_triggers_ensure} and
TestTasksBranchGates.test_session_log_ignores_edit's `run.assert_called_once()`
half, along with the whole TestFailuresSwallowed class, which existed only to
prove the removed subprocess call was swallowed on OSError and on timeout.

What survives is the real contract: a Write under tasks/ still gets its
session-log line, an Edit still gets nothing at all, and the hook spawns no
subprocess from any path. The mock patches subprocess.run at module level, not
through the hook's own namespace, so the assertion holds even if a future
branch re-imports subprocess under another name.
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
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
        # Yesterday, not a fixed 2020 date: the midnight-straddle fallback
        # now has a floor, so a note from years ago is deliberately no
        # longer eligible to absorb today's log lines.
        _y = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.session_note = self.project_root / "sessions" / f"{_y}.md"
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
        with mock.patch("subprocess.run") as run:
            rc = self._run(payload)
        return rc, run


class TestTasksBranchIsGone(_TasksHookCase):

    def test_write_under_tasks_spawns_nothing(self):
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, run = self._run_mocked(self._payload(task_note, tool_name="Write"))
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_edit_under_tasks_spawns_nothing(self):
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, run = self._run_mocked(self._payload(task_note, tool_name="Edit"))
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_write_under_tasks_seeds_no_board(self):
        # The end the argv contract was a proxy for: no board files appear.
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        task_note.write_text(
            "---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
            "status: backlog\n---\n\n# Refactor auth\n")
        self.assertEqual(self._run(self._payload(task_note, tool_name="Write")), 0)
        board = self.project_root / "board"
        self.assertFalse(board.exists(),
                         f"a task write scaffolded a board: "
                         f"{sorted(p.name for p in board.rglob('*'))}")

    def test_the_hook_imports_no_subprocess(self):
        # A branch that shells out cannot come back unnoticed. Checked on the
        # loaded module, not on the prose: the docstring names the deleted
        # call so the reason it went stays readable.
        self.assertFalse(hasattr(vault_log, "subprocess"),
                         "the hook imported subprocess again")
        imports = [ln.strip() for ln in HOOK.read_text().splitlines()
                   if ln.strip().startswith(("import ", "from "))]
        self.assertEqual(
            [ln for ln in imports if "subprocess" in ln], [],
            "the hook imported subprocess again")


class TestTasksStillLogged(_TasksHookCase):

    def test_write_under_tasks_still_session_logged(self):
        # Deleting job 0 must not cost job 1: a task note is still a real
        # write and still earns its session-log line.
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, _ = self._run_mocked(self._payload(task_note, tool_name="Write"))
        self.assertEqual(rc, 0)
        self.assertRegex(self.session_note.read_text(),
                         r"- \d{2}:\d{2} · Added: \[\[demo/tasks/refactor-auth\]\]")

    def test_session_log_ignores_edit(self):
        # The session-log job stays Write-only.
        task_note = self.project_root / "tasks" / "refactor-auth.md"
        rc, run = self._run_mocked(self._payload(task_note, tool_name="Edit"))
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertEqual(self.session_note.read_text(), "## Log\n")

    def test_edit_elsewhere_writes_nothing(self):
        note = self.project_root / "notes" / "scratch.md"
        rc, run = self._run_mocked(self._payload(note, tool_name="Edit"))
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertEqual(self.session_note.read_text(), "## Log\n")

    def test_edit_outside_vault_writes_nothing(self):
        rc, run = self._run_mocked(
            self._payload(self.project / "src" / "main.py", tool_name="Edit"))
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertEqual(self.session_note.read_text(), "## Log\n")


class TestHookWiring(_TasksHookCase):

    def test_matcher_is_write_only(self):
        # The matcher was widened to Write|Edit for job 0 alone. With job 0
        # gone, an Edit anywhere on the machine would wake this hook to do
        # nothing, so it narrows back.
        hooks = json.loads((HOOK.parents[1] / "hooks.json").read_text())
        entries = [e for e in hooks["hooks"]["PostToolUse"]
                   if any("posttooluse-vault-log.py" in h.get("command", "")
                          for h in e.get("hooks", []))]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "Write")


if __name__ == "__main__":
    unittest.main()
