# /adjudant dream

**Content/knowledge/memory refresh.** The third and deepest cleanup tier — semantic, not mechanical. Reads the actual prose of decisions, notes, and sessions; surfaces what's gone stale. Five-phase, judgment-heavy, human-in-the-loop. Mirrors `clean --deep`'s shape on the content layer.

*Staleness is the enemy.* Dream catches "the doc says X but reality is now Y."

## What dream does (vs clean)

```
clean        = surface mechanical sweep (frontmatter, indexes, wikilink form). Routine.
clean --deep = STRUCTURAL findings (folders, schema, file types, naming). Sparing, reports only.
dream        = CONTENT/knowledge/memory refresh (semantic). THIS verb.
```

Dream operates on the **content layer**, where judgment — not regex — decides:
- **Outdated info** — decisions/notes whose content no longer reflects reality
- **Supersession** — a newer decision overrules an older one that was never marked superseded
- **Redundancy** — multiple notes saying the same thing, ripe for consolidation
- **Stale references** — links that still resolve but point to archived/old content
- **Orphan threads** — open questions (TODO/OPEN/TBD) from old sessions, never resolved

Dream cleans semantically: mark decisions superseded, consolidate duplicates, archive stale sessions, close or re-surface orphan threads.

## Why it's LLM-judgment heavy

`dream.py` **cannot decide semantics**. Where `clean --deep` decides a structural fact ("this filename violates §4"), `dream.py` only emits *candidates* — comparators with `file · line · excerpt` — and **Claude judges**. "Decision A of January is superseded by decision B of May" is a candidate; whether B actually overrules A is a read-and-reason call, not a pattern match.

**Precision over recall.** The 2026-08-13 run turned 602 files into 602 candidates at 918k read tokens; a sampled review found none real. Every candidate now carries a `confidence` (0 to 1), the catalog keeps only the top 20, and a finding a past report dismissed stays out until the file it names changes. A catalog nobody can read is a catalog nobody reads.

## The 5-phase shape (superpowers chain)

> **Cost pre-flight (locked).** Run the analyser with `--estimate-only` before the real scan. If `cost.warn` is true, stop and confirm with the user per the SKILL.md cost gate.

| Phase | Skill | Output |
|---|---|---|
| 1. Analyse | `dream.py` + Claude | Content/staleness comparator catalog (JSON → narrative) |
| 2. Judge | Claude (judgment-heavy) | Which candidates are *real*: superseded / contradictory / redundant / stale / orphaned |
| 3. Refresh plan | `superpowers:writing-plans` → `dream-report` | Concrete refresh plan (mark superseded, consolidate dupes, archive stale sessions, close orphan threads) |
| 4. Review | (human checkpoint) | User reviews + approves plan; can edit or defer items |
| 5. Execute | `superpowers:executing-plans` | Apply with checkpoints + backups; calls `clean.py` for any mechanical follow-up |

## Phase 1 — Run the scanner

```bash
python3 "$(dirname "$0")/../../../scripts/dream.py" \
  --project-dir "$PROJECT_ROOT" \
  --vault-dir "$VAULT_PATH" \
  --out /tmp/dream-scan-{slug}.json
```

Optional flags: `--today YYYY-MM-DD` (override "now" for age math — deterministic), `--stale-days N` (staleness threshold, default 180), `--include-legacy`, `--folder <path>` (scope the walk to one project subtree, e.g. `decisions`).

**Scoped runs.** `--folder` is the sanctioned answer when the cost gate warns on a big project: proceed on a slice instead of aborting. The estimate then covers the subtree only, and the report carries a top-level `scope` field — **render it in the header** ("dream — scoped to `decisions/`") so a narrowed run never reads as a full one. Containment-checked: a path that resolves outside the project is refused. Deliberately just this one flag; recency windows and sampling were considered and rejected (they hide exactly what dream exists to find).

The JSON catalog (the **comparator catalog**) carries nine categories:

