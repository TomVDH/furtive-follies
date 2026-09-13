#!/usr/bin/env python3
"""Adjudant orchestrate: activate or deactivate orchestrator mode.

Writes `orchestrator: on` or `orchestrator: off` in the breadcrumb.
SessionStart reads it and injects the orchestrator instruction set.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _read_breadcrumb(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_breadcrumb(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _set_field(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    line = f"{key}: {value}"
    if pattern.search(text):
        return pattern.sub(line, text)
    return text.rstrip("\n") + "\n" + line + "\n"


def activate(project_dir: Path) -> dict:
    bc = project_dir / ".claude" / "adjudant"
    text = _read_breadcrumb(bc)
    if not text:
        return {"error": "no breadcrumb at " + str(bc)}
    new = _set_field(text, "orchestrator", "on")
    _write_breadcrumb(bc, new)
    return {"status": "activated", "breadcrumb": str(bc)}


def deactivate(project_dir: Path) -> dict:
    bc = project_dir / ".claude" / "adjudant"
    text = _read_breadcrumb(bc)
    if not text:
        return {"error": "no breadcrumb at " + str(bc)}
    new = _set_field(text, "orchestrator", "off")
    _write_breadcrumb(bc, new)
    return {"status": "deactivated", "breadcrumb": str(bc)}


def status(project_dir: Path) -> dict:
    bc = project_dir / ".claude" / "adjudant"
    text = _read_breadcrumb(bc)
    m = re.search(r"^orchestrator:\s*(\S+)", text, re.MULTILINE)
    current = m.group(1) if m else "off"
    return {"orchestrator": current, "breadcrumb": str(bc)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Orchestrator mode")
    ap.add_argument("--project-dir", required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--activate", action="store_true")
    group.add_argument("--deactivate", action="store_true")
    group.add_argument("--status", action="store_true")
    args = ap.parse_args()

    import json
    project = Path(args.project_dir)
    if args.activate:
        result = activate(project)
    elif args.deactivate:
        result = deactivate(project)
    else:
        result = status(project)

    print(json.dumps(result, indent=2))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
