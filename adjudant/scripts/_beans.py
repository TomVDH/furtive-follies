#!/usr/bin/env python3
"""Beans as a work-item source. The ONE module that runs the `beans` binary.

Beans (github.com/hmans/beans) is a CLI issue tracker storing issues as
markdown in `.beans/`. Where a repo uses it, Beans owns the work items and
adjudant stops writing vault task notes there. Adjudant keeps everything Beans
does not do: sessions, decisions, briefs, handoffs, indexes, draw, dream.

TWO FACTS, NEVER CONFLATED
--------------------------
  owns(code_root)  — does this REPO use Beans?  breadcrumb `tracker: beans`.
                     Persistent, travels with the repo, read in microseconds.
  available()      — is `beans` on THIS MACHINE? shutil.which, never persisted.

They are orthogonal because the repo is mirrored across machines and the binary
is a Homebrew install that may exist on only one. Conflating them turns a repo
that is Beans-owned on one machine into a broken repo on the other.

    repo says | machine has | behaviour
    ----------|-------------|----------------------------------------------
    vault     | no beans    | today's behaviour, byte for byte
    vault     | beans       | today's behaviour
    beans     | beans       | this module drives it
    beans     | no beans    | REFUSE AND REPORT. Never fall back to vault.

The last row is why `owns()` does not call the binary. A Beans-owned repo has
an empty `tasks/` by design, so falling back would reseed the board from an
empty folder and replace a deck of real beans with an empty board. The work
would look like it had vanished.

WHY THE CLI AND NOT THE FILES
-----------------------------
Reading `.beans/*.md` directly would reimplement Beans' id generation, its
index and its etag semantics, and would break the first time Beans changes its
own format. The CLI is the supported contract, it already emits JSON, and it is
the only thing that hands us compare-and-swap (`--if-match`).

NO HOOK MAY IMPORT THIS MODULE'S RUNNING HALF. Hooks fire on every Write and
every Bash call; `beans list --json` measured 52-62 ms. The `beans-not-in-hooks`
validator enforces it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

# A verb may wait this long for the binary. Beans is local and answered in
# 52-62 ms when measured; anything past this is a hung filesystem, not slow work.
TIMEOUT_S = 10.0

BINARY = "beans"

# Beans' status vocabulary, in workflow order. Lanes come from Beans, not from
# adjudant: five lanes, not adjudant's seven, so nothing is mapped and nothing
# is lost. `beans update --help` declares exactly these five; the
# `beans-adapter-parity` validator fails the build when that stops being true.
STATUSES: tuple[str, ...] = ("draft", "todo", "in-progress", "completed", "scrapped")

# Terminal-lane treatment, same deck keys board.py already understands.
COLUMNS: list[dict[str, Any]] = [
    {"id": "draft", "name": "Draft", "muted": True},
    {"id": "todo", "name": "Todo"},
    {"id": "in-progress", "name": "In progress"},
    {"id": "completed", "name": "Completed", "stamp": "BUILT", "stampTone": "built",
     "muted": True},
    {"id": "scrapped", "name": "Scrapped", "stamp": "DROPPED", "stampTone": "parked",
     "muted": True},
]

# A bean carrying this priority says nothing worth showing on a card.
_UNREMARKABLE_PRIORITY = "normal"


class Result:
    """What a `beans` invocation produced, or why it produced nothing.

    Never raises at the caller. A failed call is a value, because every caller
    here is either an ambient path or a verb that must degrade rather than
    crash, and the reason has to survive to the person reading the report.
    """

    __slots__ = ("ok", "value", "reason")

    def __init__(self, ok: bool, value: Any = None, reason: str = "") -> None:
        self.ok = ok
        self.value = value
        self.reason = reason

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Result(ok={self.ok!r}, reason={self.reason!r})"


def available() -> bool:
    """Is the `beans` binary on THIS machine's PATH?

    A probe only, matching `_profile.present_capabilities`. Microseconds, and
    it never persists its answer: the same repo gives a different answer on the
    other machine and both are correct.
    """
    return shutil.which(BINARY) is not None


def _breadcrumb_value(code_root: Path, key: str) -> str:
    """One key from `<code_root>/.claude/adjudant`, lowercased.

    Parsed here rather than via `_vault_walk.parse_breadcrumb` so this module
    stays importable when the vault layer is broken or mid-sync: a repo's
    tracker is a fact about the repo, not about the vault.
    """
    crumb = Path(code_root) / ".claude" / "adjudant"
    try:
        text = crumb.read_text()
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = ":" if ":" in line else ("=" if "=" in line else None)
        if not sep:
            continue
        name, val = line.split(sep, 1)
        if name.strip() == key:
            return val.strip().lower()
    return ""


def _breadcrumb_tracker(code_root: Path) -> str:
    """The `tracker:` value in this repo's breadcrumb, lowercased."""
    return _breadcrumb_value(code_root, "tracker")


