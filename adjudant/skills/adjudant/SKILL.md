---
name: adjudant
description: Operate an Obsidian vault from a code project. `/adjudant {connect|status|clean|dream|board}` — connect onboards a project and asks where it lives; status reports where you are, what is wrong, and what is stale; clean removes what the vault does not need; dream reads the prose and reports what only judgement finds; board runs a self-hosted kanban. Also fires whenever decisions, sessions, or notes are written into a linked vault.
version: 2.0.0
user-invocable: true
argument-hint: "[connect|status|clean|dream|board] [args]"
license: MIT
---

# Adjudant

<!-- VERBS:SUMMARY:START -->
Vault editor/writer and project initializer. One skill, one command, five verbs.
<!-- VERBS:SUMMARY:END -->

Pairs with hookify for universal drift-defense hooks.

## Verb router

<!-- VERBS:ROUTER:START -->
| Verb | Loads | Purpose |
|---|---|---|
| `connect` | `reference/connect.md` | Link a project to its vault. Infers slug, type, and status, confirms one card of required fields, applies with a receipt. Idempotent. |
| `status` | `reference/status.md` | Make derived state current (brief date, handoff, index row), then report in three bands: what is wrong now, what is going stale, and what is worth a look. |
| `clean` | `reference/clean.md` | Cleanup sweep: indexes, wikilink form, updated dates, off-schema frontmatter. Previews then applies, and never creates a vault file. --deep adds the structural pass. [vault\|repo\|all] adds repo symlinks. |
| `dream` | `reference/dream.md` | Semantic refresh, the deepest tier: surfaces stale, superseded, redundant, or orphaned content as scored candidates you judge before anything changes. --folder scopes the walk to one subtree. |
| `board` | `reference/board.md` | Scaffold a self-hosted kanban seeded from tasks/: drag to move, saved to disk. Re-seeding keeps your dragged cards. Use --project SLUG or --all. |
| _(internals)_ | `reference/internals.md` | Not a verb. Hook wiring, verb-to-helper map, environment probes. Load only when the question is about adjudant's own machinery |
<!-- VERBS:ROUTER:END -->

When a verb is invoked, load **only** the matching reference file. Do not bring all reference files into context.

## The cleanup model

```
clean        = mechanical sweep     (routine, daily/weekly, never breaks)
clean --deep = structural pass      (sparing, quarterly, reports only)
dream        = content/knowledge/memory refresh (semantic; judgment-heavy)
```

`clean` may rewrite a file in place and remove one. It may not create a vault file — `_vault_write.VaultWriteGuard` enforces that, so anything clean cannot fix by rewriting it reports instead. A folder missing its `_index.md` is reported as a gap, never filled.

`dream` reads actual prose and surfaces outdated/redundant/stale/orphaned content as scored *candidates* for Claude to judge, capped at the twenty most confident. `dream.py` is its read-only analyser.

## Cost gate (locked)

Verb weights live in `scripts/command-metadata.json` (`weight: light | medium | heavy`). The estimate approximates what Claude will read back into context; helpers compute it with a stat-only walk (`bytes // 4`).

<!-- VERBS:WEIGHTS:START -->
- **Heavy verbs** (`dream`): run the backing helper with `--estimate-only` FIRST. If `cost.warn` is true, stop, show the numbers, and ask the user to proceed, scope down, or abort. Proceed only on explicit confirmation. If `warn` is false, run normally and include the estimate as one line.
- **Medium verbs** (`status`, `clean`): no pre-flight. The helper's JSON carries a `cost` block; render it as one line.
- **Light verbs** (`connect`, `board`): no estimate; the static weight badge is enough.
<!-- VERBS:WEIGHTS:END -->
- The heavy list above is by verb weight. Two flag-scoped forms escalate into it and get the same `--estimate-only` pre-flight: `clean --deep` and `status all`.
- `status all` sums two estimates: `status.py --estimate-only` plus `repo_scan.py --estimate-only`.
- If an estimate cannot be computed (unresolvable vault or breadcrumb), treat it as `warn: true` and ask before proceeding.
- Threshold default is the build profile's `cost_warn_tokens` (`scripts/build-profile.json`); per-project override via `cost_warn_tokens:` in `.claude/adjudant`.

## Voice (locked)

Load `reference/voice.md` with every verb (the one exception to
load-only-the-matching-reference; it is small). It defines the banned lexicon, the
glazing ban, the pushback contract, the ELI5/ELI12/ELICTO explanation modes with
per-verb defaults, ASD-STE100 (Simplified Technical English) as the preferred register, and typography (no em dashes in rendered output or vault writes).
The `voice-lexicon` validator enforces the machine-checkable subset.

## Vault standards — single source of truth

`reference/vault-standards.md` is the authoritative spec for frontmatter requirements per file type, folder structure, file-naming rules, and wikilink form. All vault writes must conform. The build's `validate.py` enforces.

## Content authoring

For specialized content types, load the matching reference on demand:

<!-- VERBS:CONTENT-REFS:START -->
- `reference/content-bases.md` — `.base` files
- `reference/content-markdown.md` — Obsidian-flavoured markdown (callouts, embeds, wikilinks)
- `reference/content-clipper.md` — Web Clipper templates
- `reference/content-cli.md` — Obsidian CLI
- `reference/repo-standards.md` — code-repo conventions (the `status`/`clean` `[repo|all]` target)
<!-- VERBS:CONTENT-REFS:END -->

## Templates

`templates/` contains the canonical scaffolds for every file type Adjudant ships. Provisioning is done by `/adjudant connect`. Schema is enforced — every write must match the template frontmatter shape per `reference/vault-standards.md`.

