# /adjudant dream

**Advisory content review.** The deepest look adjudant takes at a vault, and the gentlest: it reads the actual prose of your decisions, notes, and sessions, and hands you a findings report. It changes nothing on its own. Read-only, project-scoped, cost-gated.

*Staleness is the enemy.* Dream catches "the doc says X but reality is now Y", then stops and lets you decide.

## What dream is, and is not

- It **surfaces candidates**, it does not act. `dream.py` cannot decide semantics; it emits comparators (`file · line · excerpt`) and Claude judges them into a report.
- It is **project-scoped**: one run reads the current project only (the breadcrumb's project), never the whole vault. Its cost is bounded by that project's prose.
- It is **read-only**. Nothing is written, moved, or deleted unless you name a specific change afterward.

## Cost pre-flight (locked)

Dream is a heavy verb. Before the real scan, run the analyser with `--estimate-only`. If `cost.warn` is true, stop and show the numbers ("dream would pull ~14k tokens into context: 55 files, 220 KB prose") and ask the user to proceed or abort. Proceed only on explicit confirmation. If `warn` is false, run normally and fold the estimate into one line. The default warn threshold is token-frugal; override per project with `cost_warn_tokens:` in `.claude/adjudant`.

```bash
python3 "$(dirname "$0")/../../../scripts/dream.py" \
  --project-dir "$PROJECT_ROOT" --vault-dir "$VAULT_PATH" --estimate-only
```

## Run the scan

```bash
python3 "$(dirname "$0")/../../../scripts/dream.py" \
  --project-dir "$PROJECT_ROOT" --vault-dir "$VAULT_PATH" --out /tmp/dream-scan.json
```

Optional flags: `--today YYYY-MM-DD` (deterministic age math), `--stale-days N` (staleness threshold, default 180), `--include-legacy`.

The JSON catalog carries ten comparator categories:

| Key | What it surfaces |
|---|---|
| `staleness_candidates` | content-type files older than the threshold |
| `supersession_signals` | same-topic decision pairs, older likely superseded |
| `contradiction_pairs` | topically overlapping files with change/negation cues |
| `redundancy_clusters` | near-duplicate notes/docs by token-set similarity |
| `stale_refs` | refs that resolve but point at archived or old targets |
| `orphan_questions` | aged open-loop markers (TODO/OPEN/TBD) never closed |
| `orphan_threads` | aged notes/docs with no inbound wikilinks |
| `unacted_decisions` | active decisions whose consequence shows no action |
| `documentation_gaps` | sessions with work but no decision, stubs, brief gaps |
| `dangling_scopes` | brief milestones or questions never touched in a session |

Each entry carries enough context (`file`, `line`, `excerpt`) for Claude to judge without re-reading every file. `meta.summary` gives per-category counts.

## The findings report

Read the JSON, judge each candidate, and render **one report** in three plain sections. No preamble, no fix applied.

1. **What was found** — the concrete signals, grouped by category, each with `file · line · excerpt`. State it plainly.
2. **Suspicions** — your read on which findings are likely real versus noise, and why. This is judgment, offered as suspicion, not verdict.
3. **Worth a closer look** — the few places a human should check directly: a decision that may be quietly reversed, two notes that may be the same thing, an open question that may already be answered.

End the report with this line, verbatim:

> These findings are ephemeral. Store them, act on them, or let them pass into the aether.

Then **stop.** The report is the whole verb. Wait for the user's response before touching anything.

## After the report, only on an explicit response

- **"store them"** → write a `dream-report` note (see `templates/dream-report.md`) into the project's `dreams/` folder as `{YYYY-MM-DD}.md`, capturing the three sections. One deliberate write, schema-checked like any other.
- **"act on X"** → make only the specific changes the user names, with ordinary edits (still schema-gated by the write hook). Mark a decision superseded, consolidate two notes, close an orphan question, one named item at a time.
- **no response, or "let it pass"** → do nothing. The findings were ephemeral by design.

Dream never assembles an execution plan and never runs one on its own. If nothing is named, nothing changes.

## Fail conditions

- No vault resolvable → stale-ref resolution is skipped (other detectors still run); the scan never hard-fails on a missing vault.
- `dream.py` exits non-zero → stop, report the error, render no findings.

## When NOT to use dream

- For routine surface cleanup (indexes, tags, wikilink form): use `/adjudant tidy`.
- For a single obviously-superseded decision: add the `superseded` marker yourself.

## See also

- `reference/tidy.md` — surface mechanical sweep
- `scripts/dream.py` — the read-only analyser
- `templates/dream-report.md` — the report scaffold for "store them"
