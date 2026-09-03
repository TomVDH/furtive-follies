# /adjudant board

Scaffold a self-hosted **work-order kanban board** — a *standard project
surface* any adjudant project can have, not a one-off. The board is a single,
dependency-free `board.html` (drag a card between stages; it auto-saves to disk
via the File System Access API on Chromium, with a localStorage mirror) driven
by a sibling `board-data.json`. Each board namespaces its own browser + disk
state by `boardId`, so a portfolio of project boards served from the same
localhost never clobber one another.

It is a *view*: cards carry short ids and mono `ref ·` tags that cross-link your
own codes (specs, handoffs, commits). Category colour is data-driven — names get
OKLCH palette hues by index, or supply explicit `{ "name": "oklch(...)" }`.

## Targeting (which project's board)

| Flag | Board(s) scaffolded |
|---|---|
| *(default)* `--project-dir PATH` | the current breadcrumb-linked project |
| `--project <slug>` | the named project under `{vault}/projects/<slug>` |
| `--all` | one board for **every** project in the vault |

`--project`/`--all` resolve the vault from the cwd breadcrumb (or explicit
`--vault PATH`) and discover projects by **filesystem truth** — every
`{vault}/projects/<slug>/brief.md`, skipping `_`/`.` dirs. The fragile
`projects/_index.md` table is never parsed, so malformed or duplicate rows can't
break discovery. `--all` is error-isolated: one bad project never aborts the batch.

**Containment.** A `--project` slug must be lowercase kebab-case and must
resolve to a path inside the vault, so a traversal slug (or a zone dir that is
a symlink out) is refused rather than scaffolded. A board is written under the
project it belongs to; the one exception is a `--dest` you pass yourself, which
may point at a code repo (see below).

## The features (locked spec)

