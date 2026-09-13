#!/usr/bin/env bash
# _ops_flash.sh — write a one-line operation flash for the statusline.
# Source this, then call: ops_flash "board: 44 cards"
# The statusline picks it up on its next repaint (default TTL: 8s).
ops_flash() {
  local msg="$1"
  [ -z "$msg" ] && return 0
  local cache_dir="${HOME}/.claude/statusline-cache"
  local project_dir="${CLAUDE_PROJECT_DIR:-}"
  [ -z "$project_dir" ] && return 0
  [ -d "$cache_dir" ] || return 0
  local key
  key="${project_dir//\//_}"; key="${key// /-}"; [ "${#key}" -gt 120 ] && key="${key: -120}"
  printf '%s %s\n' "$(date +%s)" "$msg" > "${cache_dir}/ops-${key}" 2>/dev/null
}
