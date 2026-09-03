#!/usr/bin/env python3
"""PreCompact / SessionEnd hook for adjudant.

MECHANICAL ONLY — no model calls. Must finish well inside the 5s hook budget.
One lane, cheap on-disk reads: mirror `.remember/remember.md` (or `now.md`) →
vault `_handoff.md`, with a freshness header (traffic light · age · NEXT ·
stale flag).

Since v3 that lane runs only under `--sync-only`, the flag SessionEnd already
passes. A PreCompact invocation drains stdin and returns: the handoff is
written once per session, not once per compaction. A session that compacted
three times rewrote the file three times and again at session end, each pass
clobbering the last, so `_handoff.md` recorded the last compaction rather than
the session. The tradeoff is that a session dying before SessionEnd leaves the
previous handoff in place — its own STALE flag says so, which is the honest
signal a fresh mirror of a rotated-empty buffer never gave.

Until v3 the hook also appended a `paused (compaction)` tombstone to the
session log. That marker, with started, resumed and ended, produced 164 lines
followed by nothing.

Freshness logic and the `.remember/` source picker are shared with
`/adjudant status` via `scripts/_handoff_freshness.py` (single source of truth).
The import is best-effort: if it ever fails, the hook still does its mechanical
work — it just omits the freshness header. All vault I/O fails closed: an
offline iCloud vault must never crash the compaction.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# Shared primitives live in <plugin>/scripts/. Bootstrap that onto the path
# (fixed plugin layout), then import each module under its own guard: a broken
# or mid-sync module must only degrade ITS OWN capability, never shadow a
# sibling import that succeeded and never crash the hook.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
except Exception:  # pragma: no cover - defensive
    pass

try:
    from _handoff_freshness import (
        HANDOFF_FRONTMATTER_TEMPLATE,
        compute_freshness,
        find_remember_source,
        freshness_header,
        latest_session_file,
        preserved_frontmatter,
        render_handoff,
    )
except Exception:  # pragma: no cover - degrade: mechanical work without freshness
    # The v3 `handoff` shape, kept in step with templates/handoff.md by
    # test_precompact. This is the ONE surviving inline copy and it exists for
    # a different reason than the ones v3 deleted: it runs only when the
    # shared module itself failed to import, when there is no renderer to ask.
    HANDOFF_FRONTMATTER_TEMPLATE = (
        "---\n"
        "type: handoff\n"
        "created: {today}\n"
        "updated: {today}\n"
        "---\n\n"
    )

    # A real picker, not a no-op: the whole point of degrading is that the
    # mechanical mirror still runs. Stdlib-free, like the guards below.
    def find_remember_source(project_dir):  # type: ignore
        for name in ("remember.md", "now.md"):
            candidate = project_dir / ".remember" / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def compute_freshness(*_a, **_k):  # type: ignore
        return ("", "", None, False)

    def freshness_header(*_a, **_k):  # type: ignore
        return ""

    def latest_session_file(sessions_dir, today):  # type: ignore
        return sessions_dir / f"{today}.md"

    def preserved_frontmatter(*_a, **_k):  # type: ignore
        return None

    def render_handoff(slug, today, ts, source_name, fresh_block, body, frontmatter):  # type: ignore
        # Minimal mirror of the shared layout so degraded mode keeps writing
        # a usable handoff (same heading, mirror line, separator, body).
        return (
            f"{frontmatter}"
            f"# Handoff: {slug}\n\n"
            f"{fresh_block}"
            f"*Mirrored from `.remember/{source_name}` on {today} {ts}.*\n\n"
            f"---\n\n"
            f"{body.rstrip()}\n"
        )

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

    # The slug guard must NOT depend on the import succeeding — a broken or
    # mid-sync _vault_walk must not reopen the traversal hole. Stdlib-free
    # on purpose: this block runs when imports are already failing.
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


def sync_handoff(project_dir: Path, project_root: Path, slug: str, today: str, ts: str, now: datetime) -> None:
    """Mirror the remember source → `_handoff.md` with a freshness header.

    Fails closed. Rendered by the SAME `render_handoff` the sync verb uses, so
    the two writers can't drift. A blank source is never mirrored — the
    remember plugin leaves its buffer empty at rest after rotation, and
    mirroring nothing would wipe the last surviving handoff.
    """
    source = find_remember_source(project_dir)
    if source is None:
        return
    try:
        body = source.read_text(errors="replace")
    except OSError:
        return
    if not body.strip():
        return

    session_file = latest_session_file(project_root / "sessions", today)
    light, age_str, next_line, stale = compute_freshness(project_dir, body, source, session_file, now)
    fresh = freshness_header(light, age_str, next_line, stale)
    fresh_block = f"{fresh}\n\n" if fresh else ""

    try:
        handoff = project_root / "_handoff.md"
        frontmatter = preserved_frontmatter(handoff, today) \
            or HANDOFF_FRONTMATTER_TEMPLATE.format(slug=slug, today=today, source_stem=source.stem)
        handoff.parent.mkdir(parents=True, exist_ok=True)
        handoff.write_text(
            render_handoff(slug, today, ts, source.name, fresh_block, body, frontmatter))
    except Exception:  # hook must never crash compaction, whatever the cause
        return


def main() -> int:
    # Drain stdin before anything can exit (finding 22): the PreCompact
    # payload is unbounded and an unread payload EPIPEs the harness writer
    # the moment this process returns. The content itself is unused — this
    # hook is mechanical — so drain and discard. The tty guard keeps a bare
    # interactive run from hanging; the broad except keeps a patched or
    # closed stdin from ever crashing the hook.
    try:
        if not sys.stdin.isatty():
            sys.stdin.buffer.read()
    except (OSError, ValueError, AttributeError):
        pass

    # SessionEnd asks for the write; compaction does not. A session that
    # compacted three times rewrote `_handoff.md` three times and once more at
    # session end, each pass clobbering the last, so the file recorded the last
    # compaction rather than the session. Tradeoff: a session that dies before
    # SessionEnd leaves the previous handoff in place — its own STALE flag
    # says so, which is the honest signal a fresh mirror of a stale buffer
    # never gave.
    if "--sync-only" not in sys.argv[1:]:
        return 0

    project_dir_str = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir_str:
        return 0

    project_dir = Path(project_dir_str)
    info = read_breadcrumb(project_dir)
    slug = info.get("slug", "")
    # The breadcrumb is repo-committed: reject any slug that isn't kebab-case
    # before it reaches a path (this hook write_text's _handoff.md, which
    # clobbers, so traversal here overwrites files outside the vault).
    if not slug or not is_safe_slug(slug):
        return 0

    # Single source of truth: the same 5-step resolve_vault chain the verbs and
    # shell hooks use (OB_VAULT override, vault_path, vault_name candidates,
    # legacy breadcrumb, Home.md walk-up) — so every hook writes to the SAME
    # vault. Degraded mode (broken _vault_walk): honor a locally-valid
    # vault_path only.
    vault = resolve_vault(project_dir)
    if vault is None and not _RESOLVER:
        # Degraded mode must keep the shell hooks' precedence: OB_VAULT first,
        # then a locally-valid vault_path — otherwise a mid-sync _vault_walk
        # splits writes across two vaults.
        ob = os.environ.get("OB_VAULT", "")
        p = Path(ob).expanduser() if ob else None
        if p is None or not p.is_dir():
            vault_path = info.get("vault_path", "")
            p = Path(vault_path).expanduser() if vault_path else None
        vault = p if (p is not None and p.is_dir()) else None
    if vault is None or not vault.is_dir():
        # Stale breadcrumb — fail closed. Writing anyway would materialize
        # a phantom vault directory chain (mkdir -p) on every compaction
        # instead of surfacing the misconfiguration.
        return 0

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    ts = now.strftime("%H:%M")

    # Zone-aware: shelf moves projects to _fridge/ and _archive/ without
    # touching the breadcrumb. Hardcoding projects/<slug> rewrote a phantom
    # _handoff.md in the active zone on EVERY compaction (write_text clobbers).
    project_root = find_project_dir(vault, slug)
    if project_root is None:
        return 0  # project exists in no zone: never materialize it

    sync_handoff(project_dir, project_root, slug, today, ts, now)
    return 0


if __name__ == "__main__":
    # A PreCompact hook must never block compaction: whatever goes wrong
    # (future logic error, exotic I/O failure), exit 0.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
