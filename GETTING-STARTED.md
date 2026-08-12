# Getting started with furtive-follies

Welcome! This guide walks you through everything, one small step at a time. You do
not need to be a terminal expert, and you can stop after the first real step
(`connect`) and be perfectly fine. Everything after that is here when you want it.

---

## 1. What adjudant is for

Think of it this way: **your project is the work; the vault is its memory.**

When you work on something over days or weeks, the important stuff — what you
decided, what you tried, where you left off — usually lives in your head or
scattered across chats. Then a weekend passes, or you switch tasks, and it's gone.

adjudant fixes that by keeping a running memory of your project:

- **Session notes** — a short record of what happened each time you worked.
- **Decisions** — the choices you made and why.
- **A handoff** — a "here's where things stand" note for the next person (usually
  future-you).
- **A kanban board** — your tasks, laid out on a board you can drag around.

Here's the shape of it: this "memory" is a **folder of markdown files** — plain text,
in a consistent structure. That folder is called a **vault**, and you read and
navigate it in **[Obsidian](https://obsidian.md)** (free), where the notes, the links
between them, and the board come to life. You'll install Obsidian as part of setup.

---

## 2. Before you start

You'll need three things:

1. **Claude Code installed** and working. If you can type `/plugin` inside it, you're set.
2. **Obsidian installed** — it's where you read the vault. It's free: [obsidian.md](https://obsidian.md), or `brew install --cask obsidian` on a Mac.
3. **GitHub access to the private repo** (`TomVDH/furtive-follies`). Because the
   repo is private, Claude Code needs permission to read it. If it can't, the fix
   is usually one command in your terminal:

   ```bash
   gh auth login
   ```

   That's the GitHub CLI signing you in once. Follow its prompts (pick GitHub.com,
   log in through the browser), and afterward Claude Code can read the private repo.
   You only do this once per machine.

---

## 3. Install

Three lines, typed inside Claude Code:

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
/adjudant connect
```

- The first line tells Claude Code where to find the kit.
- The second installs the adjudant plugin.
- The third links your current project to a vault (more on that next).

If line one or two complains that it can't reach the repo, that's the private-repo
access thing from step 2 — run `gh auth login`, then try again.

---

## 4. Choosing where your vault lives

The first time you run `/adjudant connect` in a project, adjudant checks whether you
already have a vault. If you don't, it helps you make one — and it asks **where** you
want it to live. This matters because your notes should end up somewhere convenient.

You don't have to figure this out alone. `connect` looks at your machine, shows you
the options that actually exist there, and gives you a recommendation:

- **A cloud-sync folder** (iCloud Drive, OneDrive, Google Drive, or Dropbox) is the
  recommended choice. Your notes then follow you across machines automatically —
  start on your laptop, pick up on your desktop, nothing lost.
- **A plain local folder** (like `~/Documents`) is perfectly fine if you only ever
  work on one machine.

Pick one, and adjudant creates the vault there for you. From then on, this project
remembers its vault; you won't be asked again. You only run `connect` once per
project.

---

## 5. A normal session

Here's the nice part: most of the time, **you just work.** Once a project is
connected, adjudant quietly does the record-keeping in the background:

- A **session note** gets started so today's work is captured.
- Anything written into the vault is **checked against a consistent shape** first,
  so your notes stay tidy and predictable instead of turning into a mess.
- A **handoff** is kept up to date so the next session starts with context, not a
  blank page.

You don't have to trigger any of that by hand. It happens as you go. The verbs
below are the few things you'll reach for on purpose.

---

## 6. The verbs you'll actually use

One command, `/adjudant`, followed by a verb. Here are the handful worth knowing.

### `sitrep` — "where was I?"

Come back after a break — a weekend, a vacation, a context-switch — and run:

```
/adjudant sitrep
```

It gives you a plain-language orientation: what this project is, what happened last,
and what's next. It's the fastest way to get your head back in the game.

### `check` — a quick health report

```
/adjudant check
```

A **read-only** look at your vault: what's in there and whether anything is
off-shape. It only reports — it changes nothing. Good for a peace-of-mind glance.

### `board` — your kanban

```
/adjudant board
```

Opens a drag-and-drop kanban board that's built automatically from your task notes.
Move cards between columns to track what's to-do, in progress, and done. No setup —
it's born from the notes you already have.

### `tidy` — gentle cleanup

```
/adjudant tidy
```

Routine, safe surface cleanup of the vault. It **shows you previews first** and is
built to never break anything. Think of it as straightening the desk, not
rearranging the house.

### `dream` — an advisory review (read-only)

```
/adjudant dream
```

This one reads through your notes and hands you a **findings report**: what looks
stale, what contradicts something else, what's orphaned and disconnected. Then it
**stops.** It changes nothing on its own — you read the report and decide what, if
anything, to do about each item. It's a thoughtful second pair of eyes, not an
autopilot.

> The other verbs — `connect` and `sync` — you'll use rarely: `connect` once per
> project (step 4), and `sync` when you want to push the current state to the vault
> on demand rather than waiting for the background updates.

---

## 7. The optional terminal niceties

If you'd like your command line to be a little friendlier, the `onboarding/` folder
has a small installer. This is **optional** and separate from adjudant — skip it and
nothing is lost.

On a Mac with Homebrew, try the dry run first (it changes nothing):

```bash
bash onboarding/onboard.sh --check
```

Happy with what it says? Run it for real:

```bash
bash onboarding/onboard.sh
```

It asks before any big change, and everything is **guarded** — anything already
installed is skipped, and a missing tool just skips quietly instead of erroring.

Here's what it can set up for you:

- **zoxide** — jump to folders you've visited with `z part-of-the-name`.
- **eza** — prettier file listings on `l`, `ll`, and `lt` (plain `ls` is left alone).
- **bat** — `cat` with syntax highlighting, so files are easier to read.
- **ripgrep** and **fd** — fast, friendly search and file-finding.
- **git-delta** — turns git diffs into clean, readable side-by-side changes.
- **tldr** — short, example-first, plain-English help for almost any command.
- **trash** — a safer `rm`: sends files to the Trash instead of deleting forever.
- **glow** — read markdown (including your vault notes) right in the terminal.
- **sl**, **gum**, and **cbonsai** — a little fun: a steam train, pretty prompts,
  and a tiny ASCII bonsai.

**Recommended terminal setup.** The shortcuts assume **Zsh** — required; it's the macOS
default since Catalina, so make it your shell with `chsh -s /bin/zsh` if it isn't already.
We also recommend [**iTerm2**](https://iterm2.com) as your terminal (`brew install --cask iterm2`),
and the [**Powerlevel10k**](https://github.com/romkatv/powerlevel10k) zsh prompt
(`brew install powerlevel10k`, then add it to `~/.zshrc` and run `p10k configure` — see its
[install guide](https://github.com/romkatv/powerlevel10k#installation)).

**Not on a Mac?** Install those tools with your own package manager (apt, dnf,
pacman, and so on), then source the shortcuts file from your shell config:

```bash
source /full/path/to/onboarding/casual-qol.zsh
```

The shortcuts are guarded the same way, so anything you didn't install simply stays
off.

---

## 8. Token-friendly habits

adjudant is built to be **light** — gentle on your usage limits. The everyday
record-keeping is designed to be small and cheap, so leaving it on doesn't cost you
much.

The one habit worth knowing: the heavier reads (like a full `dream` review) **ask
first** rather than quietly chewing through a lot at once. So you stay in control of
the bigger operations, and the small stuff just hums along.

---

## 9. When something looks off

A couple of common hiccups and their easy fixes:

- **"Vault not found" (or a project that seems disconnected).** Re-run
  `/adjudant connect`. It re-links the project to its vault and gets you sorted.
- **A write got blocked.** That's the shape-check doing its job. The message will
  tell you exactly which field needs fixing — adjust that, and the write goes
  through. It's protecting your notes from getting messy, not stopping you.

That's everything. Start with `/adjudant connect`, work like you normally would, and
reach for the other verbs whenever they'd help. Welcome aboard!
