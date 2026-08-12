#!/usr/bin/env python3
"""Adjudant board bridge: session task ledger to vault task notes to board.

The task-ledger hook (hooks/scripts/task-ledger.py) appends one JSONL entry
per TaskCreated/TaskCompleted event to a TMPDIR file keyed by session id. At
session end this bridge replays it:

  1. Latest status per id wins (file order). Ids whose latest status is
     `completed` are done work, skipped. Everything else is a survivor:
     status changes other than completion fire no events, so no-completion
     means not-completed by construction.
  2. Each survivor becomes `tasks/{kebab-subject}.md`, rendered from
     templates/task.md (status: todo, the task description in the ## Task
     section), deduped against existing task-note slugs: a note already on
     disk is canonical and is never touched.
  3. `board.ensure_board` runs, so the first bridged note births the board
     and later ones reseed it. Verdict on the last stdout line, same
     contract as `board.py --ensure`.

CLI:
    python3 board_bridge.py --bridge LEDGER [--project-dir PATH]
    python3 board_bridge.py --ensure-only [--project-dir PATH]

`--ensure-only` skips the ledger entirely (sessionend uses it when no ledger
file exists but a board does). A missing or unreadable ledger under
`--bridge` degrades to the same thing: no notes, ensure still runs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from _vault_walk import VaultUnresolvableError, smart_project_dir
from board import ensure_board

TEMPLATE = Path(__file__).resolve().parent.parent / "skills" / "adjudant" / "templates" / "task.md"

# Inlined equivalent of templates/task.md, used only when the template file
# is unreadable (mid-sync clone): the bridge must not drop survivors over a
# missing template.
_FALLBACK_TEMPLATE = """---
type: task
status: todo
category: ""
code: ""
related: []
note: ""
tags:
  - task
---

## Task

## Notes
"""

# Vault task filenames are strict ascii kebab ({kebab-title}.md per
# vault-standards §naming); 80 chars keeps sync-hostile paths off the table.
_KEBAB_MAX = 80


def kebab(subject: str) -> str:
    """`Fix the widget` -> `fix-the-widget`. Empty when nothing survives."""
    s = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")
    return s[:_KEBAB_MAX].rstrip("-")


def read_ledger(path: Path) -> dict[str, dict[str, Any]]:
    """Latest entry per task id, in first-seen order. Malformed lines and
    entries without an id are skipped; a missing file reads as empty."""
    entries: dict[str, dict[str, Any]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        # `or ""` would drop a falsy-but-real id of 0 (finding 31): only a
        # genuinely absent id skips the entry.
        tid_raw = entry.get("id")
        tid = "" if tid_raw is None else str(tid_raw).strip()
        if not tid:
            continue
        entries[tid] = entry
    return entries


def _strip_frontmatter_comments(text: str) -> str:
    """Drop `# guidance` comments inside the frontmatter block, full-line
    and trailing forms both.

    The template carries them for the human/model author; a mechanical
    writer must emit clean values (the minimal YAML parser keeps trailing
    comments on quoted-value lines like `code: ""  # ...`, which would then
    leak into card ids)."""
    lines = text.split("\n")
    closes = [i for i, ln in enumerate(lines[1:], 1) if ln.rstrip() == "---"]
    if not text.startswith("---") or not closes:
        return text
    out: list[str] = []
    for i, ln in enumerate(lines):
        if 0 < i < closes[0]:
            if ln.lstrip().startswith("#"):
                continue
            ln = re.sub(r"[ \t]+#.*$", "", ln)
        out.append(ln)
    return "\n".join(out)


def render_task_note(slug: str, description: str) -> str:
    """templates/task.md with {slug} filled and the description inserted
    into the ## Task section (left untouched when the description is empty,
    matching the template's own empty shape)."""
    try:
        text = TEMPLATE.read_text()
    except OSError:
        text = _FALLBACK_TEMPLATE
    text = _strip_frontmatter_comments(text).replace("{slug}", slug)
    desc = description.strip()
    if desc:
        marker = "## Task\n"
        idx = text.find(marker)
        if idx != -1:
            at = idx + len(marker)
            text = text[:at] + "\n" + desc + "\n" + text[at:]
    return text


def bridge_ledger(project_dir: Path, ledger_path: Path) -> list[Path]:
    """Write one task note per survivor; returns the notes actually written.

    Dedupe is by slug: an existing `tasks/{slug}.md` wins, always. One
    failed write skips that survivor only, never the batch.
    """
    written: list[Path] = []
    tasks_dir = project_dir / "tasks"
    for entry in read_ledger(ledger_path).values():
        if str(entry.get("status") or "").strip().lower() == "completed":
            continue
        slug_name = kebab(str(entry.get("subject") or ""))
        if not slug_name:
            continue
        note = tasks_dir / f"{slug_name}.md"
        if note.exists():
            continue
        try:
            tasks_dir.mkdir(parents=True, exist_ok=True)
            note.write_text(render_task_note(
                project_dir.name, str(entry.get("description") or "")))
        except OSError:
            continue
        written.append(note)
    return written


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="board_bridge.py",
        description="Bridge the session task ledger into vault task notes, then ensure the board.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bridge", metavar="LEDGER",
                      help="task ledger JSONL to replay into tasks/ before ensuring the board")
    mode.add_argument("--ensure-only", action="store_true",
                      help="skip the ledger, just run board.ensure_board")
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

    if args.bridge:
        written = bridge_ledger(project_dir, Path(args.bridge).expanduser())
        if written:
            print(f"[bridge] {len(written)} task note(s) from the session ledger", file=sys.stderr)

    try:
        verdict = ensure_board(project_dir)
    except Exception as e:  # a broken template/deck must not traceback at hook time
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
