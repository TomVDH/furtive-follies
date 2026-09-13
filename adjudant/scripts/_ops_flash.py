"""Ops flash writer. The statusline paints the message as a bolt.

Writes `<unix ts> <message>` to `~/.claude/statusline-cache/ops-{key}`.
The statusline (S0a) paints `⚡ <message>` on the next repaint.
It appends `seen <ts>` and keeps the flash ten seconds from that stamp.
This module is the only writer. The reader is statusline/statusline.sh.

The key rule, stated once:
  1. Resolve the path: absolute, symlinks followed (`Path.resolve()`).
     Node's `process.cwd()` gives the statusline the physical path.
  2. Fold a linked worktree to its main checkout.
     Read `gitdir: <main>/.git/worktrees/<name>` from the `.git` file.
     Resolve first, then fold. Both sides strip the same string.
  3. Replace `/` with `_` and space with `-`. Keep the last 120 characters.

CLI: `python3 _ops_flash.py --project-dir DIR MESSAGE`.
The bash helper wraps the CLI. Never raises.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

KEY_TAIL = 120


def main_checkout(path: str) -> str:
    """Return the main checkout for a worktree path, else the path.

    Walk up to the first `.git`. A directory ends the walk: keep the path.
    A file is a worktree or a submodule pointer.
    Only a `/.git/worktrees/` pointer is a worktree.
    Same strip as statusline.sh S1: `${_p%/.git/worktrees/*}`.
    """
    d = path
    while d and d != os.path.dirname(d):
        git = os.path.join(d, ".git")
        if os.path.isdir(git):
            return path
        if os.path.isfile(git):
            try:
                first = open(git, encoding="utf-8", errors="replace").readline()
            except OSError:
                return path
            first = first.strip()
            if first.startswith("gitdir:"):
                target = first[len("gitdir:"):].strip()
                if "/.git/worktrees/" in target:
                    return target.rsplit("/.git/worktrees/", 1)[0]
            return path
        d = os.path.dirname(d)
    return path


def flash_key(project_dir) -> str:
    """`/Users/t/My Repo` -> `_Users_t_My-Repo`, the last 120 characters."""
    try:
        p = str(Path(os.fspath(project_dir)).expanduser().resolve())
    except (OSError, RuntimeError):
        p = os.path.abspath(os.fspath(project_dir))
    p = main_checkout(p)
    k = p.replace("/", "_").replace(" ", "-")
    return k[-KEY_TAIL:]


def cache_dir() -> Path:
    return Path.home() / ".claude" / "statusline-cache"


def flash_file(project_dir) -> Path:
    return cache_dir() / f"ops-{flash_key(project_dir)}"


def ops_flash(msg: str, project_dir=None) -> bool:
    """Write the flash. Return True when a file was written.

    `project_dir` defaults to $CLAUDE_PROJECT_DIR, then the cwd.
    The statusline keys on the session root. Hooks see it as $CLAUDE_PROJECT_DIR.
    Create the cache dir only when `~/.claude` exists.
    """
    msg = " ".join(str(msg or "").split())
    if not msg:
        return False
    target = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        cache = cache_dir()
        if not cache.is_dir():
            if not cache.parent.is_dir():
                return False
            cache.mkdir(exist_ok=True)
        f = cache / f"ops-{flash_key(target)}"
        # Whole-file rewrite. A newer flash drops the `seen` line.
        # The clock restarts.
        f.write_text(f"{int(time.time())} {msg}\n")
        return True
    except Exception:
        return False


def _cli(argv: list[str]) -> int:
    project_dir = None
    words: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--project-dir" and i + 1 < len(argv):
            project_dir = argv[i + 1]
            i += 2
            continue
        if a.startswith("--project-dir="):
            project_dir = a[len("--project-dir="):]
        else:
            words.append(a)
        i += 1
    ops_flash(" ".join(words), project_dir)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_cli(sys.argv[1:]))
    except Exception:  # pragma: no cover - a flash never fails its caller
        sys.exit(0)
