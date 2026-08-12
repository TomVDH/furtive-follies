#!/usr/bin/env python3
"""Adjudant repo_walk — read-only primitives for the repo cleanup target.

Mirrors _vault_walk.py's role for the code repo: plugin discovery, marketplace
detection, Impeccable-symlink integrity, context-file presence, plan-file age.
Stdlib only, never writes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

HARNESS_DIRS = ("source", ".claude", ".gemini")
COMPLETION_MARKERS = ("status: done", "status: complete", "status: shipped", "✅")


@dataclass
class PluginInfo:
    name: str
    dir: Path
    version: str
    has_skills: bool
    source: str  # the plugin dir name


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def parse_plugin_json(plugin_dir: Path) -> dict:
    return _read_json(plugin_dir / ".claude-plugin" / "plugin.json")


def parse_marketplace_json(root: Path) -> dict:
    return _read_json(root / ".claude-plugin" / "marketplace.json")


def is_marketplace_repo(root: Path) -> bool:
    return (root / ".claude-plugin" / "marketplace.json").is_file()


def walk_plugins(root: Path) -> list[PluginInfo]:
    """Every subdir with a .claude-plugin/plugin.json. Sorted by name."""
    out: list[PluginInfo] = []
    for d in sorted(root.iterdir(), key=lambda p: p.name):
        if not d.is_dir() or d.name.startswith(".") or d.name.startswith("_"):
            continue
        pj = d / ".claude-plugin" / "plugin.json"
        if not pj.is_file():
            continue
        meta = parse_plugin_json(d)
        skills_dir = d / "skills"
        has_skills = skills_dir.is_dir() and any(skills_dir.iterdir())
        out.append(PluginInfo(
            name=meta.get("name", d.name),
            dir=d,
            version=meta.get("version", ""),
            has_skills=has_skills,
            source=d.name,
        ))
    return out


def _canonical_skill_dirs(plugin: PluginInfo) -> list[Path]:
    base = plugin.dir / "skills"
    if not base.is_dir():
        return []
    return [c for c in sorted(base.iterdir()) if c.is_dir() and not c.name.startswith(".")]


def plugin_symlink_status(plugin: PluginInfo) -> dict:
    """Per-harness-dir link status for a plugin's canonical skill(s).

    A plugin with no skills needs no harness → all links "n/a", adopted False.
    Otherwise, for the first canonical skill dir, each of source/.claude/.gemini
    is "ok" (symlink resolves to canonical), "dangling" (symlink exists but
    resolves elsewhere / to a missing target), or "missing" (no such symlink).
    adopted = has_skills AND at least one link is "ok".
    """
    links = {h: "n/a" for h in HARNESS_DIRS}
    canon_dirs = _canonical_skill_dirs(plugin)
    if not plugin.has_skills or not canon_dirs:
        return {"adopted": False, "links": links}
    canon = canon_dirs[0]
    canon_name = canon.name
    for h in HARNESS_DIRS:
        link = plugin.dir / h / "skills" / canon_name
        if not link.is_symlink():
            links[h] = "missing"
            continue
        try:
            resolved = link.resolve()
        except OSError:
            resolved = None
        links[h] = "ok" if (resolved is not None and resolved == canon.resolve()) else "dangling"
    adopted = any(v == "ok" for v in links.values())
    return {"adopted": adopted, "links": links}


def context_files_status(root: Path) -> dict:
    agents = (root / "AGENTS.md").is_file()
    claude_p = root / "CLAUDE.md"
    claude = claude_p.is_file()
    imports = False
    if claude:
        try:
            first = next((ln.strip() for ln in claude_p.read_text().splitlines() if ln.strip()), "")
            imports = first == "@AGENTS.md"
        except OSError:
            imports = False
    return {"agents": agents, "claude": claude, "claude_imports_agents": imports}


def _has_completion_marker(text: str) -> bool:
    head = text[:400].lower()
    return any(m.lower() in head for m in COMPLETION_MARKERS)


def plan_file_ages(root: Path, today: date, stale_days: int = 30) -> list[dict]:
    """docs/superpowers/**/*.md with no completion marker, with age in days.

    Age is from mtime. `today` is injected for deterministic tests.
    """
    base = root / "docs" / "superpowers"
    if not base.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(base.rglob("*.md")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if _has_completion_marker(text):
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).date()
        except OSError:
            continue
        age = (today - mtime).days
        out.append({"path": str(f.relative_to(root)), "age_days": age, "stale": age >= stale_days})
    return out
