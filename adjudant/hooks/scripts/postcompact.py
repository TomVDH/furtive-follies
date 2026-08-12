#!/usr/bin/env python3
"""PostCompact hook for adjudant.

MECHANICAL ONLY, no model calls. One cheap job: after the harness compacts
the conversation, append the compaction gist to today's vault session log:

    - HH:MM · compacted: <summary, single line, first 160 chars>

PreCompact keeps writing the pause tombstone; this hook adds the content the
markers-only log was missing. The summary rides in on stdin JSON: the probe
on this Claude Code version confirmed the `compaction_summary` field, and
documented fallback keys (`summary`, `compact_summary`, `message`) are tried
in order for older or future payload shapes. An empty or missing summary
writes nothing (gate on real signal). All vault I/O fails closed: a stale
breadcrumb or offline vault must never crash the hook, and the hook never
creates a session note (that is SessionStart's job).
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Shared primitives live in <plugin>/scripts/. Mirror precompact's bootstrap
# pattern, one guard per module: a broken or mid-sync module must only degrade
# ITS OWN capability, never shadow a sibling import that succeeded.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
except Exception:  # pragma: no cover - defensive
    pass

try:
    from _vault_walk import find_project_dir, is_safe_slug, resolve_vault
    _RESOLVER = True
except Exception:  # pragma: no cover - degrade: breadcrumb vault_path only
    _RESOLVER = False

    def resolve_vault(_project_root, _env_vault=None):  # type: ignore
        return None

    # Zone-aware project resolution must not depend on the import succeeding
    # (stdlib-free: this block runs when imports are already failing).
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

    # Slug guard must not depend on the import succeeding (stdlib-free: this
    # block runs when imports are already failing).
    def is_safe_slug(slug):  # type: ignore
        if not isinstance(slug, str) or not slug or len(slug) > 64:
            return False
        return slug[0] != "-" and all(
            c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)

# Payload keys that may carry the compaction summary, most trusted first.
# `compaction_summary` is probe-verified on the installed Claude Code version;
# the rest are documented fallbacks so a payload rename degrades gracefully.
SUMMARY_KEYS = ("compaction_summary", "summary", "compact_summary", "message")

# One log line stays scannable; the full summary lives in the transcript.
GIST_MAX = 160


def read_breadcrumb(project_dir: Path) -> dict:
    """Read `.claude/adjudant` breadcrumb (`key: value` per line, YAML-ish).

    Format written by connect.py uses the `:` separator; the old `=` format
    (pre-v0.4.0) is also tolerated for transition.
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


def extract_gist(payload: dict) -> str:
    """Pull the compaction summary from the payload, collapsed to one line.

    First non-empty string among SUMMARY_KEYS wins. Newlines and runs of
    whitespace collapse to single spaces; the result is clipped to GIST_MAX.
    Returns "" when there is no real signal.
    """
    for key in SUMMARY_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:GIST_MAX]
    return ""


def main() -> int:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return 0

    info = read_breadcrumb(Path(project_dir))
    slug = info.get("slug", "")
    # Repo-committed breadcrumb: reject non-kebab slugs before any path build.
    if not slug or not is_safe_slug(slug):
        return 0

    # Same 5-step resolve_vault chain as the verbs and the other hooks, so
    # every hook writes to the SAME vault. Degraded mode (broken _vault_walk):
    # honor OB_VAULT first, then a locally-valid vault_path only.
    vault = resolve_vault(Path(project_dir))
    if vault is None and not _RESOLVER:
        ob = os.environ.get("OB_VAULT", "")
        p = Path(ob).expanduser() if ob else None
        if p is None or not p.is_dir():
            vault_path = info.get("vault_path", "")
            p = Path(vault_path).expanduser() if vault_path else None
        vault = p if (p is not None and p.is_dir()) else None
    if vault is None or not vault.is_dir():
        return 0  # stale breadcrumb, fail closed, never log to a phantom path

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    gist = extract_gist(payload)
    if not gist:
        return 0  # empty compaction summary carries no signal worth a write

    now = datetime.now()  # single clock read: date and time can't straddle midnight
    ts = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    # Today's note, or the latest existing one when the session straddles
    # midnight (the new day's note appears at the next SessionStart).
    project_root = find_project_dir(vault, slug)
    if project_root is None:
        return 0  # zone-aware: never materialize a project that exists nowhere
    sessions_dir = project_root / "sessions"
    session_file = sessions_dir / f"{today}.md"
    if not session_file.exists():
        try:
            # digit classes, not ?: a stray abcd-ef-gh.md must never win
            candidates = sorted(sessions_dir.glob(
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
    if not session_file.exists():
        return 0  # no daily note to append to; creating one is not this hook's job

    try:
        with session_file.open("a") as f:
            f.write(f"- {ts} · compacted: {gist}\n")
    except OSError:
        pass  # offline vault: the compaction already happened, nothing to block

    return 0


if __name__ == "__main__":
    # A PostCompact hook must never surface as a harness failure: whatever
    # goes wrong (future logic error, exotic I/O failure), exit 0.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
