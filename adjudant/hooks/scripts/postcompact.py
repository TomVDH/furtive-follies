#!/usr/bin/env python3
"""PostCompact hook for adjudant. Writes nothing.

Until v3 this appended the compaction summary to today's vault session log:

    - HH:MM · compacted: <summary, single line, first 160 chars>

The summary is the harness talking to itself, and 160 chars of it is the
opening of a sentence. 34 files in the real vault carry a fragment of raw
model reasoning because of that, several as exact consecutive duplicates. A
compaction is a harness event, not project work, so nothing about it belongs
in a note about the work.

The hook stays registered for one reason: it must drain stdin. An unread
PostCompact payload EPIPEs the harness writer the moment this process exits.
"""

import sys


def main() -> int:
    # The tty guard keeps a bare interactive run from hanging; the broad
    # except keeps a patched or closed stdin from ever crashing the hook.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    # A PostCompact hook must never surface as a harness failure: whatever
    # goes wrong (future logic error, exotic I/O failure), exit 0.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
