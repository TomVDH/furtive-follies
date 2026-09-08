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
  printf -- '[adjudant] Intent line is still the placeholder in `%s`: replace it with one plain sentence now that the session has a purpose, then leave it frozen.\n' "$session_file"
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
    printf '[adjudant] Vault not linked for this project. Run `/adjudant connect` to capture this work in the vault.\n'
  fi
}

main "$@" || exit 0
