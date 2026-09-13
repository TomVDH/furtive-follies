#!/usr/bin/env bash
# Claude Code statusline v2.6 — shipped by adjudant
# [git] │ [⊙ vault/work-items] │ [model·effort·ctx] │ [agent-bus] │ [24h cost]
#
# v2.6 (2026-09-10): LIVES IN ADJUDANT. This file is adjudant/statusline/
# statusline.sh in the plugin repo, git-tracked and covered by
# test_statusline.py, and reaches ~/.claude through a shim (shim.sh) that
# execs the installed plugin copy. Every adjudant surface it reads (the
# breadcrumb, .beans, board paths, the task ledger) is documented in
# reference/state-contract.md, and a change to one of those now lands in the
# same commit as the matching change here. The git segment also gains one
# glyph: a red ! after the branch when the repo breaks adjudant's branch
# rule (feature beans on feature/<bean-id> in a worktree, main checkout on
# main). See "S1b" below.
#
# v2.5 (2026-09-09): ONE WORK-ITEMS SLOT, and the breadcrumb picks its store.
# beans and the adjudant deck were two separate segments answering the same
# question side by side. Now `tracker:` in .claude/adjudant decides which one
# fills the slot, matching what adjudant's own board does, so the statusline
# and the board can never report from different stores in the same repo.
# The beans count is a clickable link to board.html, same as the deck was.
#
# v2 of claude/statusline-command.sh, which is left untouched so that reverting
# is a one-line edit of ~/.claude/settings.json (statusLine.command). Both
# scripts render side by side. Design decisions and their rationale live in
# ~/.claude/plans/take-a-look-at-nested-music.md; the short version:
#
#   - The bar reports REMAINING, not used. Filled cells are headroom.
#   - Selected values are deviations against a personal baseline rather than
#     bare levels: the commit clock tints against this repo's median gap, the
#     board reports which way it moved rather than only where it stands.
#   - Signals that are always on were cut, because a signal that never varies
#     carries no information (the session-note marker, the always-visible
#     dream age, the commit hash on an ordinary branch).
#   - Glyphs only, never emoji — the convention the v1 palette already stated
#     but broke with ⚡.
input=$(cat)

# ── Palette ───────────────────────────────────────────────────────────────────
R="\033[0m"
HASH="\033[38;2;120;120;125m"
BRANCH="\033[38;2;235;235;235m"
BRANCH_DIRTY="\033[38;2;210;185;120m"
DIFF_ADD="\033[38;2;120;180;105m"
DIFF_DEL="\033[38;2;185;95;85m"
VAULT_OK="\033[38;2;75;155;185m"
VAULT_STALE="\033[2m\033[38;2;150;120;185m"
VAULT_NO="\033[2m\033[38;2;95;95;100m"
GIT_NO="\033[2m\033[38;2;95;95;100m"
MODEL="\033[38;2;235;235;235m"
EFFORT="\033[38;2;165;130;195m"
ULTRA="\033[38;2;170;105;240m"             # vivid purple — ultracode pip beside effort
CTX="\033[38;2;95;175;125m"
CTX_WARN="\033[38;2;195;175;90m"
CTX_HOT="\033[38;2;185;95;85m"
BUS="\033[38;2;95;175;125m"                # green — agent-bus, same as active beans
VAULTOP="\033[38;2;150;190;140m"           # green — adjudant is writing to the vault
BEANS="\033[38;2;205;165;110m"             # tan — open beans, the repo's own tracker
EPIC="\033[38;2;185;120;200m"              # orchid — an epic near enough to finish.
                                           # Deliberately NOT EFFORT/ULTRA purple: those
                                           # live in S4 and mean model, not work.
MILE="\033[38;2;235;205;125m"              # light gold — a milestone near enough to
                                           # finish. Same glyph as EPIC, so the hue is
                                           # the only thing telling them apart: it is
                                           # lifted well off BEANS tan (205,165,110),
                                           # which sits directly to its left whenever
                                           # the in-progress reading is absent.
BUG="\033[38;2;155;155;160m"               # grey — open bugs. Quiet on purpose; a bug
                                           # count is a fact, `!` carries the urgency.
METRIC="\033[2m\033[38;2;130;130;140m"
BOLT_FRESH="\033[38;5;80m"                 # bright — vault state is acceptable
BOLT_STALE="\033[2m\033[38;2;120;120;125m" # dim grey — something needs attention
BLURT="\033[1m\033[38;2;235;90;75m"       # hot red — the break nag, tiers 1 and 2
BLURT_HOT="\033[5m\033[1m\033[38;2;255;60;60m" # + blink where honoured — tier 3
ACCT_C="\033[1m\033[38;2;215;160;70m"     # amber — signed in on a corporate account
ACCT_P="\033[1m\033[38;2;110;185;150m"    # teal — personal; unused while P renders silent
ACCT_UNK="\033[2m\033[38;2;150;150;155m"  # dim — account unrecognised or unreadable
WT="\033[38;2;135;150;215m"                # indigo — a linked worktree, not the main checkout

BOARD_ICON="▦"   # geometric only, no emoji — swap freely (▦ ▤ ▩ ◫ ■ #)
BEANS_ICON="◍"   # beans. Geometric, same rule (◍ ◎ ⬡ ⬢ ○)
BLURT_ICON="⚑"   # the break nag. Glyph, not emoji — same rule as the rest.
BOLT="↯"         # the state light. Was "~" in v1; same two-tone behaviour.
SEP="  \033[38;2;100;100;105m│\033[0m  "  # uniform 2-space padding each side
SP="  "

# ── Why there is no width-aware truncation ───────────────────────────────────
# There used to be a `term_width=$(tput cols)` here, plus a plain-text twin of
# every colored segment, built so the line could shed segments when it outgrew
# the terminal. None of it was ever wired up, and it can't be: a statusline
# command CANNOT learn the terminal width.
#   - stdout is always a pipe (Claude Code captures it), so `tput cols` returns
#     the terminfo default 80 regardless of the real width.
#   - /dev/tty is not available — the subprocess has no controlling terminal.
#   - The stdin JSON carries no width field (checked against the full documented
#     schema: model, workspace, context_window, rate_limits, effort, vim, agent,
#     pr, worktree, cost, output_style — no columns anywhere).
# Claude Code renders the line with Ink's wrap:"truncate", so an over-long line
# is hard-cut at the right edge, never wrapped. Segment ORDER is therefore the
# only lever we have over what survives a narrow terminal — which is why the
# render at the bottom puts the least actionable segment last. Don't reinstate
# tput; measure nothing; just keep the ordering honest.
#
# v2 attacks the same problem from the other end: rather than shedding segments
# under pressure, it spends fewer columns to begin with. The hash is gone on an
# ordinary branch, the session marker is gone entirely, the board and the dream
# stay silent until they have something to say, and S2's "· " separators were
# dropped because every item already leads with its own glyph. A quiet line is
# ~78 columns against v1's ~110, so truncation fires far less often. It is
# mitigation, not a fix: an alarm state is still ~153 columns.

# Bounded file read. TCAT's targets are vault files in iCloud, so the real hazard
# is eviction: reading a "dataless" (evicted) file blocks until iCloud finishes
# downloading it, which would hang the whole repaint. macOS reports st_blocks == 0
# for such files while st_size stays non-zero, so they can be identified from
# metadata alone — without touching the contents that would trigger the download.
# Evicted files are skipped outright rather than waited on; a statusline should
# render stale-but-instant, never block. GNU timeout, where present, bounds the
# rest. macOS ships neither timeout nor gtimeout by default, so plain cat is the
# fallback (the old inline `timeout 0.2s cat` silently produced nothing on macOS).
_materialized() {  # 1 = evicted, would block on read; 0 = safe to read
  local st
  st=$(stat -f '%b %z' "$1" 2>/dev/null) || return 1
  case "$st" in
    "0 0") return 0 ;;   # genuinely empty file — nothing to download
    "0 "*) return 1 ;;   # dataless: has size but no blocks on disk
    *)     return 0 ;;
  esac
}
if   command -v timeout  >/dev/null 2>&1; then TCAT() { _materialized "$1" && timeout  0.2s cat "$1" 2>/dev/null; }
elif command -v gtimeout >/dev/null 2>&1; then TCAT() { _materialized "$1" && gtimeout 0.2s cat "$1" 2>/dev/null; }
else                                           TCAT() { _materialized "$1" && cat "$1" 2>/dev/null; }
fi

# ── Baseline cache ───────────────────────────────────────────────────────────
# v2 needs a little memory: this repo's median commit gap (so the clock can tint
# against YOUR rhythm rather than an arbitrary constant) and a weekly snapshot of
# the board's open count (so it can report direction, not just position).
#
# An earlier draft put this behind a locked background refresher, mirroring
# statusline-tokens-24h.sh. It was dropped: after the design was trimmed the
# work came to ~7ms total, which is cheaper to do inline than to fork a process
# for. So the repaint computes these itself — but only when the cache is older
# than a day, meaning it pays ~7ms once per day and nothing on every other
# repaint.
#
# Lives under ~/.claude, never iCloud: nothing to sync, nothing to conflict
# between machines, and it survives a reboot (unlike /tmp).
CACHE_DIR="$HOME/.claude/statusline-cache"
BASELINE_TTL=86400          # rebuild a repo's medians at most once a day
BOARD_WINDOW=604800         # board direction is a week-over-week figure

# Path -> filename, without spawning md5/cksum. Slashes and spaces are folded,
# then the tail is kept: two paths only collide if their last 120 characters
# match, and a collision costs a wrong baseline, not a crash.
# The length guard is load-bearing: bash's ${k: -120} expands to EMPTY when the
# string is shorter than 120 chars (negative offset past the start), which made
# every short path share the key "" and overwrite one another's baselines.
_ckey() {
  local k="${1//\//_}"; k="${k// /-}"
  [ "${#k}" -gt 120 ] && k="${k: -120}"
  printf '%s' "$k"
}

_cache_get() {  # _cache_get <file> <key> -> value on stdout, empty if absent
  [ -f "$1" ] || return 0
  awk -F= -v k="$2" '$1==k {print $2; exit}' "$1" 2>/dev/null
}

_cache_put() {  # _cache_put <file> <k1> <v1> [<k2> <v2> ...]  (whole-file rewrite)
  local f="$1"; shift
  local tmp="${f}.$$"
  : > "$tmp" 2>/dev/null || return 0
  while [ "$#" -ge 2 ]; do printf '%s=%s\n' "$1" "$2" >> "$tmp" 2>/dev/null; shift 2; done
  mv -f "$tmp" "$f" 2>/dev/null || rm -f "$tmp" 2>/dev/null
}

mkdir -p "$CACHE_DIR" 2>/dev/null

