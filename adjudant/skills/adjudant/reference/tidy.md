# /adjudant tidy

Mechanical vault sweep. Idempotent — second run with no fresh drift = no changes. **Two-phase preview → apply**.

## Target `[vault|repo|all]`

`tidy` takes an optional target; default is `vault` (the sweep described below —
exact back-compat, `/adjudant tidy` is unchanged).

- **`repo`** — safe mechanical repair of the *code repo*: **harness symlink
  repair only**. Two-phase via `repo_tidy.py`:

  ```bash
  python3 "$(dirname "$0")/../../../scripts/repo_tidy.py" preview --project-dir "$REPO_ROOT"
  # review .adjudant-repo-tidy-preview/summary.md, then:
  python3 "$(dirname "$0")/../../../scripts/repo_tidy.py" apply --project-dir "$REPO_ROOT"
  ```

  `apply` backs the prior link state up to `.adjudant-repo-tidy-backup/{ts}/*.legacy`,
  recreates each missing/dangling harness symlink on an **already-adopted**
  plugin as a relative link to its canonical `skills/<name>`, and deletes the
  preview. It **never** creates a harness for a plugin that lacks one
  (auto-adoption is out of scope for this tool), and it does **not** touch versions
  (the `check_marketplace_versions.py` pre-commit gate owns those). On a clean
  repo `tidy repo` is a no-op — it is the repair arm of `check repo`'s detect
  (`harness-parity` fails the build when a symlink breaks; `tidy repo` fixes it).
  Repo conventions: `reference/repo-standards.md`.
- **`all`** — run the vault sweep *and* the repo repair.

Repo ops use `--project-dir` as the repo root directly (no breadcrumb).

## When to run

- Routine — daily/weekly cadence; a light mechanical pass, not deep restructure
- Before a `/adjudant sync` if drift has accumulated
- After `/adjudant dream` flags fixable items
- After importing/merging vault content

## The 5 features (locked spec)

1. **Rebuild `_index.md`** in every project subfolder with ≥2 same-type siblings. Chronological reverse-sort for date-prefixed filenames, alphabetical otherwise. Skip `sessions/`, `images/`, `assets/`, `previews/`, `iterations/`, `_archive/`. A **curated alias is carried forward**: `- [[note|Something a human wrote]]` keeps its text, because a filename cannot reconstruct it and a slug-title would contradict the one-line-per-entry convention the rebuild serves. Entries with no existing alias get the generated one; an alias for an entry that no longer exists is dropped with it.
2. **Bump `updated:` frontmatter** on touched files where applicable (`doc`, `project`, `note` types). Never adds the field if absent.
3. **Normalise tags** per the locked 2026-05-25 schema in `reference/vault-standards.md` §2 — drop Bucket D (`#ob/*`, vague topicals, project-slug self-tags, crew names, `type/*` tags). Leave Bucket A + Bucket C untouched.
4. **Fix wikilink form** — rewrite `[text](path.md)` to `[[stem|text]]` IFF `path` resolves to a vault `.md`. Leave external links + non-vault paths alone.
5. **Repair frontmatter schema** per `FIELD_SCHEMA` (vault-standards §1/§9) — strip unknown fields (`project:`, stray keys), migrate legacy keys (`node_type` → `type`, `originSessionId` → `source_session`; rename when the target is absent, drop when both exist), normalise decision-status aliases (`accepted`/`locked`/`current` → `active`). Never touches required keys, parse-error files, non-canonical types, or task-status aliases (accepted input; the board normalizes lanes on read). The preview's `summary.md` lists every strip/migrate under `## Schema`.

   **An uncorroborated `type:` is reported, never acted on.** The strip reads `type:` as ground truth, which holds for a file adjudant wrote and not for a foreign file that acquired a colliding `type:` some other way — a Claude Code auto-memory note flattened by an external editor arrives as `type: project` carrying none of a brief's fields, so every real field it does have (`name:`, `description:` — exactly what the memory system reads for relevance) looks "unknown" and gets stripped. Corroboration is the required set beyond `type` itself: a majority present means the file backs its own declaration and the strip proceeds; a minority means it is misclassified, and tidy emits an `unverified_type` line instead of touching it. Retype the file or fill it in.

   To repair memory notes already flattened, `scripts/renest_memory.py preview <dir>` lists them and `apply` puts `metadata.type` back, backing each file up first. The flattening preserves the value, so the repair is mechanical — but only while `name:`/`description:` are still on the file, so run it before applying any tidy preview computed under 1.0.0.

