# /adjudant board

Scaffold a self-hosted **kanban board** — a *standard project
surface* any adjudant project can have, not a one-off. The board is a single,
dependency-free `board.html` (drag a card between lanes; it auto-saves to disk
via the File System Access API on Chromium, with a localStorage mirror) driven
by a sibling `board-data.json`. Each board namespaces its own browser + disk
state by `boardId`, so a portfolio of project boards served from the same
localhost never clobber one another.

The board wears **Adjudant Classic**: a 70s corporate-paper memo in warm cream
OKLCH stock, brown-black ink, one warm orange accent and no rounded corner
anywhere. It is ported from the ZenaSoft design system, whose own references are
IBM annual reports c.1973, Container Corp of America, Mobil corporate guidelines
and Knoll spec sheets. Fonts are named, never fetched: the board is served off a
disk and validator 24 fails the build on any off-machine `href`, so the display
serif resolves through its declared fallbacks.

It is a *view*. A card's face carries its title, the first two lines of its
note, and its id; open the card for everything else. Category colour is
data-driven — names get OKLCH palette hues by index, or supply explicit
`{ "name": "oklch(...)" }`.

## The card, and the card opened

The face is a `<button>` inside the draggable card, so there is still one tab
stop per card: click it, or focus it and press `Enter`. Its sheet opens against
the right edge of the board and carries **every field the card holds**, because
the opened card is where a person goes to see the whole item and a field that
reaches nowhere else is a field they cannot see at all:

| Section | Carries |
|---|---|
| Lane | the lane rail (below) |
| Note | the whole note, `Pretty` or `Raw` (below) |
| Tags | one chip per tag |
| Relations | `Parent`, `Blocked by`, `Blocking` and `References`, each under its own name |
| Details | `Id`, `Category`, `Priority`, `From`, `Created`, `Updated`, `Slug`, `File`, `Etag`, `Task status`, and the lane a card was dragged out of while that move has not reached the deck yet |

A row appears only when the card actually carries that field, so the sheet never
shows an empty one.

The **lane rail** is one button per lane in a scrolling track, the current lane
lifted out of it, one press to move the card. It is the move control for a phone
and for a screen reader, and it replaced a tap-then-tap mode that existed only on
devices matching `(pointer: coarse)` and that nothing on the page announced. It
scrolls rather than wraps: a segmented control folded onto a second row reads as
broken, and a vault board has seven lanes. The note toggle is drawn as the same
control, because it is the same idea.

**The note is Markdown, and is read as Markdown.** A bean's body is Markdown, and
a note shown as one flat run with the syntax still in it is source rather than a
note. The sheet renders the subset bodies use: headings, emphasis, code, fenced
blocks, lists, quotes, rules and links. `Raw` is one click away and keeps every
character, and the choice is remembered for every board this browser opens. The
card face always shows the note's text with the syntax stripped, because two
lines of card preview spent on `## ` is two lines of punctuation.

The reader builds DOM nodes and never assembles an HTML string. `board-data.json`
is documented as hand-editable and a bean body is written by whoever writes
beans, so a renderer that went through `innerHTML` would hand either of them the
page. For the same reason a link is made clickable only for `http`, `https`,
`mailto` and `obsidian`; anything else is shown as text. Nothing is fetched, and
no library is loaded: the board is one offline file and validator 24 keeps it
that way.

Three marks reach the face, and only when they carry something:

- **Category**, as a coloured swatch and its name, on a card whose category is
  not the deck's ordinary one. A category is the ordinary one when it covers
  more cards than every other category put together. So a beans board of 78
  tasks and 9 epics marks the nine epics and leaves the tasks unmarked, an
  evenly split deck marks all of them, and a deck with one category marks
  nothing. The name is in the card's label, the legend and the sheet regardless.
- **Priority**, whenever the deck carries one. A tracker writes `priority` only
  when it is worth showing (`_beans.to_card` drops `normal`), so the key's
  presence is the whole signal. `low`, `deferred` and their kin are drawn quiet;
  the rest are drawn in the alert colour.
- **Tags**, as quiet chips between the note and the id, minus the deck's
  ordinary ones. A tag is ordinary when more cards carry it than not, which is
  the same threshold that keeps the ordinary category off the face: both marks
  answer the same question, so suppressing one at a majority and the other only
  at unanimity would be arbitrary. On a real beans deck `adjudant` sat on 71 of
  92 cards, so the informative cards were the 21 without it, and absence is the
  one thing a chip cannot show. Three chips, then
  a `+N`: a tracker sets no ceiling on how many tags a card may hold and the
  face must not grow with them. The full set is in the sheet, and every shown
  tag is in the card's accessible name, because an `aria-label` replaces a
  button's contents rather than adding to them.

### The version stamp

A tiny mono tag beside the wordmark, naming which adjudant drew the page.

Stamped by `board.py` at scaffold time, between `ADJ_VERSION_START` and
`ADJ_VERSION_END`, read from the plugin's own `plugin.json`. Not read at page
load: the file is static once written, so a board scaffolded by 4.1.9 must keep
saying 4.1.9 after the plugin moves on. A load-time read would make the stamp a
guess about what is installed now rather than a fact about what produced this
file. Every failure to read the manifest yields an empty string and the tag
hides itself, so a template run straight out of a checkout still renders.

`--text-faint` and no more. 9.5px is body size for contrast, and stacking
opacity on that token measured 3.74:1 on dark and 2.93:1 on light against a
4.5:1 floor. The token alone is 5.97:1 and 5.00:1, and it is already the
quietest ink on the board.

### The type keys carry a shape

Colour alone excluded anyone with a colour vision deficiency from telling one
type key from another. Each palette hue gets a ColorSym symbol, so the same fact
is carried in shape: the swatch does not gain an element, it becomes the mark,
drawn in the category hue.

