#!/usr/bin/env python3
"""Adjudant connect — automate the 5-step project init.

Onboards a code-side project to the vault:
  1. Write `.claude/adjudant` breadcrumb (vault_path, vault_name, slug, mode,
     cost_warn_tokens, stale_after_days)
  2. Provision AGENTS.md + CLAUDE.md + GEMINI.md at project root (skip if exist)
  3. Scaffold vault project: brief.md (from project_type template) + per-type
     subfolders + per-folder `_index.md` (skip per-folder indexes for
     INDEX_EXEMPT_FOLDERS like sessions/ and images/)
  4. Write today's session note: `{vault}/projects/{slug}/sessions/{YYYY-MM-DD}.md`
  5. Append `.claude/adjudant` to project `.gitignore`
  Also: add or update the project's row in `{vault}/projects/_index.md`.

Idempotent. Re-running fills gaps; never overwrites user-authored content.

CLI:
    python3 connect.py \\
        --project-root PATH \\
        [--vault-name NAME] [--vault-path PATH] \\
        [--slug SLUG] [--project-type {coding|knowledge|plugin|tinkerage}] \\
        [--project-name "Display Name"]

Resolution order (per field):
    vault       → --vault-path → OB_VAULT env → --vault-name → existing breadcrumb → walk up
    slug        → --slug → existing breadcrumb → cwd basename
    type        → --project-type → existing brief → required (fail if missing)
    name        → --project-name → existing brief heading → slug.title()

See docs/superpowers/2026-05-26-adjudant-tidy-ramasse-log.design.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from _vault_walk import (
    INDEX_EXEMPT_FOLDERS,
    PROJECT_TYPE_DEFAULT_FOLDERS,
    _candidate_vault_paths,
    find_project_dir,
    parse_breadcrumb,
    parse_frontmatter,
    resolve_vault,
    suggest_vault_roots,
    zone_of,
)


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES = SCRIPT_DIR.parent / "skills" / "adjudant" / "templates"

VALID_PROJECT_TYPES = ("coding", "knowledge", "plugin", "tinkerage")
# Single source of the kebab-case rule: the same predicate the hooks and
# resolve_project_from_cwd gate on, so a slug connect accepts is never one a
# later consumer silently refuses.
from _vault_walk import SLUG_MAX_LEN, is_safe_slug


# ============================================================
# Slug + name helpers
# ============================================================


def validate_slug(slug: str) -> Optional[str]:
    """Return None if valid, else an error message. Thin message-carrying
    wrapper over is_safe_slug: one rule, two shapes."""
    if not slug:
        return "slug must not be empty"
    if not is_safe_slug(slug):
        return (
            f"slug {slug!r} must be lowercase kebab-case "
            f"(letters, digits, hyphens; no leading hyphen; "
            f"{SLUG_MAX_LEN} chars max)"
        )
    return None


def slug_to_title(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.replace("_", "-").split("-") if part)


def _project_dir(vault_path: Path, slug: str) -> Path:
    """Resolve a project's vault dir across zones (live / _fridge / _archive).

    Falls back to the default live-zone path when the project doesn't exist
    yet anywhere (fresh connect). Zone-aware so re-connecting a shelved
    project fills gaps in place instead of forking a duplicate in
    `projects/{slug}`.
    """
    return find_project_dir(vault_path, slug) or (vault_path / "projects" / slug)


# ============================================================
# Contract inference (v0.14.0)
# ============================================================

_CODE_EXTS = {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".rb",
              ".sh", ".swift", ".c", ".cpp", ".java"}
_INFER_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def infer_project_type(project_root: Path) -> tuple[str, str]:
    """(project_type, signal) from repo signals. Cheapest signal first."""
    if (project_root / ".claude-plugin" / "plugin.json").is_file() or \
       (project_root / "plugin.json").is_file():
        return "plugin", "plugin.json present"
    code = md = 0
    for f in project_root.rglob("*"):
        if not f.is_file():
            continue
        if any(p in _INFER_SKIP for p in f.relative_to(project_root).parts):
            continue
        if f.suffix in _CODE_EXTS:
            code += 1
        elif f.suffix == ".md":
            md += 1
    if code > 0:
        return "coding", f"{code} code file(s)"
    if md >= 3:
        return "knowledge", f"{md} markdown files, no code"
    return "tinkerage", "no dominant signal"


def infer_initial_status(project_root: Path) -> tuple[str, str]:
    """seed when the repo is nearly empty (fewer than 3 visible top-level
    entries), else active."""
    n = 0
    for f in project_root.iterdir():
        if f.name.startswith("."):
            continue
        n += 1
        if n >= 3:
            return "active", "3+ top-level entries"
    return "seed", f"{n} top-level entr{'y' if n == 1 else 'ies'}"


ARTIFACT_READERS: list[tuple[str, str]] = [
    ("AGENTS.md", "Codex, Gemini/agy, any agent"),
    ("CLAUDE.md", "Claude Code"),
    ("GEMINI.md", "agy / Antigravity"),
    (".claude/adjudant", "adjudant helpers"),
    ("vault scaffold", "the user, in Obsidian"),
    (".gitignore entries", "git"),
]


def _gitignore_has_breadcrumb(project_root: Path) -> bool:
    gi = project_root / ".gitignore"
    if not gi.is_file():
        return False
    return any(line.strip() == ".claude/adjudant" for line in gi.read_text().splitlines())


def build_contract(
    project_root: Path,
    vault_path: Optional[Path],
    vault_name: Optional[str],
    slug: str,
    project_type: str,
    type_signal: str,
    initial_status: str,
    status_signal: str,
    purpose: Optional[str],
) -> dict[str, Any]:
    """The connect contract: five required fields + per-agent artifact
    disclosure. Read-only."""
    vault_proj = _project_dir(vault_path, slug) if vault_path else None
    present = {
        "AGENTS.md": (project_root / "AGENTS.md").exists(),
        "CLAUDE.md": (project_root / "CLAUDE.md").exists(),
        "GEMINI.md": (project_root / "GEMINI.md").exists(),
        ".claude/adjudant": (project_root / ".claude" / "adjudant").is_file(),
        "vault scaffold": bool(vault_proj and vault_proj.is_dir()),
        ".gitignore entries": _gitignore_has_breadcrumb(project_root),
    }
    return {
        "required": {
            "vault": str(vault_path) if vault_path else None,
            "vault_name": vault_name,
            "slug": slug,
            "project_type": project_type,
            "initial_status": initial_status,
            "purpose": purpose,
        },
        "inferred_from": {
            "slug": "dirname / breadcrumb",
            "project_type": type_signal,
            "initial_status": status_signal,
        },
        "artifacts": [
            {"artifact": a, "reader": rdr,
             "state": "already-present" if present[a] else "will-create"}
            for a, rdr in ARTIFACT_READERS
        ],
        "state": detect_state(project_root, vault_path, slug),
        "zone": zone_of(vault_proj) if vault_proj is not None else "",
    }


# ============================================================
# Vault path resolution for connect (more permissive — accepts unconnected case)
# ============================================================


def resolve_vault_for_connect(
    project_root: Path,
    vault_path_arg: Optional[str],
    vault_name_arg: Optional[str],
) -> Optional[Path]:
    """Resolve vault path for the connect flow.

    Different from `resolve_vault()` in that it accepts explicit args first
    and tolerates a missing breadcrumb (since connect creates it).
    """
    # 1. --vault-path argument
    if vault_path_arg:
        p = Path(vault_path_arg).expanduser()
        if p.is_dir():
            return p

    # 2. OB_VAULT env var (documented in reference/connect.md resolution order)
    env = os.environ.get("OB_VAULT")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p

    # 3. --vault-name argument → search standard locations
    if vault_name_arg:
        for cand in _candidate_vault_paths(vault_name_arg):
            if cand.is_dir():
                return cand

    # 4. Existing breadcrumb (re-connect case)
    bc = parse_breadcrumb(project_root)
    if bc:
        if "vault_path" in bc:
            p = Path(bc["vault_path"]).expanduser()
            if p.is_dir():
                return p
        if "vault_name" in bc:
            for cand in _candidate_vault_paths(bc["vault_name"]):
                if cand.is_dir():
                    return cand

    # 5. Walk up for Home.md
    return resolve_vault(project_root)


def derive_vault_name(vault_path: Path, vault_name_arg: Optional[str]) -> str:
    """Vault name = explicit arg → vault dir basename."""
    if vault_name_arg:
        return vault_name_arg
    return vault_path.name


# ============================================================
# Step 1: breadcrumb
# ============================================================


def write_breadcrumb(
    project_root: Path,
    vault_path: Path,
    vault_name: str,
    slug: str,
) -> str:
    """Write the .claude/adjudant breadcrumb (six canonical keys).

    Every other key already in the file is carried through untouched. It used
    to be a hardcoded allowlist (audit 2026-07-27 finding 16), so a key added
    by hand or by a future adjudant version was silently deleted on every
    re-connect. A byte-identical rewrite is skipped.

    Returns 'created' | 'updated' | 'already-present'.
    """
    existing = parse_breadcrumb(project_root) or {}
    cwt = existing.get("cost_warn_tokens", "10000")
    sad = existing.get("stale_after_days", "30")
    canonical = {"vault_path", "vault_name", "slug", "mode",
                 "cost_warn_tokens", "stale_after_days"}
    extra = "".join(f"{k}: {v}\n" for k, v in existing.items()
                    if k not in canonical)
    content = (
        f"vault_path: {vault_path}\n"
        f"vault_name: {vault_name}\n"
        f"slug: {slug}\n"
        f"mode: project\n"
        f"cost_warn_tokens: {cwt}\n"
        f"stale_after_days: {sad}\n"
        + extra
    )
    bc = project_root / ".claude" / "adjudant"
    if bc.is_file():
        if bc.read_text() == content:
            return "already-present"
        bc.write_text(content)
        return "updated"
    bc.parent.mkdir(parents=True, exist_ok=True)
    bc.write_text(content)
    return "created"


# ============================================================
# Step 2: context files
# ============================================================


def provision_context_files(
    project_root: Path,
    slug: str = "",
    project_type: str = "",
    project_name: str = "",
    purpose: Optional[str] = None,
) -> dict[str, str]:
    """Copy AGENTS.md + CLAUDE.md + GEMINI.md from templates if missing,
    rendering placeholders in AGENTS.md. Existing files are never touched.

    Returns dict mapping filename → action ('created' | 'preserved').
    """
    actions: dict[str, str] = {}
    for fname in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        live = project_root / fname
        if live.exists():
            actions[fname] = "preserved"
            continue
        template = TEMPLATES / fname
        if not template.is_file():
            actions[fname] = f"template missing: {template}"
            continue
        text = template.read_text()
        if fname == "AGENTS.md":
            if project_name:
                text = text.replace("{Project Name}", project_name)
            if slug:
                text = text.replace("{slug}", slug)
            if project_type:
                text = text.replace("{coding|knowledge|plugin|tinkerage}", project_type)
            if purpose:
                text = text.replace("> One-line purpose of this project.", f"> {purpose}")
        live.write_text(text)
        actions[fname] = "created"
    return actions


# ============================================================
# Step 3: vault scaffold
# ============================================================


def derive_project_name(
    project_name_arg: Optional[str],
    existing_brief: Optional[Path],
    slug: str,
) -> str:
    if project_name_arg:
        return project_name_arg
    if existing_brief and existing_brief.is_file():
        text = existing_brief.read_text(errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
    return slug_to_title(slug)


def derive_project_type(
    project_type_arg: Optional[str],
    existing_brief: Optional[Path],
) -> Optional[str]:
    if project_type_arg:
        return project_type_arg
    if existing_brief and existing_brief.is_file():
        fm, _ = parse_frontmatter(existing_brief.read_text(errors="replace"))
        pt = fm.fields.get("project_type")
        if isinstance(pt, str) and pt in VALID_PROJECT_TYPES:
            return pt
    return None


def scaffold_vault_project(
    vault_path: Path,
    slug: str,
    project_type: str,
    project_name: str,
    today: str,
    initial_status: str = "active",
    purpose: Optional[str] = None,
    proj_dir: Optional[Path] = None,
) -> dict[str, list[str]]:
    """Create vault project folder, brief, subfolders, per-folder indexes.

    `proj_dir` lets the caller pass a zone-resolved dir (e.g. `_fridge/{slug}`)
    so gaps are filled in place instead of forking `projects/{slug}`. Defaults
    to the live-zone path when omitted, matching prior behavior.

    Returns dict with 'created' / 'preserved' filenames lists.
    """
    if proj_dir is None:
        proj_dir = vault_path / "projects" / slug
    created: list[str] = []
    preserved: list[str] = []

    if not proj_dir.exists():
        proj_dir.mkdir(parents=True)
        created.append(str(proj_dir.relative_to(vault_path)))

    # brief.md
    brief_path = proj_dir / "brief.md"
    if not brief_path.is_file():
        template_path = TEMPLATES / f"project-brief-{project_type}.md"
        if not template_path.is_file():
            raise RuntimeError(f"template missing: {template_path}")
        text = template_path.read_text()
        text = (
            text.replace("{kebab-slug}", slug)
                .replace("{YYYY-MM-DD}", today)
                .replace("{Project Name}", project_name)
        )
        text = text.replace("status: active", f"status: {initial_status}", 1)
        if purpose:
            text = text.replace("## INTRO\n", f"## INTRO\n\n{purpose}\n", 1)
        brief_path.write_text(text)
        created.append("brief.md")
    else:
        preserved.append("brief.md")

    # Subfolders + per-folder indexes
    defaults = PROJECT_TYPE_DEFAULT_FOLDERS.get(project_type)
    if not defaults:
        raise RuntimeError(f"unknown project_type: {project_type}")
    with_index = defaults["with_index"]
    no_index = defaults["no_index"]

    for sub in with_index + no_index:
        sub_dir = proj_dir / sub
        if not sub_dir.exists():
            sub_dir.mkdir()
            created.append(f"{sub}/")
        idx = sub_dir / "_index.md"
        if sub in INDEX_EXEMPT_FOLDERS:
            continue
        if sub in with_index and not idx.is_file():
            heading = " ".join(w.capitalize() for w in sub.replace("-", " ").replace("_", " ").split())
            idx_content = (
                "---\n"
                "type: index\n"
                f"updated: {today}\n"
                "tags:\n"
                "  - index\n"
                "---\n\n"
                f"# {heading}\n\n"
                "## Entries\n\n"
            )
            idx.write_text(idx_content)
            created.append(f"{sub}/_index.md")

    return {"created": created, "preserved": preserved}


# ============================================================
# Step 4: today's session note
# ============================================================


def write_session_note(
    vault_path: Path,
    slug: str,
    today: str,
    now_hhmm: str,
    proj_dir: Optional[Path] = None,
) -> str:
    """`proj_dir` lets the caller pass a zone-resolved dir; defaults to the
    live-zone path when omitted, matching prior behavior."""
    if proj_dir is None:
        proj_dir = vault_path / "projects" / slug
    sess_dir = proj_dir / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    sess_file = sess_dir / f"{today}.md"
    if sess_file.is_file():
        return "preserved"
    template_path = TEMPLATES / "session.md"
    if template_path.is_file():
        text = template_path.read_text()
        text = (
            text.replace("{slug}", slug)
                .replace("{YYYY-MM-DD}", today)
                .replace("{HH:MM}", now_hhmm)
        )
        # Default intent + first log entry if template has placeholder
        text = text.replace(
            "{One-line intent. Frozen after first write.}",
            "Session initiated by /adjudant connect.",
        )
    else:
        # Template-less fallback. session_id starts empty; the SessionStart
        # hook appends the live conversation UUID on the next session start.
        text = (
            "---\n"
            "type: session\n"
            f"date: {today}\n"
            f"started: \"{now_hhmm}\"\n"
            "session_id: []\n"
            "tags:\n"
            "  - session\n"
            "---\n\n"
            f"> Session initiated by /adjudant connect.\n\n"
            "## Log\n\n"
            f"- {now_hhmm} · session started\n"
        )
    sess_file.write_text(text)
    return "created"


# ============================================================
# Step 5: .gitignore
# ============================================================


def provision_dashboards(proj_dir: Path, slug: str) -> str:
    """Install the shipped .base dashboards into {project}/bases/.

    Write-if-absent per file: connect is idempotent, and a dashboard the
    human edited must never be clobbered. `{slug}` in the templates becomes
    the real slug so the base scopes to this project only. Returns
    "provisioned" (any file written), "present" (all already there), or
    "no-templates" (plugin install missing the folder - degrade quietly)."""
    src = TEMPLATES / "bases"
    if not src.is_dir():
        return "no-templates"
    dest = proj_dir / "bases"
    wrote = False
    for tpl in sorted(src.glob("dashboard-*.base")):
        target = dest / tpl.name
        if target.exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target.write_text(tpl.read_text().replace("{slug}", slug))
        wrote = True
    return "provisioned" if wrote else "present"


def append_gitignore(project_root: Path) -> str:
    """Add `.claude/adjudant` to .gitignore (idempotent). Returns 'added' / 'preserved'."""
    gi = project_root / ".gitignore"
    breadcrumb_line = ".claude/adjudant"
    if gi.is_file():
        text = gi.read_text()
        if any(line.strip() == breadcrumb_line for line in text.splitlines()):
            return "preserved"
        sep = "" if text.endswith("\n") else "\n"
        gi.write_text(text + sep + "\n# Adjudant breadcrumb (project-local)\n" + breadcrumb_line + "\n")
        return "added"
    else:
        gi.write_text("# Adjudant breadcrumb (project-local)\n" + breadcrumb_line + "\n")
        return "created"


# ============================================================
# Step 6: projects/_index.md row (upsert)
# ============================================================


PROJECTS_INDEX_ROW_RE = re.compile(
    r"^\|\s*\[\[(?P<slug>[^/|\]]+)/brief\\?\|[^]]+\]\]\s*\|"
)


def count_non_index_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(
        1 for f in folder.iterdir()
        if f.is_file() and f.suffix == ".md" and f.name != "_index.md"
    )


def newest_session_date(sessions_dir: Path) -> str:
    if not sessions_dir.is_dir():
        return "—"
    dates: list[str] = []
    for f in sessions_dir.iterdir():
        if not f.is_file() or f.suffix != ".md":
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", f.stem)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else "—"


# The canonical 6-column projects-index header (templates/_index-projects.md).
# A row is only ever inserted under THIS header — anything else is a
# hand-maintained or custom index and is left alone.
_CANONICAL_INDEX_HEADER_RE = re.compile(
    r"^\|\s*Project\s*\|\s*Type\s*\|\s*Status\s*\|\s*Decisions\s*\|"
    r"\s*Sessions\s*\|\s*Last Session\s*\|\s*$", re.I)


_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s\-|:]+\|\s*$")


def _canonical_table_body(lines: list[str]) -> Optional[tuple[int, int]]:
    """`(start, end)` line range of the canonical projects table's row body.

    `start` is the first line after the header's `|---|` separator; `end` is
    one past its last row, stopping at the first line that is not a table row.
    None when there is no canonical header, or no separator beneath it.

    Both the insert AND the replace path go through this: a row for our slug
    that happens to live in the user's own table is not ours to rewrite.
    """
    for i, line in enumerate(lines):
        if not _CANONICAL_INDEX_HEADER_RE.match(line):
            continue
        # Markdown requires the delimiter row directly beneath the header.
        if i + 1 >= len(lines) or not _TABLE_SEPARATOR_RE.match(lines[i + 1]):
            return None
        start = end = i + 2
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            end += 1
        return start, end
    return None


def upsert_projects_index_row(
    vault_path: Path,
    slug: str,
    project_type: str,
    status: str,
    decisions_n: int,
    sessions_n: int,
    last_session: str,
) -> str:
    idx = vault_path / "projects" / "_index.md"
    new_row = (
        f"| [[{slug}/brief\\|{slug}]] | {project_type} | {status} | "
        f"{decisions_n} | {sessions_n} | {last_session} |"
    )
    if not idx.is_file():
        idx.write_text(
            "---\ntype: index\nupdated: " + datetime.now().strftime("%Y-%m-%d") + "\ntags:\n  - index\n---\n\n"
            "# All Projects\n\n"
            "| Project | Type | Status | Decisions | Sessions | Last Session |\n"
            "|---------|------|--------|-----------|----------|--------------|\n"
            + new_row + "\n"
        )
        return "created-index"

    text = idx.read_text()
    lines = text.splitlines()
    # Never corrupt a hand-maintained index (audit 2026-07-27 finding 13, and
    # fix wave 1 finding 4). Insert used to land after the FIRST `|---|`
    # anywhere in the file; replace used to match `| [[slug/brief\|` on ANY
    # line, so a row for this slug inside the user's own table was overwritten
    # with the canonical 6-column form. Both paths are now confined to the
    # canonical table's own row body.
    body = _canonical_table_body(lines)
    if body is None:
        return "skipped-unknown-format"
    start, end = body

    target_pattern = re.compile(
        r"^\|\s*\[\[" + re.escape(slug) + r"/brief\\?\|"
    )
    found = False
    for i in range(start, end):
        if target_pattern.search(lines[i]):
            lines[i] = new_row
            found = True
    if not found:
        lines.insert(start, new_row)
    idx.write_text("\n".join(lines) + "\n")
    return "updated" if found else "inserted"


# ============================================================
# Top-level run
# ============================================================


def detect_state(project_root: Path, vault_path: Optional[Path], slug: Optional[str]) -> str:
    """Returns 'fresh' | 'partial' | 'connected'."""
    bc_exists = (project_root / ".claude" / "adjudant").is_file()
    vault_proj_exists = False
    if vault_path and slug:
        vault_proj_exists = _project_dir(vault_path, slug).is_dir()
    if bc_exists and vault_proj_exists:
        return "connected"
    if bc_exists or vault_proj_exists:
        return "partial"
    return "fresh"


_RECEIPT_MARK = {
    "created": "created", "preserved": "already-present",
    "added": "updated", "updated": "updated", "inserted": "updated",
    "created-index": "created",
}


def build_receipt(summary: dict[str, Any]) -> list[dict[str, str]]:
    steps = summary["steps"]
    cf = steps["context_files"]
    scaffold = steps["vault_scaffold"]
    receipt = [
        {"artifact": "AGENTS.md", "state": _RECEIPT_MARK.get(cf.get("AGENTS.md", ""), cf.get("AGENTS.md", "missing"))},
        {"artifact": "CLAUDE.md", "state": _RECEIPT_MARK.get(cf.get("CLAUDE.md", ""), cf.get("CLAUDE.md", "missing"))},
        {"artifact": "GEMINI.md", "state": _RECEIPT_MARK.get(cf.get("GEMINI.md", ""), cf.get("GEMINI.md", "missing"))},
        {"artifact": ".claude/adjudant", "state": steps["breadcrumb"]},
        {"artifact": "vault scaffold", "state": "created" if scaffold["created"] else "already-present"},
        {"artifact": "session note", "state": _RECEIPT_MARK.get(steps["session_note"], steps["session_note"])},
        {"artifact": ".gitignore entries", "state": _RECEIPT_MARK.get(steps["gitignore"], steps["gitignore"])},
        {"artifact": "projects/_index.md row", "state": _RECEIPT_MARK.get(steps["projects_index_row"], steps["projects_index_row"])},
    ]
    # Board pointer for the project types that get a tasks/ folder by default
    if summary.get("project_type") in ("coding", "plugin"):
        receipt.append({
            "artifact": "board",
            "state": "tasks/ seeds the kanban: /adjudant board, born automatically on the first task note",
        })
    return receipt


def run_connect(
    project_root: Path,
    vault_path: Path,
    vault_name: str,
    slug: str,
    project_type: str,
    project_name: str,
    today: str,
    now_hhmm: str,
    initial_status: str = "active",
    purpose: Optional[str] = None,
) -> dict[str, Any]:
    """Idempotent connect. Returns summary dict."""
    summary: dict[str, Any] = {
        "project_root": str(project_root),
        "vault_path": str(vault_path),
        "vault_name": vault_name,
        "slug": slug,
        "project_type": project_type,
        "project_name": project_name,
        "today": today,
        "steps": {},
    }

    # Zone-aware project dir: an existing shelved project is filled in place
    # rather than forked into a fresh `projects/{slug}` (v0.14.0 zones).
    proj_dir = _project_dir(vault_path, slug)

    # Step 1
    summary["steps"]["breadcrumb"] = write_breadcrumb(
        project_root, vault_path, vault_name, slug)

    # Step 2
    summary["steps"]["context_files"] = provision_context_files(
        project_root, slug, project_type, project_name, purpose)

    # Step 3
    summary["steps"]["vault_scaffold"] = scaffold_vault_project(
        vault_path, slug, project_type, project_name, today,
        initial_status=initial_status, purpose=purpose, proj_dir=proj_dir)

    # Step 4
    summary["steps"]["session_note"] = write_session_note(
        vault_path, slug, today, now_hhmm, proj_dir=proj_dir)

    # Step 5
    summary["steps"]["gitignore"] = append_gitignore(project_root)

    # Step 5b: .base dashboards (tranche 2B) - write-if-absent, so an edited
    # dashboard is never clobbered by an idempotent re-run.
    summary["steps"]["base_dashboards"] = provision_dashboards(proj_dir, slug)

    # Step 6
    decisions_n = count_non_index_files(proj_dir / "decisions")
    sessions_n = count_non_index_files(proj_dir / "sessions")
    last_session = newest_session_date(proj_dir / "sessions")
    brief_path = proj_dir / "brief.md"
    status = "active"
    if brief_path.is_file():
        fm, _ = parse_frontmatter(brief_path.read_text(errors="replace"))
        s = fm.fields.get("status")
        if isinstance(s, str) and s:
            status = s
    summary["steps"]["projects_index_row"] = upsert_projects_index_row(
        vault_path, slug, project_type, status, decisions_n, sessions_n, last_session
    )

    summary["receipt"] = build_receipt(summary)
    return summary


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="connect.py",
        description="Adjudant connect — onboard a project to the vault. Idempotent.",
    )
    parser.add_argument("--project-root", "--project-dir", dest="project_root",
                        default=".", help="Project root (default: cwd)")
    parser.add_argument("--vault-path", help="Explicit vault path")
    parser.add_argument("--vault-name", help="Vault name (looked up under standard locations)")
    parser.add_argument("--suggest-vaults", action="store_true",
                        help="Print existing cloud/local vault-location options (JSON) and exit")
    parser.add_argument("--create-vault", action="store_true",
                        help="Create --vault-path (with a projects/ dir) if it does not exist")
    parser.add_argument("--slug", help="Project slug (kebab-case)")
    parser.add_argument("--project-type", choices=VALID_PROJECT_TYPES)
    parser.add_argument("--project-name", help="Human-readable display name")
    parser.add_argument("--detect-only", action="store_true",
                        help="Print state ('fresh' | 'partial' | 'connected') and exit")
    parser.add_argument("--contract", action="store_true",
                        help="Print the init contract (inferred fields + artifact disclosure) and exit; writes nothing")
    parser.add_argument("--purpose", help="One-line project purpose (lands in AGENTS.md + brief INTRO)")
    parser.add_argument("--initial-status",
                        choices=[s for s in ("active", "seed", "fridge", "done", "dead")],
                        help="Initial brief status (default: inferred seed|active)")
    args = parser.parse_args(argv)

    if args.suggest_vaults:
        print(json.dumps({"vault_roots": suggest_vault_roots()}, indent=2))
        return 0

    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        print(f"error: project-root not found: {project_root}", file=sys.stderr)
        return 1

    # Create a brand-new vault at an explicit path when asked (guided setup):
    # the coworker picked a location that does not hold a vault yet.
    if args.create_vault and args.vault_path:
        new_vault = Path(args.vault_path).expanduser()
        if not new_vault.is_dir():
            new_vault.mkdir(parents=True, exist_ok=True)
            (new_vault / "projects").mkdir(exist_ok=True)

    # Resolve vault
    vault_path = resolve_vault_for_connect(project_root, args.vault_path, args.vault_name)
    if not vault_path:
        print("error: vault unresolvable. Pass --vault-path or --vault-name, "
              "or run inside a directory under a vault containing Home.md.", file=sys.stderr)
        return 1
    vault_name = derive_vault_name(vault_path, args.vault_name)

    # Resolve slug
    slug = args.slug
    if not slug:
        bc = parse_breadcrumb(project_root)
        if bc and "slug" in bc:
            slug = bc["slug"]
        else:
            slug = project_root.name
    err = validate_slug(slug)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    if args.contract:
        ptype_arg = args.project_type
        existing_brief = _project_dir(vault_path, slug) / "brief.md"
        ptype = derive_project_type(ptype_arg, existing_brief if existing_brief.is_file() else None)
        if ptype:
            type_signal = "explicit --project-type or existing brief"
        else:
            ptype, type_signal = infer_project_type(project_root)
        if args.initial_status:
            istatus, status_signal = args.initial_status, "explicit --initial-status"
        else:
            istatus, status_signal = infer_initial_status(project_root)
        contract = build_contract(
            project_root, vault_path, vault_name, slug,
            ptype, type_signal, istatus, status_signal, args.purpose)
        print(json.dumps({"contract": contract}, indent=2, default=str))
        return 0

    if args.detect_only:
        print(detect_state(project_root, vault_path, slug))
        return 0

    # Resolve project_type (zone-aware: a shelved project's brief still wins)
    existing_brief = _project_dir(vault_path, slug) / "brief.md"
    project_type = derive_project_type(args.project_type, existing_brief if existing_brief.is_file() else None)
    if not project_type:
        project_type = infer_project_type(project_root)[0]

    # Resolve project_name
    project_name = derive_project_name(args.project_name, existing_brief if existing_brief.is_file() else None, slug)

    # Resolve initial_status
    if args.initial_status:
        initial_status = args.initial_status
    else:
        initial_status, _sig = infer_initial_status(project_root)

    today = datetime.now().strftime("%Y-%m-%d")
    now_hhmm = datetime.now().strftime("%H:%M")

    summary = run_connect(
        project_root=project_root,
        vault_path=vault_path,
        vault_name=vault_name,
        slug=slug,
        project_type=project_type,
        project_name=project_name,
        today=today,
        now_hhmm=now_hhmm,
        initial_status=initial_status,
        purpose=args.purpose,
    )

    print(f"[connect] state: {detect_state(project_root, vault_path, slug)}", file=sys.stderr)
    print(f"[connect] vault: {vault_name} at {vault_path}", file=sys.stderr)
    print(f"[connect] project: {slug} ({project_type}) - {project_name}", file=sys.stderr)
    print(f"[connect] breadcrumb: {summary['steps']['breadcrumb']}", file=sys.stderr)
    print(f"[connect] context_files: {summary['steps']['context_files']}", file=sys.stderr)
    print(f"[connect] vault_scaffold: created={len(summary['steps']['vault_scaffold']['created'])}, "
          f"preserved={len(summary['steps']['vault_scaffold']['preserved'])}", file=sys.stderr)
    print(f"[connect] session_note: {summary['steps']['session_note']}", file=sys.stderr)
    print(f"[connect] gitignore: {summary['steps']['gitignore']}", file=sys.stderr)
    print(f"[connect] projects_index_row: {summary['steps']['projects_index_row']}", file=sys.stderr)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
