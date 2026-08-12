# Adjudant internals

How adjudant itself is built: the hook wiring, the verb-to-helper map, and the
environment probes. Load this when the question is about adjudant's own
machinery. Running a verb does not need it - `SKILL.md` routes, and the verb's
own reference file describes the job.

## Python helper layer

Every file-touching verb is backed by a Python helper. Helpers follow the `.claude/adjudant` breadcrumb automatically — pass `--project-dir` pointed at the code project root and the helper auto-resolves to the vault project. Cross-machine portable via `vault_name` fallback resolution.

| Verb | Helper | Output |
|---|---|---|
| `connect` | `connect.py` | idempotent project init (5 steps + projects-index row) |
| `sync` | `sync.py` | brief refresh + handoff mirror + projects-index row refresh |
| `tidy` | `tidy.py` + `_vault_walk.py` | preview/apply with backup |
| `dream` | `dream.py` + `_vault_walk.py` | JSON content/staleness comparator catalog (read-only analysis); Claude renders an advisory findings report and enacts nothing on its own |
| `check` | `check.py` + `_vault_walk.py` | JSON status snapshot |
| `sitrep` | `sitrep.py` + `_vault_walk.py` | JSON orientation briefing (recent activity, NEXT, vault location + counts); Claude renders ELI5 |
| `board` | `board.py` + `_vault_walk.py` | scaffold per-project `board-data.json` + a self-contained `board.html`; resolves any project by slug (or `--all`) via `enumerate_projects`. Refresh-without-clobber: re-seeding from `tasks/` merges, preserving dragged columns (idempotent; `--force` rebuilds with a `.bak`). `status` prints per-column counts |

`_vault_walk.py` is the shared primitives module (frontmatter, wikilinks, tags, vault index, vault/project resolvers, schema constants). Read-only CLI smoke-test: `python3 _vault_walk.py --project-dir PATH [--vault-dir PATH]`.

## Hooks

This plugin registers 10 hook entries across 9 events (vault-aware only):

| Event | Script | Purpose |
|---|---|---|
| SessionStart | `hooks/scripts/session-start.sh` | Leads the context block with the **voice directive** (see below) — the contract's fourth and widest surface, and the only one that reaches the chat rather than a file; off via `voice: off` in the breadcrumb or `ADJUDANT_VOICE_DISABLE=1`. Then: discover vault, detect AGENTS.md+CLAUDE.md, init/resume session note; stamp the Claude Code conversation UUID into `session_id:` (list, idempotent on resume); no resumed marker on `compact`/`clear` sources; writes the resolved session-note path to `$TMPDIR/adjudant-session-{session_id}` for the per-turn hook to read (the intent nudge itself moved to UserPromptSubmit — this hook runs before the session has a purpose to record, and re-runs on every resume and compact, so it nagged early and repeatedly); renders a board status line when a board exists; echoes the handoff freshness banner (and a STALE warning) so a red handoff cannot sit unseen |
| UserPromptSubmit | `hooks/scripts/user-prompt-reminder.sh` | Two nags with inverse audiences, branched on the breadcrumb. **Unlinked project:** smart-fire vault reminder when the prompt has vault-y keywords (at most once per session). **Linked project:** the intent-line nudge — fires from the second prompt on (by then the session has a purpose; firing at the first is the mistimed nag this replaced), at most once per session, and only while the placeholder stands, so writing the line ends it. Reads the session-note path from the pointer SessionStart drops rather than re-deriving it — a second copy of the zone-aware lookup would drift |
### The voice contract, and where it bites

Two sources feed `reference/voice.md`. **Shape** comes from the `i-have-adhd`
plugin: ordering, step counts, restating state, capping lists. **Texture**
comes from the `no-ai-slop` skill: banned words and sentence patterns. They do
not overlap, so neither has to win. Both stay soft dependencies; the contract
holds with neither installed.

Four surfaces carry it. Three are mechanical and read `scripts/_voice.py`:

| Surface | Enforced by | Scope |
|---|---|---|
| Repo docs, templates, SKILL.md | validators 21 + 30, at commit | full lexicon, all named patterns |
| Rendered CLI output | validator 31, at commit | same, over every string literal the helpers can print, via `ast` so comments and identifiers are not hits |
| Prose written into the vault | the PreToolUse gate, at write time | `BLOCKING_PHRASES` only: openers, closers, glazing |

The fourth is the widest and is not enforcement at all. The three above reach
files; **the chat is where adjudant is actually read**, and nothing was setting
its register. i-have-adhd ships `disable-model-invocation: true`, so its ten
rules stay inert unless someone types `/i-have-adhd`. SessionStart is the only
component that speaks into every session regardless, so it leads its context
block with a one-line voice directive that governs the whole conversation, not
just the files touched in it.