`nth-child` is exact rather than a guess, because `catColor` assigns the palette
BY INDEX, so key order is hue order.

The eight are a **bijection**, solved as an assignment problem rather than
matched by nearest hue. Nearest-hue gave blue and cyan the same symbol, and two
categories wearing one mark is worse than no mark at all. Total hue error 73
degrees, worst single 40 (blue, which has nowhere closer to go).

Inlined as CSS masks, 3,479 characters for all eight: no font, no fetch, and the
offline lock untouched. A tag key takes no symbol, because a tag has no hue to
key and a mark there would be noise.

Symbols are ColorSym, github.com/luisfrancisco/colorsym, CC BY-SA 4.0.

One vocabulary (4.1.23). The rail taught a mark the card and the sheet did not
speak; they carried an 8px square. Now all three carry the same mark, built
once as `.sym` and keyed by `data-sym`, which `catSym` assigns by index beside
the hue, so a card and its key always agree. Sizes: 20px on the rail key, 14px
on the card face, 16px beside the category in the sheet, and 26px in front of
the sheet's title (4.1.24), the largest it is drawn anywhere: the sheet is
where one card is read, so its type is said first. On a multi-type deck every typed card
wears its mark; the type's name is still printed only on the exceptions. The
mark is decoration; the name stays in the card's accessible label.

The palette gained 0.03 chroma for punch, with lightness trimmed on avocado,
petrol and teal to hold the 3:1 floor. All 48 ratios are still computed.

### The key is a word on a rule, not a pill (4.1.17)

Eight bordered pills in a row read as a toolbar and pulled the eye off the
cards. The key is now a word standing on a 2px rule in its own hue, with the
18px mark beside it: the rule keys the colour, the mark keys the shape, and the
paper shows through. The filtered key darkens and its rule gains an ink line.

Picked from eight studies under impeccable live: square, symbol, knockout,
symbol only, tinted field, leading rule, stamp, underscored. Underscored won.

A tag key is not a second type key (4.1.21). With the marks gone it was the
type key minus its mark, and the two rails read as one. A tag is an identifier,
so it is set the way identifiers are set on this page: in the mono face with a
leading hash, no rule and no box. The filtered tag is stamped in ink. Picked
from four studies under live: mono labels, index line, ticket stub, stamp row.
The hash is decoration; the key's accessible name stays the tag and its count.

Its state is stated on its own rule on purpose: a `box-shadow` built on the
`--c` a tag never sets is not "no colour", it is an invalid declaration, and
the whole shadow would go.

The palette is eight 70s tones at 56 to 59 percent lightness: aubergine,
avocado, burnt orange, petrol, rust rose, teal, harvest gold, brick. That band
is the only one that clears 3:1 (SC 1.4.11, the floor for a rule and a mark) on
every surface in both schemes. A pastel fails the dark ground; a deep tone fails
the paper. A test computes all 48 ratios.

### One rail, not two

Type and tag both answer "narrow this board", so they are one scrolling line
rather than two labelled rows. The rule that divided the rails went with the
chip boxes: keys that sit on the paper are already read as two groups by the
gap, and the marks say which group is which. The labels go with the second
row. The tags scroll rather than wrap: wrapping inside a one-line rail is the
two-row layout again with the label removed, and it measured 59px against the
type rail's 25px.

### Persisting board edits

The control that connects `board-data.json` was called "Connect file", which
named a mechanism nobody had to care about. It read as an unexplained button and
went unused, so it was hidden, which hid the only affordance for the thing that
makes a drag outlive the tab.

It is back, and the label names what you get. The punctuation carries the mood:
unconnected it ASKS, connected it REPORTS.

| state | label |
|---|---|
| unconnected | Persist board edits? |
| connected | Persisting board edits |
| handle lapsed | Resume persisting? |
| both stores failed | Not persisted |

Dashed while it is still a question, solid once it is answered.

### The masthead, in three bands

Identity and the ambient glance, then state and actions, then filters. Generous
between the bands, tight inside them: that contrast is what makes them read as
three groups rather than one list.

It was four bands of identical spacing, and a right-hand column 125px tall
against the brand's 55px, so the taller column set the header's height and left
a 38px hole under the mark. Measured at 1200x800 the masthead took 295px, 37% of
the screen, and the first card began at 379px. Three bands take 247px, 31%, and
the first card starts at 331px.

The counts and the actions are one relationship, not two: what the board holds
sits left, what you do to it sits right, on one line.

The activity card is ambient. It sets the top band's height now, at 89px
against the identity block's 55, and nothing in it changes size on hover: the
date lives in the cell's tooltip, never swapped into the label.

### The masthead, a letterhead over a ledger (4.5.2)

Chosen from four renders of the real board, 126 cards, at 1440 and 1180, on
2026-09-12 (the design-pass bean). The three bands stacked the brand, a docket
line and the rails, and the activity card sat alone in the top right where it
set the brand row's height: 119px against the brand's 69. Download wrapped
onto a second line. Measured at 1440 the masthead was 300px and the first card
began at 332px.

Now the masthead is one grid and the HTML is unchanged: the row wrappers are
`display:contents`, so DOM order and reading order still agree. The docket
stands beside the mark, as tall as it: the state line over the filter and one
menu. The two time sheets run the width of the page as a ruled ledger, a week
a column, under the letterhead. The rail sits under a rule. 270px open, 166px
folded, and the first card begins at 302px or 198px.

**The letterhead.** The tool and the kind of document on one line, the
board's name on the next behind a drawn chevron: a title in the display face
now, where it was a mono chip in a box behind a glyph.

**One menu, not four buttons.** The filter stays out because it is the control
the board is used through. Persist, grouping and download are used once a
session or less, so they fold into a `<details>` menu that closes after a
choice, on Escape and on a click elsewhere. The buttons wear the campaign
request form's ghost button: body face, 13px, weight 500, sentence case, a 1px
strong rule. The uppercase, tracked-out version was a costume the form does
not wear.

