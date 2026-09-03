# Adjudant internals

How adjudant itself is built: the hook wiring, the verb-to-helper map, and the
environment probes. Load this when the question is about adjudant's own
machinery. Running a verb does not need it - `SKILL.md` routes, and the verb's
own reference file describes the job.

## Environment awareness

Adjudant probes for optional environments and never drives them. Each is
declared once in `scripts/build-profile.json` under `capabilities`: an `id`, a
`probe` executable looked up on PATH, a reference doc, and the three lines the
consumers render (`status`, its briefing, and the SessionStart banner). A build
that declares none prints nothing and loads nothing. Nothing here executes the
probe; presence is the whole signal. Load a capability's reference doc only
when its territory comes up.

## Python helper layer

Every file-touching verb is backed by a Python helper. Helpers follow the `.claude/adjudant` breadcrumb automatically — pass `--project-dir` pointed at the code project root and the helper auto-resolves to the vault project. Cross-machine portable via `vault_name` fallback resolution.

| Verb | Helper | Output |
|---|---|---|
| `connect` | `connect.py` | idempotent project init (5 steps + projects-index row) |
| `clean` | `clean.py` + `_vault_write.py` + `_vault_walk.py` | preview/apply with backup, every live write through `VaultWriteGuard` (rewrite and remove only, never create). `--deep` adds the structural drift catalog, reported and never applied |
| `dream` | `dream.py` + `_vault_walk.py` | JSON content/staleness comparator catalog (analysis phase); judge + plan + execute via superpowers |
| `status` | `status.py` + `_vault_walk.py` | make-current phase (brief refresh, handoff mirror, projects-index row) then one JSON report: three bands, orientation, compliance, §4 naming, advisor pulse. `--no-sync` for a read-only pass |
| `board` | `board.py` + `_vault_walk.py` | scaffold per-project `board-data.json` + a self-contained `board.html`; resolves any project by slug (or `--all`) via `enumerate_projects`. Refresh-without-clobber: re-seeding from `tasks/` merges, preserving dragged columns (idempotent; `--force` rebuilds with a `.bak`). `status` prints per-column counts |

`_vault_walk.py` is the shared primitives module (frontmatter, wikilinks, tags, vault index, vault/project resolvers, schema constants). Read-only CLI smoke-test: `python3 _vault_walk.py --project-dir PATH [--vault-dir PATH]`.

`_template_schema.py` parses `templates/*.md` into the schema at import, and `_vault_walk.FIELD_SCHEMA` is the result. `_render.py` writes with it: `render(kind, fields, body)` for a full note, `frontmatter(kind, fields)` for a writer that supplies its own body (the handoff mirror mirrors `.remember/`). Every mechanical vault write goes through one of the two. A missing template raises; there is no fallback copy to substitute.

## Hooks

This plugin registers 11 hook entries across 10 events (vault-aware only):

| Event | Script | Purpose |
|---|---|---|
| SessionStart | `hooks/scripts/session-start.sh` | Leads the context block with the **voice directive** (see below) — the contract's fourth and widest surface, and the only one that reaches the chat rather than a file; off via `voice: off` in the breadcrumb or `ADJUDANT_VOICE_DISABLE=1`. Then: discover vault, detect AGENTS.md+CLAUDE.md, init/resume session note; stamp the Claude Code conversation UUID into `session_id:` (list, idempotent on resume); no resumed marker on `compact`/`clear` sources; writes the resolved session-note path to `$TMPDIR/adjudant-session-{session_id}` for the per-turn hook to read (the intent nudge itself moved to UserPromptSubmit — this hook runs before the session has a purpose to record, and re-runs on every resume and compact, so it nagged early and repeatedly); renders a board status line when a board exists, plus one banner line per declared capability whose probe is on PATH, on `startup` only (`scripts/_profile.py --session-banner`); echoes the handoff freshness banner (and a STALE warning) so a red handoff cannot sit unseen |
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
| Repo docs, templates, SKILL.md | validators 23 + 28, at commit | full lexicon, all named patterns |
| Rendered CLI output | validator 29, at commit | same, over every string literal the helpers can print, via `ast` so comments and identifiers are not hits |
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

### The advisor (v2)

The proactive layer, and the inverse of the voice directive's defaults:
**opt-in, off until asked for**. Its state is deliberately visible twice —
`advisor: on` in the breadcrumb (machine-read, repo-committed, syncs across
machines) and a marker line in AGENTS.md (context-injected by the harness,
readable by any human at project root). `scripts/status.py` owns both
surfaces so they cannot drift; the `advisor-wiring` validator fails the
build if the contract doc, the SessionStart banner, or the marker drops out.

When on, SessionStart emits a banner (120-token cap, tested, placed after
Voice: the register governs how observations are said before anything
decides what to notice) pointing at `reference/advisor.md` — the standing
contract: notice tasks/gaps/gaffes/stale-context, tier them (urgent inline,
routine to the board or the next `status`), dry wit, `❦` lead-in, raise-once
dedup, never auto-write. The intelligence is the model's; hooks stay
mechanical.