# ── Stdin: one jq pass for everything ────────────────────────────────────────
# Fields are joined on \037 (US), NOT tab. Tab is IFS-whitespace, so bash would
# collapse runs of it and an absent middle field would silently shift every
# later field left (an empty effort.level would land ctx% in $effort_raw).
# \037 is not whitespace, so empty fields are preserved positionally.
IFS=$'\037' read -r cwd proj_dir sid model_raw effort_raw used_pct rl5_raw ctx_size sess_cost_raw <<<"$(
  printf '%s' "$input" | jq -r '[
    (.workspace.current_dir // .cwd // ""),
    (.workspace.project_dir // .workspace.current_dir // .cwd // ""),
    (.session_id // ""),
    (.model.display_name // .model.id // ""),
    (.effort.level // ""),
    ((.context_window.used_percentage // "") | tostring),
    ((.rate_limits.five_hour.used_percentage // "") | tostring),
    ((.context_window.context_window_size // "") | tostring),
    ((.cost | if type == "number" then . elif type == "object" then (.total_cost_usd // .totalCost // 0) else 0 end) | tostring)
  ] | join("")' 2>/dev/null)"

# Where the breadcrumb is read from. The project root, except in a linked
# worktree (S1 below moves it to the main checkout, see there).
crumb_dir="$proj_dir"

NOW=$(date +%s)

# ── S0: grind clock + break blurt ────────────────────────────────────────────
# Every so often, once you have been at this too long, the entire statusline is
# replaced by an insult telling you to get up. This is the only section that
# renders by REPLACING the line rather than contributing a segment to it, and
# the only one that exits early — see the render note at the bottom of the file.
#
# THE CLOCK IS NOT THE SESSION. /tmp/claude-session-epoch (written by the
# SessionStart hook) is global and stomped by every new window, so a "3h
# session" measured that way resets the moment you open a second tab — exactly
# backwards for a break nag. Instead the clock is derived from the repaint
# itself: the statusline is only rendered when Claude Code renders, so a gap in
# `seen` IS idle time. Walk away for GRIND_IDLE and the clock restarts; keep
# working across four windows and it keeps counting. One human, one machine,
# one shared cache file — deliberately not session-keyed.
#
# The cost is one builtin read per repaint and one redirect at most every
# GRIND_SEEN_TTL seconds — no forks on either path. See the note by
# GRIND_CACHE for why this section does not use the _cache_* helpers.
#
# Set GRIND_ON=0 to switch the whole thing off without touching anything else.
GRIND_ON=1
GRIND_IDLE=1800         # 30 min without a repaint = you took a break; clock resets
GRIND_SEEN_TTL=20       # only rewrite `seen` when it is this stale (repaint is ~3Hz)
GRIND_T1=10800          # 3h — the nagging starts
GRIND_T2=18000          # 5h — it gets mean
GRIND_T3=25200          # 7h — it stops pretending to be on your side

blurt_msg=""; blurt_col=""; blurt_dur=""
if [ "$GRIND_ON" = 1 ]; then
  # Deliberately NOT _cache_get/_cache_put. Those are shaped for the sections
  # that read one key occasionally; this section needs five keys on EVERY
  # repaint, and _cache_get forks an awk inside a command substitution — ten
  # forks per repaint, measured at ~15ms, which is 10% of the whole statusline
  # for a joke. One space-separated line read by the `read` builtin costs zero
  # forks. An old key=value file lands non-numeric in g_start, fails the guard
  # below, and self-heals into the new format on the first repaint.
  GRIND_CACHE="${CACHE_DIR}/grind"
  g_start=0; g_seen=0; g_next=0; g_until=0; g_line=0
  [ -f "$GRIND_CACHE" ] && read -r g_start g_seen g_next g_until g_line < "$GRIND_CACHE" 2>/dev/null
  # Same non-numeric guard the rest of the file uses. A corrupt cache must
  # degrade to "no nag", never to an arithmetic error that kills the statusline.
  case "$g_start" in (*[!0-9]*|"") g_start=0;; esac
  case "$g_seen"  in (*[!0-9]*|"") g_seen=0;;  esac
  case "$g_next"  in (*[!0-9]*|"") g_next=0;;  esac
  case "$g_until" in (*[!0-9]*|"") g_until=0;; esac
  case "$g_line"  in (*[!0-9]*|"") g_line=0;;  esac

  # Reset on: first ever run, a real break, or clock skew in either direction
  # (iCloud-synced script, two machines, one cache — assume nothing).
  if [ "$g_start" -eq 0 ] || [ "$g_seen" -eq 0 ] \
     || [ $(( NOW - g_seen )) -gt "$GRIND_IDLE" ] \
     || [ "$g_seen" -gt "$NOW" ] || [ "$g_start" -gt "$NOW" ]; then
    g_start=$NOW; g_seen=$NOW; g_next=0; g_until=0; g_line=0
    printf '%s %s 0 0 0\n' "$g_start" "$g_seen" > "$GRIND_CACHE" 2>/dev/null
  fi

  g_elapsed=$(( NOW - g_start ))
  g_write=0
  [ $(( NOW - g_seen )) -ge "$GRIND_SEEN_TTL" ] && { g_seen=$NOW; g_write=1; }

  if [ "$g_elapsed" -ge "$GRIND_T1" ]; then
    # Tier picks the pool, the interval, and how long the takeover lasts. The
    # intervals SHRINK as the tiers escalate, so ignoring it costs you.
    if [ "$g_elapsed" -ge "$GRIND_T3" ]; then
      g_min=300; g_max=600; g_lng=60; blurt_col="$BLURT_HOT"
      g_pool=(
        "Fucking hell. You're not shipping, you're marinating. Close it."
        "This stopped being work and started being a symptom."
        "Whatever this is, it isn't productivity. Get up."
        "You are a liability to this repo right now. Log off."
        "Nobody is going to thank you for this. Not one person."
        "This is the shift where you delete the wrong branch. Stop."
        "Close the laptop. Close it. I'm not asking."
        "You've been here so long the chair is warmer than you are."
        "Whatever you 'finish' now you will rewrite. Skip the round trip."
        "Stop. You aren't thinking any more, you're just typing."
        "All you've built in the last hour is a headache. Well done."
        "Go the fuck to sleep. The code will still be broken tomorrow."
      )
    elif [ "$g_elapsed" -ge "$GRIND_T2" ]; then
      g_min=600; g_max=1200; g_lng=45; blurt_col="$BLURT"
      g_pool=(
        "Nothing you write from here is good. Go eat something."
        "This isn't discipline any more. It's stubbornness with extra steps."
        "Whose fucking approval do you think you're earning?"
        "You are actively making this codebase worse. Step away."
        "Welcome to the part of the day where you introduce the bugs."
        "Your judgement clocked off at hour four. You've been driving blind since."
        "Go outside. The sun is a real thing and it's free."
        "This is a hostage situation and you're playing both parties."
        "You're not solving it. You're staring at it harder."
        "Eat. Drink. Piss. Any order. Just fucking do one of them."
        "Every commit from here is one you revert tomorrow."
        "You've stopped programming and started haunting the machine."
      )
    else
      g_min=1200; g_max=2400; g_lng=45; blurt_col="$BLURT"
      g_pool=(
        "Your spine has filed a formal complaint."
        "Stand up. Yes, now. I'll wait, you gremlin."
        "Sitting this long is a medical event, not a work ethic."
        "Piss, water, daylight. Pick two. Minimum."
        "You've stared at this long enough to hallucinate a solution."
        "Blink. Properly. Both eyes. Christ."
        "The bug isn't going anywhere. Neither are you, apparently."
        "Whatever you're about to type, you'll delete it after a sandwich."
        "Go and be a mammal for ten minutes."
        "Your hips are fusing to that chair as we speak."
        "Nobody's watching. Get up and stretch, you absolute unit."
        "The good ideas left about forty minutes ago."
      )
    fi
    # A tier change mid-linger can leave the stored index past the new pool's
    # end, and a schedule set under a slacker tier can sit further out than the
    # tighter tier would ever allow — pull both back into range so escalation
    # takes effect immediately rather than after the old timer finally expires.
    [ "$g_line" -ge "${#g_pool[@]}" ] && g_line=0
    if [ "$g_next" -eq 0 ] || [ "$g_next" -gt $(( NOW + g_max )) ]; then
      g_next=$(( NOW + g_min + RANDOM % (g_max - g_min + 1) )); g_write=1
    fi

    if [ "$NOW" -lt "$g_until" ]; then
      # Mid-blurt. Re-read the STORED index rather than re-rolling, or the
      # insult would reshuffle on every repaint and be unreadable.
      blurt_msg="${g_pool[$g_line]}"
    elif [ "$NOW" -ge "$g_next" ]; then
      g_line=$(( RANDOM % ${#g_pool[@]} ))
      g_until=$(( NOW + g_lng ))
      g_next=$(( g_until + g_min + RANDOM % (g_max - g_min + 1) ))
      g_write=1
      blurt_msg="${g_pool[$g_line]}"
    fi
    [ -n "$blurt_msg" ] && blurt_dur=$(printf '%dh %02dm' $(( g_elapsed / 3600 )) $(( (g_elapsed % 3600) / 60 )))
  fi

  [ "$g_write" = 1 ] && printf '%s %s %s %s %s\n' \
    "$g_start" "$g_seen" "$g_next" "$g_until" "$g_line" > "$GRIND_CACHE" 2>/dev/null

  # The takeover. Everything below this point is skipped while a blurt is live:
  # no git, no vault, no token refresher. All of those are TTL-cached and
  # self-healing, so losing 45s of them costs nothing — and it means the loud
  # version of the statusline is also the cheapest one.
  # %s for the message, never %b: the pool is prose and must not be walked for
  # backslash escapes.
  if [ -n "$blurt_msg" ]; then
    printf '%b%s %s — %s%b\n' "$blurt_col" "$BLURT_ICON" "$blurt_dur" "$blurt_msg" "$R"
    exit 0
  fi
fi

# ── S0a: ops flash takeover ──────────────────────────────────────────────────
# Same pattern as the break blurt. A live flash paints the whole line.
# Everything else is skipped. The break nag wins if both are live.
#
# File: `${CACHE_DIR}/ops-<key>`. Line one: `<unix ts> <message>`.
# Writer: scripts/_ops_flash.py, and nothing else. Hooks and verbs call it.
# hooks/scripts/_ops_flash.sh wraps it for bash.
# Key: the session root, absolute, folded to its main checkout, then _ckey.
# The worktree fold repeats S1's walk. S1 has not run yet. No fork.
# A worker in .worktrees/<bean> flashes the orchestrator's bar this way.
#
# Seen, then ten seconds. The first paint appends `seen <ts>`.
# The TTL counts from that stamp, never from the write.
# A newer flash rewrites the file without a seen line. The clock restarts.
# An unseen flash older than OPS_FLASH_MAX_AGE is dropped, not shown late.
OPS_FLASH_TTL="${ADJUDANT_OPS_FLASH_TTL:-10}"
case "$OPS_FLASH_TTL" in (*[!0-9]*|"") OPS_FLASH_TTL=10;; esac
OPS_FLASH_MAX_AGE="${ADJUDANT_OPS_FLASH_MAX_AGE:-600}"
case "$OPS_FLASH_MAX_AGE" in (*[!0-9]*|"") OPS_FLASH_MAX_AGE=600;; esac
if [ -n "${CACHE_DIR:-}" ] && [ -n "${cwd:-}" ]; then
  _of_dir="$cwd"
  _d="$cwd"
  while [ -n "$_d" ] && [ "$_d" != "/" ]; do
    if [ -e "${_d}/.git" ]; then
      if [ -f "${_d}/.git" ]; then
        read -r _k _p < "${_d}/.git" 2>/dev/null
        case "$_p" in *"/.git/worktrees/"*) _of_dir="${_p%/.git/worktrees/*}" ;; esac
      fi
      break
    fi
    _d="${_d%/*}"
  done
  _of_file="${CACHE_DIR}/ops-$(_ckey "$_of_dir")"
  if [ -f "$_of_file" ]; then
    _of_ts=0; _of_text=""; _of_k=""; _of_seen=""
    { read -r _of_ts _of_text; read -r _of_k _of_seen; } < "$_of_file" 2>/dev/null
    case "$_of_ts" in (*[!0-9]*|"") _of_ts=0;; esac
    [ "$_of_k" = "seen" ] || _of_seen=""
    case "$_of_seen" in (*[!0-9]*) _of_seen="";; esac
    _of_live=""
    if [ -n "$_of_text" ]; then
      if [ -z "$_of_seen" ]; then
        if [ $(( NOW - _of_ts )) -lt "$OPS_FLASH_MAX_AGE" ]; then
          printf 'seen %s\n' "$NOW" >> "$_of_file" 2>/dev/null
          _of_live=1
        fi
      elif [ $(( NOW - _of_seen )) -lt "$OPS_FLASH_TTL" ]; then
        _of_live=1
      fi
    fi
    if [ -n "$_of_live" ]; then
      printf '%b⚡ %s%b\n' "\033[38;2;100;140;185m" "$_of_text" "$R"
      exit 0
    fi
  fi
fi

# ── S0b: account badge — amber C for corporate, silent for personal ──────────
# Which Anthropic account is signed in, reduced to a single letter. Nothing in
# the statusline's stdin carries this, so it comes from ~/.claude.json's
# oauthAccount.emailAddress — of which ONLY the domain is ever compared and only
# the letter is ever rendered. The address itself never reaches the terminal.
#
# SILENCE MEANS CONFIRMED PERSONAL. Corporate shows an amber C, personal shows
# nothing (a deliberate choice — the common case should cost zero columns), and
# anything else shows a dim "?". That last arm is the load-bearing one: an
# unreadable file or an unfamiliar domain must NOT render as "nothing", or a
# failure to determine the account becomes indistinguishable from a personal
# session, and the badge would be lying exactly when it matters.
#
# ~/.claude.json is rewritten on almost every interaction, so a read can and
# does land mid-write and come back empty. Hence the cache is not only a speed
# trick, it is the fallback: an empty read keeps the last known letter instead
# of blanking the badge, which is what stops it flickering on and off. Its mtime
# is useless as a cache key for the same reason (it changes constantly), so this
# is a plain TTL — the signed-in account changes at most a few times a year, and
# switching it requires a /login that takes far longer than ACCT_TTL anyway.
#
# jq over the whole 144K file measures ~6ms — faster than `grep -m1 -o`, which
# has no newline to bail at and must regex-scan the entire single-line document.
# The TTL amortises that to roughly nothing.
ACCT_CACHE="${CACHE_DIR}/account"
ACCT_TTL=30                                    # seconds; staleness window on a /login
ACCT_CORP="zenatech.com"                       # space-separated; C
ACCT_PERS="pm.me proton.me protonmail.com"     # space-separated; silent

acct_col=""; acct_ts=0; acct_letter=""
[ -f "$ACCT_CACHE" ] && read -r acct_ts acct_letter < "$ACCT_CACHE" 2>/dev/null
case "$acct_ts" in (*[!0-9]*|"") acct_ts=0;; esac

if [ $(( NOW - acct_ts )) -ge "$ACCT_TTL" ] && [ -r "$HOME/.claude.json" ]; then
  acct_dom=$(jq -r '((.oauthAccount.emailAddress // "") | ascii_downcase | split("@") | .[1] // "")' \
             "$HOME/.claude.json" 2>/dev/null)
  # An empty domain is a failed or mid-write read: fall through holding whatever
  # the cache already had, and try again on the next repaint.
  if [ -n "$acct_dom" ]; then
    acct_letter="?"
    for _d in $ACCT_CORP; do [ "$acct_dom" = "$_d" ] && { acct_letter="C"; break; }; done
    if [ "$acct_letter" = "?" ]; then
      for _d in $ACCT_PERS; do [ "$acct_dom" = "$_d" ] && { acct_letter="P"; break; }; done
    fi
    # Plain redirect, not _cache_put: no temp file, no mv, no fork.
    printf '%s %s\n' "$NOW" "$acct_letter" > "$ACCT_CACHE" 2>/dev/null
  fi
fi

acct_col=""
case "$acct_letter" in
  C) SEP="  ${ACCT_C}│${R}  " ;;
  P) SEP="  \033[38;2;100;100;105m│\033[0m  " ;;
  *) SEP="  \033[38;2;100;100;105m│\033[0m  " ;;
esac

# ── S0c: peer presence — how many Claude Code sessions on this machine ──────
# pgrep -x claude counts running instances of the claude binary. Cached at
# PEER_TTL seconds because pgrep forks a process and the count changes slowly.
# Shows a circled digit (②③…) in dim when 2+ sessions are active. The test
# suite overrides the count via ADJUDANT_PEER_COUNT to avoid pgrep in CI.
PEER_TTL=30
peer_col=""
peer_count="${ADJUDANT_PEER_COUNT:-}"
if [ -z "$peer_count" ]; then
  _peer_cache="${CACHE_DIR}/peers"
  _peer_ts=0; peer_count=0
  [ -f "$_peer_cache" ] && read -r _peer_ts peer_count < "$_peer_cache" 2>/dev/null
  case "$_peer_ts" in (*[!0-9]*|"") _peer_ts=0;; esac
  case "$peer_count" in (*[!0-9]*|"") peer_count=0;; esac
  if [ $(( NOW - _peer_ts )) -ge "$PEER_TTL" ]; then
    peer_count=$(pgrep -x claude 2>/dev/null | wc -l)
    peer_count="${peer_count##* }"
    case "$peer_count" in (*[!0-9]*|"") peer_count=0;; esac
    printf '%s %s\n' "$NOW" "$peer_count" > "$_peer_cache" 2>/dev/null
  fi
fi
case "$peer_count" in (*[!0-9]*|"") peer_count=0;; esac
if [ "$peer_count" -gt 1 ] 2>/dev/null; then
  case "$peer_count" in
    2) _pg="②" ;; 3) _pg="③" ;; 4) _pg="④" ;; 5) _pg="⑤" ;;
    6) _pg="⑥" ;; 7) _pg="⑦" ;; 8) _pg="⑧" ;; 9) _pg="⑨" ;;
    *) _pg="⑨+" ;;
  esac
  peer_col="${METRIC}${_pg}${R}"
fi

# ── S1: [⊘ hash]  branch[*]  +N -N  ◷age  ↑↓ ─────────────────────────────────
# One `status --porcelain=v2 --branch` replaces six separate git invocations
# (rev-parse --is-inside-work-tree / symbolic-ref / rev-parse --short /
# diff-index / ls-files --others / rev-list --count --left-right). Headers are
# always emitted before entry lines, so the loop can stop as soon as both the
# dirty and untracked flags are set.
branch=""; tracked_dirty=""; untracked=""; hash_plain=""; ab_raw=""; initial=""
gs=$(git -C "$cwd" --no-optional-locks status --porcelain=v2 --branch --untracked-files=normal 2>/dev/null)
if [ $? -eq 0 ] && [ -n "$gs" ]; then
  while IFS= read -r gline; do
    case "$gline" in
      '# branch.oid '*)  hash_plain="${gline#\# branch.oid }" ;;
      '# branch.head '*) branch="${gline#\# branch.head }"    ;;
      '# branch.ab '*)   ab_raw="${gline#\# branch.ab }"      ;;
      '1 '*|'2 '*|'u '*) tracked_dirty="*" ;;
      '? '*)             untracked="?"     ;;
    esac
    [ -n "$tracked_dirty" ] && [ -n "$untracked" ] && break
  done <<< "$gs"
  # `(detached)` / `(initial)` are porcelain sentinels, not real values.
  [ "$branch" = "(detached)" ] && branch=""
  if [ "$hash_plain" = "(initial)" ]; then
    hash_plain=""; initial=1
  else
    # Fixed 7 chars. `rev-parse --short` auto-scales its abbreviation with repo
    # size, which would make the leftmost field change width as a repo grows —
    # unwanted in a statusline. Deterministic beats git's heuristic here.
    hash_plain="${hash_plain:0:7}"
  fi
