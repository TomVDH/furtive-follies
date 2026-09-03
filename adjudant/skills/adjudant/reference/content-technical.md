# Technical pages

How to write the five kinds that document code: `api`, `schema`, `component`,
`spec`, `source`. Each kind's frontmatter is declared by its template and
nowhere else. This is the part a template cannot carry.

Load it when you write or review one of those pages.

## The rule under all five

**Write what the source cannot tell you.**

A generated inventory already lists every endpoint, every field, every path. A
page that restates it is a copy that goes stale in silence. The page earns its
place by carrying what a reader cannot get from the machine: the constraint, the
trap, the call that succeeds and does nothing.

State the measurement, not the belief. *"Measured on 2026-09-01 against the live
service: both credentials answer."* survives a reader asking "says who?".
*"This endpoint works."* does not.

Correct in place, and say what changed. A page that quietly stops being wrong
teaches nobody. A page that says *"this previously documented a flat request
body; that was wrong and the call rejects it"* stops the next person repeating
the mistake.

## `api`

One page per endpoint family, not per endpoint.

`## Endpoints` names where the full list lives and how to regenerate it. Do not
hand-copy paths into the vault. They drift the day someone adds one, and the
page cannot tell you it has drifted.

`## Quirks` is why the page exists. What the documentation omits: a call that
accepts your payload and discards it, a parameter silently ignored rather than
rejected, a limit whose failure mode changed, a permission that differs between
reading and writing.

`## Helpers` names the functions in this project that wrap the call, with their
file, so the next person writes no new client.

`## See also` links the schema the endpoint returns and the decisions behind it.

Say which credential the call needs and how that was established. An
authorisation rule nobody has probed is a guess.

## `schema`

One page per object.

The identity line sits directly under the H1 and reads as one fact: the ids
needed to address this object, together. Three frontmatter fields nothing
queries would be worse than one line read as a unit. Where part of the identity
does not apply, drop it rather than carrying an empty field.

`## Object metadata` holds labels and display properties. `## Properties` is the
table. Add `## Associations` when the object joins others, and draw it: a
three-way join is a diagram, not a paragraph.

## `component`

Two files, and the split is the design.

    components/modules/button.md              hand-written, yours
    components/modules/button-generated.md    machine, carries source:

The generated half names its script in `source:`. Adjudant never cleans, indexes
or nags a page with one, because the script rewrites it every run and nagging
about output is nagging about the wrong file. It embeds the sidecar so the prose
has one home.

Never write into the generated half, and give its generator a guard that returns
the moment the hand-written file exists. A generator is one bad run from
deleting somebody's afternoon.

For the generator to prune, have it write `.{script name}.manifest` beside its
output, one stem per line, listing what this run produced. `status` then reports
a page claiming that script and absent from the list. A timestamp cannot do this
job: a sync client touches every file, and the whole set reads as written the
same day.

`## Diagram` shows the branch a field list cannot: which class is computed, and
from what. `## Traps` is what breaks.

## `spec`

`## Out of scope` is the section that makes a spec a contract. Without it a
reader cannot tell what was decided from what was merely imagined, and someone
bolts a prose callout on months later explaining that two sections were never
real.

`status: draft | agreed | superseded` says whether it is settled. `verified:`
says the built thing was confirmed to match it. Agreed, never verified, and no
task card citing it is a spec nobody built, and `status` reports exactly that.

Where a repo holds the canonical copy, say so at the top and set `source:`.

## `source`

Material you did not write. `source:` records where it came from. `verified:`
records when someone last confirmed upstream still says this.

Say plainly that upstream is canonical and local edits are lost.

## `verified_by`, and being honest about it

Three values, and the difference between them is the point:

    tested   someone ran it against the live thing
    read     someone read the code it describes
    docs     someone took a vendor's word for it

`status` reports a page that is only ever `docs`. That is not a failure. It is
the report telling you which pages have never actually been proven.

Any other value is reported and never rewritten.

## Procedures

A runbook, a glossary, a standard and a bug log are all a `doc`. Set `doc_kind:`
so they stay findable:

    doc_kind: runbook | glossary | standard | bug-log

**A runbook** carries prerequisites, numbered phases, verification, and
rollback. The first three usually get written. Rollback usually does not:
surveyed across a real vault, no procedure had a rollback section, while the
deploy tool was quietly snapshotting for one nobody had documented. Write it, or
say plainly that there is no way back.

Attach the reason to a rule, not just the rule. "Never edit the shared layer"
is a rule someone will break under pressure. "Never edit the shared layer,
because every consumer inherits the change silently" is one they will not.

End with what cannot be automated, as its own checklist.

**A bug log** is one file, `bug-log.md`, with `## BUG-NNN` per entry and an
optional `status: closed | fixed | dropped` line inside the entry. One file, not
one per bug: three entries turning out to be the same defect is only visible
when they sit in one list. `status` reports an open entry no task card cites.

**A glossary** is term and definition, one term per heading.

## Identifiers

Real account ids, object ids and endpoint hosts belong in the vault, which is
yours. They do not belong in this plugin, which is public, or in any example
written for someone else's project. Write the shape, never a live value.
