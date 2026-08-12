#!/usr/bin/env python3
"""Adjudant session-id stamping primitives.

Single source of truth for stamping the Claude Code conversation UUID onto
vault writes — so "which session produced this decision?" is one hop, not a
grep through 191 transcript files.

Two fields:
  - `session_id:` (YAML list) on session notes — accumulates every conversation
    UUID that touched the session. Stamped by the SessionStart hook
    (connect.py only scaffolds the empty list).
  - `source_session:` (scalar) on decisions/notes/docs/sources/releases/etc. —
    the conversation UUID the file was authored in. Stamped by the PostToolUse
    hook on new vault writes.

Both writes are idempotent and fail-closed: if the file isn't shaped like an
adjudant frontmatter file, we do nothing rather than corrupt it.

The UUID is a pointer that can dangle (Claude Code transcripts in
~/.claude/projects/...-private-tmp/ are ephemeral, and $HOME can change between
machines). That is a feature: a dangling pointer still tells you *which*
session and roughly *when* — the decision's content lives in the vault.

Stdlib only.

CLI:
    python3 _session_stamp.py session-id  <session_file> <uuid>
    python3 _session_stamp.py source-session <file_path> <uuid>

Both subcommands exit 0 on success (including no-op idempotent cases) and 0
on safe-skip (no frontmatter, malformed, etc.). They exit 1 only on argparse
errors. Hook scripts should treat any failure as "best-effort, never block."
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# A vault frontmatter file starts with `---\n` on line 1 and has a closing
# `\n---\n` somewhere below (or `\n---` at EOF, matching parse_frontmatter's
# grammar). Anything else: skip. A CRLF file never matches because reads are
# newline-untranslated — stamping one would rewrite every line as LF.
_FM_OPEN = "---\n"
# Trailing whitespace on the fence line is tolerated, but NOT subsequent
# newlines — eating those would silently strip the blank line a Markdown body
# usually starts with.
_FM_CLOSE_RE = re.compile(r"\n---[ \t]*(?:\n|\Z)", re.MULTILINE)

# Conservative UUID acceptance — Claude Code uses standard UUIDs, but accept
# anything that isn't whitespace and is short enough to look like an ID. We
# don't want to silently drop a malformed-but-real ID; we just refuse empty.
_VALID_ID_RE = re.compile(r"^\S{4,}$")


def _primitives():
    """Shared write primitives, imported lazily so the no-op paths of the
    hooks that import this module never pay the _vault_walk import cost."""
    try:
        from _vault_walk import atomic_write_text, file_lock
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _vault_walk import atomic_write_text, file_lock
    return atomic_write_text, file_lock


def _read_text_safe(path: Path) -> str | None:
    """Read UTF-8 with newlines untranslated; None on decode or OS errors.

    `newline=""` is the CRLF guard: a translated read would let a CRLF file
    through the `---\\n` gate and the rewrite would churn every line to LF.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _write_safe(path: Path, text: str) -> bool:
    """Atomic replace; False when the write cannot land (safe-skip)."""
    atomic_write_text, _ = _primitives()
    try:
        atomic_write_text(path, text)
    except OSError:
        return False
    return True


def _split_frontmatter(text: str) -> tuple[str | None, str | None]:
    """Return (frontmatter_block, body) — both without the `---` fences — or
    (None, None) if the file is not frontmatter-shaped."""
    if not text.startswith(_FM_OPEN):
        return None, None
    rest = text[len(_FM_OPEN):]
    m = _FM_CLOSE_RE.search("\n" + rest)
    if not m:
        return None, None
    # m.start() is relative to "\n" + rest; subtract the leading newline
    fm_block = rest[: m.start()]
    body_start = m.end() - 1  # account for the leading newline we prepended
    body = rest[body_start:]
    return fm_block, body


def _join_frontmatter(fm_block: str, body: str) -> str:
    if not fm_block.endswith("\n"):
        fm_block += "\n"
    return _FM_OPEN + fm_block + "---\n" + body


# ============================================================
# session_id: list-valued, on session notes
# ============================================================

def add_to_session_id_list(session_file: Path, uuid: str) -> bool:
    """Append `uuid` to the `session_id:` list in the session note frontmatter.

    Idempotent — if `uuid` is already in the list, no-op. Creates the list if
    the field is missing. Returns True if the file was modified, False
    otherwise. Returns False on any safe-skip (no file, symlink, no
    frontmatter, malformed, undecodable, unwritable, empty uuid).

    The whole read-modify-write runs under `file_lock` with an atomic
    replace: concurrent SessionStart hooks were measured losing 19 of 30
    appended UUIDs on the bare write_text path.
    """
    if not _VALID_ID_RE.match(uuid or ""):
        return False
    if session_file.is_symlink():
        return False
    if not session_file.is_file():
        return False
    _, file_lock = _primitives()
    with file_lock(session_file):
        return _append_session_id_locked(session_file, uuid)


