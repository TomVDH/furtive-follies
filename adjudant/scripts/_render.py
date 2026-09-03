#!/usr/bin/env python3
"""One renderer for every mechanical vault write.

Six writers hand-built markdown from string literals, and four carried a
hardcoded fallback copy of a template for when the file was missing. Each
fallback was a second declaration, and board_bridge's had drifted far enough
that a captured task carried `code: ""` and `note: ""`, fields no v3 kind has.

A write goes through here or it is a bug. A missing template raises rather
than substituting something plausible: a loud failure is recoverable, a quiet
wrong one is what filled the vault.

Two entry points, because two writers own their body:

    render(kind, fields, body)  -> frontmatter + the template's own body
    frontmatter(kind, fields)   -> the fenced block alone

The handoff mirror mirrors `.remember/`; it does not want the template
body, and it used to declare the shape inline instead. It takes the block
and supplies its own body.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from _template_schema import TEMPLATES_DIR, is_note_template, load_schema

# A trailing guidance comment. The value may itself contain a '#', so the
# comment is only what follows whitespace-hash-space.
_COMMENT_RE = re.compile(r"\s+#\s.*$")
_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")
_TYPE_RE = re.compile(r"^type:\s*(\S+)", re.M)


def _template_for(kind: str) -> Path:
    """The template file declaring `kind`, found by its `type:` value.

    Never by filename: `brief.md` is `project` and `home.md` is `index`. Two
    files may declare one kind (home.md and index-project.md are both `index`)
    and then the first in sorted order wins, which is the shape a mechanical
    writer gets. A writer that needs the other shape writes its own body.
    """
    for path in sorted(TEMPLATES_DIR.glob("*.md")):
        if not is_note_template(path):
            continue
        m = _TYPE_RE.search(path.read_text()[:400])
        if m and m.group(1) == kind:
            return path
    raise FileNotFoundError(
        f"no template declares kind '{kind}' in {TEMPLATES_DIR}")


def _front_lines(kind: str, fields: dict) -> list:
    """The frontmatter lines for one note, guidance comments removed.

    An optional field with no value is omitted entirely, never written as
    `""`. A required field with no value keeps the template's own token, so
    the gap is visible to a reader rather than silently blank.
    """
    path = _template_for(kind)
    text = path.read_text()
    schema = load_schema()[kind]

    end = text.index("\n---", 4)
    out = []
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        if key == "type":
            out.append(f"type: {kind}")
            continue
        value = str(fields.get(key, "")).strip()
        if not value and key in schema["optional"]:
            continue                       # omit, never write an empty string
        if not value:
            value = _COMMENT_RE.sub("", m.group(2)).strip()
        out.append(f"{key}: {value}")
    return out


def frontmatter(kind: str, fields: dict) -> str:
    """The fenced frontmatter block for `kind`, fences and trailing newline
    included, for a writer that supplies its own body."""
    return "---\n" + "\n".join(_front_lines(kind, fields)) + "\n---\n"


def render(kind: str, fields: dict,
           body: Optional[dict] = None) -> str:
    """Render a note of `kind`, filling `fields` and substituting `body`.

    `body` maps a placeholder's inner text to its replacement, so
    `{"What was decided": "Bucket-A tags go"}` turns the template's
    `# {What was decided}` into `# Bucket-A tags go`. A placeholder left
    unfilled stays as `{Its Name}`, so a human editing the file afterwards
    can see what belongs there.
    """
    path = _template_for(kind)
    text = path.read_text()
    end = text.index("\n---", 4)

    rendered_body = text[end + 4:]
    for placeholder, replacement in (body or {}).items():
        rendered_body = rendered_body.replace("{" + placeholder + "}", replacement)

    return "---\n" + "\n".join(_front_lines(kind, fields)) + "\n---" + rendered_body