**The ledger is ruled.** Stretched cells with no rules measured as faint
stripes. A 1px rule between days and between weeks, on a ground lighter than
the page like a card's, and an empty week still reads as a week. The caption
sits on the caption line at the right end, where the row is empty.

**The ledger folds.** A tab hung from the rail's rule at the right end, a
chevron drawn in one stroke. The rule is the rail's, not the ledger's, so it
stays when the sheet goes. The choice is kept per browser beside the other
reading preferences. Driven in a browser before shipping: the tab shared the
rail's grid cell and came first in the DOM, so the rail painted over it and
took every click until it was given a stacking order.

**The keys wear the light bevel on light paper.** The vintage key shipped as
the campaign form's dark `.btn-fancy` in both schemes and sat on the paper as
five black slabs. The light scheme takes the form's light variant, 2px raised
insets in paper tones with the label in ink tinted by the type's hue; pressing
inverts the insets. Equal widths, so the row reads as a keyboard. The dark
scheme keeps the dark bevel.

**Drift taken from the campaign request form**, which is the most complete
ZenaSoft surface: the masthead was a flat fill over a grained body, so it
takes the same two dot grids; the labels on the ledger and the `hide closed`
button were set in mono, which the register keeps for code, data and
measurement, so the field names take the lane heading's face and the button
the body's. Two departures stand, both recorded in the template's token
comment: `--text-faint` at 48% for the 4.5:1 floor, and the dark scale.

### On a phone

The filter rails scroll sideways rather than wrapping. Measured on a 375px
screen before that rule: the tag rail wrapped to four rows and the type rail to
two, the header took 487px of an 812px phone, and the first card began at 559px.
Sixty percent of the screen was chrome and six cards were reachable without
scrolling. A rail is scanned along, not read down, so one scrolling line buys the
board its screen back: the header is 261px, the first card starts at 333px, and
fourteen cards are in view.

The activity card is not drawn under 900px. There is no corner to spare, and
two time sheets on a 375px screen are a decoration.

### Closed lanes

A lane is closed when it carries a stamp, which is deck data rather than two
hardcoded ids: `completed` and `scrapped` declare theirs on a beans deck, `done`
and `icebox` inherit theirs from the STAMP defaults on a vault deck. One rule
covers both, and a lane renamed from `done` to `shipped` keeps working.

`hide closed` drops them from the BOARD, never from the deck. The card keeps its
column, the sheet's lane row still lists every lane, and the button says how many
lanes and cards went. A deck of nothing but closed lanes shows them anyway,
because hiding the whole board is not a view. The preference is per browser,
beside the note view: it is how a person reads, not a fact about a deck.

### The activity card (4.1.22)

Top right of the masthead, which is sticky, so it holds the viewport's corner
without a floating overlay that could obstruct a lane. Plain elements, no SVG,
no library: the page is offline-locked and a chart library is a fetch.

It is a time sheet, not a histogram. Bars binned by a unit that changed with
the deck's age answered "how much" and never "when do we work on this". Two
panels of the same six weeks, Touched and Filed: seven day-rows with Monday at
the top, a column per week, a cell per day tinted by count in four steps of
the accent. Today is ringed in ink. Whole weeks only, padded to the Sunday
that closes this one. Chosen from three readings under impeccable live.

Three things it refuses to do.

It does not call a bulk write activity. Measured on a real deck, 83 of 92 cards
shared one `updatedAt` day, which was an import plus the churn bug fixed in
4.1.1. A day holding more than half the cards goes grey and its tooltip says
bulk write.

It does not make you choose a field. `createdAt` is when work was filed,
`updatedAt` is when the file last changed. Different questions, so both are on
the page at once, each panel captioned with its own. The switch is gone.

It does not change size on hover. The old chart swapped a date readout into
its label, and a label that changes width moves the whole masthead every time
the pointer crosses a cell. The date and count live in the cell's tooltip and
accessible name; a lit cell is a focusable list item.

It describes the board in front of you: filters and hidden lanes both count, or
it would be reporting cards the reader cannot see.

### The type book (4.5.2)

A deck lists its types in order of first appearance, so the rail's order and
every type's hue and mark used to change from board to board. The beans
vocabulary now has fixed seats, chosen on 2026-09-12: task, feature, bug, epic,
milestone, the ordinary work first and the containers last. Task is blue with
the chevron, feature green, bug red with the ringed dot, epic purple with the
task's old mark, milestone yellow and keeps its own. A type outside the book
follows in deck order on the free hues and marks, and the marks stay a
bijection. Every book hue is in the palette, so the 3:1 contrast test covers it.

### The sheet's cover (4.5.2)

The type bar is a risograph field in the card's type hue, chosen from seven
studies rendered on the real sheet on 2026-09-12. The full tint sits top right
and the hue's own dark ink pools in the bottom-left corner, under the mark and
the type name, which are printed in paper. That is what keeps them legible: a
blue at 45% and a yellow at 80% both put the label on a ground under 45%
lightness. The grain is a soft-light overlay; multiplied in, it turned every hue
to mud in the first three studies. Nothing drifts. The old mesh animated for
eight seconds on a loop, which is ambient motion on a surface a person reads.

The sheet's labels share the lane heading's face, so the board has one
field-name voice: lane headings, ledger captions, sheet sections and fact
names. The vintage key on the sheet, close and copy, is the campaign form's
light variant on light paper. The ledger is folded by default; the cards are
the product and a glance is opened, not walked past.

