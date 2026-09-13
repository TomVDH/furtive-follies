#!/usr/bin/env python3
"""Adjudant ORCA engine: Orchestrated Review, Concurrent Audit.

Scaffolds, validates, monitors and closes multi-lane code audits.
Each lane is an independent reviewer session with a self-contained brief.
The chair dispatches, collects findings, and scores held-back controls.

CLI:
    python3 orca.py --run    --project-root PATH [--lanes A,B,C]
    python3 orca.py --seats  --project-root PATH
    python3 orca.py --status --project-root PATH
    python3 orca.py --close  --project-root PATH

All modes print JSON to stdout and human summaries to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


ORCA_ROOT = "_docs/orca"
BRIEFS_DIR = "briefs"
RUNS_DIR = "runs"
CURRENT_LINK = "CURRENT"

DEFAULT_LANES = ("a", "b", "c", "d", "e", "f", "g")

_ORCA_MD = """\
# ORCA — Orchestrated Review, Concurrent Audit

A recurring multi-lane code audit. Each lane is an independent reviewer.
The chair dispatches, collects findings, and scores held-back controls.

See `adjudant/skills/adjudant/reference/orca.md` for the full protocol.
"""

_CHAIR_BRIEF = """\
---
type: doc
created: {today}
updated: {today}
---

# ORCA Chair Brief

## Role

Gate the review. Seed 3-5 held-back controls. Score independent discovery.

## Held-back controls

1. (Seed a known finding here that lanes must discover independently)
2. (Another seeded finding)
3. (A third seeded finding)

## Scoring

Each lane is scored on whether it found its assigned control without being told.
"""

_PROBE_BRIEF = """\
---
type: doc
created: {today}
updated: {today}
---

# ORCA Probe Brief

## Role

State dumps before the review starts. Capture baselines for each lane.

## Deliverables

- [ ] Dependency tree snapshot
- [ ] Test suite results
- [ ] Coverage numbers
- [ ] Build output
"""

_LANE_BRIEF = """\
---
type: doc
created: {today}
updated: {today}
---

# ORCA Lane {lane_upper} Brief

## Scope

{scope}

## Rules of engagement

1. Start in plan mode. Read the codebase before proposing findings.
2. Report findings in the prescribed format (see below).
3. Do not coordinate with other lanes. Independent discovery is the point.

## Finding format

For each finding:
- **File**: path relative to project root
- **Line**: line number or range
- **Severity**: critical / high / medium / low
- **Summary**: one sentence
- **Detail**: what is wrong and why it matters
- **Suggested fix**: concrete, not vague

## Wave sequence

1. Read: scan the scope, build a mental model
2. Probe: run the code, trigger edge cases
3. Report: write findings in the format above
"""

_SEAT_REPORT = """\
---
type: doc
created: {today}
updated: {today}
---

# Seat Report: {seat}

Run: {run_id}

## Findings

(Findings go here)

## Summary

