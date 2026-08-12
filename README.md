# furtive-follies

A small kit that gives your projects a memory. It has two parts:

1. **adjudant** — a Claude Code plugin that keeps a running record of your work
   (session notes, decisions, a handoff, and a kanban board) in a plain folder of
   markdown files. You keep working like normal; it keeps the notes tidy and
   current in the background so future-you can pick up right where you left off.
2. **onboarding/** — an optional, friendly installer that adds a handful of nice
   terminal tools if you'd like a smoother command line. Totally optional.

You do **not** need to be a terminal person to use this, and you do **not** need
to install Obsidian. (A "vault" is just a folder of markdown files. Obsidian only
makes them prettier to browse if you ever want that.)

## Two steps to start

### A. (Optional) Run the onboarding script for the terminal niceties

Nice to have, not required. On a Mac with Homebrew:

```bash
bash onboarding/onboard.sh --check   # dry run: shows what it WOULD do, changes nothing
bash onboarding/onboard.sh           # the real thing (asks before any big change)
```

It's guarded and safe — anything already installed is skipped, and a missing tool
just skips quietly.

### B. Install the plugin and connect your project

Inside Claude Code, run these three lines:

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
/adjudant connect
```

`connect` sets up the vault and walks you through where to put it. That's it —
everything else is optional.

> The repo is **private**, so you need GitHub access to it first. If Claude Code
> can't read it, run `gh auth login` once in your terminal, then try again.

## What you get

- **One command, `/adjudant`, with seven verbs** — `connect`, `sync`, `check`,
  `sitrep`, `tidy`, `dream`, and `board`.
- **A project memory that keeps itself current** — notes and a handoff get written
  as you work, so you never start cold.
- **A drag-and-drop kanban board** (`/adjudant board`) built automatically from
  your task notes.
- **Read-only safety valves** — `check` and `dream` look and report; they don't
  change anything on their own.
- **Light on the wallet** — built to be token-frugal, so it's gentle on usage
  limits.

## Full walkthrough

New here? Read **[GETTING-STARTED.md](GETTING-STARTED.md)** — a friendly, step-by-step
guide to installing, choosing where your vault lives, and the handful of verbs
you'll actually use day to day.

Prefer to skim? Open **[field-guide.html](field-guide.html)** in a browser — the same
thing as a one-page ZenaSoft field guide: context management, the verbs, setup, and the suitcase.
