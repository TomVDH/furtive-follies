#!/usr/bin/env bash
# statusline-tokens-24h — background refresher for the statusline's S4b segment.
#
# Aggregates the trailing 24h of token usage across EVERY session transcript under
# ~/.claude/projects and writes the result to /tmp/claude-tokens-24h.json, which
# claude/statusline-command.sh reads (and only ever reads — it never computes).
#
# Spawned detached via nohup by statusline-command.sh when the cache ages past its
# TOK_TTL (120s). Takes no arguments, holds its own lock so at most one runs, and
# writes the cache atomically so a half-written file is never observed.
#
# Contract (statusline-command.sh:394-423):
#   ts          unix epoch of this write — drives the staleness gate
#   wire_sent   input + cache writes + cache reads  (what actually hit the API)
#   fresh_sent  input + cache writes                (new tokens, no cache replay)
#   out         output tokens
#   cost_usd    simulated API-equivalent value; NOT a bill (Max subscription)
#
# Lives in adjudant/statusline/ next to statusline.sh, which calls it by a
# path relative to itself. Nothing here is linked into ~/.claude any more.

set -uo pipefail

PROJECTS="$HOME/.claude/projects"
CACHE="/tmp/claude-tokens-24h.json"
LOCK="/tmp/claude-tokens-24h.lock"
LOCK_STALE=300   # seconds; a lock older than this is presumed abandoned

command -v jq >/dev/null 2>&1 || exit 0
[ -d "$PROJECTS" ] || exit 0

# ── Lock ─────────────────────────────────────────────────────────────────────
# mkdir is atomic on every POSIX filesystem, so it works as a mutex without
# flock (which macOS/bash 3.2 lacks). A crashed run leaves the dir behind, so
# anything older than LOCK_STALE is reclaimed.
if ! mkdir "$LOCK" 2>/dev/null; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$lock_age" -lt "$LOCK_STALE" ]; then exit 0; fi
  rmdir "$LOCK" 2>/dev/null
  mkdir "$LOCK" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

NOW=$(date +%s)

# ── Aggregate ────────────────────────────────────────────────────────────────
# Only transcripts touched in the last 24h can hold entries from the last 24h
# (they're append-only), so -mtime -1 cuts ~940 files down to a handful. Those
# files still contain much older entries, so each line is timestamp-filtered too.
#
# Dedup by requestId is mandatory, not an optimisation: one API request emits
# many assistant lines, each carrying the SAME cumulative usage. Summing raw
# lines overcounts by roughly 2x.
#
# Pricing per https://platform.claude.com/docs/en/about-claude/pricing —
# cache multipliers are 1.25x base input (5m write), 2x (1h write), 0.1x (read).
read -r ts wire fresh out cost <<EOF
$(
  find "$PROJECTS" -name '*.jsonl' -mtime -1 -print0 2>/dev/null \
  | xargs -0 -- cat 2>/dev/null \
  | jq -n --argjson now "$NOW" -r '
    def base($m):
      if   ($m|test("^claude-opus-(5|4-[5-9])"))  then {i:5,   o:25}
      elif ($m|test("^claude-opus-4"))            then {i:15,  o:75}
      elif ($m|test("^claude-sonnet-5"))          then
             # Introductory pricing through 2026-08-31; standard from Sep 1 2026.
             (if $now < 1788220800 then {i:2, o:10} else {i:3, o:15} end)
      elif ($m|test("^claude-sonnet-4"))          then {i:3,   o:15}
      elif ($m|test("^claude-haiku-4"))           then {i:1,   o:5}
      elif ($m|test("^claude-haiku-3"))           then {i:0.8, o:4}
      elif ($m|test("^claude-(fable|mythos)-5"))  then {i:10,  o:50}
      else null end;

    # Fast mode (research preview) reprices Opus 5 / 4.8 across the whole window.
    def rates($m; $speed):
      if $speed == "fast" and ($m|test("^claude-opus-(5|4-8)"))
      then {i:10, o:50} else base($m) end;

    def cutoff: $now - 86400;

    reduce (inputs
            | select(.requestId? and (.message.usage? | type == "object"))
            | select((.message.model // "") | startswith("claude"))
            | select((.timestamp // "" | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601? // 0) >= cutoff)
           ) as $l ({};
      # Keep the line with the LARGEST output_tokens, not the first. Streaming
      # emits several lines per request: input/cache figures are identical on all
      # of them, but output_tokens grows, and only the final line (stop_reason set)
      # carries the true total. Taking the first would undercount output badly.
      if (.[$l.requestId].message.usage.output_tokens // -1)
         >= ($l.message.usage.output_tokens // 0)
      then . else .[$l.requestId] = $l end
    )
    | [ .[] ]
    | map(
        .message.usage as $u
        | ($u.cache_creation.ephemeral_5m_input_tokens // 0) as $w5
        | ($u.cache_creation.ephemeral_1h_input_tokens // 0) as $w1h
        # Fall back to the flat cache_creation_input_tokens (priced as a 5m write)
        # when the per-duration breakdown is absent on older entries.
        | (if ($u.cache_creation | type) == "object" then $w5
           else ($u.cache_creation_input_tokens // 0) end) as $w5
        | ($u.input_tokens // 0)             as $in
        | ($u.cache_read_input_tokens // 0)  as $rd
        | ($u.output_tokens // 0)            as $og
        | ($w5 + $w1h)                       as $wr
        | rates(.message.model; $u.speed // "standard") as $r
        # US-only inference carries a 1.1x multiplier on every token category.
        | (if ($u.inference_geo // "") == "us" then 1.1 else 1 end) as $geo
        | {
            wire:  ($in + $wr + $rd),
            fresh: ($in + $wr),
            out:   $og,
            cost:  (if $r == null then 0 else
                     (($in * $r.i) + ($w5 * $r.i * 1.25) + ($w1h * $r.i * 2)
                      + ($rd * $r.i * 0.1) + ($og * $r.o)) / 1000000 * $geo
                   end)
          }
      )
    | [ (map(.wire) | add // 0), (map(.fresh) | add // 0),
        (map(.out)  | add // 0), (map(.cost)  | add // 0) ]
    | "\(.[0]|round) \(.[1]|round) \(.[2]|round) \(.[3])"
  ' 2>/dev/null | { read -r a b c d; echo "$NOW ${a:-0} ${b:-0} ${c:-0} ${d:-0}"; }
)
EOF

# A failed/empty aggregation must not clobber a good cache with zeroes.
if [ "${wire:-0}" = "0" ] && [ "${out:-0}" = "0" ] && [ -s "$CACHE" ]; then
  exit 0
fi

# ── Write atomically ─────────────────────────────────────────────────────────
# Same filesystem as the target, so mv is a rename — readers see old or new, never partial.
tmp="${CACHE}.$$"
jq -n --argjson ts "${ts:-$NOW}" \
      --argjson wire_sent "${wire:-0}" \
      --argjson fresh_sent "${fresh:-0}" \
      --argjson out "${out:-0}" \
      --argjson cost_usd "${cost:-0}" \
      '{ts:$ts, wire_sent:$wire_sent, fresh_sent:$fresh_sent, out:$out, cost_usd:$cost_usd}' \
      > "$tmp" 2>/dev/null \
  && mv -f "$tmp" "$CACHE" \
  || rm -f "$tmp"