fi
dirty="${tracked_dirty}${untracked}"

if [ -z "$hash_plain" ] && [ -z "$branch" ] && [ -z "$initial" ]; then
  # Fallback notice — git probe failed (not a repo, or .git pointer broken).
  # Keeps the section visible so the user sees a diagnostic, not blank space.
  s1_col="${GIT_NO}no git${R}"
else
  s1_col=""

  # -- Linked worktree? In a linked worktree .git is a FILE holding
  #    "gitdir: <repo>/.git/worktrees/<name>"; a main checkout has a .git
  #    DIRECTORY. A submodule ALSO uses a .git file, so the file type alone is
  #    not the test — the pointer is: /worktrees/ is a worktree, /modules/ is a
  #    submodule and gets no marker.
  #
  #    `git rev-parse --git-common-dir` answers the same question for an 11ms
  #    fork. This walks up with parameter expansion instead and costs nothing:
  #    ${_d%/*} rather than dirname, which is why this loop does not look like
  #    the .agent-bus walk in S5 (that one forks a dirname per level).
  #
  #    Why it earns a column: a worktree is a DISPOSABLE checkout. Committing or
  #    stashing here while believing you are in the main tree is how work gets
  #    orphaned when the worktree is later pruned. Always-on inside a worktree,
  #    invisible everywhere else — which is the point, since being in one is the
  #    rare state.
  wt=""
  _d="$cwd"
  while [ -n "$_d" ] && [ "$_d" != "/" ]; do
    if [ -e "${_d}/.git" ]; then
      if [ -f "${_d}/.git" ]; then
        # 2 fields, so a repo path containing spaces lands whole in $_p.
        read -r _k _p < "${_d}/.git" 2>/dev/null
        case "$_p" in *"/worktrees/"*) wt="⑂" ;; esac
      fi
      break
    fi
    _d="${_d%/*}"
  done
  # Worktree count: how many linked worktrees exist beside the main checkout.
  # One git call, cached at WT_COUNT_TTL. Shows as ⑂3 (count after the glyph)
  # when >1 worktree; plain ⑂ when you are the only one. On the main checkout,
  # the count is appended in dim after the branch when >0.
  _wt_count=0
  WT_COUNT_TTL=60
  _wt_cache="${CACHE_DIR}/wt-$(_ckey "$cwd")"
  _wt_ts=0
  [ -f "$_wt_cache" ] && read -r _wt_ts _wt_count < "$_wt_cache" 2>/dev/null
  case "$_wt_ts" in (*[!0-9]*|"") _wt_ts=0;; esac
  case "$_wt_count" in (*[!0-9]*|"") _wt_count=0;; esac
  if [ $(( NOW - _wt_ts )) -ge "$WT_COUNT_TTL" ]; then
    _wt_count=$(git -C "$cwd" worktree list --porcelain 2>/dev/null | grep -c '^worktree')
    _wt_count=$(( _wt_count - 1 ))
    [ "$_wt_count" -lt 0 ] && _wt_count=0
    printf '%s %s\n' "$NOW" "$_wt_count" > "$_wt_cache" 2>/dev/null
  fi

  if [ -n "$wt" ]; then
    if [ "$_wt_count" -gt 1 ] 2>/dev/null; then
      s1_col+="${WT}${wt}${HASH}${_wt_count}${R} "
    else
      s1_col+="${WT}${wt}${R} "
    fi
  fi
  # The breadcrumb is git-ignored, so a linked worktree never carries one and
  # every vault signal below went dark there. The pointer just read names the
  # main checkout: strip "/.git/worktrees/<name>" and read ITS breadcrumb. The
  # session-start hook symlinks one in for adjudant's other readers; this is
  # the bar being right on the first repaint regardless, and in a worktree
  # made by hand. Still no fork.
  if [ -n "$wt" ] && [ ! -e "${proj_dir}/.claude/adjudant" ]; then
    _main="${_p%/.git/worktrees/*}"
    [ -f "${_main}/.claude/adjudant" ] && crumb_dir="$_main"
  fi

  # -- Identity. v1 printed the hash on every repaint; v2 prints it only when it
  #    is news. On an ordinary branch the branch name already identifies you and
  #    the hash is nine columns of noise, so it is suppressed. It comes back on a
  #    detached HEAD, where it is the ONLY identity available — marked with ⊘ so
  #    the state is explicit rather than inferred from an absence.
  #
  #    The name is an OSC 8 link to the folder the session is actually in:
  #    workspace.current_dir, which is the worktree dir inside a linked worktree
  #    and the project dir otherwise. Cmd+click opens it in Finder; a terminal
  #    without OSC 8 shows the bare name. file:// wants spaces as %20, and a
  #    OneDrive path has them. Same wrap as the board and handoff links in S2.
  #
  #    A plain checkout gets ⎇ in front, in the branch white, so it reads as
  #    the counterpart of the indigo ⑂ a linked worktree shows, not as the
  #    absence of one. Never both; a detached HEAD keeps ⊘ alone.
  if [ -z "$branch" ] && [ -n "$hash_plain" ]; then
    s1_col+="${BRANCH_DIRTY}⊘${R} ${HASH}${hash_plain}${R}"
    [ -n "$dirty" ] && s1_col+="${HASH}${dirty}${R}"
  else
    if [ -z "$wt" ]; then
      s1_col+="${BUG}⎇${R} "
    fi
    branch_lnk="\033]8;;file://${cwd// /%20}\033\\\\${branch}\033]8;;\033\\\\"
    if [ -n "$tracked_dirty" ]; then
      s1_col+="${BRANCH_DIRTY}${branch_lnk}${dirty}${R}"
    elif [ -n "$untracked" ]; then
      s1_col+="${BUG}${branch_lnk}${R}${HASH}${untracked}${R}"
    else
      s1_col+="${BUG}${branch_lnk}${R}"
    fi
    # On the main checkout, show the worktree count after the branch when >0
    if [ -z "$wt" ] && [ "$_wt_count" -gt 0 ] 2>/dev/null; then
      s1_col+=" ${HASH}⑂${_wt_count}${R}"
    fi
  fi

  # An initial commit has no oid to show and no history to measure, so it says
  # so plainly and skips the diff/age/ahead blocks below.
  if [ -n "$initial" ]; then
    s1_col+="${SP}${VAULT_NO}initial${R}"
  else
    stats=$(git -C "$cwd" --no-optional-locks diff --shortstat HEAD 2>/dev/null)
    ins=$(echo "$stats" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+')
    del=$(echo "$stats" | grep -oE '[0-9]+ deletion'  | grep -oE '[0-9]+')
    if [ -n "$ins" ] || [ -n "$del" ]; then
      ins="${ins:-0}"; del="${del:-0}"
      s1_col+=" ${DIFF_ADD}${ins}${R}${HASH}/${R}${DIFF_DEL}${del}${R}"
    fi

    # Last commit, human-readable age — sits beside the diff delta so "when did I
    # last commit" is answerable without leaving the git segment. Geometric glyph
    # (◷) rather than an emoji clock face, matching the icon convention above.
    # Its own block, NOT folded into the delta above: the delta only prints when
    # the tree is dirty, and the age is worth seeing on a clean tree too.
    # Sub-minute reads "now" rather than "0m" (0m is a common state here and reads
    # like a bug). Clamped at 0 so a skewed clock cannot print a negative age.
    #
    # v2 tints it. "Three hours since a commit" is only interesting relative to
    # how you actually work in THIS repo: it is nothing in a repo you touch
    # monthly and a lot in one where you commit every forty minutes. So the age
    # is compared against this repo's own median inter-commit gap — amber at 2x,
    # red at 4x. With no baseline yet the tint simply never fires and the clock
    # stays exactly as grey as it is in v1.
    commit_ts=$(git -C "$cwd" --no-optional-locks log -1 --format=%ct 2>/dev/null)
    case "$commit_ts" in (*[!0-9]*|"") commit_ts="";; esac
    if [ -n "$commit_ts" ]; then
      delta=$(( NOW - commit_ts ))
      [ "$delta" -lt 0 ] && delta=0
      if   [ "$delta" -lt 60 ];     then ago="now"
      elif [ "$delta" -lt 3600 ];   then ago="$(( delta / 60 ))m"
      elif [ "$delta" -lt 86400 ];  then ago="$(( delta / 3600 ))h"
      elif [ "$delta" -lt 604800 ]; then ago="$(( delta / 86400 ))d"
      else                               ago="$(( delta / 604800 ))w"
      fi

      repo_cache="${CACHE_DIR}/repo-$(_ckey "$cwd")"
      gap_median=$(_cache_get "$repo_cache" gap_median)
      gap_ts=$(_cache_get "$repo_cache" gap_ts)
      case "$gap_median" in (*[!0-9]*) gap_median="";; esac
      case "$gap_ts"     in (*[!0-9]*|"") gap_ts=0;; esac
      if [ $(( NOW - gap_ts )) -ge "$BASELINE_TTL" ]; then
        # ~5ms, once a day. Fifty commits is enough for a stable median and
        # short enough not to matter in a large repo.
        gap_median=$(git -C "$cwd" --no-optional-locks log -50 --format=%ct 2>/dev/null | awk '
          NR==1 { prev=$1; next }
          { g[++n] = prev - $1; prev = $1 }
          END {
            if (n < 3) exit
            for (i=2; i<=n; i++) { v=g[i]; j=i-1; while (j>0 && g[j]>v) { g[j+1]=g[j]; j-- } g[j+1]=v }
            if (n % 2) print g[(n+1)/2]; else print int((g[n/2] + g[n/2+1]) / 2)
          }')
        case "$gap_median" in (*[!0-9]*|"") gap_median="";; esac
        _cache_put "$repo_cache" gap_median "${gap_median:-0}" gap_ts "$NOW"
      fi

      # Show age only on 15-minute intervals (10-second window)
      if [ $(( NOW % 900 )) -lt 10 ]; then
        s1_col+=" ${METRIC}◷${ago}${R}"
      fi
    fi

    # Ahead/behind upstream — catches forgot-to-push + behind-other-machine on
    # the 2-machine iCloud workflow. `# branch.ab` is absent without an
    # upstream, so this stays silent exactly as before. Format is "↑A ↓B".
    if [ -n "$branch" ] && [ -n "$ab_raw" ]; then
      ahead="${ab_raw%% *}";  ahead="${ahead#+}"
      behind="${ab_raw##* }"; behind="${behind#-}"
      if [ "$ahead" != "0" ]; then
        _ac="$HASH"
        [ "$ahead" -ge 5 ] 2>/dev/null && _ac="\033[38;2;100;140;185m"
        [ "$ahead" -ge 10 ] 2>/dev/null && _ac="$CTX_WARN"
        s1_col+=" ${_ac}⇡${ahead}${R}"
      fi
      if [ "$behind" != "0" ]; then
        s1_col+=" ${CTX_HOT}⇣${behind}${R}"
      fi
    fi
    # Fetch staleness: if FETCH_HEAD is >1h old, hint a fetch
    _fh="${cwd}/.git/FETCH_HEAD"
    [ ! -f "$_fh" ] && _fh="${cwd}/.git"
    if [ -f "${cwd}/.git/FETCH_HEAD" ]; then
      _fh_ts=$(stat -f%m "${cwd}/.git/FETCH_HEAD" 2>/dev/null || stat -c%Y "${cwd}/.git/FETCH_HEAD" 2>/dev/null)
      case "$_fh_ts" in (*[!0-9]*|"") _fh_ts="";; esac
      if [ -n "$_fh_ts" ]; then
        _fh_age=$(( NOW - _fh_ts ))
        if [ "$_fh_age" -ge 14400 ]; then
          s1_col+=" ${CTX_WARN}⟳${R}"
        elif [ "$_fh_age" -ge 3600 ]; then
          s1_col+=" ${HASH}⟳${R}"
        fi
      fi
    fi
  fi
fi

# ── S2: ↯ vault  adjudant slug · state + telemetry ───────────────────────────
# Split into a core and a tail. The core carries identity and everything that
# demands action; the tail is ambient telemetry. They are separate strings
# because the tail is the part worth losing first if the line is ever cut.
#
# v2 drops the "· " separators that used to join these. Every item leads with
# its own glyph (⤳ ▦ ☾ ≡), so the dots were delimiting something already
# delimited — eight columns of pure punctuation.
beans_label=""; beans_pip=""; beans_extra=""; beans_slotted=""
# WHICH STORE OWNS THE WORK ITEMS is the breadcrumb's call, not a guess from
# what happens to be on disk. adjudant's board keys on `tracker:` and this must
# key on the same thing, or the statusline and the board answer "what is open"
# from two different stores in the same repo — the exact disagreement the
# one-tracker rule exists to end.
#
#   tracker: beans  -> beans takes the work-items slot
#   tracker: vault  -> the adjudant deck does, and beans stays hidden
#   no breadcrumb   -> nothing else claims the slot, so beans shows standalone
#   linked, no key  -> the deck wins; run /adjudant connect to hand it to beans
beans_tracker=""
if [ -f "${crumb_dir}/.claude/adjudant" ]; then
  beans_tracker=$(awk '/^tracker:/ {sub(/^tracker:[[:space:]]*/,""); sub(/[[:space:]]+$/,""); print; exit}' \
                    "${crumb_dir}/.claude/adjudant" 2>/dev/null)
  [ -z "$beans_tracker" ] && beans_tracker="vault"
fi
# ── Beans counts, read BEFORE the work-items slot ────────────────────────────
# beans (github.com/hmans/beans) stores issues as markdown beside the code, so
# it is file-is-truth in exactly the way the agent-bus is, and it reads the same
# way: walk up for the marker, then one pass over the files.
#
# It does NOT shell out to the `beans` binary. The load-bearing reason is that
# `beans list` EXITS 0 when there is no project, printing its error to stdout —
# so an exit-code check would render a segment made of an error message.
# Reading frontmatter cannot be fooled that way.
#
# Cost is the lesser reason, and measured rather than assumed: the spawn is
# ~10ms against a ~70ms repaint, so it would have been affordable. Reading the
# files is free — baseline and instrumented repaints both best-of-5 at 70ms.
#
# The marker is `.beans.yml`, not the `.beans/` directory: the config names the
# data path and a project may move it. Default is `.beans`.
#
# Statuses, from `beans update --help`: in-progress, todo, draft, completed,
# scrapped. OPEN MEANS DRAFT, TODO OR IN-PROGRESS. Draft used to be excluded
# here on the reading that it is "not yet work", but beans itself files draft
# with todo (internal/ui/styles.go:175), and the disagreement was not academic:
# a not-yet-started child scored as a finished one, so an untouched epic read
# as complete. One definition of open, and it is the tracker's.
# Priorities: critical, high, normal, low, deferred.
#
# Renders this and nothing more:
#   ◍ 7    seven open beans
#   ▸2     two of them in progress
#   ⬡1     gold: one milestone is three quarters done
#   ⬡2     orchid: two epics are three quarters done
#   ✕3     three open beans are bugs
#   !1     one open bean is critical
# Silent when the repo has no beans project, which is almost every repo.
BEANS_TIMEOUT="0.2s"
beans_cfg=""
_d="$proj_dir"
while [ -n "$_d" ] && [ "$_d" != "/" ]; do
  if [ -f "${_d}/.beans.yml" ]; then beans_cfg="${_d}/.beans.yml"; break; fi
  _d=$(dirname "$_d")
done

if [ -n "$beans_cfg" ] && [ "${beans_tracker:-beans}" = "beans" ]; then
  # `path:` out of the yml without a yaml parser: the one indented key needed.
  beans_rel=$(awk -F': *' '/^[[:space:]]+path:/ {gsub(/["'"'"']/,"",$2); print $2; exit}' \
                "$beans_cfg" 2>/dev/null)
  [ -z "$beans_rel" ] && beans_rel=".beans"
  beans_dir="$(dirname "$beans_cfg")/${beans_rel}"

  if [ -d "$beans_dir" ]; then
    # One awk over the frontmatter, bounded by the same timeout the rest of the
    # script uses: these files can live in iCloud, and a repaint must render
    # stale-but-instant rather than block on a dataless read. Stock macOS ships
    # no timeout binary, so the unbounded call is the fallback, matching TCAT.
    read -r -d '' BEANS_AWK <<'BEANSAWK'
# One pass. Six numbers, a list, and a seventh number:
#   open, in-progress, critical, bugs, closeable-epics, closeable-milestones,
#   then the ids of in-progress FEATURE beans joined by commas (`-` when none),
#   then the total number of beans, open or not. The total is what tells an
#   added bean from a closed one in the flash below: closing keeps the total
#   and lowers open, adding raises the total.
# The list feeds S1b: under adjudant's branch rule each of those ids owns a
# feature/<id> branch, and one that does not is drift worth a glyph.
#
# A bean's id comes from its FILENAME (`<id>--<slug>.md`), because the id inside
# the file is a `# comment` line and parsing that is a second grammar for no gain.
# `parent:` names an epic by that same id, which is what lets epic progress be
# computed here rather than by shelling out.
#
# `seen` guards the FIRST frontmatter block only. 71 of 85 real bean bodies carry
# `---` rules, which toggle the fence flag a second time; without this a body
# line reading `status: todo` would be counted as another bean.
function idof(path,   n, a, b) { n = split(path, a, "/"); b = a[n]
                                 sub(/--.*$/, "", b); sub(/\.md$/, "", b); return b }
FNR==1 { infm=0; seen=0; st=""; pr=""; ty=""; par=""; id=idof(FILENAME) }
/^---[[:space:]]*$/ {
  if (infm && !seen) {
    seen = 1
    all++
    stat[id] = st
    type[id] = ty
    # Draft is OPEN, matching beans (internal/ui/styles.go:175). This one flag
    # feeds both the pip and the child ratio below, so getting it wrong scored
    # unstarted children as done.
    isopen = (st=="todo" || st=="in-progress" || st=="draft")
    if (isopen) {
      open++
      if (st=="in-progress") doing++
      if (st=="in-progress" && ty=="feature") feat = (feat=="" ? id : feat "," id)
      if (pr=="critical")    crit++
      if (ty=="bug")         bug++
      if (ty=="milestone")   mile_all++
      if (ty=="epic")        epic_all++
    }
    if (par != "") { tot[par]++; if (!isopen) don[par]++ }
  }
  infm = !infm; next
}
infm && !seen && /^status:/   { st=$2 }
infm && !seen && /^priority:/ { pr=$2 }
infm && !seen && /^type:/     { ty=$2 }
infm && !seen && /^parent:/   { par=$2 }
END {
  # A parent counts when three quarters of its children are done AND the parent
  # itself is still open. A closed epic has nothing left to do, and a count of
  # epics that merely exist is inventory, which does not earn a glyph.
  #
  # TYPE IS THE GATE. tot[] is keyed by whatever any bean names in `parent:`,
  # so without this test a feature or a task earned the epic glyph the moment
  # it had children. Only the two planning tiers qualify: a task with subtasks
  # is not a milestone, and nobody reads the statusline to learn that one.
  for (p in tot) {
    if (tot[p] == 0) continue
    if (!(stat[p]=="todo" || stat[p]=="in-progress" || stat[p]=="draft")) continue
    if (don[p]/tot[p] < 0.75) continue
    if      (type[p] == "milestone") mile++
    else if (type[p] == "epic")      epic++
  }
  printf "%d %d %d %d %d %d %s %d %d %d", open, doing, crit, bug, epic, mile, (feat=="" ? "-" : feat), all, mile_all, epic_all
}
BEANSAWK
    if   command -v timeout  >/dev/null 2>&1; then
      beans_counts=$(timeout  "$BEANS_TIMEOUT" awk "$BEANS_AWK" "$beans_dir"/*.md 2>/dev/null)
    elif command -v gtimeout >/dev/null 2>&1; then
      beans_counts=$(gtimeout "$BEANS_TIMEOUT" awk "$BEANS_AWK" "$beans_dir"/*.md 2>/dev/null)
    else
      beans_counts=$(awk "$BEANS_AWK" "$beans_dir"/*.md 2>/dev/null)
    fi

    set -- $beans_counts
    b_beans_open="${1:-0}"; b_beans_doing="${2:-0}"; b_beans_crit="${3:-0}"
    b_beans_bug="${4:-0}";  b_beans_epic="${5:-0}"; b_beans_mile="${6:-0}"
    b_beans_feat="${7:-}"; [ "$b_beans_feat" = "-" ] && b_beans_feat=""
    b_beans_all="${8:-0}"; b_beans_mile_all="${9:-0}"; b_beans_epic_all="${10:-0}"

    # -- The flash. A bean was just added, removed, closed or reopened: say
    #    so for a few seconds next to the count, then go back to the plain
    #    readout. No hook and no writer cooperation: the bar already reads
    #    every bean on every repaint, so it remembers the last three counts
    #    per beans dir and compares. One `read` builtin per repaint; one
    #    redirect when something changed (the grind clock's pattern, for
    #    the same reason: this runs every repaint and must not fork).
    #
    #    First sight of a beans dir records and stays silent, so a fresh
    #    machine does not flash "+89". Only the counts are compared, not the
    #    files, so a rename or an edit that leaves counts alone is not news.
    #    A change while a flash is live replaces it and restarts the clock.
    # Seconds the delta stays up. The env knob exists for the tests, which
    # cannot wait eight real seconds for an expiry and should not fake NOW.
    BEANS_FLASH_TTL="${ADJUDANT_BEANS_FLASH_TTL:-8}"
    case "$BEANS_FLASH_TTL" in (*[!0-9]*|"") BEANS_FLASH_TTL=8;; esac
    beans_flash=""
    _bf_file="${CACHE_DIR}/beans-$(_ckey "$beans_dir")"
    _bf_open=""; _bf_doing=""; _bf_all=""; _bf_crit=""; _bf_bug=""; _bf_mile=""; _bf_epic=""; _bf_ts=0; _bf_text=""
    [ -f "$_bf_file" ] && read -r _bf_open _bf_doing _bf_all _bf_crit _bf_bug _bf_mile _bf_epic _bf_ts _bf_text < "$_bf_file" 2>/dev/null
    case "$_bf_ts" in (*[!0-9]*|"") _bf_ts=0;; esac
    case "$_bf_crit" in (*[!0-9]*|"") _bf_crit=0;; esac
    case "$_bf_bug" in (*[!0-9]*|"") _bf_bug=0;; esac
    case "$_bf_mile" in (*[!0-9]*|"") _bf_mile=0;; esac
    case "$_bf_epic" in (*[!0-9]*|"") _bf_epic=0;; esac
    _bf_new=""
    if [ -n "$_bf_all" ]; then
      _bf_has_crit=""
      [ "$b_beans_crit"     -gt "$_bf_crit" ] 2>/dev/null && { _bf_new+="!$(( b_beans_crit - _bf_crit )) "; _bf_has_crit=1; }
      [ -z "$_bf_has_crit" ] && [ "$b_beans_bug" -gt "$_bf_bug" ] 2>/dev/null && _bf_new+="✕$(( b_beans_bug - _bf_bug )) "
      [ "$b_beans_mile_all" -gt "$_bf_mile" ] 2>/dev/null && _bf_new+="▣$(( b_beans_mile_all - _bf_mile )) "
      [ "$b_beans_epic_all" -gt "$_bf_epic" ] 2>/dev/null && _bf_new+="⬡$(( b_beans_epic_all - _bf_epic )) "
      if [ -z "$_bf_new" ]; then
        [ "$b_beans_all"  -gt "$_bf_all"  ] 2>/dev/null && _bf_new="+$(( b_beans_all - _bf_all )) "
        [ "$b_beans_all"  -lt "$_bf_all"  ] 2>/dev/null && _bf_new="−$(( _bf_all - b_beans_all )) "
      fi
      if [ -z "$_bf_new" ]; then
        [ "$b_beans_open" -lt "$_bf_open" ] 2>/dev/null && _bf_new="✓$(( _bf_open - b_beans_open )) "
        [ "$b_beans_open" -gt "$_bf_open" ] 2>/dev/null && _bf_new="↺$(( b_beans_open - _bf_open )) "
      fi
      _bf_new="${_bf_new% }"
    fi
    if [ -n "$_bf_new" ]; then
      _bf_ts=$NOW; _bf_text="$_bf_new"
    fi
    if [ -n "$_bf_new" ] || [ "$b_beans_open $b_beans_doing $b_beans_all $b_beans_crit $b_beans_bug $b_beans_mile_all $b_beans_epic_all" != "$_bf_open $_bf_doing $_bf_all $_bf_crit $_bf_bug $_bf_mile $_bf_epic" ]; then
      printf '%s %s %s %s %s %s %s %s %s\n' "$b_beans_open" "$b_beans_doing" "$b_beans_all" "$b_beans_crit" "$b_beans_bug" "$b_beans_mile_all" "$b_beans_epic_all" "$_bf_ts" "$_bf_text" > "$_bf_file" 2>/dev/null
    fi
    if [ -n "$_bf_text" ] && [ $(( NOW - _bf_ts )) -lt "$BEANS_FLASH_TTL" ]; then
      beans_flash=""; _bf_ct=0
      for _tok in $_bf_text; do
        [ "$_bf_ct" -ge 3 ] && break; _bf_ct=$(( _bf_ct + 1 ))
        _fb="\033[48;2;40;40;45m"; _ff="${MODEL}"
        case "$_tok" in
          !*)  _fb="\033[48;2;70;20;20m"; _ff="${CTX_HOT}" ;;
          ✕*)  _fb="\033[48;2;45;45;50m"; _ff="${BUG}" ;;
          ▣*)  _fb="\033[48;2;55;55;50m"; _ff="${MILE}" ;;
          ⬡*)  _fb="\033[48;2;48;30;52m"; _ff="${EPIC}" ;;
          +*)  _fb="\033[48;2;30;55;28m"; _ff="${DIFF_ADD}" ;;
          −*)  _fb="\033[48;2;60;28;25m"; _ff="${DIFF_DEL}" ;;
          ✓*)  _fb="\033[48;2;28;50;38m"; _ff="${VAULTOP}" ;;
          ↺*)  _fb="\033[48;2;55;42;25m"; _ff="${BEANS}" ;;
        esac
        beans_flash+=" ${_fb}${_ff} ${_tok} ${R}"
      done
    fi

    # Bean velocity: 7-day closed-per-day from .beans/archive/ mtimes.
    # Cached daily. Produces per-day counts and a total; the crunch flash
    # renders them as a sparkline (▁▃▅▇▃▁▂) and a total. Not displayed
    # persistently: periodic data that changes once a day is crunch-flash
    # material, not a permanent fixture.
    _vel_cache="${CACHE_DIR}/velocity-$(_ckey "$beans_dir")"
    _vel_ts=0; _vel_spark=""; _vel_total=0
    [ -f "$_vel_cache" ] && read -r _vel_ts _vel_spark _vel_total < "$_vel_cache" 2>/dev/null
    case "$_vel_ts" in (*[!0-9]*|"") _vel_ts=0;; esac
    case "$_vel_total" in (*[!0-9]*|"") _vel_total=0;; esac
    if [ $(( NOW - _vel_ts )) -ge 86400 ]; then
      _archive="$(dirname "$beans_cfg")/.beans/archive"
      if [ -d "$_archive" ]; then
        # Compute 7 day-counts with awk, return them space-separated.
        # Bash then maps to bar chars (avoids awk substr on multibyte).
        _vel_days=$(stat -f '%m' "$_archive"/*.md 2>/dev/null | awk -v now="$NOW" '
          BEGIN { for(i=0;i<7;i++) c[i]=0; t=0 }
          { d = int((now - $1) / 86400); if(d>=0 && d<7) { c[d]++; t++ } }
          END { for(i=6;i>=0;i--) printf "%d ", c[i]; print t }')
        if [ -n "$_vel_days" ]; then
          set -- $_vel_days
          _vel_total="${8:-0}"
          _bars=(" " "▁" "▂" "▃" "▄" "▅" "▆" "▇" "█")
          _vmax=0; for _vi in "$1" "$2" "$3" "$4" "$5" "$6" "$7"; do
            [ "$_vi" -gt "$_vmax" ] 2>/dev/null && _vmax="$_vi"
          done
          _vel_spark=""
          if [ "$_vmax" -gt 0 ]; then
            for _vi in "$1" "$2" "$3" "$4" "$5" "$6" "$7"; do
              _idx=$(( _vi * 8 / _vmax ))
              [ "$_idx" -lt 1 ] && [ "$_vi" -gt 0 ] && _idx=1
              [ "$_idx" -gt 8 ] && _idx=8
              _vel_spark+="${_bars[$_idx]}"
            done
          fi
          set --
        fi
      fi
      printf '%s %s %s\n' "$NOW" "${_vel_spark:--}" "${_vel_total:-0}" > "$_vel_cache" 2>/dev/null
    fi
    [ "$_vel_spark" = "-" ] && _vel_spark=""

    # Rendered in the WORK-ITEMS slot (Signal 5), not as a segment of its own.
    # One repo has one tracker, so one slot shows it: beans where the repo has
    # beans, the adjudant deck otherwise. Two counts of "what is open" side by
    # side is the two-stores-disagree confusion in miniature.
    if [ "${b_beans_open:-0}" -gt 0 ] 2>/dev/null; then
      # SPLIT, because only the pip is a link. `◍ N` is the thing you click to
      # open the board; `▸N` and `!N` are readings of it, and making them
      # clickable would put three hover targets on one figure and imply they
      # lead somewhere different. Signal 5 wraps beans_pip alone.
      _bc="${MODEL}"
      _bb=""
      [ "${b_beans_open}" -ge 45 ] 2>/dev/null && _bc="${BEANS}"
      [ "${b_beans_open}" -ge 75 ] 2>/dev/null && _bb="\033[48;2;55;42;25m"
      [ "${b_beans_open}" -ge 125 ] 2>/dev/null && { _bc="${MODEL}"; _bb="\033[48;2;60;60;55m"; }
      beans_pip="${BEANS}${BEANS_ICON} ${_bb}${_bc}${b_beans_open}${R}"
      # When a flash is live, ONLY the pip and the flash. Everything else
      # (doing, milestone, epic, bug, critical) is suppressed so the flash
      # is the single thing you read. They return when the flash expires.
      beans_extra=""
      [ "${b_beans_doing:-0}" -gt 0 ] 2>/dev/null && beans_extra+=" ${CTX}▸${b_beans_doing}${R}"
      [ "${b_beans_mile:-0}"  -gt 0 ] 2>/dev/null && beans_extra+=" ${MILE}⬡${b_beans_mile}${R}"
      [ "${b_beans_epic:-0}"  -gt 0 ] 2>/dev/null && beans_extra+=" ${EPIC}⬡${b_beans_epic}${R}"
      [ "${b_beans_bug:-0}"   -gt 0 ] 2>/dev/null && beans_extra+=" ${BUG}✕${b_beans_bug}${R}"
      [ "${b_beans_crit:-0}"  -gt 0 ] 2>/dev/null && beans_extra+=" ${CTX_HOT}!${b_beans_crit}${R}"
      [ -n "$beans_flash" ] && beans_extra+="$beans_flash"
      beans_label="${beans_pip}${beans_extra}"
    elif [ -n "$beans_flash" ]; then
      # The last open bean just went: nothing to count, but the going is news.
      beans_pip="${BEANS}${BEANS_ICON} ${MODEL}0${R}"; beans_extra="$beans_flash"
      beans_label="${beans_pip}${beans_extra}"
    fi
  fi
fi

# Ops flash moved to S0a (full-line takeover, like the break blurt).

# ── S1b: branch-rule drift, one red ! in front of the git segment ────────────
# adjudant's branch rule (reference/repo-standards.md, "Git practice"): a
# feature bean works on feature/<bean-id>, always in a linked worktree, and
# the main checkout stays on main. The full report is `/adjudant status`;
# the bar shows one glyph so the drift is seen before the next commit.
#
# Gated on `tracker: beans` in the breadcrumb, so a repo that never adopted
# the rule is never nagged, and on S1 having found a branch at all. Three
# conditions, cheapest first; the first hit wins and nothing else runs:
#   1. This is the main checkout (no ⑂) and the branch is not main/master.
#      Pure string test on values S1 already has.
#   2. The branch is feature/<id> and <id>'s bean is completed or scrapped:
#      the worktree outlived its bean. One awk over one file.
#   3. An in-progress feature bean has no feature/<id> branch. One
#      `for-each-ref` (~5ms), and only when the awk pass listed any such bean.
# Recolours the glyph S1 already placed, because s1_col is assembled by the
# time the beans scan has run; a substitution on the glyph's own colour code
# is the one seam that does not couple this block to S1's layout.
git_drift=""
if [ "${beans_tracker:-}" = "beans" ] && [ -n "$branch" ] && [ -n "$beans_cfg" ]; then
  if [ -z "$wt" ]; then
    case "$branch" in main|master) ;; *) git_drift=1 ;; esac
  fi
  if [ -z "$git_drift" ] && [ -d "${beans_dir:-}" ]; then
    case "$branch" in
      feature/*)
        _bid="${branch#feature/}"
        for _bf in "$beans_dir"/"$_bid"--*.md; do
          [ -f "$_bf" ] || continue
          _bst=$(awk '/^---[[:space:]]*$/ { if (infm) exit; infm=1; next }
                      infm && /^status:/ { print $2; exit }' "$_bf" 2>/dev/null)
          case "$_bst" in completed|scrapped) git_drift=1 ;; esac
          break
        done
        ;;
    esac
  fi
  if [ -z "$git_drift" ] && [ -n "${b_beans_feat:-}" ]; then
    _refs=$(git -C "$cwd" --no-optional-locks for-each-ref --format='%(refname:short)' refs/heads/feature/ 2>/dev/null)
    _old_ifs="$IFS"; IFS=","
    for _bid in $b_beans_feat; do
      case "$_refs" in *"feature/${_bid}"*) ;; *) git_drift=1; break ;; esac
    done
    IFS="$_old_ifs"
  fi
fi
# The red is the message, and it goes on the checkout glyph: ⎇ or ⑂ painted
# in the diff-red instead of white or indigo. The glyph is already in s1_col
# with its colour in front of it, so this is one substitution, not a second
# mark. Drift needs a branch name (the gate above), and a named branch always
# has one of the two glyphs, so there is no third case.
if [ -n "$git_drift" ]; then
  s1_col="${s1_col/"${BUG}⎇"/"${DIFF_DEL}⎇"}"
  s1_col="${s1_col/"${WT}⑂"/"${DIFF_DEL}⑂"}"
fi


s2_col=""; s2_tail_col=""
# ── The vault-operation light ────────────────────────────────────────────────
# ⊙ used to mean "a yap agent is running". It now means ADJUDANT IS WRITING TO
# THE VAULT, and it sits in front of the bolt because that is the thing it is
# talking about.
#
# It cannot be literal. A hook's vault write finishes in milliseconds and this
# line paints once per turn, so "a write is happening right now" would never be
# caught. Adjudant touches a timestamped marker when it documents, and the light
# lingers for VAULTOP_LINGER seconds after — which reads as "adjudant just wrote"
# and is the honest version of what was asked for.
#
# No marker means no light. An adjudant older than 3.3.0 writes none, so this
# degrades to silence rather than to a wrong answer.
VAULTOP_LINGER=20
vaultop_col=""
for _m in "${TMPDIR:-/tmp}/adjudant-vault-write${sid:+-$sid}" "${TMPDIR:-/tmp}/adjudant-vault-write"; do
  [ -f "$_m" ] || continue
  _at=$(tr -dc '0-9' < "$_m" 2>/dev/null)
  if [ -n "$_at" ] && [ $(( NOW - _at )) -lt "$VAULTOP_LINGER" ]; then
    vaultop_col="${CTX}⊙${R} "
    break
  fi
done

breadcrumb="${crumb_dir}/.claude/adjudant"
if [ -f "$breadcrumb" ]; then
  slug=$(awk -F': ' '/^slug:/ {gsub(/[[:space:]]+$/, "", $2); print $2; exit}' "$breadcrumb")
  # vault_path may contain spaces; join all fields after the key
  vault_path=$(awk '/^vault_path:/ {sub(/^vault_path:[[:space:]]*/,""); sub(/[[:space:]]+$/,""); print; exit}' "$breadcrumb")
  # `.claude/adjudant` is gitignored and rides iCloud, so it carries whichever
  # machine wrote last: an absolute /Users/<someone>/… that is wrong everywhere
  # else. adjudant survives this (it expanduser()s the field, then falls back to
  # _candidate_vault_paths on vault_name); the statusline just went dark, which
  # is why every vault signal below stayed empty under the other machine's
  # breadcrumb. Two recoveries, cheapest first, both metadata-only:
  #   ~/…            expand it — the one value that is correct on both machines
  #   /Users/x/rest  if it does not resolve, retry the same tail under $HOME
  # iCloud and OneDrive roots both live under $HOME, so the tail swap holds.
  case "$vault_path" in
    "~/"*)
      vault_path="${HOME}/${vault_path#\~/}"
      ;;
    /Users/*)
      if [ ! -d "$vault_path" ]; then
        _vp_tail="${vault_path#/Users/*/}"
        [ -d "${HOME}/${_vp_tail}" ] && vault_path="${HOME}/${_vp_tail}"
      fi
      ;;
  esac
  stale_after=$(awk '/^stale_after_days:/ {sub(/^stale_after_days:[[:space:]]*/,""); sub(/[[:space:]]+$/,""); print; exit}' "$breadcrumb")
  case "$stale_after" in (*[!0-9]*|"") stale_after=30;; esac
  label="${slug:-vault}"
  [ "${#label}" -gt 10 ] && label="${label:0:9}…"

  # -- Verb state. Gerund = a preview is pending review; past tense = applied.
  #    Since adjudant v3 the clean and repo-tidy previews live under $TMPDIR
  #    rather than inside the tree they clean, so those two tests read the
  #    scratch root. port (previews in the repo) and shelf (previews in the
  #    vault) are unchanged; remise (0.27, reserved) is handled below.
  #    "tidied" went with the in-tree backup: it only ever said "a backup
  #    directory exists", which a rotating $TMPDIR backup makes true after
  #    every run, so it carries no signal. "ported" survives because port
  #    still backs up in place.
  #
  #    tidy and ramasse merged into clean, so the kind is "clean-preview".
  #    The old "tidy-preview" spelling is still accepted: renaming a scratch
  #    kind is the one change state-contract.md calls unsafe, and a preview
  #    left on disk by an older adjudant should still show as pending.
  #
  #    _scratch.scratch_dir keys on the basename of the directory being
  #    operated on, sanitised to [A-Za-z0-9_.-]: the VAULT project dir for
  #    clean — whose basename is the slug, in every zone — and the CODE repo
  #    root for repo-tidy. _adj_keyify mirrors that sanitiser exactly.
  _adj_key=""
  _adj_keyify() {
    local s="${1//[!A-Za-z0-9_.-]/-}"
    while [ "${s#-}" != "$s" ]; do s="${s#-}"; done
    while [ "${s%-}" != "$s" ]; do s="${s%-}"; done
    _adj_key="${s:-project}"
  }
  _adj_root="${TMPDIR:-/tmp}/adjudant"
  #    The scratch key gained an 8-hex digest of the project's resolved path:
  #    the name alone was not unique, so two vaults each holding a project
  #    called `demo` shared one preview and one backup root. Glob the suffix
  #    rather than recompute the hash — a shasum subshell on every render, for
  #    a directory that usually is not there, is not worth it.
  _adj_any_dir() { [ -d "${1}" ]; }
  _adj_cleaning=0
  if [ -n "$slug" ]; then
    _adj_keyify "$slug"
    for _d in "${_adj_root}/${_adj_key}-"*/clean-preview "${_adj_root}/${_adj_key}"/clean-preview; do
      _adj_any_dir "$_d" && { _adj_cleaning=1; break; }
    done
  fi
  _adj_repo_tidying=0
  _adj_keyify "$(basename "$proj_dir")"
  for _d in "${_adj_root}/${_adj_key}-"*/repo-tidy-preview "${_adj_root}/${_adj_key}"/repo-tidy-preview; do
    _adj_any_dir "$_d" && { _adj_repo_tidying=1; break; }
  done
  if   [ "$_adj_cleaning" = 1 ];                                     then state="cleaning";     scol="$VAULT_STALE"
  elif [ -d "${proj_dir}/.adjudant-port-preview" ];                  then state="porting";      scol="$VAULT_STALE"
  elif [ "$_adj_repo_tidying" = 1 ];                                 then state="repo-tidying"; scol="$VAULT_STALE"
  elif [ -n "$vault_path" ] && [ -d "${vault_path}/.adjudant-shelf-preview" ]; then state="shelving"; scol="$VAULT_STALE"
  elif [ -d "${proj_dir}/.adjudant-port-backup"  ];                  then state="ported";       scol="$VAULT_STALE"
  else                                                                    state="fresh";        scol="$VAULT_OK"
  fi

  today=$(date +%Y-%m-%d)

  # Helper: days delta from a YYYY-MM-DD string (returns empty on failure)
  _daysdelta() {
    local d="$1"
    local then
    then=$(date -j -f "%Y-%m-%d" "$d" +%s 2>/dev/null) || return
    echo $(( (NOW - then) / 86400 ))
  }

  # Zone-aware project resolution. Since adjudant v3 the lifecycle is four
  # NAMED folders under projects/; the pre-v3 shapes (bare, _fridge, _archive)
  # are probed after them so an untriaged vault still renders. Prefer the
  # candidate that actually holds a brief.md.
  proj_vault=""; zone=""
  if [ -n "$vault_path" ] && [ -n "$slug" ]; then
    for z in active paused finished archive "" _fridge _archive; do
      cand="${vault_path}/projects${z:+/${z}}/${slug}"
      if [ -f "${cand}/brief.md" ]; then proj_vault="$cand"; zone="$z"; break; fi
    done
    if [ -z "$proj_vault" ]; then
      for z in active paused finished archive "" _fridge _archive; do
        cand="${vault_path}/projects${z:+/${z}}/${slug}"
        if [ -d "$cand" ]; then proj_vault="$cand"; zone="$z"; break; fi
      done
    fi
    case "$zone" in
      ""|active) zone="" ;;     # the working folder is the default; no badge
      _fridge)   zone="paused" ;;
      _archive)  zone="archive" ;;
    esac
  fi

  # remise (v0.27+) previews land at the vault project root, not the repo root
  if [ "$state" = "fresh" ] && [ -n "$proj_vault" ] && [ -d "${proj_vault}/.adjudant-remise-preview" ]; then
    state="remising"; scol="$VAULT_STALE"
  fi

  # Signal 1: project status from brief.md frontmatter.
  # Templates carry an inline "# active | stale | ..." comment — strip it.
  proj_status=""
  if [ -n "$proj_vault" ]; then
    brief_raw=$(TCAT "${proj_vault}/brief.md")
    if [ -n "$brief_raw" ]; then
      proj_status=$(echo "$brief_raw" | awk '/^status:/ {sub(/^status:[[:space:]]*/,""); sub(/[[:space:]]*#.*$/,""); sub(/[[:space:]]+$/,""); print; exit}')
    fi
  fi

  # Signal 2 (today's session marker, ⚡ / ⚡?) was CUT in v2.
  # The session note is written at session start, so the plain marker was on
  # essentially always — and a signal that never varies carries no information.
  # Only the "intent placeholder still unfilled" case was ever news, and that
  # was dropped too rather than folded into the state light, which already
  # carries five meanings. The sessions/ directory is still read below, because
  # signal 3 needs its newest entry to detect lifecycle drift.

  # Signal 3: lifecycle drift, mirroring _vault_walk.suggest_status —
  # active + quiet ≥ stale_after_days → stale; stale + recent → active.
  status_hint=""
  sess_dir="${proj_vault}/sessions"
  if [ -n "$proj_vault" ] && [ -d "$sess_dir" ] && [ -n "$proj_status" ]; then
    last_sess=$(/bin/ls "$sess_dir" 2>/dev/null | LC_ALL=C grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.md$' | sort | tail -1)
    if [ -n "$last_sess" ]; then
      dq=$(_daysdelta "${last_sess%.md}")
      if [ -n "$dq" ]; then
        [ "$proj_status" = "active" ] && [ "$dq" -ge "$stale_after" ] && status_hint="→stale"
        [ "$proj_status" = "stale"  ] && [ "$dq" -lt "$stale_after" ] && status_hint="→active"
      fi
    fi
  fi

  # Signal 4: handoff freshness. Prefer the pre-rendered banner precompact/sync
  # writes into _handoff.md (hour-granular, already traffic-lit: 🟢<2h 🟡<8h 🔴≥8h,
  # "!" = STALE, session activity newer than the handoff). Fall back to the
  # frontmatter updated: date (day-granular) for handoffs without a banner.
  #
  # v2 renames the label from "h:9h" to "⤳9h" — same age, same colour tiers,
  # the glyph just replaces the prefix and matches the icon convention. Note the
  # emoji above are READ, never printed: they are what precompact wrote into the
  # file, and only their colour tier reaches the line.
  handoff_label=""
  handoff_col="$METRIC"
  handoff_stale=""
  if [ -n "$proj_vault" ]; then
    handoff_raw=$(TCAT "${proj_vault}/_handoff.md")
    if [ -n "$handoff_raw" ]; then
      banner=$(echo "$handoff_raw" | grep -m1 -E '(🔴|🟡|🟢).*handoff age')
      if [ -n "$banner" ]; then
        h_ba=$(echo "$banner" | sed -E 's/.*handoff age: ?\**([0-9]+[a-z]+).*/\1/')
        case "$h_ba" in (*[!0-9a-z]*|"") h_ba="?";; esac
        handoff_label="${h_ba}"
        case "$banner" in
          *🔴*) handoff_col="$CTX_HOT"  ;;
          *🟡*) handoff_col="$CTX_WARN" ;;
          *🟢*) handoff_col="$CTX"      ;;
        esac
        echo "$handoff_raw" | grep -qE '🔴 ?\*\*STALE\*\*' && { handoff_label+="!"; handoff_col="$CTX_HOT"; handoff_stale=1; }
      else
        h_updated=$(echo "$handoff_raw" | awk -F': ' '/^updated:/ {print $2; exit}' | tr -d '[:space:]')
        # Normalise: strip time component if ISO timestamp (keep YYYY-MM-DD prefix)
        h_date="${h_updated:0:10}"
        if [ -n "$h_date" ]; then
          h_age=$(_daysdelta "$h_date")
          if [ -n "$h_age" ]; then
            if   [ "$h_age" -lt 1   ]; then handoff_label="fresh"
            elif [ "$h_age" -lt 7   ]; then handoff_label="${h_age}d"
            else
              h_weeks=$(( h_age / 7 ))
              handoff_label="${h_weeks}w"
            fi
            # Color tier: 3-6d warn, 7d+ hot
            if   [ "$h_age" -ge 7 ]; then handoff_col="$CTX_HOT"
            elif [ "$h_age" -ge 3 ]; then handoff_col="$CTX_WARN"
            fi
          fi
        fi
      fi
    fi
  fi

  # OSC 8 link on the ⤳ label, so the age and the document it measures are one
  # Cmd+click apart. obsidian:// rather than file://: the handoff lives in the
  # vault, and the app scheme opens it in Obsidian directly instead of routing
  # through whatever owns .md system-wide. Addressed relative to the vault root
  # so shelved zones resolve. Wrapped once here rather than in each branch above.
  if [ -n "$handoff_label" ] && [ -n "$vault_path" ]; then
    ho_vault="${vault_path##*/}"
    ho_file="${proj_vault#"$vault_path"/}/_handoff.md"
    ho_url="obsidian://open?vault=${ho_vault// /%20}&file=${ho_file// /%20}"
    handoff_label="\033]8;;${ho_url}\033\\\\${handoff_label}\033]8;;\033\\\\"
  fi

  # Signal 5: WORK ITEMS — beans when the repo has them, else the adjudant
  # deck from board-data.json (5 KB, one awk pass).
  #
  # v2 changes what it reports and when. WHAT: v1 printed "▦ 12·3" — open and
  # in-flight, both pure positions. A backlog total is not news second to
  # second; which way it MOVED is. So the open count is now paired with a
  # week-over-week direction (↘ green when you are closing faster than you
  # open, ↗ amber when the backlog is winning), computed from a snapshot cached
  # per project. WHEN: it stays hidden entirely unless something is in flight or
  # the deck is lagging behind a task file — the "L2 lean" density rule. A board
  # sitting still says nothing, so it says nothing.
  board_label=""; board_col="$METRIC"; board_dir=""; board_dir_col="$METRIC"
  deck="${proj_vault}/board/board-data.json"
  if [ -n "$beans_cfg" ] && [ "${beans_tracker:-beans}" = "beans" ]; then
    # ONE REPO, ONE TRACKER, ONE SLOT. Where the repo has beans, beans owns the
    # work items and takes this position; the adjudant deck is not even read.
    # Showing both would put two answers to "what is open" side by side, which
    # is the two-stores-disagree confusion the tracker rule exists to end.
    # beans_label carries its own colour and resets, so board_col stays empty.
    board_label="$beans_label"; board_col=""; beans_slotted=1
    # Clickable, exactly like the deck variant. In a Beans-owned repo the very
    # same board.html renders the beans, so the target is unchanged — only the
    # source of its cards moved. Linked only when the page exists: a hyperlink
    # to a file that is not there is worse than plain text.
    board_html="${proj_vault}/board/board.html"
    if [ -f "$board_html" ]; then
      board_url="file://${board_html// /%20}"
      board_label="\033]8;;${board_url}\033\\\\${beans_pip}\033]8;;\033\\\\${beans_extra}"
    fi
  elif [ -n "$proj_vault" ] && [ -f "$deck" ]; then
    read -r b_total b_done b_ice b_doing <<<"$(awk -F'"' '/"column":/ {t++; c=$4; if(c=="done")d++; else if(c=="icebox")i++; else if(c=="doing")g++} END{printf "%d %d %d %d\n", t+0, d+0, i+0, g+0}' "$deck" 2>/dev/null)"
    if [ -n "$b_total" ] && [ "$b_total" -gt 0 ] 2>/dev/null; then
      b_open=$(( b_total - b_done - b_ice ))

      board_lag=""
      [ -n "$(find "${proj_vault}/tasks" -name '*.md' -newer "$deck" -print -quit 2>/dev/null)" ] && board_lag=1

      # Only render when there is something to act on.
      if [ "$b_doing" -gt 0 ] 2>/dev/null || [ -n "$board_lag" ]; then
        # Week-over-week direction. The snapshot is only rolled forward once the
        # window has elapsed, so the arrow is stable between rolls rather than
        # flickering — it is a weekly signal and behaves like one.
        board_cache="${CACHE_DIR}/board-$(_ckey "$proj_vault")"
        snap_open=$(_cache_get "$board_cache" snap_open)
        snap_ts=$(_cache_get "$board_cache" snap_ts)
        b_delta=$(_cache_get "$board_cache" delta)
        case "$snap_open" in (*[!0-9]*|"") snap_open="";; esac
        case "$snap_ts"   in (*[!0-9]*|"") snap_ts=0;;    esac
        case "$b_delta"   in (*[!0-9-]*|"") b_delta="";;  esac
        if [ -z "$snap_open" ] || [ $(( NOW - snap_ts )) -ge "$BOARD_WINDOW" ]; then
          if [ -n "$snap_open" ]; then b_delta=$(( b_open - snap_open )); else b_delta=""; fi
          _cache_put "$board_cache" snap_open "$b_open" snap_ts "$NOW" delta "${b_delta:-0}"
        fi

        board_label="${BOARD_ICON} ${b_open}"
        [ -n "$board_lag" ] && board_col="$CTX_WARN"

        # Make the board label a clickable OSC 8 hyperlink to the project's
        # board.html (Cmd+click in iTerm2). file:// URLs need spaces percent-
        # encoded; the rest of a vault path is URL-safe in practice. Terminals
        # without OSC 8 support ignore the sequence and render the bare label;
        # if Claude Code's renderer strips it, same harmless result.
        board_html="${proj_vault}/board/board.html"
        if [ -f "$board_html" ]; then
          board_url="file://${board_html// /%20}"
          board_label="\033]8;;${board_url}\033\\\\${board_label}\033]8;;\033\\\\"
        fi
        if [ -n "$b_delta" ] && [ "$b_delta" -ne 0 ] 2>/dev/null; then
          if [ "$b_delta" -gt 0 ]; then
            board_dir="↗${b_delta}"; board_dir_col="$CTX_WARN"
          else
            board_dir="↘${b_delta#-}"; board_dir_col="$CTX"
          fi
        fi
      fi
    fi
  fi

  # Signal 6: last dream — ☾ age since the newest dreams/YYYY-MM-DD[-dream].md.
  # Keyed on the report's DATE, not on a parsed drift count: `drift_items` is
  # optional in adjudant's own contract (check.py sets it only when a
  # "N drift items" phrase matches) and real reports never carry that phrasing,
  # so a count-keyed indicator can never light. The date is always there.
  #
  # v2 shows it ONLY when overdue. Dream is periodic hygiene: a recent one is
  # reassurance, not information, and reassurance does not deserve permanent
  # columns. Past stale_after_days it appears in amber and is one of the
  # conditions that dims the state light.
  drift_label=""; drift_old=""
  if [ -n "$proj_vault" ] && [ -d "${proj_vault}/dreams" ]; then
    latest_dream=$(/bin/ls "${proj_vault}/dreams" 2>/dev/null | LC_ALL=C grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}(-dream)?\.md$' | sort | tail -1)
    if [ -n "$latest_dream" ]; then
      d_age=$(_daysdelta "${latest_dream:0:10}")
      if [ -n "$d_age" ] && [ "$d_age" -ge "$stale_after" ] 2>/dev/null; then
        drift_old=1
        if [ "$d_age" -lt 7 ]; then drift_label="☾ ${d_age}d"
        else                        drift_label="☾ $(( d_age / 7 ))w"
        fi
        # Append the count when the report happens to carry a parseable one.
        drift_n=$(grep -oiE '[0-9]+[[:space:]]+(distinct[[:space:]]+)?drift item' "${proj_vault}/dreams/${latest_dream}" 2>/dev/null | head -1 | grep -oE '^[0-9]+')
        [ -n "$drift_n" ] && [ "$drift_n" -gt 0 ] 2>/dev/null && drift_label+="·${drift_n}"
      fi
    fi
  fi

  # Signal 7: in-flight session tasks from the adjudant task ledger
  # (TaskCreated/TaskCompleted hooks; latest status per id, open = not completed).
  # v2 adds the denominator: "≡ 4/9" — four still open of nine created this
  # session — because a bare count says how much is left but nothing about how
  # the session is going.
  ledger_label=""
  ledger="${TMPDIR:-/tmp}/adjudant-task-ledger-${sid}.jsonl"
  if [ -n "$sid" ] && [ -f "$ledger" ]; then
    read -r t_open t_all <<<"$(jq -rs '
      [group_by(.id)[] | last] as $l
      | "\($l | map(select(.status != "completed")) | length) \($l | length)"
    ' "$ledger" 2>/dev/null)"
    case "$t_open" in (*[!0-9]*|"") t_open=0;; esac
    case "$t_all"  in (*[!0-9]*|"") t_all=0;;  esac
    [ "$t_open" -gt 0 ] 2>/dev/null && ledger_label="≡ ${t_open}/${t_all}"
  fi

  # Build S2.
  # The bolt IS the state light: bright when the vault is in an acceptable state,
  # dim grey when something wants attention (an unapplied preview, a leftover
  # backup, a lifecycle mismatch, a stale handoff, a lagging board, an overdue
  # dream). "fresh" is therefore never spelled out — a bright bolt already says
  # it. Only the states that need a name (cleaning, repo-tidying, remising…) print one.
  #
  # v2 swapped the glyph from "~" to "↯" and kept the behaviour byte for byte:
  # same two tones, same five conditions. A tilde reads as a path; a bolt reads
  # as an indicator lamp, which is what it always was.
  attention=""
  [ "$state" != "fresh" ] && attention=1
  [ -n "$status_hint" ] && attention=1
  # Explicit flag, NOT a glob on the label: the label is wrapped in an OSC 8
  # hyperlink above, so its last bytes are the link terminator, not "!" — a
  # (*!) pattern here would silently never match again.
  [ -n "$handoff_stale" ] && attention=1
  [ -n "$board_lag" ] && attention=1
  [ -n "$drift_old" ] && attention=1

  if [ -n "$attention" ]; then bolt_col="$BOLT_STALE"; else bolt_col="$BOLT_FRESH"; fi

  # The slug links into Obsidian itself via its URL scheme — unlike the two
  # file:// links above, obsidian:// targets the app directly, bypassing the
  # system's .md handler. Vault name is the vault folder's basename; the file
  # is the project's brief, addressed relative to the vault root so shelved
  # zones (_fridge/_archive) resolve too. Spaces percent-encoded, as ever.
  label_lnk="$label"
  if [ -n "$proj_vault" ] && [ -n "$vault_path" ]; then
    obs_vault="${vault_path##*/}"
    obs_file="${proj_vault#"$vault_path"/}/brief.md"
    obs_url="obsidian://open?vault=${obs_vault// /%20}&file=${obs_file// /%20}"
    label_lnk="\033]8;;${obs_url}\033\\\\${label}\033]8;;\033\\\\"
  fi

  # -- core: identity + anything that demands action
  s2_col="${vaultop_col}\033[38;2;100;140;185m${BOLT}${R} \033[38;2;100;140;185m${label_lnk}${R}"
  [ -n "$zone" ] && s2_col+="${VAULT_STALE} (${zone})${R}"
  # Status is only worth pixels when it isn't the obvious one: we're working in
  # the project, so "active" is a given. Shelved/stale/done and any suggested
  # transition still print.
  if [ -n "$status_hint" ]; then
    s2_col+="${VAULT_NO} [${proj_status}${R}${CTX_WARN}${status_hint}${R}${VAULT_NO}]${R}"
  elif [ -n "$proj_status" ] && [ "$proj_status" != "active" ]; then
    s2_col+="${VAULT_NO} [${proj_status}]${R}"
  fi
  [ "$state" != "fresh" ] && s2_col+="${scol} · ${state}${R}"
  if [ -n "$handoff_label" ]; then
    _h_age_str="${handoff_label}"
    _h_col="\033[2m\033[38;2;75;100;140m"
    [ -n "$handoff_stale" ] && _h_col="\033[1m\033[38;2;235;235;235m"
    case "$handoff_col" in "$CTX_WARN") _h_col="\033[38;2;100;140;185m" ;; esac
    s2_col+=" ${_h_col}(${_h_age_str})${R}"
  fi

  # -- tail: ambient telemetry, nothing here is a call to act
  if [ -n "$board_label" ]; then
    s2_tail_col+="${SP}${board_col}${board_label}${R}"
    [ -n "$board_dir" ] && s2_tail_col+=" ${board_dir_col}${board_dir}${R}"
  fi
  [ -n "$drift_label"  ] && s2_tail_col+="${SP}${CTX_WARN}${drift_label}${R}"
  [ -n "$ledger_label" ] && s2_tail_col+="${SP}${METRIC}${ledger_label}${R}"
