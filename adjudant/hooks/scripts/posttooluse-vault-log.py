#!/usr/bin/env python3
"""PostToolUse hook for adjudant.

Three mechanical jobs on tool writes under {vault}/projects/{slug}/:

  0. On a task-note change (Write OR Edit under tasks/), nudge the board:
     `board_bridge.py --ensure-only` in a capped subprocess, fire-and-forget.
  1. Append a `- HH:MM · Decision|Added: [[link]]` entry to today's session log.
  2. Stamp `source_session: <uuid>` into the new file's frontmatter — ONLY
     when the breadcrumb opts in with `stamp_source_session: true` (accepted
     truthy spellings: true|1|yes|on, case-insensitive; absent means off).
     The session log (job 1) already records the session→file mapping, so
     the per-file stamp is opt-in provenance, not a default. Session notes /
     _handoff / _index files are excluded by the stamping primitive.

Jobs 1 and 2 fire only on Write (not Edit/MultiEdit, which typically modify
existing files). All jobs are best-effort and fail-closed.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Shared primitives live in <plugin>/scripts/. Deferred behind the breadcrumb
# gate (finding 21): this hook fires on EVERY Write/Edit machine-wide, and the
# module-level _vault_walk import made even the unlinked no-op path pay ~37 ms.
# One guard per module, mirroring precompact: a broken or mid-sync module must
# only degrade ITS OWN capability, never shadow a sibling import that
# succeeded.
_BOOTSTRAPPED = False
_STAMP = False
_RESOLVER = False


def _bootstrap() -> None:
    """Populate the helper globals; called only once a breadcrumb names a
    slug, so unlinked projects never pay the import."""
    global _BOOTSTRAPPED, _STAMP, _RESOLVER
    global stamp_source_session, find_project_dir, is_safe_slug, resolve_vault
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    except Exception:  # pragma: no cover - defensive
        pass

    try:
        from _session_stamp import stamp_source_session as _stamp
        stamp_source_session = _stamp
        _STAMP = True
    except Exception:  # pragma: no cover - degrade: log without stamping
        _STAMP = False

        def stamp_source_session(*_a, **_k):  # type: ignore
            return False

    try:
        from _vault_walk import (find_project_dir as _fpd,
                                 is_safe_slug as _iss,
                                 resolve_vault as _rv)
        find_project_dir, is_safe_slug, resolve_vault = _fpd, _iss, _rv
        _RESOLVER = True
    except Exception:  # pragma: no cover - degrade: breadcrumb vault_path only
        _RESOLVER = False

        def resolve_vault(_project_root, _env_vault=None):  # type: ignore
            return None

        # Zone-aware project resolution must not depend on the import
        # succeeding (stdlib-free: this block runs when imports are already
        # failing).
        def find_project_dir(vault, slug):  # type: ignore
            cands = [vault / "projects" / slug,
                     vault / "projects" / "_fridge" / slug,
                     vault / "projects" / "_archive" / slug]
            for c in cands:
                if (c / "brief.md").is_file():
                    return c
            for c in cands:
                if c.is_dir():
                    return c
            return None

        # Slug guard must not depend on the import succeeding (stdlib-free:
        # this block runs when imports are already failing).
        def is_safe_slug(slug):  # type: ignore
            if not isinstance(slug, str) or not slug or len(slug) > 64:
                return False
            return slug[0] != "-" and all(
                c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)


def read_breadcrumb(project_dir: Path) -> dict:
    """Read `.claude/adjudant` breadcrumb (`key: value` per line, YAML-ish).

    Format written by connect.py — uses `:` separator. Old `=` format
    (pre-v0.4.0) also tolerated for transition.
    """
    breadcrumb = project_dir / ".claude" / "adjudant"
    if not breadcrumb.exists():
        return {}
    info = {}
    for line in breadcrumb.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = ":" if ":" in line else ("=" if "=" in line else None)
        if not sep:
            continue
        k, v = line.split(sep, 1)
        info[k.strip()] = v.strip()
    return info


def main() -> int:
    # Drain stdin before anything can exit (finding 22 discipline): the
    # payload carries the full Write content, and an unread 8 MB payload
    # EPIPEs the harness writer when the hook returns early.
    try:
        raw = sys.stdin.read()
    except OSError:
        raw = ""

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return 0

    info = read_breadcrumb(Path(project_dir))
    slug = info.get("slug", "")
    if not slug:
        return 0
    # Past the breadcrumb gate: now pay for the helper imports (finding 21).
    _bootstrap()
    # Repo-committed breadcrumb: reject non-kebab slugs before any path build.
    # (The resolve+relative_to containment check below already fails closed for
    # writes; this stops a traversal slug from being used at all.)
    if not is_safe_slug(slug):
        return 0

    # Same 5-step resolve_vault chain as the verbs and the other hooks, so
    # every hook writes to the SAME vault. Degraded mode (broken _vault_walk):
    # honor a locally-valid vault_path only.
    vault = resolve_vault(Path(project_dir))
    if vault is None and not _RESOLVER:
        # Degraded mode keeps the shell hooks' precedence: OB_VAULT first,
        # then a locally-valid vault_path (same-vault invariant).
        ob = os.environ.get("OB_VAULT", "")
        p = Path(ob).expanduser() if ob else None
        if p is None or not p.is_dir():
            vault_path = info.get("vault_path", "")
            p = Path(vault_path).expanduser() if vault_path else None
        vault = p if (p is not None and p.is_dir()) else None
    if vault is None or not vault.is_dir():
        return 0  # stale breadcrumb — fail closed, never log to a phantom path
    # Zone-aware: shelf moves projects to _fridge/ and _archive/ without
    # touching the breadcrumb. A hardcoded projects/<slug> silently dropped
    # every write to a shelved project (the relative_to check below failed).
    project_root = find_project_dir(vault, slug)
    if project_root is None:
        return 0  # project exists in no zone: never materialize it

    try:
        payload = json.loads(raw)
    except Exception:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    file_path_str = tool_input.get("file_path") or tool_input.get("path")
    session_id = (payload.get("session_id") or "").strip()
    if not file_path_str:
        return 0

    file_path = Path(file_path_str)
    try:
        # Resolve both sides so a symlinked or differently-normalized Write
        # path (~/Obsidian/V → iCloud, `..` segments) still matches the vault.
        rel = file_path.resolve().relative_to(project_root.resolve())
    except (ValueError, OSError):
        return 0

    parts = rel.parts
    if not parts:
        return 0

    # --- Job 0: task-note change (Write OR Edit under tasks/) nudges the
    # board. Capped subprocess (3s), output discarded, every failure mode
    # (missing bridge, timeout, dead python3) swallowed: a board refresh must
    # never block the hook or the log jobs below. ---
    if tool_name in ("Write", "Edit") and parts[0] == "tasks":
        bridge = Path(__file__).resolve().parents[2] / "scripts" / "board_bridge.py"
        try:
            subprocess.run(
                ["python3", str(bridge), "--ensure-only",
                 "--project-dir", str(project_dir)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=3, check=False)
        except Exception:
            pass

    # Jobs 1 and 2 act only on NEW files (Write tool, not Edit/MultiEdit)
    if tool_name != "Write":
        return 0

    now = datetime.now()  # single clock read: date and time can't straddle midnight
    today = now.strftime("%Y-%m-%d")
    ts = now.strftime("%H:%M")
    # Today's note — or the latest existing one when the session straddles
    # midnight (the new day's note appears at the next SessionStart).
    session_file = project_root / "sessions" / f"{today}.md"
    if not session_file.exists():
        try:
            # digit classes, not ?: a stray abcd-ef-gh.md must never win
            candidates = sorted((project_root / "sessions").glob(
                "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"))
        except OSError:
            candidates = []
        # Real dates not after today only (finding 19): a future-dated note
        # must never absorb appends; the digit glob admits impossible dates.
        for cand in reversed(candidates):
            try:
                datetime.strptime(cand.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if cand.stem <= today:
                session_file = cand
                break

    # --- Job 1: append a session-log entry (if a session note exists) ---
    if session_file.exists():
        is_decision = parts[0] == "decisions"
        label = "Decision" if is_decision else "Added"
        link = f"[[projects/{slug}/{'/'.join(parts)}]]"
        try:
            with session_file.open("a") as f:
                f.write(f"- {ts} · {label}: {link}\n")
        except OSError:
            pass  # log-write failure must not block job 2

    # --- Job 2: stamp source_session on the new file, breadcrumb opt-in
    # (stamp_source_session: true). The stamping primitive itself decides
    # what's eligible (skips session notes, _handoff, _index, files without
    # frontmatter, files already stamped). Best-effort. ---
    stamp_enabled = (info.get("stamp_source_session", "") or "").strip().lower() in (
        "true", "1", "yes", "on")
    if stamp_enabled and session_id and _STAMP:
        try:
            stamp_source_session(file_path, session_id)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    # A PostToolUse hook must never surface as a tool failure: whatever goes
    # wrong (future logic error, exotic I/O failure), exit 0.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
