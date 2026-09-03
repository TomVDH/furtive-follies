#!/usr/bin/env python3
"""Bump a marketplace plugin's version across all lockstep files at once.

The `version-consistency` validator (adjudant/scripts/validate.py) requires a
plugin's version to match across up to four files, and a repo may add a
marketplace-parity guard on top. Keeping them in sync by hand is error-prone —
this writes all of them atomically.

Files updated (only those that exist for the plugin):
  1. <plugin>/.claude-plugin/plugin.json          → "version"
  2. <plugin>/scripts/command-metadata.json       → "version"
  3. <plugin>/skills/<plugin>/SKILL.md            → frontmatter `version:`
  4. .claude-plugin/marketplace.json              → plugins[name==plugin].version

Usage:
    python3 scripts/bump_plugin_version.py <plugin> <X.Y.Z>

Idempotent. Exits 0 on success (including no-op when already at the target),
1 on a bad version string or unknown plugin. Stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.\-]+)?$")


def _set_json_version(path: Path, version: str) -> bool:
    """Set top-level `version` in a JSON file, preserving 2-space indent. Returns
    True if changed, False if absent/unchanged."""
    if not path.is_file():
        return False
    data = json.loads(path.read_text())
    if data.get("version") == version:
        return False
    data["version"] = version
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def _set_skill_version(path: Path, version: str) -> bool:
    """Replace the `version:` line inside SKILL.md's frontmatter BLOCK only
    (a body line starting with `version:` must not be touched). Returns True
    if changed; False when absent/unchanged/no frontmatter version."""
    if not path.is_file():
        return False
    text = path.read_text()
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return False
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        return False
    for i in range(1, close):
        m = re.match(r"^version:\s*(\S+)\s*$", lines[i])
        if m:
            if m.group(1) == version:
                return False
            lines[i] = f"version: {version}"
            path.write_text("\n".join(lines))
            return True
    return False


def _set_marketplace_version(path: Path, plugin: str, version: str) -> bool:
    """Set the plugin's entry version in marketplace.json. Returns True if changed.
    Raises KeyError if the plugin isn't listed."""
    if not path.is_file():
        return False
    data = json.loads(path.read_text())
    entry = next((p for p in data.get("plugins", []) if p.get("name") == plugin), None)
    if entry is None:
        raise KeyError(f"plugin {plugin!r} not found in {path}")
    if entry.get("version") == version:
        return False
    entry["version"] = version
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def bump(plugin: str, version: str, root: Path = ROOT) -> list[str]:
    """Apply the version to every lockstep file that exists. Returns the list of
    changed file paths (relative to root)."""
    if not SEMVER_RE.match(version):
        raise ValueError(f"not a semver version: {version!r}")
    plugin_dir = root / plugin
    if not plugin_dir.is_dir():
        raise KeyError(f"no plugin directory: {plugin_dir}")

    # Validate the marketplace entry BEFORE any write — otherwise a KeyError
    # here would leave the plugin files bumped but the marketplace stale
    # (partial lockstep), defeating the point of this script.
    mk_path = root / ".claude-plugin" / "marketplace.json"
    if mk_path.is_file():
        entries = json.loads(mk_path.read_text()).get("plugins", [])
        if not any(p.get("name") == plugin for p in entries):
            raise KeyError(f"plugin {plugin!r} not found in {mk_path}")

    changed: list[str] = []
    targets = [
        (plugin_dir / ".claude-plugin" / "plugin.json", _set_json_version),
        (plugin_dir / "scripts" / "command-metadata.json", _set_json_version),
    ]
    # Every skill's SKILL.md with a frontmatter `version:` — not just the one
    # named after the plugin (cabinet-of-imd's skill dir is `crew-roster`).
    targets.extend(
        (skill_md, _set_skill_version)
        for skill_md in sorted((plugin_dir / "skills").glob("*/SKILL.md"))
    )
    for path, fn in targets:
        if fn(path, version):
            changed.append(str(path.relative_to(root)))

    mk = root / ".claude-plugin" / "marketplace.json"
    if _set_marketplace_version(mk, plugin, version):
        changed.append(str(mk.relative_to(root)))

    return changed


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("usage: bump_plugin_version.py <plugin> <X.Y.Z>", file=sys.stderr)
        return 1
    plugin, version = argv
    try:
        changed = bump(plugin, version)
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if changed:
        print(f"{plugin} → {version}:")
        for c in changed:
            print(f"  updated {c}")
    else:
        print(f"{plugin} already at {version} (no changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
