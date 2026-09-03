# /adjudant clean

**Cleanup sweep.** Idempotent — a second run with no fresh drift makes no changes. **Two-phase preview → apply.**

**The contract, enforced in code.** `clean` rewrites a file in place and may not create a vault file. It removes CONTENT, not files: a run strips retired fields and stale lines, so the byte count falls while the file count holds. Deleting a whole note is a judgement call and belongs to `dream`, which applies through this same guard. `VaultWriteGuard.remove` exists for that path and `clean` never calls it. `scripts/_vault_write.py` holds the guard every live write passes through, and a proposal naming a path that holds no file is refused and listed, not written. Anything clean cannot fix by rewriting, it reports. That was previously a sentence in this document, and a sentence cannot be tested.

`clean` replaces `tidy` and `ramasse`. The surface sweep is the default; `--deep` adds the structural pass that was ramasse's analysis phase. Both read the whole project before proposing anything.

## Target `[vault|repo|all]`

Default is `vault` (the sweep below).

- **`repo`** — mechanical repair of the *code repo*: **harness symlink repair only**. Two-phase via `repo_tidy.py`:

  ```bash
  python3 "$(dirname "$0")/../../../scripts/repo_tidy.py" preview --project-dir "$REPO_ROOT"
  # review {preview}/summary.md (preview prints the path; it is under
  # $TMPDIR/adjudant/{repo}/repo-tidy-preview, not in the repo), then:
  python3 "$(dirname "$0")/../../../scripts/repo_tidy.py" apply --project-dir "$REPO_ROOT"
  ```

  `apply` backs the prior link state up to
  `$TMPDIR/adjudant/{repo}/repo-tidy-backup/{ts}/*.legacy` (newest 5 kept),
  recreates each missing or dangling harness symlink on an **already-adopted**
  plugin as a relative link to its canonical `skills/<name>`, and deletes the
  preview. It **never** creates a harness for a plugin that lacks one, and it
  does **not** touch versions (the `check_marketplace_versions.py` pre-commit
  gate owns those). On a clean repo `clean repo` is a no-op: it is the repair
  arm of `check repo`'s detect. Repo conventions: `reference/repo-standards.md`.
- **`all`** — the vault sweep *and* the repo repair.

Repo ops use `--project-dir` as the repo root directly (no breadcrumb).

## When to run

- Routine — daily or weekly, the default sweep
- `--deep` sparingly, roughly quarterly or after a major shape change
- Before a `/adjudant status` if drift has accumulated
- After `/adjudant dream` flags fixable items
- After importing or merging vault content

## The surface features (numbered 2-4)

Feature 1 was rebuilding an existing folder-level `_index.md`. It is gone:
plan 4's `_index_gen` owns the only two index surfaces left (`Home.md`,
`{slug}/_index.md`), both generated whole from the filesystem. The gap report
that replaced the rebuild is gone too: `prune_index_files` DELETES any other
`_index.md` on the next status run, so asking a reader to hand-build one was
asking for work the tool undoes. The numbers below stay 2-4 on purpose: `vault-standards.md` and `internals.md` both cite
"clean feature 3" and "clean feature 4" by number.

2. **Bump `updated:` frontmatter** on touched files where applicable (`doc`, `project`, `note` types). Never adds the field if absent.
3. **Fix wikilink form** — rewrite `[text](path.md)` to `[[{slug}/path|text]]` when `path` resolves to a vault `.md`. Every link is built by `_place.link()`, which carries the project-relative path and never the lifecycle folder, so a project that moves keeps its inbound links. Leaves external links and non-vault paths alone.
4. **Repair frontmatter schema** per `FIELD_SCHEMA` (vault-standards §1/§9) — strip unknown fields (`tags:`, `project:`, stray keys), migrate the one legacy key with a live target (`node_type` → `type`; rename when `type:` is absent, drop when both exist, and `originSessionId` drops as an ordinary unknown field because no template declares `source_session`), normalise decision-status aliases (`accepted`/`locked`/`current` → `active`). Never touches required keys, parse-error files or non-canonical types. Task-status aliases are retired: the board reads its vocabulary from the task template, and a value outside it is reported by `status`, never rewritten here. The preview's `summary.md` lists every strip and migrate under `## Schema`.

   **An uncorroborated `type:` is reported, never acted on.** The strip reads `type:` as ground truth, which holds for a file adjudant wrote and not for a foreign file that acquired a colliding `type:` some other way — a Claude Code auto-memory note flattened by an external editor arrives as `type: project` carrying none of a brief's fields, so every real field it does have (`name:`, `description:`, exactly what the memory system reads for relevance) looks unknown and gets stripped. Corroboration is the required set beyond `type` itself: a majority present means the file backs its own declaration and the strip proceeds; a minority means it is misclassified, and clean emits an `unverified_type` line instead of touching it. Retype the file or fill it in.

   To repair memory notes already flattened, `scripts/renest_memory.py preview <dir>` lists them and `apply` puts `metadata.type` back, backing each file up first. The flattening preserves the value, so the repair is mechanical, but only while `name:` and `description:` are still on the file.

## The deep pass (`--deep`)