fi

# ── S4: model  effort  ctx ────────────────────────────────────────────────────
# Model display name -> family initial + version: "Claude Opus 4.8" -> "O-4.8".
# A generic rule rather than one case arm per release. The old per-model table
# had no 4.8 arm, so the current model only rendered by falling through to the
# raw passthrough; this shortens every future release automatically. Handles
# both orderings ("Opus 4.5" and "3 Opus") and drops any trailing parenthetical
# such as "(1M context)".
_m="${model_raw#Claude }"; _m="${_m%% (*}"
case "$_m" in
  "Opus "*)   model="O${_m#Opus }"   ;;
  "Sonnet "*) model="S${_m#Sonnet }" ;;
  "Haiku "*)  model="H${_m#Haiku }"  ;;
  "Fable "*)  model="F${_m#Fable }"  ;;
  "Mythos "*) model="M${_m#Mythos }" ;;
  *" Opus")   model="O${_m% Opus}"   ;;
  *" Sonnet") model="S${_m% Sonnet}" ;;
  *" Haiku")  model="H${_m% Haiku}"  ;;
  *" Fable")  model="F${_m% Fable}"  ;;
  *" Mythos") model="M${_m% Mythos}" ;;
  *)          model="$_m"            ;;
esac
# Effort as a short code: lo md hi xhi max. A dot count reads badly.
# Ordinary levels take the EFFORT colour.
# xhi and max take the ULTRA purple plus a filled pip. You chose those.
e=""; e_col=""; e_pip=""
case "$effort_raw" in
  low)    e="lo";  e_col="$EFFORT";;
  medium) e="md";  e_col="$EFFORT";;
  high)   e="hi";  e_col="$EFFORT";;
  xhigh)  e="xhi"; e_col="$ULTRA"; e_pip=1;;
  max)    e="max"; e_col="$ULTRA"; e_pip=1;;
