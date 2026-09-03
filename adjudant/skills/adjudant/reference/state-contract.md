# State contract

Files and lines outside adjudant that read adjudant's output. Anything listed
here is a published interface: moving or reformatting it silently breaks a
consumer that has no test in this repo.

## Consumer: the statusline

`~/.claude/statusline-v2.sh`, a symlink into
`~/Library/Mobile Documents/com~apple~CloudDocs/Projects/IDE/claude/`. It lives
in iCloud and syncs to both machines, so it is edited once and lands on both.

Paths below are relative to the vault project directory unless they say
otherwise. `{project}` is that directory, `{repo}` is the code root, `{slug}`
is its basename, and the lifecycle folder (`active`, `paused`, `finished`,
`archive`, plus the pre-v3 shapes resolved after them) is resolved by
probing, not read from the breadcrumb.

| It reads | For |
|---|---|
| `{repo}/.claude/adjudant`, `vault_path:` and `slug:` | vault location, project name |
| `{repo}/.claude/adjudant`, `stale_after_days:` | the threshold for both the lifecycle hint and the dream age (30 when absent or non-numeric) |
| `{vault}/projects/{active\|paused\|finished\|archive}/{slug}/` (dir exists) | lifecycle folder, rendered as a badge for anything but `active` |
| `{vault}/projects/{slug}/`, `{vault}/projects/_fridge/{slug}/`, `{vault}/projects/_archive/{slug}/` | pre-v3 shapes, probed after the four; `_fridge` reads as paused, `_archive` as archive |
| `$TMPDIR/adjudant/{slug}-{digest}/clean-preview`, directory exists | "cleaning" state |
| `$TMPDIR/adjudant/{repo basename}-{digest}/repo-tidy-preview`, directory exists | "repo-tidying" state |
| `{project}/.adjudant-remise-preview`, directory exists | "remising" (reserved, nothing writes it yet) |
| `brief.md` frontmatter, `status:` | lifecycle drift, read against the newest session date |
| newest `sessions/{YYYY-MM-DD}.md`, the filename only | how long the project has been quiet |
| `_handoff.md`, the first line matching `(🔴\|🟡\|🟢).*handoff age` | freshness tier and the age string, plus `🔴 **STALE**` anywhere in the file |
| `_handoff.md` frontmatter, `updated:` | the freshness fallback for a handoff with no banner |
| `board/board-data.json`, `"column":` on each card | open count, in-flight count, week-over-week direction |
| `board/board.html`, file exists | the board label becomes an OSC 8 link |
| `tasks/*.md` mtimes against the deck's | board lag |
| newest `dreams/{YYYY-MM-DD}.md` or `dreams/{YYYY-MM-DD}-dream.md`, the filename only | dream age |
| `$TMPDIR/adjudant-task-ledger-{session_id}.jsonl`, `.id` and `.status` per line | in-flight task count |

## Rules

0. The scratch key is `{name}-{digest}`, where digest is the first eight hex
   characters of a SHA-256 of the project's RESOLVED path. The name alone was
   not unique: two vaults each holding a project called `demo` shared one
   preview and one backup root, so one project's apply could read the other's
   preview and one project's rotation could delete the other's only pre-change
   backup. The statusline globs the suffix rather than recomputing the hash,
   because a subshell per render for a directory that is usually absent is not
   worth it. Changing how the key is built breaks that glob.


1. The handoff traffic-light line keeps its exact format. It is the one surface
   where an emoji carries meaning rather than decoration, and the statusline
   reads those emoji without ever printing one.
2. The dream report keeps its dated filename, in either spelling. The finding
   count is optional: the statusline greps `N drift item` anywhere in the file
   and appends it only on a match, so a report without the phrase still ages
   correctly.
3. The task ledger keeps its `$TMPDIR` path and JSONL shape. Only its replay
   into vault task notes was removed.
4. Scratch is `$TMPDIR/adjudant/{key}/{kind}`, where `{key}` is the basename of
   the directory being operated on with every character outside
   `[A-Za-z0-9_.-]` collapsed to a hyphen, ends trimmed, empty becoming
   `project`. Adding a kind is safe; renaming one is not. Two paths are named
   exceptions to it — see below — and they are the only two.
5. Anything added to this table needs the statusline updated in the same
   change. Nothing in this repo can catch that break.
6. The lifecycle folder is the project's lifecycle state; `zone_of()` is
   authoritative and nothing compares it against a declared status anymore.
   A v3 brief writes no `status:` field. Where one survives from before v3,
   it is read only to suggest a stale/active transition, never to grade the
   folder. A project's folder and the newest file in its `sessions/` are the
   two inputs to lifecycle drift.

## In-vault backups: the two named exceptions

Rule 4 sends scratch out of the vault. Two backup paths deliberately stay in,
and v3 confirmed both rather than leaving the rule stated absolutely while they
survived:

| Path | Written by | Fires on | Bound |
|---|---|---|---|
| `{project}/board/.bak/board-data-{ts}.json` | `board.py`, `backup_deck` | an explicit `board --force` or `board --data`, never the ambient reseed | newest 5 |
| `.{name}.{ts}.bak` beside an `--out` target | `graph.py`, `backup_out` | `draw --out … --force` over a file that already exists | newest 5 per target |

Three reasons they are not scratch, all three needed:

- **What they hold is the user's own content, not a derived preview.** A deck
  carries cards hand-added straight to `board-data.json` and lane placement
  that exists nowhere else; an `--out` target is whatever the operator pointed
  `--force` at. Neither is rebuildable from the vault.
- **They are the undo for a destructive command someone typed**, not a
  by-product of a routine pass. A project that never runs one never grows them.
- **The vault syncs across machines and `$TMPDIR` does not.** A deck replaced
  on one machine and missed until the next day on the other would have its only
  copy in a temp dir that is already gone.

Both are dot-prefixed, so Obsidian never lists them, the `rglob("*.md")`
walkers never index them, and `check`/`clean` never report them as schema-less
notes. Both rotate, because an unbounded exception is just the growth this
release exists to stop: `backup_out` was uncapped until v3.

Nothing else adjudant writes into a vault project is a backup. A third belongs
in this table or in `$TMPDIR`, and either way the code and this section move
together — `test_board.py` and `test_graph.py` each assert their own path is
named here.
