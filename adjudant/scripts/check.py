#!/usr/bin/env python3
"""Adjudant check — quick project status snapshot.

Read-only. Reads brief.md frontmatter + folder counts + recent activity
+ handoff freshness + latest dream drift signal. Emits JSON for Claude
to render as a 3-section summary.

CLI:
    python3 check.py --project-dir PATH [--vault-dir PATH] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _cost import breadcrumb_int, cost_block, read_threshold, stat_walk
from _handoff_freshness import compute_freshness, latest_session_file
from _vault_walk import (
    DEFAULT_STALE_DAYS, freshness_report, obsidian_cli_path, parse_frontmatter,
    resolve_vault, schema_drift, smart_project_dir, suggest_status,
    walk_project, zone_matches_status, zone_of, VaultUnresolvableError,
)

# Task-status alias set for schema_drift's normalizable flag (import
# precedent: validate.py validator 26). Defensive: check must render even
# if board.py is mid-edit.
try:
    from board import STATUS_TO_COLUMN
    _TASK_ALIASES: set = set(STATUS_TO_COLUMN)
except Exception:  # pragma: no cover - degraded, schema still reports
    _TASK_ALIASES = set()


def _read_brief(project_dir: Path) -> dict[str, Any]:
    """Read brief.md frontmatter + first heading."""
    brief = project_dir / "brief.md"
    if not brief.is_file():
        return {"present": False}
    text = brief.read_text(errors="replace")
    fm, body = parse_frontmatter(text)
    title = None
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return {
        "present": True,
        "title": title,
        "slug": fm.fields.get("slug"),
        "project_type": fm.fields.get("project_type"),
        "status": fm.fields.get("status"),
        "codename": fm.fields.get("codename"),
        "created": fm.fields.get("created"),
        "updated": fm.fields.get("updated"),
    }


def _folder_counts(project_dir: Path) -> dict[str, int]:
    """Count non-index .md files per common folder."""
    counts: dict[str, int] = {}
    for folder in ["decisions", "sessions", "dreams", "notes", "tasks", "references", "sources", "releases"]:
        d = project_dir / folder
        if not d.is_dir():
            continue
        counts[folder] = sum(
            1 for f in d.iterdir()
            if f.is_file() and f.suffix == ".md" and f.name != "_index.md"
        )
    return counts


def _most_recent_dated(folder: Path, *, pattern: re.Pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})")) -> Optional[str]:
    """Return the most recent YYYY-MM-DD prefix among .md files in folder."""
    if not folder.is_dir():
        return None
    dates = []
    for f in folder.iterdir():
        if not f.is_file() or f.suffix != ".md":
            continue
        m = pattern.match(f.stem)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def _handoff_info(project_dir: Path, code_root: Optional[Path] = None,
                  now: Optional[datetime] = None) -> dict[str, Any]:
    """Read _handoff.md and report BOTH clocks — they answer different questions.

    `updated:` / `mirror_hours` is when the handoff was last WRITTEN. Every
    SessionEnd and PreCompact stamps it to today, so a mirror of an empty
    buffer still reads fresh. On its own it made check report hours-fresh
    against a handoff whose own banner said days-stale.

    `light` / `age` / `next` / `stale` come from _handoff_freshness — real
    activity (remember dailies, session-note markers), the same sensor the
    hooks and /adjudant sync already render into the handoff banner. This is
    the one to trust; keep them in agreement.
    """
    handoff = project_dir / "_handoff.md"
    if not handoff.is_file():
        return {"present": False}
    text = handoff.read_text(errors="replace")
    fm, _ = parse_frontmatter(text)
    updated = fm.fields.get("updated")
    info: dict[str, Any] = {"present": True, "updated": updated}

    # Activity-derived freshness — the honest sensor.
    now = now or datetime.now()
    try:
        session_file = latest_session_file(
            project_dir / "sessions", now.strftime("%Y-%m-%d"))
        light, age_str, next_line, stale = compute_freshness(
            code_root or project_dir, text, handoff, session_file, now)
        info["light"] = light
        info["age"] = age_str
        info["next"] = next_line
        info["stale"] = stale
    except Exception:  # pragma: no cover - degraded, check still renders
        info["light"] = info["age"] = info["next"] = None
        info["stale"] = None

    if updated:
        try:
            # Accept YYYY-MM-DD or full ISO. `updated:` is written with local
            # dates (sync/hooks use datetime.now()), so bare dates and naive
            # timestamps are interpreted as LOCAL time — not UTC midnight,
            # which skewed staleness by the UTC offset (even negative).
            if re.match(r"^\d{4}-\d{2}-\d{2}$", str(updated)):
                dt = datetime.fromisoformat(str(updated) + "T00:00:00")
            else:
                dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)  # aware → local naive
            stale_hours = (datetime.now() - dt).total_seconds() / 3600.0
            info["stale_hours"] = round(stale_hours, 1)
        except (ValueError, TypeError):
            info["stale_hours"] = None
    return info


_DRIFT_HEADER_RE = re.compile(r"(\d+)\s+(?:distinct\s+)?drift\s+items?", re.IGNORECASE)


def _latest_dream_signal(project_dir: Path) -> dict[str, Any]:
    """Find the most recent dream report and try to parse drift_items from it."""
    dreams = project_dir / "dreams"
    if not dreams.is_dir():
        return {"present": False}
    # Dream reports are written as {YYYY-MM-DD}-dream.md (reference/dream.md);
    # bare {YYYY-MM-DD}.md accepted for hand-authored reports.
    candidates = sorted(
        (f for f in dreams.iterdir() if f.is_file() and re.match(r"^\d{4}-\d{2}-\d{2}(-dream)?\.md$", f.name)),
        reverse=True,
    )
    if not candidates:
        return {"present": False}
    latest = candidates[0]
    info: dict[str, Any] = {"present": True, "file": latest.name, "date": latest.name[:10]}
    try:
        text = latest.read_text(errors="replace")
        m = _DRIFT_HEADER_RE.search(text)
        if m:
            info["drift_items"] = int(m.group(1))
    except OSError:
        pass
    return info


def _board_status(project_dir: Path) -> dict[str, Any]:
    """Read-only board snapshot from `board/board-data.json`.

    A pure JSON parse: no board.py import, nothing written. Cards are counted
    per deck column id, exactly as the deck names its lanes (custom lanes
    included, empty lanes shown as 0); never against a hardcoded status list.
    `stale` is an mtime comparison: any `tasks/*.md` newer than the deck file
    means the board lags the task notes. Missing or unreadable deck: just
    `{"present": False}`, never a crash.
    """
    data_path = project_dir / "board" / "board-data.json"
    if not data_path.is_file():
        return {"present": False}
    try:
        deck = json.loads(data_path.read_text(errors="replace"))
        if not isinstance(deck, dict):
            raise ValueError("deck root must be a JSON object")
    except (OSError, json.JSONDecodeError, ValueError):
        return {"present": False}
    columns: dict[str, int] = {}
    for col in deck.get("columns") or []:
        if isinstance(col, dict) and col.get("id") is not None:
            columns[str(col["id"])] = 0
    for card in deck.get("cards") or []:
        if not isinstance(card, dict):
            continue
        col_id = card.get("column")
        if not col_id:
            continue
        col_id = str(col_id)
        columns[col_id] = columns.get(col_id, 0) + 1
    stale = False
    try:
        deck_mtime = data_path.stat().st_mtime
        tasks = project_dir / "tasks"
        if tasks.is_dir():
            for f in tasks.iterdir():
                if f.is_file() and f.suffix == ".md" and f.stat().st_mtime > deck_mtime:
                    stale = True
                    break
    except OSError:
        stale = False
    return {
        "present": True,
        "columns": columns,
        "updated": deck.get("updated"),
        "stale": stale,
    }


def run_check(project_dir: Path, code_root: Optional[Path] = None,
              today: Optional[date] = None) -> dict[str, Any]:
    brief = _read_brief(project_dir)
    counts = _folder_counts(project_dir)
    recent = {
        "last_session": _most_recent_dated(project_dir / "sessions"),
        "last_decision": _most_recent_dated(project_dir / "decisions"),
        "last_dream": _most_recent_dated(project_dir / "dreams"),
    }
    handoff = _handoff_info(project_dir, code_root)
    drift_signal = _latest_dream_signal(project_dir)
    stale_days = breadcrumb_int(code_root, "stale_after_days", DEFAULT_STALE_DAYS)
    sug = suggest_status(
        brief.get("status") if brief.get("present") else None,
        project_dir, today or date.today(), stale_days)
    zone = zone_of(project_dir)
    status = {**sug, "zone": zone,
              "zone_matches": zone_matches_status(brief.get("status"), zone)}
    # One walk feeds both audit sections: schema (shape) and freshness
    # (what valid declarations mean today).
    files = list(walk_project(project_dir))
    return {
        "project": brief,
        "counts": counts,
        "recent": recent,
        "handoff": handoff,
        "drift_signal": drift_signal,
        "board": _board_status(project_dir),
        "status": status,
        "schema": schema_drift(files, _TASK_ALIASES),
        "freshness": freshness_report(files, today or date.today()),
        "environment": {"obsidian_cli": obsidian_cli_path() is not None},
    }


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check.py",
        description="Adjudant check — quick status snapshot (read-only).",
    )
    parser.add_argument("--project-dir", default=".", help="Project root (default: cwd)")
    parser.add_argument("--vault-dir", help="Vault root (currently informational only)")
    parser.add_argument("--out", help="Write JSON to FILE instead of stdout")
    parser.add_argument("--estimate-only", action="store_true",
                        help="Print only the cost block (stat-only walk) and exit")
    args = parser.parse_args(argv)

    try:
        project_dir, _vault_hint = smart_project_dir(args.project_dir)
    except VaultUnresolvableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not project_dir.is_dir():
        # Breadcrumb resolved to a vault project that doesn't exist yet
        if (Path(args.project_dir).expanduser() / ".claude" / "adjudant").is_file():
            print(
                f"error: breadcrumb at {args.project_dir}/.claude/adjudant points to "
                f"vault project {project_dir} which doesn't exist. Run /adjudant connect "
                f"to create it.",
                file=sys.stderr,
            )
        else:
            print(f"error: project-dir not found: {project_dir}", file=sys.stderr)
        return 1

    code_root = Path(args.project_dir).expanduser().resolve()
    files, n_bytes = stat_walk(project_dir)
    cost = cost_block(files, n_bytes, read_threshold(code_root))
    if args.estimate_only:
        print(json.dumps({"cost": cost}, indent=2))
        return 0

    report = run_check(project_dir, code_root=code_root)
    report["cost"] = cost

    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).expanduser().write_text(payload + "\n")
        print(f"[check] wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
