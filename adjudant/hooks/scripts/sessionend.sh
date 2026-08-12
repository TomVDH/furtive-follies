#!/usr/bin/env bash
# sessionend.sh — SessionEnd hook for adjudant
# 1. Append session-ended marker to today's session log
# 2. Run handoff sync via precompact.py (same logic)
#
# Resolution parity: same chain as session-start.sh — Python resolve_vault
# when reachable, OB_VAULT + local vault_path in pure-bash degraded mode.
set -euo pipefail

# Zone-aware project resolution. Mirrors _vault_walk.find_project_dir (and
# session-start.sh's copy): prefer a candidate holding brief.md, else any
# existing dir, else fail so the caller no-ops instead of creating a phantom.
zone_project_dir() {
  local vault="$1" slug="$2" c
  for c in "$vault/projects/$slug" "$vault/projects/_fridge/$slug" \
           "$vault/projects/_archive/$slug"; do
    if [ -f "$c/brief.md" ]; then printf '%s' "$c"; return 0; fi
  done
  for c in "$vault/projects/$slug" "$vault/projects/_fridge/$slug" \
           "$vault/projects/_archive/$slug"; do
    if [ -d "$c" ]; then printf '%s' "$c"; return 0; fi
  done
  return 1
}

main() {
  local project_dir="${CLAUDE_PROJECT_DIR:-}"
  [ -z "$project_dir" ] && return 0

  # Best-effort: read the Claude Code session UUID from stdin JSON (same
  # advisory pattern as session-start.sh). It keys the task ledger the
  # task-ledger hook may have written for this session.
  local session_id=""
  if [ ! -t 0 ] && command -v python3 >/dev/null 2>&1; then
    local payload
    payload=$(cat 2>/dev/null || true)
    if [ -n "$payload" ]; then
      session_id=$(printf '%s' "$payload" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("session_id",""))
except Exception:
    pass' 2>/dev/null || true)
    fi
  fi

  local breadcrumb="$project_dir/.claude/adjudant"
  [ ! -f "$breadcrumb" ] && return 0

  local vault_path slug
  # Breadcrumb format is `key: value` (YAML-ish, written by connect.py);
  # legacy pre-v0.4.0 `key=value` tolerated, matching the Python hooks.
  # tr -d '\r' matches session-start.sh — no CRLF leakage into paths/slugs.
  vault_path=$(sed -n 's/^vault_path[:=][[:space:]]*//p' "$breadcrumb" 2>/dev/null | head -n1 | tr -d '\r' || true)
  slug=$(sed -n 's/^slug[:=][[:space:]]*//p' "$breadcrumb" 2>/dev/null | head -n1 | tr -d '\r' || true)

  [ -z "$slug" ] && return 0
  # Repo-committed breadcrumb: enforce the kebab-case contract before the slug
  # reaches a path (matches session-start.sh and the Python hooks).
  case "$slug" in
    [a-z0-9]*) ;;
    *) return 0 ;;
  esac
  case "$slug" in
    *[!a-z0-9-]*) return 0 ;;
  esac
  [ "${#slug}" -gt 64 ] && return 0
  vault_path="${vault_path/#\~/$HOME}"

  # Full-chain resolution via the Python layer when available (same vault as
  # every other hook and the verbs).
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/scripts/_vault_walk.py" ] \
     && command -v python3 >/dev/null 2>&1; then
    local resolved
    resolved=$(python3 -c 'import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from _vault_walk import resolve_vault
v = resolve_vault(Path(sys.argv[2]))
print(v or "")' "$CLAUDE_PLUGIN_ROOT/scripts" "$project_dir" 2>/dev/null || true)
    [ -n "$resolved" ] && vault_path="$resolved"
  elif [ -n "${OB_VAULT:-}" ] && [ -d "${OB_VAULT/#\~/$HOME}" ]; then
    vault_path="${OB_VAULT/#\~/$HOME}"
  fi

  [ -z "$vault_path" ] && return 0
  [ ! -d "$vault_path" ] && return 0  # stale breadcrumb: never write to a phantom path

  local today ts session_dir session_file vault_project
  # Zone-aware: shelf moves projects to _fridge/ and _archive/ without touching
  # the breadcrumb; hardcoding projects/$slug appended to a phantom twin.
  vault_project=$(zone_project_dir "$vault_path" "$slug") || return 0
  # Single clock read so date and time can't straddle midnight between calls.
  read -r today ts <<< "$(date '+%Y-%m-%d %H:%M')"
  session_dir="$vault_project/sessions"
  session_file="$session_dir/$today.md"

  # Midnight straddle: a session started 23:40 and ended 00:10 targets the new
  # day's note, which doesn't exist yet — fall back to the latest daily note
  # so the end marker isn't silently dropped. Digit classes, not ?: a stray
  # abcd-ef-gh.md must never lexically outrank a real date.
  if [ ! -f "$session_file" ]; then
    session_file=$(ls "$session_dir"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md 2>/dev/null | tail -n1 || true)
  fi

  if [ -n "$session_file" ] && [ -f "$session_file" ]; then
    # Skip the marker when nothing was logged since the last hook marker: a
    # bare started/resumed/paused/ended tail means the pair would be pure
    # churn (quick open/close sessions used to stack noise lines daily).
    local last_line
    last_line=$(grep -v '^[[:space:]]*$' "$session_file" 2>/dev/null | tail -n 1 || true)
    case "$last_line" in
      *"· session started"*|*"session resumed ---"*|*"· session ended"*|*"paused (compaction)"*)
        : ;;
      *)
        # brace group so stderr is silenced BEFORE the >> open (redirections
        # are processed left→right; a read-only vault must stay noiseless)
        { printf -- '- %s · session ended\n' "$ts" >> "$session_file"; } 2>/dev/null || true ;;
    esac
  fi

  # Run handoff sync only — no pause marker (best effort, never block)
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/hooks/scripts/precompact.py" ] \
     && command -v python3 >/dev/null 2>&1; then
    python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/precompact.py" --sync-only 2>/dev/null || true
  fi

  # Task bridge + board reseed (best effort, never block). A session ledger
  # (written by the task-ledger hook) turns its survivors into task notes
  # and births/reseeds the board; without one, an existing board still gets
  # the ensure-only reseed. No ledger and no board: nothing to do, no board
  # is born from a bare session end.
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/scripts/board_bridge.py" ] \
     && command -v python3 >/dev/null 2>&1; then
    local ledger="${TMPDIR:-/tmp}/adjudant-task-ledger-${session_id}.jsonl"
    if [ -n "$session_id" ] && [ -f "$ledger" ]; then
      python3 "$CLAUDE_PLUGIN_ROOT/scripts/board_bridge.py" --bridge "$ledger" \
        --project-dir "$vault_project" >/dev/null 2>&1 || true
    elif [ -f "$vault_project/board/board-data.json" ]; then
      python3 "$CLAUDE_PLUGIN_ROOT/scripts/board_bridge.py" --ensure-only \
        --project-dir "$vault_project" >/dev/null 2>&1 || true
    fi
  fi
}

main "$@" || exit 0
