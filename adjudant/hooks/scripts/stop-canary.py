#!/usr/bin/env python3
"""Drift canary - has the model stopped following its standing instructions?

SessionStart states one rule: end every message with a codeword. This hook
reads `last_assistant_message` on Stop and records whether it did.

The value is that the rule is trivial. A model that stops honouring a one-word
instruction it was given minutes ago has stopped honouring instructions
generally, and everything else it says this session is worth less. That is the
moment to start fresh, and nothing else tells you it has arrived.

Block once, then report. The first miss blocks and asks the model to re-read
its instructions; every later miss is only recorded, because coercing
compliance past that point manufactures the appearance of health. The miss is
counted either way: if a block makes the retry succeed and the miss were
forgotten, the counter would read clean through the degradation it exists to
catch.
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
    else:
        state["misses"] = int(state.get("misses", 0)) + 1

    should_block = (not present) and not state.get("blocked")
    if should_block:
        state["blocked"] = True

    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError:
        pass                           # a full TMPDIR must not surface as a failure

    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": (f"The session canary {word} was missing from that reply. "
                       "Re-read your standing instructions and end every message "
                       f"with {word} on its own line. This is said once: a later "
                       "lapse is recorded, not corrected."),
        }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                  # pragma: no cover - last-resort guard
        sys.exit(0)