esac
# ultracode: the harness reports it as a plain xhigh. A marker carries it.
# Writer: hooks/scripts/user-prompt-reminder.sh, on the word `ultracode`.
# `ultracode off` removes it. Path: $TMPDIR/claude-ultracode-<session_id>.
# Keyed to the session id. Under $TMPDIR so tests can sandbox it.
ultra=""; [ -n "$sid" ] && [ -f "${TMPDIR:-/tmp}/claude-ultracode-${sid}" ] && ultra=1

# The bar reports REMAINING, not used. Two reasons: what you act on is headroom,
# and the number you act on should be the one that shrinks toward zero.
#
# Cell count rounds rather than truncates. v1 used `u * 8 / 100`, which meant 96%
# rendered seven of eight cells and the bar could only look full at exactly 100% —
# a level compaction prevents from ever arriving. Under depletion that bug moves
# to the START of every session (98% remaining would draw 7/8 on a window you
# have barely touched), so the +50 is required here, not merely tidy.
#
# The triangle is an advisory, not an alarm: hollow △ means this is a good moment
# to compact while you still have room to choose; filled ▲ means it is nearly
# gone. Fill therefore carries meaning, which is why v1's wall-clock ▲/△ pulse is
# deleted rather than retimed — it would collide with the semantics.
ctx_col=""
if [ -n "$used_pct" ]; then
  u=$(printf "%.0f" "$used_pct")
  [ "$u" -lt 0 ] 2>/dev/null && u=0
  [ "$u" -gt 100 ] 2>/dev/null && u=100
  r=$(( 100 - u ))
  f=$(( (r * 8 + 50) / 100 )); [ "$f" -gt 8 ] && f=8
  em=$(( 8 - f ))
  bar=""
  for ((i=0;i<f;i++)); do bar="${bar}▓"; done
  for ((i=0;i<em;i++)); do bar="${bar}░"; done
  ctx_glyph=""
  if   [ "$r" -le 5  ]; then ctx_glyph=" ▲"
  elif [ "$r" -le 20 ]; then ctx_glyph=" △"
  fi
  if [ -n "$ultra" ]; then
    c="$ULTRA"
  elif [ "$r" -le 10 ]; then c="\033[38;2;200;70;60m"
  elif [ "$r" -le 20 ]; then c="$CTX_HOT"
  elif [ "$r" -le 30 ]; then c="\033[38;2;200;130;75m"
  elif [ "$r" -le 40 ]; then c="$CTX_WARN"
  elif [ "$r" -le 50 ]; then c="\033[38;2;175;185;85m"
  elif [ "$r" -le 65 ]; then c="\033[38;2;130;180;105m"
  elif [ "$r" -le 80 ]; then c="$CTX"
  else c="\033[38;2;120;180;105m"
  fi
  ctx_col="${c}${bar} ${r}%${ctx_glyph}${R}"
