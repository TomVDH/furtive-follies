#!/usr/bin/env python3
"""Adjudant index generation — the two surfaces that survive.

Folder indexes are gone, all 139 of them. For an agent they are worth nothing:
listing a directory gives the true current contents in one call, while a
markdown copy is stale the moment anything changes. 24 were already staler
than their own folder and 15 had a body under 25 bytes.

What is left is two generated files, and neither is a listing:

  Home.md            every project grouped by lifecycle folder, last active
  {slug}/_index.md   a project contents page: where to start, the specs, then
                     counts and the newest entry per folder

Both are rewritten whole from the filesystem, so neither can drift. Both link
through _place.link, so neither carries a lifecycle folder. Both go through
_render.frontmatter for the fenced block, same as every other mechanical
write in adjudant: a hand-typed `---\\ntype: ...` block is exactly the second
declaration this module exists to not have.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from _place import link
from _render import frontmatter
from _vault_walk import (
    PROJECT_ZONES,
    enumerate_projects_all_zones,
    newest_dated_stem,
    parse_frontmatter,
)

# Folders the contents table lists, in reading order. A folder with nothing in
# it does not appear: an empty row is the same lie as an empty index file.
_CONTENTS_ORDER: tuple[str, ...] = (
    "sessions", "decisions", "tasks", "notes", "docs", "specs",
    "components", "api", "schemas", "sources", "releases", "dreams",
)

_ZONE_HEADING: dict[str, str] = {
    "active": "Active", "paused": "Paused",
    "finished": "Finished", "archive": "Archive",
}


def _fields(path: Path) -> dict:
    try:
        fm, _ = parse_frontmatter(path.read_text(errors="replace"))
    except OSError:
        return {}
    return fm.fields


def _is_generated(path: Path) -> bool:
    """True when another script owns this file.

    A page carrying `source:` is overwritten by its generator every run.
    Adjudant does not clean, index or nag about one — the rule that stops it
    writing an index into a directory whose own docstring says it is
    regenerated.
    """
    return bool(_fields(path).get("source"))


def _listable(folder: Path) -> list[Path]:
    """Content files in one folder: .md, not an index, not generated.

    One level deep only. 225 component pages need components/modules/ and
    components/templates/; nothing needs to go deeper, so nothing is looked
    for deeper.
    """
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for f in sorted(folder.rglob("*.md")):
        rel = f.relative_to(folder)
        if len(rel.parts) > 2 or f.name.startswith("_"):
            continue
        if _is_generated(f):
            continue
        out.append(f)
    return out


def _rel(path: Path, project_dir: Path) -> str:
    parts = list(path.relative_to(project_dir).parts)
    parts[-1] = parts[-1][:-3] if parts[-1].endswith(".md") else parts[-1]
    return "/".join([project_dir.name] + parts)


def _title(path: Path) -> str:
    """The file's H1, else its stem with hyphens read as spaces."""
    try:
        _fm, body = parse_frontmatter(path.read_text(errors="replace"))
    except OSError:
        body = ""
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return path.stem.replace("-", " ")


def _created(existing_path: Path, today_s: str) -> str:
    """The surface's own `created:`, preserved across every regenerate.

    Home.md and {slug}/_index.md are rewritten whole on every run, but
    `created:` still means when the surface was first generated, not
    "whenever regenerate last ran" — the same reason
    `_handoff_freshness.preserved_frontmatter` bumps only `updated:` on an
    existing handoff rather than restamping the whole block. A path with no
    frontmatter yet, or none at all, was just born: today.
    """
    if existing_path.is_file():
        fm, _ = parse_frontmatter(existing_path.read_text(errors="replace"))
        created = fm.fields.get("created")
        if isinstance(created, str) and created.strip():
            return created.strip()
    return today_s


# ============================================================
# Home.md
# ============================================================


def render_home(vault: Path, today: date) -> str:
    """Every project, grouped by lifecycle folder, with its last active date.

    `type: index` is what makes Home resolvable: `_vault_walk.VAULT_HOME_TYPES`
    accepts either "vault-home" or "index" for Home.md, and "index" is the one
    of the two that is still a real kind — home.md and index-project.md share
    it, so Home does not need a sixteenth kind of its own just to resolve.
    """
    today_s = today.strftime("%Y-%m-%d")
    home_path = vault / "Home.md"
    rows: dict[str, list[str]] = {z: [] for z in PROJECT_ZONES}
    for slug, pdir, zone in enumerate_projects_all_zones(vault):
        last = newest_dated_stem(pdir / "sessions", not_after=today_s)
        when = last or "never"
        rows.setdefault(zone, []).append(
            f"- {link(f'{slug}/brief', slug)} · last active {when}")

    parts = [
        frontmatter("index", {
            "created": _created(home_path, today_s),
            "updated": today_s,
        }).rstrip("\n"),
        "",
        "# Vault",
        "",
        "Every project, grouped by lifecycle folder. This file is generated:",
        "edits are overwritten.",
        "",
    ]
    any_rows = False
    for zone in PROJECT_ZONES:
        if not rows.get(zone):
            continue
        any_rows = True
        parts.append(f"## {_ZONE_HEADING[zone]}")
        parts.append("")
        parts.extend(sorted(rows[zone]))
        parts.append("")
    if not any_rows:
        parts.append("No projects yet. Run `/adjudant connect` to link one.")
        parts.append("")
    return "\n".join(parts)


