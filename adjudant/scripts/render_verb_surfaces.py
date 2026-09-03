#!/usr/bin/env python3
"""Render every verb-derived doc surface from scripts/command-metadata.json.

Ten places used to spell out the verb list by hand: SKILL.md's frontmatter
description and argument-hint, its verb-count sentence, its router table, its
cost-gate weight bullets and its content-authoring list; the README's heading
and verb table; plugin.json's description; and the marketplace entry's
description. Twice over, because adjudant ships in two repositories. The
`verb-surface-parity` validator existed only to notice when they disagreed,
and the marketplace's own AGENTS.md still said eleven verbs when there were
thirteen.

They are rendered from one file now. `build-profile.json` says which audience
this build serves, so the same renderer produces the full build's verbs and the
public build's subset.

Usage:
    python3 render_verb_surfaces.py            # write the surfaces
    python3 render_verb_surfaces.py --check    # exit 1 if any surface is stale

Stdlib only. Idempotent: a second run reports nothing changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import _profile

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Index to word. validate.py imports this and inverts it, so the language table
# lives once.
NUMBER_WORDS: tuple[str, ...] = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
)

ROUTER_TAIL = ("| _(internals)_ | `reference/internals.md` | Not a verb. Hook "
               "wiring, verb-to-helper map, environment probes. Load only when "
               "the question is about adjudant's own machinery |")

SKILL_SUMMARY = ("Vault editor/writer and project initializer. One skill, one "
                 "command, {count} verbs.")

SKILL_DESCRIPTION = (
    "Operate an Obsidian vault from a code project. `/adjudant {{{pipes}}}` — "
    "{clauses}. Also fires whenever decisions, sessions, or notes are written "
    "into a linked vault.")

PLUGIN_DESCRIPTION = (
    "Operate an Obsidian vault from your code project. One command, /adjudant, "
    "with {count} verbs: {clauses}. Schema-locked vault writes, cost-gated "
    "heavy verbs, and ambient hooks that keep session notes, handoffs, and the "
    "board current. Stdlib-only Python helpers, no build step.{suffix}")

WEIGHT_BULLETS = (
    "- **Heavy verbs** ({heavy}): run the backing helper with `--estimate-only` "
    "FIRST. If `cost.warn` is true, stop, show the numbers, and ask the user to "
    "proceed, scope down, or abort. Proceed only on explicit confirmation. If "
    "`warn` is false, run normally and include the estimate as one line.\n"
    "- **Medium verbs** ({medium}): no pre-flight. The helper's JSON carries a "
    "`cost` block; render it as one line.\n"
    "- **Light verbs** ({light}): no estimate; the static weight badge is enough."
)


class SurfaceError(RuntimeError):
    """A surface is missing its markers, or its shape is not what we render."""


def _audience(plugin_root: Path = PLUGIN_ROOT) -> str:
    """The audience of the tree being rendered, not of the tree we run from.

    `--plugin-root` can point at the other build. Reading this module's own
    profile there would write the full build's verb list into the public
    build's README, and a wrong doc is only ever found by a reader.
    """
    return str(_profile.load(plugin_root / "scripts" / "build-profile.json")["audience"])


def load_metadata(plugin_root: Path = PLUGIN_ROOT) -> dict[str, Any]:
    path = plugin_root / "scripts" / "command-metadata.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SurfaceError(f"{path}: {exc}") from exc


def _wanted(entry: dict[str, Any], audience: str) -> bool:
    """An entry ships in this build. `all` ships everywhere; `full` only in the
    full build. An entry with no audience is a metadata bug, not a default."""
    declared = entry.get("audience")
    if declared not in ("all", "full"):
        raise SurfaceError(
            f"{entry.get('name') or entry.get('path')}: audience must be "
            f"'all' or 'full', got {declared!r}")
    return declared == "all" or audience == "full"


def verbs_for(meta: dict[str, Any], audience: str) -> list[dict[str, Any]]:
    return [v for v in meta["verbs"] if _wanted(v, audience)]


def content_refs_for(meta: dict[str, Any], audience: str) -> list[dict[str, Any]]:
    return [c for c in meta["content_references"] if _wanted(c, audience)]


def full_only_paths(meta: dict[str, Any]) -> set[str]:
    """Plugin-relative paths a public build must not carry, derived from data.

    Task 8's generator uses this as its deletion allowlist: a file it cannot
    trace back to a full-only verb, a full-only content reference, or a
    capability this build declares is never deleted.
    """
    out: set[str] = set()
    for verb in meta["verbs"]:
        if verb.get("audience") == "full":
            out.update(verb.get("files", []))
    for ref in meta["content_references"]:
        if ref.get("audience") == "full":
            out.add(f"skills/adjudant/{ref['path']}")
    for cap in _profile.capabilities():
        out.add(f"skills/adjudant/{cap['reference']}")
    return out


def _escape_pipes(text: str) -> str:
    """A raw `|` closes a markdown cell. The hint `[vault|repo|all]` is the
    common case, and forgetting it is how the projects index grew rows with
    the wrong column count."""
    return text.replace("|", "\\|")


def _clauses(verbs: list[dict[str, Any]]) -> str:
    return "; ".join(f"{v['name']} {v['blurb']}" for v in verbs)


def _pipes(verbs: list[dict[str, Any]]) -> str:
    return "|".join(v["name"] for v in verbs)


def _count_word(verbs: list[dict[str, Any]]) -> str:
    n = len(verbs)
    if n >= len(NUMBER_WORDS):
        raise SurfaceError(f"{n} verbs is past the spelled-out range")
    return NUMBER_WORDS[n]


def render_router(verbs: list[dict[str, Any]]) -> str:
    rows = ["| Verb | Loads | Purpose |", "|---|---|---|"]
    rows += [f"| `{v['name']}` | `{v['reference']}` | {_escape_pipes(v['description'])} |"
             for v in verbs]
    rows.append(ROUTER_TAIL)
    return "\n".join(rows)


def render_content_refs(refs: list[dict[str, Any]]) -> str:
    return "\n".join(f"- `{r['path']}` — {r['label']}" for r in refs)


def render_weights(verbs: list[dict[str, Any]]) -> str:
    def named(weight: str) -> str:
        picked = [f"`{v['name']}`" for v in verbs if v["weight"] == weight]
        return ", ".join(picked) if picked else "none in this build"
    return WEIGHT_BULLETS.format(heavy=named("heavy"), medium=named("medium"),
                                 light=named("light"))


def render_readme_table(verbs: list[dict[str, Any]]) -> str:
    rows = [f"## The {_count_word(verbs)} verbs", "",
            "| Verb | What it does |", "|---|---|"]
    for v in verbs:
        hint = "" if v["argumentHint"] == "(no args)" else " " + v["argumentHint"]
        cmd = _escape_pipes(f"/adjudant {v['name']}{hint}")
        blurb = v["blurb"][0].upper() + v["blurb"][1:]
        rows.append(f"| `{cmd}` | {blurb}. |")
    return "\n".join(rows)


def replace_region(text: str, tag: str, body: str) -> str:
    start, end = f"<!-- {tag}:START -->", f"<!-- {tag}:END -->"
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        raise SurfaceError(f"missing or inverted {tag} markers")
    return text[:i + len(start)] + "\n" + body.rstrip("\n") + "\n" + text[j:]


def set_frontmatter_field(text: str, key: str, value: str) -> str:
    """Replace `key:` inside the frontmatter BLOCK only. Borrowed from
    bump_plugin_version._set_skill_version, which learned the hard way that a
    body line starting with the same key must not be touched."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        raise SurfaceError("SKILL.md has no frontmatter")
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        raise SurfaceError("SKILL.md frontmatter is not closed")
    for i in range(1, close):
        if lines[i].startswith(f"{key}:"):
            lines[i] = f"{key}: {value}"
            return "\n".join(lines)
    raise SurfaceError(f"SKILL.md frontmatter has no {key}:")


