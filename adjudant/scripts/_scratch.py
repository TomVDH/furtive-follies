#!/usr/bin/env python3
"""Adjudant scratch space — preview and backup directories, OUTSIDE the vault.

Every preview and backup adjudant wrote used to land inside the vault project
it was operating on, unbounded and never reaped: a cleanup run whose whole benefit
was dropping three tags from twelve notes wrote roughly 25 files and 3x the
touched bytes into the vault, permanently. A cleanup tool that adds more than
it removes is not a cleanup tool.

Scratch now lives under $TMPDIR, keyed by project, and backups rotate. The
rotation is modelled on board.py's BACKUP_KEEP, which was the only backup path
in the plugin that ever pruned itself.

Nothing here creates a directory: callers mkdir when they are ready to write,
so a read-only run leaves no trace at all.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

# Newest N backup directories kept per project per kind. Mirrors board.py:69.
BACKUP_KEEP = 5

# A project directory name reaches us from the filesystem, so it is not
# trusted to be a safe path segment. Anything outside this class collapses to
# a hyphen, which makes traversal impossible by construction rather than by
# a check that could be forgotten.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _tmp_root() -> Path:
    """$TMPDIR when set, else the platform default. Mirrors task-ledger.py."""
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir())


def scratch_dir(project_dir: Path, kind: str) -> Path:
    """Where `kind` scratch for `project_dir` belongs. Creates nothing.

    `kind` is a short slug such as "clean-preview" or "clean-backup". The result
    is never inside `project_dir`, which is the entire point of this module.
    """
    name = _UNSAFE.sub("-", project_dir.name).strip("-") or "project"
    # The NAME alone is not unique: two vaults each holding a project called
    # `demo` shared one tree, so one project's apply could read the other's
    # preview and one project's rotation could delete the other's only
    # pre-change backup. It also made tests fail from a previous run's
    # leftovers, with a clean tree and no code change. The digest of the
    # resolved path disambiguates; the name stays so a human finding the
    # directory can tell whose it is.
    try:
        resolved = str(project_dir.resolve())
    except OSError:
        resolved = str(project_dir)
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]
    safe_kind = _UNSAFE.sub("-", kind).strip("-") or "scratch"
    return _tmp_root() / "adjudant" / f"{name}-{digest}" / safe_kind


def prune_backups(backup_root: Path, keep: int = BACKUP_KEEP) -> None:
    """Keep the newest `keep` subdirectories of `backup_root`, delete the rest.

    Timestamped names sort lexically by time, so a plain sort is chronological.
    Housekeeping only: every failure is swallowed, because failing to prune
    must never fail the operation that just succeeded.
    """
    try:
        existing = sorted(d for d in backup_root.iterdir() if d.is_dir())
    except OSError:
        return
    for stale in existing[:max(0, len(existing) - keep)]:
        shutil.rmtree(stale, ignore_errors=True)
