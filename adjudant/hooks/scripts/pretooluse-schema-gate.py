#!/usr/bin/env python3
"""PreToolUse hook for adjudant: schema gate on vault writes.

Validates the PROPOSED frontmatter of a Write landing under the resolved
vault project, using the same FIELD_SCHEMA detector that `check` reports and
`clean` feature 4 repairs. Catching drift at write time is what lets
vault-standards.md stop restating enforceable detail.

  - BLOCK (exit 2) on missing required fields or a type/node_type conflict.
    PreToolUse exit 2 stops the tool and feeds stderr back to the model, so
    it corrects in the same turn.
  - ALLOW SILENTLY on unknown fields. This used to print a warning, but a
    PreToolUse hook's stderr only reaches anyone on a NON-ZERO exit, so the
    warning was written to nobody. `check` reports unknown fields and `clean`
    strips them, which is where the correction belongs.
  - FAIL OPEN (exit 0) on anything infrastructural. A write must never be
    blocked because a hook had a bad day.

Write-only: an Edit payload carries old_string/new_string, not the resulting
file, so the outcome cannot be judged without simulating the edit. Edits keep
clean as their backstop.
"""

import json
import os
import re
import sys
from pathlib import Path

# Shared primitives live in <plugin>/scripts/. Same bootstrap as the other
# python hooks: a broken or mid-sync module only degrades its own capability.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
except Exception:  # pragma: no cover - defensive
    pass

try:
    from _vault_walk import (find_project_dir, is_safe_slug, resolve_vault,
                             schema_drift_for_text)
    from _template_schema import FIELD_SCHEMA, schema_errors
    _READY = True
except Exception:  # pragma: no cover - degrade: gate disabled, never blocks
    _READY = False

# Imported separately: the voice half degrades on its own. A missing or
# unreadable voice.md leaves BLOCKING_PHRASES empty, which blocks nothing,
# rather than taking the schema gate down with it.
try:
    import _voice
except Exception:  # pragma: no cover - degrade: voice check disabled
    _voice = None

# The gate exists to catch model drift in hand-authored notes. These four are
# not that vector. brief.md is written by connect, _index.md by
# connect and clean's index rebuilder, _handoff.md by sync and precompact.
# _iteration.md is the optional index of an iteration folder whose sibling
# build artefacts carry no frontmatter at all. All four have full FIELD_SCHEMA
# entries, so check still reports them and clean still repairs them after the
# fact; only the write-time block is waived.
_SKIP_NAMES = ("_handoff.md", "_index.md", "_iteration.md", "brief.md")


def read_breadcrumb(project_dir: Path) -> dict:
    """Read `.claude/adjudant` breadcrumb (`key: value` per line, YAML-ish)."""
    breadcrumb = project_dir / ".claude" / "adjudant"
    if not breadcrumb.exists():
        return {}
    info = {}
    for line in breadcrumb.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = ":" if ":" in line else ("=" if "=" in line else None)
        if not sep:
            continue
        k, v = line.split(sep, 1)
        info[k.strip()] = v.strip()
    return info


def _voice_verdict(content: str, rel) -> int:
    """Block a vault write carrying a conversational tic, else allow.

    Surface 2 of the voice contract (reference/voice.md). A note lives for
    years and nothing sweeps its prose afterwards: clean repairs frontmatter and
    structure, never sentences. This hook is the last point where the text can
    still be fixed in the turn that wrote it.

    Deliberately narrower than validator 24. Blocking is expensive - a false
    positive wedges the model mid-write - so only `_voice.BLOCKING_PHRASES`
    qualifies: openers, closers and glazing that have no technical reading at
    all. A banned word like `robust` is worth a commit-time failure, not a
    refused note. Fails open on any import or scan problem, like every other
    infrastructural path in this gate.
    """
    if _voice is None:
        return 0
    try:
        hits = _voice.scan_blocking(content)
    except Exception:
        return 0
    if not hits:
        return 0
    named = ", ".join(repr(h) for h in hits[:4])
    print(f"adjudant voice gate: {rel} carries {named}.", file=sys.stderr)
    print("  Vault prose has no openers, closers or glazing. State the thing "
          "and stop. See reference/voice.md.", file=sys.stderr)
    return 2



_TYPE_RE = re.compile(r"^type:[ \t]*([A-Za-z0-9_-]+)", re.M)


def _declared_type(content: str) -> str:
    """The `type:` value in a proposed file's frontmatter, or ''.

    Deliberately independent of the schema: it must still work when the
    schema is the thing that is broken.
    """
    if not content.startswith("---"):
        return ""
    head = content[:2000]
    m = _TYPE_RE.search(head)
    return m.group(1) if m else ""


