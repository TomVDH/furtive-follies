#!/usr/bin/env python3
"""Report whether field-guide.html still names the verbs adjudant ships.

The guide is one self-contained page carrying six embedded WebP screenshots
and a PNG: 0.9 MB of HTML and a 3.3 MB PDF beside it. Regenerating it
for a one-word change would push megabytes of near-identical binary into
history, and the screenshots are shot by hand. So it is regenerated at a
RELEASE BOUNDARY only, and this script is how you learn the boundary arrived.

It is a reporter, never a gate. It is deliberately absent from
.pre-commit-config.yaml and from CI: a red build on every verb change would
train people to skip the hook that also runs the validators.

Usage:
    python3 scripts/check_field_guide.py         # exit 1 when the guide is behind
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# The guide marks its verb region. Everything named in a <code> inside it is a
# verb the guide teaches.
#
# This replaced a regex for one visual shape, `<div class="verb"><code>x</code>`.
# That asserted the MARKUP, not the fact the checker exists to protect, so a
# redesign that still listed all five verbs correctly reported five missing
# cards. A marker survives a restyle; a class name does not.
VERB_REGION_RE = re.compile(
    r'<!--\s*VERBS:GUIDE:START.*?-->(.*?)<!--\s*VERBS:GUIDE:END\s*-->', re.S)
VERB_NAME_RE = re.compile(r'<code>([a-z][a-z-]*)</code>')
# "The five verbs." — the spelled-out count, wherever the guide states it.
COUNT_RE = re.compile(r'\b(?:the\s+)?([a-z]+)\s+verbs\b', re.I)

NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
)


def baked_verbs(html: str) -> list[str]:
    """Verb names the guide teaches, read from its marked region.

    An unmarked guide returns nothing, and `report` says so plainly rather
    than claiming every verb is missing.
    """
    region = VERB_REGION_RE.search(html)
    if region is None:
        return []
    seen: list[str] = []
    for name in VERB_NAME_RE.findall(region.group(1)):
        if name not in seen:
            seen.append(name)
    return seen


def baked_count_word(html: str) -> Optional[str]:
    m = COUNT_RE.search(html)
    return m.group(1).lower() if m else None


def shipped_verbs(root: Path) -> list[str]:
    meta = json.loads(
        (root / "adjudant" / "scripts" / "command-metadata.json").read_text())
    return [v["name"] for v in meta["verbs"]]


def report(root: Path = REPO_ROOT) -> list[str]:
    """Every disagreement between the guide and the shipped verb list."""
    guide = root / "field-guide.html"
    if not guide.is_file():
        return [f"{guide.name} is missing"]
    html = guide.read_text(errors="replace")
    shipped = shipped_verbs(root)
    if VERB_REGION_RE.search(html) is None:
        return ["the guide carries no VERBS:GUIDE region; the checker cannot "
                "read its verbs. Wrap the verb list in the marker comments."]
    baked = baked_verbs(html)
    lines: list[str] = []
    for name in baked:
        if name not in shipped:
            lines.append(f"the guide still shows a `{name}` card; it is not a verb")
    for name in shipped:
        if name not in baked:
            lines.append(f"the guide has no card for `{name}`")
    word = baked_count_word(html)
    expected = NUMBER_WORDS[len(shipped)] if len(shipped) < len(NUMBER_WORDS) else None
    if word is not None and expected is not None and word != expected:
        lines.append(f"the guide says '{word} verbs'; there are {expected}")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_field_guide.py",
        description="Report whether the field guide is behind the verb list.")
    ap.add_argument("--root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)
    lines = report(Path(args.root).expanduser().resolve())
    if not lines:
        print("field guide is current")
        return 0
    print("field guide is behind; regenerate it at the next release:")
    for line in lines:
        print(f"  {line}")
    print("\nSee RELEASING.md. Both field-guide.html and field-guide.pdf are "
          "regenerated together, at a release boundary only.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