**One button.** The campaign request form's vintage bevel, in the voice the
type keys set: the body face at 12px, weight 700, uppercase, tracked. Every
button on the board is this key: the docket and its menu, hide closed, close,
copy, the note view, the lane rail, and the type keys, which tint it by hue.
The pressed key inverts its insets and takes a 5px stroke of the accent down
its left edge. The key's tokens live on `:root` and switch with the scheme, so
a key is the same key on light paper and on the dark ground. A ghost variant
shipped beside it for a day and the sheet spoke two vocabularies; the user's
call on 2026-09-13 was one. The exception is the lane rail, which is not a
button row but the routing slip (4.1.26) with square stations: the one place
in the sheet where a move is made, and a row of keys only ever said where the
card is. Round pips read as a stepper from a web kit; square is the memo.

**Serve answers every tab.** `board.py serve` was a plain `TCPServer`, one
socket at a time, and a browser keeps its socket open between requests, so a
second tab waited on the first forever. It threads now, one per connection.

An impeccable live session had left a second sheet dialog in the template,
wrapped in variant markup with its own scoped stylesheet. Two elements with one
id, one of them dead. Stripped, and a test counts the dialogs.

### The dashboard parses (4.5.8)

`dashboard.html`, the orchestrator surface `board.py dashboard` scaffolds, has
one script, and from 4.5.1 to 4.5.7 it did not parse: a bare copy of the
board's example deck first, then an unclosed `paintMark()`, two
`ADJUDANT_VERSION`, two `DARK_SCHEME`, and a second `boot()`/`fetch` tail
after the IIFE had closed. One parse error blanks the whole page. It is one
declaration of each now, `boot()` paints the mark and sets ORCHESTRATOR or
ORCHESTRATRICE from the figure, and `test_board` pins the counts and, where
node is on the path, compiles every script block.

### The favicon

The band, not the figure: at 16px the head is a smudge and three stripes are
unmistakable. The stripes are whichever figure the roll produced, so the tab
carries the easter egg too. Written as an inline `data:` URI, which fetches
nothing.

### The lane rail is a routing slip (4.1.26)

A segmented control only ever said where the card is. A work order's routing
slip says where it has been: the lanes are stations on a printed line, the
ones the card has passed are inked, the one it is at is the accent and a size
larger, the ones ahead are hollow. One tap on a station still moves the card.
Chosen under impeccable live from four ways of letting a move feel like
something (a sliding thumb, the slip, a stamp, keycap hints). The note view's
Pretty/Raw rail keeps its track.

### Dependency lines on demand (4.5.8)

The board used to draw every `blockedBy` relation at once: an SVG overlay
pinned in the board's scroll space, one straight line per pair, redrawn only on
a full render. That misrendered as a matter of design, not of a bug. Each lane
scrolls on its own, so the moment one lane moved, its lines stayed where the
cards had been: ending in empty paper, or running to the bottom edge of a lane
whose card had scrolled out of it. The screenshot of 2026-09-13 09:15 was the
measurement, a Todo lane of 155 cards with lines to nowhere. Even when it was
right, a diagonal from one lane to another crossed every card in between.

Now nothing is drawn at rest. A card with relations carries a mark in its
foot, in the slot the lock already uses: the lock for a blocked card, then a
small mono count in `--text-faint` at the id's size, `2` for the cards
blocking this one and `→3` for the cards it blocks. The mark joins the card's
accessible name the way tags do (`blocked by 2, blocking 3`), because an
aria-label replaces the button's contents and a mark left out of it is a mark
only sighted people get.

Hover or keyboard focus on such a card lights it. The card and every rendered
card it is tied to take a 1px `--accent` outline, no fill, no shadow. Its
connectors are drawn for that one card only, blockers into it and the cards it
blocks out of it, `--accent` at 55%, 1.5px, with the arrowhead the old overlay
already had. Each is three orthogonal segments: out of the card's edge to the
gutter between the lanes, along the gutter, into the far card. Never a diagonal
across a card.

The overlay is `position:fixed`, sized to the board's box, and redrawn from
`getBoundingClientRect` on every animation frame while a card is lit. There is
no scroll arithmetic, so no scroll can leave it stale; at most a handful of
paths, so a frame costs nothing worth measuring. A far card scrolled outside
its lane's visible box gets a stub: the connector runs to the lane's edge and
the arrowhead points the way, up or down, saying "the blocker is above"
instead of drawing to nowhere. A far card not rendered at all (filtered, or in
a closed lane) draws nothing. On a phone the lanes stack and the page is what
scrolls, so the visible box is the lane cut to the viewport and the stubs work
the same way.

Leaving the card, blurring it, pressing Escape, starting a drag, or opening the
sheet clears the overlay. Nothing animates; the lines appear and vanish. The
lines are `aria-hidden`: the relation is already spoken through the card's
name and the sheet's Blocked by and Blocking rows. The board carries one
delegated listener each for mouseover, mouseout, focusin and focusout, not four
per card, because a deck can hold a few hundred tickets and every render
rebuilds them all.

The sheet's relations section changed in the same release. It used to print
bare ids in mono, which named a relation and said nothing about it. Now every
relation is a row: the type mark, the id, the title and the lane, under its
group (Parent, Children, Blocked by, Blocking, References, with a count in the
label past one), and a click opens that card in the same sheet. Children and
Blocking are derived from the deck and merged with whatever the card declares,
because beans stores each relation on one side only: a child names its parent,
a blocked bean names its blocker. An id the deck does not hold is said, dimmed,
and is not a button, because there is nothing to open. The sheet's labels
grew with it, to the register's field-label role: section labels at 14.5px
and fact keys at 13px in the condensed face, the key column widened to 112px
so CATEGORY and PRIORITY sit on one line, and each relation row 34px tall
with the title at 13px and the lane at 12px.

