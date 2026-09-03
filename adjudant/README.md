# Adjudant

*Every unit has someone who keeps the records straight. Now your project does too.*

Adjudant runs an Obsidian vault from inside your code project. The vault holds your
project's long-term memory: session notes, decisions, a handoff, and a kanban board.
Background hooks keep it current while you work. One command drives it: `/adjudant`.

**New here? Read the [walkthrough](GUIDE.md).** This page is the reference.

## Install

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
```

Link your project once:

```
/adjudant connect
```

`connect` asks where the vault lives. It writes the answer to `.claude/adjudant`.
Every other verb reads that file, so you never type a path again.

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

Start with `status`. It is read-only, and it tells you what the others should do.

## What each verb costs you

`status` and `dream` change nothing. Read their reports, then decide.

`clean` takes an explicit phase. It never applies unless you name that phase.

```
clean detect     lists what it found
clean preview    writes the full proposal outside the vault
clean apply      makes the changes, after a backup
```

The two cleanup verbs form a ladder by risk:

```
clean          routine     mechanical repairs, and it creates no vault file
clean --deep   sparing     structural findings, reported for you to judge
dream          as needed   semantic findings, and you approve every change
```

`clean apply` deletes retired folder indexes. Adjudant generates two index
surfaces and retires the rest, so read the preview before you apply.

## How it works

- **One breadcrumb.** `connect` writes `.claude/adjudant` in your code project. It
  stores the vault path and the vault name, so the link survives a move between
  machines.
- **The template is the schema.** Each file type has a template. Adjudant parses the
  required fields from that template. Nothing declares them a second time. A write
  that breaks the shape fails before it lands.
- **Ambient by default.** Hooks maintain the session note, the handoff, and the
  board. A session note appears on your first real write, never when you open a
  session. You rarely call these verbs by hand.
- **Bounded cost.** `dream`, `clean --deep` and `status all` estimate their cost
  first. They ask before they pull a large vault into the conversation.
- **A drift canary.** Session start names one rare word. Adjudant checks every reply
  for it. A model that drops a one-word instruction has stopped following
  instructions, and that is the moment to start a fresh session.

## The vault

```
{vault}/
  Home.md                         generated
  projects/
    active/ paused/ finished/ archive/
      {slug}/
        brief.md  _handoff.md  _index.md
        sessions/  decisions/  tasks/  notes/
        docs/  specs/  releases/  dreams/
```

A folder exists when something is in it. Adjudant creates none up front.

Links carry the project-relative path and never the lifecycle folder. Move a
project between `active/` and `paused/` and every inbound link still resolves.

## At a glance

| | |
|---|---|
| Command | `/adjudant <verb>` |
| Skill | one (`adjudant`); verbs load reference files on demand |
| Hooks | 11 entries across 10 events, all vault-aware |
| Templates | 20 file-type scaffolds + `board.html` |
| Helpers | stdlib-only Python, one per file-touching verb; no build step |
| Drift defense | `python3 scripts/validate.py` — 26 validators, run on pre-commit |
| Tests | `python3 -m unittest discover -p 'test_*.py'` |

Hook wiring and the verb-to-helper map live in
[`reference/internals.md`](skills/adjudant/reference/internals.md).
Frontmatter, folders and naming live in
[`reference/vault-standards.md`](skills/adjudant/reference/vault-standards.md).

## Voice

Adjudant sets a direct register for the session and holds every surface it writes to
it. Vault writes follow ASD-STE100: one instruction per sentence, active voice,
present tense, under twenty words. The full contract is in `reference/voice.md`.

Turn it off for one project with `voice: off` in `.claude/adjudant`. Turn it off for
the machine with `ADJUDANT_VOICE_DISABLE=1`.

## Pairing

- `hookify` — universal drift-defense hooks: git safety, secret scanning. Adjudant
  leaves those to it.
- `i-have-adhd` — shapes conversational output. Adjudant carries its own copy of the
  rules, so it does not require the plugin.

## License

MIT
