# Adjudant

*Every unit has someone who keeps the records straight. Now your project does too.*

Run an Obsidian vault from inside your code project. Adjudant keeps a vault as your project's long-term memory: session notes, decisions, a handoff, and a kanban board, all written to a schema and kept current by background hooks. One command, `/adjudant`. Successor to `obsidian-bridge`.

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

<!-- VERBS:TABLE:START -->
## The five verbs

| Verb | What it does |
|---|---|
| `/adjudant connect` | Onboards a project and asks where it lives. |
| `/adjudant status [vault\|repo\|all] [--no-sync]` | Reports where you are, what is wrong, and what is stale. |
| `/adjudant clean [vault\|repo\|all] [--deep] [--folder <path>]` | Removes what the vault does not need. |
| `/adjudant dream [--folder <path>]` | Reads the prose and reports what only judgement finds. |
| `/adjudant board [scaffold\|serve\|status] [--project SLUG\|--all] [--from-tasks] [--force]` | Runs a self-hosted kanban. |
<!-- VERBS:TABLE:END -->

The two cleanup verbs form a ladder by risk:

```
clean        routine    surface mechanical, never breaks anything
clean --deep sparing    structural findings, reported for you to decide
dream        as needed  semantic, LLM-judged, you approve every change
```

## How it works

- **One breadcrumb.** `connect` writes `.claude/adjudant` in your code project, pointing at the vault. Every verb reads it, so you never pass paths by hand. It stores both an absolute path and the vault name, so it survives moving between machines.
- **Schema-locked writes.** Every note has a required frontmatter shape (`FIELD_SCHEMA`). A write that breaks it is blocked before it lands; `status` reports drift and `clean` repairs it.
- **Ambient by default.** Session notes, the handoff, and the board maintain themselves through background hooks. The board is born on your first task note and reseeds itself as tasks change. You rarely call these verbs directly.
- **Bounded cost.** Heavy verbs (`dream`, `clean --deep`, `status all`) estimate their context cost first and ask before pulling a large vault into the conversation.

## At a glance

| | |
|---|---|
| Command | `/adjudant <verb>` |
| Skill | one (`adjudant`); verbs dispatch to reference files on demand |
| Hooks | 11 entries across 10 events, all vault-aware |
| Templates | 20 file-type scaffolds + `board.html` |
| Helpers | stdlib-only Python, one per file-touching verb; no build step |
| Drift defense | `python3 scripts/validate.py` — 26 validators, run on pre-commit |
| Tests | `python3 -m unittest discover -p 'test_*.py'` |

Deep reference (hook wiring, the verb-to-helper map, cross-machine details) lives in [`skills/adjudant/reference/internals.md`](skills/adjudant/reference/internals.md). Vault rules (frontmatter, folders, naming) live in [`reference/vault-standards.md`](skills/adjudant/reference/vault-standards.md).

## Voice

Adjudant sets a direct, anti-slop register for the whole session and enforces it on every surface it writes. The full contract is in `reference/voice.md`. Turn it off per project with `voice: off` in `.claude/adjudant`, or per machine with `ADJUDANT_VOICE_DISABLE=1`.

## Pairing

- `hookify` — universal drift-defense hooks (git safety, secrets). Adjudant leaves those to it.
- `i-have-adhd` — soft dependency; shapes conversational output. Adjudant carries its own copy of the rules, so it isn't required.

## License

MIT
