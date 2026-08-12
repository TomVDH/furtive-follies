# /adjudant check

Read-only summary. Never writes. Backed by `check.py` which scans the project mechanically and emits structured JSON; this skill consumes the JSON and renders the 3-section block.

## Target `[vault|repo|all]`

`check` takes an optional target; default is `vault` (the sections below —
exact back-compat, `/adjudant check` is unchanged).

- **`repo`** — audit the *code repo* instead of the vault. Runs
  `python3 "$(dirname "$0")/../../../scripts/repo_scan.py" --project-dir "$REPO_ROOT"`
  and renders the JSON: a version-coherence table (marketplace.json ↔ each
  plugin.json), a symlink-integrity matrix (skills-bearing plugins only), a
  registration check (every plugin registered, every `source` path resolves),
  a stale-plan list, the repo-root context-file + `@AGENTS.md` import check, and
  a single `drift_items` score. Per-plugin context files are shown
  *informational* (not counted). Repo conventions live in
  `reference/repo-standards.md`. Never writes.
  - `token_budget`: per-surface context cost (`file`, `tokens`, `budget`,
    `over`) plus `total` and `over_count`, from `token_budget.py`. Render as
    one line when `over_count` is 0 (`context: ~{total/1000}k tokens across
    {n} surfaces`), and list the offenders when it is not. Report only: it
    never fails a build, and an over-budget surface is a prompt to look, not
    an error.
- **`all`** — run the vault check *and* the repo scan; render both blocks.

Repo ops use `--project-dir` as the repo root directly (no breadcrumb — the repo
*is* the project dir).

## The 3 features (locked spec)

1. **Current project state** — brief summary (title, type, status), recent sessions/decisions (last by date), handoff freshness (timestamp + delta vs now)
2. **Folder counts** — non-index `.md` counts per standard folder (decisions, sessions, dreams, notes, …)
3. **Drift signal** — date + item count from this project's latest dream report (`dreams/{date}-dream.md`); full audit is `/adjudant dream`

## Run

```bash
python3 "$(dirname "$0")/../../../scripts/check.py" \
  --project-dir "$PROJECT_ROOT" \
  --vault-dir "$VAULT_PATH" \
  --out /tmp/check-{slug}.json
```

JSON output shape (top-level keys):
- `project` — brief fields (slug, project_type, status, title, created, updated, codename)
- `counts` — non-_index .md per common folder (decisions, sessions, dreams, notes, etc.)
- `recent` — last_session, last_decision, last_dream (YYYY-MM-DD)
- `handoff` — two clocks, deliberately. `updated` + `stale_hours` are the **mirror
  clock**: when the handoff was last written. Every SessionEnd/PreCompact stamps
  it, so a mirror of an empty buffer still reads fresh — diagnostic only, never
  the answer to "are we drifting?". `light` / `age` / `next` / `stale` are the
  **activity clock** from `_handoff_freshness` (remember dailies + session-note
  markers) — the same sensor sync and the hooks render into the handoff banner.
  **Render the activity clock.** When the two disagree the handoff has not been
  re-synced since the last real work: say so rather than picking one
- `drift_signal` — latest dream date + drift_items count if parseable
- `board`: `{present, columns, updated, stale}`. Cards counted per deck column id
  (custom lanes included, empty lanes shown as 0), never a hardcoded status list;
  `stale` is true when any `tasks/*.md` mtime is newer than the deck file. No board
  or unreadable deck: just `{present: false}`
- `status` — declared vs. machine-suggested lifecycle status: `declared`, `declared_valid`,
  `last_session`, `days_quiet`, `suggested`, `reason`, `nudge`, `zone`, `zone_matches`
- `schema` — frontmatter drift per `FIELD_SCHEMA`: `checked`, `unchecked` (no block /
  parse error / non-canonical type — those are out of scope for automated repair), `flagged`, `counts`
  (`missing_required`, `unknown_fields`, `status_invalid`, `type_conflict`,
  `epistemic_invalid`), `samples` (capped at 20, each with `file`, `type`, and the
  offending keys/values)
- `freshness` — what valid epistemic declarations MEAN today (shape problems are
  `schema`'s): `expired` (`valid_until` past, with `days_expired`),
  `dangling_supersession` (`superseded_by` target resolves to no file),
  `dated_unbounded` (`freshness: dated` with no validity window), `counts`
  (adoption per field). Vault-standards section 10 owns the vocabulary
- `environment` — capability probes: `obsidian_cli` (official CLI on PATH)

## Render

> Render the JSON `cost` block as one line: `cost: ~{est_read_tokens/1000}k tokens, {files} files`.

Output a single rendered block:

```
## Project — {slug}

{title}
Type: {project_type} · Status: {status} · Codename: {codename or none}
Created: {created} · Updated: {updated}

## Activity

- Last session: {last_session}
- Last decision: {last_decision}
- Last dream:    {last_dream}
- Handoff:       {light} {age}{" · NEXT: " + next if next}{" · STALE" if stale}
                 (mirrored {updated}{" — not re-synced since" if disagrees})
- Counts:        {decisions} decisions, {sessions} sessions, {dreams} dreams, {notes} notes
- Board:         {board.columns as "{id}: {n}" pairs, deck order}{" · stale" if board.stale}
- Schema:        {schema.checked} checked, {schema.flagged} flagged
                 ({counts as "{n} missing-required, {n} unknown-field, {n} status, {n} type-conflict, {n} epistemic", nonzero only})
- Freshness:     {sum of counts} declared{" — all current" when expired/dangling/unbounded all empty;
                 else "{n} expired, {n} dangling supersession, {n} dated-unbounded", nonzero only}
                 (skip the line entirely when no note declares any epistemic field)

## Drift signal

{drift_signal.drift_items} items per dream {drift_signal.date}
  (or "Run /adjudant dream — no recent diagnostic" if absent)
```

Adapt phrasing to be conversational; the shape above is the data layout, not a rigid template.

Shape (voice.md §Shape): open the rendered block with the most decision-relevant fact
(status plus freshness beats the title), and close with exactly one next step (the
pending board reseed or `/adjudant dream`, whichever the data
points at). Conditional nudges render above that final line, never after it.

Skip the Board line entirely when `board.present` is false. When `board.stale` is true,
the deck lags the task notes: mention that a reseed is pending (`/adjudant board` or the
next ambient refresh), no alarm.

### Status nudges (conditional)

- If `status.suggested` is set, render one line reporting the mismatch: "brief says {status.declared}, looks {status.suggested}: {status.reason}".
- If `status.nudge` is set, render the nudge as its own line.
- If `status.zone_matches` is false, flag the mismatch: the declared status doesn't match the vault zone the project actually sits in.
- If `schema.flagged` > 0, render one line: "{flagged} files off schema → run /adjudant tidy (strip/migrate after preview)". Skip the Schema activity line entirely when flagged is 0 and every file checked out clean.
- If `freshness.expired` or `freshness.dangling_supersession` is non-empty, render one line: "{n} declared facts need review (expired validity / dangling supersession) → run /adjudant dream". Judgment-heavy by design: expiry review is dream territory, never tidy's.

## Inputs

None. Operates on the project resolved from `.claude/adjudant` breadcrumb at cwd.

## Fail conditions

- No breadcrumb at cwd and arg isn't a vault project dir → exit non-zero pointing at `/adjudant connect`
- Vault path unreachable → exit non-zero with message

## See also

- `scripts/check.py`, `scripts/test_check.py`
- `reference/dream.md` — full diagnostic; use when `drift_signal` looks elevated
