#!/usr/bin/env python3
"""Adjudant dream — semantic content/staleness comparator catalog (dream analysis phase).

Scans an adjudant-managed vault project and emits a structured *content*
catalog (JSON) for Claude to JUDGE. Where the deep pass decides
structural facts ("this filename violates §4"), `dream.py` cannot decide
semantics — it surfaces *candidates* (with file · line · excerpt) and
leaves the judgment to Claude. Read-only — never writes.

Catalog (the comparator catalog):
  - staleness_candidates   aged files whose content may be outdated
  - supersession_signals   same-topic decisions, older likely superseded
  - redundancy_clusters    near-duplicate notes/docs (token-set similarity)
  - stale_refs             refs that resolve but point to archived/old targets
  - orphan_questions       aged open-loop markers (TODO/OPEN/TBD/…) never closed
  - unacted_decisions      active decisions whose stated consequence shows no action
  - documentation_gaps     under-documentation (session w/o decision, stubs, brief gaps)
  - dangling_scopes        brief milestones/questions never touched in any session

Every entry carries a `confidence` (0-1) and the catalog is a shortlist, not a
census: the top CATALOG_CAP entries by confidence survive, and a finding a past
report dismissed stays out until the file it names changes.

CLI:
    python3 dream.py --project-dir PATH [--vault-dir PATH] [--out FILE]
                     [--today YYYY-MM-DD] [--stale-days N] [--include-legacy]

This is the analysis phase for `/adjudant dream` (the third cleanup tier):
  - clean        = surface mechanical sweep       (clean.py)
  - clean --deep = structural drift catalog       (clean.py, read-only)
  - dream   = content/knowledge/memory refresh    (this scanner)

See docs/superpowers/2026-05-26-adjudant-tidy-ramasse-log.design.md and
skills/adjudant/reference/dream.md.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from _cost import cost_block, read_threshold, stat_walk
from _vault_walk import (
    VaultFile,
    _wikilink_stem,
    build_vault_index,
    resolve_vault,
    resolve_wikilink,
    smart_project_dir, VaultUnresolvableError,
    resolve_scope,
    scope_rel,
    walk_project,
)


# File types whose prose dream reads (the content layer)
CONTENT_TYPES: frozenset[str] = frozenset({"decision", "note", "session", "doc"})

# Required brief body sections per project_type, for pre-v3 briefs that still
# carry project_type. The v3 brief is one file whose optional sections are
# marked `<!-- when: ... -->`, and it declares no project_type, so a brief
# written by v3 connect matches no key here and reports no section gap.
REQUIRED_BRIEF_SECTIONS: dict[str, list[str]] = {
    "coding": ["INTRO", "TECHNICAL STACK", "CONSTRAINTS", "WORK NOTES", "MILESTONES"],
    "plugin": ["INTRO", "TECHNICAL STACK", "CONSTRAINTS", "WORK NOTES", "RELEASE NOTES"],
    "knowledge": ["INTRO", "SOURCES", "OPEN QUESTIONS", "WORK NOTES"],
    "tinkerage": ["INTRO", "WORK NOTES"],
}

# Brief sections whose bullet items represent declared-but-unstarted work
SCOPE_SECTIONS: tuple[str, ...] = ("MILESTONES", "OPEN QUESTIONS", "SCOPE")

# A session with this many substantive log lines but no decision = a doc gap
DOC_GAP_SESSION_MIN_LINES = 5

# Default age threshold (days) past which content is a staleness candidate
DEFAULT_STALE_DAYS = 180
# Open-loop markers go orphan sooner than prose goes stale
DEFAULT_ORPHAN_QUESTION_DAYS = 90

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(.*))?$")

# Open-loop / unresolved-thread markers. `??` is its own alternate with no
# delimiter/\b requirements: the old trailing \b (only matching before a word
# char) made the marker dead for the common `really??` / bare `??` forms.
OPEN_LOOP_RE = re.compile(
    r"(?:(?:^|[\s(>\-*])("
    r"TODO|FIXME|TBD|OPEN:|UNRESOLVED|open question|to decide|to-do|"
    r"follow[ -]?up|needs decision|still unclear"
    r")\b)|(\?\?)",
    re.IGNORECASE,
)

# Ref target segments that mean "archived / parked"
ARCHIVE_HINT_RE = re.compile(r"(?:^|/)(_legacy|_archive|archive|archived|legacy)(?:/|$|#)", re.IGNORECASE)

# Tokenisation stopwords (title + body overlap)
_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "into", "what", "when",
    "where", "which", "while", "about", "over", "under", "your", "their", "them",
    "then", "than", "have", "has", "had", "are", "was", "were", "will", "would",
    "should", "could", "can", "not", "but", "all", "any", "our", "out", "via",
    "use", "using", "used", "new", "old", "see", "note", "notes", "doc", "docs",
    "decision", "decisions", "session", "sessions", "index", "project", "adjudant",
    # Fillers that tie two unrelated filenames together. "per" alone made
    # `knowledge-base-per-brand-theming` read as superseded by
    # `per-brand-favicon-baked`.
    "per", "off", "one", "two", "its", "own", "now", "way", "yet",
    "per-brand", "non", "pre", "post", "sub", "top", "end",
})


def _parse_date(value: Any) -> Optional[_dt.date]:
    """Parse the first YYYY-MM-DD found in a value (str/date), else None."""
    if isinstance(value, _dt.date):
        return value
    if not isinstance(value, str):
        return None
    m = DATE_RE.search(value)
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _file_date(vf: VaultFile) -> Optional[_dt.date]:
    """Best-known date for a file: updated > date frontmatter > filename prefix."""
    fields = vf.frontmatter.fields
    for key in ("updated", "date"):
        d = _parse_date(fields.get(key))
        if d:
            return d
    stem = vf.rel_path.name[:-3] if vf.rel_path.name.endswith(".md") else vf.rel_path.name
    m = DATE_PREFIX_RE.match(stem)
    if m:
        return _parse_date(m.group(1))
    return None


def _age_days(vf: VaultFile, today: _dt.date) -> Optional[int]:
    d = _file_date(vf)
    if d is None:
        return None
    return (today - d).days


def _first_excerpt(body: str, limit: int = 160) -> str:
    """First non-empty, non-heading-only prose line, truncated."""
    for line in body.split("\n"):
        s = line.strip()
        if s and not s.startswith("#"):
            return s[:limit]
    return ""


def _title_tokens(vf: VaultFile) -> set[str]:
    """Significant tokens from the filename (date prefix stripped)."""
    stem = vf.rel_path.name[:-3] if vf.rel_path.name.endswith(".md") else vf.rel_path.name
    m = DATE_PREFIX_RE.match(stem)
    if m and m.group(2):
        stem = m.group(2)
    elif m and not m.group(2):
        stem = ""
    parts = re.split(r"[-_\s]+", stem.lower())
    return {p for p in parts if len(p) >= 3 and p not in _STOPWORDS}


def _body_tokens(vf: VaultFile) -> set[str]:
    """Significant word-set from the prose body (for redundancy similarity)."""
    words = re.findall(r"[a-z0-9][a-z0-9'-]{2,}", vf.body.lower())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


def _shared_wikilink_targets(a: VaultFile, b: VaultFile) -> list[str]:
    ta = {wl.target for wl in a.wikilinks if wl.target}
    tb = {wl.target for wl in b.wikilinks if wl.target}
    return sorted(ta & tb)


def _all_cue_lines(vf: VaultFile, pattern: re.Pattern) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    in_fenced = False
    for lineno, line in enumerate(vf.body.split("\n"), start=1):
        if line.lstrip().startswith("```"):
            in_fenced = not in_fenced
            continue
        if in_fenced:
            continue
        if pattern.search(line):
            out.append((lineno, line.strip()[:160]))
    return out


def _split_sections(body: str) -> dict[str, str]:
    """Map `## HEADING` / `### HEADING` (upper-cased key) → that section's text.

    Skips fenced code. The level-1 `# Title` is not a section.
    """
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    in_fenced = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_fenced = not in_fenced
            if current is not None:
                sections[current].append(line)
            continue
        if not in_fenced:
            m = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
            if m:
                current = m.group(1).strip().upper()
                sections.setdefault(current, [])
                continue
        if current is not None:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def _substantive_lines(text: str) -> list[str]:
    """Body lines that carry content — excludes blanks, headings, and `>` quotes."""
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">"):
            continue
        out.append(s)
    return out


def _session_link_targets(files: list[VaultFile]) -> set[str]:
    """Every wikilink target (full + basename forms) emitted by session files."""
    targets: set[str] = set()
    for f in files:
        if f.file_type != "session":
            continue
        for wl in f.wikilinks:
            if not wl.target:
                continue
            base = wl.target.replace("\\", "/").rstrip("/").split("/")[-1]
            targets.add(wl.target)
            targets.add(base)
    return targets


# ============================================================
# Comparator detectors
# ============================================================


def detect_staleness(
    files: list[VaultFile], today: _dt.date, *, stale_days: int = DEFAULT_STALE_DAYS
) -> list[dict]:
    """Content-type files whose best-known date is older than the threshold.

    Declared epistemic signals outrank the mtime heuristic (v0.22.0):
    `freshness: timeless` never ages out on the clock, and an expired
    `valid_until` is stale no matter how recently the file was touched.
    """
    out: list[dict] = []
    for f in files:
        if f.file_type not in CONTENT_TYPES:
            continue
        fields = f.frontmatter.fields
        fr = fields.get("freshness")
        if isinstance(fr, str) and fr.strip() == "timeless":
            continue
        declared_expired_days: Optional[int] = None
        vu = fields.get("valid_until")
        if isinstance(vu, str) and vu.strip():
            try:
                vu_date = _dt.datetime.strptime(vu.strip(), "%Y-%m-%d").date()
                if vu_date < today:
                    declared_expired_days = (today - vu_date).days
            except ValueError:
                pass  # malformed declaration is schema drift's finding
        age = _age_days(f, today)
        if declared_expired_days is None and (age is None or age <= stale_days):
            continue
        entry = {
            "file": str(f.rel_path),
            "type": f.file_type,
            "date": str(_file_date(f)),
            "age_days": age if age is not None else declared_expired_days,
            "excerpt_head": _first_excerpt(f.body),
        }
        if declared_expired_days is not None:
            entry["reason"] = "declared validity expired"
        out.append(entry)
    out.sort(key=lambda x: x["age_days"], reverse=True)
    return out



# A link most decisions carry is background, and background ties nothing. On a
# real 93-decision project, 46 of 61 supersession pairs shared no title token
# at all and were held together only by `../brief`, which almost every decision
# links. Ten of the twenty capped slots went to such pairs, at the top
# confidence band. That is the failure of the contradiction detector this
# redesign deleted, which "fired on any two files sharing vocabulary", moved
# one field over.
#
# The threshold is a share of the corpus rather than a fixed count, so a small
# project is not silenced: with four decisions nothing is background, and a
# link two of them carry stays evidence.
_BACKGROUND_LINK_SHARE = 0.25


def _background_links(decisions: list) -> set:
    """Link targets carried by so many decisions that sharing one says nothing."""
    if len(decisions) < 8:
        return set()
    counts: dict = {}
    for f in decisions:
        for target in {wl.target for wl in f.wikilinks if wl.target}:
            counts[target] = counts.get(target, 0) + 1
    ceiling = max(2, int(len(decisions) * _BACKGROUND_LINK_SHARE))
    return {t for t, c in counts.items() if c > ceiling}

def detect_supersession_signals(files: list[VaultFile], today: _dt.date) -> list[dict]:
    """Same-topic decision pairs ordered by date — older likely superseded.

    Mechanical signal only: topical overlap (shared title tokens or shared
    wikilink targets) + date ordering + whether the older file already carries
    a `superseded_by` marker. Claude confirms and writes the marker.
    """
    decisions = [f for f in files if f.file_type == "decision" and _file_date(f)]
    toks = {id(f): _title_tokens(f) for f in decisions}
    common = _background_links(decisions)
    out: list[dict] = []
    for i in range(len(decisions)):
        for j in range(i + 1, len(decisions)):
            a, b = decisions[i], decisions[j]
            shared_tokens = sorted(toks[id(a)] & toks[id(b)])
            shared_links = [t for t in _shared_wikilink_targets(a, b)
                            if t not in common]
            # A shared link SUPPORTS a pair; it can no longer create one.
            # Supersession means one decision replaces another on the same
            # SUBJECT, and two decisions citing a third share a citation, not a
            # subject. Measured on a real 92-decision project: five decisions
            # linked one hub, which alone produced ten pairs, and 46 of 61
            # pairs had no shared vocabulary at all. Frequency filtering could
            # not save it, because the most-linked target appeared in only 5
            # of 92 files: the corpus has no background link, just hubs.
            if len(shared_tokens) < 2:
                continue
            da, db = _file_date(a), _file_date(b)
            if da == db:
                continue
            older, newer = (a, b) if da < db else (b, a)
            older_marked = (
                "superseded_by" in older.frontmatter.fields
                or re.search(r"supersed(?:ed|es)\s+by", older.body, re.IGNORECASE) is not None
            )
            out.append({
                "older": {"file": str(older.rel_path), "date": str(_file_date(older))},
                "newer": {"file": str(newer.rel_path), "date": str(_file_date(newer))},
                "shared_terms": shared_tokens,
                "shared_links": shared_links,
                "older_has_superseded_marker": older_marked,
            })
    # Dangling declared supersession (v0.22.0): a frontmatter superseded_by
    # whose target resolves to no file in the project. Distinguishable shape
    # via kind — the pair entries above carry no kind.
    stems = {f.path.stem for f in files}
    for f in files:
        if f.file_type not in ("decision", "note", "doc"):
            continue
        val = f.frontmatter.fields.get("superseded_by")
        if val is None:
            continue
        target = _wikilink_stem(val)
        if target is not None and target not in stems:
            out.append({"kind": "dangling-pointer",
                        "file": str(f.rel_path), "target": target})
    return out


def detect_redundancy_clusters(
    files: list[VaultFile], today: _dt.date, *, threshold: float = 0.6
) -> list[dict]:
    """Near-duplicate notes/docs via token-set (Jaccard) similarity, unioned."""
    candidates = [
        f for f in files
        if f.file_type in ("note", "doc") and f.rel_path.name != "_index.md"
    ]
    tokens = {id(f): _body_tokens(f) for f in candidates}

    # Union-find over pairs above threshold
    parent: dict[int, int] = {id(f): id(f) for f in candidates}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    pair_sim: dict[tuple[int, int], float] = {}
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            ta, tb = tokens[id(a)], tokens[id(b)]
            if not ta or not tb:
                continue
            inter = len(ta & tb)
            if inter == 0:
                continue
            jac = inter / len(ta | tb)
            if jac >= threshold:
                union(id(a), id(b))
                pair_sim[(id(a), id(b))] = jac

    clusters: dict[int, list[VaultFile]] = defaultdict(list)
    for f in candidates:
        clusters[find(id(f))].append(f)

    out: list[dict] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        member_ids = {id(m) for m in members}
        sims = [s for (x, y), s in pair_sim.items() if x in member_ids and y in member_ids]
        shared = set.intersection(*(tokens[id(m)] for m in members)) if members else set()
        out.append({
            "files": sorted(str(m.rel_path) for m in members),
            "similarity": round(min(sims), 3) if sims else None,
            "shared_terms": sorted(shared)[:15],
        })
    out.sort(key=lambda x: (x["similarity"] or 0.0), reverse=True)
    return out


def detect_stale_refs(
    files: list[VaultFile],
    today: _dt.date,
    vault_index: Optional[set[str]] = None,
    *,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> list[dict]:
    """Refs that point to archived locations or to old dated targets.

    Broken wikilinks stay the deep pass's job — these refs RESOLVE (when a vault
    index is available) but point somewhere stale.
    """
    out: list[dict] = []
    for f in files:
        refs: list[tuple[str, int, str]] = []
        for wl in f.wikilinks:
            refs.append((wl.target, wl.line, "wikilink"))
        for text, path, line in f.markdown_md_links:
            refs.append((path.split("#", 1)[0], line, "markdown"))
        for target, line, kind in refs:
            if not target:
                continue
            reason: Optional[str] = None
            if ARCHIVE_HINT_RE.search(target):
                reason = "points to archived/legacy location"
            else:
                td = _parse_date(target.split("/")[-1])
                if td is not None and (today - td).days > stale_days:
                    reason = f"references dated target {(today - td).days}d old"
            if not reason:
                continue
            resolves = resolve_wikilink(target, vault_index) if vault_index else None
            if vault_index is not None and not resolves:
                continue  # unresolved → the deep pass, not dream
            out.append({
                "file": str(f.rel_path),
                "line": line,
                "ref": target,
                "kind": kind,
                "reason": reason,
                "resolves": resolves,
            })
    return out


def detect_orphan_questions(
    files: list[VaultFile], today: _dt.date, *, orphan_days: int = DEFAULT_ORPHAN_QUESTION_DAYS
) -> list[dict]:
    """Aged open-loop markers (TODO/OPEN/TBD/…) that were never closed."""
    out: list[dict] = []
    for f in files:
        if f.file_type not in CONTENT_TYPES:
            continue
        age = _age_days(f, today)
        if age is not None and age < orphan_days:
            continue  # recent open loops aren't orphans yet
        for line, text in _all_cue_lines(f, OPEN_LOOP_RE):
            out.append({
                "file": str(f.rel_path),
                "line": line,
                "text": text,
                "type": f.file_type,
                "age_days": age,
            })
    return out


# detect_orphan_threads was deleted in v3. It flagged an aged note that no
# wikilink pointed at, and it decided "pointed at" by bare stem: `[[popular]]`
# counted as an inbound link to notes/popular.md, and to every other
# popular.md in the vault. Once links resolve by path that heuristic has no
# honest form, and the question it answered is not one a note has. An orphan
# is an Obsidian graph concept; an agent finds a note by its folder path.


def detect_unacted_decisions(
    files: list[VaultFile], today: _dt.date, *, min_age_days: int = 30
) -> list[dict]:
    """`status: active` decisions with a stated `## Consequence` that have aged
    past the threshold, carrying the count of sessions that link to them.

    Revives the original /dream's "unacted decisions" check. Mechanical signal
    only; Claude judges whether the consequence was actually implemented. An
    inbound session link damps the confidence score rather than excluding the
    decision: adjudant asks you to link decisions from sessions, so treating
    that link as proof of action silenced the audit on almost everything it
    was built to read.
    """
    session_targets = _session_link_targets(files)
    out: list[dict] = []
    for f in files:
        if f.file_type != "decision":
            continue
        if f.frontmatter.fields.get("status") != "active":
            continue
        consequence = _split_sections(f.body).get("CONSEQUENCE", "")
        conseq_lines = _substantive_lines(consequence)
        if not conseq_lines:
            continue  # no stated consequence → nothing to be "unacted"
        age = _age_days(f, today)
        if age is not None and age < min_age_days:
            continue
        # A session link is weak evidence of action, not proof: adjudant tells
        # you to link decisions from sessions, so this test excluded 47 of 55
        # active decisions in the real vault — the only audit of them, defeated
        # by adjudant's own convention. It now lowers the score instead.
        stem = f.rel_path.stem
        rel_no_ext = str(f.rel_path)[:-3] if str(f.rel_path).endswith(".md") else str(f.rel_path)
        refs = sum(1 for key in (stem, rel_no_ext, str(f.rel_path))
                   if key in session_targets)
        out.append({
            "file": str(f.rel_path),
            "date": str(_file_date(f)),
            "age_days": age,
            "consequence_excerpt": conseq_lines[0][:160],
            "inbound_session_refs": refs,
        })
    out.sort(key=lambda x: (x["age_days"] or 0), reverse=True)
    return out


def detect_documentation_gaps(files: list[VaultFile], today: _dt.date) -> list[dict]:
    """Under-documentation, the inverse of staleness. Three kinds:
      - session-without-decision: a session with real work but no decision on its date
      - stub: a note/doc/decision with < 3 substantive body lines
      - brief-missing-sections: a brief missing required sections for its project_type
    """
    gaps: list[dict] = []

    decision_dates = {d for d in (_file_date(f) for f in files if f.file_type == "decision") if d}
    for f in files:
        if f.file_type == "session":
            d = _file_date(f)
            if d and d not in decision_dates and len(_substantive_lines(f.body)) >= DOC_GAP_SESSION_MIN_LINES:
                gaps.append({
                    "file": str(f.rel_path),
                    "kind": "session-without-decision",
                    "detail": "substantial session log but no decision recorded on this date",
                })

    for f in files:
        if f.file_type not in ("note", "doc", "decision"):
            continue
        if f.rel_path.name == "_index.md":
            continue
        # templates/ holds intentionally-skeletal scaffolds — not under-documented content.
        if "templates" in f.rel_path.parts:
            continue
        n = len(_substantive_lines(f.body))
        if n < 3:
            gaps.append({
                "file": str(f.rel_path),
                "kind": "stub",
                "detail": f"only {n} substantive body line(s)",
            })

    for f in files:
        if f.file_type == "project" and f.rel_path == Path("brief.md"):
            ptype = f.frontmatter.fields.get("project_type")
            required = REQUIRED_BRIEF_SECTIONS.get(ptype, []) if isinstance(ptype, str) else []
            present = set(_split_sections(f.body).keys())
            missing = [s for s in required if s not in present]
            if missing:
                gaps.append({
                    "file": str(f.rel_path),
                    "kind": "brief-missing-sections",
                    "detail": "missing: " + ", ".join(missing),
                })

    return gaps


def detect_dangling_scopes(files: list[VaultFile], today: _dt.date) -> list[dict]:
    """Brief items declared under MILESTONES / OPEN QUESTIONS / SCOPE whose key
    terms never appear in any session — planned work that was never touched.

    Revives the original /dream's "dangling scopes" check, adapted to the
    adjudant brief schema (which has no explicit scope-in/out block).
    """
    sessions_text = "\n".join(f.body.lower() for f in files if f.file_type == "session")
    out: list[dict] = []
    for f in files:
        if f.file_type != "project" or f.rel_path != Path("brief.md"):
            continue
        sections = _split_sections(f.body)
        for sec_name in SCOPE_SECTIONS:
            text = sections.get(sec_name)
            if not text:
                continue
            for line in text.split("\n"):
                s = line.strip()
                if not re.match(r"^(?:[-*+]|\d+\.)\s+", s):
                    continue
                if re.match(r"^[-*+]\s+\[[xX]\]", s):
                    continue  # completed checkbox
                item = re.sub(r"^(?:[-*+]|\d+\.)\s+", "", s)
                item = re.sub(r"^\[[ xX]\]\s*", "", item)
                if not item or item.startswith("{"):  # skip template placeholders
                    continue
                tokens = {
                    w for w in re.findall(r"[a-z0-9][a-z0-9'-]{3,}", item.lower())
                    if w not in _STOPWORDS
                }
                if not tokens:
                    continue
                if not any(tok in sessions_text for tok in tokens):
                    out.append({
                        "file": str(f.rel_path),
                        "section": sec_name,
                        "item": item[:160],
                        "reason": "declared in brief, never referenced in any session",
                    })
    return out


# ============================================================
# Scoring, the cap, and dismissals
# ============================================================

# A candidate's score is the detector's own confidence, damped by evidence
# that the thing was already handled. The catalog is a shortlist a human
# reads, not a census: the 2026-08-13 run produced 602 candidates and a
# sampled review found zero real, which is what "deliberately generous"
# bought. Twenty is a number someone will actually read.
CATALOG_CAP = 20

# Per-detector base confidence, from the one review with measured outcomes.
_BASE_CONFIDENCE: dict[str, float] = {
    "supersession_signals": 0.8,   # a real, checkable relationship
    "stale_refs": 0.7,             # resolves but points at an archive
    "orphan_questions": 0.6,       # an open marker with a date
    "unacted_decisions": 0.5,      # judgement, but a real question
    "staleness_candidates": 0.4,   # old is not the same as wrong
    "redundancy_clusters": 0.3,    # a documentation convention reads as this
    "documentation_gaps": 0.3,
    "dangling_scopes": 0.3,
}


def _score(category: str, entry: dict) -> float:
    base = _BASE_CONFIDENCE.get(category, 0.3)
    if entry.get("inbound_session_refs"):
        base -= 0.15 * min(entry["inbound_session_refs"], 2)
    if entry.get("older_has_superseded_marker"):
        base -= 0.5          # already marked: this is the convention working
    return max(0.0, min(1.0, round(base, 2)))


# Every category with candidates keeps at least this many slots, before the
# rest of the cap is filled by score. Without it a low-scoring category is
# silently starved to zero by a noisier one, and the report looks like that
# category found nothing rather than like it was outranked.
CATEGORY_FLOOR = 2


def _cap(report: dict, cap: int = CATALOG_CAP) -> dict:
    """Keep the highest-scoring `cap` candidates, without starving a category.

    A pure global sort undid the session-link fix. `_score` damps a
    session-linked unacted decision from 0.5 to 0.35, which ranks it below
    every staleness candidate at 0.4, so on a real-shaped vault all 47 linked
    decisions were cut before the cap was reached and the delivered report
    contained none of them. The detector had stopped excluding them and the
    cap excluded them instead: same outcome for the reader, by a different
    route. Found by an adversarial prover; the repo's own test missed it by
    asserting that the score drops rather than that the entry survives.

    So: each non-empty category takes its best CATEGORY_FLOOR entries first,
    then the remaining budget goes to whatever scores highest. Damping still
    orders within a category, which is what it was for.

    Non-list keys (`scope`, `meta`, `summary`) pass through untouched: the CLI
    and the statusline read them.
    """
    out = {k: ([] if isinstance(v, list) else v) for k, v in report.items()}
    ranked: dict[str, list] = {
        cat: sorted(entries, key=lambda e: e.get("confidence", 0.0), reverse=True)
        for cat, entries in report.items() if isinstance(entries, list) and entries
    }
    taken = 0
    for cat, entries in ranked.items():
        for entry in entries[:CATEGORY_FLOOR]:
            if taken >= cap:
                break
            out[cat].append(entry)
            taken += 1
    rest = [(cat, e) for cat, entries in ranked.items()
            for e in entries[CATEGORY_FLOOR:]]
    rest.sort(key=lambda pair: pair[1].get("confidence", 0.0), reverse=True)
    for cat, entry in rest:
        if taken >= cap:
            break
        out[cat].append(entry)
        taken += 1
    return out


_DISMISS_ROW_RE = re.compile(r"^\|\s*(?P<finding>[^|]+?)\s*\|[^|]*\|[^|]*\|\s*$")

# Both spellings a dream report is written under. reference/dream.md mandates
# `{YYYY-MM-DD}-dream.md`; the state contract records the statusline reading
# either, and status.py already matches both.
_DREAM_REPORT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-dream)?\.md$")


def read_dismissals(project_dir: Path) -> dict[str, _dt.date]:
    """Findings a previous dream report dismissed: file → newest dismissal date.

    Two consecutive reports in the real vault dismissed the same `_archive/`
    naming finding in identical words. A dismissal that does not persist is an
    invitation to waste the same hour again.

    The date comes back with the key so a dismissal can expire: the report
    judged the file as it stood that day, and a file edited since deserves
    re-reading.
    """
    out: dict[str, _dt.date] = {}
    dreams = project_dir / "dreams"
    if not dreams.is_dir():
        return out
    for report in sorted(dreams.iterdir()):
        if not report.is_file():
            continue
        m = _DREAM_REPORT_RE.match(report.name)
        if not m:
            continue
        on = _parse_date(m.group(1))
        if on is None:
            continue
        try:
            text = report.read_text(errors="replace")
        except OSError:
            continue
        if "## Dismissed" not in text:
            continue
        section = text.split("## Dismissed", 1)[1].split("\n## ", 1)[0]
        for line in section.splitlines():
            mrow = _DISMISS_ROW_RE.match(line.strip())
            if not mrow:
                continue
            finding = mrow.group("finding")
            if finding.startswith(("Finding", "---")):
                continue
            parts = finding.split()
            if not parts:
                continue
            key = parts[0]
            if out.get(key) is None or out[key] < on:
                out[key] = on
    return out


def _apply_dismissals(catalog: dict[str, list[dict]], files: list[VaultFile],
                      dismissals: dict[str, _dt.date]) -> int:
    """Drop candidates a past report dismissed, unless the file changed since.

    "Changed" reads the file's own declared date rather than its filesystem
    mtime, the same precedence every other detector here uses: a vault synced
    between machines rewrites mtimes it never edited.

    Entries keyed on something other than a single `file` — redundancy
    clusters and supersession pairs — carry no dismissal key and always pass.
    """
    if not dismissals:
        return 0
    dated = {str(f.rel_path): _file_date(f) for f in files}
    dropped = 0
    for entries in catalog.values():
        kept = []
        for e in entries:
            key = e.get("file")
            on = dismissals.get(key) if isinstance(key, str) else None
            if on is not None:
                changed = dated.get(key)
                if changed is None or changed <= on:
                    dropped += 1
                    continue
            kept.append(e)
        entries[:] = kept
    return dropped


# ============================================================
# Top-level scan
# ============================================================


def run_dream(
    project_dir: Path,
    vault_dir: Optional[Path],
    *,
    today: Optional[_dt.date] = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    orphan_question_days: int = DEFAULT_ORPHAN_QUESTION_DAYS,
    unacted_min_age_days: int = 30,
    include_legacy: bool = False,
    scope: Optional[str] = None,
) -> dict[str, Any]:
    """Run all content/staleness detectors. Returns the full JSON report.

    `scope` is a project-relative folder (`notes`, `notes/deep`): detectors
    then see only that subtree. Filtered AFTER the walk rather than by walking
    the subfolder directly, so every rel_path keeps its project-root form and
    the folder-name logic in the detectors keeps working. The report carries
    the scope so a narrowed run can never present itself as a full one.
    """
    today = today or _dt.date.today()
    files = list(walk_project(project_dir, include_legacy=include_legacy))
    if scope:
        prefix = tuple(Path(scope).parts)
        files = [f for f in files if f.rel_path.parts[:len(prefix)] == prefix]
    slug = _project_slug(files, project_dir)
    proj_type = _project_type(files)

    vault_index: Optional[set[str]] = None
    if vault_dir and vault_dir.is_dir():
        vault_index = build_vault_index(vault_dir)

    staleness = detect_staleness(files, today, stale_days=stale_days)
    supersession = detect_supersession_signals(files, today)
    redundancy = detect_redundancy_clusters(files, today)
    stale_refs = detect_stale_refs(files, today, vault_index, stale_days=stale_days)
    orphan_questions = detect_orphan_questions(files, today, orphan_days=orphan_question_days)
    unacted = detect_unacted_decisions(files, today, min_age_days=unacted_min_age_days)
    doc_gaps = detect_documentation_gaps(files, today)
    dangling = detect_dangling_scopes(files, today)

    catalog: dict[str, list[dict]] = {
        "staleness_candidates": staleness,
        "supersession_signals": supersession,
        "redundancy_clusters": redundancy,
        "stale_refs": stale_refs,
        "orphan_questions": orphan_questions,
        "unacted_decisions": unacted,
        "documentation_gaps": doc_gaps,
        "dangling_scopes": dangling,
    }
    for category, entries in catalog.items():
        for entry in entries:
            entry["confidence"] = _score(category, entry)

    dismissed = _apply_dismissals(catalog, files, read_dismissals(project_dir))
    found_total = sum(len(v) for v in catalog.values()) + dismissed

    report: dict[str, Any] = {
        "scope": scope,
        "meta": {
            "project_dir": str(project_dir),
            "project_slug": slug,
            "project_type": proj_type,
            "vault_dir": str(vault_dir) if vault_dir else None,
            "files_scanned": len(files),
            "today": str(today),
            "include_legacy": include_legacy,
            "thresholds": {
                "stale_days": stale_days,
                "orphan_question_days": orphan_question_days,
                "unacted_min_age_days": unacted_min_age_days,
            },
        },
        "summary": {},
        **catalog,
    }
    report = _cap(report)

    # The summary describes what the report holds, not what the walk saw, so a
    # capped run can never present itself as a census. `candidates_found` and
    # `dismissed` keep the difference visible.
    report["summary"] = {
        "candidates": sum(len(v) for v in report.values() if isinstance(v, list)),
        "candidates_found": found_total,
        "dismissed": dismissed,
        "cap": CATALOG_CAP,
        "staleness": len(report["staleness_candidates"]),
        "supersession": len(report["supersession_signals"]),
        "redundancy_clusters": len(report["redundancy_clusters"]),
        "stale_refs": len(report["stale_refs"]),
        "orphan_questions": len(report["orphan_questions"]),
        "unacted_decisions": len(report["unacted_decisions"]),
        "documentation_gaps": len(report["documentation_gaps"]),
        "dangling_scopes": len(report["dangling_scopes"]),
    }
    return report


def _project_slug(files: list[VaultFile], project_dir: Path) -> Optional[str]:
    for f in files:
        if f.rel_path == Path("brief.md"):
            slug = f.frontmatter.fields.get("slug")
            if isinstance(slug, str) and slug:
                return slug
    return project_dir.name


def _project_type(files: list[VaultFile]) -> Optional[str]:
    for f in files:
        if f.rel_path == Path("brief.md"):
            pt = f.frontmatter.fields.get("project_type")
            if isinstance(pt, str) and pt:
                return pt
    return None


# ============================================================
# CLI
# ============================================================


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dream.py",
        description="Adjudant dream — semantic content/staleness comparator catalog (read-only).",
    )
    parser.add_argument("--project-dir", help="Project root (default: cwd)", default=".")
    parser.add_argument("--vault-dir", help="Vault root (default: resolved from breadcrumb)")
    parser.add_argument("--out", help="Write JSON to FILE instead of stdout")
    parser.add_argument("--today", help="Override 'today' (YYYY-MM-DD) for age math")
    parser.add_argument(
        "--stale-days", type=int, default=DEFAULT_STALE_DAYS,
        help=f"Staleness age threshold in days (default: {DEFAULT_STALE_DAYS})",
    )
    parser.add_argument("--include-legacy", action="store_true", help="Include _legacy/ in scan")
    parser.add_argument("--folder", help="Scope the walk to one project subfolder "
                        "(e.g. 'decisions'); the report header states the scope")
    parser.add_argument("--estimate-only", action="store_true",
                        help="Print only the cost block (stat-only walk) and exit")
    args = parser.parse_args(argv)

    today: Optional[_dt.date] = None
    if args.today:
        today = _parse_date(args.today)
        if today is None:
            print(f"error: --today not a valid YYYY-MM-DD: {args.today}", file=sys.stderr)
            return 1

    try:
        project_dir, vault_hint = smart_project_dir(args.project_dir)
    except VaultUnresolvableError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not project_dir.is_dir():
        if (Path(args.project_dir).expanduser() / ".claude" / "adjudant").is_file():
            print(
                f"error: breadcrumb at {args.project_dir}/.claude/adjudant points to "
                f"vault project {project_dir} which doesn't exist. Run /adjudant connect first.",
                file=sys.stderr,
            )
        else:
            print(f"error: project-dir not found: {project_dir}", file=sys.stderr)
        return 1

    scope: Optional[str] = None
    scope_dir = project_dir
    if args.folder:
        try:
            scope_dir = resolve_scope(project_dir, args.folder)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        scope = scope_rel(project_dir, scope_dir)

    code_root = Path(args.project_dir).expanduser().resolve()
    # The estimate walks what the run will read: the scoped subtree when
    # --folder is given. This is the flag's point — proceed on a slice when
    # the full-vault estimate trips the cost gate.
    files_n, n_bytes = stat_walk(scope_dir)
    cost = cost_block(files_n, n_bytes, read_threshold(code_root))
    if args.estimate_only:
        print(json.dumps({"scope": scope, "cost": cost}, indent=2))
        return 0

    vault_dir: Optional[Path]
    if args.vault_dir:
        vault_dir = Path(args.vault_dir).expanduser().resolve()
    elif vault_hint:
        vault_dir = vault_hint
    else:
        vault_dir = resolve_vault(project_dir)
    if vault_dir and not vault_dir.is_dir():
        print(f"warn: vault-dir not a directory: {vault_dir}", file=sys.stderr)
        vault_dir = None

    report = run_dream(project_dir, vault_dir, today=today, stale_days=args.stale_days,
                       scope=scope)
    report["cost"] = cost

    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).expanduser().write_text(payload + "\n")
        print(f"[dream] wrote {args.out}", file=sys.stderr)
    else:
        print(payload)

    s = report["summary"]
    shown = f"{s['candidates']} candidates"
    if s["candidates_found"] > s["candidates"]:
        shown = (f"{s['candidates']} of {s['candidates_found']} candidates"
                 f" (top {s['cap']} by confidence)")
    if s["dismissed"]:
        shown += f", {s['dismissed']} dismissed earlier"
    print(
        f"[dream] {report['meta']['project_slug']}: "
        f"{report['meta']['files_scanned']} files, "
        f"{shown} "
        f"({s['staleness']} stale, {s['supersession']} supersede, "
        f"{s['redundancy_clusters']} dup-clusters, "
        f"{s['stale_refs']} stale-refs, {s['orphan_questions']} open-loops, "
        f"{s['unacted_decisions']} unacted, "
        f"{s['documentation_gaps']} doc-gaps, {s['dangling_scopes']} dangling)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
