# furtive-follies

A small **ZenaSoft** kit that gives your projects a memory: **adjudant** — a Claude
Code plugin that keeps a vault of notes (sessions, decisions, a handoff, a board)
for each project — plus an optional terminal **suitcase** of quality-of-life tools.

You don't need to be a terminal person — but you do need two free apps: **Claude Code**
and **[Obsidian](https://obsidian.md)**, which is where you read and navigate the vault.

## 📖 Read the field guide

**[`field-guide.html`](field-guide.html)** is the whole thing on one page — a
self-contained, offline visual guide (nothing to install to read it).

- **Download it and double-click** to open in your browser, or
- clone the repo and open the file locally, or
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

## Hosting the field guide on GitHub Pages

The guide is a single self-contained file: fonts and images are embedded, and it
loads nothing from the network. It works from disk, from a shared folder, or from
Pages, with no build step. This repo is already prepared for it:

- `index.html` sends the site root to `field-guide.html` (relative, so a project
  site at `/furtive-follies/` works the same as a user site)
- `.nojekyll` stops Jekyll from processing the files

To turn it on: **Settings → Pages → Source: Deploy from a branch**, pick the
`onboarding` branch and the `/ (root)` folder.

> **Before you do:** a Pages site is reachable by anyone with the link, even when
> the repository is private (per-visitor access control is GitHub Enterprise
> only). The guide contains a screenshot of a real vault. Check what is legible
> in it before publishing, or keep sharing the file directly instead.

## More

- Plain-text walkthrough: **[GETTING-STARTED.md](GETTING-STARTED.md)**
- The plugin itself: **[`adjudant/`](adjudant/)** (seven verbs, its own README + GUIDE)
