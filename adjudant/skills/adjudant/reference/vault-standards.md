# Vault standards

Structure, naming and links. Field shapes are not here: the template file is
the only declaration of a kind's shape, and this document links to it rather
than restating it. Every rule stated twice is a rule that drifts.

## 1. Structure

    {vault}/
      Home.md                      generated
      projects/
        active/ paused/ finished/ archive/
          {slug}/
            brief.md  _handoff.md  _index.md
            sessions/     2026-09-01.md
            decisions/    2026-09-01-drop-bucket-a-tags.md
            tasks/        rebuild-board-deck.md
            notes/        cold-cache-quadratic.md
            docs/         cache.md  bug-log.md  glossary.md
            specs/        spec-018-page-spinup.md
            components/   modules/button.md
            api/          contacts.md
            schemas/      ep-object.md
            sources/      attribution-test-runbook.md
            releases/     v2.1.0.md
            dreams/       2026-09-01.md
            images/

A folder exists when something is in it. One level of grouping inside a
folder, never two. The folder for each kind is `KIND_FOLDER` in
`scripts/_place.py`, and `place()` is the only thing that decides a path.

## 2. Lifecycle

Four folders: `active/`, `paused/`, `finished/`, `archive/`. The folder is the
project's lifecycle state, and there is no `status:` field on a brief. Moves
happen through the guided triage in `/adjudant status`, one project at a time.

## 3. Naming

Kebab-case everywhere, no exceptions. Dated kinds keep the date prefix;
numbered kinds keep the number. Where a filename carries a date, `created:` is
derived from it at write time and `status` asserts the two match.

## 4. Links

Wikilinks with the project-relative path and a display alias for anything in
the vault; markdown links for anything outside it. The lifecycle folder is
omitted:

    [[acme-web/decisions/2026-08-12-branch-track|branch track]]

Obsidian resolves by matching the end of a path, so a project moving between
folders breaks nothing. `link()` in `scripts/_place.py` is the only thing that
writes one, and it refuses a target that names a lifecycle folder.

## 5. The kinds

Fifteen. Each one's shape is declared by its template and nowhere else:

    project   session   decision  task     note
    doc       source    spec      handoff  index
    release   dream     component api      schema

See `../templates/`. A runbook, a glossary, a standard and a bug log are all
written as a `doc`: a thing gets its own kind only when it needs a line at the
top that a plain page does not have.

## 6. Markdown elements

One rule per element, in `content-markdown.md`.
