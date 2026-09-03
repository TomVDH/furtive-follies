# Templates

Each file here is three things at once: the example a writer copies, the schema
`check` enforces, and the documentation of what a kind of note is for. There is
no second declaration anywhere. `_template_schema.py` parses these files at
import time and `_vault_walk.FIELD_SCHEMA` is the result.

Before v3 the same rule was written three times: a Python constant, the
template, and a prose section in `vault-standards.md`, with three validators
whose entire job was checking the three agreed. When they disagreed the vault
was already wrong. One declaration cannot disagree with itself.

## Which files here are templates

A file in this directory is a note template when it opens with `---`
frontmatter. Every other file is skipped by the parser and by every test that
walks this directory: this README, and `AGENTS.md`, `CLAUDE.md` and
`GEMINI.md`, which are the agent-context files `connect` copies into a code
project rather than notes written into a vault.

A template's kind is its `type:` value, never its filename. `brief.md` declares
`type: project`. Two files may declare the same kind, and then that kind has
two legal shapes: `home.md` and `index-project.md` are both `type: index`.

## How a template declares its fields

Frontmatter, with trailing comments carrying the rules:

```yaml
---
type: decision
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
status: active                    # active | superseded | reversed
superseded_by:                    # optional
session:                          # optional
---
```

| Trailing comment | Means |
|---|---|
| none | Required. Present on every file of this kind. |
| `# optional` | Optional. Written only when it has a value. |
| `# a \| b \| c` | Required, and the value must be one of these words. |
| `# optional: a \| b \| c` | Optional, and when present the value must be one of these words. |

What the template itself shows on the right of the colon:

- `type:` is the one fixed value. It is the kind's name and never a placeholder.
- A field with a vocabulary shows one of its own words as the default:
  `status: active`, `verified_by: read`.
- Any other required field shows a placeholder in braces: `created: {YYYY-MM-DD}`.
- An optional field shows no value at all, so the comment is the whole right
  hand side. `superseded_by: ""` and `related: []` are not how an optional
  field is written; they are the pre-v3 habit that put an empty value into 181
  files of the vault this was measured against.

When two files declare the same kind they must declare the same fields. The
parser raises rather than guess which one is right.

## How a template declares its body

Every `##` heading is a required section. The `#` title line is not a section
and is not checked. A heading that only sometimes applies carries a marker on
the line directly below it:

```markdown
## Stack
<!-- when: coding, plugin -->
```

The list after `when:` is comma-separated project types. A heading belongs to
the template, not to the kind: a kind with two templates has two legal shapes,
and a file matches one of them.

Braces mark a placeholder. `{YYYY-MM-DD}` in the frontmatter and
`{One sentence stating the decision.}` in the body are both spans a writer
replaces, and both are what `_render` substitutes when a script writes the note
instead.

`status` reports a kind whose files are missing a required heading, one line
per kind rather than one per file, and it does not read the prose under one.

## Rules

1. **No empty optional fields in a written file.** The template shows an
   optional field so a reader knows it exists; a writer omits it. Today's
   `task.md` ships three empty strings and one empty list.
2. **A heading is a section a reader needs.** No scaffolding. If a section
   would always be empty, the template should not have it.
3. **Frontmatter is the minority of the file.** Count the required fields,
   count the non-blank body lines, and the body wins. Optional fields do not
   count, because rule 1 keeps them out of the written file. Across the pre-v3
   templates frontmatter was 68% of every non-blank content line, `iteration.md`
   was 92%, and 15 of the 18 carried at least as much frontmatter as body.
4. **Adding a field means editing one file.** If you find yourself editing
   Python to add a field, stop: the design has regressed.
