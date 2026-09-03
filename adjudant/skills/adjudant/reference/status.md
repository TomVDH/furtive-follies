# /adjudant status

Makes derived state current, then reports on it. Backed by `status.py`, which
absorbed `sync`, `check`, `sitrep`, `kebab --scan` and the advisor pulse: five
verbs that answered one question between them, and none of which told you which
finding mattered most.

The report has three bands, ordered by the cost of being wrong:

| Band | Means |
|---|---|
| `wrong_now` | The vault is making a claim that is false today. The only band that earns an interruption. |
| `going_stale` | True now, decaying. A nudge, not an alarm. |
| `worth_a_look` | A question rather than a defect. |

## Target `[vault|repo|all]`

Default is `vault` (everything below).

- **`repo`** — audit the *code repo* instead of the vault. Runs
  `python3 "$(dirname "$0")/../../../scripts/repo_scan.py" --project-dir "$REPO_ROOT"`
  and renders the JSON: a version-coherence table (marketplace.json against each
  plugin.json), a symlink-integrity matrix (skills-bearing plugins only), a
  registration check (every plugin registered, every `source` path resolves), a
  stale-plan list, the repo-root context-file and `@AGENTS.md` import check, and
  a single `drift_items` score. Per-plugin context files are shown
  *informational* (not counted). Repo conventions live in
  `reference/repo-standards.md`. Never writes.
  - `token_budget`: per-surface context cost (`file`, `tokens`, `budget`,
    `over`) plus `total` and `over_count`, from `token_budget.py`. Render as one
    line when `over_count` is 0 (`context: ~{total/1000}k tokens across {n}
    surfaces`), and list the offenders when it is not. Report only: it never
    fails a build, and an over-budget surface is a prompt to look, not an error.
- **`all`** — run the vault report *and* the repo scan; render both blocks.

Repo ops use `--project-dir` as the repo root directly (no breadcrumb: the repo
*is* the project dir).

## Run

```bash
python3 "$(dirname "$0")/../../../scripts/status.py" \
  --project-dir "$PROJECT_ROOT" \
  --vault-dir "$VAULT_PATH" \
  --out /tmp/status-{slug}.json
```

Add `--no-sync` for a strictly read-only pass. Add `--estimate-only` for the
cost block alone.

## The make-current phase

The one part that writes, and it writes exactly what `sync` wrote:

1. **Brief refresh** — bump `updated:` in `brief.md`. An undecodable brief is
   left byte-identical and the skip is reported, never silent.
2. **Handoff mirror** — copy the `.remember/` body into `_handoff.md` through
   `_handoff_freshness.render_handoff`, the same renderer the PreCompact and
   SessionEnd hooks use, so a manual run and an auto-compaction produce
   byte-identical handoffs. A blank source is never mirrored. Needs the CODE
   root; without one there is nothing to mirror, which is a state, not a
   failure.

There used to be a third step here, refreshing this project's row in
`projects/_index.md`. That surface is retired: Home groups every project by
lifecycle folder and is generated whole instead, so a hand-upserted row could
only disagree with it.

Results land under `synced.steps`; anything the phase could not do lands in
`synced.warnings`. Report both.

## JSON output shape (top-level keys)

- `synced` — `{today, slug, steps, warnings}` from the phase above
- `wrong_now`, `going_stale`, `worth_a_look` — the three bands, each a list of
  `{signal, file?, detail?, …}`
- `orientation` — momentum: `project`, `purpose`, `freshness` (traffic light and
  age from real activity), `were_doing`, `whats_done`, `board`, `repo` (branch,
  dirty count, recent commits), `server` (dev servers from `.claude/launch.json`,
  probed with a 0.6s HEAD; down is an answer, never an error), `capabilities`,
  `next_step`, `open_signals`, `status`
- `compliance` — `project`, `counts`, `recent`, `handoff`, `drift_signal`,
  `board`, `capabilities`, `remember`, `status`, `schema`, `environment`
  - `project` mirrors `brief.md`'s frontmatter and adds `legacy_breadcrumb`:
    true when `.claude/obsidian-bridge` exists and `.claude/adjudant` does not.
    The retired breadcrumb stopped being a vault-resolution step in v3, so the
    project is not connected at all. It arrives in the `wrong_now` band as
    `.claude/obsidian-bridge is a retired breadcrumb ... run /adjudant connect`
- `capabilities` — the optional environments present on THIS machine, each
  `{id, line}`, in both halves. Render each `line` verbatim, one line, above the
  board line; render nothing when the list is empty. A build that declares no
  capabilities always sends an empty list, so nothing ever renders as absent
- `environment` — one boolean per capability this build declares
  (`scripts/build-profile.json`), plus `obsidian_cli`. A capability the build
  does not declare has no key at all