def render(plugin_root: Path, audience: str) -> dict[Path, str]:
    """The desired text of every markdown surface. Nothing is written here."""
    meta = load_metadata(plugin_root)
    verbs = verbs_for(meta, audience)
    refs = content_refs_for(meta, audience)
    count = _count_word(verbs)

    skill_path = plugin_root / "skills" / "adjudant" / "SKILL.md"
    skill = skill_path.read_text()
    skill = set_frontmatter_field(skill, "description", SKILL_DESCRIPTION.format(
        pipes=_pipes(verbs), clauses=_clauses(verbs)))
    skill = set_frontmatter_field(skill, "argument-hint",
                                  f'"[{_pipes(verbs)}] [args]"')
    skill = replace_region(skill, "VERBS:SUMMARY", SKILL_SUMMARY.format(count=count))
    skill = replace_region(skill, "VERBS:ROUTER", render_router(verbs))
    skill = replace_region(skill, "VERBS:WEIGHTS", render_weights(verbs))
    skill = replace_region(skill, "VERBS:CONTENT-REFS", render_content_refs(refs))

    readme_path = plugin_root / "README.md"
    readme = replace_region(readme_path.read_text(), "VERBS:TABLE",
                            render_readme_table(verbs))

    return {skill_path: skill, readme_path: readme}