fi
# Five-hour rate limit, as one micro-bar column right after the context bar —
# the two budget readouts sitting together. Gated at 50%: below that there is
# ample headroom and a glyph that never changes is a glyph you stop seeing (the
# same reasoning that cut the session marker). Over 100% breaks out of the ramp
# to ⊘ rather than drawing a taller block, because being blocked is a different
# state from being nearly out, not a further step along the same one.
# The seven-day limit is available in the same payload but deliberately unused.
rl_col=""
case "$rl5_raw" in (*[!0-9.]*|"") rl5_raw="";; esac
if [ -n "$rl5_raw" ]; then
  rl5=$(printf "%.0f" "$rl5_raw" 2>/dev/null) || rl5=""
fi
if [ -n "$rl5" ] && [ "$rl5" -ge 25 ] 2>/dev/null; then
  if [ "$rl5" -gt 100 ]; then
    rl_col="${CTX_HOT}⊘${R}"
  elif [ "$rl5" -ge 90 ]; then
    rl_col="${CTX_HOT}●${R}"
  elif [ "$rl5" -ge 75 ]; then
    rl_col="${CTX_WARN}◕${R}"
  elif [ "$rl5" -ge 50 ]; then
    rl_col="${CTX}◑${R}"
  else
    rl_col="${METRIC}◔${R}"
  fi