def _append_session_id_locked(session_file: Path, uuid: str) -> bool:
    text = _read_text_safe(session_file)
    if text is None:
        return False
    fm, body = _split_frontmatter(text)
    if fm is None:
        return False

    lines = fm.split("\n")
    # Find an existing session_id: line. Support three shapes:
    #   session_id: []            (inline empty)
    #   session_id:               (block list follows on next lines as "  - x")
    #   session_id:\n  - x\n  - y (block list, possibly with items)
    sid_idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^session_id\s*:", ln):
            sid_idx = i
            break

    if sid_idx is None:
        # No field at all — append a new block before the end of the frontmatter.
        new_block = f"session_id:\n  - {uuid}"
        # Drop trailing empties so we don't leave a blank line in the middle.
        while lines and lines[-1] == "":
            lines.pop()
        lines.append(new_block)
        new_fm = "\n".join(lines) + "\n"
        return _write_safe(session_file, _join_frontmatter(new_fm, body))

    head = lines[sid_idx]
    after_colon = head.split(":", 1)[1].strip()

    # Find where the block list (if any) ends.
    j = sid_idx + 1
    list_items: list[str] = []
    while j < len(lines):
        m = re.match(r"^\s*-\s*(.*)$", lines[j])
        if not m:
            break
        # Strip quotes so `- "uuid"` dedupes against the bare uuid (mirrors
        # the inline-list branch)
        list_items.append(m.group(1).strip().strip('"').strip("'"))
        j += 1

    if after_colon in ("", "[]"):
        # Block-style or inline-empty
        if uuid in list_items:
            return False
        # Replace the head with the canonical block opener
        lines[sid_idx] = "session_id:"
        # Insert the new item at end of the existing block
        insert_at = sid_idx + 1 + len(list_items)
        lines.insert(insert_at, f"  - {uuid}")
        new_fm = "\n".join(lines)
        if not new_fm.endswith("\n"):
            new_fm += "\n"
        return _write_safe(session_file, _join_frontmatter(new_fm, body))

    # Inline-with-items form like `session_id: [uuid1, uuid2]`. Convert
    # the file to block form to keep one shape.
    inline_items = []
    m = re.match(r"^\[(.*)\]$", after_colon)
    if m:
        inline_items = [s.strip().strip('"').strip("'") for s in m.group(1).split(",") if s.strip()]
    else:
        # A bare scalar value — treat as the first item.
        inline_items = [after_colon.strip().strip('"').strip("'")]
    if uuid in inline_items:
        return False
    inline_items.append(uuid)
    block = "session_id:\n" + "\n".join(f"  - {it}" for it in inline_items)
    lines[sid_idx] = block
    new_fm = "\n".join(lines)
    if not new_fm.endswith("\n"):
        new_fm += "\n"
    return _write_safe(session_file, _join_frontmatter(new_fm, body))


# ============================================================
# source_session: scalar, on decisions/notes/docs/sources/releases/etc.
# ============================================================

# Files we should NOT stamp — system-managed or aggregate files where
# "which conversation authored this" makes no sense.
_NEVER_STAMP_NAMES = {
    "_handoff.md",
    "_index.md",
    "_iteration.md",
    "MEMORY.md",   # aggregate perma-memory: many sessions append, none authored it
}
_NEVER_STAMP_PREFIXES = ("_index-",)


def _should_stamp_source(file_path: Path) -> bool:
    name = file_path.name
    if name in _NEVER_STAMP_NAMES:
        return False
    if any(name.startswith(p) for p in _NEVER_STAMP_PREFIXES):
        return False
    # Session notes get session_id (list), not source_session. Adjudant pins
    # session notes to live directly under a `sessions/` folder, so we check
    # the immediate parent — NOT `parts`, which false-positives on any ancestor
    # directory happening to be called "sessions".
    if file_path.parent.name == "sessions":
        return False
    return True


def stamp_source_session(file_path: Path, uuid: str) -> bool:
    """Insert `source_session: <uuid>` into the file's frontmatter if absent.

    Idempotent — if a `source_session:` field is already present (even empty),
    no-op. Returns True if the file was modified. Returns False on any
    safe-skip (excluded filename, no file, no frontmatter, malformed, empty
    uuid, already present).
    """
    if not _VALID_ID_RE.match(uuid or ""):
        return False
    if file_path.is_symlink():
        return False
    if not file_path.is_file():
        return False
    if not _should_stamp_source(file_path):
        return False
    _, file_lock = _primitives()
    with file_lock(file_path):
        text = _read_text_safe(file_path)
        if text is None:
            return False
        fm, body = _split_frontmatter(text)
        if fm is None:
            return False
        if re.search(r"^source_session\s*:", fm, re.MULTILINE):
            return False  # already stamped (even empty); never overwrite

        # Append at the end of the frontmatter block.
        fm = fm.rstrip("\n") + f"\nsource_session: {uuid}\n"
        return _write_safe(file_path, _join_frontmatter(fm, body))


# ============================================================
# CLI
# ============================================================

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="_session_stamp")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_sid = sub.add_parser("session-id", help="Append UUID to session_id list")
    p_sid.add_argument("file")
    p_sid.add_argument("uuid")

    p_src = sub.add_parser("source-session", help="Stamp source_session if absent")
    p_src.add_argument("file")
    p_src.add_argument("uuid")

    args = parser.parse_args(argv)

    try:
        if args.mode == "session-id":
            add_to_session_id_list(Path(args.file), args.uuid)
        elif args.mode == "source-session":
            stamp_source_session(Path(args.file), args.uuid)
    except Exception:
        # Best-effort: a stamping failure must never break the host workflow.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
