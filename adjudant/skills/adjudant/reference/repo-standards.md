# Repo standards

Single source of truth for **code-repo** conventions — the counterpart to
`vault-standards.md`, which governs the vault. These are the categories the
`check repo` / `tidy repo` target audits and (safely) repairs. The general core
applies to any connected repo; the marketplace layer auto-activates when a
`.claude-plugin/marketplace.json` is present at the repo root.

## General core (any repo)

### Context files

The repo root carries `AGENTS.md` (canonical, harness-agnostic project context)
and `CLAUDE.md`. `CLAUDE.md`'s first non-empty line must be `@AGENTS.md` — it
imports AGENTS rather than duplicating it. Per-plugin `AGENTS.md`/`CLAUDE.md` are
optional at this repo's scale; their absence is reported *informational*, never
counted as drift.

### Plan age

Design/plan docs live under `docs/superpowers/`. A doc with no completion marker
(`status: done`, `status: complete`, `status: shipped`, or a ✅ near the top)
that is older than the staleness threshold (default 30 days by mtime) is flagged
as a stale plan. The fix is to mark it complete or archive it — an archival move
is deliberate structural work (`clean --deep` reports it), not a mechanical fix.

### Git practice

Where the code for a work item goes is decided by the bean's type. The rule
is stated once per session by the session-start banner, provisioned into
every linked project by `connect` (the `## Git practice` section of
`templates/AGENTS.md`), and observed by `status` through the six `git-*`
signals in `status.md`. Instruct and observe: nothing blocks a commit. The
report is the guard.

1. Run `git pull --ff-only` on `main` before starting work. The other machine
   is often ahead.
2. A `feature` bean gets its own branch, named `feature/<bean-id>`. An `epic`
   is a container and gets no branch: it is never worked on directly, so a
   branch for it would hold nothing.
3. That branch is always checked out in a linked worktree at
   `.worktrees/<bean-id>` (`git worktree add .worktrees/<bean-id> -b
   feature/<bean-id>`). The main checkout stays on `main` and is never
   `git switch`ed. Keep `.worktrees/` in `.gitignore`; the rule must land on
   `main` before the first worktree exists.
4. The breadcrumb `.claude/adjudant` is usually git-ignored, so a fresh
   worktree has none of its own. It does not need one: the worktree's `.git`
   file names the main checkout, the statusline reads the breadcrumb from
   there, and the session-start hook links it in
   (`.worktrees/<bean-id>/.claude/adjudant -> <main>/.claude/adjudant`) so
   every other reader finds it where it expects. Nothing to copy.
5. A `task` or `bug` bean with no in-progress feature parent commits on
   `main`. One under an in-progress feature commits on that feature's branch,
   in its worktree. Neither owns a branch of its own.
6. Merge-back is a pull request: `git push -u origin feature/<bean-id>`,
   `gh pr create`, merge on GitHub. Then `git worktree remove
   .worktrees/<bean-id>`, `git branch -d feature/<bean-id>`, and
   `git pull --ff-only` on `main`. A worktree removed on the other machine
   leaves a dangling entry here; `git worktree prune` clears it.
7. Conventional Commits. The bean file lands in the same commit as the code
   it describes. A version bump happens on the feature branch, through the
   script below, and reaches `main` through the PR.

## Marketplace layer (marketplace.json present)

### Version coherence

Every plugin listed in `.claude-plugin/marketplace.json` must declare the same
`version` as its own `<source>/.claude-plugin/plugin.json`. This is already
gated at commit time by `scripts/check_marketplace_versions.py`; `check repo`
surfaces the same signal read-only and never "fixes" it (the pre-commit gate
owns repair by blocking drift). Use `python3 scripts/bump_plugin_version.py
<plugin> <X.Y.Z>` to move a version — never hand-edit. Run it on the feature
branch; the bump lands on `main` with the PR, so `main` only ever carries
released versions.

### Symlink integrity

The Impeccable pattern: a plugin's real skill content lives at
`<plugin>/skills/<name>/`, and the three harness dirs mirror it via **relative
symlinks** — `<plugin>/source/skills/<name>`, `<plugin>/.claude/skills/<name>`,
and `<plugin>/.gemini/skills/<name>` each resolve to `../../skills/<name>`. A
plugin is *harness-adopted* when its canonical skill dir exists and at least one
of the three symlinks is present. `tidy repo` repairs a missing or dangling
symlink on an adopted plugin; it never creates a harness for a plugin that has
none (auto-adoption is deferred structural work). A plugin with no `skills/` dir
needs no harness and is not flagged.

### Registration

Every plugin directory at the repo root (one carrying
`.claude-plugin/plugin.json`) must be registered in `marketplace.json`, and
every registered `source` path must resolve to an existing directory. An
unregistered plugin or a dangling `source` path is drift.

## What the repo target does NOT touch

- **No content/prose cleanup** — that is the vault's `dream` tier, vault-only.
- **No regex drift-defense** (whitespace, secrets, deprecated tags) — hookify
  owns that.
- **No auto-adoption or archival in `clean`** — structural moves are deferred
  work that `clean --deep` reports and a human decides.
