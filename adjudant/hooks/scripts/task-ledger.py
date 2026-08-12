#!/usr/bin/env python3
"""TaskCreated/TaskCompleted hook for adjudant: the session task ledger.

One script wired to BOTH events; the event name rides in on the payload's
hook_event_name (probe-verified on the installed Claude Code version, along
with top-level task_id / task_subject / task_description). Each event appends
one JSONL entry to

    $TMPDIR/adjudant-task-ledger-{session_id}.jsonl

Append-only in-session: the file is created on first write and never read
back here. The session-end bridge (scripts/board_bridge.py) replays it: ids
whose latest status is not completed become vault task notes. TaskUpdate
status changes other than completion fire no events, so an id without a
TaskCompleted entry is a survivor by construction.

No vault I/O at all; the ledger lives in TMPDIR and dies with the OS temp
cleanup. Gate on real signal: no session_id means no ledger path, no task_id
means no key to bridge on, either one skips the write. Fail open on itself:
whatever breaks, exit 0.
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime

# Only these events are wired; anything else (a future rewire, a stray
# matcher) is ignored rather than guessed at.
_EVENT_STATUS = {"TaskCreated": "created", "TaskCompleted": "completed"}

# The session_id becomes a filename component: only filename-safe ids may
# steer the ledger path (a hostile or malformed id must not escape TMPDIR).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def ledger_path(session_id: str) -> str:
    root = os.environ.get("TMPDIR") or tempfile.gettempdir()
    return os.path.join(root, f"adjudant-task-ledger-{session_id}.jsonl")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    status = _EVENT_STATUS.get(str(payload.get("hook_event_name") or ""))
    if status is None:
        return 0
    session_id = str(payload.get("session_id") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    if not session_id or not task_id:
        return 0
    if not _SESSION_ID_RE.match(session_id):
        return 0

    entry = {
        "id": task_id,
        "subject": str(payload.get("task_subject") or ""),
        "status": status,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "description": str(payload.get("task_description") or ""),
    }
    try:
        with open(ledger_path(session_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # a full or read-only TMPDIR must never surface as a task failure
    return 0


if __name__ == "__main__":
    # A task-event hook must never surface as a harness failure: whatever
    # goes wrong (future logic error, exotic I/O failure), exit 0.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