## Run

> Render the JSON `cost` block as one line: `cost: ~{est_read_tokens/1000}k tokens, {files} files`.

```bash
# Phase 1 — preview (writes .adjudant-tidy-preview/, never touches live files)
python3 "$(dirname "$0")/../../../scripts/tidy.py" preview \
  --project-dir "$PROJECT_ROOT" \
  --vault-dir "$VAULT_PATH"

# Review the preview
# - .adjudant-tidy-preview/summary.md            human-readable diff
# - .adjudant-tidy-preview/changes.json          structured change list
# - .adjudant-tidy-preview/files/<rel_path>      proposed file contents

# Phase 2 — apply (creates .adjudant-tidy-backup/{timestamp}/, then writes live)
python3 "$(dirname "$0")/../../../scripts/tidy.py" apply --project-dir "$PROJECT_ROOT"

# Or: detect what state we're in without touching anything
python3 "$(dirname "$0")/../../../scripts/tidy.py" detect --project-dir "$PROJECT_ROOT"
# → {"state": "fresh|preview|applied", "cost": {...}}
```

Render shape (voice.md §Shape): after `preview`, lead with the change count and end
with the one next step (review `summary.md`, then `apply`); after `apply`, lead with
what was written and name a follow-up only if something was skipped.

## Apply: what happens

- Backup live files to `.adjudant-tidy-backup/{ISO-8601-Z-timestamp}/<rel_path>.legacy`
- Copy `.adjudant-tidy-preview/files/<rel_path>` to live position
- Delete `.adjudant-tidy-preview/`

## Stale-preview guard (locked)

`apply` re-checks every proposal, index rebuilds included, against the state
recorded at preview time. Anything that no longer matches is **left alone**,
listed in `{backup}/SKIPPED-STALE.txt`, and reported on stderr plus the
`skipped_stale` JSON key (`[{"path": …, "reason": …}]`). Four reasons:

| Reason | Meaning |
|---|---|
| `changed` | Edited between the two phases (by you, another session, or the other machine via sync) |
| `vanished` | Deleted or renamed since preview; re-creating it would undo an intentional act |
| `appeared` | The preview expected no file at that path and something got there first |
| `unreadable` | Could not be read to compare |

Render any non-empty skip list as its own line and point at a fresh `preview`.
Targets that resolve outside the project dir are refused outright, each path is
applied at most once per run, and each apply gets its own backup dir so a
same-second retry can never overwrite the first backup.

## Fail conditions

| Condition | Action |
|---|---|
| Preview already exists | Error — review or delete first |
| Apply with no preview | Error — run preview first |
| Vault unresolvable | Wikilink-form fix is skipped (other features still apply) |
| Preview `changes.json` missing | Error — preview corrupt, delete and re-run |

## Scope

Default: current project resolved via `.claude/adjudant` breadcrumb (auto-followed by `tidy.py`). Vault-wide variant is **not yet implemented** — invoke per-project for now.

## What tidy does NOT do

- No deep restructure (out of scope for this tool)
- No content edits to existing wikilinks targets (only the form `[text](path.md)` rewrite)
- No new file creation beyond `_index.md` regenerations
- No deletion (only modification + index rebuild)

## See also

- `reference/dream.md` — content/knowledge/memory refresh (semantic tier); dream may spin fixable mechanical items back to tidy
- `scripts/tidy.py`, `scripts/test_tidy.py`