Read-only. It proposes nothing and applies nothing: every finding needs a human decision, which is why it never grew a write path.

| Detector | Finds |
|---|---|
| frontmatter drift | missing block, parse error, `: null`/`~` values (§1 says omit the key) |
| type drift | a `type:` no template declares, with counts and examples |
| naming violations | doc filename not UPPERCASE, decision without a date prefix, session not `YYYY-MM-DD.md` (§4) |
| artefact naming | `.canvas`/`.base` filenames that are not strict kebab-case (§4) |
| wikilink form violations | `[text](*.md)` links whose path resolves in the vault (§6) |
| broken wikilinks | wikilinks whose target does not resolve, with the top targets and a sample |
| doc/decision flags | `type: decision` sitting at project root instead of `decisions/` (§3) |

Counts land in `summary.md` under `## Structural findings`; the full detail is in `changes.json` under `structural_findings`.

A scoped run (`--folder`) skips folder drift: root shape is a whole-project question, and answering it from a fraction of the folders would be wrong rather than partial.

> **Cost pre-flight (locked).** `--deep` is a heavy invocation. Run `--estimate-only` first, and if `cost.warn` is true, stop and confirm per the SKILL.md cost gate. `--folder <path>` scopes the walk to one subtree (containment-checked; the estimate follows). The preview header states the scope, so a narrowed run never reads as a full one.

## Run

> Render the JSON `cost` block as one line: `cost: ~{est_read_tokens/1000}k tokens, {files} files`.

```bash
# Phase 1 — preview (writes the scratch preview dir, never touches live files)
python3 "$(dirname "$0")/../../../scripts/clean.py" preview \
  --project-dir "$PROJECT_ROOT" \
  --vault-dir "$VAULT_PATH"

# Add the structural pass, optionally scoped:
python3 "$(dirname "$0")/../../../scripts/clean.py" preview \
  --project-dir "$PROJECT_ROOT" --vault-dir "$VAULT_PATH" --deep --folder notes

# Review the preview. It lives OUTSIDE the vault, at
# $TMPDIR/adjudant/{project}/clean-preview — preview prints the path on stderr.
# - {preview}/summary.md            human-readable diff
# - {preview}/changes.json          structured change list
# - {preview}/files/<rel_path>      proposed file contents

# Phase 2 — apply (backs up to $TMPDIR/adjudant/{project}/clean-backup/{ts}/, then writes live)
python3 "$(dirname "$0")/../../../scripts/clean.py" apply --project-dir "$PROJECT_ROOT"

# Or: detect what state we're in without touching anything
python3 "$(dirname "$0")/../../../scripts/clean.py" detect --project-dir "$PROJECT_ROOT"
# → {"state": "fresh|preview|applied", "cost": {...}}
```

Render shape (voice.md §Shape): after `preview`, lead with the change count and end
with the one next step (review `summary.md`, then `apply`); after `apply`, lead with
what was written and name a follow-up only if something was skipped.

## Apply: what happens

- Back live files up to `$TMPDIR/adjudant/{project}/clean-backup/{ISO-8601-Z-timestamp}/<rel_path>.legacy`
- Rewrite each live file with `{preview}/files/<rel_path>`, through the write guard
- Delete the preview dir
- Keep the newest 5 backups for the project and delete older ones

Neither directory is in the vault. A cleanup verb that wrote three copies of
every touched note into the thing it was cleaning added more than it removed;
both now land under `$TMPDIR` and the backups rotate.

## Stale-preview guard (locked)

`apply` re-checks every proposal against the state
recorded at preview time. Anything that no longer matches is **left alone**,
listed in `{backup}/SKIPPED-STALE.txt`, and reported on stderr plus the
`skipped_stale` JSON key (`[{"path": …, "reason": …}]`). Four reasons:

| Reason | Meaning |
|---|---|
| `changed` | Edited between the two phases (by you, another session, or the other machine via sync) |
| `vanished` | Deleted or renamed since preview; re-creating it would undo an intentional act |
| `unreadable` | Could not be read to compare |
| `refused` | The write guard declined it: nothing is at that path, and clean does not create |

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
| `--folder` escapes the project | Error before anything is read |
| Preview `changes.json` missing | Error — preview corrupt, delete and re-run |

## Scope

Default: current project resolved via the `.claude/adjudant` breadcrumb (auto-followed by `clean.py`). `--folder <path>` narrows the walk to one subtree. A vault-wide variant is **not implemented** — invoke per project.

## What clean does NOT do

- **No file creation.** Not an index, not a report, not a workspace or iteration folder. The guard refuses it.
- No deletion of content (the surface sweep modifies; the deep pass only reports)
- No renames, folder moves, or wikilink repointing — those need judgment, so `--deep` reports them and a human decides
- No content edits beyond the `[text](path.md)` form rewrite

## See also

- `reference/dream.md` — content and memory refresh, the semantic tier; dream may spin fixable mechanical items back to clean
- `reference/repo-standards.md` — the `[repo|all]` target's conventions
- `scripts/clean.py`, `scripts/test_clean.py`, `scripts/test_clean_deep.py`
- `scripts/_vault_write.py` — the guard, and `scripts/test__vault_write.py`
