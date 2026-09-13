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
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


import tempfile
from typing import Optional

from _render import render
from _vault_walk import VaultUnresolvableError, smart_project_dir
from board import ensure_board


def _ops_flash(msg: str, project_dir: str) -> None:
    """Flash to the statusline through scripts/_ops_flash.py. Never raises.

    The writer resolves the key. A relative --project-dir once keyed `.`.
    """
    try:
        from _ops_flash import ops_flash
        ops_flash(msg, project_dir)
    except Exception:
        pass

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


def _beans_snapshot(code_root: Path) -> dict[str, str]:
    """Read current bean states: {bean_id: status}."""
    beans_dir = code_root / ".beans"
    if not beans_dir.is_dir():
        return {}
    snap: dict[str, str] = {}
    for f in beans_dir.glob("*.md"):
        bid = f.stem.split("--")[0]
        status = ""
        in_fm = False
        for line in f.read_text(errors="replace").splitlines():
            if line.strip() == "---":
                if in_fm:
                    break
                in_fm = True
                continue
            if in_fm and line.startswith("status:"):
                status = line.split(":", 1)[1].strip()
                break
        if bid and status:
            snap[bid] = status
    return snap


def _snapshot_path(session_id: str) -> Path:
    return Path(tempfile.gettempdir()) / f"adjudant-beans-snap-{session_id}.json"


def bean_diff(code_root: Path, session_id: str = "") -> Optional[str]:
    """Compare current beans to session-start snapshot. Return a summary line
    like 'Beans: +3, ✓2, moved proj-xyz todo->doing' or None if no change.
    On first call per session, saves the snapshot and returns None."""
    if not session_id:
        return None

    current = _beans_snapshot(code_root)
    snap_file = _snapshot_path(session_id)

    if not snap_file.exists():
        try:
            snap_file.write_text(json.dumps(current))
        except OSError:
            pass
        return None

    try:
        prev = json.loads(snap_file.read_text())
    except (OSError, json.JSONDecodeError):
        prev = {}

    created = [b for b in current if b not in prev]
    closed = [b for b in current
              if b in prev and current[b] in ("completed", "scrapped")
              and prev[b] not in ("completed", "scrapped")]
    moved = [(b, prev[b], current[b]) for b in current
             if b in prev and current[b] != prev[b]
             and current[b] not in ("completed", "scrapped")
             and prev[b] not in ("completed", "scrapped")]

    if not created and not closed and not moved:
        return None

    parts = []
    if created:
        parts.append(f"+{len(created)}")
    if closed:
        parts.append(f"✓{len(closed)}")
    for bid, old, new in moved[:3]:
        parts.append(f"moved {bid} {old}->{new}")

    try:
        snap_file.write_text(json.dumps(current))
    except OSError:
        pass

    return "Beans: " + ", ".join(parts)


def _append_to_session_note(breadcrumb_dir: Path, line: str) -> None:
    """Append a line to today's session note in the vault."""
    bc = breadcrumb_dir / ".claude" / "adjudant"
    if not bc.is_file():
        return
    info: dict[str, str] = {}
    for raw in bc.read_text().splitlines():
        raw = raw.strip()
        if ":" in raw:
            k, v = raw.split(":", 1)
            info[k.strip()] = v.strip()
    vault_path = info.get("vault_path", "")
    slug = info.get("slug", "")
    if not vault_path or not slug:
        return
    vp = Path(vault_path).expanduser()
    if not vp.is_dir():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    for zone in ("active", "paused", "finished", "archive", ""):
        sess_dir = vp / "projects" / (zone + "/" if zone else "") / slug / "sessions"
        note = sess_dir / f"{today}.md"
        if note.is_file():
            try:
                with note.open("a") as f:
                    f.write(f"- {datetime.now().strftime('%H:%M')} · {line}\n")
            except OSError:
                pass
            return


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

    # Beans lives beside the code, and --project-dir may name either side of
    # the link, so the code root is found by the breadcrumb rather than assumed.
    import _beans
    code_root = _beans.code_root_from(Path(args.project_dir))

    try:
        verdict = ensure_board(project_dir, code_root=code_root)
    except Exception as e:  # a broken template/deck must not traceback at hook time
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(verdict)
    if verdict in ("reseeded", "created", "tasks-synced", "html-refreshed"):
        _ops_flash(f"board: {verdict}", args.project_dir)

    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id and code_root:
        diff_line = bean_diff(code_root, session_id)
        if diff_line:
            _append_to_session_note(Path(args.project_dir), diff_line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
