# {Project Name}

`{slug}` · type: `{coding|knowledge|plugin|tinkerage}` · vault: [[{slug}/brief|{slug}]]

> One-line purpose of this project.

## What this is

{Two to four sentences. Why this project exists, who it's for, what success looks like.}

## Where things live

| | |
|---|---|
| Working tree | (this folder) |
| Canonical context | [[{slug}/brief]] |
| Decisions | [[{slug}/decisions]] |
| Sessions | [[{slug}/sessions]] |
| Handoff | [[{slug}/_handoff]] |

## Conventions

{Project-specific guardrails. Add as they're decided. Examples: stack choices, naming rules, forbidden commands, deploy paths.}

## Git practice

Work items are beans. The bean type decides where the code goes.

- Run `git pull --ff-only` on `main` before you start.
- A `feature` bean gets its own branch, named `feature/<bean-id>`. An `epic` is a container and gets no branch.
- Check that branch out in a worktree: `git worktree add .worktrees/<bean-id> -b feature/<bean-id>`. Do not `git switch` in the main checkout. The main checkout stays on `main`.
- Keep `.worktrees/` in `.gitignore`. A worktree finds the main checkout's `.claude/adjudant` on its own; adjudant links it in at session start.
- A `task` or `bug` bean with no in-progress feature parent commits on `main`.
- A `task` or `bug` bean under an in-progress feature commits on that feature's branch, in its worktree.
- To merge: push the branch, run `gh pr create`, and merge on GitHub. Then run `git worktree remove .worktrees/<bean-id>`, `git branch -d feature/<bean-id>`, and `git pull --ff-only` on `main`.
- Use Conventional Commits. Commit the bean file in the same commit as the code.
- `/adjudant status` reports drift from this practice. Nothing blocks a commit; the report is the guard.

## Vault is canonical

When asked "is X documented?" or "do we know Y?", check the vault first: repos document code, the vault documents decisions and context. Use the `adjudant` skill to read/write vault files.

## Claude-specific overrides

Live in `CLAUDE.md` next to this file. CLAUDE.md `@`-imports this file.
