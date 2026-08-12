#!/usr/bin/env python3
"""Adjudant sync — push current project state to the vault.

Three locked features (per reference/sync.md):
  1. Brief refresh — update brief.md frontmatter `updated:` field
  2. Handoff mirror — copy `.remember/remember.md` (or `.remember/now.md`
     fallback) body into `_handoff.md`, preserving handoff frontmatter
  3. Project-row refresh — update `{vault}/projects/_index.md` row counts
     for this project

CLI:
    python3 sync.py --project-dir PATH

`--project-dir` is the CODE project root (with `.claude/adjudant` breadcrumb).
Auto-resolves the vault project location via the breadcrumb.

Idempotent. Re-running produces identical effects given identical inputs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from _vault_walk import (
    find_project_dir,
    parse_breadcrumb,
    parse_frontmatter,
    resolve_project_from_cwd,
    PROJECT_STATUS_VALUES,
)
from _handoff_freshness import (
    HANDOFF_FRONTMATTER_TEMPLATE,
    compute_freshness,
    freshness_header,
    latest_session_file,
    preserved_frontmatter,
    render_handoff,
)
from connect import (
    count_non_index_files,
    newest_session_date,
    upsert_projects_index_row,
)


# ============================================================
# Step 1: brief refresh
# ============================================================


def refresh_brief_updated(brief_path: Path, today: str) -> str:
    """Update brief.md frontmatter `updated:` field.

    Returns 'bumped' / 'unchanged' / 'missing' / 'skipped-undecodable'.

    Strict decode: this text is written straight back at the end, so reading
    with errors="replace" baked a permanent U+FFFD over every undecodable byte
    (shelf and tidy avoid this same trap). An undecodable brief is reported and
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


# ============================================================
# Step 2: handoff mirror
# ============================================================


def find_remember_source(project_root: Path) -> Optional[Path]:
    """Return the best `.remember/` file to mirror into handoff body.

    Priority:
      1. `.remember/remember.md` (canonical per sync runbook)
      2. `.remember/now.md` (newer convention used on this machine)

    Returns None if neither exists.
    """
    canonical = project_root / ".remember" / "remember.md"
    if canonical.is_file():
        return canonical
    now_file = project_root / ".remember" / "now.md"
    if now_file.is_file():
        return now_file
    return None


def mirror_handoff(
    project_root: Path,
    handoff_path: Path,
    slug: str,
    today: str,
    now: Optional[datetime] = None,
) -> str:
    """Copy remember/now body into handoff body, with a freshness header.

    Output is rendered by `_handoff_freshness.render_handoff` — the SAME
    renderer the PreCompact/SessionEnd hook uses, so a manual `/adjudant sync`
    and an auto-compaction sync produce byte-identical handoffs.

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
    # every sync regenerates: the source of truth is never written, so no bytes
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
    light, age_str, next_line, stale = compute_freshness(project_root, body, source, session_file, now)
    fresh = freshness_header(light, age_str, next_line, stale)
    fresh_block = f"{fresh}\n\n" if fresh else ""

    frontmatter = preserved_frontmatter(handoff_path, today) \
        or HANDOFF_FRONTMATTER_TEMPLATE.format(slug=slug, today=today, source_stem=source.stem)

    handoff_path.write_text(
        render_handoff(slug, today, ts, source.name, fresh_block, body, frontmatter))
    return "mirrored"


# ============================================================
# Step 3: project-row refresh (delegates to connect.upsert_projects_index_row)
# ============================================================


def refresh_projects_index_row(vault_path: Path, slug: str) -> str:
    proj_dir = find_project_dir(vault_path, slug) or (vault_path / "projects" / slug)
    if not proj_dir.is_dir():
        return "project-missing"
    brief_path = proj_dir / "brief.md"
    if not brief_path.is_file():
        return "brief-missing"
    fm, _ = parse_frontmatter(brief_path.read_text(errors="replace"))
    project_type = fm.fields.get("project_type") or "coding"
    status = fm.fields.get("status") or "active"
    decisions_n = count_non_index_files(proj_dir / "decisions")
    sessions_n = count_non_index_files(proj_dir / "sessions")
    last_session = newest_session_date(proj_dir / "sessions")
    return upsert_projects_index_row(
        vault_path, slug, project_type, status, decisions_n, sessions_n, last_session
    )


# ============================================================
# Top-level run
# ============================================================


def run_sync(project_root: Path) -> dict[str, Any]:
    ctx = resolve_project_from_cwd(project_root)
    if ctx is None:
        raise RuntimeError(
            f"no .claude/adjudant breadcrumb at {project_root} (run /adjudant connect first)"
        )
    if not ctx.vault_project_dir.is_dir():
        raise RuntimeError(
            f"vault project dir missing: {ctx.vault_project_dir} (run /adjudant connect)"
        )

    today = datetime.now().strftime("%Y-%m-%d")
    summary: dict[str, Any] = {
        "project_root": str(project_root),
        "vault_path": str(ctx.vault_path),
        "slug": ctx.slug,
        "today": today,
        "steps": {},
    }

    brief_path = ctx.vault_project_dir / "brief.md"
    handoff_path = ctx.vault_project_dir / "_handoff.md"

    summary["steps"]["brief_refresh"] = refresh_brief_updated(brief_path, today)
    if summary["steps"]["brief_refresh"] == "skipped-undecodable":
        # Never silent: the requested `updated:` bump did not happen.
        summary.setdefault("warnings", []).append(
            f"{brief_path.name} is not valid UTF-8; it was left byte-identical "
            f"and its `updated:` field was NOT bumped. Fix the file's encoding.")
    summary["steps"]["handoff_mirror"] = mirror_handoff(
        project_root, handoff_path, ctx.slug, today
    )
    summary["steps"]["projects_index_row"] = refresh_projects_index_row(ctx.vault_path, ctx.slug)

    fm, _ = parse_frontmatter(brief_path.read_text(errors="replace")) if brief_path.is_file() else (None, "")
    if fm is not None:
        status = fm.fields.get("status")
        if isinstance(status, str) and status not in PROJECT_STATUS_VALUES:
            summary.setdefault("warnings", []).append(
                f"brief status {status!r} is off-vocabulary "
                f"({' | '.join(PROJECT_STATUS_VALUES)}); row written as-is, fix via /adjudant shelf")

    return summary


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sync.py",
        description="Adjudant sync — push project state to the vault.",
    )
    parser.add_argument("--project-dir", default=".",
                        help="Code project root with .claude/adjudant breadcrumb (default: cwd)")
    args = parser.parse_args(argv)

    project_root = Path(args.project_dir).expanduser().resolve()
    if not project_root.is_dir():
        print(f"error: project-dir not found: {project_root}", file=sys.stderr)
        return 1

    try:
        summary = run_sync(project_root)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"[sync] {summary['slug']}: "
          f"brief={summary['steps']['brief_refresh']}, "
          f"handoff={summary['steps']['handoff_mirror']}, "
          f"row={summary['steps']['projects_index_row']}",
          file=sys.stderr)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
