#!/usr/bin/env python3
"""Adjudant token budget: report-only context-cost accounting.

Every reference file and SKILL.md is prose the model pays for on invocation.
This reports what each surface costs, using the repo's own `bytes // 4`
estimator, against declared budgets.

REPORT ONLY, by design. A hard ceiling would turn legitimate documentation
growth into a fight and become the thing people work around; visibility is
enough pressure. Nothing here fails a build.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# Declared budgets, in estimated tokens. Surfaces with no entry are reported
# without a verdict. Lives here rather than in command-metadata.json, which
# is verb metadata and has no natural slot for per-document limits.
BUDGETS: dict[str, int] = {
    "SKILL.md": 2000,
    # 2500, not the 1800 first planned: commit 0a27fb6 raised it after three
    # drafts measured a floor of 2446 with no rules lost. Don't lower this
    # without re-reading that ruling.
    "reference/vault-standards.md": 2500,
    "reference/voice.md": 600,
}


def estimate_tokens(text: str) -> int:
    """The repo's own estimator, shared with _cost.py: 4 bytes per token."""
    return len(text) // 4


def report(skill_root: Path) -> dict[str, Any]:
    """Per-surface token cost for SKILL.md + reference/*.md."""
    surfaces: list[dict[str, Any]] = []
    if not skill_root.is_dir():
        return {"surfaces": [], "total": 0, "over_count": 0}
    paths = []
    skill = skill_root / "SKILL.md"
    if skill.is_file():
        paths.append(skill)
    ref = skill_root / "reference"
    if ref.is_dir():
        paths.extend(sorted(ref.glob("*.md")))
    for p in paths:
        try:
            tokens = estimate_tokens(p.read_text())
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(p.relative_to(skill_root))
        budget: Optional[int] = BUDGETS.get(rel)
        surfaces.append({
            "file": rel,
            "tokens": tokens,
            "budget": budget,
            "over": budget is not None and tokens > budget,
        })
    return {
        "surfaces": surfaces,
        "total": sum(s["tokens"] for s in surfaces),
        "over_count": sum(1 for s in surfaces if s["over"]),
    }


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="token_budget.py",
        description="Adjudant token budget: report-only (never fails).")
    parser.add_argument("--skill-root",
                        default=str(Path(__file__).resolve().parent.parent
                                    / "skills" / "adjudant"),
                        help="Path to skills/adjudant (default: this plugin's)")
    args = parser.parse_args(argv)
    print(json.dumps(report(Path(args.skill_root).expanduser()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
