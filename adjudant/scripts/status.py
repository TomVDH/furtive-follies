#!/usr/bin/env python3
"""Adjudant status — make derived state current, then report on it.

Five verbs asked one question between them: is what the vault says about this
project still true? `sync` made three derived facts current, `check` reported
compliance, `sitrep` reported momentum, `kebab --scan` reported one naming
rule, and the advisor's pulse reported context integrity. Each needed its own
invocation, and none of them told you which finding mattered most.

`status` runs the make-current phase, then reports in three bands ordered by
the cost of being wrong:

    wrong_now     a claim in the vault is false today
    going_stale   true now, decaying
    worth_a_look  cosmetic, advisory, or a question rather than a defect

The make-current phase is the only part that writes, and it writes exactly
what `sync` wrote: the brief's `updated:` field and the handoff mirror. It no
longer upserts a row into `projects/_index.md` — plan 4 retired that surface;
Home groups every project by lifecycle folder and is generated whole instead.

CLI:
    python3 status.py [--project-dir PATH] [--vault-dir PATH] [--out FILE]
    python3 status.py --estimate-only
    python3 status.py --advisor {on,off,status}
    python3 status.py --capture-task --title TITLE [--note NOTE]
    python3 status.py --slug TEXT...
    python3 status.py --triage
    python3 status.py --move SLUG ZONE
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _profile  # noqa: E402
from _cost import (  # noqa: E402
    breadcrumb_int, cost_block, read_threshold, stat_walk,
)
from _handoff_freshness import (  # noqa: E402
    HANDOFF_FRONTMATTER_TEMPLATE,
    age_hours,
    compute_freshness,
    find_remember_source,
    fmt_age,
    freshness_header,
    latest_session_file,
    latest_today_activity,
    parse_next_line,
    preserved_frontmatter,
    remember_status,
    render_handoff,
    traffic_light,
)
from _lifecycle import apply_move, triage_plan  # noqa: E402
import _index_gen
from _vault_walk import (  # noqa: E402
    DEFAULT_STALE_DAYS,
    VaultUnresolvableError,
    ZONE_FOR_STATUS,
    atomic_write_text,
    file_lock,
    obsidian_cli_path,
    parse_frontmatter,
    resolve_project_from_cwd,
    resolve_vault,
    schema_drift,
    smart_project_dir,
    suggest_status,
    walk_project,
    zone_of,
)
from board_bridge import kebab as _bridge_kebab  # noqa: E402
from truth import truth_report  # noqa: E402

# Task-status alias set for schema_drift's normalizable flag. Defensive:
# status must render even if board.py is mid-edit.
try:
    from board import STATUS_TO_COLUMN
    _TASK_ALIASES: set = set(STATUS_TO_COLUMN)
except Exception:  # pragma: no cover - degraded, schema still reports
    _TASK_ALIASES = set()


# ============================================================
# Naming — vault-standards §4 (was kebab.py)
# ============================================================

# Types whose filename is `{kebab-title}.md` per §4. `doc` is deliberately
# absent: §4 wants docs UPPERCASE, and a kebab rule applied blindly would
# fight the standard it exists to serve.
KEBAB_TITLE_TYPES: frozenset = frozenset({"note", "task", "source"})
# Decisions are `{YYYY-MM-DD}-{kebab-title}.md`: the date is clean --deep's to
# check, the title is ours.
DATED_TITLE_TYPES: frozenset = frozenset({"decision"})

# Written for you, or shaped by another rule entirely.
EXEMPT_NAMES: frozenset = frozenset({
    "brief.md", "_handoff.md", "_index.md", "_iteration.md", "MEMORY.md"})
EXEMPT_FOLDERS: frozenset = frozenset({
    "sessions", "dreams", "releases", "templates", "images", "assets",
    "previews", "iterations", "board", "bases", "canvases"})

DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(.*))?$")
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(text: str) -> str:
    """`Fix the parser` -> `fix-the-parser`. Empty when nothing survives.

    Delegates to board_bridge.kebab so the plugin has ONE slug rule: a
    captured task and a hand-named note must agree about what the same title
    is called.
    """
    return _bridge_kebab(text)


def kebab_violations(project_dir: Path) -> list:
    """§4 title violations, newest rule first. Read-only."""
    out: list = []
    for vf in walk_project(project_dir):
        name = vf.rel_path.name
        if name in EXEMPT_NAMES or name.startswith("_"):
            continue
        if any(p in EXEMPT_FOLDERS for p in vf.rel_path.parts[:-1]):
            continue
        ftype = vf.file_type
        stem = name[:-3] if name.endswith(".md") else name

        if ftype in DATED_TITLE_TYPES:
            m = DATE_PREFIX_RE.match(stem)
            if not m or not m.group(2):
                continue      # the date shape is clean --deep's finding, not ours
            date_part, title = m.group(1), m.group(2)
            if KEBAB_RE.match(title):
                continue
            fixed = slugify(title)
            if not fixed:
                continue
            out.append({"file": str(vf.rel_path), "type": ftype,
                        "suggested": f"{date_part}-{fixed}.md",
                        "issue": "decision title is not kebab-case (§4)"})
            continue

        if ftype not in KEBAB_TITLE_TYPES:
            continue
        if KEBAB_RE.match(stem):
            continue
        fixed = slugify(stem)
        if not fixed:
            continue
        out.append({"file": str(vf.rel_path), "type": ftype,
                    "suggested": f"{fixed}.md",
                    "issue": f"type:{ftype} filename is not kebab-case (§4)"})
    return out


# ============================================================
# Snapshot readers (was check.py)
# ============================================================


def _read_brief(project_dir: Path) -> dict:
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


def _folder_counts(project_dir: Path) -> dict:
    """Count non-index .md files per common folder."""
    counts: dict = {}
    for folder in ["decisions", "sessions", "dreams", "notes", "tasks",
                   "references", "sources", "releases"]:
        d = project_dir / folder
        if not d.is_dir():
            continue
        counts[folder] = sum(
            1 for f in d.iterdir()
            if f.is_file() and f.suffix == ".md" and f.name != "_index.md"
        )
    return counts


def _most_recent_dated(folder: Path, *,
                       pattern: "re.Pattern" = re.compile(r"^(\d{4}-\d{2}-\d{2})")) -> Optional[str]:
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
                  now: Optional[datetime] = None) -> dict:
    """Read _handoff.md and report BOTH clocks — they answer different questions.

    `updated:` / `mirror_hours` is when the handoff was last WRITTEN. Every
    SessionEnd and PreCompact stamps it to today, so a mirror of an empty
    buffer still reads fresh. On its own it made the report hours-fresh
    against a handoff whose own banner said days-stale.

    `light` / `age` / `next` / `stale` come from _handoff_freshness — real
    activity (remember dailies, session-note markers), the same sensor the
    hooks and the make-current phase already render into the handoff banner.
    This is the one to trust; keep them in agreement.
    """
    handoff = project_dir / "_handoff.md"
    if not handoff.is_file():
        return {"present": False}
    text = handoff.read_text(errors="replace")
    fm, _ = parse_frontmatter(text)
    updated = fm.fields.get("updated")
    info: dict = {"present": True, "updated": updated}

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
    except Exception:  # pragma: no cover - degraded, status still renders
        info["light"] = info["age"] = info["next"] = None
        info["stale"] = None

    if updated:
        try:
            # Accept YYYY-MM-DD or full ISO. `updated:` is written with local
            # dates (the make-current phase and the hooks use datetime.now()),
            # so bare dates and naive timestamps are interpreted as LOCAL time
            # — not UTC midnight, which skewed staleness by the UTC offset.
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


def _latest_dream_signal(project_dir: Path) -> dict:
    """Find the most recent dream report and try to parse drift_items from it."""
    dreams = project_dir / "dreams"
    if not dreams.is_dir():
        return {"present": False}
    # Dream reports are written as {YYYY-MM-DD}-dream.md (reference/dream.md);
    # bare {YYYY-MM-DD}.md accepted for hand-authored reports.
    candidates = sorted(
        (f for f in dreams.iterdir()
         if f.is_file() and re.match(r"^\d{4}-\d{2}-\d{2}(-dream)?\.md$", f.name)),
        reverse=True,
    )
    if not candidates:
        return {"present": False}
    latest = candidates[0]
    info: dict = {"present": True, "file": latest.name, "date": latest.name[:10]}
    try:
        text = latest.read_text(errors="replace")
        m = _DRIFT_HEADER_RE.search(text)
        if m:
            info["drift_items"] = int(m.group(1))
    except OSError:
        pass
    return info


def _environment(project_dir: Path) -> dict:
    """Capability probes, keyed by id. Presence only: nothing here is executed.

    A capability this build does not declare produces no key at all, so a
    reduced build renders nothing rather than rendering "absent" - the
    difference that used to be carried by forking this file.
    """
    env: dict = {"obsidian_cli": obsidian_cli_path() is not None}
    present = {c["id"] for c in _profile.present_capabilities()}
    for cap in _profile.capabilities():
        env[cap["id"]] = cap["id"] in present
    return env


def _capability_notes(line_key: str) -> list:
    """The line each half renders, for the capabilities present right now.

    The text lives in scripts/build-profile.json, so the two report halves and
    the SessionStart banner read three fields of one record instead of three
    copies of one sentence.
    """
    return [{"id": c["id"], "line": c[line_key]}
            for c in _profile.present_capabilities()]


def _board_status(project_dir: Path) -> dict:
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
    columns: dict = {}
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


def _legacy_breadcrumb(code_root: Optional[Path]) -> bool:
    """A retired `.claude/obsidian-bridge` file with no `.claude/adjudant`.

    v3 stopped resolving it (see _vault_walk.resolve_vault). Reporting it is
    the replacement: the project was never connected, and the fix is one
    command, so say that rather than quietly serving a stale path.
    """
    if code_root is None:
        return False
    claude = Path(code_root) / ".claude"
    return (claude / "obsidian-bridge").is_file() and not (claude / "adjudant").is_file()


def compliance(project_dir: Path, code_root: Optional[Path] = None,
               today: Optional[date] = None) -> dict:
    """Compliance half of the report: schema, counts, freshness, environment.

    Read-only. Was `check.run_check`; the keys are unchanged so the render
    contract and every downstream reader survive the merge.
    """
    brief = _read_brief(project_dir)
    # The frontmatter mirror plus one fact about the project that is not in
    # its frontmatter: `project` is where a reader looks for both.
    brief["legacy_breadcrumb"] = _legacy_breadcrumb(code_root)
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
    status_block = {**sug, "zone": zone_of(project_dir)}
    files = list(walk_project(project_dir))
    return {
        "project": brief,
        "counts": counts,
        "recent": recent,
        "handoff": handoff,
        "drift_signal": drift_signal,
        "board": _board_status(project_dir),
        "capabilities": _capability_notes("check_line"),
        # `.remember/` sits beside the CODE, never in the vault project — the
        # same root compute_freshness reads its dailies from.
        "remember": remember_status(code_root or project_dir),
        "status": status_block,
        "schema": schema_drift(files, _TASK_ALIASES),
        "environment": _environment(project_dir),
    }


# ============================================================
# Orientation (was sitrep.py)
# ============================================================


def _board_brief(project_dir: Path) -> dict:
    """The board snapshot plus the numbers the briefing line needs.

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


