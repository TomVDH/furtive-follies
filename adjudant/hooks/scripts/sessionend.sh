#!/usr/bin/env bash
# sessionend.sh — SessionEnd hook for adjudant
# 1. Run handoff sync via precompact.py (same logic)
# 2. Reseed an existing board so the session's last task edits reach it
#
# It writes no session-log marker: since v3 a session note records work, and
# ending a session is not work.
#
# Resolution parity: same chain as session-start.sh — Python resolve_vault
# when reachable, OB_VAULT + local vault_path in pure-bash degraded mode.
set -euo pipefail

# Zone-aware project resolution. Mirrors _vault_walk.find_project_dir (and
# session-start.sh's copy): prefer a candidate holding brief.md, else any
# existing dir, else fail so the caller no-ops instead of creating a phantom.
# Four named folders first, then the pre-v3 shapes, so a migrated project
# always beats a twin left behind by an interrupted move.
zone_project_dir() {
  local vault="$1" slug="$2" c
  local zones="active paused finished archive"
  local legacy="_fridge _archive"
  local cands=""
  for c in $zones; do cands="$cands $vault/projects/$c/$slug"; done
  cands="$cands $vault/projects/$slug"
  for c in $legacy; do cands="$cands $vault/projects/$c/$slug"; done
  for c in $cands; do
    if [ -f "$c/brief.md" ]; then printf '%s' "$c"; return 0; fi
  done
  for c in $cands; do
    if [ -d "$c" ]; then printf '%s' "$c"; return 0; fi
  done
  return 1
}

main() {
  local project_dir="${CLAUDE_PROJECT_DIR:-}"
  [ -z "$project_dir" ] && return 0

  # Drain stdin. Nothing here needs the payload since the v3 ledger replay
  # went away, but an unread SessionEnd payload EPIPEs the harness writer
  # when this process exits.
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

  local vault_project
  # Zone-aware: shelf moves projects to _fridge/ and _archive/ without touching
  # the breadcrumb; hardcoding projects/$slug appended to a phantom twin.
  vault_project=$(zone_project_dir "$vault_path" "$slug") || return 0

  # v3: no end marker. Together with the start, resume and pause markers this
  # produced 164 "session resumed" lines followed by nothing, and a guard that
  # suppressed exactly one of the four when the tail was already a marker.
  # A session note records work, and the absence of a note records its absence.

  # Handoff sync (best effort, never block)
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/hooks/scripts/precompact.py" ] \
     && command -v python3 >/dev/null 2>&1; then
    python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/precompact.py" --sync-only 2>/dev/null || true
  fi

  # Board reseed only. The ledger replay that turned every uncompleted harness
  # todo into a permanent vault note was removed in v3: an id with no
  # TaskCompleted event is an abandoned or renamed todo, not a work item. The
  # ledger itself stays in $TMPDIR, where the statusline reads it.
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/scripts/board_bridge.py" ] \
     && command -v python3 >/dev/null 2>&1 \
     && [ -f "$vault_project/board/board-data.json" ]; then
    python3 "$CLAUDE_PLUGIN_ROOT/scripts/board_bridge.py" --ensure-only \
      --project-dir "$vault_project" >/dev/null 2>&1 || true
  fi
}

main "$@" || exit 0
