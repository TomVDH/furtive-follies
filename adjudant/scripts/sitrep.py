#!/usr/bin/env python3
"""Adjudant sitrep — ELI5 orientation briefing.

Read-only. Answers the "I've been away, catch me up" question: what were we
doing, what's done, where the vault is, and where best to start. Emits JSON for
Claude to render as four plain-language lines (see reference/sitrep.md).

Distinct from check.py: `check` reports schema/compliance state; `sitrep` reports
momentum and the single next action. It reuses check's snapshot primitives and the
shared handoff-freshness layer rather than re-deriving anything.

CLI:
    python3 sitrep.py --project-dir PATH [--vault-dir PATH] [--out FILE]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Optional

from _cost import breadcrumb_int, cost_block, read_threshold, stat_walk
from _handoff_freshness import (
    age_hours,
    fmt_age,
    latest_today_activity,
    parse_next_line,
    traffic_light,
)
from _vault_walk import (
    DEFAULT_STALE_DAYS, resolve_vault, smart_project_dir, suggest_status,
    zone_matches_status, zone_of, VaultUnresolvableError,
)
from check import (
    _board_status,
    _folder_counts,
    _latest_dream_signal,
    _most_recent_dated,
    _read_brief,
)


def _board_brief(project_dir: Path) -> dict[str, Any]:
    """check's board snapshot plus the numbers the briefing line needs.

    `open` is every card outside `done` and `icebox` (custom lanes count as
    open work); `doing` is the doing column. `line` is the preformatted
    briefing line, present only when the board is: rendered right before the
    final line, so the single next action stays last.
    """
    board = dict(_board_status(project_dir))
    if not board.get("present"):
        return board
    cols = board.get("columns") or {}
    board["open"] = sum(n for cid, n in cols.items() if cid not in ("done", "icebox"))
    board["doing"] = cols.get("doing", 0)
    board["line"] = (f"Board: {board['open']} open ({board['doing']} in motion)"
                     + (", stale" if board.get("stale") else ""))
    return board


def _repo_brief(code_root: Optional[Path]) -> dict[str, Any]:
    """Code-side git state — the half of orientation the vault cannot know.

    "Where were we" is only half answered by the vault: the other half is what
    the working tree is actually doing right now. Returns `{present: False}` for
    a non-repo, a missing git, or any git failure — orientation must never be
    the thing that breaks.
    """
    if not code_root or not (code_root / ".git").exists():
        return {"present": False}
    if not shutil.which("git"):
        return {"present": False, "reason": "git not on PATH"}

    def g(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(
                ["git", "-C", str(code_root), *args],
                capture_output=True, text=True, timeout=5,
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    branch = g("rev-parse", "--abbrev-ref", "HEAD")
    porcelain = g("status", "--porcelain")
    recent_raw = g("log", "-5", "--format=%h\t%ad\t%s", "--date=short")
    recent: list[dict[str, str]] = []
    for line in (recent_raw or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            recent.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return {
        "present": True,
        "branch": branch,
        "detached": branch == "HEAD",
        "head": recent[0] if recent else None,
        "dirty": len([l for l in (porcelain or "").splitlines() if l.strip()]),
        "recent": recent,
    }


def _server_brief(code_root: Optional[Path]) -> dict[str, Any]:
    """Is the project's dev server up?

    Ports come from `.claude/launch.json` (the Claude Code convention) rather
    than a guess, so this stays generic across projects. Probing is a HEAD
    request to localhost with a hard 0.6s timeout — a dev server that is down is
    the normal case, so a refused connection is an answer, never an error.
    Absent launch.json => `{present: False}` and nothing is probed.
    """
    if not code_root:
        return {"present": False}
    cfg = code_root / ".claude" / "launch.json"
    if not cfg.is_file():
        return {"present": False}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {"present": False, "reason": "launch.json unreadable"}

    out: list[dict[str, Any]] = []
    for c in (data.get("configurations") or [])[:8]:  # sanity cap
        port = c.get("port")
        if not isinstance(port, int):
            continue
        url = c.get("url") or f"http://localhost:{port}"
        req = urllib.request.Request(url, method="HEAD")
        alive = False
        try:
            with urllib.request.urlopen(req, timeout=0.6) as r:
                alive = 200 <= getattr(r, "status", 0) < 500
        except Exception:  # noqa: BLE001 - down is an answer, not a failure
            alive = False
        out.append({"name": c.get("name"), "port": port, "url": url, "up": alive})
    return {"present": bool(out), "servers": out}


def _next_step(project_dir: Path) -> Optional[str]:
    """The single NEXT action from _handoff.md, if any (read-only)."""
    handoff = project_dir / "_handoff.md"
    if not handoff.is_file():
        return None
    try:
        return parse_next_line(handoff.read_text(errors="replace"))
    except OSError:
        return None


def run_sitrep(
    project_dir: Path,
    vault_path: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    code_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Compose a read-only orientation snapshot. `now` is injectable for tests.

    `project_dir` is the vault project dir; `code_root` is the code-side project
    root where `.remember/` lives (they differ in the breadcrumb flow — falls
    back to `project_dir` when the two are the same directory).
    """
    now = now or _dt.datetime.now()

    brief = _read_brief(project_dir)
    counts = _folder_counts(project_dir)

    activity = latest_today_activity((code_root or project_dir) / ".remember")
    hours = age_hours(activity, now)
    freshness = {
        "light": traffic_light(hours),
        "age": fmt_age(hours),
        "last_activity": activity.isoformat(timespec="minutes") if activity else None,
    }

    whats_done = {
        "last_session": _most_recent_dated(project_dir / "sessions"),
        "last_decision": _most_recent_dated(project_dir / "decisions"),
        "counts": counts,
        "total_files": sum(counts.values()),
    }

    stale_days = breadcrumb_int(code_root, "stale_after_days", DEFAULT_STALE_DAYS)
    sug = suggest_status(
        brief.get("status") if brief.get("present") else None,
        project_dir, now.date(), stale_days)
    zone = zone_of(project_dir)
    status = {**sug, "zone": zone,
              "zone_matches": zone_matches_status(brief.get("status"), zone)}

    return {
        "project": brief,
        "vault_path": str(vault_path) if vault_path else None,
        "purpose": brief.get("title") if brief.get("present") else None,
        "freshness": freshness,
        "were_doing": freshness["last_activity"],
        "whats_done": whats_done,
        "board": _board_brief(project_dir),
        "repo": _repo_brief(code_root),
        "server": _server_brief(code_root),
        "next_step": _next_step(project_dir),
        "open_signals": _latest_dream_signal(project_dir),
        "status": status,
    }


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sitrep.py",
        description="Adjudant sitrep — ELI5 orientation briefing (read-only).",
    )
    parser.add_argument("--project-dir", default=".", help="Project root (default: cwd)")
    parser.add_argument("--vault-dir", help="Vault root (informational; auto-resolved otherwise)")
    parser.add_argument("--out", help="Write JSON to FILE instead of stdout")
    parser.add_argument("--estimate-only", action="store_true",
                        help="Print only the cost block (stat-only walk) and exit")
    args = parser.parse_args(argv)

    try:
        project_dir, vault_hint = smart_project_dir(args.project_dir)
    except VaultUnresolvableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not project_dir.is_dir():
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

    if args.vault_dir:
        vault_path = Path(args.vault_dir).expanduser()
    elif vault_hint is not None:
        vault_path = vault_hint
    else:
        # Direct vault-project-dir mode: walk up for Home.md (same fallback
        # check/dream/tidy use) so the briefing can still name the vault.
        vault_path = resolve_vault(project_dir)
    code_root = Path(args.project_dir).expanduser().resolve()
    files, n_bytes = stat_walk(project_dir)
    cost = cost_block(files, n_bytes, read_threshold(code_root))
    if args.estimate_only:
        print(json.dumps({"cost": cost}, indent=2))
        return 0

    report = run_sitrep(project_dir, vault_path, code_root=code_root)
    report["cost"] = cost

    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).expanduser().write_text(payload + "\n")
        print(f"[sitrep] wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