def breadcrumb_slug(code_root: Optional[Path]) -> str:
    """The vault project slug this code root is linked to, or ''.

    A batch caller (`board --all`) walks projects it did NOT resolve from a
    breadcrumb, so it must not hand this repo's code root to another project's
    board. Comparing slugs is the only check that separates them.
    """
    if code_root is None:
        return ""
    return _breadcrumb_value(code_root, "slug")


def owns(code_root: Optional[Path]) -> bool:
    """Does Beans own the work items in this repo? Breadcrumb only, no binary.

    Deliberately independent of `available()`. A machine without the binary
    must still know the repo is Beans-owned, or `clean` and `dream` would start
    judging a `tasks/` folder they were told to ignore, on the machine least
    able to check the result.
    """
    if code_root is None:
        return False
    return _breadcrumb_tracker(code_root) == "beans"


def code_root_from(hint: Optional[Path] = None) -> Optional[Path]:
    """The CODE repo root, or None when there is not one to be sure of.

    Beans lives beside the code; adjudant's own entry points are handed a path
    that may be either side of the link (`smart_project_dir` accepts both). So
    the code root is found by the thing that only a code root has: the
    breadcrumb.

    Order: `CLAUDE_PROJECT_DIR` (what every hook and verb runs under), then the
    caller's hint. No breadcrumb anywhere means no tracker key, which means not
    Beans-owned, which means today's behaviour. That is the correct answer, not
    a degraded one.
    """
    candidates: list[Path] = []
    env = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if env:
        candidates.append(Path(env))
    if hint is not None:
        candidates.append(Path(hint))
    for cand in candidates:
        try:
            if (cand / ".claude" / "adjudant").is_file():
                return cand
        except OSError:
            continue
    return None


def _config_reachable(code_root: Path) -> bool:
    """Is there a `.beans.yml` at or above `code_root`?

    A cheap stat walk standing in front of the subprocess. Beans resolves a
    project by searching upward for that file, so its absence is a definite no
    and needs no process. Measured: running the binary unconditionally in
    `detect` doubled the test suite, 40s to 81s, because connect is exercised
    hundreds of times.

    This never decides that a project EXISTS — only that one cannot. The CLI
    remains the authority for yes.
    """
    try:
        cur = Path(code_root).resolve()
    except OSError:
        return False
    while True:
        try:
            if (cur / ".beans.yml").is_file():
                return True
        except OSError:
            return False
        if cur == cur.parent:
            return False
        cur = cur.parent


def detect(code_root: Path) -> bool:
    """Does the binary report a usable Beans project at `code_root`?

    Runs the CLI, so it belongs to `connect` and to a person asking, never to
    an ambient path. `beans list` exits 0 in an empty project and 1 when there
    is no project, which is the distinction connect needs.
    """
    if not available() or not _config_reachable(code_root):
        return False
    return _run(code_root, ["list", "--json"]).ok


