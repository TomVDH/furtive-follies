#!/usr/bin/env python3
"""Adjudant build profile — the one file that differs between builds.

Adjudant ships twice: the full build in this marketplace, and a reduced public
build in the furtive-follies twin. Until v3 the difference was carried by
forking source files. A token threshold lived in `_cost.py` and again as a
string in `connect.py`, and a PATH probe lived in three places. Every edit to a
shared file had to be made twice, and between edits the trees drifted.

Everything that legitimately differs now lives in `build-profile.json` beside
this module. The Python is identical in both trees; only the data changes.

There is no fallback and there is no default. A missing or malformed profile
raises ProfileError and the caller dies. An inline default would be a second
declaration of the same fact, which is the drift this module ends.

Public API:
    PROFILE_PATH: Path
    ProfileError
    load(path=None) -> dict            # cached per resolved path
    audience() -> str                  # "full" | "public"
    description_suffix() -> str
    cost_warn_tokens() -> int
    capabilities() -> list[dict]
    present_capabilities() -> list[dict]

CLI, called by the SessionStart hook:
    python3 _profile.py --session-banner
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

PROFILE_PATH = Path(__file__).resolve().parent / "build-profile.json"

AUDIENCES = ("full", "public")
REQUIRED_KEYS = ("audience", "description_suffix", "cost_warn_tokens",
                 "capabilities")
CAPABILITY_KEYS = ("id", "probe", "reference", "check_line",
                   "sitrep_line", "session_banner")


class ProfileError(RuntimeError):
    """The build profile is absent, unreadable, or does not declare a build."""


@lru_cache(maxsize=None)
def load(path: Optional[Path] = None) -> dict[str, Any]:
    """Parse and validate the build profile. Cached per resolved path."""
    target = Path(path) if path is not None else PROFILE_PATH
    try:
        raw = target.read_text()
    except OSError as exc:
        raise ProfileError(f"no build profile at {target}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{target} must hold a JSON object")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ProfileError(f"{target} is missing: {', '.join(missing)}")
    if data["audience"] not in AUDIENCES:
        raise ProfileError(
            f"{target}: audience {data['audience']!r} is not one of {AUDIENCES}")
    if not isinstance(data["capabilities"], list):
        raise ProfileError(f"{target}: capabilities must be a list")
    for cap in data["capabilities"]:
        if not isinstance(cap, dict):
            raise ProfileError(f"{target}: each capability must be an object")
        absent = [k for k in CAPABILITY_KEYS if k not in cap]
        if absent:
            raise ProfileError(
                f"{target}: capability {cap.get('id', '?')!r} is missing: "
                f"{', '.join(absent)}")
    return data


def audience() -> str:
    return str(load()["audience"])


def description_suffix() -> str:
    return str(load()["description_suffix"])


def cost_warn_tokens() -> int:
    return int(load()["cost_warn_tokens"])


def capabilities() -> list[dict[str, Any]]:
    return list(load()["capabilities"])


def present_capabilities() -> list[dict[str, Any]]:
    """Declared capabilities whose probe resolves on THIS machine's PATH.

    A probe only. The executable is never run: adjudant reports that an
    environment is there, it does not drive it.
    """
    return [c for c in capabilities() if shutil.which(c["probe"]) is not None]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="_profile.py",
        description="Report this build's profile.")
    p.add_argument("--session-banner", action="store_true",
                   help="print one banner line per present capability")
    p.add_argument("--json", action="store_true",
                   help="print the whole profile as JSON")
    args = p.parse_args(argv)
    try:
        if args.json:
            print(json.dumps(load(), indent=2))
            return 0
        if args.session_banner:
            for cap in present_capabilities():
                print(cap["session_banner"])
            return 0
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