That line is capped at 120 tokens and the cap is tested. It loads every
session on top of a context block that already costs, and voice.md is the
cautionary tale: a 600-token budget with 7 characters of headroom. Opt out per
project with `voice: off` in the breadcrumb (syncs across machines) or per
machine with `ADJUDANT_VOICE_DISABLE=1`.

The gate is deliberately the narrowest of the three. It refuses a write, and a
false positive there wedges the model mid-turn, so it only blocks phrases with
no technical reading at all. A merely banned word (`robust`) passes the gate
and fails the build later instead. Vault prose is the surface that most needs
it: a note lives for years and nothing sweeps its sentences afterwards, since
tidy repairs frontmatter and structure only.

Every pattern in `SLOP_PATTERNS` was measured against adjudant's own docs
before admission and scored zero false positives. Three source rules were
rejected on that evidence and stay judgment: no-ai-slop's often-empty adverbs
(conditional by definition), the colon-reveal pattern (20 false positives on
ordinary labels like `Read-only views:`), and everything structural. Encoding
judgment as a build failure teaches people to silence the build.

One lexicon exemption: `harness`, a noun here (the Claude Code harness, the
test harness) rather than the marketing verb the skill bans. It is recorded in
`TECHNICAL_EXEMPTIONS` with its reason rather than left as an absence a later
merge would quietly undo. Where the sources conflict on em dashes — no-ai-slop
allows one or two in a long draft — adjudant allows none and wins.

| PreToolUse (Write) | `hooks/scripts/pretooluse-schema-gate.py` | Validates the proposed frontmatter of a Write landing under the resolved vault project against `FIELD_SCHEMA`, via the same detector `check` reports and `tidy` feature 5 repairs; blocks (exit 2, stderr naming the expected shape) on a missing required field or on both `type:` and `node_type:` being set, so the model corrects within the same turn; allows unknown fields silently, since a PreToolUse hook's stderr only reaches anyone on a non-zero exit and `tidy` strips them after the fact anyway; fails open on anything infrastructural (no breadcrumb, unresolvable vault, unparseable payload, import failure); skips `brief.md`, session notes, `_legacy/` at any depth, and the `_`-prefixed system files (`_handoff.md`, `_index.md`, `_iteration.md`). Does not check status values, but DOES block malformed epistemic declarations (vault-standards §10) — those have no legacy values to migrate. Write-only: an Edit payload carries no resulting file to judge. Also runs the voice gate on the same surface: a write carrying an opener, closer or glazing phrase (`_voice.BLOCKING_PHRASES`) is blocked with the phrase named, since nothing sweeps vault prose after the fact the way tidy sweeps frontmatter. Schema failures report first — those are objectively wrong, voice is a judgment. Narrower than validator 24 on purpose: blocking a write is expensive, so a merely banned word passes here and fails the build instead |
| PostToolUse (Write\|Edit) | `hooks/scripts/posttooluse-vault-log.py` | Append vault file creation entries to today's session log; stamp `source_session: <uuid>` into the new file's frontmatter only when the breadcrumb opts in via `stamp_source_session: true` (default off — the session log already records the mapping; skips session notes / `_handoff` / `_index*` / `_iteration`); matcher widened to `Write\|Edit` so a task-note change under `tasks/` nudges the board via `board_bridge.py --ensure-only` (log + stamp jobs stay Write-only) |
| PostToolUse (Bash) | `hooks/scripts/posttooluse-commit-log.py` | Self-gated commit logging (async; the `if: Bash(git commit *)` filter is defense in depth): append `- HH:MM · commit: {subject}` to today's session log; on `release(<plugin>): vX.Y.Z` subjects also scaffold `releases/v{X.Y.Z}.md` + an index row, never overwriting an existing note |
| PreCompact | `hooks/scripts/precompact.py` | Mechanical, no model calls (5s budget): append enriched pause tombstone (`· next: …`) + mirror handoff with a freshness header (traffic light · age · NEXT · stale flag); a blank `.remember` source is never mirrored over a populated handoff |
| PostCompact | `hooks/scripts/postcompact.py` | Append `- HH:MM · compacted: {gist}` (single line, first 160 chars of the compaction summary) to today's session log; an empty or missing summary writes nothing |
| TaskCreated / TaskCompleted | `hooks/scripts/task-ledger.py` | One script wired to both events (async): append one JSONL entry per event to the TMPDIR session task ledger; zero vault writes in-session, the SessionEnd bridge replays survivors |
| SessionEnd | `hooks/scripts/sessionend.sh` | Append `session ended` marker only when something was logged since the last hook marker + sync handoff to vault; then bridge ledger survivors into `tasks/` notes and birth/reseed the board via `board_bridge.py` |

Universal drift-defense hooks (git safety, voice checks, etc.) live in hookify, not here.
