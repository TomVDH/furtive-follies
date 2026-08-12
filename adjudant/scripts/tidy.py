#!/usr/bin/env python3
"""Adjudant tidy — mechanical vault sweep.

Five features (locked spec — replaces the old ramasse mechanical surface):
  1. Rebuild `_index.md` in every project subfolder with ≥2 same-type siblings
  2. Bump `updated:` frontmatter on touched files (doc, brief, note types)
  3. Normalise tags per locked 2026-05-25 schema (drop Bucket D, migrate Bucket B)
  4. Rewrite `[text](path.md)` → `[[path-stem|text]]` when path resolves in vault
  5. Frontmatter schema repair per FIELD_SCHEMA: strip unknown fields, migrate
     legacy keys (node_type → type, originSessionId → source_session), and
     normalise decision-status aliases (accepted/locked/current → active).
     Task-status aliases are accepted input and never rewritten.

Idempotent: a second run with no fresh drift = no changes.

Phases (mirrors port.py):
  detect   — print one of: 'fresh' | 'preview' | 'applied'
  preview  — write .adjudant-tidy-preview/ with proposed changes (read-only sweep)
  apply    — backup live files to .adjudant-tidy-backup/{ts}/, then apply preview

CLI:
    python3 tidy.py detect  --project-dir PATH
    python3 tidy.py preview --project-dir PATH [--vault-dir PATH]
    python3 tidy.py apply   --project-dir PATH

See docs/superpowers/2026-05-26-adjudant-tidy-ramasse-log.design.md.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _cost import cost_block, read_threshold, stat_walk
from _vault_walk import (
    FIELD_SCHEMA,
    BUCKET_A_TYPES,
    BUCKET_B_MIGRATIONS,
    DECISION_STATUS_ALIASES,
    INDEX_EXEMPT_FOLDERS,
    MD_LINK_RE,
    VaultFile,
    build_vault_index,
    is_bucket_b_migration,
    is_bucket_d_tag,
    parse_frontmatter,
    resolve_vault,
    resolve_wikilink,
    schema_drift_for_file,
    smart_project_dir, VaultUnresolvableError,
    walk_project,
)

# Task-status alias set for feature 5's drift check (same defensive import
# as check.py; aliases are accepted input, never rewritten by tidy).
try:
    from board import STATUS_TO_COLUMN
    _TASK_STATUS_ALIASES: set = set(STATUS_TO_COLUMN)
except Exception:  # pragma: no cover - degraded, schema phase still strips
    _TASK_STATUS_ALIASES = set()


def _migrate_ob_to_bucket_a(tag: str) -> Optional[str]:
    """If tag is `ob/<bucket-A-type>`, return the bare type. Else None.

    Preserves the file-type tag mandate (§2A) when dropping `ob/*` prefix.
    """
    if not tag.startswith("ob/"):
        return None
    bare = tag[3:]
    if bare in BUCKET_A_TYPES:
        return bare
    return None


PREVIEW_DIR_NAME = ".adjudant-tidy-preview"
BACKUP_DIR_NAME = ".adjudant-tidy-backup"

DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(.*))?$")

# Types eligible for `updated:` bump (per spec)
UPDATED_BUMP_TYPES = {"doc", "project", "note"}


# ============================================================
# Detection
# ============================================================


def detect_phase(project_dir: Path) -> str:
    """Return 'preview' if preview dir exists, 'applied' if backup but no preview,
    else 'fresh'."""
    preview = project_dir / PREVIEW_DIR_NAME
    backup = project_dir / BACKUP_DIR_NAME
    if preview.is_dir():
        return "preview"
    if backup.is_dir() and any(backup.iterdir()):
        return "applied"
    return "fresh"


# ============================================================
# Tag normalisation
# ============================================================


def normalize_tags(tags: list[str], project_slug: Optional[str]) -> tuple[list[str], list[str]]:
    """Return (new_tags, dropped_tags). Preserves order, removes duplicates."""
    seen: set[str] = set()
    new: list[str] = []
    dropped: list[str] = []
    for t in tags:
        if not isinstance(t, str) or not t.strip():
            continue
        tag = t.strip()
        # Bucket B migration first (opt-in, empty by default)
        migration = is_bucket_b_migration(tag)
        if migration:
            if migration not in seen:
                new.append(migration)
                seen.add(migration)
            dropped.append(f"{tag} → {migration}")
            continue
        # ob/{bucket-A-type} → {bucket-A-type} (preserves §2A file-type tag)
        ob_migration = _migrate_ob_to_bucket_a(tag)
        if ob_migration:
            if ob_migration not in seen:
                new.append(ob_migration)
                seen.add(ob_migration)
            dropped.append(f"{tag} → {ob_migration}")
            continue
        # Bucket D drop
        if is_bucket_d_tag(tag, project_slug=project_slug):
            dropped.append(tag)
            continue
        # Keep
        if tag not in seen:
            new.append(tag)
            seen.add(tag)
    return new, dropped


# ============================================================
# Wikilink form fix
# ============================================================


# Split-with-capture: odd segments are inline-code spans, left untouched
_INLINE_CODE_SPLIT_RE = re.compile(r"(`[^`\n]+`)")


def fix_wikilink_form(body: str, vault_index: set[str]) -> tuple[str, int]:
    """Rewrite `[text](path.md)` → `[[stem|text]]` IFF path resolves in vault.

    Returns (new_body, fix_count). Skips fenced + 4-space-indented code blocks
    and inline-code spans (mirrors what the detectors count). Preserves heading
    anchors (`[t](n.md#Sec)` → `[[n#Sec|t]]`). Leaves `./`/`../` relative links
    untouched — Obsidian resolves the markdown form, not a `[[../…]]` wikilink.
    """
    if not vault_index:
        return body, 0
    fixed_count = 0
    out_lines = []
    in_fenced = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_fenced = not in_fenced
            out_lines.append(line)
            continue
        if in_fenced:
            out_lines.append(line)
            continue
        # Indented code block (same heuristic as extract_markdown_md_links)
        if line.startswith("    ") and line.lstrip()[:1] not in ("-", "*", "+", "|", "["):
            out_lines.append(line)
            continue
        def _sub(m):
            nonlocal fixed_count
            text = m.group(1)
            path = m.group(2)
            if path.startswith(("./", "../")):
                return m.group(0)
            stem, _, anchor = path.partition("#")
            if resolve_wikilink(stem, vault_index):
                # Compute display stem without extension
                no_ext = stem[:-3] if stem.endswith(".md") else stem
                target = f"{no_ext}#{anchor}" if anchor else no_ext
                stem_basename = no_ext.split("/")[-1]
                # If display text matches the basename, skip the alias
                if text.strip() == stem_basename or text.strip() == no_ext:
                    fixed_count += 1
                    return f"[[{target}]]"
                fixed_count += 1
                return f"[[{target}|{text}]]"
            return m.group(0)
        segments = _INLINE_CODE_SPLIT_RE.split(line)
        rebuilt = "".join(
            seg if i % 2 else MD_LINK_RE.sub(_sub, seg)
            for i, seg in enumerate(segments)
        )
        out_lines.append(rebuilt)
    return "\n".join(out_lines), fixed_count


# ============================================================
# Index regeneration
# ============================================================


def _capitalize_folder_name(name: str) -> str:
    name = name.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split())


def _sort_entries(entries: list[Path]) -> list[Path]:
    """Sort: reverse-chronological for date-prefixed, alphabetical otherwise.
    Mixed sets: date entries first (reverse chrono), then plain alphabetical."""
    dated = []
    plain = []
    for f in entries:
        m = DATE_PREFIX_RE.match(f.stem)
        if m and m.group(1):
            dated.append((m.group(1), f))
        else:
            plain.append(f)
    if dated and not plain:
        return [f for _, f in sorted(dated, key=lambda x: x[0], reverse=True)]
    if not dated and plain:
        return sorted(plain, key=lambda x: x.stem)
    return (
        [f for _, f in sorted(dated, key=lambda x: x[0], reverse=True)]
        + sorted(plain, key=lambda x: x.stem)
    )


# A bullet the rebuild could have produced tells us nothing; one with any other
# alias is a line a human wrote, and the filename cannot reconstruct it.
_CURATED_BULLET_RE = re.compile(r"^\s*-\s+\[\[([^\]|#]+?)(?:#[^\]|]*)?\|(.+?)\]\]\s*$")


def harvest_aliases(section_lines: list[str]) -> dict[str, str]:
    """`{link target: alias}` for every aliased bullet in an Entries section.

    First occurrence wins, matching the rest of tidy's duplicate handling.
    """
    found: dict[str, str] = {}
    for ln in section_lines:
        m = _CURATED_BULLET_RE.match(ln)
        if m:
            found.setdefault(m.group(1).strip(), m.group(2).strip())
    return found


def _format_entry_bullet(f: Path, aliases: Optional[dict[str, str]] = None) -> str:
    """One index row. A curated alias for this entry outranks the generated
    one; regenerating over it discards the only authored content an index
    holds, and `stem.replace("-", " ")` cannot get it back."""
    stem = f.stem
    curated = (aliases or {}).get(stem)
    if curated:
        return f"- [[{stem}|{curated}]]"
    m = DATE_PREFIX_RE.match(stem)
    if m and m.group(1) and m.group(2):
        display = f"{m.group(1)} {m.group(2).replace('-', ' ')}"
    else:
        display = stem.replace("-", " ").replace("_", " ")
    return f"- [[{stem}|{display}]]"


def generate_index_content(
    folder_name: str,
    entries: list[Path],
    project_slug: Optional[str],
) -> str:
    """Generate canonical `_index.md` content for a folder with no existing index."""
    today = datetime.now().strftime("%Y-%m-%d")
    pretty = _capitalize_folder_name(folder_name)
    sorted_entries = _sort_entries(entries)
    rows = [_format_entry_bullet(f) for f in sorted_entries]
    return (
        "---\n"
        "type: index\n"
        f"updated: {today}\n"
        "tags:\n"
        "  - index\n"
        "---\n\n"
        f"# {pretty}\n\n"
        "## Entries\n\n"
        + "\n".join(rows)
        + "\n"
    )


_ENTRIES_HEADING_RE = re.compile(r"^##\s+entries\b", re.IGNORECASE)
_NEXT_H2_RE = re.compile(r"^##\s+")
_BULLET_LINK_RE = re.compile(r"^\s*-\s+\[\[")


def _find_entries_section_in_body(body: str) -> Optional[tuple[int, int]]:
    """Locate the `## Entries` section. Returns (content_start, content_end)
    as 0-indexed line bounds (end exclusive). Excludes the heading itself.
    Returns None if no `## Entries` heading exists.
    """
    lines = body.split("\n")
    heading_idx = None
    for i, line in enumerate(lines):
        if _ENTRIES_HEADING_RE.match(line.strip()):
            heading_idx = i
            break
    if heading_idx is None:
        return None
    start = heading_idx + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if _NEXT_H2_RE.match(lines[i]):
            end = i
            break
    return (start, end)


def _section_is_bullet_list(lines: list[str]) -> bool:
    """True if section content is predominantly `- [[wikilink]]` bullets."""
    non_blank = [l for l in lines if l.strip()]
    if not non_blank:
        return True  # empty section — safe to fill
    bullets = [l for l in non_blank if _BULLET_LINK_RE.match(l)]
    return len(bullets) >= max(1, len(non_blank) // 2)


def upsert_index_content(
    existing_text: str,
    folder_name: str,
    entries: list[Path],
    project_slug: Optional[str],
) -> tuple[str, str]:
    """Conservatively update an existing `_index.md`.

    Behaviour:
      - Normalise frontmatter tags (drop Bucket D, migrate Bucket B + ob/*)
      - Bump `updated:` to today (if field present)
      - If body has `## Entries` heading with bullet-list content: replace bullets,
        keep heading + everything else. mode='upserted'.
      - If body has `## Entries` with non-bullet content (table, prose): leave
        body alone (only frontmatter changes). mode='frontmatter_only'.
      - If no `## Entries` heading: leave body alone. mode='frontmatter_only'.

    Returns (new_text, mode).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    fm, body = parse_frontmatter(existing_text)

    # Frontmatter side: normalize tags + bump updated
    new_text = existing_text
    fm_tags = fm.fields.get("tags") if isinstance(fm.fields.get("tags"), list) else []
    new_tags, _ = normalize_tags([str(t) for t in fm_tags] if fm_tags else [], project_slug)
    # Ensure 'index' is present (this IS an index)
    if "index" not in new_tags:
        new_tags = ["index"] + new_tags
    new_text = _rewrite_tags_block(new_text, new_tags)
    new_text = _bump_updated_field(new_text, today)

    # Body side: try entries upsert
    # Re-parse to get the body AFTER frontmatter changes
    fm2, body2 = parse_frontmatter(new_text)
    section = _find_entries_section_in_body(body2)
    if section is None:
        return new_text, "frontmatter_only"

    start, end = section
    body_lines = body2.split("\n")
    section_lines = body_lines[start:end]
    if not _section_is_bullet_list(section_lines):
        return new_text, "frontmatter_only"

    # Generate new entry bullets, carrying forward any alias a human curated
    # for an entry that still exists.
    sorted_entries = _sort_entries(entries)
    aliases = harvest_aliases(section_lines)
    new_bullets = [_format_entry_bullet(f, aliases) for f in sorted_entries]

    # Replace section content: keep leading/trailing blank lines if any in original style
    # Use one blank before bullets, one trailing blank
    new_section = [""] + new_bullets + [""]
    # Trim trailing blank from input section if we'd duplicate
    while new_section and new_section[-1] == "" and end < len(body_lines) and body_lines[end - 1] == "":
        # already blank-padded
        break

    new_body_lines = body_lines[:start] + new_section + body_lines[end:]
    new_body = "\n".join(new_body_lines)

    # Reassemble: keep frontmatter from new_text, replace body
    new_text = _strip_then_prepend_body(new_text, new_body)
    return new_text, "upserted"


# ============================================================
# File content rewriter — surgical edit of tags + body wikilinks + updated
# ============================================================


def _rewrite_tags_block(text: str, new_tags: list[str]) -> str:
    """Surgically replace the `tags:` block in frontmatter with new_tags.

    Handles two existing forms: list (`tags:\\n  - foo`) and missing.
    If the file has no `tags:` field, adds one before the closing `---`.
    If new_tags is empty, removes the block.
    """
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return text

    # Find frontmatter closing index
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return text

    fm_lines = lines[1:close_idx]
    # Find existing tags block
    tags_start = None
    tags_end = None
    for i, ln in enumerate(fm_lines):
        if re.match(r"^tags\s*:", ln):
            tags_start = i
            # find end: subsequent indented list items
            j = i + 1
            while j < len(fm_lines):
                if re.match(r"^\s+-\s+", fm_lines[j]):
                    j += 1
                else:
                    break
            tags_end = j
            break

    new_block: list[str] = []
    if new_tags:
        new_block.append("tags:")
        for t in new_tags:
            new_block.append(f"  - {t}")

    if tags_start is not None:
        # Replace [tags_start:tags_end] with new_block
        fm_lines = fm_lines[:tags_start] + new_block + fm_lines[tags_end:]
    else:
        # Add tags block before close (only if there are tags to add)
        if new_tags:
            fm_lines = fm_lines + new_block

    return "\n".join([lines[0]] + fm_lines + lines[close_idx:])


def _bump_updated_field(text: str, today: str) -> str:
    """If frontmatter has `updated:`, set it to today. Does NOT add the field."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return text
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return text
    for i in range(1, close_idx):
        m = re.match(r"^(updated\s*:\s*).*$", lines[i])
        if m:
            lines[i] = f"{m.group(1)}{today}"
            break
    return "\n".join(lines)


def _frontmatter_close(lines: list[str]) -> Optional[int]:
    """Index of the closing --- line, or None when there is no block."""
    if not lines or lines[0].rstrip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return i
    return None


def _drop_frontmatter_keys(text: str, keys: set[str]) -> str:
    """Drop column-0 frontmatter keys plus their indented continuation lines
    (block lists and nested maps alike). Never touches the body."""
    lines = text.split("\n")
    close = _frontmatter_close(lines)
    if close is None or not keys:
        return text
    out = [lines[0]]
    i = 1
    while i < close:
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:", lines[i])
        if m and m.group(1) in keys:
            i += 1
            while i < close and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out + lines[close:])


def _rename_frontmatter_key(text: str, old: str, new: str) -> str:
    """Rename a column-0 frontmatter key, value untouched."""
    lines = text.split("\n")
    close = _frontmatter_close(lines)
    if close is None:
        return text
    pat = re.compile(rf"^{re.escape(old)}(\s*:)")
    for i in range(1, close):
        if pat.match(lines[i]):
            lines[i] = pat.sub(f"{new}\\1", lines[i], count=1)
            break
    return "\n".join(lines)


def _set_frontmatter_scalar(text: str, key: str, value: str) -> str:
    """Set a scalar frontmatter value, preserving any trailing # comment.
    Narrow use: enum values (status), never quoted strings."""
    lines = text.split("\n")
    close = _frontmatter_close(lines)
    if close is None:
        return text
    for i in range(1, close):
        m = re.match(rf"^({re.escape(key)}\s*:\s*)([^#]*?)(\s*#.*)?$", lines[i])
        if m:
            lines[i] = f"{m.group(1)}{value}{m.group(3) or ''}"
            break
    return "\n".join(lines)


def _uncorroborated_type(file_type: Optional[str], fields: dict[str, Any]) -> Optional[str]:
    """Explain why `type:` is not to be trusted, or None when it is.

    The schema strip is destructive and reads `type:` as ground truth. That
    holds for a file adjudant wrote; it does not hold for a foreign file that
    acquired a colliding `type:` some other way — a Claude Code auto-memory
    note flattened by an external editor arrives as `type: project` carrying
    none of a brief's fields, and every real field it does carry then looks
    "unknown". Corroboration is the required set beyond `type` itself: a
    majority present means the declaration is backed by the file, a minority
    means the file is misclassified and the strip would be the data loss.
    """
    if file_type not in FIELD_SCHEMA:
        return None
    required = set(FIELD_SCHEMA[file_type]["required"]) - {"type"}
    if not required:
        return None
    present = sum(1 for k in required if k in fields)
    if present * 2 > len(required):
        return None
    return (f"type: {file_type} is not corroborated "
            f"({len(required) - present} of {len(required)} required fields missing) "
            f"— left untouched; retype the file or fill it in")


# ============================================================
# Preview build
# ============================================================


def build_preview(
    project_dir: Path,
    vault_index: set[str],
    project_slug: Optional[str],
) -> dict[str, Any]:
    """Walk project, compute all proposed changes, return a change-set dict
    (not yet written to disk). Caller serialises it.
    """
    files = list(walk_project(project_dir))
    today = datetime.now().strftime("%Y-%m-%d")

    # Bucket: per-file proposed full content (only when content changes)
    file_proposals: dict[str, dict[str, Any]] = {}
    # Index proposals (always-regenerated)
    index_proposals: dict[str, dict[str, Any]] = {}

    # --- Feature 1: index rebuilds ---
    from collections import defaultdict
    by_parent: dict[Path, list[VaultFile]] = defaultdict(list)
    for f in files:
        parent = f.rel_path.parent
        if parent == Path("."):
            continue
        by_parent[parent].append(f)

    for parent, members in by_parent.items():
        # Skip exempt folders
        if any(p in INDEX_EXEMPT_FOLDERS for p in parent.parts):
            continue
        non_index = [m for m in members if m.rel_path.name != "_index.md"]
        if len(non_index) < 2:
            continue
        idx_rel = str(parent / "_index.md")
        existing_path = project_dir / parent / "_index.md"

        if existing_path.is_file():
            try:
                existing = existing_path.read_text()  # strict: never write replaced bytes back
            except UnicodeDecodeError:
                continue
            proposed, mode = upsert_index_content(
                existing,
                folder_name=parent.name,
                entries=[m.rel_path for m in non_index],
                project_slug=project_slug,
            )
            if proposed.strip() != existing.strip():
                index_proposals[idx_rel] = {
                    "folder": str(parent),
                    "had_existing": True,
                    "mode": mode,
                    "entry_count": len(non_index),
                    # Hashed like a file proposal: `proposed` was computed FROM
                    # `existing`, so an edit landing between preview and apply
                    # is genuinely lost, not regenerated. The apply-time guard
                    # needs this to notice.
                    "original_hash": _hash_short(existing),
                    "proposed_content": proposed,
                }
        else:
            proposed = generate_index_content(
                folder_name=parent.name,
                entries=[m.rel_path for m in non_index],
                project_slug=project_slug,
            )
            # No `original_hash`: there was nothing to hash. `had_existing`
            # False is itself the guard — apply refuses this proposal if a
            # file has appeared at the path in the meantime.
            index_proposals[idx_rel] = {
                "folder": str(parent),
                "had_existing": False,
                "mode": "generated",
                "entry_count": len(non_index),
                "proposed_content": proposed,
            }

    # --- Features 2-5: per-file edits ---
    schema_actions: dict[str, dict[str, Any]] = {}
    for f in files:
        try:
            # Strict decode: never round-trip errors="replace" text back to
            # disk — that would silently bake U+FFFD into the vault file.
            original = f.path.read_text()
        except UnicodeDecodeError:
            continue
        modified = original

        # Feature 3: tag normalisation
        if f.tags_frontmatter:
            new_tags, dropped = normalize_tags(f.tags_frontmatter, project_slug)
            if dropped:
                modified = _rewrite_tags_block(modified, new_tags)

        # Feature 4: wikilink form fix
        fm, body = parse_frontmatter(modified)
        new_body, wf_count = fix_wikilink_form(body, vault_index)
        if wf_count > 0:
            # Re-assemble: original frontmatter prefix + new body
            modified = _strip_then_prepend_body(modified, new_body)

        # Feature 5: frontmatter schema repair. Legacy-key migrations run on
        # any parse-clean block; unknown-field strips and decision-status
        # normalisation additionally need a canonical type (schema_drift).
        if f.frontmatter.has_block and not f.frontmatter.parse_error:
            fields = f.frontmatter.fields
            renames: list[tuple[str, str]] = []
            drops: set[str] = set()
            status_fix: Optional[tuple[str, str]] = None
            if "node_type" in fields:
                if "type" in fields:
                    drops.add("node_type")
                else:
                    renames.append(("node_type", "type"))
            if "originSessionId" in fields:
                if "source_session" in fields:
                    drops.add("originSessionId")
                else:
                    renames.append(("originSessionId", "source_session"))
            drift = schema_drift_for_file(f, _TASK_STATUS_ALIASES)
            unverified = _uncorroborated_type(f.file_type, fields) if drift else None
            if drift and not unverified:
                for k in drift.get("unknown_fields", ()):
                    if k not in ("node_type", "originSessionId"):
                        drops.add(k)
                si = drift.get("status_invalid")
                if si and f.file_type == "decision" and si.get("normalizable"):
                    status_fix = (si["value"], DECISION_STATUS_ALIASES[si["value"]])
            if unverified:
                # Reported, never acted on. The human decides whether the file
                # is mistyped or genuinely half-built; tidy is not entitled to
                # strip content on the strength of a `type:` nothing backs up.
                schema_actions[str(f.rel_path)] = {"unverified_type": unverified}
            if renames or drops or status_fix:
                for old, new in renames:
                    modified = _rename_frontmatter_key(modified, old, new)
                if drops:
                    modified = _drop_frontmatter_keys(modified, drops)
                if status_fix:
                    modified = _set_frontmatter_scalar(modified, "status", status_fix[1])
                act: dict[str, Any] = {}
                if drops:
                    act["dropped"] = sorted(drops)
                if renames:
                    act["renamed"] = [f"{o} -> {n}" for o, n in renames]
                if status_fix:
                    act["status"] = f"{status_fix[0]} -> {status_fix[1]}"
                schema_actions[str(f.rel_path)] = act

        # Feature 2: bump updated (only if other changes happened, and only on eligible types)
        if modified != original and f.file_type in UPDATED_BUMP_TYPES:
            modified = _bump_updated_field(modified, today)

        if modified != original:
            rel = str(f.rel_path)
            file_proposals[rel] = {
                "original_hash": _hash_short(original),
                "proposed_hash": _hash_short(modified),
                "proposed_content": modified,
            }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_dir": str(project_dir),
        "project_slug": project_slug,
        "summary": {
            "files_modified": len(file_proposals),
            "indexes_rebuilt": len(index_proposals),
            "schema_files": len(schema_actions),
            "total_changes": len(file_proposals) + len(index_proposals),
        },
        "file_proposals": file_proposals,
        "index_proposals": index_proposals,
        "schema_actions": schema_actions,
    }


def _strip_then_prepend_body(text: str, new_body: str) -> str:
    """Replace the body portion of a file (keeping frontmatter intact)."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return new_body
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return new_body
    return "\n".join(lines[: close_idx + 1]) + "\n" + new_body


def _hash_short(s: str) -> str:
    """8-char hex content hash (for visual diff confidence in summary)."""
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


# ============================================================
# Preview writer (disk)
# ============================================================


def write_preview_to_disk(project_dir: Path, change_set: dict[str, Any]) -> Path:
    """Write the change_set to .adjudant-tidy-preview/. Returns preview path."""
    preview = project_dir / PREVIEW_DIR_NAME
    if preview.exists():
        shutil.rmtree(preview)
    preview.mkdir()

    # changes.json
    (preview / "changes.json").write_text(json.dumps(change_set, indent=2, default=str))

    # files/ tree
    files_root = preview / "files"
    files_root.mkdir()
    for rel, info in change_set["file_proposals"].items():
        target = files_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(info["proposed_content"])
    for rel, info in change_set["index_proposals"].items():
        target = files_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(info["proposed_content"])

    # summary.md
    summary_lines = [
        "# Tidy preview",
        "",
        f"Generated: {change_set['generated_at']}",
        f"Project: {change_set['project_slug']}",
        "",
        "## Summary",
        "",
        f"- Files to modify: {change_set['summary']['files_modified']}",
        f"- Indexes to rebuild: {change_set['summary']['indexes_rebuilt']}",
        f"- Total changes: {change_set['summary']['total_changes']}",
        "",
        "## Index rebuilds",
        "",
    ]
    for rel, info in sorted(change_set["index_proposals"].items()):
        if not info["had_existing"]:
            marker = "create"
        elif info.get("mode") == "frontmatter_only":
            marker = "frontmatter-only"
        elif info.get("mode") == "upserted":
            marker = "upsert-entries"
        else:
            marker = "rewrite"
        summary_lines.append(f"- {marker}: `{rel}` ({info['entry_count']} entries)")
    summary_lines.append("")
    summary_lines.append("## File modifications")
    summary_lines.append("")
    for rel, info in sorted(change_set["file_proposals"].items()):
        summary_lines.append(f"- `{rel}` ({info['original_hash']} → {info['proposed_hash']})")
    if change_set.get("schema_actions"):
        summary_lines.append("")
        summary_lines.append("## Schema")
        summary_lines.append("")
        for rel, act in sorted(change_set["schema_actions"].items()):
            parts = []
            if act.get("renamed"):
                parts.append("rename " + ", ".join(act["renamed"]))
            if act.get("dropped"):
                parts.append("strip " + ", ".join(act["dropped"]))
            if act.get("status"):
                parts.append("status " + act["status"])
            summary_lines.append(f"- `{rel}`: {'; '.join(parts)}")
    summary_lines.append("")
    summary_lines.append("## Next steps")
    summary_lines.append("")
    summary_lines.append("- Review the proposed files under `files/`")
    summary_lines.append("- To apply: `python3 tidy.py apply --project-dir <PATH>`")
    summary_lines.append(f"- To discard: delete `{PREVIEW_DIR_NAME}/`")
    (preview / "summary.md").write_text("\n".join(summary_lines) + "\n")

    return preview


# ============================================================
# Apply phase
# ============================================================


def _contained(root: Path, rel: str) -> Optional[Path]:
    """`root/rel` resolved, or None when it escapes `root`.

    changes.json is editable by design (the preview window exists so a human
    or agent can review it), so its keys are untrusted input: a tampered
    `../escaped.md` used to be written outside the project, bypassing both the
    backup and the walker's skip set.
    """
    try:
        root_r = root.resolve()
        target = (root_r / rel).resolve()
    except (OSError, ValueError):
        return None
    if target == root_r or root_r not in target.parents:
        return None
    return target


SKIPPED_NOTE_NAME = "SKIPPED-STALE.txt"

# Why a proposal was refused. Four different stories: an edit is not a
# deletion, and neither is a file someone else created in the meantime.
SKIP_REASONS: dict[str, str] = {
    "changed": "edited since preview, applying would eat that edit",
    "vanished": "deleted or renamed since preview, applying would resurrect it",
    "appeared": "created since preview, the preview expected nothing here",
    "unreadable": "could not be read to compare against the preview",
}


def _skip_reason(
    live: Path,
    original_hash: Optional[str],
    expects_creation: bool,
) -> Optional[str]:
    """A SKIP_REASONS key when this proposal must not be applied, else None.

    `expects_creation` marks a proposal built for a path that held no file at
    preview time (an `_index.md` for a folder that had none). It records no
    hash because there was nothing to hash, so its guard is presence rather
    than content: if something is there now, someone else put it there and it
    is not ours to overwrite.

    Otherwise the proposal was computed FROM the live bytes, so anything that
    no longer matches those bytes means applying it would destroy newer work.
    A missing file counts: a deletion or rename between the two phases is an
    intentional act, and copying the proposal back would silently undo it.
    """
    if expects_creation:
        return "appeared" if live.exists() else None
    if not original_hash:
        return None  # pre-guard preview: nothing recorded to compare against
    if not live.is_file():
        return "vanished"
    try:
        if _hash_short(live.read_text()) != original_hash:
            return "changed"
    except (OSError, UnicodeDecodeError):
        return "unreadable"
    return None


def _write_skipped_note(backup_dir: Path, skipped: list[tuple[str, str]]) -> None:
    """Record refused proposals. Body lines are `reason<TAB>path` so that
    `read_skipped_note` reads back exactly what was written."""
    legend = "\n".join(f"  {key}: {why}" for key, why in SKIP_REASONS.items())
    body = "\n".join(f"{reason}\t{rel}" for rel, reason in sorted(skipped))
    (backup_dir / SKIPPED_NOTE_NAME).write_text(
        "These paths no longer match what the preview was built from, so they\n"
        "were left alone. Re-run `tidy preview` to fold the current state in.\n\n"
        f"{legend}\n\n{body}\n"
    )


def read_skipped_note(backup_dir: Path) -> list[dict[str, str]]:
    """Parse a SKIPPED-STALE.txt back into [{'path': ..., 'reason': ...}].

    Empty list when nothing was skipped. Only tab-bearing lines are entries,
    which keeps the prose header and the reason legend out of the result.
    """
    note = backup_dir / SKIPPED_NOTE_NAME
    if not note.is_file():
        return []
    entries: list[dict[str, str]] = []
    for line in note.read_text().splitlines():
        if "\t" not in line:
            continue
        reason, _, rel = line.partition("\t")
        entries.append({"path": rel, "reason": reason})
    return entries


def apply_preview(project_dir: Path) -> Path:
    """Apply .adjudant-tidy-preview/ to live files. Returns backup dir path.

    Every proposal is gated four ways before it can touch a live file: the
    target must stay inside the project, the path must not have been applied
    already in this same run, the live file must still match what the proposal
    was computed from (see `_skip_reason`), and the pre-change copy must land
    in a backup dir that no concurrent or retried apply can overwrite.
    """
    preview = project_dir / PREVIEW_DIR_NAME
    if not preview.is_dir():
        raise RuntimeError(f"no preview at {preview}")
    changes_path = preview / "changes.json"
    if not changes_path.is_file():
        raise RuntimeError(f"corrupt preview: {changes_path} missing")
    change_set = json.loads(changes_path.read_text())

    # Unique per apply: second-granularity dirs with exist_ok=True let a retry
    # inside the same second overwrite the ONLY pre-change backup with
    # already-tidied content, making the original unrecoverable.
    backup_root = project_dir / BACKUP_DIR_NAME
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(tempfile.mkdtemp(prefix=f"{timestamp}-", dir=backup_root))

    files_root = preview / "files"
    skipped: list[tuple[str, str]] = []
    handled: set[str] = set()

    # Backup + apply
    for rel_set in (change_set["file_proposals"], change_set["index_proposals"]):
        for rel, info in rel_set.items():
            live = _contained(project_dir, rel)
            proposed = _contained(files_root, rel)
            if live is None or proposed is None or not proposed.is_file():
                continue
            # `write_preview_to_disk` collapses both proposal dicts into one
            # `files/<rel>`, so a path in both (an `_index.md` that also needs
            # a tag or schema fix) has exactly ONE proposed body and must be
            # applied exactly once. A second pass would compare the live file
            # against a hash this run just invalidated (a false stale report)
            # and overwrite the pre-change backup with already-tidied content.
            if rel in handled:
                continue
            handled.add(rel)
            # changes.json is editable by design, so `info` is untrusted too.
            info = info if isinstance(info, dict) else {}
            reason = _skip_reason(
                live,
                info.get("original_hash"),
                expects_creation=info.get("had_existing") is False,
            )
            if reason:
                skipped.append((rel, reason))
                continue
            # Backup live (if exists)
            if live.is_file():
                backup_target = backup_dir / (rel + ".legacy")
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, backup_target)
            # Apply
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(proposed, live)

    if skipped:
        _write_skipped_note(backup_dir, skipped)

    # Clean up preview
    shutil.rmtree(preview)
    return backup_dir


# ============================================================
# CLI
# ============================================================


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tidy.py",
        description="Adjudant tidy — mechanical sweep (preview / apply).",
    )
    parser.add_argument("phase", choices=["detect", "preview", "apply"])
    parser.add_argument("--project-dir", default=".", help="Project root (default: cwd)")
    parser.add_argument("--vault-dir", help="Vault root (default: resolved from breadcrumb)")
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
                f"vault project {project_dir} which doesn't exist. Run /adjudant connect first.",
                file=sys.stderr,
            )
        else:
            print(f"error: project-dir not found: {project_dir}", file=sys.stderr)
        return 1

    code_root = Path(args.project_dir).expanduser().resolve()
    files_n, n_bytes = stat_walk(project_dir)
    cost = cost_block(files_n, n_bytes, read_threshold(code_root))
    if args.estimate_only:
        print(json.dumps({"cost": cost}, indent=2))
        return 0

    if args.phase == "detect":
        print(json.dumps({"state": detect_phase(project_dir), "cost": cost}, indent=2))
        return 0

    # Resolve vault for both preview + apply (preview needs index for feature 4;
    # apply just needs project_dir but we keep the same flag for parity).
    vault_dir: Optional[Path]
    if args.vault_dir:
        vault_dir = Path(args.vault_dir).expanduser().resolve()
    elif vault_hint:
        vault_dir = vault_hint
    else:
        vault_dir = resolve_vault(project_dir)

    # Project slug: from brief.md
    slug: Optional[str] = None
    brief = project_dir / "brief.md"
    if brief.is_file():
        fm, _ = parse_frontmatter(brief.read_text(errors="replace"))
        s = fm.fields.get("slug")
        if isinstance(s, str):
            slug = s

    if args.phase == "preview":
        if detect_phase(project_dir) == "preview":
            print(f"error: preview already exists at {project_dir / PREVIEW_DIR_NAME}", file=sys.stderr)
            print("delete it or run 'apply' to commit it", file=sys.stderr)
            return 1
        vault_index = build_vault_index(vault_dir) if vault_dir and vault_dir.is_dir() else set()
        change_set = build_preview(project_dir, vault_index, slug)
        preview = write_preview_to_disk(project_dir, change_set)
        print(f"[tidy] preview written to {preview}", file=sys.stderr)
        summary = change_set["summary"]
        print(
            f"[tidy] {summary['total_changes']} changes "
            f"({summary['files_modified']} files, {summary['indexes_rebuilt']} indexes)",
            file=sys.stderr,
        )
        # Stdout: compact JSON of the summary block for Claude
        print(json.dumps({**summary, "cost": cost}))
        return 0

    if args.phase == "apply":
        if detect_phase(project_dir) != "preview":
            print(f"error: no preview at {project_dir / PREVIEW_DIR_NAME}; run 'preview' first", file=sys.stderr)
            return 1
        backup_dir = apply_preview(project_dir)
        skipped = read_skipped_note(backup_dir)
        print(f"[tidy] applied; backup at {backup_dir}", file=sys.stderr)
        if skipped:
            # Never let a skip be silent: the user asked for these changes.
            print(f"[tidy] {len(skipped)} path(s) LEFT ALONE, they no longer match "
                  f"the preview:", file=sys.stderr)
            for item in skipped:
                print(f"[tidy]   {item['path']}: "
                      f"{SKIP_REASONS.get(item['reason'], item['reason'])}",
                      file=sys.stderr)
            print("[tidy] re-run preview to fold the current state in", file=sys.stderr)
        print(json.dumps({"backup_dir": str(backup_dir), "skipped_stale": skipped}))
        return 0

    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(cli_main())
