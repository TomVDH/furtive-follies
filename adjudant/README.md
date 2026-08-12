# Adjudant

*Every unit has someone who keeps the records straight. Now your project does too.*

Run an Obsidian vault from inside your code project. Adjudant keeps a vault as your project's long-term memory: session notes, decisions, a handoff, and a kanban board, all written to a schema and kept current by background hooks. One command, `/adjudant`, with seven verbs.

**New here? Read the [walkthrough](GUIDE.md).** This page is the reference.

## Install

```
# in Claude Code
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
```

Then link your project once:

```
/adjudant connect
```

## The seven verbs

| Verb | What it does |
|---|---|
| `/adjudant connect` | Link a project to its vault. Run once per project. |
| `/adjudant sync` | Push project state to the vault: brief, handoff, project index. |
| `/adjudant check [vault\|repo\|all]` | Read-only health report: project, vault, and schema drift. `repo`/`all` also audit repo structure. |
| `/adjudant sitrep` | Plain-language orientation after a break. Read-only. |
| `/adjudant tidy [vault\|repo\|all]` | Routine surface cleanup: indexes, tags, wikilinks, frontmatter. Preview then apply. |
| `/adjudant dream` | Advisory content review: flags stale, contradictory, or orphaned notes as an ephemeral report you respond to. Read-only. |
| `/adjudant board [scaffold\|serve\|status]` | Scaffold a self-hosted kanban seeded from your tasks. |

Cleanup runs in two tiers, by how much you trust it:

```
tidy   routine    surface mechanical, never breaks anything
dream  as needed  semantic review, read-only, you decide what to act on
```

## How it works

- **One breadcrumb.** `connect` writes `.claude/adjudant` in your code project, pointing at the vault. Every verb reads it, so you never pass paths by hand. It stores both an absolute path and the vault name, so it survives moving between machines.
- **Schema-locked writes.** Every note has a required frontmatter shape (`FIELD_SCHEMA`). A write that breaks it is blocked before it lands; `check` reports drift and `tidy` repairs it.
- **Ambient by default.** Session notes, the handoff, and the board maintain themselves through background hooks. The board is born on your first task note and reseeds itself as tasks change. You rarely call these verbs directly.
- **Bounded cost.** Heavy verbs (`dream`, `check all`) estimate their context cost first and ask before pulling a large vault into the conversation — built for tight token budgets.

## At a glance

| | |
|---|---|
| Command | `/adjudant <verb>` |
| Skill | one (`adjudant`); verbs dispatch to reference files on demand |
| Hooks | vault-aware, ambient (session notes, handoff, board, schema gate) |
| Helpers | stdlib-only Python, one per file-touching verb; no build step |
| Drift defense | `python3 scripts/validate.py` |
| Tests | `python3 -m unittest discover -p 'test_*.py'` |

Deep reference (hook wiring, the verb-to-helper map) lives in [`skills/adjudant/reference/internals.md`](skills/adjudant/reference/internals.md). Vault rules (tags, frontmatter, folders, naming) live in [`reference/vault-standards.md`](skills/adjudant/reference/vault-standards.md).

## Voice

Adjudant sets a direct, anti-slop register for the whole session and enforces it on every surface it writes — a plain, get-to-the-point style tuned for clear, low-noise communication. The full contract is in `reference/voice.md`. Turn it off per project with `voice: off` in `.claude/adjudant`, or per machine with `ADJUDANT_VOICE_DISABLE=1`.

## License

MIT
