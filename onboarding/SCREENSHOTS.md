# Screenshot runbook

Nine slots in `field-guide.html`. Each one below says what to run and what has to
be on screen. Run them against any linked project: `demo-stage.sh` builds a
throwaway one if you would rather not photograph real work.

Before you start, in the terminal you are shooting:

```bash
printf '\033]0;acme-web\007'   # tab title, so no folder path leaks into the shot
clear
```

Window at roughly 1100x700, font one or two sizes up from your usual. Wider than
that and the text is unreadable when the image is scaled into the page.

---

## 1. The vault in Obsidian  — FILLED

Already in the guide. Replace it only if you want a shot with no real project
names in the sidebar.

Open the vault, click any note, turn on the graph pane on the right.

## 2. adjudant at work in the CLI

```
/adjudant sync
```

Wants: the tool reporting what it wrote. Let it finish, then capture the whole
exchange including your prompt line.

## 3. The board in a browser

```
/adjudant board
```

Then in the browser, drag one card between columns before the shot so the
columns are visibly uneven. Capture the browser content only, no bookmarks bar.

## 4. The statusline

```bash
cd <the project>
claude
```

The statusline sits at the very bottom. Shoot the bottom third of the window:
branch, vault name, model, and the context bar are what matter. Ask one small
question first so the window is not empty above it.

## 5. `ls` next to `ll`

```bash
cd <the project>
ls
ll
```

Both in one shot, so the difference is the point. Needs `eza` installed and
`casual-qol.zsh` sourced, or `ll` will not exist.

## 6. Obsidian's "Open folder as vault"

Obsidian → **Open another vault** → **Open folder as vault** → pick the vault
folder. Capture the dialog with the folder highlighted, before you confirm.

## 7. `/adjudant connect` choosing a location

Run in a project with **no** `.claude/adjudant` file, or it will report that the
project is already linked:

```
/adjudant connect
```

Wants: the list of candidate locations with the recommendation visible. Stop
before answering.

## 8. `/adjudant sitrep`

```
/adjudant sitrep
```

The most persuasive image in the guide, so it needs a project with history:
several sessions, a handoff, and a couple of decisions. Wants: where you left
off, what is done, and the next step, all in one frame.

## 9. A session note in Obsidian

Open `projects/<slug>/sessions/<today>.md`. Reading view, not edit mode. Wants:
the intent line and a log with timestamps.

## 10. Ubuntu on Windows

Windows only, and it cannot be staged from here. Start menu → Ubuntu, then run
`cd ~` and `ls`. Wants: the Ubuntu window with its prompt, so a Windows reader
recognises the thing they are being asked to open.

---

## Before you hand them over

Check each image for: folder paths with your name in them, client or project
names in a sidebar, tab titles, other windows behind, notification banners.
Anything legible in a screenshot is legible to everyone who gets the guide.

Send them over and they get embedded as data URIs, the same as the first one, so
the page stays a single file.
