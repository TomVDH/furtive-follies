#!/usr/bin/env bash
# user-prompt-reminder.sh — UserPromptSubmit hook for adjudant
# Smart-fire vault reminder when project isn't vault-linked AND prompt mentions vault-y keywords.
# Fires at most ONCE per Claude Code session (marker keyed by session_id).
# Suppression: ADJUDANT_REMINDER_DISABLE=1 turns it off entirely.
set -euo pipefail

PLACEHOLDER='{One-line intent. Frozen after first write.}'

# Nag a LINKED project about an unwritten intent line. Lives here rather than
# in SessionStart because SessionStart runs before the session has a purpose to
# record, and re-runs on every resume and compact — it fired twice in three
# hours, both times too early to act on. By the time a prompt exists there is
# something to write. Fires at most once, from the second prompt on, and only
# while the placeholder stands, so writing the line ends it.
#
# The session note's path comes from a pointer SessionStart drops after it has
# resolved the vault. Re-deriving it here would mean a second copy of the
# zone-aware lookup, and two copies drift.
intent_nag() {
  local session_id="$1" tmp="${TMPDIR:-/tmp}"
  if [ -z "$session_id" ] || [ "$session_id" = "-" ]; then return 0; fi
  local pointer="$tmp/adjudant-session-$session_id"
  local fired="$tmp/adjudant-intent-$session_id"
  local turns="$tmp/adjudant-turns-$session_id"
  [ -f "$pointer" ] || return 0
  [ -f "$fired" ] && return 0
  # First prompt of the session: the purpose is still being stated. Count it
  # and stay quiet — firing here is the bug this move fixes.
  if [ ! -f "$turns" ]; then { : > "$turns"; } 2>/dev/null || true; return 0; fi
  local session_file
  session_file=$(head -n1 "$pointer" 2>/dev/null | tr -d '\r' || true)
  [ -n "$session_file" ] && [ -f "$session_file" ] || return 0
  grep -qF -- "$PLACEHOLDER" "$session_file" 2>/dev/null || return 0
  find "$tmp" -maxdepth 1 \( -name 'adjudant-intent-*' -o -name 'adjudant-turns-*' \
       -o -name 'adjudant-session-*' \) -mtime +1 -delete 2>/dev/null || true
  { : > "$fired"; } 2>/dev/null || true
  printf -- '[adjudant] `%s`: intent line is a placeholder. Write it.\n' "$session_file"
}

# The canary's reporting half. SessionStart names the codeword once; this reads
# the tally the Stop hook keeps and speaks only after a miss. It must NEVER
# print the codeword itself: restating the instruction would keep the model
# obeying it and the check would measure nothing (test_canary asserts this).
canary_report() {
  local session_id="$1" tmp="${TMPDIR:-/tmp}"
  [ -n "$session_id" ] || return 0
  case "$session_id" in *[!A-Za-z0-9._-]*) return 0 ;; esac
  local state="$tmp/adjudant-canary-${session_id}.json"
  [ -f "$state" ] || return 0
  local scripts_dir
  scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  python3 - "$state" "$scripts_dir" <<'CANARY_PY' 2>/dev/null || true
import json, sys, os
sys.path.insert(0, sys.argv[2])
from _canary_swan import SWAN
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
misses, turns = int(s.get("misses", 0)), int(s.get("turns", 0))
word = s.get("word", "")
if misses and turns > 0:
    if misses / turns > 0.75 and word in SWAN:
        print(f"[adjudant] Canary: {misses}/{turns} missed. "
              f"☾ {SWAN[word]}.")
    else:
        print(f"[adjudant] Canary: {misses}/{turns} missed. Wrap up.")
CANARY_PY
}

# Ultracode marker. The statusline paints the context bar purple while it exists.
# Path: $TMPDIR/claude-ultracode-<session_id>. Nothing wrote it before this.
# The word `ultracode` in a prompt is the opt-in. `ultracode off` ends it.
# Keyed to the session id. Same filename check as canary_report.
# Old markers are swept.
ultracode_marker() {
  local session_id="$1" prompt="$2" tmp="${TMPDIR:-/tmp}"
  if [ -z "$session_id" ] || [ "$session_id" = "-" ]; then return 0; fi
  case "$session_id" in *[!A-Za-z0-9._-]*) return 0 ;; esac
  local marker="$tmp/claude-ultracode-${session_id}"
  if printf '%s' "$prompt" | grep -qiE '\bultracode\s+off\b'; then
    rm -f "$marker" 2>/dev/null || true
  elif printf '%s' "$prompt" | grep -qiE '\bultracode\b'; then
    find "$tmp" -maxdepth 1 -name 'claude-ultracode-*' -mtime +1 -delete 2>/dev/null || true
    { : > "$marker"; } 2>/dev/null || true
  fi
}

