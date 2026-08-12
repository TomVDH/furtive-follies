#!/usr/bin/env python3
"""Adjudant cost primitives: token-cost estimation for heavy verbs.

Stdlib-only. Read-only: uses stat() sizes exclusively, never opens files.

The estimate approximates what Claude will READ back into context (the
helper's JSON plus prose the verb sends Claude to read), not what Python
scans. `bytes // 4` is the locked heuristic (ASCII-dominant markdown).

Public API:
    est_tokens(n_bytes) -> int
    stat_walk(root, exts=(".md",), skip=DEFAULT_SKIP) -> (files, bytes)
    breadcrumb_int(code_root, key, default) -> int
    read_threshold(code_root) -> int          # breadcrumb cost_warn_tokens or 10000
    cost_block(files, n_bytes, threshold) -> dict
    verb_weights() -> dict[str, str]           # verb name -> light|medium|heavy
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from _vault_walk import DEFAULT_SKIP, parse_breadcrumb

# Token-frugal default: the reduced build asks before pulling more than this
# many estimated tokens into context (heavy verbs pre-flight on it). Override
# per project with `cost_warn_tokens:` in `.claude/adjudant`.
DEFAULT_WARN_TOKENS = 10000
VALID_WEIGHTS = ("light", "medium", "heavy")
METADATA_PATH = Path(__file__).resolve().parent / "command-metadata.json"


def est_tokens(n_bytes: int) -> int:
    """bytes // 4: the locked chars-per-token heuristic."""
    return max(0, int(n_bytes)) // 4


def stat_walk(
    root: Path,
    exts: tuple[str, ...] = (".md",),
    skip: tuple[str, ...] = DEFAULT_SKIP,
) -> tuple[int, int]:
    """(file_count, total_bytes) for files under root with the given suffixes.

    stat() only, never opens a file. Skips DEFAULT_SKIP dirs plus _legacy/.
    """
    skip_set = set(skip) | {"_legacy"}
    if not root.is_dir():
        return 0, 0
    files = 0
    total = 0
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix not in exts:
            continue
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        if any(part in skip_set for part in rel.parts):
            continue
        try:
            total += f.stat().st_size
        except OSError:
            continue
        files += 1
    return files, total


def breadcrumb_int(code_root: Optional[Path], key: str, default: int) -> int:
    """Integer breadcrumb field with fallback (bad values fall back silently)."""
    if code_root:
        bc = parse_breadcrumb(Path(code_root))
        if bc and key in bc:
            try:
                return max(1, int(bc[key]))
            except (TypeError, ValueError):
                pass
    return default


def read_threshold(code_root: Optional[Path]) -> int:
    return breadcrumb_int(code_root, "cost_warn_tokens", DEFAULT_WARN_TOKENS)


def cost_block(files: int, n_bytes: int, threshold: int) -> dict:
    est = est_tokens(n_bytes)
    return {
        "est_read_tokens": est,
        "files": files,
        "bytes": n_bytes,
        "threshold": threshold,
        "warn": est >= threshold,
    }


def verb_weights() -> dict[str, str]:
    meta = json.loads(METADATA_PATH.read_text())
    return {v["name"]: v.get("weight", "") for v in meta.get("verbs", [])}
