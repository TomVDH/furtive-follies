# /adjudant connect

Onboard project to vault. **One rigid path — no flags, no branching, idempotent.**

## Contract flow (locked)

connect is three phases; the card in the middle is the only thing the user must read.

1. **Infer.** Run `connect.py --contract --project-root {code root} [flags]`. The JSON
   contract carries the five required fields (vault, slug, project_type, initial_status,
   purpose) with inferred values pre-filled, plus the per-agent artifact disclosure
   (AGENTS.md, CLAUDE.md, GEMINI.md, breadcrumb, vault scaffold, .gitignore) each marked
   already-present or will-create.
2. **Confirm.** Render the contract as ONE card, both halves. Ask the user to approve or
   correct the five fields once. purpose is the one field with no inference: ask for it
   if empty; it becomes the brief's opening line and what sitrep orients from. If
   `contract.zone` is `_fridge` or `_archive`, add one nudge line: project is
   shelved; move its folder back to `projects/` by hand to reactivate it.
3. **Apply + receipt.** Run connect.py with the confirmed values (`--purpose`,
   `--initial-status`, plus the usual flags). Render `summary.receipt` back as the same
   card with per-artifact marks: created / already-present / updated. A re-run on a
   healthy project shows all already-present and writes nothing new. For `coding` and `plugin`
   projects the receipt closes with one board pointer: tasks/ holds the cards;
   run /adjudant board to open a deck on them (opt-in, never auto-seeded).

Config knobs land in the breadcrumb at init with defaults visible on the card:
`cost_warn_tokens` (the build profile's default), `stale_after_days: 30`. Existing overrides survive re-connect,
as does an opt-in `stamp_source_session: true` (per-file session stamping, default off —
connect never writes the key itself).

## The 5 features (locked spec)

1. **Breadcrumb** — write `.claude/adjudant` at project root containing `vault_path`, `vault_name`, `slug`, `mode`, `cost_warn_tokens`, `stale_after_days` (plus `stamp_source_session` when a hand-added opt-in already exists)
2. **Context files** — provision `AGENTS.md` + `CLAUDE.md` + `GEMINI.md` at project root from the matching templates (skip if files exist)
3. **Vault scaffold** — create `{vault}/projects/active/{slug}/` with `brief.md` (from `templates/brief.md`, its `<!-- when: -->` sections resolved for the project type). No subfolders and no indexes: a folder exists once a write puts something in it.
4. **Session note** — create today's `{vault}/projects/{zone}/{slug}/sessions/{YYYY-MM-DD}.md` from `templates/session.md` with frontmatter filled in
5. **Gitignore** — append `.claude/adjudant` to project `.gitignore` (create file if missing)
6. **Base dashboards** — install `templates/bases/dashboard-*.base` into `{project}/bases/`, each `file.inFolder(...)` filter rewritten to the project's real vault path (sessions, decisions, tasks, freshness views). Write-if-absent: an edited dashboard is never clobbered by an idempotent re-run.

## Inputs

`/adjudant connect` takes no arguments. Resolves everything from environment:

| Need | Resolution order |
|---|---|
| Vault path | `--vault-path` arg → `OB_VAULT` env var → `--vault-name` arg → existing breadcrumb → walk parent dirs for `Home.md` with `type: vault-home` or `type: index` → guided location setup (see below) |
| Project slug | existing breadcrumb → cwd basename (enforce kebab-case) |
| `project_type` | existing brief → prompt once (`coding | knowledge | plugin | tinkerage`) |
| Project display name | prompt once if creating new |

## No vault yet? Guided location setup

Coworkers keep their notes in different places, and many will not have a vault at all. When the vault cannot be resolved (no `OB_VAULT`, no breadcrumb, no `Home.md` up the tree), do NOT guess a path. Walk the user through choosing one:

1. Run `connect.py --suggest-vaults`. It prints the vault-location options that exist on THIS machine as JSON: cloud-sync roots first (`recommended: true`), then local-only folders.
2. Present them as a short numbered list. Recommend a **cloud-sync** root (iCloud Drive, OneDrive, Google Drive, Dropbox) so the vault follows the user across machines; note that a **local** folder (`~/Documents`) is fine for a single machine. The user may also type any absolute path.
3. Ask for a vault name (default `Claude Vault`). The vault will live at `<chosen root>/<vault name>`.
4. Create and scaffold it in one step:

   ```
   connect.py --project-root {code root} --vault-path "<root>/<vault name>" --create-vault --purpose "..." [flags]
   ```

   `--create-vault` makes the directory (and its `projects/` folder) when it does not exist; connect then scaffolds the project inside it as usual, and writes the breadcrumb so later sessions resolve it silently.

If a vault already resolves, skip all of this: connect uses it without asking.

## Idempotent behavior

Re-running on an already-connected project fills gaps; never overwrites user content.

- Existing `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` untouched
- Existing `brief.md` untouched
- Missing subfolders created; existing untouched
- Today's session note: if exists, no-op (the SessionStart hook handles append-on-resume separately)

## Fail conditions

- Vault path can't be resolved AND user declines to provide one → exit non-zero with message
- `project_type` not provided → inferred from repo signals (never exits non-zero for this)
- Slug contains invalid characters (spaces, dots, uppercase) → exit non-zero with rename suggestion

## Subfolders

There are no default subfolders. `connect` creates the project directory and
`brief.md`, and stops.

Every other folder is created by the write that puts something in it, from the
one table in `scripts/_place.py` that maps each kind to its folder. `project_type`
still decides which `<!-- when: -->` sections the brief gets; it no longer decides
which empty folders a project starts with.

This retires the brief's `extra_folders:` field. It existed to excuse a folder
from a comparison against the per-type defaults, and there are no defaults left
to compare against.
