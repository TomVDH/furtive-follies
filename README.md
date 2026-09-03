# furtive-follies

A small **ZenaSoft** kit that gives your projects a memory.

**adjudant** is a Claude Code plugin. It keeps a vault of notes for each project:
what you did each day, what you decided, what is left, and a board of your tasks.
The kit also carries an optional terminal **suitcase** of quality-of-life tools.

You do not need to be a terminal person. Claude Code runs as a desktop app for Mac
and for Windows. Adjudant behaves the same in every shape.

**What you need**

- **Claude Code**, signed in
- **[Obsidian](https://obsidian.md)**, free. You read the vault in it.
- **Python 3.9 or newer.** Adjudant uses it behind the scenes. You never write any.

Not sure about Python? Ask Claude Code: *"Do I have Python 3.9 or newer? If not,
tell me exactly how to get it on this machine."* On a Mac the answer is usually
Apple's free developer tools, and your computer offers to install them for you.

## 📖 Read the field guide

**[`field-guide.html`](field-guide.html)** is the whole thing on one page. It is
self-contained and works offline. You install nothing to read it.

- **Download it and double-click** to open it in your browser, or
- **prefer a PDF?** Take **[`field-guide.pdf`](field-guide.pdf)**. It opens anywhere.

The guide covers the same ground as this page. It is nicer to read, and you can
share it with anyone.

## The short version

Run these three inside **Claude Code**, one at a time:

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
/adjudant connect
```

`connect` shows you where a vault can live. Pick a cloud-sync folder, and your
notes follow you between computers. `connect` then makes the vault and links your
project.

That is the whole setup. The session note, the handoff and the board keep
themselves current from here.

Come back after a break and run `/adjudant status`.

## The five verbs

| Verb | What it does |
|---|---|
| `/adjudant connect` | Links a project to a vault. You run it once per project. |
| `/adjudant status` | Tells you where you are, what is wrong, and what is stale. |
| `/adjudant clean` | Removes what the vault does not need. |
| `/adjudant dream` | Reads your notes and reports what only judgement finds. |
| `/adjudant board` | Runs a kanban board built from your task notes. |

`status` and `dream` change nothing. They report, and you decide.

`clean` takes a phase: `detect`, `preview`, then `apply`. It changes nothing until
you name `apply`.

## Optional: the suitcase

A small kit of terminal tools. They make the occasional command-line moment nicer.
Everything above works without them.

The suitcase needs the files on your machine. Download the folder first
([zip](https://github.com/TomVDH/furtive-follies/archive/refs/heads/master.zip)),
unzip it, then open a terminal inside it. The field guide walks you through that,
including the trick of dragging the folder onto the terminal.

```bash
bash onboarding/onboard.sh --check   # reports what it finds, changes nothing
bash onboarding/onboard.sh           # installs the tools
```

We recommend [iTerm2](https://iterm2.com) as your terminal and the
[Powerlevel10k](https://github.com/romkatv/powerlevel10k) prompt. The shortcuts
assume **Zsh**, which is the macOS default. The field guide holds the details.

## More

- Plain-text walkthrough: **[GETTING-STARTED.md](GETTING-STARTED.md)**
- The plugin itself: **[`adjudant/`](adjudant/)**, with its own README and guide
