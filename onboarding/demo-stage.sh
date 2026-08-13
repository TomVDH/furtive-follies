#!/usr/bin/env bash
# demo-stage.sh — build a throwaway project + vault to photograph.
#
# WHY: the field guide needs screenshots, and shooting them against real work
# puts client names, project codes and folder paths into a document you hand to
# other people. This stages a complete, believable, entirely invented setup
# instead, so every screenshot is safe to publish.
#
#   bash demo-stage.sh            # build it at ~/adjudant-demo
#   bash demo-stage.sh --where DIR  # build it somewhere else
#   bash demo-stage.sh --clean    # delete it again
#
# SAFE BY DESIGN: it only ever writes inside its own root, refuses to run if that
# root already holds something it did not create, and never touches ~/.claude,
# your real vaults, or any git repo but the one it makes.

set -euo pipefail

ROOT="${HOME}/adjudant-demo"
CLEAN=0
STAMP=".adjudant-demo-stage"          # marker proving this dir is ours to remove

while [ $# -gt 0 ]; do
  case "$1" in
    --where) ROOT="${2:?--where needs a path}"; shift 2 ;;
    --clean) CLEAN=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

VAULT="${ROOT}/Demo Vault"
PROJ="${ROOT}/acme-web"
SLUG="acme-web"

# ── portable dates: BSD date (macOS) and GNU date (Linux, WSL) ──
_days_ago() {
  if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then date -v-"$1"d +%Y-%m-%d
  else date -d "$1 days ago" +%Y-%m-%d; fi
}
TODAY=$(_days_ago 0); D1=$(_days_ago 1); D3=$(_days_ago 3); D8=$(_days_ago 8); D20=$(_days_ago 20)

# ── --clean ──
if [ "$CLEAN" = "1" ]; then
  if [ ! -e "$ROOT" ]; then echo "nothing at ${ROOT}"; exit 0; fi
  if [ ! -f "${ROOT}/${STAMP}" ]; then
    echo "refusing to delete ${ROOT}: no ${STAMP} marker, so this was not made here." >&2
    exit 1
  fi
  rm -rf "$ROOT"; echo "removed ${ROOT}"; exit 0
fi

if [ -e "$ROOT" ] && [ ! -f "${ROOT}/${STAMP}" ]; then
  echo "refusing to write into ${ROOT}: it already exists and is not a demo stage." >&2
  echo "pass --where <other path>, or move that folder first." >&2
  exit 1
fi

mkdir -p "$ROOT"; : > "${ROOT}/${STAMP}"

# ══════════════════════════════════════════════════════════════════
# 1. the vault
# ══════════════════════════════════════════════════════════════════
mkdir -p "${VAULT}/_index" \
         "${VAULT}/projects/${SLUG}"/{sessions,decisions,tasks,board,notes}

# Home.md carries `type: vault-home`, which is what marks a folder as a vault.
cat > "${VAULT}/Home.md" <<EOF
---
type: vault-home
updated: ${TODAY}
---

# Vault

Persistent knowledge base. Project briefs, decisions, session history: all interlinked and queryable.

## Active Projects

- [[brief|Acme Web]] — the marketing site rebuild

## Recent Decisions

- [[${D8}-static-site-generator]]
- [[${D3}-drop-the-carousel]]

## Recent Sessions

- [[${TODAY}]] — wiring the pricing page
EOF

cat > "${VAULT}/_index/projects.md" <<EOF
---
type: index
tags:
  - index
---

# All Projects

| Project | Type | Status | Decisions | Sessions | Last Session |
|---|---|---|---|---|---|
| [[brief\\|Acme Web]] | coding | active | 2 | 4 | ${TODAY} |
EOF

cat > "${VAULT}/projects/${SLUG}/brief.md" <<EOF
---
type: project
project_type: coding
slug: ${SLUG}
aliases:
  - ${SLUG}
status: active
created: ${D20}
updated: ${TODAY}
tags:
  - project
repo: ""
stack: []
extra_folders: []
relations:
  parents: []
  children: []
  related: []
---

# Acme Web

A rebuild of the marketing site: faster to load, easier to edit, and no longer
dependent on a CMS nobody enjoys using.

## Why

The old site took nine seconds to load on a phone and every copy change needed a
developer. Both of those are fixable.

## Scope

- Pricing, product and about pages
- A contact form that posts somewhere sensible
- Copy editable without a deploy

## Out of scope

- The customer dashboard: it stays where it is.
EOF