Two faces are embedded now, both as data URIs, because the file used to carry
only Mozilla Headline Condensed at 600 and every rule that named the regular
face first fell through to the condensed one. Only artful labels are condensed,
like logos: the wordmark's fallback and the cover's type name keep `--serif`.
Every other label and title, the lane headings and stamps, the ledger captions,
the board's name, BEAN BOARD, the sheet's title, section labels and fact
names, is set in the regular Mozilla Headline through `--display`, variable
200 to 700, 21 KB, the register's own cut.

### The sheet under live preview (4.1.25)

A modal dialog sits in the top layer, above impeccable's picker, so nothing
in the sheet could be put through a round. When the page carries the injected
live script the sheet opens with `show()` and is pinned where the modal one
sits. A shipped board never carries that tag and keeps `showModal()`, the
focus trap and the inert background. The signal is read at open time, because
the tag is injected at the end of the body.

### Taking things out of the sheet

The id and the body are the two things a person takes OUT of the page, and both
were readable and untakeable. A Copy sits on the Id row and beside the
Pretty/Raw toggle.

The note copies the RAW body whichever view is showing. Pretty is a reading
convenience; what you paste into a `beans update` has to be the source the
tracker holds.

Two write paths, because a board is opened both ways. Served from `board.py` it
is a secure context and `navigator.clipboard` exists. Opened straight off disk
as `file://` it is not, and the async API is simply absent, so `execCommand` is
the fallback rather than the first choice. The button reports the result either
way and announces it: a page must not claim a copy it did not make.

### The mark (refined 4.1.27)

The figure is inline SVG, built at load from path data the template carries. Not
a `data:` URI and not a raster: the page is offline-locked and a raster large
enough to stay sharp on a 3x display costs more bytes than the vectors while
still being fixed at one size, which a mark destined for reports and exports
cannot be.