- Findings: 0
- Critical: 0
- High: 0
"""


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_if_absent(p: Path, content: str) -> bool:
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return True


def _read_frontmatter(p: Path) -> dict:
    try:
        text = p.read_text()
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def _lane_scope(lane: str) -> str:
    scopes = {
        "a": "Architecture and module boundaries",
        "b": "Business logic correctness",
        "c": "Configuration and environment handling",
        "d": "Data handling, persistence, migrations",
        "e": "Error handling and edge cases",
        "f": "Frontend and user-facing surfaces",
        "g": "Diagnosability: the 8-dimension rubric (identity, correlation, "
             "error catching, failure loudness, machine trace, human trace, "
             "actionability, retention)",
    }
    return scopes.get(lane, f"Domain-specific review lane {lane.upper()}")


def run_orca(project_root: Path, lanes: tuple[str, ...] = DEFAULT_LANES) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    run_id = today
    orca_dir = project_root / ORCA_ROOT
    briefs_dir = orca_dir / BRIEFS_DIR
    runs_dir = orca_dir / RUNS_DIR
    run_dir = runs_dir / run_id

    created = []

    if _write_if_absent(orca_dir / "ORCA.md", _ORCA_MD):
        created.append("ORCA.md")
    if _write_if_absent(briefs_dir / "orca-chair.md",
                        _CHAIR_BRIEF.format(today=today)):
        created.append("briefs/orca-chair.md")
    if _write_if_absent(briefs_dir / "orca-probe.md",
                        _PROBE_BRIEF.format(today=today)):
        created.append("briefs/orca-probe.md")

    for lane in lanes:
        fname = f"orca-{lane}-review.md"
        content = _LANE_BRIEF.format(
            today=today, lane_upper=lane.upper(),
            scope=_lane_scope(lane))
        if _write_if_absent(briefs_dir / fname, content):
            created.append(f"briefs/{fname}")

    _ensure_dir(run_dir)

    current = runs_dir / CURRENT_LINK
    try:
        if current.is_symlink() or current.exists():
            current.unlink()
        current.symlink_to(run_id)
    except OSError:
        try:
            current.write_text(run_id + "\n")
        except OSError:
            pass

    return {
        "action": "run",
        "run_id": run_id,
        "orca_dir": str(orca_dir),
        "created": created,
        "lanes": list(lanes),
        "briefs": [str(briefs_dir / f"orca-{l}-review.md") for l in lanes],
    }


def seats_orca(project_root: Path) -> dict:
    orca_dir = project_root / ORCA_ROOT
    briefs_dir = orca_dir / BRIEFS_DIR

    if not briefs_dir.is_dir():
        return {"error": f"No briefs directory at {briefs_dir}. Run --run first."}

    seats = []
    missing = []
    for f in sorted(briefs_dir.glob("orca-*.md")):
        name = f.stem
        if name in ("orca-chair", "orca-probe"):
            role = "chair" if "chair" in name else "probe"
        else:
            m = re.match(r"orca-([a-z0-9]+)-", name)
            role = f"lane-{m.group(1)}" if m else name
        seats.append({"seat": role, "brief": str(f), "exists": f.is_file()})
        if not f.is_file():
            missing.append(role)

    result: dict[str, Any] = {"action": "seats", "seats": seats}
    if missing:
        result["missing"] = missing
        result["error"] = f"Missing briefs: {', '.join(missing)}"
    return result


def status_orca(project_root: Path) -> dict:
    orca_dir = project_root / ORCA_ROOT
    runs_dir = orca_dir / RUNS_DIR
    current = runs_dir / CURRENT_LINK

    if not current.exists():
        return {"error": "No active run. Run --run first."}

    if current.is_symlink():
        run_id = os.readlink(str(current))
    else:
        run_id = current.read_text().strip()

    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        return {"error": f"Run directory not found: {run_dir}"}

    reported = []
    outstanding = []
    for f in sorted(run_dir.glob("seat-*.md")):
        seat = f.stem.replace("seat-", "")
        fm = _read_frontmatter(f)
        reported.append({"seat": seat, "file": str(f)})

    briefs_dir = orca_dir / BRIEFS_DIR
    if briefs_dir.is_dir():
        for f in sorted(briefs_dir.glob("orca-*-*.md")):
            m = re.match(r"orca-([a-z0-9]+)-", f.stem)
            if m:
                lane = m.group(1)
                seat_file = run_dir / f"seat-{lane}.md"
                if not seat_file.exists():
                    outstanding.append(lane)

    return {
        "action": "status",
        "run_id": run_id,
        "reported": reported,
        "outstanding": outstanding,
        "complete": len(outstanding) == 0 and len(reported) > 0,
    }


def close_orca(project_root: Path) -> dict:
    orca_dir = project_root / ORCA_ROOT
    runs_dir = orca_dir / RUNS_DIR
    current = runs_dir / CURRENT_LINK

    if not current.exists():
        return {"error": "No active run to close."}

    if current.is_symlink():
        run_id = os.readlink(str(current))
    else:
        run_id = current.read_text().strip()

    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        return {"error": f"Run directory not found: {run_dir}"}

    today = datetime.now().strftime("%Y-%m-%d")
    findings_all = []
    seats_found = []

    for f in sorted(run_dir.glob("seat-*.md")):
        seat = f.stem.replace("seat-", "")
        seats_found.append(seat)
        try:
            text = f.read_text()
        except OSError:
            continue
        findings_count = 0
        for line in text.splitlines():
            if line.strip().startswith("- **File**:") or line.strip().startswith("**File**:"):
                findings_count += 1
        findings_all.append({"seat": seat, "findings_count": findings_count})

    total_findings = sum(s["findings_count"] for s in findings_all)

    chair_report = run_dir / "chair-report.md"
    report_content = f"""\
---
type: doc
created: {today}
updated: {today}
---

# ORCA Chair Report — {run_id}

## Summary

- Seats reported: {len(seats_found)}
- Total findings: {total_findings}

## Per-seat breakdown

"""
    for s in findings_all:
        report_content += f"- **{s['seat']}**: {s['findings_count']} findings\n"

    report_content += """
## Held-back control scoring

(Score each lane on whether it found its assigned held-back control)

## Consolidated findings

(Merge and deduplicate findings from all seats here)
"""

    chair_report.write_text(report_content)

    return {
        "action": "close",
        "run_id": run_id,
        "seats_reported": seats_found,
        "total_findings": total_findings,
        "chair_report": str(chair_report),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="orca.py",
        description="ORCA: Orchestrated Review, Concurrent Audit")
    ap.add_argument("--project-root", default=".",
                    help="Project root directory (default: cwd)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true",
                       help="Scaffold a new ORCA run")
    group.add_argument("--seats", action="store_true",
                       help="Validate and print seat briefs")
    group.add_argument("--status", action="store_true",
                       help="Report current run status")
    group.add_argument("--close", action="store_true",
                       help="Close the current run, consolidate findings")
    ap.add_argument("--lanes", default=None,
                    help="Comma-separated lane letters (default: a-g)")
    args = ap.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    lanes = tuple(args.lanes.split(",")) if args.lanes else DEFAULT_LANES

    if args.run:
        result = run_orca(project_root, lanes)
    elif args.seats:
        result = seats_orca(project_root)
    elif args.status:
        result = status_orca(project_root)
    else:
        result = close_orca(project_root)

    print(json.dumps(result, indent=2))

    action = result.get("action", "")
    if action == "run":
        print(f"[orca] scaffolded run {result.get('run_id')} "
              f"with {len(result.get('lanes', []))} lanes, "
              f"{len(result.get('created', []))} files created",
              file=sys.stderr)
    elif action == "seats":
        n = len(result.get("seats", []))
        m = len(result.get("missing", []))
        print(f"[orca] {n} seats, {m} missing", file=sys.stderr)
    elif action == "status":
        r = len(result.get("reported", []))
        o = len(result.get("outstanding", []))
        print(f"[orca] run {result.get('run_id')}: "
              f"{r} reported, {o} outstanding", file=sys.stderr)
    elif action == "close":
        print(f"[orca] closed run {result.get('run_id')}: "
              f"{result.get('total_findings', 0)} total findings",
              file=sys.stderr)

    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
