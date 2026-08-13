---
name: adjudant
description: Operate an Obsidian vault from a code project. `/adjudant {connect|sync|check|sitrep|tidy|dream|board}` — project init, schema-enforced writes, surface cleanup (tidy), an advisory content review (dream), read-only status (check) and orientation (sitrep), and a self-hosted kanban board. Also fires whenever decisions, sessions, or notes are written into a linked vault.
version: 1.0.0
user-invocable: true
argument-hint: "[connect|sync|check|sitrep|tidy|dream|board] [args]"
license: MIT
---

# Adjudant

Vault editor/writer and project initializer. One skill, one command, seven verbs.

## Verb router

| Verb | Loads | Purpose |
|---|---|---|
| `connect` | `reference/connect.md` | Link a project to its vault: breadcrumb, AGENTS.md+CLAUDE.md, vault scaffold, session note, .gitignore. Idempotent |
| `sync` | `reference/sync.md` | Push project state to the vault: brief, handoff, project index row |
| `check` | `reference/check.md` | Read-only project + vault health, with schema drift. `[vault\|repo\|all]` also audits repo structure (versions, symlinks, registration, stale plans) |
| `sitrep` | `reference/sitrep.md` | Plain-language orientation after a break: where you left off, what's done, where the vault is, what's next, plus git and dev-server state. Read-only |
| `tidy` | `reference/tidy.md` | Routine surface sweep: indexes, tags, wikilink form, `updated:`, off-schema frontmatter. Two-phase preview → apply. `[vault\|repo\|all]` adds repo symlinks |
| `dream` | `reference/dream.md` | Advisory content review: surfaces stale, contradictory, redundant, and orphaned notes as an ephemeral findings report you respond to. Read-only; enacts nothing on its own |
| `board` | `reference/board.md` | Scaffold a self-hosted kanban seeded from `tasks/`: drag to move, saved to disk, re-seeds without clobbering dragged cards. `--project <slug>` or `--all` |
| _(internals)_ | `reference/internals.md` | Not a verb. Hook wiring, verb-to-helper map, environment probes. Load only when the question is about adjudant's own machinery |

When a verb is invoked, load **only** the matching reference file. Do not bring all reference files into context.

## The two-tier cleanup model

```
tidy   = surface mechanical      (routine, daily/weekly, never breaks anything)
dream  = content/knowledge review (semantic; advisory, judgment-heavy, read-only)
```

`tidy` fixes mechanical drift (indexes, tags, wikilink form, `updated:` dates) behind a preview → apply. `dream` reads actual prose and surfaces outdated / contradictory / redundant / stale / orphaned content as an **ephemeral findings report** — it enacts nothing on its own; you decide what to store, act on, or let pass. `dream.py` is its read-only analyser.

## Cost gate (locked)

Verb weights live in `scripts/command-metadata.json` (`weight: light | medium | heavy`). The estimate approximates what Claude will read back into context; helpers compute it with a stat-only walk (`bytes // 4`).

- **Heavy verbs** (`dream`, `check all`): run the backing helper with `--estimate-only` FIRST. If `cost.warn` is true, stop and show the numbers ("dream would pull ~12k tokens into context: 40 files, 160 KB prose") and ask the user to proceed or abort. Proceed only on explicit confirmation. If `warn` is false, run normally and include the estimate as one line.
- **Medium verbs** (`check`, `sitrep`, `tidy`): no pre-flight. The helper's JSON carries a `cost` block; render it as one line.
- **Light verbs** (`connect`, `sync`, `board`): no estimate; the static weight badge is enough.
- `check all` sums two estimates: `check.py --estimate-only` plus `repo_scan.py --estimate-only`.
- If an estimate cannot be computed (unresolvable vault or breadcrumb), treat it as `warn: true` and ask before proceeding.
- Threshold defaults to a token-frugal value; per-project override via `cost_warn_tokens:` in `.claude/adjudant`.

## Voice (locked)

Load `reference/voice.md` with every verb (the one exception to
load-only-the-matching-reference; it is small). It defines the banned lexicon, the
glazing ban, the pushback contract, the ELI5/ELI12/ELICTO explanation modes with
per-verb defaults, ASD-STE100 (Simplified Technical English) as the preferred register, and typography (no em dashes in rendered output or vault writes).
The `voice-lexicon` validator enforces the machine-checkable subset.

## Vault standards — single source of truth

`reference/vault-standards.md` is the authoritative spec for tag taxonomy, frontmatter requirements per file type, folder structure, file-naming rules, and wikilink form. All vault writes must conform. The build's `validate.py` enforces.

## Content authoring

For specialized content types, load the matching reference on demand:

- `reference/content-bases.md` — `.base` files
- `reference/content-markdown.md` — Obsidian-flavoured markdown (callouts, embeds, wikilinks)
- `reference/content-clipper.md` — Web Clipper templates
- `reference/content-cli.md` — Obsidian CLI
- `reference/repo-standards.md` — code-repo conventions (the `check`/`tidy` `[repo|all]` target)

## Templates

`templates/` contains the canonical scaffolds for every file type Adjudant ships. Provisioning is done by `/adjudant connect`. Schema is enforced — every write must match the template frontmatter shape per `reference/vault-standards.md`.