fi

s4_col="${MODEL}${model}${R}"
case "$ctx_size" in
  [1-9][0-9][0-9][0-9][0-9][0-9][0-9]*) s4_col+="${HASH}/1M${R}" ;;
esac
[ -n "$e"       ] && s4_col+="${SP}${e_col}${e}${R}"
[ -n "$e_pip"   ] && s4_col+=" ${ULTRA}●${R}"
[ -n "$ultra"   ] && s4_col+=" ${ULTRA}●${R}"
[ -n "$ctx_col" ] && s4_col+="${SP}${ctx_col}"
[ -n "$rl_col"  ] && s4_col+=" ${rl_col}"

# ── S4b: session cost + 24h cost ──────────────────────────────────────────────
# Session cost comes from the JSON input (the `cost` field Claude Code provides).
# 24h cost is the API-equivalent aggregate from statusline-tokens-24h.sh.
# Session cost shows as `$0.43`, 24h as `~$1.20`. Both in METRIC dim.
TOK_CACHE="/tmp/claude-tokens-24h.json"
TOK_TTL=120
s4b_col=""
# Session cost from the statusline JSON. Format it the same way as the 24h cost.
case "$sess_cost_raw" in (*[!0-9.]*|"") sess_cost_raw="";; esac
if [ -n "$sess_cost_raw" ]; then
  sess_fmt=$(awk -v c="$sess_cost_raw" 'BEGIN{ if(c+0<=0) exit 1; if(c>=100)printf "%.0f",c; else if(c>=10)printf "%.1f",c; else printf "%.2f",c }') \
    && s4b_col+="${METRIC}\$${sess_fmt}${R}"
