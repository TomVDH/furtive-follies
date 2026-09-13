#!/usr/bin/env bash
# _ops_flash.sh: write an ops flash for the statusline.
# Source it, then call: ops_flash "board: 44 cards" [project_dir]
# The statusline paints ⚡ on the next repaint. It stays ten seconds after first sight.
# Thin wrapper over scripts/_ops_flash.py. The Python module owns the key rule.
# One spelling of the key. Two spellings drift.
ops_flash() {
  local msg="$1" project_dir="${2:-${CLAUDE_PROJECT_DIR:-}}"
  [ -z "$msg" ] && return 0
  [ -z "$project_dir" ] && return 0
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  python3 "${here}/../../scripts/_ops_flash.py" --project-dir "$project_dir" "$msg" >/dev/null 2>&1 || true
}
