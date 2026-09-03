#!/usr/bin/env python3
"""PostToolUse hook for adjudant.

Two mechanical jobs on tool writes under {vault}/projects/{slug}/:

  1. Append a `- HH:MM · Decision|Added: [[link]]` entry to today's session
     log, creating that note when this is the first real write of the day
     (v3: SessionStart no longer creates one on every open).
  2. Stamp `source_session: <uuid>` into the new file's frontmatter — ONLY
     when the breadcrumb opts in with `stamp_source_session: true` (accepted
     truthy spellings: true|1|yes|on, case-insensitive; absent means off).
     The session log (job 1) already records the session→file mapping, so
     the per-file stamp is opt-in provenance, not a default. Session notes /
     _handoff / _index files are excluded by the stamping primitive.

Both jobs fire only on Write (not Edit/MultiEdit, which typically modify
existing files). Both are best-effort and fail-closed.

This hook spawns no subprocess. Until v3 a job 0 fired
`board_bridge.py --ensure-only` on any Write OR Edit under `tasks/`, which
scaffolded `board-data.json`, `board.html` and a lock file into a vault
project that had never asked for a board — three unrequested files against
six intentional writes. `board` is opt-in: the deck is born by running
`/adjudant board`, and nothing else. Deleting the branch is what let the
PostToolUse matcher narrow back to `Write`, so an Edit anywhere on the
machine no longer wakes this hook for work it cannot do.
"""

import json
import os
import sys
from datetime import datetime, timedelta
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
            cands = [vault / "projects" / z / slug
                     for z in ("active", "paused", "finished", "archive")]
            cands.append(vault / "projects" / slug)
            cands += [vault / "projects" / z / slug
                      for z in ("_fridge", "_archive")]
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


_SESSION_NOTE = """---
type: session
created: %(today)s
updated: %(today)s
---

> {One-line intent. Frozen after first write.}

## Log

"""


def ensure_session_note(sessions_dir: Path, today: str) -> Path:
    """Today's session note, created if this is the first real write.

    v3 moved creation here from SessionStart: a note that exists only because
    a session opened records nothing, and 29% of the vault's notes were exactly
    that. Created with noclobber semantics so two async hooks racing on the
    first write of the day cannot truncate each other.

    The frontmatter is the `session` shape templates/session.md declares, which
    since v3 is the schema itself — `type`, `created`, `updated`, and nothing
    else. A note this hook writes must validate against the same derived
    FIELD_SCHEMA the write gate applies, so the three pre-v3 fields (`date`,
    `started`, `session_id`) and the bare `session` tag are gone with the
    template that used to declare them. The intent placeholder stays: the
    UserPromptSubmit nudge greps for it, and the template's own first line is
    written at session end rather than at creation.
    """
    note = sessions_dir / f"{today}.md"
    if note.exists():
        return note
    try:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(note, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return note                      # the other hook won; append to theirs
    except OSError:
        return note                      # read-only vault: caller's append no-ops
    try:
        with os.fdopen(fd, "w") as f:
            f.write(_SESSION_NOTE % {"today": today})
    except OSError:
        pass
    return note


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

    # Both jobs act only on NEW files (Write tool, not Edit/MultiEdit)
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
        # Yesterday or today only. Two guards, two different bugs:
        #
        # A future-dated note must never absorb appends (finding 19) — the
        # digit glob admits impossible dates.
        #
        # And the straddle has a FLOOR. This fallback exists for a session
        # that starts 23:40 and ends 00:10; it is not a licence to append to
        # whatever note happens to be newest. Before lazy creation this never
        # showed, because SessionStart always made today's note first. With
        # Task 6 the mask is gone, and an unbounded `<= today` let a vault
        # whose newest session note was months old silently absorb the day's
        # work into it. Found by an adversarial prover after plan 1 landed.
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        for cand in reversed(candidates):
            try:
                datetime.strptime(cand.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if yesterday <= cand.stem <= today:
                session_file = cand
                break

    # --- Job 1: append a session-log entry, creating the note if this is the
    # first real write of the day ---
    is_decision = parts[0] == "decisions"
    label = "Decision" if is_decision else "Added"
    try:
        from _place import link as _link
        entry = _link(f"{slug}/{'/'.join(parts)}")
    except Exception:
        # Degraded mode: _place is unimportable, or the path shape is one it
        # refuses. Write the bare target rather than nothing — the hook must
        # not fail, and a target with no brackets is visibly not a link.
        entry = f"{slug}/{'/'.join(parts)}"
    if not session_file.exists():
        # No note for today and none to straddle into: this write is the first
        # real work of the day, so the note is born here.
        session_file = ensure_session_note(project_root / "sessions", today)
    try:
        with session_file.open("a") as f:
            f.write(f"- {ts} · {label}: {entry}\n")
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
