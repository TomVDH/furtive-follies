# furtive-follies

A small **ZenaSoft** kit that gives your projects a memory: **adjudant** — a Claude
Code plugin that keeps a vault of notes (sessions, decisions, a handoff, a board)
for each project — plus an optional terminal **suitcase** of quality-of-life tools.

You don't need to be a terminal person. Claude Code runs as a desktop app for Mac and
Windows as well as in a terminal, and adjudant behaves the same either way.

**What you need**

- **Claude Code**, signed in
- **[Obsidian](https://obsidian.md)** (free) — where you read and navigate the vault
- **Python 3.9 or newer** — adjudant uses it behind the scenes. You never write any.
  Not sure? Ask Claude Code: *"Do I have Python 3.9 or newer? If not, tell me exactly
  how to get it on this machine."* On a Mac the fix is usually Apple's free developer
  tools, which your computer offers to install for you.

## 📖 Read the field guide

**[`field-guide.html`](field-guide.html)** is the whole thing on one page — a
self-contained, offline visual guide (nothing to install to read it).

- **Download it and double-click** to open in your browser, or
- **prefer a PDF?** Grab **[`field-guide.pdf`](field-guide.pdf)** — same guide, opens anywhere, nothing to install.

It's the same guide as this README, just nicer to read — share it with anyone.

## The short version

Inside **Claude Code**, run these three, one at a time:

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
/adjudant connect
```


`connect` shows where a vault can live (a cloud-sync folder is recommended so your
notes follow you across computers), makes it for you, and links your project.
That's it — session notes, the handoff, and the board keep themselves current.
Come back with `/adjudant status` after a break.

## Optional: the suitcase

A small kit of terminal tools that make the occasional command-line moment nicer.
Entirely optional: everything above works without it.

It needs the files on your machine, so download the folder first
([zip](https://github.com/TomVDH/furtive-follies/archive/refs/heads/master.zip)),
unzip it, then open a terminal inside it. The field guide walks through that,
including the drag-the-folder trick for getting the terminal to the right place.

```bash
bash onboarding/onboard.sh --check   # looks and reports, changes nothing
bash onboarding/onboard.sh           # installs the tools
```

We recommend [iTerm2](https://iterm2.com) as your terminal and the
[Powerlevel10k](https://github.com/romkatv/powerlevel10k) prompt, and the shortcuts
assume **Zsh** (the macOS default). Details are in the field guide.

## More

- Plain-text walkthrough: **[GETTING-STARTED.md](GETTING-STARTED.md)**
- The plugin itself: **[`adjudant/`](adjudant/)** (five verbs, its own README + GUIDE)