- `naming` — vault-standards §4 title violations: `{file, type, suggested, issue}`.
  Read-only; a rename breaks every wikilink pointing at the file, and that repair
  belongs to `clean`, with its preview and its backups
- `advisor` — `{state, pulse}`. The pulse is the read-only context-integrity
  check: `quiet`, `next_step`, `dangling_scopes`, `recent_decisions`
- `truth` — the truth checks: `{findings, counts, checked}`. A finding is
  `{band, kind, file, detail}`, sorted by band. See **Truth checks** below
- `cost` — `{est_read_tokens, files, bytes, threshold, warn}`

`handoff` carries two clocks, deliberately. `updated` and `stale_hours` are the
**mirror clock**: when the handoff was last written. Every SessionEnd stamps it
to today, so a mirror of an empty buffer still reads fresh — diagnostic only,
never the answer to "are we drifting?". `light` / `age` / `next` / `stale` come
from `_handoff_freshness`: remember dailies and session-note markers, the same
sensor the hooks render into the handoff banner. **Render the activity clock.**
When the two disagree, the handoff has not been re-synced since the last real
work: say so rather than picking one.

`schema` is frontmatter drift per `FIELD_SCHEMA`, which is the templates:
`checked`, `unchecked` (no block, parse error, or non-canonical type — those are
`clean --deep` territory), `flagged`, `counts`, and `samples` capped at 20.

## Truth checks

Backed by `truth.py`. This is what replaced shape grading: `check` read 110
frontmatter keys against a schema and produced 99 failures, 69 of them from a
folder adjudant does not own, and nobody acted on one of them. Every check
below is settled by a file's existence or a date comparison, in seconds, so it
is safe to run on every invocation. Reading prose to find what only
comprehension finds is `dream`'s job, and dream is the expensive one.

Two exclusions, both permanent. `memory/` is never checked, because adjudant
does not own that file format. A page carrying `source:` is generated by a
script that overwrites it every run, so it is excluded from every check except
the one that is about generated pages.

**The report gates nothing.** A check that refuses a write is a check people
learn to route around.

### `wrong-now` — the vault says something that is false today

| Kind | Fires on | Traces to |
|---|---|---|
| `broken-wikilink` | A link that resolves to nothing. Embeds and attachment names are never counted. | 733 broken links out of 9611, at 7.6% |
| `superseded-target-missing` | `superseded_by:` points at a file that does not exist. | a chain of supersessions with a gap in it |
| `task-spec-missing` | A card whose `spec:` cites a spec that was never written. | phantom SPEC codes on live cards |
| `brief-repo-missing` | The brief's `Repo` cell names an absolute path that is not on this disk. An elided path, a URL and an unfilled `{placeholder}` are not claims about the disk. | a repo that moved and a brief that did not |
| `open-card-in-archive` | A card in `tasks/_archive/` whose status is not `done` or `dropped`. | one sweep moved 97 cards and closed zero; 44 still read open |
| `bug-entry-uncited` | A bug-log entry that reads open and no card cites. | entries nobody picked up |
| `spec-agreed-unbuilt` | A spec agreed 60 days with no card citing it and no `verified:`. | SPEC-012, agreed two months, zero cards |
| `decision-consequence-uncarded` | A decision whose `## Consequence` names work in prose with no link to a card. | work that exists nowhere a board can see |
| `superseded-without-target` | `status: superseded` and nothing says what replaced it. | a record that contradicts itself |
| `status-off-vocabulary` | A `status:` outside its type's vocabulary. Reported, never rewritten. | the board silently refiled `obsolete` as backlog |
| `created-filename-mismatch` | A `created:` date against a different date in the same filename. | one of the two was edited by hand |
| `version-filename-mismatch` | A release note's `version:` against the version in its filename. A leading `v` on either side is not a disagreement. | the same, for releases |

### `going-stale` — true now, decaying

| Kind | Fires on | Traces to |
|---|---|---|
| `verified-stale` | `verified:` more than 90 days old. | `updated:` says the text changed; only `verified:` says a person confirmed it |
| `verified-missing` | A kind whose template requires `verified:` and carries none, or carries something that is not a date. | 71 component sidecars with no verification at all |
| `brief-stale` | A brief untouched for 90 days while sessions kept landing. A quiet project is triage's finding, not this one. | a project that moved on without its brief |
| `handoff-behind-session` | A handoff older than the newest session note. | a handoff describing a session two sessions ago |
| `generated-page-stale` | A generated page older than the script named in its `source:`. A `source:` naming a system (`confluence`) is provenance, not a generator, and is skipped. | output that no longer matches its generator |

### `worth-a-look` — a question, not a defect

| Kind | Fires on | Traces to |
|---|---|---|
| `verified-docs-only` | `verified_by: docs` — a vendor's word, never a live probe. | a bare date threw away how it was checked |
| `project-zone-drift` | A project in `active/` with no session for 30 days. Names the `--move` call that fixes it. | the lifecycle move nothing ever asked about |