fi
tok_ts=""; tok_cost=""
if [ -f "$TOK_CACHE" ]; then
  IFS=$'\037' read -r tok_ts tok_cost <<<"$(
    jq -r '[
      ((.ts // 0) | tostring),
      ((.cost_usd // "") | tostring)
    ] | join("")' "$TOK_CACHE" 2>/dev/null)"
fi
case "$tok_ts" in (*[!0-9]*|"") tok_ts=0;; esac
# The refresher ships next to this file; since v2.6 it is found relative to
# this script, never by an absolute ~/.claude path.
# ADJUDANT_STATUSLINE_NO_SPAWN=1 keeps the refresher from starting: the
# test suite renders the bar under a throwaway HOME and must not overwrite
# the real machine's cost cache with a scan of an empty projects dir.
_tok_refresher="$(dirname "${BASH_SOURCE[0]}")/statusline-tokens-24h.sh"
if [ $(( NOW - tok_ts )) -ge "$TOK_TTL" ] && [ -x "$_tok_refresher" ] \
   && [ -z "${ADJUDANT_STATUSLINE_NO_SPAWN:-}" ]; then
  ( nohup "$_tok_refresher" >/dev/null 2>&1 & ) 2>/dev/null
fi
if [ -n "$tok_cost" ]; then
  cost_fmt=$(awk -v c="$tok_cost" 'BEGIN{ if(c+0<=0) exit 1; if(c>=100)printf "%.0f",c; else if(c>=10)printf "%.1f",c; else printf "%.2f",c }') \
    && { [ -n "$s4b_col" ] && s4b_col+=" "; s4b_col+="${METRIC}~\$${cost_fmt}${R}"; }
fi

# ── S5: agent-bus (the suitcase agent system) ────────────────────────────────
# v2.5 settled the open question: the yap half is GONE. It fired often, the
# agent bus has not been used in a long time, and the ⊙ glyph it owned was
# better spent on vault operations, where it now lives in front of the bolt.
# What remains is the bus itself.
#
# The bus is file-is-truth, which makes it perfectly statusline-shaped:
#   log/requests.log   one line per dispatch:  <ts>  <request-id>  -> <who> (<lane>)
#   out/<id>.md        the worker's answer; absent while the lane is in flight
#   first line of out/ BLOCKED: / QUESTION:  → the protocol says these are
#                      SURFACED to the orchestrator, never auto-approved
# So: no out file = still running; a BLOCKED/QUESTION first line = it is waiting
# on YOU. Those two get loud colors because ignoring them stalls the whole run.
#
# Only today's and yesterday's UTC request ids are considered — request ids are
# timestamp-prefixed, so this is a string compare, and it stops a July BLOCKED
# from haunting the statusline forever.
s5_col=""
bus_root=""
_d="$proj_dir"
while [ -n "$_d" ] && [ "$_d" != "/" ]; do
  if [ -d "${_d}/.agent-bus" ]; then bus_root="${_d}/.agent-bus"; break; fi
  _d=$(dirname "$_d")
done
if [ -n "$bus_root" ] && [ -f "${bus_root}/log/requests.log" ]; then
  _today=$(date -u +%Y%m%d)
  _yest=$(date -u -v-1d +%Y%m%d 2>/dev/null || echo "$_today")
  bus_run=0; bus_blocked=0; bus_question=0
  while read -r rid; do
    [ -z "$rid" ] && continue
    case "$rid" in "${_today}"*|"${_yest}"*) ;; *) continue;; esac
    f="${bus_root}/out/${rid}.md"
    if [ ! -f "$f" ]; then
      bus_run=$(( bus_run + 1 ))
    else
      case "$(head -1 "$f" 2>/dev/null)" in
        [Bb][Ll][Oo][Cc][Kk][Ee][Dd]:*)     bus_blocked=$(( bus_blocked + 1 )) ;;
        [Qq][Uu][Ee][Ss][Tt][Ii][Oo][Nn]:*) bus_question=$(( bus_question + 1 )) ;;
      esac
    fi
  done <<< "$(tail -30 "${bus_root}/log/requests.log" 2>/dev/null | awk '{print $2}')"

  if [ "$bus_run" -gt 0 ]; then
    s5_col="${BUS}⇄ ${bus_run}${R}"
  elif [ "$bus_blocked" -gt 0 ] || [ "$bus_question" -gt 0 ]; then
    s5_col="${METRIC}⇄${R}"
  fi
  [ "$bus_blocked"  -gt 0 ] && s5_col+=" ${CTX_HOT}!${bus_blocked}${R}"
  [ "$bus_question" -gt 0 ] && s5_col+=" ${CTX_WARN}?${bus_question}${R}"
fi

# ── Render: natural widths, uniform 2-space padding on every pipe ────────────
# Each SEP carries its own "  │  " — no extra section padding, so pipes
# always have exactly 2 spaces on each side.
#
# ORDER IS THE TRUNCATION POLICY. Claude Code hard-cuts this line at the right
# edge (Ink wrap:"truncate") and we cannot measure the terminal — so whatever
# sits rightmost is what gets sacrificed on a narrow window. Least actionable
# goes last:
#   S0b account badge      — leads the line; the one thing always visible
#   S1 git · S2 vault core   — identity and state, never sacrificed
#   S4 model + ctx%          — the headroom bar must always be visible
#   S2 tail                  — ambient telemetry (board, dream, task ledger)
#   S5 agent-bus             — transient, but !N means an agent is BLOCKED on you
#   S4b 24h cost             — informational only; first to go, by design
#
# S0 is not in that list because it never shares the line: when a break blurt
# is live it prints and exits above, and none of this runs at all.
#
# Order is UNCHANGED from v1. Moving the S2 tail behind S4 and S5 would put the
# bar inside a 110-column window (~106 instead of ~130) and is the one further
# improvement available without runtime reordering — proposed but not approved,
# so it is deliberately not done here.
# ── Crunch flash: gold full-liner with session stats ─────────────────────────
# Fires at random intervals (13-52 min, 8-min dead zone from the previous).
# Uses data already computed above: bean counts, git stats, context %.
if [ -n "${CACHE_DIR:-}" ] && [ -n "${cwd:-}" ]; then
  _cf_file="${CACHE_DIR}/crunch-$(_ckey "$cwd")"
  _cf_last=0; _cf_interval=0; _cf_prev=0
  [ -f "$_cf_file" ] && read -r _cf_last _cf_interval _cf_prev < "$_cf_file" 2>/dev/null
  case "$_cf_last"     in (*[!0-9]*|"") _cf_last=0;; esac
  case "$_cf_interval"  in (*[!0-9]*|"") _cf_interval=$(( RANDOM % 40 + 13 ));; esac
  case "$_cf_prev"      in (*[!0-9]*|"") _cf_prev=0;; esac
  # First sight: record and stay silent
  if [ "$_cf_last" -eq 0 ]; then
    _cf_interval=$(( RANDOM % 40 + 13 ))
    printf '%s %s %s\n' "$NOW" "$_cf_interval" "0" > "$_cf_file" 2>/dev/null
  elif [ $(( NOW - _cf_last )) -ge $(( _cf_interval * 60 )) ]; then
    # Pick next interval, 8-min dead zone from previous
    _cf_next=$(( RANDOM % 40 + 13 ))
    _cf_diff=$(( _cf_next - _cf_interval )); [ "$_cf_diff" -lt 0 ] && _cf_diff=$(( -_cf_diff ))
    [ "$_cf_diff" -lt 8 ] && _cf_next=$(( (_cf_interval + 15) % 40 + 13 ))
    printf '%s %s %s\n' "$NOW" "$_cf_next" "$_cf_interval" > "$_cf_file" 2>/dev/null
    # Build the phrase with live data
    _o="${b_beans_open:-0}"; _d="${b_beans_doing:-0}"; _cr="${b_beans_crit:-0}"
    _bg="${b_beans_bug:-0}"; _r="${r:-?}"; _ins="${ins:-0}"; _del="${del:-0}"
    _ah="${ahead:-0}"
    _mi="${b_beans_mile:-0}"; _ep="${b_beans_epic:-0}"
    _pool=(
      "${_o} beans open. ${_d} in progress. ${_cr} on fire. Still here."
      "${_ins} lines in. ${_del} lines out. Net $(( _ins - _del )). Make of that what you will."
      "${_d} beans in flight. ${_o} total. You are the bottleneck."
      "${_r}% context left. ${_o} beans open. The math checks out."
      "${_o} open. ${_bg} bugs. ${_cr} critical. Sleep is a feature."
      "${_ins} insertions this session. The keyboard owes you a drink."
      "Running ${_d} concurrent threads of work. Each one trusts you not to drop it."
      "${_ah} commits unpushed. Your future self just sighed."
      "${_o} beans. ${_bg} bugs. ${_cr} fires. ${_r}% context. This is fine."
      "The backlog sees you. ${_o} items. It can wait. Can you?"
      "${_d} beans doing. ${_cr} critical. The standup was 4 hours ago."
      "${_ins}/${_del} lines touched. Zero mass produced. Artisanal code."
      "${_o} beans open at $(date +%H:%M). The grind has no clock."
      "${_bg} bugs. That is all. That is the update."
      "${_r}% headroom. ${_o} beans. ${_d} doing. Pace yourself. Or don't."
    )
    [ "$_mi" -gt 0 ] 2>/dev/null && _pool+=("${MILE}▣${_mi}${R}${BEANS} milestones nearing the line. Almost there. Almost.")
    [ "$_ep" -gt 0 ] 2>/dev/null && _pool+=("${EPIC}⬡${_ep}${R}${BEANS} epics closing in. The children are almost done.")
    [ "$_mi" -gt 0 ] 2>/dev/null && [ "$_ep" -gt 0 ] 2>/dev/null && _pool+=("${MILE}▣${_mi}${R}${BEANS} milestones. ${EPIC}⬡${_ep}${R}${BEANS} epics. The roadmap is moving.")
    [ -n "${_vel_spark:-}" ] && _pool+=("${HASH}${_vel_spark}${R}${BEANS} — ${_vel_total:-0} beans closed this week. The backlog remembers.")
    _ci=$(( RANDOM % ${#_pool[@]} ))
    printf '%b◍ %b%b\n' "${BEANS}" "${_pool[$_ci]}" "$R"
    exit 0
  fi
fi

out="${s1_col}"
[ -n "$peer_col" ] && out+=" ${peer_col}"
[ -n "$s2_col"  ] && out+="${SEP}${s2_col}${s2_tail_col}"
out+="${SEP}${s4_col}"
[ -n "$s5_col"  ] && out+="${SEP}${s5_col}"
[ -n "$beans_label" ] && [ -z "$beans_slotted" ] && out+="${SEP}${beans_label}"
[ -n "$s4b_col" ] && out+="${SEP}${s4b_col}"
printf '%b\n' "$out"