| Key | What it surfaces |
|---|---|
| `staleness_candidates` | Content-type files older than the threshold (`updated:`/`date:`/filename date). A legacy epistemic declaration still outranks the clock where one survives on disk: `freshness: timeless` never appears here; an expired `valid_until` appears regardless of age, tagged `reason: declared validity expired`. No template declares either field since v3, so `clean` is what finally removes them |
| `supersession_signals` | Same-topic decision pairs, older likely superseded (+ whether already marked). Also `kind: dangling-pointer` entries: a frontmatter `superseded_by` whose target resolves to no file |
| `redundancy_clusters` | Near-duplicate notes/docs by token-set (Jaccard) similarity |
| `stale_refs` | Refs that *resolve* but point to `_archive`/`_legacy` or old dated targets (broken links stay `clean --deep`'s job) |
| `orphan_questions` | Aged open-loop markers (`TODO`/`OPEN:`/`TBD`/`follow-up`) never closed |
| `unacted_decisions` | `status: active` decisions whose stated `## Consequence` shows no action (unreferenced by any session, aged) |
| `documentation_gaps` | Under-documentation — sessions with real work but no decision, stub files, briefs missing required sections |
| `dangling_scopes` | Brief `MILESTONES`/`OPEN QUESTIONS` items whose terms never appear in any session |

The last three revive the original `/dream`'s content checks (see *Lineage* below). Each entry carries enough context (`file`, `line`, `excerpt`/`shared_terms`) for Claude to judge without re-reading every file, plus a `confidence` between 0 and 1.

The top-level `summary` gives per-category counts **of what the report holds**, alongside `candidates_found` (what the walk saw before the cap), `dismissed` (suppressed by a past report) and `cap`. Render the difference when there is one: "20 of 602, top 20 by confidence" is honest, "20 candidates" is not.

**Scoring.** Base confidence per detector: supersession 0.8, stale refs 0.7, orphan questions 0.6, unacted decisions 0.5, staleness 0.4, everything else 0.3. Two dampers: an inbound session link on an unacted decision costs 0.15 each up to two, and an older decision that already carries a `superseded_by` marker costs 0.5, because that is the convention working rather than drift.

**Dismissals.** `read_dismissals()` reads the `## Dismissed` table of every past report in `dreams/` (either filename spelling) and drops candidates naming those files. A dismissal expires when the file's own declared date moves past the report that dismissed it — the template's "Suppress until: the file changes", read from `updated:`/`date:` rather than the filesystem mtime a vault sync rewrites. Candidates keyed on a pair or a cluster rather than a single `file` carry no dismissal key and always resurface.

Claude reads the JSON, renders a content-state narrative, and judges each candidate before planning.

## Phase 2 — Judge

For each candidate, Claude reads the cited prose and decides:
- **staleness** → is the content actually outdated, or just old-but-correct?
- **supersession** → does the newer decision truly overrule the older? If so, the older needs a `superseded_by` marker.
- **redundancy** → consolidate into one note, or are the duplicates intentionally distinct?
- **stale_refs** → repoint, archive, or leave?
- **orphan_questions** → still open (re-surface), resolved-elsewhere (close), or archive?
- **unacted_decisions** → was the consequence actually implemented (mark `status: implemented`), still pending (leave / re-surface), or abandoned (mark `reversed`)?
- **documentation_gaps** → real gap worth backfilling, or intentionally terse?
- **dangling_scopes** → still planned (keep), silently done (record it), or dropped (strike from brief)?

Discard false positives here. The catalog is already a shortlist: it arrives scored, capped and stripped of anything a past report dismissed, so a candidate that reaches phase 2 has earned a read. Low `confidence` is a reason to read it first, not a reason to skip it.

## Phase 3 — Write the refresh plan

Invoke `superpowers:writing-plans` to produce a concrete content-refresh plan, and mirror it into a `dream` note (see `templates/dream.md`) written to the project's `dreams/` folder as `{YYYY-MM-DD}-dream.md`:
- Decisions to mark `superseded` (with the superseding file)
- Note/doc consolidations (which files merge into which canonical target; which get archived)
- Sessions to archive (move to `_archive/`)
- Orphan threads to close or re-surface (as a fresh open question / decision)
- Stale refs to repoint or remove
- Plus any mechanical fixes spun off to `clean`

## Phase 4 — Human review

User reviews the plan. May approve as-is, edit specific entries, reject and re-judge, or defer specific items (e.g., "archive the 2024 sessions in a separate pass").

## Phase 5 — Execute

Invoke `superpowers:executing-plans` to apply with checkpoints. Each plan step is its own commit-able unit.

Apply through `clean`'s primitives — it rewrites and removes and cannot create — and let it carry the backup. **Content operations are destructive, so every one is backed up first**, to the scratch path `$TMPDIR/adjudant/{slug}/dream-backup/{timestamp}/<rel_path>.legacy`, outside the vault and rotating at five. Checkpoint state lives beside it. The vault gains exactly one file from a dream: the report in `dreams/`.

## Inputs

Default: current project (resolved from the `.claude/adjudant` breadcrumb — `dream.py` auto-follows it, same as the other helpers). Vault-wide dream means a deliberate per-project loop; not a single invocation.

## Fail conditions

- No vault resolvable → stale-ref resolution is skipped (other detectors still run); the scan never hard-fails on a missing vault
- `dream.py` exits non-zero → halt before phase 2
- User aborts during phase 4 → leave the scratch checkpoint for resume; no live changes, and nothing written into the vault
- Phase 5 partial failure → halt at last checkpoint, leave `$TMPDIR/adjudant/{slug}/dream-backup/` for rollback

## Lineage — the original `/dream`

This verb's content checks descend from an earlier `/dream` (its historical two-pass design is preserved in `docs/superpowers/2026-04-30-obsidian-bridge.design.md` §13): a structural-sanitation pass — now split across `clean` and `clean --deep` — and a content-analysis pass (stale info, dangling scopes, unacted decisions, documentation gaps). adjudant `dream` is that content pass, modernised into a read-only comparator catalog. **It is fully standalone** — it has no dependency on, and no interoperation with, any other plugin; the report is always dry, with no personality layer.

## When NOT to use dream

- For routine surface cleanup: use `/adjudant clean`
- For folder/schema/naming findings: use `/adjudant clean --deep`
- For a single obviously-superseded decision: just add the `superseded_by` marker manually

## See also

- `reference/clean.md` — the cleanup sweep and its deep pass
- `scripts/dream.py` — phase 1 analyser (this tier's scanner)
- `scripts/clean.py` — the deep pass's structural detectors
- `templates/dream.md` — phase 3 output scaffold
- `docs/superpowers/2026-05-26-adjudant-tidy-ramasse-log.design.md` — design lock
