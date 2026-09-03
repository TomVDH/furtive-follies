#!/usr/bin/env python3
"""The net-subtractive contract, enforced.

`clean` may delete, merge and rewrite in place. It may not create a vault
file. That was a promise in a reference doc — `reference/tidy.md` read "No new
file creation beyond _index.md regenerations" — and a promise in prose cannot
be tested. Two things then made it false. The preview and backup trees wrote
roughly 25 copies per run into the vault they were cleaning, which plan 1
fixed by moving scratch under $TMPDIR. The index generator is the half that
survived: on a folder with no `_index.md` it wrote a brand-new file, so the
sentence disclaimed its own exception and the only measurable growth left in
the vault was the one the doc had carved out.

This module is the same rule expressed where it can be checked. Every write
`clean` makes goes through the guard, and a path that does not already hold a
file is refused instead of created.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Optional


class VaultCreateRefused(RuntimeError):
    """A caller tried to create a vault file inside a net-subtractive pass."""


class VaultWriteGuard:
    """Context manager permitting only in-place rewrites and removals.

    Every write `clean` makes goes through `rewrite` or `remove`. A path that
    does not already exist, or that resolves outside `root`, is refused rather
    than created — so "clean must not add files" is a property of the code
    rather than a rule someone has to remember.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.rewritten = 0
        self.removed = 0
        self.created = 0          # stays 0; a non-zero value is a bug

    def __enter__(self) -> "VaultWriteGuard":
        return self

    def __exit__(self, exc_type: Optional[type], exc: Optional[BaseException],
                 tb: Optional[TracebackType]) -> None:
        return None

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if self.root not in resolved.parents and resolved != self.root:
            raise VaultCreateRefused(f"{path} is outside {self.root}")
        return resolved

    def rewrite(self, path: Path, text: str) -> None:
        """Replace the content of a file that already exists."""
        target = self._contained(path)
        if not target.is_file():
            raise VaultCreateRefused(
                f"clean may not create {path}: it rewrites and removes only")
        target.write_text(text)
        self.rewritten += 1

    def remove(self, path: Path) -> None:
        """Delete a file that already exists. Absent is not an error."""
        target = self._contained(path)
        if not target.exists():
            return
        target.unlink()
        self.removed += 1
