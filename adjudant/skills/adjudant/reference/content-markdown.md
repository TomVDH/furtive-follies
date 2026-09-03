# Markdown elements

One rule per element, applied to every template and every vault write.
Obsidian's own syntax (embeds, callout folding, block references) still works
exactly as Obsidian defines it; nothing here overrides Obsidian, it narrows
which parts of it adjudant uses.

## Headings

One H1, the title, and only in documents that need one. H2 for sections. H3
sparingly. Never H4 or deeper. No decorative punctuation in headings.

## Lists

`-` for bullets, `1.` for ordered. Never `*` or `+`.

## Emphasis

`*italic*` and `**bold**`. Never `_underscore_` forms. Bold the first words of
a bullet, never a whole sentence.

## Code

Fenced with a language tag, always. Never four-space indentation. This alone
removes the class of bug where an unfenced `[[ -z "$VAR" ]]` became a
wikilink.

## Tables

For anything with three or more parallel attributes. Escape pipes.

## Callouts

`> [!note]` and `> [!warning]` only. Plain `>` is a quotation.

## Links

Wikilinks with the project-relative path and a display alias for anything in
the vault. Markdown links for anything outside it. The exact shape, and the
refusal on a target that names a lifecycle folder, are in `vault-standards.md`
section 4.

## Mermaid

For flow, sequence, and state. Never for a list or a table that would read
better as one. Diagrams follow `draw`'s generation rules in
`mermaid-generation-rules.md`.

## Emoji

None as semantic markup, with one documented exception: the handoff traffic
light, which the statusline reads.

## Register

ASD-STE100 across every write. One instruction per sentence, active voice,
present tense, one word per meaning, under twenty words.