Two helper subcommands serve it, both riding existing machinery:

- the pulse, folded into every `status` report — read-only context-integrity check: `freshness_report`
  (expired / dangling-supersession / unbounded facts), dream's
  dangling-scope detector, the handoff NEXT, and the five most recent
  decisions, with a `quiet` verdict so a healthy project produces silence.
  Run at resume when the mode is on; it rides `status`'s `advisor.pulse` key.
- `status.py --capture-task --title … [--note …]` — lands an approved
  suggestion as `tasks/{slug}.md` via `templates/task.md` and lets
  `ensure_board` seed the card. Dedup by slug; an existing note is never
  touched. The same rail the session-end bridge uses, so a captured task is
  indistinguishable from any other.

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

| PreToolUse (Write) | `hooks/scripts/pretooluse-schema-gate.py` | Validates the proposed frontmatter of a Write landing under the resolved vault project against `FIELD_SCHEMA`, via the same detector `status` reports and `clean` feature 4 repairs; blocks (exit 2, stderr naming the expected shape) on a missing required field or on both `type:` and `node_type:` being set, so the model corrects within the same turn; allows unknown fields silently, since a PreToolUse hook's stderr only reaches anyone on a non-zero exit and `clean` strips them after the fact anyway; fails open on anything infrastructural (no breadcrumb, unresolvable vault, unparseable payload, import failure); skips `brief.md`, session notes, `_legacy/` at any depth, and the `_`-prefixed system files (`_handoff.md`, `_index.md`, `_iteration.md`). Does not check status values. Write-only: an Edit payload carries no resulting file to judge. Also runs the voice gate on the same surface: a write carrying an opener, closer or glazing phrase (`_voice.BLOCKING_PHRASES`) is blocked with the phrase named, since nothing sweeps vault prose after the fact the way clean sweeps frontmatter. Schema failures report first — those are objectively wrong, voice is a judgment. Narrower than validator 24 on purpose: blocking a write is expensive, so a merely banned word passes here and fails the build instead |
| PostToolUse (Write) | `hooks/scripts/posttooluse-vault-log.py` | Append vault file creation entries to today's session log; stamp `source_session: <uuid>` into the new file's frontmatter only when the breadcrumb opts in via `stamp_source_session: true` (default off — the session log already records the mapping; skips session notes / `_handoff` / `_index*` / `_iteration`). Spawns no subprocess: v3 deleted the job-0 branch that nudged `board_bridge.py --ensure-only` on a task-note change, which auto-seeded a board nobody asked for, and the matcher narrowed from `Write\|Edit` back to `Write` with it |
| PostToolUse (Bash) | `hooks/scripts/posttooluse-commit-log.py` | Self-gated commit logging (async; the `if: Bash(git commit *)` filter is defense in depth): append `- HH:MM · commit: {subject}` to today's session log; on `release(<plugin>): vX.Y.Z` subjects also scaffold `releases/v{X.Y.Z}.md` + an index row, never overwriting an existing note |
| PreCompact | `hooks/scripts/precompact.py` | Writes nothing. Registered to drain stdin: an unread PreCompact payload EPIPEs the harness writer. The handoff mirror it used to run is gated behind `--sync-only`, the flag only SessionEnd passes, so a session that compacts three times no longer rewrites `_handoff.md` three times and again at the end. Tradeoff: a session that dies before SessionEnd keeps the previous handoff, and that handoff's own STALE flag says so. Writes no session-log marker either: the `paused (compaction)` tombstone went in v3 |
| PostCompact | `hooks/scripts/postcompact.py` | Writes nothing. It appended `- HH:MM · compacted: {gist}`, the harness summary clipped at 160 chars, which put fragments of raw model reasoning in the vault. Registered only to drain stdin: an unread PostCompact payload EPIPEs the harness writer |
| TaskCreated / TaskCompleted | `hooks/scripts/task-ledger.py` | One script wired to both events (async): append one JSONL entry per event to the TMPDIR session task ledger; zero vault writes, in-session or after — the ledger is read by the statusline and dies with the TMPDIR |
| SessionEnd | `hooks/scripts/sessionend.sh` | The one handoff writer: runs `precompact.py --sync-only` to mirror `.remember/remember.md` (or `now.md`) into `_handoff.md` with a freshness header (traffic light · age · NEXT · stale flag), never mirroring a blank source over a populated handoff; then reseeds an existing board via `board_bridge.py --ensure-only`. Writes no session-log marker: `session ended` went in v3 with the rest of the lifecycle markers |
| Stop | `hooks/scripts/stop-canary.py` | The drift canary. SessionStart states one rule (end every message with a rare codeword) and never restates it; this reads `last_assistant_message` and records a hit or a miss in `$TMPDIR/adjudant-canary-{session_id}.json`. First miss blocks once and asks the model to re-read its instructions; later misses are recorded only, since coercing compliance past that point manufactures the appearance of health. The miss is kept either way, so a block that works cannot erase the evidence. The per-turn hook reports the tally; no vault writes |

Universal drift-defense hooks (git safety, voice checks, etc.) live in hookify, not here.
