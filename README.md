# furtive-follies

A small **ZenaSoft** kit that gives your projects a memory: **adjudant** — a Claude
Code plugin that keeps a vault of notes (sessions, decisions, a handoff, a board)
for each project — plus an optional terminal **suitcase** of quality-of-life tools.

You don't need to be a terminal person, and you don't need to install Obsidian.

## 📖 Read the field guide

**[`field-guide.html`](field-guide.html)** is the whole thing on one page — a
self-contained, offline visual guide (nothing to install to read it).

- **Download it and double-click** to open in your browser, or
- clone the repo and open the file locally.

It's the same guide as this README, just nicer to read — share it with anyone.

## The short version

Inside **Claude Code**, run these three, one at a time:

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
/adjudant connect
```

The repo is private, so if Claude Code can't reach it, sign in once in your
terminal with `gh auth login`, then try again.

`connect` shows where a vault can live (a cloud-sync folder is recommended so your
notes follow you across computers), makes it for you, and links your project.
That's it — session notes, the handoff, and the board keep themselves current.
Come back with `/adjudant sitrep` after a break.

## Optional: the suitcase

A small kit of friendly command-line tools. On a Mac with Homebrew:

```bash
bash onboarding/onboard.sh --check   # dry run — changes nothing
bash onboarding/onboard.sh           # installs the tools
```

We recommend [iTerm2](https://iterm2.com) as your terminal and the
[Powerlevel10k](https://github.com/romkatv/powerlevel10k) prompt, and the shortcuts
assume **Zsh** (the macOS default). Details are in the field guide.

## More

- Plain-text walkthrough: **[GETTING-STARTED.md](GETTING-STARTED.md)**
- The plugin itself: **[`adjudant/`](adjudant/)** (seven verbs, its own README + GUIDE)
