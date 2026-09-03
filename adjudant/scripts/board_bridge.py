#!/usr/bin/env python3
"""Adjudant board bridge: vault task notes to board.

The board is a view of `tasks/`. This script ensures the deck and its HTML
exist and match the notes on disk, and nothing else.

Until v3 it also replayed the session task ledger (hooks/scripts/task-ledger.py)
at session end: every id whose latest event was not `TaskCompleted` became
`tasks/{kebab-subject}.md`. Status changes other than completion fire no
events, so abandoned, superseded and merely renamed todos all qualified as
"survivors" and all became permanent vault notes. An id without a
`TaskCompleted` event is an unfinished harness todo, not a work item, and
treating it as one filled `tasks/` with cards nobody wrote. The replay is
gone; the ledger itself stays in $TMPDIR, where the statusline reads it.

CLI:
    python3 board_bridge.py --ensure-only [--project-dir PATH]

`render_task_note` stays: the advisor's `capture-task` verb writes a task note
on an explicit request, which is the supported way one gets created. It goes
through `_render` now. The inline fallback copy of the template is gone, and
with it the comment stripper that existed because the fallback and the real
template disagreed: the fallback declared `code`, `note` and a `task` tag,
none of which is a v3 field, and the real template's guidance comments
survived the minimal YAML parser and poisoned card ids.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from typing import Optional

from _render import render
from _vault_walk import VaultUnresolvableError, smart_project_dir
from board import ensure_board

# Vault task filenames are strict ascii kebab ({kebab-title}.md per
# vault-standards §naming); 80 chars keeps sync-hostile paths off the table.
_KEBAB_MAX = 80


def kebab(subject: str) -> str:
    """`Fix the widget` -> `fix-the-widget`. Empty when nothing survives."""
    s = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")
    return s[:_KEBAB_MAX].rstrip("-")


def render_task_note(title: str, description: str = "") -> str:
    """A task note from templates/task.md: the title in the heading, the
    description under `## Notes`.

    The card's title on the board is the note's first heading, so a capture
    that left `# {What needs doing}` in place produced a card literally called
    that. The optional fields (`session`, `spec`, `category`, `related`) are
    omitted rather than written bare, which is README rule 1 and the reason
    the comment stripper is gone: there is no valueless line left to clean.
    """
    body = {}
    if title.strip():
        body["What needs doing"] = title.strip()
    if description.strip():
        body["Anything the person picking this up needs."] = description.strip()
    today = datetime.now().strftime("%Y-%m-%d")
    return render("task", {"created": today, "updated": today}, body)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="board_bridge.py",
        description="Ensure the board deck and HTML exist and match tasks/.")
    p.add_argument("--ensure-only", action="store_true", required=True,
                   help="run board.ensure_board for the project (the only mode since v3)")
    p.add_argument("--project-dir", default=".",
                   help="project root (breadcrumb-resolved; default cwd)")
    args = p.parse_args(argv)

    try:
        project_dir, _vault_hint = smart_project_dir(args.project_dir)
    except VaultUnresolvableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not project_dir.is_dir():
        print(f"error: project not found: {project_dir} (run /adjudant connect first)", file=sys.stderr)
        return 1

    try:
        verdict = ensure_board(project_dir)
    except Exception as e:  # a broken template/deck must not traceback at hook time
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
