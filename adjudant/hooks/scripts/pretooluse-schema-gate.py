#!/usr/bin/env python3
"""PreToolUse hook for adjudant: schema gate on vault writes.

Validates the PROPOSED frontmatter of a Write landing under the resolved
vault project, using the same FIELD_SCHEMA detector that `check` reports and
`tidy` feature 5 repairs. Catching drift at write time is what lets
vault-standards.md stop restating enforceable detail.

  - BLOCK (exit 2) on missing required fields or a type/node_type conflict.
    PreToolUse exit 2 stops the tool and feeds stderr back to the model, so
    it corrects in the same turn.
  - ALLOW SILENTLY on unknown fields. This used to print a warning, but a
    PreToolUse hook's stderr only reaches anyone on a NON-ZERO exit, so the
    warning was written to nobody. `check` reports unknown fields and `tidy`
    strips them, which is where the correction belongs.
  - FAIL OPEN (exit 0) on anything infrastructural. A write must never be
    blocked because a hook had a bad day.

Write-only: an Edit payload carries old_string/new_string, not the resulting
file, so the outcome cannot be judged without simulating the edit. Edits keep
tidy as their backstop.
"""

import json
import os
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
# not that vector. brief.md is written by connect and port, _index.md by
# connect and tidy's index rebuilder, _handoff.md by sync and precompact.
# _iteration.md is the optional index of an iteration folder whose sibling
# build artefacts carry no frontmatter at all. All four have full FIELD_SCHEMA
# entries, so check still reports them and tidy still repairs them after the
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
    years and nothing sweeps its prose afterwards: tidy repairs frontmatter and
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


def main() -> int:
    # Read stdin FIRST: exiting before consuming it EPIPEs the harness writer
    # on multi-MB Write payloads.
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not _READY:
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
    # and _cost and shelf add it to their own skip sets. Matching walk_project,
    # the exemption applies at any depth, not just at the project root.
    if (not rel.parts or rel.name in _SKIP_NAMES
            or rel.parts[0] == "sessions" or "_legacy" in rel.parts):
        return 0

    try:
        drift = schema_drift_for_text(content, str(rel))
    except Exception:
        drift = None

    if not drift:
        return _voice_verdict(content, rel)

    ftype = drift.get("type")
    hard = []
    if drift.get("missing_required"):
        hard.append(f"missing required field(s): {', '.join(drift['missing_required'])}")
    if drift.get("type_conflict"):
        hard.append("both `type:` and `node_type:` are set; keep `type:` only")
    # Epistemic declarations (v0.22.0) have zero legacy values, so a
    # malformed one is pure model drift - block, unlike status values whose
    # historical synonyms tidy migrates after the fact.
    for e in drift.get("epistemic_invalid", []):
        hard.append(f"`{e['field']}: {e['value']}` - {e['reason']}")
    if hard:
        print(f"adjudant schema gate: {rel} (type: {ftype}) "
              f"does not match the vault schema.", file=sys.stderr)
        for h in hard:
            print(f"  - {h}", file=sys.stderr)
        print("  Fix the frontmatter and write again. "
              "See reference/vault-standards.md.", file=sys.stderr)
        return 2
    # Everything else in `drift` (unknown fields, status values) is tidy's and
    # check's territory. Saying so here would go to /dev/null on an exit 0.
    return _voice_verdict(content, rel)


if __name__ == "__main__":
    # Only the deliberate schema block may exit non-zero.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