main() {
  [ "${ADJUDANT_REMINDER_DISABLE:-0}" = "1" ] && return 0

  local project_dir="${CLAUDE_PROJECT_DIR:-}"
  [ -z "$project_dir" ] && return 0

  # Hook payloads arrive on stdin; when run manually (a TTY) there is nothing
  # to read — bail instead of blocking on cat until Ctrl-D.
  [ -t 0 ] && return 0

  # Read prompt + session id from stdin JSON
  local input prompt="" session_id=""
  input=$(cat 2>/dev/null || true)
  [ -z "$input" ] && return 0

  if command -v python3 >/dev/null 2>&1; then
    # One line out: "<session_id-or--> <prompt, newlines collapsed>"
    read -r session_id prompt <<< "$(printf '%s' "$input" | python3 -c 'import json,sys
try:
  d = json.load(sys.stdin)
  sid = str(d.get("session_id") or "-")
  prompt = str(d.get("prompt") or "").replace("\n", " ")
  print(sid, prompt)
except Exception:
  pass' 2>/dev/null || true)" || true
  fi
  [ -z "$prompt" ] && return 0

  # Code comments rule, every prompt. No once-per-session marker.
  # A rule stated once is lost at the next compact.
  # The text lives in _comment_rule.txt. session-start reads the same file.
  local _rule
  _rule=$(head -n1 "$(dirname "${BASH_SOURCE[0]}")/_comment_rule.txt" 2>/dev/null | tr -d '\r' || true)
  [ -n "$_rule" ] && printf '[adjudant] %s\n' "$_rule"

  # Silent: a marker on disk, never a line of output.
  ultracode_marker "$session_id" "$prompt"

  # Every turn, linked project or not: drift is a property of the session, not
  # of the vault. Silent while healthy, the rule the statusline applies to its
  # own segments - a signal that never varies carries no information.
  canary_report "$session_id"

  # Graceful sign-off: on a wrap-up phrase, print the etymology as a farewell.
  # "wrap the convo" and "wrap this session" said goodbye and got nothing
  # (2026-09-13): the list knew only "wrap up". A wrap that names the thing
  # being wrapped counts too, and so does "let's wrap" on its own; "wrap this
  # in a div" still does not.
  if printf '%s' "$prompt" | grep -qiE '\b(let.?s|we.?ll|time to)\s+wrap\b|\bwrap(ping)?\s+((it|this|things)\s+)?up\b|\bwrap(ping)?\s+(it|this|the|our|up the)\s+(convo|conversation|session|chat|talk)\b|\bwinding down\b|\bsigning off\b|\bdone for (now|today|tonight)\b|\bthat.s (it|all)\b|\bwe.re done\b'; then
    local tmp="${TMPDIR:-/tmp}" canary_state=""
    if [ -n "$session_id" ] && [ "$session_id" != "-" ]; then
      canary_state="$tmp/adjudant-canary-${session_id}.json"
      if [ -f "$canary_state" ]; then
        local sd
        sd="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        python3 - "$canary_state" "$sd" <<'SWAN_PY' 2>/dev/null || true
import json, sys
sys.path.insert(0, sys.argv[2])
from _canary_swan import SWAN
try:
    s = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
word = s.get("word", "")
if word in SWAN:
    print(f"[adjudant] ☾ {SWAN[word]}.")
SWAN_PY
      fi
    fi
  fi

  # The two nags have inverse audiences: a linked project can never need the
  # connect reminder, and an unlinked one has no session note to have an
  # intent line in.
  if [ -f "$project_dir/.claude/adjudant" ]; then
    intent_nag "$session_id"
    return 0
  fi

  # Once per session: after the first reminder, stay quiet for this session_id.
  local marker=""
  if [ -n "$session_id" ] && [ "$session_id" != "-" ]; then
    marker="${TMPDIR:-/tmp}/adjudant-reminder-${session_id}"
    [ -f "$marker" ] && return 0
  fi

  # Vault-y keywords → fire reminder. Distinctive words and phrase forms
  # only: bare `brief`/`decision` fired on everyday English like "give me a
  # brief summary" or "good decision" (finding 31) — precision over recall.
  if printf '%s' "$prompt" | grep -qiE '\b(vault|obsidian|handoff|note this|document this|put in vault|record (this|that)|the brief|(this|that) decision)\b'; then
    # Sweep markers from past sessions (they leaked one per session forever),
    # then write this session's. Both best-effort.
    find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'adjudant-reminder-*' -mtime +1 -delete 2>/dev/null || true
    # brace group: silence stderr BEFORE the > open (unwritable TMPDIR)
    if [ -n "$marker" ]; then { : > "$marker"; } 2>/dev/null || true; fi
    printf '[adjudant] No vault linked. Run `/adjudant connect`.\n'
  fi
}

main "$@" || exit 0
