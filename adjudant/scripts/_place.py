#!/usr/bin/env python3
"""Adjudant placement — where a file goes, and how it is linked.

Two decisions used to be spread across every writer. Placement was a folder
name typed at each call site, which is how `references/` ended up holding six
unrelated kinds. Linking had three shapes: the session-log hook wrote
`[[projects/{slug}/…]]`, connect wrote `[[{slug}/brief\\|{slug}]]`, and the
index generator wrote a bare `[[{stem}|{display}]]`. Two of the three embedded
the lifecycle folder, so a project moving between active/ and paused/ broke
every link into it — which is the only thing the deleted 380-line vault-wide
link rewrite ever did.

Obsidian resolves a wikilink by matching the END of a path, so
`[[acme-web/decisions/2026-08-12-branch-track]]` finds the file under
any lifecycle folder. Omitting the folder is therefore not a compromise: it is
the form that stays true.

Every rule here fails loudly. A silent coercion is how `obsolete` became
invisible work and how 45 type values grew out of five.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# The fifteen kinds and the one folder each lives in. "" means the project
# root. Nothing else may name a project subfolder.
KIND_FOLDER: dict[str, str] = {
    "project": "",          # brief.md
    "handoff": "",          # _handoff.md
    "index": "",            # _index.md
    "session": "sessions",
    "decision": "decisions",
    "task": "tasks",
    "note": "notes",
    "doc": "docs",
    "spec": "specs",
    "component": "components",
    "api": "api",
    "schema": "schemas",
    "source": "sources",
    "release": "releases",
    "dream": "dreams",
}

# Kinds whose filename carries an ISO date prefix. `created:` is derived from
# it at write time, so the two can never disagree.
DATED_KINDS: frozenset[str] = frozenset({"session", "decision", "dream"})

# The fixed filenames of the three root kinds.
_ROOT_FILENAME: dict[str, str] = {
    "project": "brief.md",
    "handoff": "_handoff.md",
    "index": "_index.md",
}

# Kinds that may take ONE level of grouping. 225 component pages need
# components/modules/ and components/templates/. Nothing needs to go deeper.
_GROUPABLE: frozenset[str] = frozenset({"component"})

# The four lifecycle folders, duplicated here rather than imported so this
# module stays importable by a hook running in degraded mode. Task 1 owns the
# authoritative copy in _vault_walk.PROJECT_ZONES; the place-zone-parity
# validator keeps them in step.
_LIFECYCLE_FOLDERS: frozenset[str] = frozenset(
    {"active", "paused", "finished", "archive"})

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_kebab(value: str, what: str) -> str:
    if not isinstance(value, str) or not _KEBAB_RE.match(value):
        raise ValueError(f"{what} must be kebab-case, got {value!r}")
    return value


def place(note_type: str, project_dir: Path,
          hints: Optional[dict] = None) -> Path:
    """Where a file of `note_type` belongs, with its folder chain created.

    `hints`: `slug` (kebab stem), `date` (YYYY-MM-DD, required for a dated
    kind), `group` (one kebab segment, only for a groupable kind).

    Creates the folder, never the file, so a caller that decides not to write
    leaves nothing behind. This is the whole fix for the fifteen index files
    with a body under 25 bytes: a folder now exists because something is in it.
    """
    hints = hints or {}
    if note_type not in KIND_FOLDER:
        raise ValueError(
            f"unknown kind {note_type!r}; the fifteen are "
            f"{', '.join(sorted(KIND_FOLDER))}")

    group = hints.get("group")
    if group is not None:
        if note_type not in _GROUPABLE:
            raise ValueError(f"{note_type} takes no grouping folder")
        _require_kebab(group, "group")

    if note_type in _ROOT_FILENAME:
        return project_dir / _ROOT_FILENAME[note_type]

    folder = project_dir / KIND_FOLDER[note_type]
    if group is not None:
        folder = folder / group

    if note_type in DATED_KINDS:
        date = hints.get("date")
        if not isinstance(date, str) or not _ISO_DATE_RE.match(date):
            raise ValueError(
                f"{note_type} needs a YYYY-MM-DD date hint, got {date!r}")
        slug = hints.get("slug")
        stem = f"{date}-{_require_kebab(slug, 'slug')}" if slug else date
    else:
        stem = _require_kebab(hints.get("slug"), "slug")

    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{stem}.md"


def project_rel(path: Path, project_dir: Path) -> str:
    """`{slug}/{path relative to the project}`, extension stripped.

    The lifecycle folder is dropped on purpose: this is the link target form,
    and it must survive the project moving between folders.
    """
    try:
        rel = path.resolve().relative_to(project_dir.resolve())
    except ValueError as e:
        raise ValueError(f"{path} is not inside {project_dir}") from e
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".md"):
        parts[-1] = parts[-1][:-3]
    return "/".join([project_dir.name] + parts)


def link(target_rel: str, alias: Optional[str] = None, *,
         in_table: bool = False) -> str:
    """The only wikilink adjudant writes.

    `target_rel` is `{slug}/{path}`. A vault-root path is accepted too and
    normalised: `projects/active/slug/notes/a` becomes `slug/notes/a`, because
    that names one file and there is one right link to it. A BARE lifecycle
    folder (`active/slug/...`) is still refused, since nothing there says
    whether `active` is a zone or a project of that name.

    An anchor is preserved. `in_table` escapes the alias separator, which a
    markdown table cell needs and nothing else does.
    """
    if not isinstance(target_rel, str) or not target_rel.strip():
        raise ValueError("link target must be a non-empty string")
    target = target_rel.strip().replace("\\", "/").strip("/")
    # An anchor rides along untouched, but the extension is stripped from the
    # PATH, not from the whole string. `a.md#Section` does not end with ".md",
    # so testing the whole string left the extension in the link.
    target, sep_hash, anchor = target.partition("#")
    if target.endswith(".md"):
        target = target[:-3]
    parts = target.split("/")
    # A vault-root path names exactly one file, and exactly one link reaches
    # it: the zone-less one. Refusing the caller made every converter strip the
    # prefix itself, and clean stopped converting half its links overnight
    # because it did not. Normalising is the whole point of having one link().
    if parts and parts[0] == "projects":
        parts = parts[1:]
        if parts and parts[0] in _LIFECYCLE_FOLDERS:
            parts = parts[1:]
        if not parts:
            raise ValueError(
                f"link target {target_rel!r} names no file under projects/")
        target = "/".join(parts)
    target = target + sep_hash + anchor
    head = target.split("/", 1)[0]
    if head in _LIFECYCLE_FOLDERS:
        raise ValueError(
            f"link target {target_rel!r} names the lifecycle folder {head!r}; "
            "a link that carries it breaks the moment the project moves")
    if alias is None:
        return f"[[{target}]]"
    if not isinstance(alias, str) or "|" in alias or "]]" in alias:
        raise ValueError(f"alias {alias!r} would truncate the link")
    sep = "\\|" if in_table else "|"
    return f"[[{target}{sep}{alias}]]"