# ── sessions: four days of history, so sitrep has something to say ──
_session() {  # $1 date, $2 intent, $3..n log lines
  local d="$1" intent="$2"; shift 2
  { printf -- '---\ntype: session\ndate: %s\nstarted: 09:%02d\nsession_id: []\ntags:\n  - session\n---\n\n' "$d" $((RANDOM % 60))
    printf '> %s\n\n## Log\n\n' "$intent"
    for l in "$@"; do printf -- '- %s\n' "$l"; done
  } > "${VAULT}/projects/${SLUG}/sessions/${d}.md"
}
_session "$D8"  "Pick the stack and get a page rendering." \
  "09:10 · session started" "09:40 · compared three static site generators" \
  "11:05 · first page rendering locally" "11:30 · decision recorded: [[${D8}-static-site-generator]]"
_session "$D3"  "Build the pricing table and kill the carousel." \
  "09:15 · session started" "10:20 · pricing table markup done" \
  "10:55 · carousel dropped, see [[${D3}-drop-the-carousel]]" "11:40 · handoff written"
_session "$D1"  "Contact form, and make the build reproducible." \
  "09:05 · session started" "09:50 · contact form posts to the queue" \
  "11:10 · pinned the build to a known node version"
_session "$TODAY" "Finish the pricing page and check the mobile layout." \
  "09:20 · session started" "09:45 · pricing copy updated from the new sheet"

# ── decisions ──
cat > "${VAULT}/projects/${SLUG}/decisions/${D8}-static-site-generator.md" <<EOF
---
type: decision
status: active
date: ${D8}
tags:
  - decision
supersedes: ""
---

## Decision

Build the site as static pages, generated at deploy time.

## Context

Every copy change needed a developer and a CMS login. Traffic is read-only:
nothing on these pages needs a database at request time.

## Consequence

Pages load in well under a second. Copy lives in markdown next to the code, so
editing is a pull request rather than a ticket.
EOF

cat > "${VAULT}/projects/${SLUG}/decisions/${D3}-drop-the-carousel.md" <<EOF
---
type: decision
status: active
date: ${D3}
tags:
  - decision
supersedes: ""
---

## Decision

Remove the homepage carousel. One fixed hero instead.

## Context

Analytics showed 4% of visitors ever reached the second slide, and the carousel
was the single largest script on the page.

## Consequence

The homepage lost 180 KB. The message no longer moves while people read it.
EOF

# ── tasks: spread across the board's columns so the kanban looks alive ──
_task() {  # $1 file, $2 status, $3 category, $4 title, $5 note
  cat > "${VAULT}/projects/${SLUG}/tasks/$1.md" <<EOF
---
type: task
status: $2
category: "$3"
code: ""
related: []
note: "$5"
tags:
  - task
---

# $4

## Notes

EOF
}
_task pricing-page   doing  build "Finish the pricing page"            "copy is in, layout pending"
_task mobile-nav     doing  build "Fix the mobile navigation"          "menu stays open on tap"
_task contact-form   review build "Contact form posts to the queue"    "needs a second pair of eyes"
_task alt-text       todo   docs  "Alt text on every image"            ""
_task meta-tags      todo   docs  "Page titles and meta descriptions"  ""
_task cache-headers  todo   infra "Set cache headers on assets"        ""
_task build-pin      done   infra "Pin the build to one node version"  ""
_task carousel-strip done   build "Strip the carousel"                 ""
_task dark-mode      icebox build "Dark mode"                          "nice to have, not now"

cat > "${VAULT}/projects/${SLUG}/_handoff.md" <<EOF
---
type: handoff
updated: ${TODAY}
source: manual
tags:
  - handoff
---

# Handoff: Acme Web

## Where things stand

Pricing page is built and the copy is in. The mobile layout is close: the nav
menu stays open after a tap, which is the next thing to fix.

## Next

1. Fix the mobile nav menu
2. Get a second opinion on the contact form
3. Alt text, then meta descriptions

## Watch out for

The build pins one node version on purpose. Bumping it changes the CSS ordering.
EOF

cat > "${VAULT}/projects/${SLUG}/notes/mobile-layout.md" <<EOF
---
type: note
created: ${D1}
updated: ${D1}
tags:
  - note
---

# Mobile layout notes

The nav menu stays open after a tap because the outside-click handler is bound
to the wrong element: it listens on the panel, not the document.

Related: [[brief]]
EOF

