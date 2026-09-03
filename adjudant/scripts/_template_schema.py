#!/usr/bin/env python3
"""The templates ARE the schema.

Before v3 a note's shape was declared three times: FIELD_SCHEMA in
_vault_walk.py, the template file, and a prose section in vault-standards.md,
with validators whose entire job was checking the three agreed. When they
disagreed the vault was already wrong: it reached 45 `type:` values and 110
frontmatter keys under that arrangement, while there were still five kinds.

This module parses the shipped templates at import and produces the schema.
There is no second declaration to drift from. Adding a field is editing one
file; if you find yourself editing Python to add a field, the design has
regressed and test__template_schema.test_deleting_a_field_changes_the_schema
should have caught it.

The comment convention is documented for humans in templates/README.md:

    field: value                  -> required
    field: value  # optional      -> optional, omitted when empty
    field: value  # a | b | c     -> required, value must be one of these
    field: value  # optional: a|b -> optional, value must be one of these

and a body heading is required unless it carries `<!-- when: a, b -->`.

Three more rules from that README, each of which the shipped templates need:

- A file in templates/ is a note template when it opens with `---`. README.md,
  AGENTS.md, CLAUDE.md and GEMINI.md open with prose; they are the
  agent-context files `connect` copies into a code project, they declare no
  kind, and they are skipped.
- Braces mark a span a writer replaces, in a heading as well as a value. So
  doc.md's `## {Section}` is the shape of a section a writer names, not a
  heading every doc must carry, and it is not collected.
- A heading belongs to the template, not to the kind. When two templates
  declare one kind (home.md and index-project.md are both `type: index`) a
  file matches one shape or the other, so the only heading the kind can
  require is one both shapes carry. For index that is none. Requiring the
  union instead would fail every index file ever written.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "skills" / "adjudant" / "templates"

# `key: value  # comment`. The value may be quoted and may itself contain a
# '#', so the comment is only what follows whitespace-hash.
_FIELD_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?P<rest>.*)$")
_COMMENT_RE = re.compile(r"\s+#\s*(?P<comment>.*)$")
_HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_WHEN_RE = re.compile(r"^<!--\s*when:\s*(?P<kinds>[^>]*?)\s*-->\s*$")
_PLACEHOLDER_RE = re.compile(r"^\{[^{}]*\}$")


def is_note_template(path: Path) -> bool:
    """True when this file in templates/ declares a kind.

    The rule README.md states: a note template opens with `---` frontmatter.
    Everything else in the directory is an agent-context file or this README.
    """
    try:
        with path.open(encoding="utf-8") as fh:
            return fh.read(4) == "---\n"
    except OSError:
        return False


def _split_comment(rest: str) -> tuple[str, str]:
    """Return (value, comment) for the text after `key:`."""
    m = _COMMENT_RE.search(rest)
    if not m:
        return rest.strip(), ""
    return rest[:m.start()].strip(), m.group("comment").strip()


# A vocabulary token: one bare word, no spaces. This is what separates
# `# active | superseded` (a rule) from `# bumped on every write` (prose).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _parse_rule(comment: str) -> tuple[bool, tuple[str, ...]]:
    """Return (is_optional, vocabulary) for a trailing comment.

    A comment is a vocabulary when every pipe-separated part is a single bare
    word. It used to require a pipe, so `status: active  # active` read to a
    human as the strictest rule possible and enforced nothing: the kind
    dropped out of STATUS_VALUES_FOR_TYPE entirely, `status: banana` came back
    clean, and the validator stayed green because it only rejected an EMPTY
    vocabulary and a missing one is not empty. Found by an adversarial prover.
    """
    c = comment.strip()
    if not c:
        return False, ()
    optional = c.startswith("optional")
    if optional:
        c = c[len("optional"):].lstrip(": ").strip()
    if not c:
        return optional, ()
    parts = [v.strip() for v in c.split("|")]
    if all(_TOKEN_RE.match(v) for v in parts if v):
        return optional, tuple(v for v in parts if v)
    return optional, ()          # prose, not a rule


def _parse_one(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: no frontmatter")
    try:
        end = text.index("\n---", 4)
    except ValueError:
        raise ValueError(f"{path.name}: unterminated frontmatter")
    front, body = text[4:end], text[end + 4:]

    required: set[str] = set()
    optional: set[str] = set()
    vocab: dict[str, tuple[str, ...]] = {}
    kind = ""

    for line in front.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue                      # a list continuation line
        key = m.group("key")
        value, comment = _split_comment(m.group("rest"))
        is_optional, values = _parse_rule(comment)
        (optional if is_optional else required).add(key)
        if values:
            vocab[key] = values
        if key == "type":
            kind = value

    if not kind:
        raise ValueError(f"{path.name}: no type: value, so it declares no kind")

    headings: list[str] = []
    conditional: dict[str, tuple[str, ...]] = {}
    lines = body.splitlines()
    for i, line in enumerate(lines):
        hm = _HEADING_RE.match(line)
        if not hm:
            continue
        title = hm.group("title")
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        wm = _WHEN_RE.match(nxt.strip())
        if wm:
            conditional[title] = tuple(
                k.strip() for k in wm.group("kinds").split(",") if k.strip())
        elif not _PLACEHOLDER_RE.match(title):
            headings.append(title)

    return kind, {
        "required": frozenset(required),
        "optional": frozenset(optional),
        "vocab": vocab,
        "headings": tuple(headings),
        "conditional": conditional,
    }


def _load(templates_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Parse `templates_dir`, returning (schema, errors).

    A file that cannot be parsed costs you that file and nothing else. This is
    the whole difference between a schema and a single point of failure: an
    adversarial prover showed that when one unparseable file could raise out of
    here, `_vault_walk` became unimportable, the PreToolUse gate hit its
    `except Exception` degrade, and every vault write was allowed, silently,
    with nothing on stderr. One stray scratch file in a directory the design
    invites people to edit turned the write gate off.

    Errors are returned rather than swallowed so `check` and the validator can
    say which file is broken.
    """
    out: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for path in sorted(templates_dir.glob("*.md")):
        if not is_note_template(path):
            continue
        try:
            kind, parsed = _parse_one(path)
        except Exception as e:
            errors.append(str(e))
            continue
        prior = out.get(kind)
        if prior is None:
            out[kind] = parsed
            continue
        if (prior["required"] != parsed["required"]
                or prior["optional"] != parsed["optional"]):
            errors.append(
                f"two templates declare kind '{kind}' with different fields: "
                f"required {sorted(prior['required'])} vs {sorted(parsed['required'])}")
            continue
        also = set(parsed["headings"])
        merged = dict(prior)
        merged["headings"] = tuple(h for h in prior["headings"] if h in also)
        merged["conditional"] = {**prior["conditional"], **parsed["conditional"]}
        merged["vocab"] = {**prior["vocab"], **parsed["vocab"]}
        out[kind] = merged
    return out, errors