def _run(code_root: Path, args: list[str]) -> Result:
    """One `beans` invocation, with cwd at the repo so Beans finds its config.

    Beans searches upward for `.beans.yml`, so cwd is the whole of how it
    resolves a project. Never `shell=True`: bean ids and titles reach this from
    a file on disk.
    """
    if not available():
        return Result(False, reason=f"`{BINARY}` is not on PATH on this machine")
    try:
        proc = subprocess.run(
            [BINARY, *args],
            cwd=str(code_root),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            # CLOSED STDIN, ALWAYS. subprocess inherits the parent's stdin, so
            # a beans subcommand that ever waits on input would hang whatever
            # called it — a verb, a hook, or the test runner. Measured: the
            # suite went from 40s to past 600s the one run this was missing.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return Result(False, reason=f"`{BINARY} {args[0]}` timed out after {TIMEOUT_S:.0f}s")
    except OSError as exc:
        return Result(False, reason=f"`{BINARY}` could not run: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        first = detail[0] if detail else f"exit {proc.returncode}"
        return Result(False, reason=first)
    return Result(True, value=proc.stdout)


def _run_json(code_root: Path, args: list[str]) -> Result:
    res = _run(code_root, args)
    if not res.ok:
        return res
    try:
        return Result(True, value=json.loads(res.value or "null"))
    except json.JSONDecodeError as exc:
        return Result(False, reason=f"`{BINARY} {args[0]}` returned unreadable JSON: {exc}")


def list_beans(code_root: Path) -> Result:
    """Every bean in the repo, as the CLI's own flat records.

    Result.value is a list of dicts carrying id, slug, path, title, status,
    type, priority, tags, parent, blocked_by, blocking, body, created_at,
    updated_at and etag.

    `--full` is what adds `body`. Without it every bean arrived with no
    description at all, and the board rendered 89 cards with an empty note
    while all 89 beans had one. It is not a second pass and it is not slower in
    any way that matters: measured over 89 beans, `list --json` took 0.03s and
    `list --full --json` took 0.02s. Reading the bodies one at a time with
    `beans show` would have been the N+1 this avoids.
    """
    res = _run_json(code_root, ["list", "--full", "--json"])
    if not res.ok:
        return res
    rows = res.value
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        return Result(False, reason=f"`{BINARY} list --json` did not return a list")
    return Result(True, value=[r for r in rows if isinstance(r, dict)])


def set_status(code_root: Path, bean_id: str, status: str,
               etag: Optional[str] = None) -> Result:
    """Move one bean, refusing when it changed underneath us.

    `--if-match` is real compare-and-swap, which is stronger than the vault
    path: that one infers intent from an ancestor snapshot because task notes
    offer nothing better. A stale etag is a genuine answer, not a failure to
    handle — the caller re-seeds and says so rather than guessing.
    """
    if status not in STATUSES:
        return Result(False, reason=f"{status!r} is not a Beans status "
                                    f"({' | '.join(STATUSES)})")
    args = ["update", str(bean_id), "--status", status, "--json"]
    if etag:
        args += ["--if-match", str(etag)]
    return _run_json(code_root, args)


def create(code_root: Path, title: str, body: str = "") -> Result:
    """A new bean. The supported way one gets created, mirroring capture-task."""
    if not str(title).strip():
        return Result(False, reason="a bean needs a title")
    args = ["create", str(title), "--json"]
    if body:
        args += ["--body", str(body)]
    return _run_json(code_root, args)


def to_card(bean: dict[str, Any]) -> dict[str, Any]:
    """One bean as the card shape `board.py` already renders.

    Every property a bean can carry reaches the card, because the board's
    opened card is where a person goes to see the whole item and a field that
    stops here is a field they cannot see anywhere:

        id        <- id            title     <- title
        column    <- status        category  <- type
        notes     <- body          priority  <- priority, when not `normal`
        tags      <- tags          parent    <- parent
        blockedBy <- blocked_by    blocking  <- blocking
        slug      <- slug          beanPath  <- path
        createdAt <- created_at    updatedAt <- updated_at
        beansEtag <- etag

    Optional keys are written only when the bean actually carries them, so a
    card never grows an empty row and the sheet has nothing to hide. `priority`
    is the pattern: Beans has always had one, and omitting `normal` is what
    lets the board treat the key's presence as the whole signal.

    `parent` used to be folded into `related` and is now its own field. The
    opened card labels it, and a labelled relation plus an unlabelled copy of
    the same id in a generic reference list is the same thing said twice.
    `related` stays for the vault's own cross-links.

    `source: "beans"` is provenance, matching the `source: "task"` that
    `merge_deck` reads: a beans-seeded card must never be iceboxed as though a
    task note had vanished.
    """
    def _ids(key: str) -> list[str]:
        raw = bean.get(key)
        if not isinstance(raw, list):
            return []
        return [str(v).strip() for v in raw if str(v).strip()]

    status = str(bean.get("status") or "").strip().lower()
    priority = str(bean.get("priority") or "").strip().lower()
    card: dict[str, Any] = {
        "id": str(bean.get("id") or ""),
        "title": str(bean.get("title") or "") or str(bean.get("slug") or "untitled"),
        # An off-vocabulary status keeps its own name so the board shows what is
        # actually written. board.py files an unknown column under Unfiled,
        # which is visible, rather than under planned work, which is not.
        "column": status if status in STATUSES else status,
        "category": str(bean.get("type") or "task"),
        "related": [],
        "notes": str(bean.get("body") or "").strip(),
        "source": "beans",
        "beansEtag": str(bean.get("etag") or ""),
    }
    optional: dict[str, Any] = {
        "priority": priority if priority != _UNREMARKABLE_PRIORITY else "",
        "tags": _ids("tags"),
        "parent": str(bean.get("parent") or "").strip(),
        "blockedBy": _ids("blocked_by"),
        "blocking": _ids("blocking"),
        "slug": str(bean.get("slug") or "").strip(),
        "beanPath": str(bean.get("path") or "").strip(),
        "createdAt": str(bean.get("created_at") or "").strip(),
        "updatedAt": str(bean.get("updated_at") or "").strip(),
    }
    for key, value in optional.items():
        if value:
            card[key] = value
    return card


def cards(code_root: Path) -> Result:
    """Every bean as a board card, ready for a deck."""
    res = list_beans(code_root)
    if not res.ok:
        return res
    return Result(True, value=[to_card(b) for b in res.value])


def open_beans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows that still represent work: not completed, not scrapped.

    Used by the vault mirror and by any caller reporting a count. Sorted by
    lane order then id, so a regenerated file is diffable and a run that
    changes nothing writes nothing.
    """
    order = {s: i for i, s in enumerate(STATUSES)}
    live = [r for r in rows
            if str(r.get("status") or "").lower() not in ("completed", "scrapped")]
    return sorted(live, key=lambda r: (order.get(str(r.get("status") or "").lower(), 99),
                                       str(r.get("id") or "")))


def unreachable_reason(code_root: Optional[Path]) -> str:
    """Why this Beans-owned repo cannot be driven right now, or ''.

    The one place the refuse-and-report message is worded, so `board`, `status`
    and `capture-task` all say the same thing about the same condition.
    """
    if not owns(code_root):
        return ""
    if not available():
        return (f"this repo is Beans-owned (`tracker: beans`) but `{BINARY}` is not "
                f"on PATH on this machine. Install Beans, or set `tracker: vault` "
                f"in .claude/adjudant to hand the work items back to the vault.")
    probe = _run(Path(code_root), ["list", "--json"])
    if not probe.ok:
        return (f"this repo is Beans-owned but `{BINARY}` cannot read it: "
                f"{probe.reason}")
    return ""


# ============================================================
# The vault mirror
# ============================================================
#
# A generated, read-only index of open beans under the vault project, so dream
# can still reason about the work and the vault is not blind to it. On a
# machine without the CLI it is the only record of the work the vault can see,
# which is its job rather than a convenience.
#
# Contents are fixed so the file is diffable and a regeneration that changes
# nothing writes nothing. No bodies, no timestamps, no etags: etags are machine
# state and would churn the file on every run without telling a reader anything.

MIRROR_NAME = "_beans.md"


def render_mirror(rows: list[dict[str, Any]], today: str) -> str:
    """The `_beans.md` text for these rows. Pure: no clock, no disk."""
    live = open_beans(rows)
    lines = [
        "---",
        "type: note",
        f"created: {today}",
        f"updated: {today}",
        "---",
        "",
        "# Open beans",
        "",
        "Generated by `/adjudant status` from `beans list --json`. Do not edit:",
        "the next run overwrites it. Beans owns these items, not the vault.",
        "",
        f"{len(live)} open of {len(rows)} total.",
        "",
    ]
    if not live:
        lines.append("Nothing open.")
        lines.append("")
        return "\n".join(lines)
    for row in live:
        bid = str(row.get("id") or "")
        status = str(row.get("status") or "")
        title = str(row.get("title") or "")
        priority = str(row.get("priority") or "").lower()
        suffix = f" · {priority}" if priority and priority != _UNREMARKABLE_PRIORITY else ""
        lines.append(f"- {bid} · {status} · {title}{suffix}")
    lines.append("")
    return "\n".join(lines)


def write_mirror(project_dir: Path, rows: list[dict[str, Any]], today: str) -> bool:
    """Write `_beans.md` into the vault project. True when it changed.

    Atomic and locked, because every new vault write path in this design uses
    both. Returns False when the content is identical, so a synced vault never
    churns on an unchanged regeneration.
    """
    from _vault_walk import atomic_write_text, file_lock

    path = Path(project_dir) / MIRROR_NAME
    text = render_mirror(rows, today)
    try:
        if path.is_file() and path.read_text() == text:
            return False
    except OSError:
        pass
    try:
        with file_lock(path):
            atomic_write_text(path, text)
    except OSError:
        return False
    return True


# ============================================================
# CLI smoke test — read-only
# ============================================================


def _cli(argv: Optional[list[str]] = None) -> int:  # pragma: no cover - manual aid
    import argparse

    p = argparse.ArgumentParser(prog="_beans.py",
                                description="Report what Beans says about a repo.")
    p.add_argument("--project-dir", default=".", help="the CODE project root")
    args = p.parse_args(argv)
    root = Path(args.project_dir).expanduser().resolve()

    print(f"binary available : {available()}")
    print(f"repo Beans-owned : {owns(root)}")
    reason = unreachable_reason(root)
    if reason:
        print(f"unreachable      : {reason}")
        return 1
    res = list_beans(root)
    if not res.ok:
        print(f"list failed      : {res.reason}")
        return 1
    rows = res.value
    print(f"beans            : {len(rows)} total, {len(open_beans(rows))} open")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