Each figure is two drawings, the artist's refined artwork of 2026-09-11. The
light one is the inked head. The dark one is its inverse: the head drawn as one
pale line (`#d2d7d8`, the artwork's own) over the ground, with the ground showing
through the face. `paintMark` picks the drawing by `prefers-color-scheme` and
repaints on a change; the figure itself is picked once and held, so a scheme
flip shows the same face in its other drawing rather than rolling the dice
again.

This retires the recolour. From 4.1.5 to 4.1.11 the dark scheme was the light
drawing with its inks swapped by three custom properties, and six patches went
into deciding which cool near-black was an ear and which an eye. A drawing made
for the ground is a better thing than a recoloured one; the tokens are gone.

One band for both figures now (`#7c1b16 #bd281c #dd4d25`): the refined artwork
gave Adjudante his ramp. It is still the wipe and still the favicon.

Path data is scaled to a 100-unit box at two decimals, the precision that closes
the hairline seams. His light file carried eight flecks of an earlier crown,
none over three pixels at 300px wide, and the inverse files an invisible
`fill:none` silhouette; all dropped. Kept: six paths for him light, five dark,
eight for her light, nine dark.

### Task lists

`- [ ]` and `- [x]` render as state, not as syntax. A tracker's loop is keeping
those markers current, so a board that printed the brackets and drew no
difference between done and open was showing the one thing you did not need and
hiding the one you did. `[x]` and `[X]` are both done; only a space is open.

The box is drawn, never an `<input>`. This board does not write bean bodies, so a
control that cannot be operated would misrepresent what the page can do. It is
`aria-hidden` decoration with a visually hidden "done, " or "to do, " beside it,
because otherwise the distinction would exist only in pixels. Done steps back to
`--text-faint` rather than striking through: a finished item is still read, and
struck text is slower to read when you need to. An ordinary list is untouched,
and inline markdown still parses inside a task's text.

The card face strips the marker along with the bullet, after it rather than
before, since the marker only starts the line once the bullet is gone.

### The wipe

The one entrance animation on the board, and a deliberate exception: nothing else
moves on arrival, because chrome that announces itself is chrome you wait for.
680 ms, once per load, and `render()` never recreates the nodes so a filter or a
move cannot re-fire it.

It is one timeline, not two things timed near each other. A wipe is a single
constraint: the width of wordmark showing must equal the width the band has
vacated. Both run the same duration, delay, driver and 44% keyframe split, and
share the exit curve. Run them on separate curves and the wordmark finishes
drawing while an opaque bar is still crossing it, which is what the first cut
did. The band unfurls out of the figure by clip-path rather than sliding in from
off-canvas, so it reads as the crest rather than a card dealt from outside.

Under `prefers-reduced-motion` the band is removed and the wordmark's clip-path
is lifted, not merely its animation stopped, or the mark would never appear.

### The display face

Mozilla Headline Condensed SemiBold is embedded as a Latin-1 woff2 subset, 12 KB.
The board is offline-locked, so a face is either carried in the file or absent,
and absent meant every lane heading, stamp and sheet title fell through to Iowan
Old Style: a wide, soft book serif where the brand is a tight condensed slab. A
data URI fetches nothing, which is what validator 24 guards; it bans a `url()`
pointing off-machine, not `url()`. One cut, at 600, so a rule asking for a
lighter weight renders at 600 and only matters if the embedded face fails.

The sheet is a native `<dialog>` opened with `showModal()`, so the focus trap,
`Esc`, the inert background and the top layer all come from the platform. The
top layer is the reason it is a dialog at all: the lane body is
`overflow-y: auto`, and a panel positioned inside it would be clipped.

A card is addressed by its **position** in `cards`, never by its id, for the
same reason a drag is: a deck may legitimately carry two cards with one id. When
a disk refresh or another tab replaces the deck under an open sheet, the card at
that position may no longer be the card that was opened, so the sheet closes and
says so rather than relabelling itself as something else.

## Targeting (which project's board)

| Flag | Board(s) scaffolded |
|---|---|
| *(default)* `--project-dir PATH` | the current breadcrumb-linked project |
| `--project <slug>` | the named project under `{vault}/projects/<slug>` |
| `--all` | one board for **every** project in the vault |

`--project`/`--all` resolve the vault from the cwd breadcrumb (or explicit
`--vault PATH`) and discover projects by **filesystem truth** — every
`{vault}/projects/<slug>/brief.md`, skipping `_`/`.` dirs. The fragile
`projects/_index.md` table is never parsed, so malformed or duplicate rows can't
break discovery. `--all` is error-isolated: one bad project never aborts the batch.

**Containment.** A `--project` slug must be lowercase kebab-case and must
resolve to a path inside the vault, so a traversal slug (or a zone dir that is
a symlink out) is refused rather than scaffolded. A board is written under the
project it belongs to; the one exception is a `--dest` you pass yourself, which
may point at a code repo (see below).

## The features (locked spec)

1. **Scaffold** — `board.py scaffold` writes `board-data.json` (the deck) + a
   self-contained `board.html` (template with the deck injected between its
   `BOARD_DATA` markers; every `<` in the payload is escaped, so task titles
   containing `</script>` or `<!--` can't break the page). Default dest is
   `{vault}/projects/{slug}/board/`; pass `--dest` to target a code repo
   (e.g. `<repo>/_docs/board`). `--dest`/`--data` are single-project only
   (not valid with `--all`).
2. **Seed from tasks** — `--from-tasks` builds one card per `{project}/tasks/*.md`
   note: `code`/`id`/filename → card id, first `# heading`/`title:` → title,
   `status:` → column, `category:`/first non-`task` tag → category,
   `related:` → mono refs, `note:` → note. `_index.md` and roadmap/index files
   (`type: tasks`) are skipped — they aren't per-card task notes. Empty `tasks/`
   yields a clean 6-stage starter deck (no error). Duplicate card ids across
   task notes are disambiguated to the filename stem, with a stderr warning.
   A task note that is not valid UTF-8 is skipped with a named stderr warning
   instead of being decoded lossily: a `U+FFFD` in a card id is permanent,
   because re-saving the note yields a new id and the old card is iceboxed
   forever rather than deleted.
3. **Serve** — `board.py serve --dir DIR` runs a localhost static server so the
   disk-save (File System Access API) works (it needs a secure context, not
   `file://`). `--open` launches the browser; `--port 0` picks a free port
   (the real port is printed); a busy port errors cleanly instead of tracebacking.
4. **Status** — `board.py status [--project SLUG|--all]` prints per-column card
   counts in the terminal (plus a warning for cards in unknown columns) without
   opening a browser. Same targeting flags as scaffold.

## Run

```bash
# the current project, from its tasks/
python3 "$(dirname "$0")/../../../scripts/board.py" scaffold --project-dir "$PROJECT_ROOT" --from-tasks

# a named project by slug (vault resolved from the cwd breadcrumb)
python3 .../scripts/board.py scaffold --project steel-tempest --from-tasks

# every project in the vault, one board each
python3 .../scripts/board.py scaffold --all --from-tasks

# or target a code repo and reuse an existing deck verbatim
python3 .../scripts/board.py scaffold --project-dir "$PROJECT_ROOT" \
  --dest "$REPO/_docs/board" --data "$REPO/_docs/board/board-data.json" --title "My Board"

# serve it (background), then open http://localhost:8787/board.html
python3 .../scripts/board.py serve --dir "$DEST" --port 8787
```

Then present the board, URL first (the line the user acts on): start `serve` in
the background, open the URL, and close with one next step: drag cards, or hit
**connect file** to enable disk auto-save.

## Data model (`board-data.json`)

```json
{
  "version": 1, "boardId": "my-project",
  "title": "My Board", "subtitle": "Work-order board", "updated": "2026-06-24",
  "columns": [{ "id": "backlog", "name": "Backlog" }, ...],
  "categories": ["build", "docs", "infra"],
  "cards": [{ "id": "X-01", "title": "...", "column": "backlog",
              "category": "build", "related": ["SPEC-001"], "notes": "",
              "source": "task", "taskStatus": "todo" }]
  // a beans-seeded card adds, when the bean carries them: priority, tags,
  // parent, blockedBy, blocking, slug, beanPath, createdAt, updatedAt, beansEtag
}
```

- `taskStatus` is the task note's `status:` as of the last reconcile — the
  common ancestor the three-way merge needs. A deck without it is pre-1.0.1
  and reconciles as "unknown", never as "the board moved".

- `boardId` (defaults to the project slug) namespaces the board's browser
  `localStorage` + IndexedDB file-handle, keeping multiple boards independent.
- `done` and `icebox` lanes are marked `BUILT` / `PARKED` in the **lane
  heading**, once for the lane rather than once on every card sitting in it. A
  renamed or added lane sets its own: `"stamp": "SHIPPED"` (or `false` for
  none), `"stampTone": "built"|"parked"`, `"muted": true|false`. A muted lane
  keeps the same well colour as every other lane and quiets its heading.
- A column may carry `"wip": N` — the lane head then shows `count / N` and
  turns red when over the limit.
- Cards whose `column` matches no lane (hand-edited deck, removed column) are
  never invisible — they render in a synthetic **UNFILED** lane you can drag
  them out of. That lane is named **Unfiled**, takes no drops, and shows no
  drop affordance.
- A category colour must be a colour the browser accepts. Anything that could
  fetch (`url(...)`) falls back to the palette hue: the board is served from
  disk and makes no outbound request.
- In-browser view tools (never persisted): a **filter** box narrows by
  id/title/category/ref/note/tag (`Esc` clears all three filters), a **Type**
  rail toggles a category filter, a **Tag** rail under it toggles a tag filter,
  each labelled with the axis it narrows because two rows of identically shaped
  buttons read as one block, and a focused card moves one lane left/right with
  `[` / `]`. A card with no lane of its own is moved from its sheet's lane row.
  The tag rail renders nothing on a deck with no tags, orders tags by how many
  cards carry each one, shows the twelve commonest plus a count of what it left
  out, and always keeps the tag being filtered by within reach. A deck swap that
  retires that tag clears the filter rather than leaving the board looking
  empty.
- The browser persists **only the moves you made by hand**, as
  `{cardId: {from, to}}`. Everything else re-renders from the deck on every
  load, so a re-scaffold that re-seeds a title, category or ref is visible
  immediately, while the lane you dragged a card into survives it. An override
  retires as soon as the deck itself moves that card somewhere else.
- Before writing to a connected `board-data.json` the page **re-reads it and
  merges**, so a reseed's new cards, its icebox moves and lanes you added by
  hand are not overwritten by a tab that has been open since before them. A
  second tab's moves are adopted rather than clobbered. A move that no store
  confirmed is rolled back with a visible message, never left looking saved.

## Idempotency — refresh without clobber

Re-running `board` does not wipe in-progress card state:

- **`--from-tasks` over an existing board → merge.** Per card id: a card present
  in both keeps the **column you dragged it to** (and any board-local `notes`),
  while `title`/`category`/`related` re-seed from the task note. New tasks are
  added in their status-derived column; a card whose task disappeared is moved to
  `icebox` (never deleted). A hand-added card with no `id`, and any second card
  sharing an `id`, is carried through the merge verbatim (it can never match a
  task note); a repeated id also warns on stderr. Deck-level
  `title`/`subtitle`/**`columns`** from disk are preserved — custom lanes you
  added survive a re-seed.
- **Without `--from-tasks` → the on-disk deck is kept untouched** (only `board.html`
  is refreshed from the template, so styling/engine updates land).
- **`--force` → full rebuild from tasks**, discarding dragged columns. It
  requires `--from-tasks` (or `--data`) when a deck already exists — `--force`
  alone would overwrite the board with an empty starter deck, so it refuses.
  Any replace (`--force` or `--data`) first copies the old deck to
  `board/.bak/board-data-<YYYYMMDD-HHMMSS>.json`, newest 5 kept. The copy is
  taken once the replacement deck has been read and validated, so a run that
  fails and writes nothing leaves every earlier backup byte-identical. This
  directory is one of the two backup paths that deliberately live inside the
  vault rather than in `$TMPDIR` — see `reference/state-contract.md` for why,
  and for the other.
- **`--data FILE` → that deck verbatim** (missing standard fields are backfilled).
- A corrupt/unreadable `board-data.json` (or `--data` file) exits non-zero with
  a clear error — it never tracebacks or silently rebuilds.

`board.html` is always re-emitted from the canonical template — never hand-fork
instantiations; change `templates/board.html` and re-run.

## Ambient board: birth and passive refresh

The board also maintains itself. `board.py --ensure --project-dir X` is the
ambient form hooks call: birth or reseed, never clobbering drag state, verdict
on the last stdout line:

| Verdict | Meaning |
|---|---|
| `no-tasks` | no real task notes (only `_index.md` / `type: tasks` roadmaps); nothing written |
| `created` | first real task note found, no deck yet: board born via the scaffold path |
| `reseeded` | deck existed and the clobber-safe `--from-tasks` merge changed it |
| `no-change` | merge would change nothing; deck untouched, mtime included (no sync churn) |
| `html-refreshed` | only board.html was stale (template drift or vanished page); deck untouched |
| `tasks-synced` | the deck was settled but a dragged lane was missing from `tasks/`: the notes were updated to match (see below) |

### The board writes back (v1.0.0)

The board is a view of the vault, but a drag happens in the view. So every
lane change on **any** surface is pushed back into the task note's `status:`:
a browser drag, an Obsidian kanban drag folded in by the read-back, or a deck
you edited by hand. Without this the note stayed stale forever, because the
merge deliberately keeps the dragged column, and `check`, `dream`, `clean`
and the sitrep board line all read the note rather than the deck.

- Each lane has exactly one canonical status (`CANONICAL_STATUS_FOR_COLUMN`),
  which is why `next` is part of the task vocabulary: a lane no status can
  express is a lane that diverges silently.
- A note is rewritten **only** when its own status maps to a different lane
  than the deck has. So an input alias (`wip` sitting in doing) and a
  distinction the lane cannot express (`blocked` sitting in review) both
  survive untouched, per the never-rewrite-aliases rule.
- **Divergence alone does not license a write (v1.0.1).** Deck ≠ note is
  equally the signature of a drag and of a human editing the note in
  Obsidian, and v1.0.0 read every such case as a drag: marking a task done in
  Obsidian was silently reverted by the next ambient hook, and the log line
  said "from the board" about a card nobody had touched. `taskStatus` is the
  common ancestor that separates the two. Unchanged since the last reconcile
  → only the board can have moved, the drag wins and the note is rewritten.
  Changed → the human moved it, the note wins and `merge_deck` moves the card
  instead. Absent (a pre-1.0.1 deck) → neither store is trusted: this path is
  ambient and has no preview or backup, so it records the ancestor and lets
  the next run decide rather than guessing once, destructively.
- Only `status:` changes; other fields, their order, the body and the line
  endings are left exactly as they were. The write is atomic and locked.
- Cards with no task note (hand-added on the board) are never materialized
  into notes here, and since v3 nowhere else either: write the note yourself,
  or use `/adjudant status --capture-task`.
- It converges: once the note matches, the next run reports `no-change`.

### Kanban surface (v0.23.0)

`scaffold --kanban` also writes `board/kanban.md` in the de-facto
obsidian-kanban format (`kanban-plugin: board` frontmatter, `##` lanes,
`- [ ]` cards), so the same deck renders as a native drag-drop board inside
Obsidian. Born only by the flag; refreshed by every scaffold/ensure once it
exists. The plugin's own state — the `%% kanban:settings %%` block, the
`## Archive` section, any other `%% %%` comment block — survives every
rewrite byte-for-byte. Drags made in Obsidian read back on the next reseed
when `kanban.md` is newer than the deck (unknown lanes keep the deck column;
archived cards never count); the deck stays the single truth. After each
kanban write the scaffold prints an `obsidian://open` URI for the file.

Projects that never grow tasks never grow board files. Passive surfaces that
keep the board current without being asked:

- **SessionStart** renders one board status line: per-column counts plus a
  stale flag when any task note is newer than the deck.
- **SessionEnd** runs `board_bridge.py --ensure-only` when a deck already
  exists, so the last edits of the session reach the board. A session end
  never births a board.

Until v3 a third surface wrote here: PostToolUse fired `board_bridge.py
--ensure-only` on any Write or Edit under `tasks/`, which meant the first task
note scaffolded `board-data.json`, `board.html` and a lock file into a project
that had never run `board`. That branch is deleted. A deck is born by
`/adjudant board` and by nothing else; a task-note edit reaches the board at
session end.

Because two surfaces write the deck, the whole read-merge-write runs under an
advisory lock and both files land via a temp file plus `os.replace`, so a
concurrent reader never sees a half-written deck and two writers cannot lose
each other's work. On a mount where locking does not work the write is still
atomic.

Read-only views: `check` renders a board section, `sitrep` one board line.

`scripts/board_bridge.py` ensures the deck and its HTML match `tasks/`, and
does nothing else. Until v3 it also replayed the session task ledger at close:
every id without a `TaskCompleted` event became a task note. Status changes
other than completion fire no events, so abandoned and renamed todos qualified
too, and `tasks/` filled with cards nobody wrote. A task note is now written
only when someone asks for one — `/adjudant status --capture-task`, or your own
hand. The ledger still lives in `$TMPDIR` for the statusline; nothing reads it
into the vault.

## Beans-owned repos

A repo whose `.claude/adjudant` carries `tracker: beans` is seeded from the
Beans CLI instead of from `tasks/`. `_beans.py` is the only module that runs the
binary, and `board.py` absorbs the answer into the ordinary deck shape before
the page sees it, so `board.html` carries no reference to beans at all.

- Lanes come from `_beans.COLUMNS`: `draft`, `todo`, `in-progress`, `completed`
  (marked BUILT, muted) and `scrapped` (marked DROPPED, muted). The deck carries
  `"tracker": "beans"`.
- Every property a bean can carry reaches the card:

  | Card | Bean | | Card | Bean |
  |---|---|---|---|---|
  | `column` | `status` | | `tags` | `tags` |
  | `category` | `type` | | `parent` | `parent` |
  | `notes` | `body` | | `blockedBy` | `blocked_by` |
  | `priority` | `priority`, when not `normal` | | `blocking` | `blocking` |
  | `slug` | `slug` | | `createdAt` | `created_at` |
  | `beanPath` | `path` | | `updatedAt` | `updated_at` |
  | `beansEtag` | `etag` | | `source` | `"beans"` |

  An optional key is written only when the bean carries it, the rule `priority`
  already followed, so the opened card never grows an empty row.
- `notes` comes from the body, which means `list_beans` asks for `--full`.
  Without it every bean arrived with no description and the board rendered a
  deck of empty notes while every bean had one. It is not a second pass and not
  slower in any way that matters: over 89 beans, `list --json` measured 0.03s
  and `list --full --json` measured 0.02s.
- A beans card's `notes` **re-seeds** on merge rather than being preserved.
  Beans owns the body; keeping the on-disk copy would pin an edited body to
  whatever the board last saw. A task-seeded card still keeps its board-local
  note, which no other writer supplies.
- `parent` is its own field, not folded into `related`. The opened card labels a
  parent, and a labelled relation plus an unlabelled copy of the same id in a
  reference list is one fact said twice.
- Write-back is `beans update --if-match`, real compare-and-swap, so this path
  needs no `taskStatus` ancestor to tell a drag from a hand edit.
- Working in a beans-owned repo is beans' own contract, not adjudant's: run
  `beans prime` and follow it. SessionStart prints a one-line banner saying so
  in any repo whose breadcrumb carries `tracker: beans`, read straight from the
  breadcrumb so no hook ever runs the binary (validator 27).
- A beans-owned repo whose CLI is unreachable **refuses and reports**. It never
  falls back to `tasks/`, which is empty by design there: a fallback would
  reseed the board from an empty folder and replace a deck of real beans with an
  empty board.

## Merge provenance (refresh-without-clobber)

Task-seeded cards carry `source: task`. On re-seed, a `source: task` card whose
backing `tasks/*.md` note disappeared is parked in `icebox` (never deleted);
cards **without** task provenance (hand-added in the board UI, or from a
pre-provenance deck) keep their current column untouched.

## Fail conditions

- No breadcrumb at cwd → the target dir is treated as the vault project dir itself
  and the board scaffolds there (deliberate scaffold-anywhere escape hatch; run
  `/adjudant connect` first if you wanted the breadcrumb flow).
- `--project`/`--all` with no resolvable vault → exit non-zero ("pass `--vault PATH`
  or run from a connected project").
- `--project <slug>` not found → exit non-zero, listing the available slugs.
- `--dest`/`--data` combined with `--all` → exit non-zero.
- Template missing / `BOARD_DATA` markers absent → exit non-zero (don't emit a
  half-written board).

## What board does NOT do

- No live sync to GitHub issues / Jira / a database — the JSON is the source of truth.
- No multi-user/server backend — single-file, local, disk-or-browser persistence.
- No card creation in the browser. A card comes from a task note or from a bean:
  write the note yourself, or use `/adjudant status --capture-task`. (This
  section used to claim there was no status write-back to `tasks/` either, which
  the "The board writes back" section above has contradicted since v1.0.0.)
