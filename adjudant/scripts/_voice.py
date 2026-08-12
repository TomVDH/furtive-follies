#!/usr/bin/env python3
"""adjudant's voice contract, in one place.

Three surfaces enforce it and they used to have no shared definition:

  1. repo docs + templates   -> validate.py (validators 24, 33)
  2. content written to the vault -> hooks/scripts/pretooluse-schema-gate.py
  3. rendered CLI output     -> validate.py (validator 34)

The lexicon merges the `no-ai-slop` skill's banned words with adjudant's own.
The SHAPE half of the contract (ordering, step counts, restating state) comes
from the `i-have-adhd` plugin and stays prose in reference/voice.md: it governs
how a response is built, which no regex can see.

WHAT IS DELIBERATELY NOT HERE
- no-ai-slop's "often-empty adverbs" (just, actually, simply, truly, ...). That
  list is explicitly conditional: cut when empty, keep when carrying emphasis
  or real uncertainty. Machine-checking it would fire on correct prose.
- The colon-reveal pattern. Measured against adjudant's own docs it produced 20
  false positives on ordinary labels (`Read-only views:`, `Also:`). It stays a
  judgment rule in voice.md.
Both are judgment. Encoding judgment as a build failure trains people to
silence the build.
"""

from __future__ import annotations

import re
from pathlib import Path

VOICE_MD = (Path(__file__).resolve().parent.parent
            / "skills" / "adjudant" / "reference" / "voice.md")

# Terms that read as slop in general prose but are load-bearing vocabulary
# here. Recorded rather than merely absent, so a later merge cannot quietly
# re-add them. Value is the reason, and a test asserts each is non-empty.
TECHNICAL_EXEMPTIONS: dict[str, str] = {
    "harness": (
        "the Claude Code harness, and the test harness: 7 legitimate hits "
        "across SKILL.md and reference/. no-ai-slop bans it as a marketing "
        "verb ('harness the power of'); adjudant uses it only as a noun."
    ),
}

# adjudant's originals. Order preserved: these were chosen against this vault's
# own prose, and the comments explain the non-obvious ones.
_ADJUDANT_LEXICON: tuple[str, ...] = (
    "forward-thinking",
    "load-bearing",
    "hand-wave",        # figurative
    "hand-wavy",
    "hand-waving",      # figurative
    "leverage",         # as a verb; add inflections as separate entries
    "deep dive",
    "delve",
    "double-click",     # figurative
    "game-changer",
    "cutting-edge",
    "seamless",
    "journey",          # figurative
    "empower",
    "unlock",           # figurative
    "elevate",          # figurative
    "circle back",
    "synergy",
    "at the end of the day",
)

# From the no-ai-slop skill: "Banned outright" plus "Often-empty phrases".
# `harness` is filtered out below via TECHNICAL_EXEMPTIONS.
_SLOP_LEXICON: tuple[str, ...] = (
    "delve", "foster", "leverage", "utilize", "facilitate", "empower",
    "streamline", "robust", "cutting-edge", "paradigm shift", "game changer",
    "this is huge", "this changes everything", "tapestry", "realm", "beacon",
    "multifaceted", "meticulous", "intricate", "paramount", "transformative",
    "elevate", "embark", "supercharge", "harness", "ever-evolving",
    "it's worth noting", "it's important to note", "at the end of the day",
    "when it comes to", "at its core", "in today's world", "in the age of",
    "in the world of", "the reality is", "the truth is", "in terms of",
    "with regard to", "in order to", "going forward", "in this article",
    "let's dive in",
)


