# Vault Standards

Canonical schema, naming, folder, and wikilink form for any Adjudant-managed vault. Every vault write must conform.

## What enforces what

Each rule states its shape once. Detail enforced mechanically is not restated:

| rule area | enforcer |
|---|---|
| frontmatter keys per type | `FIELD_SCHEMA` in `_vault_walk.py`; validator 29; the PreToolUse schema gate; `tidy` feature 5 repairs |
| tag buckets A, B, D | `BUCKET_A_TYPES` / `BUCKET_B_MIGRATIONS` / `BUCKET_D_TAG_EXACT`; `tidy.normalize_tags`; validator 2 |
| status + freshness vocabularies | validators 23 (project), 26 (task aliases), 28 (decision), 31 (freshness) |
| wikilink form | `tidy` feature 4 |
| folder shape, some file naming | `check` reports; `tidy` repairs |

Validators 2, 23, 26, 28, 29 and 31 are parity checks: they hold this document, the templates and the `_vault_walk.py` constants to each other. They never read a vault file.

Caught at write time: a `Write` missing a required field, setting both `type:` and `node_type:`, or carrying a malformed §10 declaration is blocked; an unknown field passes, for `tidy` to strip. The gate ignores `Edit`s and status values; its skip list is internals.md detail. Everything else is reported after the fact or is judgment.

## 1. Frontmatter

Every file has YAML frontmatter, except `Home.md` (`type` + `updated` only). Which keys are legal on which type is `FIELD_SCHEMA`: a required set, an optional set, anything else is drift. Form rules, unenforced: standard YAML, no Obsidian syntax inside values except wikilink fields such as `supersedes`; ISO `YYYY-MM-DD` for dates and full ISO 8601 for timestamps; quote any string containing a colon or bracket; omit an empty optional key rather than writing `null` or `""`; write arrays as YAML lists, not inline, though an empty array is `[]`.

Project membership is the folder path (`projects/[zone/]slug/…`), never a frontmatter field: the retired `project:` field (dropped v0.16.0) duplicated the path on every note and drifted whenever a project changed zones. The graph backlink flows through each folder's `_index.md` instead.

`session_id:` (session notes) and `source_session:` (optional, content types) hold Claude Code conversation UUIDs so the conversation behind a write is one hop away. Both are hook-stamped, never hand-written, and `source_session` is off by default since v0.16.0. A stored UUID may dangle (transcripts are ephemeral): it retraces reasoning, never holds the conclusion, so a decision's content must land in the vault.

## 2. Tag schema (locked 2026-05-25)

Bare tags only, no prefix. Every file carries exactly one file-type tag matching its `type:`; `Home.md` is the lone exception (`type: vault-home`, no tag). **Bucket A** (thirteen file-type tags), **B** (custom-type tags, dropped) and **D** (deprecated: `ob/*`, leftover `cabinet/*`, project-slug tags, vague topicals, crew names) are enumerated in `_vault_walk.py` and applied by `tidy.normalize_tags`.

**Bucket C**, topical tags, is judgment with no enforcer: optional, queryable, sparing. Established clusters are `#content/` plus one of `seafood-companies`, `blog`, `page`, `hardware`, `personnel`, `videos`, `workflows`, `features`. A new topical needs all three: namespaced (`category/value`), queryable (you would filter on it), used across three or more files. Project kind is not a tag: it is `project_type:` on the brief, one of `coding | knowledge | plugin | tinkerage`. `cssclasses:` is an Obsidian CSS class, not a tag, and tag normalization leaves it alone.

## 3. File-type schemas

`templates/{type}.md` carries the canonical frontmatter shape (exceptions: four `project-brief-{project_type}.md`, two `_index-*.md`, and `home.md`). Body shape is not machine-checked. Decision: `## Decision` / `## Context` / `## Consequence`. Session: intent quote + append-only `## Log`. Doc: purpose sentence + `## {Section}`. Source: `## Key Points` / `## Notes` / `## Relevance`. Release: `## Changes`. Task: `## Task` / `## Notes`. Index: `# {Collection Name}`, one-line description, then `## Entries` of wikilinks, chronological where filenames carry dates and alphabetical otherwise. Iteration: a **folder** of build artefacts (HTML tryouts, experiments) whose optional `_iteration.md` indexes it. Note is free-form, project follows its `project_type` template, and handoff, dream-report and vault-home are machine-written.

Doc vs decision, the common mix-up. A decision has a date-prefixed filename, lives in `decisions/`, says "we picked X over Y because Z", and is append-only history of a moment. A doc lives at project root or in `docs/`, says "what is true now / how X works", and gets rewritten as understanding evolves.

## 4. Naming rules

