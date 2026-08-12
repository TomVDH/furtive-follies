#!/usr/bin/env python3
"""Adjudant repo_tidy — safe two-phase repo repair (symlink repair only).

Mirrors tidy.py: `preview` writes .adjudant-repo-tidy-preview/, `apply` backs
the live state up to .adjudant-repo-tidy-backup/{ts}/*.legacy then repairs.
Only repairs harness symlinks on ALREADY-ADOPTED plugins — never auto-adopts
a harness where none exists (that is ramasse-tier, deferred). Stdlib only.

CLI:  python3 repo_tidy.py {detect|preview|apply} --project-dir PATH
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from repo_walk import plugin_symlink_status, walk_plugins

PREVIEW_DIR_NAME = ".adjudant-repo-tidy-preview"
BACKUP_DIR_NAME = ".adjudant-repo-tidy-backup"


def detect_repairs(root: Path) -> list[dict[str, Any]]:
    """Missing/dangling harness symlinks on adopted plugins, with the relative
    target the repaired symlink should point at (../../skills/<name>)."""
    repairs: list[dict[str, Any]] = []
    for p in walk_plugins(root):
        st = plugin_symlink_status(p)
        if not st["adopted"]:
            continue
        # canonical skill dir name = the first real dir under skills/
        canon = next((c for c in sorted((p.dir / "skills").iterdir())
                      if c.is_dir() and not c.name.startswith(".")), None)
        if canon is None:
            continue
        for h, state in st["links"].items():
            if state in ("missing", "dangling"):
                repairs.append({
                    "plugin": p.name,
                    "source_dir": p.source,
                    "harness": h,
                    "state": state,
                    "canon_name": canon.name,
                    "link_rel": str(Path(p.source) / h / "skills" / canon.name),
                    "target_rel": str(Path("../../skills") / canon.name),
                })
    return repairs


def write_preview(root: Path, repairs: list[dict[str, Any]]) -> Path:
    preview = root / PREVIEW_DIR_NAME
    if preview.exists():
        shutil.rmtree(preview)
    (preview / "files").mkdir(parents=True)
    (preview / "changes.json").write_text(json.dumps({"repairs": repairs}, indent=2) + "\n")
    lines = ["# repo-tidy preview", "", f"{len(repairs)} symlink repair(s):", ""]
    for r in repairs:
        lines.append(f"- `{r['link_rel']}` -> `{r['target_rel']}` (was {r['state']})")
    (preview / "summary.md").write_text("\n".join(lines) + "\n")
    # record intended targets as plain files under files/ (audit trail)
    for i, r in enumerate(repairs):
        (preview / "files" / f"repair-{i:03d}.txt").write_text(
            f"{r['link_rel']} -> {r['target_rel']}\n")
    return preview


def apply_preview(root: Path) -> Path:
    preview = root / PREVIEW_DIR_NAME
    if not preview.is_dir():
        raise FileNotFoundError(f"no preview at {preview} — run preview first")
    changes = json.loads((preview / "changes.json").read_text())
    repairs = changes.get("repairs", [])
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / BACKUP_DIR_NAME / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    for r in repairs:
        link = root / r["link_rel"]
        stem = r["link_rel"].replace("/", "__")
        # A REAL directory at the link path is never destroyed: unlink() would
        # raise (swallowed), then os.symlink would crash the loop mid-repair.
        # Report it and move on (audit 2026-07-27 finding 14).
        if link.is_dir() and not link.is_symlink():
            skipped.append(r["link_rel"])
            continue
        # back up the current state of the link path (dangling target record)
        rec = backup_dir / (stem + ".legacy")
        prior = ""
        if link.is_symlink():
            try:
                prior = os.readlink(link)
            except OSError:
                prior = ""
        rec.write_text(f"state-before: {r['state']}\nprior-target: {prior}\n")
        # A REAL file at the link path holds content the metadata record does
        # not capture — unlinking it without copying the bytes destroyed it.
        if link.is_file() and not link.is_symlink():
            try:
                shutil.copy2(link, backup_dir / (stem + ".content.legacy"))
            except OSError:
                skipped.append(r["link_rel"])
                continue
        # repair: remove any existing (dangling) link, recreate relative symlink
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            try:
                link.unlink()
            except OSError:
                skipped.append(r["link_rel"])
                continue
        os.symlink(r["target_rel"], link)
    if skipped:
        (backup_dir / "SKIPPED.txt").write_text(
            "These link paths were left alone (a real directory, or the "
            "content could not be preserved):\n\n" + "\n".join(skipped) + "\n")
    shutil.rmtree(preview)
    return backup_dir


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="repo_tidy.py", description="Adjudant repo tidy — symlink repair.")
    parser.add_argument("phase", choices=["detect", "preview", "apply"])
    parser.add_argument("--project-dir", default=".", help="Repo root (default: cwd)")
    args = parser.parse_args(argv)
    root = Path(args.project_dir).expanduser().resolve()
    if not root.is_dir():
        print(f"error: project-dir not found: {root}", file=sys.stderr)
        return 1
    if args.phase == "detect":
        print(json.dumps({"repairs": detect_repairs(root)}, indent=2))
    elif args.phase == "preview":
        p = write_preview(root, detect_repairs(root))
        print(f"[repo_tidy] preview at {p}", file=sys.stderr)
        print(str(p))
    else:
        try:
            b = apply_preview(root)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"[repo_tidy] applied; backup at {b}", file=sys.stderr)
        print(str(b))
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
