#!/usr/bin/env python3
"""Adjudant truth checks — what a file's existence or a date comparison proves.

`check` used to grade shape: 110 frontmatter keys against a schema, producing
99 failures of which 69 came from a folder adjudant does not own. Nobody acted
on any of them, and meanwhile a project's AGENTS.md carried five false
statements, 44 task cards sat open where nobody could see them, and a spec had
been agreed for two months with no card citing it.

Every finding here traces to one of those. Every one is settled mechanically,
in seconds, so the report is safe to run constantly. Reading prose to find what
only comprehension finds is `dream`'s job, and dream is the expensive one.

The output is a read-only report in three bands, ordered by the cost of being
wrong. It never gates anything: a check that refuses a write is a check people
learn to route around.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from _agents_reach import AGENTS_STALE_COMMITS, agents_reach
from _vault_walk import (
    ALIAS_SEP_RE as _ALIAS_SEP_RE,
    FIELD_SCHEMA,
    STATUS_VALUES_FOR_TYPE,
    VaultFile,
    build_vault_index,
    is_checkable_wikilink,
    is_unowned,
    newest_dated_stem,
    parse_frontmatter,
    resolve_wikilink,
    walk_project,
    zone_of,
)

# Ordered by the cost of being wrong. "wrong-now" is a statement the vault
# makes that is false today; "going-stale" is one that is drifting; and
# "worth-a-look" is a judgement call for a person.
BANDS: tuple[str, ...] = ("wrong-now", "going-stale", "worth-a-look")


@dataclass
class Finding:
    band: str
    kind: str
    file: str       # project-relative path, or "" for a project-level finding
    detail: str

    def as_dict(self) -> dict:
        return {"band": self.band, "kind": self.kind,
                "file": self.file, "detail": self.detail}


@dataclass
class _Ctx:
    """Everything a detector may read. Built once, never mutated."""
    project_dir: Path
    slug: str
    vault: Optional[Path]
    code_root: Optional[Path]
    today: date
    files: list           # list[VaultFile]: owned, and not generated
    all_owned: list       # list[VaultFile]: owned, generated pages included
    index: set            # set[str] from build_vault_index
    by_type: dict         # str -> list[VaultFile], built from `files`

    def fields(self, vf: "VaultFile") -> dict:
        return vf.frontmatter.fields

    def rel(self, vf: "VaultFile") -> str:
        return str(vf.rel_path)


def _is_generated(vf: "VaultFile") -> bool:
    """True when another script owns and overwrites this file every run."""
    return bool(vf.frontmatter.fields.get("source"))


def _wikilink_target(value: Any) -> Optional[str]:
    """The full target a frontmatter wikilink value points at, or None.

    `_vault_walk._wikilink_stem` is the older sibling and is no use here: it
    returns the BARE stem, which since v3 resolves against nothing. A path is
    the whole point — `[[demo/specs/spec-018|SPEC-018]]` names one file, and
    `spec-018` names every project's copy of it.

    Both spellings of the field have to read the same. Obsidian's Properties
    editor and a hand-written YAML line produce `spec: [[demo/specs/s-1]]`
    unquoted, which the frontmatter parser reads as a one-item list holding
    `[demo/specs/s-1]`; only the quoted spelling arrives as a plain string.
    Reading the unquoted one literally reported every working link in the
    vault as broken, in the band that costs the most to get wrong.
    """
    if isinstance(value, list):
        # A one-item list is the unquoted `[[…]]` above. A longer one is a
        # genuine YAML list, which this field is not, and an empty one is the
        # template's unfilled `superseded_by:` round-tripped by an editor.
        if len(value) != 1:
            return None
        value = value[0]
    if value is None:
        return None
    target = str(value).strip().strip('"').strip("'").strip()
    target = target.lstrip("[").rstrip("]").strip()
    target = _ALIAS_SEP_RE.split(target, maxsplit=1)[0].strip()
    return target.split("#", 1)[0].strip() or None


# ============================================================
# Band: wrong-now — names something that is not there
# ============================================================


def _check_broken_wikilinks(ctx: _Ctx) -> Iterator[Finding]:
    """733 of 9611 links were broken, at 7.6%.

    Embeds and attachment names are not checkable and never counted. The index
    resolves by path only since v3, so a link that does not say which project
    it means is reported rather than silently matched to an arbitrary file.
    """
    if not ctx.index:
        return
    for vf in ctx.files:
        for wl in vf.wikilinks:
            if not is_checkable_wikilink(wl):
                continue
            if resolve_wikilink(wl.target, ctx.index):
                continue
            yield Finding("wrong-now", "broken-wikilink", ctx.rel(vf),
                          f"line {wl.line}: link to `{wl.target}` resolves to nothing")


def _check_superseded_target_missing(ctx: _Ctx) -> Iterator[Finding]:
    """`superseded_by` is written only when true, and must point at a file."""
    if not ctx.index:
        return
    for vf in ctx.files:
        target = _wikilink_target(ctx.fields(vf).get("superseded_by"))
        if not target:
            continue
        if resolve_wikilink(target, ctx.index):
            continue
        yield Finding("wrong-now", "superseded-target-missing", ctx.rel(vf),
                      f"superseded_by points at {target!r}, which does not exist")


def _check_task_spec_missing(ctx: _Ctx) -> Iterator[Finding]:
    """`spec:` is a wikilink, not a bare code, so this is checkable at all.

    SPEC-012 sat agreed for two months with no card citing it and no way to
    notice; a bare `SPEC-012` string could never have been resolved.
    """
    if not ctx.index:
        return
    for vf in ctx.by_type.get("task", []):
        target = _wikilink_target(ctx.fields(vf).get("spec"))
        if not target:
            continue
        if resolve_wikilink(target, ctx.index):
            continue
        yield Finding("wrong-now", "task-spec-missing", ctx.rel(vf),
                      f"cites spec {target!r}, which was never written")


# The brief's `## Where things are` table. Row one cell is the label, row two
# is the value.
_TABLE_ROW_RE = re.compile(r"^\|([^|]*)\|([^|]*)\|\s*$")


def _is_a_claim_about_this_disk(value: str) -> bool:
    """False when a repo cell says something no stat can settle.

    Three ways it can. `_render.render` leaves an unfilled placeholder as
    `{Its Name}` on purpose, and the brief template ships `{path or url}`, so
    a braced value is a blank waiting to be filled and not a moved repo — the
    unguarded version opened every new project's first report with that lie.
    An elided path (`~/…/Acme - Web`) is a person shortening a real
    one. And a value that is not absolute after `~` expansion is measured
    against whatever directory the command ran from, which made the same
    brief clean from one shell and wrong from the next; `acme/toolkit` is
    a perfectly good answer to "path or url" and names no directory here.
    """
    if value.startswith("{") and value.endswith("}"):
        return False
    if "…" in value or "..." in value:
        return False
    if "://" in value:
        return False                # a URL is not a path we can stat
    return Path(value).expanduser().is_absolute()


def _brief_repo_path(brief_body: str) -> Optional[str]:
    for line in brief_body.split("\n"):
        m = _TABLE_ROW_RE.match(line.rstrip())
        if not m:
            continue
        if m.group(1).strip().lower() != "repo":
            continue
        value = m.group(2).strip().strip("`")
        return value or None
    return None


def _check_brief_repo_missing(ctx: _Ctx) -> Iterator[Finding]:
    """A brief's repo path that no longer resolves on disk."""
    brief = ctx.project_dir / "brief.md"
    try:
        body = brief.read_text(errors="replace")
    except OSError:
        return
    value = _brief_repo_path(body)
    if not value or not _is_a_claim_about_this_disk(value):
        return
    if Path(value).expanduser().exists():
        return
    yield Finding("wrong-now", "brief-repo-missing", "brief.md",
                  f"repo path {value!r} does not resolve on this machine")


