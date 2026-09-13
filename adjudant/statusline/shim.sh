#!/usr/bin/env bash
# ~/.claude/statusline-v2.sh — installed by adjudant/statusline/install.sh.
#
# Claude Code runs this on every repaint. It execs the statusline that ships
# inside the installed adjudant plugin, so the bar follows plugin updates and
# nothing under ~/.claude carries statusline logic of its own.
#
# The plugin's install path is versioned (~/.claude/plugins/cache/<market>/
# adjudant/<version>/), so a symlink would break on every release. Instead
# adjudant's session-start hook writes the current path to the pointer file
# below each time a session starts; this shim reads it. The glob is the
# fallback for a machine that installed the plugin but has not started a
# session since, or whose pointer names a version that was pruned.
#
# ADJUDANT_STATUSLINE, when set, wins outright: that is how a checkout of the
# plugin repo drives the bar with its working copy while editing it.
pointer="$HOME/.claude/adjudant-statusline-path"
target="${ADJUDANT_STATUSLINE:-}"
if [ -z "$target" ] && [ -r "$pointer" ]; then
  IFS= read -r target < "$pointer"
fi
if [ -z "$target" ] || [ ! -f "$target" ]; then
  target=""
  for cand in "$HOME"/.claude/plugins/cache/*/adjudant/*/statusline/statusline.sh; do
    [ -f "$cand" ] || continue
    # Newest version wins: sort -V on the version segment of the path.
    if [ -z "$target" ] || [ "$(printf '%s\n%s\n' "$target" "$cand" | sort -V | tail -1)" = "$cand" ]; then
      target="$cand"
    fi
  done
fi
if [ -z "$target" ]; then
  # Nothing to run. Say so on the bar rather than render blank space.
  cat >/dev/null
  printf '\033[2mno adjudant statusline\033[0m\n'
  exit 0
fi
exec bash "$target"
