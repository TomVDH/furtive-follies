#!/usr/bin/env python3
"""Re-nest Claude Code auto-memory frontmatter that was flattened.

A memory note is `name` / `description` / `metadata.type`. Something flattened
`metadata.type` up to a top-level `type:` — most likely Obsidian's Properties
editor, which does not support nested objects and rewrites frontmatter flat
when a file is edited through it. adjudant then read those files as whatever
`type:` claimed and, for `type: project`, proposed stripping `name:` and
`description:` as unknown fields. adjudant 1.0.1 stops proposing that; this
puts the frontmatter back.

The flattening PRESERVED the value, so this is a mechanical re-nest, not a
reconstruct-from-content. It only holds while `name:`/`description:` are still
on the file — run it before applying any clean preview computed under 1.0.0.

    python3 renest_memory.py preview <dir>    # read-only, lists candidates
    python3 renest_memory.py apply   <dir>    # backs up, then rewrites

Stdlib only. Never recurses into its own backup dir. Every file it rewrites is
copied to `<dir>/.renest-backup/<relative path>` first.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# metadata.type's vocabulary. A `type:` outside this set was not produced by
# flattening a memory note, so it is some other problem and not ours to touch.
METADATA_TYPES = ("user", "feedback", "project", "reference")

BACKUP_DIR_NAME = ".renest-backup"

_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def _frontmatter_bounds(lines: list[str]):
    """(close_index) of the frontmatter block, or None."""
    if not lines or lines[0].rstrip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return i
    return None


def plan_file(text: str):
    """Return (new_text, type_value) when this file is a flattened memory
    note, else (None, reason).

    The bar is deliberately high — this runs over a real vault:
      - frontmatter parses and has a closing fence
      - top-level `name:` AND `description:` (no adjudant type has either, so
        their presence alone rules out every real vault file)
      - top-level `type:` whose value is a metadata.type value
      - no `metadata:` already (nothing to undo)
      - none of a project brief's own required fields, belt-and-braces
    """
    lines = text.split("\n")
    close = _frontmatter_bounds(lines)
    if close is None:
        return None, "no frontmatter"

    fields: dict[str, tuple[int, str]] = {}
    for i in range(1, close):
        m = _KEY_RE.match(lines[i])
        if m:
            fields.setdefault(m.group(1), (i, m.group(2).strip().rstrip("\r")))

    if "metadata" in fields:
        return None, "already nested"
    if "name" not in fields or "description" not in fields:
        return None, "not a memory note (no name/description)"
    if "type" not in fields:
        return None, "no type to put back"
    for guard in ("project_type", "slug", "aliases", "created"):
        if guard in fields:
            return None, f"looks like a real brief ({guard} present)"

    idx, value = fields["type"]
    if value not in METADATA_TYPES:
        return None, f"type: {value} is not a metadata.type value"

    # Match the file's line endings rather than imposing LF on a CRLF vault.
    eol = "\r" if lines[idx].endswith("\r") else ""
    out = lines[:idx] + lines[idx + 1:]
    # Dropping the type line shifted the closing fence left by one; metadata
    # goes immediately above it, which is where the memory writer puts it.
    new_close = close - 1
    out.insert(new_close, f"  type: {value}{eol}")
    out.insert(new_close, f"metadata:{eol}")
    return "\n".join(out), value


def iter_candidates(root: Path):
    for p in sorted(root.rglob("*.md")):
        if BACKUP_DIR_NAME in p.parts:
            continue
        try:
            with p.open(encoding="utf-8", newline="") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        new_text, info = plan_file(text)
        if new_text is not None:
            yield p, text, new_text, info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("phase", choices=("preview", "apply"))
    ap.add_argument("directory")
    args = ap.parse_args(argv)

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        print(f"[renest] not a directory: {root}", file=sys.stderr)
        return 2

    found = list(iter_candidates(root))
    if not found:
        print("[renest] 0 flattened memory notes found — nothing to do.")
        return 0

    backup_root = root / BACKUP_DIR_NAME
    for path, original, new_text, value in found:
        rel = path.relative_to(root)
        if args.phase == "preview":
            print(f"  {rel}  →  metadata.type: {value}")
            continue
        dest = backup_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        tmp = path.with_name(path.name + ".renest-tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        tmp.replace(path)
        print(f"  {rel}  →  metadata.type: {value}")

    verb = "would re-nest" if args.phase == "preview" else "re-nested"
    print(f"[renest] {verb} {len(found)} file(s).")
    if args.phase == "preview":
        print("[renest] nothing was written. Re-run with `apply` to do it.")
    else:
        print(f"[renest] originals backed up under {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