# ============================================================
# Band: wrong-now — work nobody can see
# ============================================================

# A spec agreed this long with no card and no verification is intent that
# never became work. SPEC-012 sat at 60+ days.
SPEC_UNBUILT_DAYS = 60

# Archiving is derived from status, never manual: `clean` moves only done and
# dropped cards. Anything else in the archive is work that was hidden rather
# than finished.
_CLOSED_TASK_STATUSES: frozenset[str] = frozenset({"done", "dropped"})

_BUG_HEADING_RE = re.compile(r"^#{2,3}\s+(BUG-\d+)\b", re.MULTILINE)
_BUG_CLOSED_RE = re.compile(r"^\s*status\s*:\s*(closed|fixed|dropped)\s*$",
                            re.IGNORECASE | re.MULTILINE)
_BUG_ID_RE = re.compile(r"\bBUG-\d+\b")
_CONSEQUENCE_RE = re.compile(
    r"^##\s+Consequence\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
_WORK_LINE_RE = re.compile(r"^\s*Work\s*:\s*(.+)$", re.MULTILINE)


def _target_stem(value: Any) -> Optional[str]:
    """The bare stem of a frontmatter wikilink, through `_wikilink_target`.

    `_vault_walk._wikilink_stem` computes the same thing and is not used here
    for one reason: it takes only a string, and the unquoted spelling
    `spec: [[demo/specs/s-1|SPEC-1]]` reaches the parser as a one-item LIST.
    Read literally, a card written that way stops counting as a citation, and
    a spec somebody is actively building gets reported as intent that never
    became work.
    """
    target = _wikilink_target(value)
    if not target:
        return None
    stem = target.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()
    if stem.endswith(".md"):
        stem = stem[:-3]
    return stem or None


def _check_open_card_in_archive(ctx: _Ctx) -> Iterator[Finding]:
    """A card in `tasks/_archive/` that still reads open. The 44."""
    for vf in ctx.by_type.get("task", []):
        parts = vf.rel_path.parts
        if "_archive" not in parts:
            continue
        status = ctx.fields(vf).get("status")
        text = str(status).strip() if status is not None else ""
        if text in _CLOSED_TASK_STATUSES:
            continue
        yield Finding("wrong-now", "open-card-in-archive", ctx.rel(vf),
                      f"status {text or 'unset'!r} inside tasks/_archive/; "
                      "only done and dropped cards belong there")


def _check_bug_entry_uncited(ctx: _Ctx) -> Iterator[Finding]:
    """A bug entry still open with no task card citing it.

    The bug log is one document on purpose: three of its sixteen entries
    turned out to be the same defect class on different surfaces, which only
    became visible once they sat in one list. Splitting it into sixteen files
    would destroy the one thing it is for, so the only mechanism it needs is
    this: what never got picked up.
    """
    logs = [vf for vf in ctx.files if vf.path.stem == "bug-log"]
    if not logs:
        return
    cited: set = set()
    for vf in ctx.by_type.get("task", []):
        # A card cites a bug by naming its id anywhere in its body. The vault
        # runs four number registries at once (BUG-NNN, T<N>, harness numbers,
        # GitHub numbers) under a standing rule never to cite a bare number,
        # so the prefix is what makes this unambiguous.
        cited.update(_BUG_ID_RE.findall(vf.body))
    for log in logs:
        body = log.body
        headings = list(_BUG_HEADING_RE.finditer(body))
        for i, m in enumerate(headings):
            bug_id = m.group(1)
            end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
            section = body[m.end():end]
            if _BUG_CLOSED_RE.search(section):
                continue
            if bug_id in cited:
                continue
            yield Finding("wrong-now", "bug-entry-uncited", ctx.rel(log),
                          f"{bug_id} reads open and no task card cites it")


def _check_spec_agreed_unbuilt(ctx: _Ctx) -> Iterator[Finding]:
    """A spec agreed 60 days, zero cards citing it, never verified.

    How much is built is the status of the cards citing it. No cards and no
    `verified:` means the intent was recorded and the work never started, and
    nothing said so.
    """
    cited: set = set()
    for vf in ctx.by_type.get("task", []):
        stem = _target_stem(ctx.fields(vf).get("spec"))
        if stem:
            cited.add(stem)
    for vf in ctx.by_type.get("spec", []):
        fields = ctx.fields(vf)
        if str(fields.get("status", "")).strip() != "agreed":
            continue
        if fields.get("verified") is not None:
            continue
        if vf.path.stem in cited:
            continue
        agreed_on = _as_date(fields.get("updated")) or _as_date(fields.get("created"))
        if agreed_on is None:
            continue
        age = (ctx.today - agreed_on).days
        if age < SPEC_UNBUILT_DAYS:
            continue
        yield Finding("wrong-now", "spec-agreed-unbuilt", ctx.rel(vf),
                      f"agreed {age} days ago, no card cites it, never verified")


def _check_decision_consequence_uncarded(ctx: _Ctx) -> Iterator[Finding]:
    """A decision whose `## Consequence` names work with no card.

    One axis only: `status:` says whether a decision is in force, and whether
    it was carried out is a card. A `Work:` line with no link is a job that
    exists in prose and nowhere a board can see it.
    """
    for vf in ctx.by_type.get("decision", []):
        m = _CONSEQUENCE_RE.search(vf.body)
        if not m:
            continue
        section = m.group(1)
        for work in _WORK_LINE_RE.finditer(section):
            if "[[" in work.group(1):
                continue
            yield Finding("wrong-now", "decision-consequence-uncarded",
                          ctx.rel(vf),
                          f"Consequence names work with no card: "
                          f"{work.group(1).strip()[:60]!r}")


# ============================================================
# Band: wrong-now — records that disagree
# ============================================================

# `blocked` stays an alias of `review`, and is the only alias truth accepts.
# An alias is a second name for a state, and a second name is how `obsolete`
# got silently refiled as backlog.
_STATUS_ALIASES: dict = {"task": frozenset({"blocked"})}

_DATED_STEM_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_VERSION_STEM_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def _check_superseded_without_target(ctx: _Ctx) -> Iterator[Finding]:
    """`status: superseded` with no `superseded_by`.

    The field is written only when true, so its absence beside a superseded
    status is a record that contradicts itself. Every shape the parser can
    hand back for "unfilled" — absent, empty string, empty list — reads the
    same, and the unquoted `[[…]]` spelling still counts as a target.
    """
    for vf in ctx.files:
        fields = ctx.fields(vf)
        if str(fields.get("status", "")).strip() != "superseded":
            continue
        if _wikilink_target(fields.get("superseded_by")):
            continue
        yield Finding("wrong-now", "superseded-without-target", ctx.rel(vf),
                      "status is superseded and nothing says what replaced it")


def _check_status_off_vocabulary(ctx: _Ctx) -> Iterator[Finding]:
    """A `status:` outside its type's vocabulary. Reported, never coerced."""
    for vf in ctx.files:
        ftype = vf.file_type or ""
        legal = STATUS_VALUES_FOR_TYPE.get(ftype)
        if not legal:
            continue
        raw = ctx.fields(vf).get("status")
        if raw is None:
            continue
        value = str(raw).strip()
        if value in legal or value in _STATUS_ALIASES.get(ftype, frozenset()):
            continue
        yield Finding("wrong-now", "status-off-vocabulary", ctx.rel(vf),
                      f"status {value!r} is not one of "
                      f"{' | '.join(legal)} for a {ftype}")


def _check_created_filename_mismatch(ctx: _Ctx) -> Iterator[Finding]:
    """A `created:` date disagreeing with the date in its own filename.

    Where the filename carries a date, `created:` is derived from it at write
    time, so the two cannot disagree unless one was edited by hand.
    """
    for vf in ctx.files:
        m = _DATED_STEM_PREFIX_RE.match(vf.path.stem)
        if not m:
            continue
        raw = ctx.fields(vf).get("created")
        if raw is None:
            continue
        value = str(raw).strip().strip('"').strip("'")
        if value == m.group(1):
            continue
        yield Finding("wrong-now", "created-filename-mismatch", ctx.rel(vf),
                      f"created: {value} against a filename dated {m.group(1)}")


def _check_version_filename_mismatch(ctx: _Ctx) -> Iterator[Finding]:
    """A release note's `version:` disagreeing with its filename.

    `version:` is derived from the filename and machine-written, by the same
    rule as dates. A leading `v` on either side is not a disagreement.
    """
    for vf in ctx.by_type.get("release", []):
        m = _VERSION_STEM_RE.match(vf.path.stem)
        if not m:
            continue
        raw = ctx.fields(vf).get("version")
        if raw is None:
            continue
        value = str(raw).strip().strip('"').strip("'").lstrip("v")
        if value == m.group(1):
            continue
        yield Finding("wrong-now", "version-filename-mismatch", ctx.rel(vf),
                      f"version: {raw} against a filename naming {m.group(1)}")


# ============================================================
# Band: going-stale — nobody has checked it lately
# ============================================================

# `verified:` says a human confirmed the file against reality. `updated:` only
# says the text changed. Ninety days is the interval past which "someone
# checked" stops meaning anything.
VERIFIED_STALE_DAYS = 90

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def verified_kinds() -> frozenset[str]:
    """Kinds whose template makes `verified` required.

    Derived from FIELD_SCHEMA, which since v3 is parsed out of the template
    files. Listing them here as well would be the second declaration this
    whole design exists to remove: deleting `verified:` from a template must
    change what is checked, with no Python edit.
    """
    return frozenset(
        t for t, schema in FIELD_SCHEMA.items()
        if "verified" in schema.get("required", frozenset()))


def _as_date(value: Any) -> Optional[date]:
    """A frontmatter value as a date, or None when it is not one."""
    text = str(value).strip().strip('"').strip("'")
    if not _ISO_DATE_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _check_verified_stale(ctx: _Ctx) -> Iterator[Finding]:
    """`verified:` older than 90 days."""
    for vf in ctx.files:
        stamped = _as_date(ctx.fields(vf).get("verified"))
        if stamped is None:
            continue
        age = (ctx.today - stamped).days
        if age < VERIFIED_STALE_DAYS:
            continue
        yield Finding("going-stale", "verified-stale", ctx.rel(vf),
                      f"last verified {stamped.isoformat()}, {age} days ago")


def _check_verified_missing(ctx: _Ctx) -> Iterator[Finding]:
    """A kind that must carry `verified:` and does not, or carries junk.

    71 component sidecars in the real vault have none. The generated half of
    each component pair carries `source:` and never reaches this detector.
    """
    required = verified_kinds()
    for vf in ctx.files:
        if (vf.file_type or "") not in required:
            continue
        raw = ctx.fields(vf).get("verified")
        if raw is None:
            yield Finding("going-stale", "verified-missing", ctx.rel(vf),
                          f"a {vf.file_type} with no verified: date; nobody has "
                          "confirmed it against reality")
            continue
        if _as_date(raw) is None:
            yield Finding("going-stale", "verified-missing", ctx.rel(vf),
                          f"verified: {raw!r} is not a YYYY-MM-DD date")


def _check_verified_docs_only(ctx: _Ctx) -> Iterator[Finding]:
    """Pages only ever `verified_by: docs`, never tested.

    tested means someone ran it against the live thing, read means someone
    read the code it describes, docs means someone took a vendor's word for
    it. A bare date throws that difference away.
    """
    for vf in ctx.files:
        value = ctx.fields(vf).get("verified_by")
        if not isinstance(value, str) or value.strip() != "docs":
            continue
        yield Finding("worth-a-look", "verified-docs-only", ctx.rel(vf),
                      "verified_by: docs — a vendor's word, never a live probe")


# ============================================================
# Band: going-stale — went stale quietly
# ============================================================

# A brief this old, while sessions kept landing, describes a project that has
# moved on without it.
BRIEF_STALE_DAYS = 90

# The interval after which a project in active/ is offered a move. This is the
# prompt that makes lifecycle triage happen instead of never happening.
ZONE_DRIFT_DAYS = 30


def _newest_session(ctx: _Ctx) -> Optional[str]:
    return newest_dated_stem(ctx.project_dir / "sessions",
                             not_after=ctx.today.strftime("%Y-%m-%d"))


def _check_brief_stale(ctx: _Ctx) -> Iterator[Finding]:
    """A brief untouched for 90 days while sessions kept landing."""
    newest = _newest_session(ctx)
    if newest is None:
        return
    last = datetime.strptime(newest, "%Y-%m-%d").date()
    if (ctx.today - last).days >= BRIEF_STALE_DAYS:
        return                      # the project is quiet; that is triage's finding
    brief = ctx.project_dir / "brief.md"
    try:
        fm, _body = parse_frontmatter(brief.read_text(errors="replace"))
    except OSError:
        return
    updated = _as_date(fm.fields.get("updated"))
    if updated is None:
        return
    age = (ctx.today - updated).days
    if age < BRIEF_STALE_DAYS:
        return
    yield Finding("going-stale", "brief-stale", "brief.md",
                  f"brief last updated {updated.isoformat()} ({age} days) "
                  f"while sessions kept landing, newest {newest}")


def _check_handoff_behind_session(ctx: _Ctx) -> Iterator[Finding]:
    """A handoff older than the newest session.

    The handoff is written once, at session end. One older than the newest
    session note is describing a session that has since been superseded.
    """
    newest = _newest_session(ctx)
    if newest is None:
        return
    handoff = ctx.project_dir / "_handoff.md"
    try:
        fm, _body = parse_frontmatter(handoff.read_text(errors="replace"))
    except OSError:
        return
    updated = _as_date(fm.fields.get("updated"))
    if updated is None or updated.isoformat() >= newest:
        return
    yield Finding("going-stale", "handoff-behind-session", "_handoff.md",
                  f"handoff dated {updated.isoformat()}, newest session {newest}")


def _check_generated_page_stale(ctx: _Ctx) -> Iterator[Finding]:
    """A generated page older than the script named in its `source:`.

    This is the one detector that looks at generated files, because it is
    about them. A `source:` that names a system rather than a path
    (`confluence`) resolves to nothing and is skipped: it is provenance, not
    a generator.
    """
    for vf in ctx.all_owned:
        raw = vf.frontmatter.fields.get("source")
        if raw is None:
            continue
        value = str(raw).strip().strip('"').strip("'")
        if not value or "://" in value:
            continue
        script: Optional[Path] = None
        for base in (ctx.code_root, ctx.project_dir):
            if base is None:
                continue
            cand = (base / value).expanduser()
            if cand.is_file():
                script = cand
                break
        if script is None:
            continue
        try:
            if vf.path.stat().st_mtime >= script.stat().st_mtime:
                continue
        except OSError:
            continue
        yield Finding("going-stale", "generated-page-stale", str(vf.rel_path),
                      f"older than {value}, the script that writes it")


# ============================================================
# Band: worth-a-look — a project in the wrong folder
# ============================================================


def _check_project_zone_drift(ctx: _Ctx) -> Iterator[Finding]:
    """A project in `active/` with no session for 30 days."""
    if zone_of(ctx.project_dir) != "active":
        return
    newest = _newest_session(ctx)
    if newest is None:
        return
    days = (ctx.today - datetime.strptime(newest, "%Y-%m-%d").date()).days
    if days < ZONE_DRIFT_DAYS:
        return
    yield Finding("worth-a-look", "project-zone-drift", "",
                  f"in active/ with no session for {days} days; "
                  f"`/adjudant status --move {ctx.slug} paused` moves it")


# ============================================================
# The reach outside the vault
# ============================================================


def _check_agents_reach(ctx: _Ctx) -> Iterator[Finding]:
    """AGENTS.md: what it names that is not there, and how long since it moved.

    The one detector that reads a file outside the vault. `file` stays empty,
    as the Finding contract says: AGENTS.md is not a project-relative vault
    path, so it is named in the detail instead.

    Nothing is written. A context file adjudant edits is a context file nobody
    trusts, which is why three writers under three contradictory policies were
    collapsed to one rule: connect provisions once if missing, and adjudant
    never overwrites.
    """
    if ctx.code_root is None:
        return
    reach = agents_reach(ctx.code_root)
    if not reach["present"]:
        return
    for miss in reach["missing"]:
        yield Finding("wrong-now", "agents-missing-path", "",
                      f"AGENTS.md line {miss['line']} names "
                      f"{miss['token']!r}, which is not there")
    n = reach["commits_since_change"]
    if n is not None and n >= AGENTS_STALE_COMMITS:
        changed = reach["last_changed"] or "an unknown date"
        yield Finding("going-stale", "agents-unchanged", "",
                      f"AGENTS.md last changed {changed}, {n} commits ago")


# Tasks 11 to 14 append to this tuple. Order inside a band is the order
# findings are reported in, so keep the most concrete first.
#
# There is deliberately NO naming-convention detector. Two consecutive dream
# reports dismissed the `_archive/` naming finding in identical words, which
# is the tool spending the same hour twice. A convention is either enforced by
# `place()` at write time or it is not enforced, and reporting one nobody
# asked about is how a report becomes something people stop reading.
_DETECTORS: tuple = (
    _check_broken_wikilinks,
    _check_superseded_target_missing,
    _check_task_spec_missing,
    _check_brief_repo_missing,
    _check_agents_reach,
    _check_open_card_in_archive,
    _check_bug_entry_uncited,
    _check_spec_agreed_unbuilt,
    _check_decision_consequence_uncarded,
    _check_superseded_without_target,
    _check_status_off_vocabulary,
    _check_created_filename_mismatch,
    _check_version_filename_mismatch,
    _check_verified_stale,
    _check_verified_missing,
    _check_verified_docs_only,
    _check_brief_stale,
    _check_handoff_behind_session,
    _check_generated_page_stale,
    _check_project_zone_drift,
)


# ============================================================
# Entry point
# ============================================================


def truth_report(project_dir: Path, *, vault: Optional[Path] = None,
                 code_root: Optional[Path] = None,
                 today: Optional[date] = None) -> dict[str, Any]:
    """Every truth finding for one project, banded and ordered. Reads only.

    Files under an unowned folder are excluded outright: adjudant does not own
    `memory/`'s format and cannot fix what it finds there. Generated pages —
    the ones carrying `source:` — are excluded from every detector except the
    one that is about them, because their script rewrites them every run and
    nagging about the output is nagging about the wrong file.
    """
    today = today or date.today()
    owned = [vf for vf in walk_project(project_dir)
             if not is_unowned(vf.rel_path)]
    checkable = [vf for vf in owned if not _is_generated(vf)]
    by_type: dict[str, list] = {}
    for vf in checkable:
        by_type.setdefault(vf.file_type or "", []).append(vf)

    ctx = _Ctx(
        project_dir=project_dir,
        slug=project_dir.name,
        vault=vault,
        code_root=code_root,
        today=today,
        files=checkable,
        all_owned=owned,
        index=build_vault_index(vault) if vault and vault.is_dir() else set(),
        by_type=by_type,
    )

    findings: list[Finding] = []
    for detector in _DETECTORS:
        findings.extend(detector(ctx))

    band_rank = {b: i for i, b in enumerate(BANDS)}
    findings.sort(key=lambda f: (band_rank.get(f.band, len(BANDS)),
                                 f.kind, f.file, f.detail))
    counts = {b: 0 for b in BANDS}
    for f in findings:
        counts[f.band] = counts.get(f.band, 0) + 1
    return {
        "findings": [f.as_dict() for f in findings],
        "counts": counts,
        "checked": len(owned),
    }
