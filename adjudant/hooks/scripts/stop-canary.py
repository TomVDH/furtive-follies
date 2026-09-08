#!/usr/bin/env python3
"""Drift canary - has the model stopped following its standing instructions?

SessionStart states one rule: end every message with a codeword. This hook
reads `last_assistant_message` on Stop and records whether it did.

The value is that the rule is trivial. A model that stops honouring a one-word
instruction it was given minutes ago has stopped honouring instructions
generally, and everything else it says this session is worth less.

It records and says nothing. This hook emits no output on any path, and the
per-turn hook carries no canary code at all. Both halves used to speak to the
model: a block on the first miss, then a line every turn afterwards telling it
to wrap up and start fresh. Both were wrong for the same reason. A reading that
reaches the model is a reading the model acts on, so the session wound itself
down before the user had decided anything - and coercing compliance
manufactures the appearance of health in the one measurement built to detect
its absence. The reading is for a person. A person decides what to do with it.

`streak` is what makes the record readable. One miss among hits is noise: a
turn that ended in a tool call, a reply the harness truncated. A run of misses
is drift. The totals alone cannot tell them apart, so the run is counted too.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

# The session_id becomes a filename component: only filename-safe ids may
# steer the path (mirrors task-ledger.py:35).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# How much of the tail counts as "the end". A word quoted mid-message is not
# compliance: the instruction says to end with it.
_TAIL_CHARS = 240


def canary_path(session_id: str) -> str:
    root = os.environ.get("TMPDIR") or tempfile.gettempdir()
    return os.path.join(root, f"adjudant-canary-{session_id}.json")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    sid = str(payload.get("session_id") or "")
    if not sid or not _SESSION_ID_RE.match(sid):
        return 0

    path = canary_path(sid)
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        return 0                       # no canary for this session: nothing to do
    if not isinstance(state, dict) or not state.get("word"):
        return 0

    word = str(state["word"])
    message = str(payload.get("last_assistant_message") or "")
    present = word in message[-_TAIL_CHARS:]

    state["turns"] = int(state.get("turns", 0)) + 1
    if present:
        state["hits"] = int(state.get("hits", 0)) + 1
        state["streak"] = 0
    else:
        state["misses"] = int(state.get("misses", 0)) + 1
        streak = int(state.get("streak", 0)) + 1
        state["streak"] = streak
        state["max_streak"] = max(int(state.get("max_streak", 0)), streak)

    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError:
        pass                           # a full TMPDIR must not surface as a failure

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                  # pragma: no cover - last-resort guard
        sys.exit(0)