def _set_json_field(path: Path, key: str, value: str) -> bool:
    if not path.is_file():
        return False
    data = json.loads(path.read_text())
    if data.get(key) == value:
        return False
    data[key] = value
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def _set_marketplace_description(path: Path, plugin: str, value: str) -> bool:
    if not path.is_file():
        return False
    data = json.loads(path.read_text())
    entry = next((p for p in data.get("plugins", []) if p.get("name") == plugin), None)
    if entry is None or entry.get("description") == value:
        return False
    entry["description"] = value
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def apply(plugin_root: Path = PLUGIN_ROOT, check: bool = False) -> list[str]:
    """Write every surface. Returns the paths that changed, or would change
    under `check`. `check` writes nothing at all, including the JSON."""
    audience = _audience(plugin_root)
    meta = load_metadata(plugin_root)
    verbs = verbs_for(meta, audience)
    description = PLUGIN_DESCRIPTION.format(
        count=_count_word(verbs), clauses=_clauses(verbs),
        suffix=str(_profile.load(
            plugin_root / "scripts" / "build-profile.json"
        )["description_suffix"]))

    changed: list[str] = []
    for path, text in render(plugin_root, audience).items():
        if path.read_text() != text:
            changed.append(str(path))
            if not check:
                path.write_text(text)

    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    marketplace = plugin_root.parent / ".claude-plugin" / "marketplace.json"
    if check:
        if plugin_json.is_file():
            if json.loads(plugin_json.read_text()).get("description") != description:
                changed.append(str(plugin_json))
        if marketplace.is_file():
            entry = next((p for p in json.loads(marketplace.read_text()).get("plugins", [])
                          if p.get("name") == meta["name"]), None)
            if entry is not None and entry.get("description") != description:
                changed.append(str(marketplace))
    else:
        if _set_json_field(plugin_json, "description", description):
            changed.append(str(plugin_json))
        if _set_marketplace_description(marketplace, meta["name"], description):
            changed.append(str(marketplace))
    return changed


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="render_verb_surfaces.py",
        description="Render the verb-derived doc surfaces from command-metadata.json.")
    p.add_argument("--check", action="store_true",
                   help="report stale surfaces and exit 1; write nothing")
    p.add_argument("--plugin-root", default=str(PLUGIN_ROOT),
                   help="plugin directory (default: this script's plugin)")
    args = p.parse_args(argv)
    try:
        changed = apply(Path(args.plugin_root).expanduser().resolve(), check=args.check)
    except (SurfaceError, _profile.ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not changed:
        print("surfaces are current")
        return 0
    verb = "stale" if args.check else "updated"
    for path in changed:
        print(f"  {verb} {path}")
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