Which kinds exist is not configurable, and the list does not grow on a hunch.
Two consecutive dream reports dismissed the same naming finding in identical
words, which is the tool spending the same hour twice. There is no
naming-convention check here for that reason: a convention is enforced at write
time or it is not enforced.

`verified_kinds()` is derived from `FIELD_SCHEMA`, which is parsed from the
templates. Delete `verified:` from a template and `verified-missing` stops
applying to that kind, with no Python edit.

## Render

> Render the JSON `cost` block as one line: `cost: ~{est_read_tokens/1000}k tokens, {files} files`.

```
## {slug} — {status} · {freshness.light} {freshness.age}

{purpose}
Last session {whats_done.last_session} · {counts summary} · NEXT: {next_step}
{orientation.repo.branch}, {dirty} dirty{" · " + board.line if board.present}

## Wrong now

{one line per entry, or "nothing" — this band silent means the vault's claims hold}

## Going stale

{one line per entry, or skip the section entirely when empty}

## Worth a look

{one line per entry, or skip the section entirely when empty}

Made current: brief {steps.brief_refresh} · handoff {steps.handoff_mirror}
```

Adapt phrasing to be conversational; the shape above is the data layout, not a
rigid template.

Shape (voice.md §Shape): open with the most decision-relevant fact — the
lifecycle status and freshness beat the title — and close with exactly one next
step, drawn from `wrong_now` if it has anything and from `next_step` otherwise.
Never end on a recap.

Each section holds two sources: the band list of the same name
(`wrong_now`, `going_stale`, `worth_a_look`) and the `truth.findings` whose
`band` matches it (`wrong-now`, `going-stale`, `worth-a-look`, in that order).
Render a truth finding as `{kind} · {file}: {detail}`, with the file omitted
when it is empty, which marks a finding about the project rather than a file.
Truth findings arrive sorted; keep that order.

Skip an empty band's section entirely rather than printing "none". A `wrong_now`
that is empty is the good case and should read as silence, not as a heading with
nothing under it.

Never render every `naming` entry when there are more than five: give the count
and the first three, and point at the list in the JSON.

## The advisor rails

The advisor mode itself is a standing contract, not code: see
`reference/advisor.md`, which the SessionStart hook loads when the mode is on.
`status.py` owns its two state surfaces so they cannot drift apart:

```bash
python3 status.py --advisor {on|off|status} --project-dir "$REPO_ROOT"
python3 status.py --capture-task --title "..." [--note "..."] --project-dir "$REPO_ROOT"
```

`--advisor on` sets `advisor: on` in `.claude/adjudant` *and* stamps a marker
line into `AGENTS.md`, so the mode is never a hidden setting someone has to
remember exists. `off` removes both. `--capture-task` lands an approved
suggestion as a task note through the same rail the session-end bridge uses,
deduplicated by slug so a re-capture never clobbers an edited note.

## Lifecycle triage

```bash
python3 status.py --triage --project-dir "$REPO_ROOT"
python3 status.py --move SLUG ZONE --project-dir "$REPO_ROOT"
```

`--triage` prints one prompt per project in the vault and moves nothing — a
read-only plan, never an action. Ask about each project one at a time rather
than dumping the whole list and letting it get skimmed. Each confirmed move is
exactly one `--move SLUG ZONE` call, so nothing moves until a person says so
for that project. A project in `active/` with no session for 30 days is the
prompt that makes triage happen at all — the verb it replaces went unused for
a year because nothing ever asked.

## Naming a thing

```bash
python3 status.py --slug Fix the parser rewrite    # -> fix-the-parser-rewrite
```

One slug rule for the whole plugin (`board_bridge.kebab`), so a captured task
and a hand-named note agree about what the same title is called.

## Inputs

None. Operates on the project resolved from the `.claude/adjudant` breadcrumb at
cwd.

## Fail conditions

- No breadcrumb at cwd and the argument is not a vault project dir → exit
  non-zero pointing at `/adjudant connect`
- A breadcrumb whose slug is not safe kebab-case, or whose project would land
  outside the vault → exit non-zero naming the slug. The verb path fails closed;
  nothing is written first
- Vault path unreachable → exit non-zero with the message

## See also

- `scripts/status.py`, `scripts/test_status.py`
- `scripts/truth.py`, `scripts/test_truth.py` — the checks behind the `truth` section
- `scripts/_lifecycle.py`, `scripts/test__lifecycle.py` — the guided triage behind `--triage`/`--move`
- `reference/clean.md` — repairs what this reports
- `reference/dream.md` — the deeper diagnostic; use when `drift_signal` looks elevated
- `reference/advisor.md` — the standing contract behind the advisor rails