def _merge(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """Union, first spelling wins, exemptions dropped, order stable."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for term in group:
            key = term.lower()
            if key in seen or key in TECHNICAL_EXEMPTIONS:
                continue
            seen.add(key)
            out.append(term)
    return tuple(out)


BANNED_LEXICON: tuple[str, ...] = _merge(_ADJUDANT_LEXICON, _SLOP_LEXICON)

# Named no-ai-slop patterns that survived a false-positive run against every
# doc adjudant ships. Anything that scored a single hit on correct prose was
# dropped rather than exempted case by case.
SLOP_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("ing-analysis", re.compile(
        r",\s+(highlighting|underscoring|reflecting|showcasing|"
        r"demonstrating|emphasising|emphasizing)\b", re.I)),
    ("binary-contrast", re.compile(
        r"\b(is|are|was|were|does|do)n[’']?t\s+[^.;!?]{1,40},?\s+"
        r"it[’']?s\b", re.I)),
    ("puffery", re.compile(
        r"\b(stands as a testament|marks a pivotal moment|plays a vital role|"
        r"solidifies its position|underscores its significance)\b", re.I)),
    ("weasel-attribution", re.compile(
        r"\b(experts agree|studies show|industry reports suggest|"
        r"widely regarded as|many argue)\b", re.I)),
    ("recap-ending", re.compile(r"(?m)^(In conclusion|Ultimately|Overall)\b")),
    ("rhetorical-setup", re.compile(
        r"(What if I told you|Think about it:|Plot twist:)", re.I)),
    ("faux-insight", re.compile(
        r"\b(what most people get wrong|nobody tells you|"
        r"the part everyone misses|most people skip)\b", re.I)),
    ("throat-clearing", re.compile(
        r"\b(Here[’']?s the thing|Let me be clear|I[’']?ll be honest|"
        r"The uncomfortable truth)\b", re.I)),
)

_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _parse_voice_bullets() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(glazing, shape) phrase bullets from reference/voice.md.

    These stay in the doc rather than moving here: unlike the lexicon they are
    short, readable, and worth seeing when the contract is read by a human.
    Missing or unreadable voice.md yields empty tuples - the gate then blocks
    nothing, matching its fail-open contract everywhere else.
    """
    groups: dict[str, list[str]] = {"glazing": [], "shape": []}
    current = None
    try:
        text = VOICE_MD.read_text()
    except OSError:
        return (), ()
    for line in text.splitlines():
        if line.startswith("## "):
            h = line[3:].strip().lower()
            current = ("glazing" if h.startswith("glazing")
                       else "shape" if h.startswith("shape phrases") else None)
            continue
        m = re.match(r"^-\s+(.+)$", line.strip())
        if m and current:
            groups[current].append(re.sub(r"\s*\(.*\)\s*$", "", m.group(1)).strip())
    return tuple(groups["glazing"]), tuple(groups["shape"])


GLAZING_PHRASES, SHAPE_PHRASES = _parse_voice_bullets()

# What the runtime gate refuses a vault write over. The bar is higher than a
# validator's: a false positive here wedges the model mid-turn, so this is only
# the conversational tics that have no technical reading at all. A merely
# banned word (`robust`) is worth fixing at commit time, not worth refusing
# a note over.
BLOCKING_PHRASES: tuple[str, ...] = _merge(GLAZING_PHRASES, SHAPE_PHRASES)


def prose_only(text: str) -> str:
    """Drop fenced blocks and inline code spans. Code is syntax, not prose:
    mermaid's `journey` keyword is not the figurative `journey`."""
    out: list[str] = []
    in_fence = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(ln)
    return _INLINE_CODE_RE.sub("", "\n".join(out))


def _term_re(term: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", re.I)


_TERM_CACHE = {t: _term_re(t) for t in BANNED_LEXICON}


def scan(text: str) -> list[tuple[str, str]]:
    """[(kind, matched)] for every voice violation in `text`.

    kind is "lexicon" for a banned term, else the pattern name.
    """
    prose = prose_only(text)
    hits: list[tuple[str, str]] = []
    for term, rx in _TERM_CACHE.items():
        if rx.search(prose):
            hits.append(("lexicon", term))
    for name, rx in SLOP_PATTERNS:
        m = rx.search(prose)
        if m:
            hits.append((name, m.group(0).strip()))
    return hits


def scan_blocking(text: str) -> list[str]:
    """Phrases in `text` that justify refusing a vault write. See
    BLOCKING_PHRASES for why this is narrower than `scan`."""
    prose = prose_only(text)
    return [p for p in BLOCKING_PHRASES if _term_re(p).search(prose)]