Only some names are checked: `check` flags doc and decision date-prefix, doc case, session filename, and `.canvas`/`.base` kebab-case. The rest are on you: decision `{YYYY-MM-DD}-{kebab-title}.md`; session and dream report `{YYYY-MM-DD}.md`, one session per project per day, appended on resume; note, task and source `{kebab-title}.md` with no date unless time-relevant; release `v{X.Y.Z}.md`; doc `{NAME}.md` in **UPPERCASE**; project slug lowercase kebab-case with no spaces or dots (`dff2026-web`); iteration, the folder `iterations/{YYYY-MM-DD}-iter-{id}-{kebab-slug}/` holding the artefacts, with an optional `_iteration.md` inside. `brief.md`, `_handoff.md` and `_index.md` are written for you. "References" is not a file type: files in `references/` take `type: doc`, `note`, or `source` by content shape.

`status:` on a task note takes one of `todo` | `next` | `doing` | `review` | `blocked` | `done` | `icebox`, one per board lane. Aliases are accepted on input and never rewritten; the board maps them to lanes (mirrors `board.py` `STATUS_TO_COLUMN`), and a card dragged on any board surface writes its lane's canonical status back here:

| Alias | Board column |
|---|---|
| `backlog`, `todo`, `planned`, `proposed` | `backlog` |
| `next`, `ready`, `queued` | `next` |
| `doing`, `in-progress`, `in_progress`, `active`, `wip` | `doing` |
| `review`, `blocked`, `in-review` | `review` |
| `done`, `complete`, `completed`, `implemented`, `shipped`, `accepted` | `done` |
| `icebox`, `deferred`, `parked`, `shelved`, `someday` | `icebox` |

## 5. Folder structure

Defaults per `project_type`. `coding`: `decisions/`, `notes/`, `tasks/`, `references/`, each carrying an `_index.md`, plus `sessions/` and `images/` without one. `plugin`: the coding set plus `releases/`. `knowledge`: `notes/`, `sources/`, `references/` plus `sessions/`. `tinkerage`: `sessions/` only, optional. Anything beyond the defaults must be in the brief's `extra_folders:`; an undeclared folder is drift `check` flags. Auto-created, so exempt: `dreams/`, `canvases/`, `bases/`, `board/`.

Every folder under a project, or at vault root, holding two or more sibling `.md` files of the same conceptual type gets an `_index.md`. Exceptions: `sessions/` (ordering is the index), `images/`, `assets/`, `previews/`, and `iterations/` plus the iteration folders inside it, where build artefacts carry no frontmatter and `_iteration.md` is the only conformant file. `/adjudant tidy` rebuilds indexes mechanically; `check` only detects the gaps.

## 6. Wikilink rules

All vault-internal links use `[[note-name]]` form. **Markdown-style `[text](path)` is allowed if and only if `path` does NOT resolve to a vault `.md` file.** Heading anchors and non-vault targets are fine in markdown form.

Body links carry the full path and a display alias: `[[projects/{slug}/brief|{display}]]`, `[[projects/{slug}/decisions/{file}|{short title}]]`, and the same shape per zone folder. Images embed as `![[image.png]]` with a caption line below. Briefs carry `aliases: [{slug}]` so a bare `[[my-project]]` resolves cleanly.

## 7. Content style

Body copy is **actionable, clear, unambiguous, and short**. Style is judgment: `dream` flags suspects. The banned-term list lives in `reference/voice.md` and `validate.py`.

## 8. Project status and zones (locked 2026-07-16)

`status:` on a brief takes exactly one of `active` | `stale` | `fridge` | `done` | `dead` | `seed`, and picking between them is judgment. `active`: being worked. `stale`: declared active but quiet past `stale_after_days` (default 30), the only machine-suggested state. `fridge`: deliberately paused, intent to return. `done`: shipped and complete, a success rather than an abandonment. `dead`: abandoned. `seed`: captured idea, not yet started.

Placement follows status: `projects/` holds active, stale and seed; `projects/_fridge/` holds fridge; `projects/_archive/` holds done and dead. There is no automated mover: a project is moved between zones by hand. The `[[{slug}/brief|{slug}]]` index-row form resolves across zones by Obsidian suffix matching, so it survives a move; full-path `[[projects/…]]` links do not, and are updated by hand.

## 9. Decision status vocabulary (locked 2026-07-27)

`status:` on a decision note takes exactly one of `active` | `superseded` | `reversed` | `implemented` | `deferred`. `active`: in force, guiding work. `superseded`: replaced by a newer decision, named in `supersedes:` on the successor. `reversed`: undone without a replacement. `implemented`: the decided work has shipped, the record is historical. `deferred`: parked with intent to revisit, neither in force nor rejected. Historical values (`accepted`, `locked`, `current`) are synonyms of `active`: `check` reports them off-vocabulary, `tidy` migrates them after preview.

## 10. Epistemic freshness (locked 2026-07-31)

Optional on content types (`decision`, `note`, `doc`, `source`) only: `freshness:` = `timeless` | `dated` | `pointer`; `certainty:` 1-5; `validity_context:`; `valid_from:`/`valid_until:` dates. Malformed declarations are drift the write gate blocks; `check` reports the semantics. Declared signals outrank heuristics in every tier.
