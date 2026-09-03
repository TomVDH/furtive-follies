"""Acceptance test for adjudant v3 plan 1: a working session leaves no crud.

Before this plan, one ordinary session produced eight unrequested vault files,
eleven whole-file rewrites and fourteen log lines against six intentional
writes — better than three to one, machine to human. This test fails if any of
that comes back.

The session runs twice, once writing into `notes/` and once into `tasks/`, and
both must leave the same single unrequested file. Plan 1's version only ever
wrote to `notes/`, and that one fixture choice hid a live leak: the PostToolUse
hook fired `board_bridge.py --ensure-only` on any write under `tasks/`, so the
same six writes scaffolded a whole board nobody asked for. The folder is a
parameter now precisely so no future branch can hide behind it.
"""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks" / "scripts"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class TestSessionLeavesNoCrud(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.home = tmp / "home"
        self.project = tmp / "code"
        self.vault = self.home / "vault"
        self.vp = self.vault / "projects" / "demo"
        (self.vp / "notes").mkdir(parents=True)
        (self.vp / "tasks").mkdir(parents=True)
        (self.project / ".claude").mkdir(parents=True)
        (self.project / ".claude" / "adjudant").write_text(
            f"vault_path: {self.vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        self._ob = os.environ.pop("OB_VAULT", None)

    def tearDown(self):
        if self._ob is not None:
            os.environ["OB_VAULT"] = self._ob
        self._tmp.cleanup()

    def _env(self):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(self.home / "tmp")
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        env.pop("OB_VAULT", None)
        (self.home / "tmp").mkdir(exist_ok=True)
        return env

    def _hook(self, script: str, payload: dict):
        runner = ["bash"] if script.endswith(".sh") else ["python3"]
        subprocess.run(runner + [str(HOOKS / script)], env=self._env(),
                       input=json.dumps(payload), capture_output=True,
                       text=True, timeout=20)

    _BODY = {
        "notes": "---\ntype: note\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n\n# N%d\n",
        "tasks": ("---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
                  "status: backlog\n---\n\n# T%d\n"),
    }

    def _one_session(self, folder: str):
        """Six intentional writes into `folder`, with the full hook lifecycle
        around them. Returns (written, session_note)."""
        sid = f"s-accept-{folder}"
        # Session opens, twice (a resume), then compacts once.
        self._hook("session-start.sh", {"session_id": sid, "source": "startup"})
        self._hook("session-start.sh", {"session_id": sid, "source": "resume"})

        # Six intentional writes.
        written = []
        for i in range(6):
            note = self.vp / folder / f"n{i}.md"
            note.write_text(self._BODY[folder] % i)
            written.append(note)
            self._hook("posttooluse-vault-log.py", {
                "tool_name": "Write",
                "tool_input": {"file_path": str(note)},
                "session_id": sid,
            })

        self._hook("precompact.py", {"session_id": sid})
        self._hook("postcompact.py", {"session_id": sid, "summary": "did things"})
        self._hook("sessionend.sh", {"session_id": sid})

        today = date.today().isoformat()
        session_note = self.vp / "sessions" / f"{today}.md"

        # Exactly one file exists that nobody explicitly asked for: the session
        # note, and only because six real writes happened.
        allowed = set(written) | {session_note, self.vp / "_handoff.md"}
        actual = {p for p in self.vp.rglob("*") if p.is_file()}
        extra = actual - allowed
        self.assertEqual(extra, set(), f"unrequested vault files: {sorted(extra)}")

        # The session note holds one line per real write and no lifecycle noise.
        log = session_note.read_text()
        for marker in ("session started", "session resumed", "session ended",
                       "paused (compaction)", "compacted:"):
            self.assertNotIn(marker, log, f"lifecycle marker survived: {marker}")
        self.assertEqual(log.count("· Added:"), 6)

        # No scratch anywhere in the vault.
        self.assertEqual(list(self.vault.rglob(".adjudant-*")), [])

    def test_one_session_writes_only_what_was_asked_for(self):
        self._one_session("notes")

    def test_a_session_of_task_writes_leaves_no_crud_either(self):
        # Same six writes, one folder over. A board is opt-in: writing a task
        # note must not scaffold board-data.json, board.html or a lock file.
        self._one_session("tasks")
        board = self.vp / "board"
        self.assertFalse(board.exists(),
                         f"a task write scaffolded a board: "
                         f"{sorted(p.name for p in board.rglob('*'))}")

    def test_a_session_with_no_writes_leaves_nothing(self):
        sid = "s-accept-2"
        self._hook("session-start.sh", {"session_id": sid, "source": "startup"})
        self._hook("sessionend.sh", {"session_id": sid})
        self.assertFalse((self.vp / "sessions").exists(),
                         "a session that did nothing still created a note")


if __name__ == "__main__":
    unittest.main()