def write_home(vault: Path, today: date) -> Path:
    """Rewrite `{vault}/Home.md` whole. Returns its path."""
    path = vault / "Home.md"
    path.write_text(render_home(vault, today))
    return path


# ============================================================
# {slug}/_index.md
# ============================================================


def render_project_index(project_dir: Path, today: date) -> str:
    """A project's contents page: a synthesis, not a listing.

    Start here, then specs as onboarding context, then per-folder counts with
    the newest entry. Two of the four hand-written examples in the real vault
    are genuinely good documents, which says the surface is worth having and
    too much work to keep by hand.
    """
    slug = project_dir.name
    today_s = today.strftime("%Y-%m-%d")
    index_path = project_dir / "_index.md"
    parts = [
        frontmatter("index", {
            "created": _created(index_path, today_s),
            "updated": today_s,
        }).rstrip("\n"),
        "",
        f"# {slug}",
        "",
        "Generated contents page. Edits are overwritten.",
        "",
        "## Start here",
        "",
    ]

    start_rows: list[str] = []
    if (project_dir / "brief.md").is_file():
        start_rows.append(
            f"- {link(f'{slug}/brief', 'brief')} · what this project is")
    if (project_dir / "_handoff.md").is_file():
        start_rows.append(
            f"- {link(f'{slug}/_handoff', 'handoff')} · where it was left")
    newest_session = newest_dated_stem(project_dir / "sessions", not_after=today_s)
    if newest_session:
        start_rows.append(
            f"- {link(f'{slug}/sessions/{newest_session}', newest_session)} "
            "· newest session")
    parts.extend(start_rows or ["- Nothing recorded yet."])
    parts.append("")

    specs = _listable(project_dir / "specs")
    if specs:
        parts.append("## Specs")
        parts.append("")
        for f in specs:
            status = _fields(f).get("status") or "unstated"
            parts.append(
                f"- {link(_rel(f, project_dir), _title(f))} · {status}")
        parts.append("")

    body_rows: list[str] = []
    for folder in _CONTENTS_ORDER:
        files = _listable(project_dir / folder)
        if not files:
            continue
        newest = max(files, key=lambda p: p.name)
        body_rows.append(
            f"| {folder} | {len(files)} | "
            f"{link(_rel(newest, project_dir), _title(newest), in_table=True)} |")
    if body_rows:
        parts.append("## Contents")
        parts.append("")
        parts.append("| Folder | Files | Newest |")
        parts.append("|---|---|---|")
        parts.extend(body_rows)
        parts.append("")
    return "\n".join(parts)


def write_project_index(project_dir: Path, today: date) -> Path:
    """Rewrite `{project}/_index.md` whole. Returns its path."""
    path = project_dir / "_index.md"
    path.write_text(render_project_index(project_dir, today))
    return path


# ============================================================
# Retiring the other 139
# ============================================================


def prune_index_files(vault: Path) -> list[Path]:
    """Delete every `_index.md` that is not a project contents page.

    139 folder indexes existed. For an agent they are worth nothing: a
    directory listing is the true current contents, a markdown copy is stale
    the moment anything changes, 24 were already staler than their own folder
    and 15 had a body under 25 bytes. `projects/_index.md` goes with them:
    Home groups by lifecycle folder now, and a second list of the same
    projects adds nothing but a second thing to disagree.

    Returns what it deleted, so a caller can report it.
    """
    keep = {pdir / "_index.md"
            for _slug, pdir, _zone in enumerate_projects_all_zones(vault)}
    deleted: list[Path] = []
    base = vault / "projects"
    if not base.is_dir():
        return deleted
    for f in sorted(base.rglob("_index.md")):
        if f in keep:
            continue
        try:
            f.unlink()
        except OSError:
            continue
        deleted.append(f)
    return deleted


def regenerate(vault: Path, today: date) -> dict:
    """Rewrite both surfaces and retire every other index. Returns a receipt."""
    deleted = prune_index_files(vault)
    projects = [str(write_project_index(pdir, today))
                for _slug, pdir, _zone in enumerate_projects_all_zones(vault)]
    return {
        "home": str(write_home(vault, today)),
        "projects": projects,
        "deleted": [str(p) for p in deleted],
    }
