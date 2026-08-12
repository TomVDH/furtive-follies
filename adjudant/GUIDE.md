# Using Adjudant

A walkthrough, from installing it to living with it. For the terse reference, see [README.md](README.md).

## What it's for

You work in a code project. The thinking around that project — why you made a decision, what you tried last week, what's half-finished — usually lives in your head or scrolls out of a chat. Adjudant writes it down, in an Obsidian vault, in a consistent shape, and keeps it current without you managing it.

The mental model: **your code project is the work, the vault is its memory.** You keep coding; adjudant keeps the record.

You don't have to open Obsidian for any of this. The vault is plain markdown files. Obsidian just makes them nice to browse if you want it.

## 1. Install and link

Install once per machine, in Claude Code:

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
```

Link each project once:

```
/adjudant connect
```

`connect` looks at your project, proposes a slug, a type, and a status, and shows you one card to confirm. Approve it and it writes a small breadcrumb (`.claude/adjudant`) pointing at the vault, a project folder in the vault with a `brief.md` and a `sessions/` folder, and today's session note. Every later verb reads the breadcrumb, so you never type vault paths.

`connect` is idempotent. Running it again on a linked project changes nothing.

## 2. Where your vault lives

Don't have a vault yet? `connect` walks you through it. It shows the vault-location options that exist on your machine and recommends a **cloud-sync folder** (iCloud Drive, OneDrive, Google Drive, Dropbox) so your notes follow you across machines. A plain **local folder** (like `~/Documents`) is fine if you only use one machine. Pick one, give the vault a name, and connect creates it for you.

If you already keep an Obsidian vault somewhere, connect finds it (or you can point it at the path). Either way, you only do this once.

## 3. A normal session

After connect, most of adjudant is invisible. As you work:

- A **session note** for today is created and kept updated. Commits, decisions, and notes you write land in it.
- When you write a decision or a note into the vault, adjudant checks its shape first. If a required field is missing, the write is blocked with a message saying what's wrong, so it never lands malformed.
- A **handoff** file tracks where things stand, so the next session (or the same project on another machine) starts oriented.

You don't call a verb for any of that. It rides on hooks.

## 4. Tasks and the board

Write a task note under the project's `tasks/` folder and a **kanban board** is born automatically. From then on:

- Open it with `/adjudant board serve` — a single HTML file, drag cards between columns, changes save to disk.
- A card you drag writes its new status back into the task note, so the board and your notes never disagree.

Projects that never grow tasks never grow board files. Nothing to clean up.

## 5. Checking in

Two read-only verbs, neither writes anything:

- `/adjudant sitrep` — orientation after a break. Where you left off, what's done, where the vault is, what's next, plus your git and dev-server state. Start here when you come back to a project cold.
- `/adjudant check` — a health report. Project and vault snapshot, plus any notes that have drifted off-shape. Add `check repo` to also audit the code repo's structure, or `check all` for both.

## 6. Keeping the vault clean

Two verbs, from safe to careful:

| Verb | Cadence | What it does | Risk |
|---|---|---|---|
| `tidy` | routine (daily/weekly) | indexes, tags, wikilink form, dates, off-shape frontmatter | none — previews first, never breaks anything |
| `dream` | as needed | reads your prose and reports what looks stale, contradictory, redundant, or orphaned | none — read-only; it hands you a report and stops |

`tidy` previews exactly what it would change and waits for your say-so. `dream` is the deeper look and the gentlest: it reads the content itself, hands you a findings report (what was found, what it suspects, where to look closer), ends with "store them, act on them, or let them pass," and changes nothing until you name what to do. It's project-scoped and asks before pulling a large project into the conversation, so it stays light on your usage.

## Living with it

- **Two machines.** The breadcrumb stores the vault's name as well as its path, so a project synced to another machine re-finds its vault even when the absolute path differs. Pull before you start; adjudant does the rest.
- **The voice.** Adjudant sets a direct, plain register for the session and refuses to write filler into vault notes. If you'd rather it didn't, add `voice: off` to `.claude/adjudant` (per project) or set `ADJUDANT_VOICE_DISABLE=1` (per machine).
- **Light on usage.** Heavy reads estimate their cost first and ask before pulling a large vault into the conversation. It's built to be gentle on usage limits.

## When something looks wrong

- **A write got blocked.** The message names the missing or malformed field. Fix the frontmatter and write again, or run `/adjudant check` to see every drifted note at once.
- **The board didn't appear.** It's born on the first real task note under `tasks/`. No tasks, no board, by design.
- **A verb can't find the vault.** The breadcrumb is missing or points nowhere. Re-run `/adjudant connect`.