def _targets_the_vault(raw: str) -> bool:
    """Is this Write aimed inside the linked vault? Import-free by necessity.

    Only reached when the normal imports have already failed, so it parses the
    breadcrumb by hand rather than calling resolve_vault. Answers False on any
    doubt: refusing a write outside the vault would be worse than missing one
    inside it.
    """
    try:
        payload = json.loads(raw)
        target = ((payload.get("tool_input") or {}).get("file_path")
                  or (payload.get("tool_input") or {}).get("path") or "")
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or ""
        if not target or not project_dir:
            return False
        crumb = Path(project_dir) / ".claude" / "adjudant"
        for line in crumb.read_text().splitlines():
            if line.startswith("vault_path:"):
                # Resolve BOTH sides. On macOS a temp path resolves /var to
                # /private/var, so comparing a resolved target against an
                # unresolved breadcrumb never matches.
                vault = Path(line.split(":", 1)[1].strip()).expanduser().resolve()
                return vault in Path(target).resolve().parents
    except Exception:
        return False
    return False


def main() -> int:
    # Read stdin FIRST: exiting before consuming it EPIPEs the harness writer
    # on multi-MB Write payloads.
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not _READY:
        # The degrade exists so a broken or mid-sync module cannot wedge every
        # Write in the editor. But it must not cover a MISSING SCHEMA: with
        # templates/ gone (a half-synced plugin, or an evicted iCloud path)
        # the gate used to run "successfully" while enforcing nothing at all.
        # Distinguish the two with a filesystem check that needs no import,
        # since the imports are exactly what is broken here.
        try:
            templates = (Path(__file__).resolve().parents[2]
                         / "skills" / "adjudant" / "templates")
            if not templates.is_dir() or not any(templates.glob("*.md")):
                if _targets_the_vault(raw):
                    print("adjudant schema gate: the templates directory is "
                          f"missing or empty at {templates}, so there is no "
                          "schema to judge this write against. Restore the "
                          "plugin files, or unlink the project. Refusing "
                          "rather than letting an unjudged write look clean.",
                          file=sys.stderr)
                    return 2
        except Exception:
            pass
        return 0
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Write":
        return 0
    tool_input = payload.get("tool_input") or {}
    file_path_str = tool_input.get("file_path") or tool_input.get("path")
    content = tool_input.get("content")
    if not file_path_str or not isinstance(content, str):
        return 0

    info = read_breadcrumb(Path(project_dir))
    slug = info.get("slug", "")
    if not slug or not is_safe_slug(slug):
        return 0
    try:
        vault = resolve_vault(Path(project_dir))
        if vault is None or not vault.is_dir():
            return 0
        project_root = find_project_dir(vault, slug)
        if project_root is None:
            return 0
        rel = Path(file_path_str).resolve().relative_to(project_root.resolve())
    except Exception:
        return 0
    # `_legacy/` is non-conformant by design and every other component exempts
    # it: walk_project drops it from the walk unless include_legacy is passed,
    # and _cost adds it to its own skip set. Matching walk_project,
    # the exemption applies at any depth, not just at the project root.
    if (not rel.parts or rel.name in _SKIP_NAMES
            or rel.parts[0] == "sessions" or "_legacy" in rel.parts):
        return 0

    try:
        drift = schema_drift_for_text(content, str(rel))
    except Exception:
        drift = None

    if not drift:
        # A kind absent from the schema produces no drift, so an unparseable
        # template would silently stop enforcing exactly the kind it defines.
        # Refuse instead: if the rule for this kind cannot be read, the write
        # cannot be judged, and an unjudged write must not pass as a clean one.
        # (A stray file no longer costs the whole schema; this closes the
        # remaining hole, where the broken file IS the kind being written.)
        try:
            declared = _declared_type(content)
            broken = schema_errors() if declared and declared not in FIELD_SCHEMA else []
        except Exception:
            broken = []
        if broken:
            print(f"adjudant schema gate: {rel} declares `type: {declared}`, "
                  "but that kind is missing from the schema because a template "
                  "did not parse:", file=sys.stderr)
            for e in broken:
                print(f"  - {e}", file=sys.stderr)
            print("  Fix the template, then write again. The gate refuses "
                  "rather than let an unjudged write look clean.", file=sys.stderr)
            return 2
        return _voice_verdict(content, rel)

    ftype = drift.get("type")
    hard = []
    if drift.get("missing_required"):
        hard.append(f"missing required field(s): {', '.join(drift['missing_required'])}")
    if drift.get("type_conflict"):
        hard.append("both `type:` and `node_type:` are set; keep `type:` only")
    if hard:
        print(f"adjudant schema gate: {rel} (type: {ftype}) "
              f"does not match the vault schema.", file=sys.stderr)
        for h in hard:
            print(f"  - {h}", file=sys.stderr)
        print("  Fix the frontmatter and write again. "
              "See reference/vault-standards.md.", file=sys.stderr)
        return 2
    # Everything else in `drift` (unknown fields, status values) is clean's and
    # check's territory. Saying so here would go to /dev/null on an exit 0.
    return _voice_verdict(content, rel)


if __name__ == "__main__":
    # Only the deliberate schema block may exit non-zero.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