# ── .remember: sitrep reads this for "where you left off" and the freshness light ──
mkdir -p "${PROJ}/.remember"
_recent_time() {  # HH:MM, about 40 minutes ago, on either date(1)
  if date -v-40M +%H:%M >/dev/null 2>&1; then date -v-40M +%H:%M; else date -d "40 minutes ago" +%H:%M; fi
}
cat > "${PROJ}/.remember/today-${TODAY}.md" <<EOF
# Today

## 09:20 | main
Picked up the pricing page. Copy came from the new sheet, layout still needs a pass.

## $(_recent_time) | main
Mobile nav bug found: the outside-click handler is bound to the panel, not the
document, so the menu never closes. Fix is one line, next session.
EOF

# ══════════════════════════════════════════════════════════════════
# 2. the code project (varied files, so `ls` next to `ll` has something to show)
# ══════════════════════════════════════════════════════════════════
mkdir -p "${PROJ}"/{src/components,public/images,content,.claude}
cat > "${PROJ}/README.md" <<'EOF'
# Acme Web

The marketing site. Static pages, no CMS.

    npm install
    npm run dev
EOF
cat > "${PROJ}/package.json" <<'EOF'
{
  "name": "acme-web",
  "version": "0.3.1",
  "private": true,
  "scripts": { "dev": "vite", "build": "vite build" }
}
EOF
printf 'node_modules/\ndist/\n.claude/\n' > "${PROJ}/.gitignore"
printf 'PUBLIC_SITE_NAME=Acme\n' > "${PROJ}/.env.example"
cat > "${PROJ}/src/main.js" <<'EOF'
import { mountNav } from "./components/nav.js";

mountNav(document.querySelector("[data-nav]"));
EOF
cat > "${PROJ}/src/components/nav.js" <<'EOF'
// The outside-click handler belongs on the document, not the panel:
// bound to the panel it never fires, and the menu stays open after a tap.
export function mountNav(root) {
  if (!root) return;
  const toggle = root.querySelector("[data-nav-toggle]");
  toggle.addEventListener("click", () => root.classList.toggle("is-open"));
  document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) root.classList.remove("is-open");
  });
}
EOF
cat > "${PROJ}/src/styles.css" <<'EOF'
:root { --ink: #22201e; --paper: #f7f4ef; }
body { margin: 0; background: var(--paper); color: var(--ink); }
EOF
cat > "${PROJ}/content/pricing.md" <<'EOF'
# Pricing

Three plans. No hidden seats, no per-request billing.
EOF
printf 'placeholder\n' > "${PROJ}/public/images/hero.txt"

# breadcrumb: what links this project to the vault
cat > "${PROJ}/.claude/adjudant" <<EOF
slug: ${SLUG}
vault_path: ${VAULT}
vault_name: Demo Vault
mode: project
stale_after_days: 30
EOF

# project-scoped statusline, so nothing global is touched
SL="$(cd "$(dirname "$0")" && pwd)/statusline.sh"
cat > "${PROJ}/.claude/settings.json" <<EOF
{
  "statusLine": { "type": "command", "command": "bash ${SL}" }
}
EOF

# git: a little history, and one modified file so the branch shows dirty
if command -v git >/dev/null 2>&1; then
  (
    cd "$PROJ"
    if [ ! -d .git ]; then
      git init -q -b main
      git add -A
      git -c user.name="Demo" -c user.email="demo@example.com" commit -qm "initial site" >/dev/null
      printf '\n.cache/\n' >> .gitignore          # leaves the tree dirty on purpose
    fi
  )
fi

# ── seed the board from tasks/, so it is there to photograph ──
_ADJ_SCRIPTS=""
for c in "$(cd "$(dirname "$0")/.." && pwd)/adjudant/scripts" "${HOME}/.claude/plugins"; do
  [ -f "${c}/board.py" ] && _ADJ_SCRIPTS="$c" && break
done
if [ -n "$_ADJ_SCRIPTS" ] && command -v python3 >/dev/null 2>&1; then
  python3 "${_ADJ_SCRIPTS}/board.py" scaffold --project "$SLUG" --vault "$VAULT" --from-tasks >/dev/null 2>&1 \
    && echo "  board seeded from tasks/" \
    || echo "  board not seeded (run /adjudant board in the session instead)"
fi

cat <<EOF

  Demo stage ready.

    project   ${PROJ}
    vault     ${VAULT}

  Next:
    cd "${PROJ}" && claude
    (the vault is already linked, so sitrep and board work straight away)

  Remove it later with:
    bash $0 --clean

EOF
