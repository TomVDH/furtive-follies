#!/usr/bin/env python3
"""Adjudant lifecycle — the guided triage across every project in the vault.

Lifecycle moves have no verb since v3. `shelf` existed for a year and was used
once, because nothing ever asked; a verb you have to remember to run is a verb
that does not run. `status` now offers a move when it sees one worth making,
and `connect` asks on first link.

Two functions, and the split between them is the whole design: `triage_plan`
reads and suggests, `apply_move` writes one project. Nothing moves until a
person says so, project by project. A sweep that moved 97 cards and closed
zero is the failure this shape prevents.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from _vault_walk import (
    DEFAULT_STALE_DAYS,
    PROJECT_ZONES,
    ZONE_FOR_STATUS,
    enumerate_projects_all_zones,
    newest_dated_stem,
    parse_frontmatter,
    zone_of,
)

# Folders whose occupants are there on purpose. Silence in paused/, finished/
# or archive/ is the point of those folders, so it is never a finding.
_QUIET_IS_FINE: frozenset[str] = frozenset({"paused", "finished", "archive"})


@dataclass
class TriageEntry:
    """One project, one prompt. `suggested == zone` means no move is offered."""
    slug: str
    path: Path
    zone: str
    suggested: str
    reason: str
    last_session: Optional[str]
    days_quiet: Optional[int]


def _legacy_status(project_dir: Path) -> Optional[str]:
    """A pre-v3 brief's `status:`, or None. v3 briefs carry no status field."""
    brief = project_dir / "brief.md"
    try:
        fm, _ = parse_frontmatter(brief.read_text(errors="replace"))
    except OSError:
        return None
    value = fm.fields.get("status")
    return value if isinstance(value, str) and value.strip() else None


def triage_plan(vault: Path, today: date,
                stale_after_days: int = DEFAULT_STALE_DAYS) -> list[TriageEntry]:
    """One entry per project in the vault. Reads only.

    An entry is produced for every project, including the ones with nothing to
    do, so the caller can walk the whole vault once and the operator sees the
    full list rather than a filtered one they have to trust.
    """
    today_s = today.strftime("%Y-%m-%d")
    out: list[TriageEntry] = []
    for slug, pdir, zone in enumerate_projects_all_zones(vault):
        last = newest_dated_stem(pdir / "sessions", not_after=today_s)
        days_quiet: Optional[int] = None
        if last:
            days_quiet = (today - datetime.strptime(last, "%Y-%m-%d").date()).days

        in_named_folder = pdir.parent.name in PROJECT_ZONES
        if not in_named_folder:
            status = _legacy_status(pdir)
            suggested = ZONE_FOR_STATUS.get(status or "", zone)
            reason = (f"not in a lifecycle folder; sits at "
                      f"{pdir.parent.name or 'projects'}/")
        elif zone in _QUIET_IS_FINE:
            suggested = zone
            reason = f"in {zone}/ on purpose"
        elif days_quiet is None:
            suggested = zone
            reason = "in active/ with no session recorded yet"
        elif days_quiet >= stale_after_days:
            suggested = "paused"
            reason = f"in active/ with no session for {days_quiet} days"
        else:
            suggested = zone
            reason = f"in active/, last session {days_quiet} days ago"

        out.append(TriageEntry(slug=slug, path=pdir, zone=zone,
                               suggested=suggested, reason=reason,
                               last_session=last, days_quiet=days_quiet))

    order = {z: i for i, z in enumerate(PROJECT_ZONES)}
    out.sort(key=lambda e: (order.get(e.zone, len(order)), e.slug))
    return out


def apply_move(vault: Path, slug: str, to_zone: str) -> Path:
    """Move one project into `to_zone`. Returns its new path.

    Refuses an unknown folder, a project it cannot find, and an occupied
    destination. Links into the project keep resolving because they never
    carried the lifecycle folder — that is the whole reason the link form
    changed first.
    """
    if to_zone not in PROJECT_ZONES:
        raise ValueError(
            f"unknown lifecycle folder {to_zone!r}; one of "
            f"{', '.join(PROJECT_ZONES)}")
    src: Optional[Path] = None
    for found_slug, pdir, _zone in enumerate_projects_all_zones(vault):
        if found_slug == slug:
            src = pdir
            break
    if src is None:
        raise ValueError(f"no project {slug!r} in {vault}")
    dest = vault / "projects" / to_zone / slug
    if src.resolve() == dest.resolve():
        return src
    if dest.exists():
        raise ValueError(
            f"{dest} already exists; two projects share the slug {slug!r}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest
