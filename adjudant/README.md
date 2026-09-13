# Adjudant

*Every unit has someone who keeps the records straight. Now your project does too.*

Adjudant runs an Obsidian vault from inside a code project. The vault holds the
project's long-term memory: session notes, decisions, a handoff, and a brief.
Hooks keep it current while you work. One command drives it: `/adjudant`.

Adjudant also ships two surfaces that read the same state: a self-hosted kanban
board and the Claude Code statusline.

**New here? Read the [walkthrough](GUIDE.md).** This page is the reference.

## Install

```
/plugin marketplace add TomVDH/furtive-follies
/plugin install adjudant
```

Link the project once:

```
/adjudant connect
```

`connect` asks where the vault lives. It writes the answer to `.claude/adjudant`.
Every other verb reads that file. You do not type the path again.

<!-- VERBS:TABLE:START -->
## The five verbs

| Verb | What it does |
|---|---|
| `/adjudant connect` | Onboards a project and asks where it lives. |
| `/adjudant status [vault\|repo\|all] [--no-sync]` | Reports where you are, what is wrong, and what is stale. |
| `/adjudant clean [vault\|repo\|all] [--deep] [--folder <path>]` | Removes what the vault does not need. |
| `/adjudant dream [--folder <path>]` | Reads the prose and reports what only judgement finds. |
| `/adjudant board [scaffold\|serve\|status] [--project SLUG\|--all] [--from-tasks] [--force]` | Runs a self-hosted kanban. |
<!-- VERBS:TABLE:END -->

Start with `status`. It changes nothing. It tells you what the other verbs do next.

## What each verb costs

`status` and `dream` change nothing. Read the report. Then decide.

`clean` needs a named phase. It does not apply a change until you name that phase.

```
clean detect     lists what it found
clean preview    writes the full proposal outside the vault
clean apply      makes the changes, after a backup
```

The two cleanup verbs form a ladder by risk:

```
clean          routine     mechanical repairs; it creates no vault file
clean --deep   sparing     structural findings, reported for you to judge
dream          as needed   semantic findings; you approve each change
```

`clean apply` deletes retired folder indexes. Adjudant keeps two index surfaces
and retires the rest. Read the preview before you apply.

## How it works

- **One breadcrumb.** `connect` writes `.claude/adjudant` in the code project. The
  file holds the vault path, the vault name, and the tracker. The link survives a
  move between machines.
- **A worktree finds its own breadcrumb.** `.claude/adjudant` is git-ignored, so a
  linked worktree has none. Its `.git` file names the main checkout. The
  statusline reads the breadcrumb from there. The session-start hook links it in
  for every other reader.
- **The template is the schema.** Each file type has one template. Adjudant reads
  the required fields from that template. A write that breaks the shape fails
  before it lands.
- **Ambient by default.** Hooks keep the session note, the handoff, and the board
  current. A session note appears on the first real write, not when a session
  opens. You rarely call these verbs by hand.
- **Bounded cost.** `dream`, `clean --deep` and `status all` estimate their cost
  first. They ask before they pull a large vault into the conversation.
- **A drift canary.** Session start names one rare word. Adjudant checks each reply
  for it. A model that drops a one-word instruction has stopped following
  instructions. Start a fresh session at that point.

## Work items

Adjudant does not track work items in the vault. Where a repo uses
[beans](https://github.com/hmans/beans), beans owns the work items and adjudant
keeps the rest: sessions, decisions, the brief, the handoff. One tracker per
repo. The breadcrumb key `tracker: beans` says which one.

Beans are markdown files in `.beans/`, tracked in git. A bean created on a
feature branch travels with that branch and reaches `main` with the merge.
Adjudant reads them, and never writes one.

**The branch rule.** In a beans-tracked repo:

- A `feature` bean works on `feature/<bean-id>`, checked out in
  `.worktrees/<bean-id>`. The main checkout stays on `main`.
- A `task` or `bug` commits on `main`, or on its in-progress parent's branch.
- An `epic` is a container. It gets no branch.
- Merge-back is a pull request.

Session start states the rule in one line. `connect` writes it into a new
project's `AGENTS.md`. `status` reports drift. The statusline shows one glyph.
Nothing blocks a commit; the report is the guard.

## The board

`/adjudant board` scaffolds one HTML file and serves it from disk. The file is
fully offline: no fetch, no font link, no library. A validator fails the build on
any off-machine reference.

What it shows:

- **Lanes** from the tracker's statuses. A closed lane carries one stamp in its
  heading, `BUILT` or `DROPPED`, and a toggle hides the closed lanes.
- **Cards** with a title, two lines of note, up to three tags, the id, and a
  priority mark. Every typed card carries its type's mark. The type's name is
  printed only on the exceptions, so `task` is not printed a hundred times.
- **A sheet** for one card. Its type mark stands in front of the title. The lane
  rail is a routing slip: the lanes are stations on a line, passed ones inked,
  the current one in the accent. One tap on a station moves the card. The sheet
  shows the full note, pretty or raw, with copy buttons for the id and the body.
- **Two filter rails.** Type keys are words on a rule in their hue, each with a
  ColorSym mark, so a type is known by shape as well as by colour. Tag keys are
  mono identifiers with a leading hash and a count. A text filter sits above.
- **An activity card.** Two time sheets, six weeks each, Touched and Filed. A cell
  per day, tinted by count. Bulk writes go grey. The date lives in the tooltip,
  so nothing moves on hover.
- **Persist board edits?** A drag is held in the browser. Press the control and
  the board writes `board-data.json` to disk too, with a merge on every write.

Both colour schemes are drawn, not derived. The mark has a light drawing and an
inverse dark drawing, and the page picks by `prefers-color-scheme`. A version tag
beside the wordmark names the adjudant that scaffolded the file. The board reads
on a phone; the histogram is not drawn under 900px.

## The vault

```
{vault}/
  Home.md                         generated
  projects/
    active/ paused/ finished/ archive/
      {slug}/
        brief.md  _handoff.md  _index.md
        sessions/  decisions/  tasks/  notes/
        docs/  specs/  releases/  dreams/
```

A folder exists when something is in it. Adjudant creates none up front.

Links carry the project-relative path and never the lifecycle folder. Move a
project from `active/` to `paused/` and every inbound link still resolves.

## The statusline

Adjudant ships the Claude Code statusline. It reads the same breadcrumb, beans
and board the verbs read. Install it once per machine:

```
bash "$(ls -d ~/.claude/plugins/cache/*/adjudant/* | sort -V | tail -1)/statusline/install.sh"
```

The installer writes a small shim to `~/.claude/statusline-v2.sh` and prints the
`statusLine` block for `~/.claude/settings.json`. Each session start points the
shim at the installed plugin copy. A plugin update moves the bar with no other
step.

The bar, left to right:

```
[git] │ [vault · work items] │ [model · effort · context] │ [agent bus] │ [24h cost]
```

Glyphs in the git segment:

| Glyph | Meaning |
|---|---|
| `⎇` | You are on a regular checkout. |
| `⑂` | You are in a linked worktree, not the main checkout. |
| red `⎇` or `⑂` | The repo breaks the branch rule. See below. |

The branch name is a link. Cmd+click opens the folder the session works in: the
worktree inside a linked worktree, the project folder otherwise.

The glyph turns red in a beans-tracked repo when one of three things is true:

1. The main checkout is on a branch other than `main`.
2. You are on `feature/<id>` and that bean is completed or scrapped. The worktree
   outlived its bean.
3. A feature bean is in progress and no `feature/<id>` branch exists.

`/adjudant status` names which one. The colour goes when the repo is back on the
rule.

Glyphs in the work-items slot, when beans owns the repo:

| Glyph | Meaning |
|---|---|
| `◍ 7` | seven open beans |
| `▸2` | two of them in progress |
| `⬡1` gold | one milestone is three quarters done |
| `⬡2` orchid | two epics are three quarters done |
| `✕3` | three open beans are bugs |
| `!1` | one open bean is critical |

After a bean is added, removed, closed or reopened, the count flashes the delta
for eight seconds: `+1`, `−1`, `✓1`, `↺1`. Click the count to open the board.

## At a glance

| | |
|---|---|
| Command | `/adjudant <verb>` |
| Skill | one (`adjudant`); verbs load reference files on demand |
| Hooks | 11 entries across 10 events, all vault-aware |
| Templates | 20 file-type scaffolds + `board.html` |
| Helpers | stdlib-only Python, one per file-touching verb; no build step |
| Statusline | `statusline/`, tested, installed by `install.sh` |
| Drift defense | `python3 scripts/validate.py`: 28 validators, run on pre-commit |
| Tests | `python3 -m unittest discover -p 'test_*.py'` |

Hook wiring and the verb-to-helper map are in
[`reference/internals.md`](skills/adjudant/reference/internals.md).
Frontmatter, folders and naming are in
[`reference/vault-standards.md`](skills/adjudant/reference/vault-standards.md).
Every file the statusline reads is listed in
[`reference/state-contract.md`](skills/adjudant/reference/state-contract.md).

## Voice

Adjudant sets a direct register for the session and holds each surface it writes
to it. Vault writes follow ASD-STE100: one instruction per sentence, active voice,
present tense, under twenty words. The full contract is in `reference/voice.md`.

Turn it off for one project with `voice: off` in `.claude/adjudant`. Turn it off
for the machine with `ADJUDANT_VOICE_DISABLE=1`.

## Pairing

- `hookify`: universal drift-defense hooks, git safety, secret scanning. Adjudant
  leaves those to it.
- `i-have-adhd`: shapes conversational output. Adjudant carries its own copy of
  the rules, so it does not require the plugin.

## License

MIT