1. **Scaffold** — `board.py scaffold` writes `board-data.json` (the deck) + a
   self-contained `board.html` (template with the deck injected between its
   `BOARD_DATA` markers; every `<` in the payload is escaped, so task titles
   containing `</script>` or `<!--` can't break the page). Default dest is
   `{vault}/projects/{slug}/board/`; pass `--dest` to target a code repo
   (e.g. `<repo>/_docs/board`). `--dest`/`--data` are single-project only
   (not valid with `--all`).
2. **Seed from tasks** — `--from-tasks` builds one card per `{project}/tasks/*.md`
   note: `code`/`id`/filename → card id, first `# heading`/`title:` → title,
   `status:` → column, `category:`/first non-`task` tag → category,
   `related:` → mono refs, `note:` → note. `_index.md` and roadmap/index files
   (`type: tasks`) are skipped — they aren't per-card task notes. Empty `tasks/`
   yields a clean 6-stage starter deck (no error). Duplicate card ids across
   task notes are disambiguated to the filename stem, with a stderr warning.
   A task note that is not valid UTF-8 is skipped with a named stderr warning
   instead of being decoded lossily: a `U+FFFD` in a card id is permanent,
   because re-saving the note yields a new id and the old card is iceboxed
   forever rather than deleted.
3. **Serve** — `board.py serve --dir DIR` runs a localhost static server so the
   disk-save (File System Access API) works (it needs a secure context, not
   `file://`). `--open` launches the browser; `--port 0` picks a free port
   (the real port is printed); a busy port errors cleanly instead of tracebacking.
4. **Status** — `board.py status [--project SLUG|--all]` prints per-column card
   counts in the terminal (plus a warning for cards in unknown columns) without
   opening a browser. Same targeting flags as scaffold.

## Run

```bash
# the current project, from its tasks/
python3 "$(dirname "$0")/../../../scripts/board.py" scaffold --project-dir "$PROJECT_ROOT" --from-tasks

# a named project by slug (vault resolved from the cwd breadcrumb)
python3 .../scripts/board.py scaffold --project steel-tempest --from-tasks

# every project in the vault, one board each
python3 .../scripts/board.py scaffold --all --from-tasks

# or target a code repo and reuse an existing deck verbatim
python3 .../scripts/board.py scaffold --project-dir "$PROJECT_ROOT" \
  --dest "$REPO/_docs/board" --data "$REPO/_docs/board/board-data.json" --title "My Board"

# serve it (background), then open http://localhost:8787/board.html
python3 .../scripts/board.py serve --dir "$DEST" --port 8787
```

Then present the board, URL first (the line the user acts on): start `serve` in
the background, open the URL, and close with one next step: drag cards, or hit
**connect file** to enable disk auto-save.

## Data model (`board-data.json`)

```json
{
  "version": 1, "boardId": "my-project",
  "title": "My Board", "subtitle": "Work-order board", "updated": "2026-06-24",
  "columns": [{ "id": "backlog", "name": "Backlog" }, ...],
  "categories": ["build", "docs", "infra"],
  "cards": [{ "id": "X-01", "title": "...", "column": "backlog",
              "category": "build", "related": ["SPEC-001"], "notes": "",
              "source": "task", "taskStatus": "todo" }]
}
```

- `taskStatus` is the task note's `status:` as of the last reconcile — the
  common ancestor the three-way merge needs. A deck without it is pre-1.0.1
  and reconciles as "unknown", never as "the board moved".

- `boardId` (defaults to the project slug) namespaces the board's browser
  `localStorage` + IndexedDB file-handle, keeping multiple boards independent.
- `done` and `icebox` columns get `BUILT` / `PARKED` rubber-stamp overprints by
  default. A renamed or added lane sets its own: `"stamp": "SHIPPED"` (or
  `false` for none), `"stampTone": "built"|"parked"`, `"muted": true|false`.
- A column may carry `"wip": N` — the lane head then shows `count/N wip` and
  turns red when over the limit.
- Cards whose `column` matches no lane (hand-edited deck, removed column) are
  never invisible — they render in a synthetic **UNFILED** lane you can drag
  them out of. That lane takes no drops, and shows no drop affordance.
- A category colour must be a colour the browser accepts. Anything that could
  fetch (`url(...)`) falls back to the palette hue: the board is served from
  disk and makes no outbound request.
- In-browser view tools (never persisted): a **filter** box narrows by
  id/title/category/ref/note (`Esc` clears), legend keys are buttons that
  toggle a category filter, a focused ticket moves one lane left/right with
  `[` / `]`, and on a touch screen you tap an order then tap a stage.
- The browser persists **only the moves you made by hand**, as
  `{cardId: {from, to}}`. Everything else re-renders from the deck on every
  load, so a re-scaffold that re-seeds a title, category or ref is visible
  immediately, while the lane you dragged a card into survives it. An override
  retires as soon as the deck itself moves that card somewhere else.
- Before writing to a connected `board-data.json` the page **re-reads it and
  merges**, so a reseed's new cards, its icebox moves and lanes you added by
  hand are not overwritten by a tab that has been open since before them. A
  second tab's moves are adopted rather than clobbered. A move that no store
  confirmed is rolled back with a visible message, never left looking saved.

## Idempotency — refresh without clobber

Re-running `board` does not wipe in-progress card state:

- **`--from-tasks` over an existing board → merge.** Per card id: a card present
  in both keeps the **column you dragged it to** (and any board-local `notes`),
  while `title`/`category`/`related` re-seed from the task note. New tasks are
  added in their status-derived column; a card whose task disappeared is moved to
  `icebox` (never deleted). A hand-added card with no `id`, and any second card
  sharing an `id`, is carried through the merge verbatim (it can never match a
  task note); a repeated id also warns on stderr. Deck-level
  `title`/`subtitle`/**`columns`** from disk are preserved — custom lanes you
  added survive a re-seed.
- **Without `--from-tasks` → the on-disk deck is kept untouched** (only `board.html`
  is refreshed from the template, so styling/engine updates land).
- **`--force` → full rebuild from tasks**, discarding dragged columns. It
  requires `--from-tasks` (or `--data`) when a deck already exists — `--force`
  alone would overwrite the board with an empty starter deck, so it refuses.
  Any replace (`--force` or `--data`) first copies the old deck to
  `board/.bak/board-data-<YYYYMMDD-HHMMSS>.json`, newest 5 kept. The copy is
  taken once the replacement deck has been read and validated, so a run that
  fails and writes nothing leaves every earlier backup byte-identical. This
  directory is one of the two backup paths that deliberately live inside the
  vault rather than in `$TMPDIR` — see `reference/state-contract.md` for why,
  and for the other.
- **`--data FILE` → that deck verbatim** (missing standard fields are backfilled).
- A corrupt/unreadable `board-data.json` (or `--data` file) exits non-zero with
  a clear error — it never tracebacks or silently rebuilds.

`board.html` is always re-emitted from the canonical template — never hand-fork
instantiations; change `templates/board.html` and re-run.

## Ambient board: birth and passive refresh

The board also maintains itself. `board.py --ensure --project-dir X` is the
ambient form hooks call: birth or reseed, never clobbering drag state, verdict
on the last stdout line:

| Verdict | Meaning |
|---|---|
| `no-tasks` | no real task notes (only `_index.md` / `type: tasks` roadmaps); nothing written |
| `created` | first real task note found, no deck yet: board born via the scaffold path |
| `reseeded` | deck existed and the clobber-safe `--from-tasks` merge changed it |
| `no-change` | merge would change nothing; deck untouched, mtime included (no sync churn) |
| `html-refreshed` | only board.html was stale (template drift or vanished page); deck untouched |
| `tasks-synced` | the deck was settled but a dragged lane was missing from `tasks/`: the notes were updated to match (see below) |

### The board writes back (v1.0.0)

The board is a view of the vault, but a drag happens in the view. So every
lane change on **any** surface is pushed back into the task note's `status:`:
a browser drag, an Obsidian kanban drag folded in by the read-back, or a deck
you edited by hand. Without this the note stayed stale forever, because the
merge deliberately keeps the dragged column, and `check`, `dream`, `clean`
and the sitrep board line all read the note rather than the deck.

- Each lane has exactly one canonical status (`CANONICAL_STATUS_FOR_COLUMN`),
  which is why `next` is part of the task vocabulary: a lane no status can
  express is a lane that diverges silently.
- A note is rewritten **only** when its own status maps to a different lane
  than the deck has. So an input alias (`wip` sitting in doing) and a
  distinction the lane cannot express (`blocked` sitting in review) both
  survive untouched, per the never-rewrite-aliases rule.
- **Divergence alone does not license a write (v1.0.1).** Deck ≠ note is
  equally the signature of a drag and of a human editing the note in
  Obsidian, and v1.0.0 read every such case as a drag: marking a task done in
  Obsidian was silently reverted by the next ambient hook, and the log line
  said "from the board" about a card nobody had touched. `taskStatus` is the
  common ancestor that separates the two. Unchanged since the last reconcile
  → only the board can have moved, the drag wins and the note is rewritten.
  Changed → the human moved it, the note wins and `merge_deck` moves the card
  instead. Absent (a pre-1.0.1 deck) → neither store is trusted: this path is
  ambient and has no preview or backup, so it records the ancestor and lets
  the next run decide rather than guessing once, destructively.
- Only `status:` changes; other fields, their order, the body and the line
  endings are left exactly as they were. The write is atomic and locked.
- Cards with no task note (hand-added on the board) are never materialized
  into notes here, and since v3 nowhere else either: write the note yourself,
  or use `/adjudant status --capture-task`.
- It converges: once the note matches, the next run reports `no-change`.

### Kanban surface (v0.23.0)

`scaffold --kanban` also writes `board/kanban.md` in the de-facto
obsidian-kanban format (`kanban-plugin: board` frontmatter, `##` lanes,
`- [ ]` cards), so the same deck renders as a native drag-drop board inside
Obsidian. Born only by the flag; refreshed by every scaffold/ensure once it
exists. The plugin's own state — the `%% kanban:settings %%` block, the
`## Archive` section, any other `%% %%` comment block — survives every
rewrite byte-for-byte. Drags made in Obsidian read back on the next reseed
when `kanban.md` is newer than the deck (unknown lanes keep the deck column;
archived cards never count); the deck stays the single truth. After each
kanban write the scaffold prints an `obsidian://open` URI for the file.

Projects that never grow tasks never grow board files. Passive surfaces that
keep the board current without being asked:

- **SessionStart** renders one board status line: per-column counts plus a
  stale flag when any task note is newer than the deck.
- **SessionEnd** runs `board_bridge.py --ensure-only` when a deck already
  exists, so the last edits of the session reach the board. A session end
  never births a board.

Until v3 a third surface wrote here: PostToolUse fired `board_bridge.py
--ensure-only` on any Write or Edit under `tasks/`, which meant the first task
note scaffolded `board-data.json`, `board.html` and a lock file into a project
that had never run `board`. That branch is deleted. A deck is born by
`/adjudant board` and by nothing else; a task-note edit reaches the board at
session end.

Because two surfaces write the deck, the whole read-merge-write runs under an
advisory lock and both files land via a temp file plus `os.replace`, so a
concurrent reader never sees a half-written deck and two writers cannot lose
each other's work. On a mount where locking does not work the write is still
atomic.

Read-only views: `check` renders a board section, `sitrep` one board line.

`scripts/board_bridge.py` ensures the deck and its HTML match `tasks/`, and
does nothing else. Until v3 it also replayed the session task ledger at close:
every id without a `TaskCompleted` event became a task note. Status changes
other than completion fire no events, so abandoned and renamed todos qualified
too, and `tasks/` filled with cards nobody wrote. A task note is now written
only when someone asks for one — `/adjudant status --capture-task`, or your own
hand. The ledger still lives in `$TMPDIR` for the statusline; nothing reads it
into the vault.

## Merge provenance (refresh-without-clobber)

Task-seeded cards carry `source: task`. On re-seed, a `source: task` card whose
backing `tasks/*.md` note disappeared is parked in `icebox` (never deleted);
cards **without** task provenance (hand-added in the board UI, or from a
pre-provenance deck) keep their current column untouched.

## Fail conditions

- No breadcrumb at cwd → the target dir is treated as the vault project dir itself
  and the board scaffolds there (deliberate scaffold-anywhere escape hatch; run
  `/adjudant connect` first if you wanted the breadcrumb flow).
- `--project`/`--all` with no resolvable vault → exit non-zero ("pass `--vault PATH`
  or run from a connected project").
- `--project <slug>` not found → exit non-zero, listing the available slugs.
- `--dest`/`--data` combined with `--all` → exit non-zero.
- Template missing / `BOARD_DATA` markers absent → exit non-zero (don't emit a
  half-written board).

## What board does NOT do

- No live sync to GitHub issues / Jira / a database — the JSON is the source of truth.
- No multi-user/server backend — single-file, local, disk-or-browser persistence.
- No auto-status-writeback to `tasks/` notes (seeding is one-way: `tasks/` → board).