def load_schema(templates_dir: Path = TEMPLATES_DIR) -> dict[str, dict[str, Any]]:
    """The parsed schema. Raises only when there is no schema at all.

    An EMPTY result is a broken install, not an empty schema, and it must raise
    rather than return {}. The missing-directory case did not even raise
    before: `glob` on an absent directory yields nothing, FIELD_SCHEMA became
    {}, and `_schema_drift_core` returned None for every file because no type
    was in the schema. The gate then ran "successfully" while enforcing
    nothing, which is the worst of both worlds.
    """
    out, _errors = _load(templates_dir)
    if not out:
        raise ValueError(
            f"no note templates parsed in {templates_dir}: this is a broken "
            "install, not an empty schema")
    return out


def schema_errors(templates_dir: Path = TEMPLATES_DIR) -> list[str]:
    """Files in `templates_dir` that did not parse. Empty when all are good."""
    _out, errors = _load(templates_dir)
    return errors


_SCHEMA = load_schema()

# The view existing callers already expect, so nothing downstream changes.
FIELD_SCHEMA: dict[str, dict[str, frozenset[str]]] = {
    kind: {"required": spec["required"], "optional": spec["optional"]}
    for kind, spec in _SCHEMA.items()
}

# Derived, not declared: the vocabulary is whatever the template's status line
# says it is.
STATUS_VALUES_FOR_TYPE: dict[str, tuple[str, ...]] = {
    kind: spec["vocab"]["status"]
    for kind, spec in _SCHEMA.items()
    if "status" in spec.get("vocab", {})
}

HEADINGS_FOR_TYPE: dict[str, tuple[str, ...]] = {
    kind: spec["headings"] for kind, spec in _SCHEMA.items()
}

# Every vocabulary a template declares, by kind then field. STATUS_VALUES_FOR_TYPE
# above exported one field and dropped the rest, so `verified_by: banana` passed
# the write gate, status and clean while the template plainly said
# `tested | read | docs`. A parsed vocabulary nothing reads is a rule that does
# not exist.
VOCAB_FOR_TYPE: dict[str, dict[str, tuple[str, ...]]] = {
    kind: dict(spec.get("vocab", {})) for kind, spec in _SCHEMA.items()
}
