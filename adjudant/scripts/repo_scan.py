#!/usr/bin/env python3
"""Adjudant repo_scan — read-only structural drift detectors for the repo target.

Mirrors clean's deep pass for the code repo. Emits a JSON report on stdout that
`check repo` renders. Layered: a general core (context files, plan age) plus a
marketplace layer (version coherence, symlink integrity, registration) that
auto-activates when .claude-plugin/marketplace.json is present.

drift_items is cardinality-based: version mismatches + symlink issues on adopted
plugins + registration gaps + stale plans. Per-plugin context files are
informational only (never counted).

CLI:  python3 repo_scan.py --project-dir PATH [--json] [--stale-days N] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

from _cost import cost_block, read_threshold, stat_walk
from repo_walk import (
    context_files_status,
    is_marketplace_repo,
    parse_marketplace_json,
    plan_file_ages,
    plugin_symlink_status,
    walk_plugins,
)
from token_budget import report as token_budget_report


def detect_version_coherence(root: Path) -> dict:
    """Marketplace entry version vs each plugin.json. Reuses the parity rule of
    scripts/check_marketplace_versions.py (read-only display here)."""
    market = parse_marketplace_json(root)
    plugins = {p.source: p for p in walk_plugins(root)}
    mismatches = []
    for entry in market.get("plugins", []):
        name = entry.get("name", "?")
        mver = entry.get("version", "")
        source = (entry.get("source", "") or "").lstrip("./")
        p = plugins.get(source)
        pver = p.version if p else None
        if pver is None:
            mismatches.append({"plugin": name, "issue": "source plugin.json not found", "source": source})
        elif mver != pver:
            mismatches.append({"plugin": name, "marketplace": mver, "plugin_json": pver})
    return {"mismatches": mismatches}


def detect_symlink_integrity(root: Path) -> dict:
    """Broken/missing harness symlinks on ADOPTED plugins only."""
    issues = []
    matrix = {}
    for p in walk_plugins(root):
        st = plugin_symlink_status(p)
        matrix[p.name] = st
        if st["adopted"]:
            for h, state in st["links"].items():
                if state in ("missing", "dangling"):
                    issues.append({"plugin": p.name, "harness": h, "state": state})
    return {"issues": issues, "matrix": matrix}


def detect_registration(root: Path) -> dict:
    """Every plugin dir registered in marketplace.json, and every registered
    source path exists."""
    market = parse_marketplace_json(root)
    registered = {(e.get("source", "") or "").lstrip("./") for e in market.get("plugins", [])}
    on_disk = {p.source for p in walk_plugins(root)}
    unregistered = sorted(on_disk - registered)
    dangling_sources = sorted(s for s in registered if s and not (root / s).is_dir())
    return {"unregistered": unregistered, "dangling_sources": dangling_sources}


def skill_roots_in(root: Path) -> list[Path]:
    """Every canonical skill dir in the scanned repo.

    Two repo shapes are covered: a marketplace repo (`<plugin>/skills/<name>/`,
    with plugins found the way every other detector finds them) and a bare
    single-plugin repo (`skills/<name>/` at the root). Harness mirrors point at
    the canonical dir, so the list is deduped by resolved path and each skill
    is measured exactly once.
    """
    bases = [p.dir / "skills" for p in walk_plugins(root)] + [root / "skills"]
    seen: set[Path] = set()
    out: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            try:
                key = d.resolve()
            except OSError:  # pragma: no cover - dangling link
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
    return out


def token_budget_for_repo(root: Path) -> dict[str, Any]:
    """Context cost of every skill surface IN THE SCANNED REPO.

    Anchored on `root`, not on this plugin's install path. The block used to
    measure `<install>/skills/adjudant` unconditionally, so `check repo
    --project-dir <any other repo>` reported adjudant's own context cost as
    that repo's.

    Surfaces are labelled relative to the repo root, so two plugins each
    shipping a SKILL.md stay distinguishable. The budget lookup inside
    token_budget.report stays skill-relative, so a declared budget still finds
    its surface wherever the repo sits on disk.
    """
    surfaces: list[dict[str, Any]] = []
    for skill_root in skill_roots_in(root):
        for s in token_budget_report(skill_root)["surfaces"]:
            surfaces.append(
                {**s, "file": str((skill_root / s["file"]).relative_to(root))})
    return {
        "surfaces": surfaces,
        "total": sum(s["tokens"] for s in surfaces),
        "over_count": sum(1 for s in surfaces if s["over"]),
    }


def run_scan(root: Path, *, today: date, stale_days: int = 30) -> dict[str, Any]:
    marketplace = is_marketplace_repo(root)
    version = detect_version_coherence(root) if marketplace else {"mismatches": []}
    symlinks = detect_symlink_integrity(root) if marketplace else {"issues": [], "matrix": {}}
    registration = detect_registration(root) if marketplace else {"unregistered": [], "dangling_sources": []}
    context = context_files_status(root)
    plans = plan_file_ages(root, today, stale_days=stale_days)
    stale_plans = [p for p in plans if p["stale"]]

    # Per-plugin context files: informational only.
    per_plugin_context = []
    if marketplace:
        for p in walk_plugins(root):
            per_plugin_context.append({
                "plugin": p.name,
                "agents": (p.dir / "AGENTS.md").is_file(),
                "claude": (p.dir / "CLAUDE.md").is_file(),
            })

    drift_items = (
        len(version["mismatches"])
        + len(symlinks["issues"])
        + len(registration["unregistered"])
        + len(registration["dangling_sources"])
        + len(stale_plans)
    )

    return {
        "meta": {
            "root": str(root),
            "is_marketplace_repo": marketplace,
            "plugins_scanned": len(walk_plugins(root)),
            "stale_days": stale_days,
        },
        "summary": {"drift_items": drift_items, "stale_plan_count": len(stale_plans)},
        "version_coherence": version,
        "symlink_integrity": symlinks,
        "registration": registration,
        "context_files": {"repo_root": context, "per_plugin_informational": per_plugin_context},
        "plan_ages": plans,
        "token_budget": token_budget_for_repo(root),
    }


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="repo_scan.py", description="Adjudant repo drift scan (read-only).")
    parser.add_argument("--project-dir", default=".", help="Repo root (default: cwd)")
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument("--today", help="YYYY-MM-DD override (testing/determinism)")
    parser.add_argument("--json", action="store_true", help="(default) emit JSON on stdout")
    parser.add_argument("--out", help="Write JSON to FILE instead of stdout")
    parser.add_argument("--estimate-only", action="store_true",
                        help="Print only the cost block (stat-only walk) and exit")
    args = parser.parse_args(argv)

    root = Path(args.project_dir).expanduser().resolve()
    if not root.is_dir():
        print(f"error: project-dir not found: {root}", file=sys.stderr)
        return 1

    files_n, n_bytes = stat_walk(root, exts=(".md", ".py", ".json"))
    cost = cost_block(files_n, n_bytes, read_threshold(root))
    if args.estimate_only:
        print(json.dumps({"cost": cost}, indent=2))
        return 0

    today = date.fromisoformat(args.today) if args.today else date.today()
    report = run_scan(root, today=today, stale_days=args.stale_days)
    report["cost"] = cost
    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).expanduser().write_text(payload + "\n")
        print(f"[repo_scan] wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