def _repo_brief(code_root: Optional[Path]) -> dict:
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
    recent: list = []
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


def _server_brief(code_root: Optional[Path]) -> dict:
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

    out: list = []
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


def orientation(
    project_dir: Path,
    vault_path: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    code_root: Optional[Path] = None,
) -> dict:
    """The momentum half: where you left off and what to do next.

    Read-only. Was `sitrep.run_sitrep`; keys unchanged. `now` is injectable
    for tests. `project_dir` is the vault project dir; `code_root` is the
    code-side project root where `.remember/` lives (they differ in the
    breadcrumb flow — falls back to `project_dir` when the two are the same).
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
    status_block = {**sug, "zone": zone_of(project_dir)}

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
        "capabilities": _capability_notes("sitrep_line"),
        "next_step": _next_step(project_dir),
        "open_signals": _latest_dream_signal(project_dir),
        "status": status_block,
    }


# ============================================================
# The make-current phase (was sync.py)
# ============================================================


def refresh_brief_updated(brief_path: Path, today: str) -> str:
    """Update brief.md frontmatter `updated:` field.

    Returns 'bumped' / 'unchanged' / 'missing' / 'skipped-undecodable'.

    Strict decode: this text is written straight back at the end, so reading
    with errors="replace" baked a permanent U+FFFD over every undecodable byte
    (clean avoids this same trap). An undecodable brief is reported and
    left byte-identical rather than corrupted.
    """
    if not brief_path.is_file():
        return "missing"
    try:
        text = brief_path.read_text()
    except UnicodeDecodeError:
        return "skipped-undecodable"
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return "no-frontmatter"
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return "unparseable-frontmatter"
    changed = False
    for i in range(1, close_idx):
        m = re.match(r"^(updated\s*:\s*).*$", lines[i])
        if m:
            new_line = f"{m.group(1)}{today}"
            if lines[i] != new_line:
                lines[i] = new_line
                changed = True
            break
    if changed:
        brief_path.write_text("\n".join(lines))
        return "bumped"
    return "unchanged"


def mirror_handoff(
    project_root: Path,
    handoff_path: Path,
    slug: str,
    today: str,
    now: Optional[datetime] = None,
) -> str:
    """Copy remember/now body into handoff body, with a freshness header.

    Output is rendered by `_handoff_freshness.render_handoff` — the SAME
    renderer the PreCompact/SessionEnd hook uses, so a manual `/adjudant
    status` and an auto-compaction sync produce byte-identical handoffs.

    An existing handoff keeps its frontmatter (only `updated:` is bumped);
    the template is used solely for brand-new files. A blank source is never
    mirrored: the remember plugin leaves its buffer empty at rest after
    rotation, and mirroring nothing would wipe the last surviving handoff.

    Returns: 'mirrored' / 'no-source' / 'source-empty'.
    """
    source = find_remember_source(project_root)
    if not source:
        return "no-source"

    # errors="replace" is deliberate here, unlike the brief refresh above. This
    # is a one-way copy out of a repo file into a derived vault artifact that
    # every run regenerates: the source of truth is never written, so no bytes
    # are lost. Decoding strictly would throw the whole handoff away over a
    # single byte, which is the worse failure.
    body = source.read_text(errors="replace")
    if not body.strip():
        return "source-empty"

    now = now or datetime.now()
    ts = now.strftime("%H:%M")

    # Freshness header — same primitives the hook uses. Session note sits
    # beside the handoff, with the shared midnight fallback.
    session_file = latest_session_file(handoff_path.parent / "sessions", today)
    light, age_str, next_line, stale = compute_freshness(
        project_root, body, source, session_file, now)
    fresh = freshness_header(light, age_str, next_line, stale)
    fresh_block = f"{fresh}\n\n" if fresh else ""

    frontmatter = preserved_frontmatter(handoff_path, today) \
        or HANDOFF_FRONTMATTER_TEMPLATE.format(
            slug=slug, today=today, source_stem=source.stem)

    handoff_path.write_text(
        render_handoff(slug, today, ts, source.name, fresh_block, body, frontmatter))
    return "mirrored"


def make_current(
    vault_project_dir: Path,
    vault_path: Optional[Path],
    slug: str,
    *,
    code_root: Optional[Path] = None,
    today: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Bring the two derived facts up to date. The one phase that writes.

    Every caller reaches the same implementation: the breadcrumb-driven
    `run_sync` and the report's `synced` band both land here, so the two can
    never drift into writing different things.

    The handoff mirror needs the CODE root (that is where `.remember/` lives).
    Without one there is nothing to mirror, which is a state, not a failure.

    `vault_path` is unused in this body now: it existed to locate
    `projects/_index.md` for the row this phase used to refresh, and plan 4
    retired that surface. Left in the signature rather than trimmed, since
    both call sites already have one to hand and a future write here (the
    generated surfaces are a plausible next step) would want it back anyway.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    steps: dict = {}
    warnings: list = []

    brief_path = vault_project_dir / "brief.md"
    steps["brief_refresh"] = refresh_brief_updated(brief_path, today)
    if steps["brief_refresh"] == "skipped-undecodable":
        # Never silent: the requested `updated:` bump did not happen.
        warnings.append(
            f"{brief_path.name} is not valid UTF-8; it was left byte-identical "
            f"and its `updated:` field was NOT bumped. Fix the file's encoding.")

    if code_root is not None:
        steps["handoff_mirror"] = mirror_handoff(
            code_root, vault_project_dir / "_handoff.md", slug, today, now)
    else:
        steps["handoff_mirror"] = "no-source"

    fm, _ = parse_frontmatter(brief_path.read_text(errors="replace")) \
        if brief_path.is_file() else (None, "")
    if fm is not None:
        declared = fm.fields.get("status")
        if isinstance(declared, str) and declared not in ZONE_FOR_STATUS:
            warnings.append(
                f"brief status {declared!r} is off-vocabulary "
                f"({' | '.join(ZONE_FOR_STATUS)}); fix the brief")

    # The two generated index surfaces are derived state, so they belong in
    # the one phase that writes. Plan 4 built _index_gen and wired nothing to
    # it: the module was referenced only in comments, so Home.md and the
    # project index were never actually written and the suite stayed green
    # because no test called regenerate. Found by an adversarial prover.
    if vault_path is not None:
        try:
            receipt = _index_gen.regenerate(vault_path, _dt.date.fromisoformat(today))
            steps["indexes"] = {
                "home": receipt["home"],
                "projects": len(receipt["projects"]),
                "retired": len(receipt["deleted"]),
            }
        except Exception as e:            # a broken vault must not fail the report
            warnings.append(f"index regeneration skipped: {e}")

    return {"today": today, "slug": slug, "steps": steps, "warnings": warnings}


def run_sync(project_root: Path) -> dict:
    """The breadcrumb-driven make-current pass, from a CODE project root."""
    ctx = resolve_project_from_cwd(project_root)
    if ctx is None:
        raise RuntimeError(
            f"no .claude/adjudant breadcrumb at {project_root} "
            f"(run /adjudant connect first)"
        )
    if not ctx.vault_project_dir.is_dir():
        raise RuntimeError(
            f"vault project dir missing: {ctx.vault_project_dir} "
            f"(run /adjudant connect)"
        )

    current = make_current(ctx.vault_project_dir, ctx.vault_path, ctx.slug,
                           code_root=project_root)
    summary: dict = {
        "project_root": str(project_root),
        "vault_path": str(ctx.vault_path),
        "slug": ctx.slug,
        "today": current["today"],
        "steps": current["steps"],
    }
    if current["warnings"]:
        summary["warnings"] = current["warnings"]
    return summary


# ============================================================
# The advisor's state and pulse (was advisor.py)
# ============================================================

# One line, greppable, self-documenting. `off` removes it entirely: a
# lingering marker would keep telling every session the advisor is watching
# when it is not.
AGENTS_MARKER_PREFIX = "**Adjudant advisor: on**"
AGENTS_MARKER = (
    f"{AGENTS_MARKER_PREFIX} — this project has opted into proactive "
    "observations (tasks, gaps, gaffes, stale context). Contract: the "
    "adjudant skill's `reference/advisor.md`. Toggle: `/adjudant status "
    "--advisor off`."
)

_KNOB_RE = re.compile(r"^advisor[:=][ \t]*\S+[ \t]*$", re.MULTILINE)


def _breadcrumb(project_dir: Path) -> Path:
    return project_dir / ".claude" / "adjudant"


def read_state(project_dir: Path) -> Optional[str]:
    """'on' | 'off' | None (no breadcrumb)."""
    bc = _breadcrumb(project_dir)
    try:
        text = bc.read_text()
    except OSError:
        return None
    m = re.search(r"^advisor[:=][ \t]*(\S+)", text, re.MULTILINE)
    if not m:
        return "off"
    return "on" if m.group(1).strip().lower() in ("on", "true", "1", "yes") else "off"


def _set_knob(project_dir: Path, state: str) -> None:
    """Set `advisor: <state>` in the breadcrumb, touching nothing else.

    Line-surgical for the same reason clean's frontmatter edits are: the
    breadcrumb is repo-committed and hand-readable, and a wholesale rewrite
    would eat comments and keys this helper does not know about.
    """
    bc = _breadcrumb(project_dir)
    text = bc.read_text()
    line = f"advisor: {state}"
    if _KNOB_RE.search(text):
        new = _KNOB_RE.sub(line, text, count=1)
    else:
        new = text if text.endswith("\n") else text + "\n"
        new += line + "\n"
    with file_lock(bc):
        atomic_write_text(bc, new)


def _stamp_agents(project_dir: Path) -> bool:
    """Append the marker to AGENTS.md (idempotent). False when there is no
    AGENTS.md to stamp - the toggle still succeeds, degraded like every
    other ambient surface."""
    agents = project_dir / "AGENTS.md"
    if not agents.is_file():
        return False
    text = agents.read_text()
    if AGENTS_MARKER_PREFIX in text:
        return True
    new = text if text.endswith("\n") else text + "\n"
    new += "\n" + AGENTS_MARKER + "\n"
    with file_lock(agents):
        atomic_write_text(agents, new)
    return True


def _unstamp_agents(project_dir: Path) -> None:
    """Remove the marker line (and the blank line the stamp added)."""
    agents = project_dir / "AGENTS.md"
    if not agents.is_file():
        return
    text = agents.read_text()
    if AGENTS_MARKER_PREFIX not in text:
        return
    lines = [ln for ln in text.split("\n") if AGENTS_MARKER_PREFIX not in ln]
    new = "\n".join(lines)
    # collapse the trailing blank the stamp introduced
    while new.endswith("\n\n"):
        new = new[:-1]
    if not new.endswith("\n"):
        new += "\n"
    with file_lock(agents):
        atomic_write_text(agents, new)


def run_pulse(project_dir: Path, today: date) -> dict:
    """Does the working context still hold? Read-only, composed from sensors
    that already exist: the handoff NEXT and dream's dangling-scope detector.
    Adds nothing clever - the pulse's one original contribution is the `quiet`
    verdict, because the advisor's contract is silence when nothing is
    flagged, and a pulse that always finds something to say trains the user to
    skip it.

    The declared truth-lifetime sensor is gone with the epistemic fields it
    read: no template declares them, so no file legally carries one.
    """
    # Local import: dream pulls its full detector suite in; the toggle path
    # (on/off/status) must not pay for it.
    from dream import detect_dangling_scopes

    files = list(walk_project(project_dir))
    dangling = detect_dangling_scopes(files, today)

    next_step: Optional[str] = None
    handoff = project_dir / "_handoff.md"
    if handoff.is_file():
        try:
            next_step = parse_next_line(handoff.read_text(errors="replace"))
        except OSError:
            pass

    decisions = sorted(
        (f for f in files if f.rel_path.parts[:1] == ("decisions",)
         and f.rel_path.name != "_index.md"),
        key=lambda f: f.rel_path.name, reverse=True)[:5]
    recent = [{
        "file": str(f.rel_path),
        "status": f.frontmatter.fields.get("status"),
        "excerpt": next((ln.strip() for ln in f.body.split("\n")
                         if ln.strip() and not ln.strip().startswith("#")), "")[:160],
    } for f in decisions]

    return {
        "today": str(today),
        "quiet": not dangling,
        "next_step": next_step,
        "dangling_scopes": dangling,
        "recent_decisions": recent,
    }


def capture_task(project_dir: Path, title: str, note: str = "") -> tuple:
    """Land an approved suggestion as a task note through the existing rail.

    (exit code, message). Writes tasks/{slug}.md through `_render` from
    templates/task.md and lets board.ensure_board seed the card - the same
    path the session-end bridge uses, so a captured task is indistinguishable
    from any other. Dedup by slug is the advisor's raise-once rule enforced at
    the disk layer: a re-capture never clobbers a note someone has since
    edited. The title lands in the heading, which is what the board reads as
    the card's name.
    """
    from board import ensure_board
    from board_bridge import kebab, render_task_note

    slug = kebab(title)
    if not slug:
        return 1, "error: --title kebabs to nothing; give it at least one word"
    tasks = project_dir / "tasks"
    note_path = tasks / f"{slug}.md"
    if note_path.is_file():
        return 0, f"tasks/{slug}.md already exists; not touching it"
    tasks.mkdir(parents=True, exist_ok=True)
    body = render_task_note(title, note)
    with file_lock(note_path):
        atomic_write_text(note_path, body)
    try:
        verdict = ensure_board(project_dir)
    except Exception as e:  # the note landed; the board can catch up next hook
        return 0, f"wrote tasks/{slug}.md (board reseed failed: {e})"
    return 0, f"wrote tasks/{slug}.md; board: {verdict}"


# ============================================================
# The three bands
# ============================================================


def _bands(comp: dict, orient: dict, naming: list,
           pulse: Optional[dict]) -> dict:
    """Sort every signal the five absorbed verbs produced into three bands.

    The bands are ordered by the cost of being wrong, not by severity of
    tone. `wrong_now` is reserved for a claim the vault is making that is
    false today — the only band that earns an interruption. `going_stale` is
    true now and decaying. `worth_a_look` is a question, not a defect.

    Nothing is invented here: every entry traces to a sensor `check`,
    `sitrep`, `kebab --scan` or the advisor pulse already had. Plan 4
    replaces these with the truth checks.
    """
    wrong_now: list = []
    going_stale: list = []
    worth_a_look: list = []

    brief = comp.get("project") or {}
    st = comp.get("status") or {}
    schema = comp.get("schema") or {}
    handoff = comp.get("handoff") or {}
    board = comp.get("board") or {}
    remember = comp.get("remember") or {}

    # --- wrong_now: the vault says something that is not true -------------
    if not brief.get("present"):
        wrong_now.append({
            "signal": "brief-missing",
            "detail": "the project has no brief.md; nothing declares what it is",
        })
    if brief.get("legacy_breadcrumb"):
        wrong_now.append({
            "signal": "legacy-breadcrumb",
            "file": ".claude/obsidian-bridge",
            "detail": ".claude/obsidian-bridge is a retired breadcrumb and is "
                      "no longer resolved; run /adjudant connect",
        })
    for sample in schema.get("samples") or []:
        entry = {"signal": "schema-drift"}
        entry.update(sample)
        wrong_now.append(entry)
    if brief.get("present") and st.get("declared_valid") is False:
        wrong_now.append({
            "signal": "status-off-vocabulary",
            "file": "brief.md",
            "detail": f"status {st.get('declared')!r} is not one of "
                      f"{' | '.join(ZONE_FOR_STATUS)}",
        })
    if st.get("zone_matches") is False:
        wrong_now.append({
            "signal": "zone-mismatch",
            "file": "brief.md",
            "detail": f"status {st.get('declared')!r} does not match the "
                      f"folder zone {st.get('zone')!r}",
        })

    # --- going_stale: true now, decaying ----------------------------------
    if handoff.get("present") and handoff.get("stale"):
        going_stale.append({
            "signal": "handoff-stale",
            "file": "_handoff.md",
            "detail": f"last real activity {handoff.get('age')}",
        })
    if st.get("suggested"):
        going_stale.append({
            "signal": "status-suggestion",
            "file": "brief.md",
            "detail": f"declared {st.get('declared')!r}, "
                      f"suggested {st.get('suggested')!r}: {st.get('reason')}",
        })
    if st.get("nudge"):
        going_stale.append({
            "signal": "status-nudge",
            "file": "brief.md",
            "detail": st["nudge"],
        })
    if remember.get("present") and remember.get("empty"):
        going_stale.append({
            "signal": "remember-empty",
            "detail": "the remember buffer is empty; the next handoff mirrors nothing",
        })
    if board.get("present") and board.get("stale"):
        going_stale.append({
            "signal": "board-stale",
            "detail": "a task note is newer than the deck; the board lags tasks/",
        })

    # --- worth_a_look: a question, not a defect ---------------------------
    for v in naming:
        entry = {"signal": "naming"}
        entry.update(v)
        worth_a_look.append(entry)
    drift = comp.get("drift_signal") or {}
    if drift.get("present") and drift.get("drift_items"):
        worth_a_look.append({
            "signal": "dream-drift",
            "file": drift.get("file"),
            "detail": f"{drift['drift_items']} drift items in the last dream report",
        })
    for scope in (pulse or {}).get("dangling_scopes") or []:
        entry = {"signal": "dangling-scope"}
        if isinstance(scope, dict):
            entry.update(scope)
        else:
            entry["detail"] = str(scope)
        worth_a_look.append(entry)
    if brief.get("present") and not orient.get("next_step"):
        worth_a_look.append({
            "signal": "no-next-step",
            "file": "_handoff.md",
            "detail": "the handoff records no NEXT line; resume has nowhere to start",
        })

    return {"wrong_now": wrong_now, "going_stale": going_stale,
            "worth_a_look": worth_a_look}


# ============================================================
# Top-level run
# ============================================================


def run(
    project_dir: Path,
    vault_dir: Optional[Path] = None,
    *,
    code_root: Optional[Path] = None,
    now: Optional[datetime] = None,
    today: Optional[str] = None,
    sync: bool = True,
) -> dict:
    """Make derived state current, then report on it.

    `project_dir` is the VAULT project dir; `vault_dir` the vault root;
    `code_root` the code-side project root where `.claude/` and `.remember/`
    live. The last is optional: without it the handoff cannot be mirrored and
    the git and dev-server halves of orientation stay absent, which is a
    state rather than a failure.

    `sync=False` gives the strictly read-only pass `check` and `sitrep` both
    guaranteed.
    """
    now = now or datetime.now()
    today_str = today or now.strftime("%Y-%m-%d")
    try:
        today_date = date.fromisoformat(today_str)
    except ValueError:
        today_date = now.date()

    brief = _read_brief(project_dir)
    slug = brief.get("slug") or project_dir.name

    if sync:
        synced = make_current(project_dir, vault_dir, slug,
                              code_root=code_root, today=today_str, now=now)
    else:
        synced = {"today": today_str, "slug": slug,
                  "steps": {}, "warnings": [], "skipped": "read-only run"}

    comp = compliance(project_dir, code_root=code_root, today=today_date)
    orient = orientation(project_dir, vault_dir, now=now, code_root=code_root)
    naming = kebab_violations(project_dir)

    advisor: dict = {"state": read_state(code_root) if code_root else None}
    try:
        advisor["pulse"] = run_pulse(project_dir, today_date)
    except Exception as e:  # pragma: no cover - degraded, status still renders
        advisor["pulse"] = None
        advisor["pulse_error"] = str(e)

    # The vault the project sits in. `vault_dir` is the caller's answer when
    # there is one; without it, derive from the path, which covers both the
    # four-folder shape (`projects/{zone}/{slug}`) and the pre-v3
    # `projects/{slug}`.
    vault_root = vault_dir
    if vault_root is None:
        vault_root = (project_dir.parent.parent.parent
                      if project_dir.parent.parent.name == "projects"
                      else project_dir.parent.parent)

    report = {
        "project_dir": str(project_dir),
        "vault_path": str(vault_dir) if vault_dir else None,
        "today": today_str,
        "slug": slug,
        "synced": synced,
        "orientation": orient,
        "compliance": comp,
        "naming": naming,
        "advisor": advisor,
        "truth": truth_report(project_dir, vault=vault_root,
                              code_root=code_root, today=today_date),
    }
    report.update(_bands(comp, orient, naming, advisor.get("pulse")))
    return report


def cli_main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="status.py",
        description="Adjudant status — make derived state current, then report.",
    )
    parser.add_argument("text", nargs="*", help="--slug: the text to slugify")
    parser.add_argument("--project-dir", default=".",
                        help="Project root (default: cwd)")
    parser.add_argument("--vault-dir",
                        help="Vault root (auto-resolved from the breadcrumb otherwise)")
    parser.add_argument("--triage", action="store_true",
                        help="Print one lifecycle prompt per project in the vault "
                             "(JSON). Read-only: moves nothing.")
    parser.add_argument("--move", nargs=2, metavar=("SLUG", "ZONE"),
                        help="Move one project into a lifecycle folder "
                             "(active|paused|finished|archive). One project per "
                             "call, only after the operator confirms.")
    parser.add_argument("--out", help="Write JSON to FILE instead of stdout")
    parser.add_argument("--estimate-only", action="store_true",
                        help="Print only the cost block (stat-only walk) and exit")
    parser.add_argument("--no-sync", action="store_true",
                        help="Report without making derived state current (read-only)")
    parser.add_argument("--today", help="Override 'today' (YYYY-MM-DD) for age math")
    parser.add_argument("--advisor", choices=("on", "off", "status"),
                        help="Toggle or read the opt-in proactive advisor")
    parser.add_argument("--capture-task", action="store_true",
                        help="Land an approved suggestion as a task note")
    parser.add_argument("--title", help="--capture-task: the task's one-line title")
    parser.add_argument("--note", default="",
                        help="--capture-task: the observation, lands in ## Notes")
    parser.add_argument("--slug", action="store_true",
                        help="Print the kebab-case slug for TEXT and exit")
    args = parser.parse_args(argv)

    if args.triage or args.move:
        try:
            vault = resolve_vault(Path(args.project_dir).expanduser(),
                                  os.environ.get("OB_VAULT"))
        except VaultUnresolvableError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if vault is None:
            print("error: no vault resolved", file=sys.stderr)
            return 1
        if args.move:
            slug, zone = args.move
            try:
                dest = apply_move(vault, slug, zone)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            print(json.dumps({"moved": slug, "to": zone, "path": str(dest)}))
            return 0
        plan = triage_plan(vault, date.today())
        print(json.dumps({"triage": [
            {"slug": e.slug, "zone": e.zone, "suggested": e.suggested,
             "reason": e.reason, "last_session": e.last_session,
             "days_quiet": e.days_quiet, "move": e.suggested != e.zone}
            for e in plan]}, indent=2))
        return 0

    if args.slug:
        if not args.text:
            print("error: --slug needs some text to skewer", file=sys.stderr)
            return 1
        slug = slugify(" ".join(args.text))
        if not slug:
            print("error: nothing survives slugification there; give it at "
                  "least one letter or digit", file=sys.stderr)
            return 1
        print(slug)
        return 0

    if args.advisor:
        project_dir = Path(args.project_dir).expanduser().resolve()
        state = read_state(project_dir)
        if state is None:
            print("error: no .claude/adjudant breadcrumb here - run /adjudant "
                  "connect first; the advisor needs a linked project.",
                  file=sys.stderr)
            return 1
        if args.advisor == "status":
            print(f"advisor: {state}")
            return 0
        if args.advisor == "on":
            _set_knob(project_dir, "on")
            stamped = _stamp_agents(project_dir)
            print("advisor: on — the next session start makes it live.")
            if not stamped:
                print("note: no AGENTS.md to stamp; the breadcrumb knob is set, "
                      "but the project-root marker is missing until connect "
                      "provisions AGENTS.md.", file=sys.stderr)
            return 0
        _set_knob(project_dir, "off")
        _unstamp_agents(project_dir)
        print("advisor: off — banner and marker removed.")
        return 0

    if args.capture_task:
        if not args.title:
            print("error: --capture-task needs --title", file=sys.stderr)
            return 1
        try:
            vault_project, _ = smart_project_dir(args.project_dir)
        except VaultUnresolvableError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        rc, msg = capture_task(vault_project, args.title, args.note)
        print(msg, file=sys.stderr if rc else sys.stdout)
        return rc

    try:
        project_dir, vault_hint = smart_project_dir(args.project_dir)
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

    if args.vault_dir:
        vault_path: Optional[Path] = Path(args.vault_dir).expanduser()
    elif vault_hint is not None:
        vault_path = vault_hint
    else:
        # Direct vault-project-dir mode: walk up for Home.md (the same fallback
        # clean and dream use) so the report can still name the vault.
        vault_path = resolve_vault(project_dir)

    code_root = Path(args.project_dir).expanduser().resolve()
    files, n_bytes = stat_walk(project_dir)
    cost = cost_block(files, n_bytes, read_threshold(code_root))
    if args.estimate_only:
        print(json.dumps({"cost": cost}, indent=2))
        return 0

    if args.today:
        try:
            _dt.date.fromisoformat(args.today)
        except ValueError:
            print(f"error: --today not a valid YYYY-MM-DD: {args.today}",
                  file=sys.stderr)
            return 1

    report = run(project_dir, vault_path, code_root=code_root,
                 today=args.today, sync=not args.no_sync)
    report["cost"] = cost

    steps = report["synced"].get("steps") or {}
    if steps:
        print(f"[status] {report['slug']}: "
              f"brief={steps.get('brief_refresh')}, "
              f"handoff={steps.get('handoff_mirror')}",
              file=sys.stderr)

    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).expanduser().write_text(payload + "\n")
        print(f"[status] wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
