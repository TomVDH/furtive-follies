# casual-qol.zsh — gentle quality-of-life shortcuts for your terminal
# --------------------------------------------------------------------
# This file is meant to be *sourced* from your ~/.zshrc (the onboarding
# installer, onboard.sh, adds that one line for you).
#
# Every block below is GUARDED: it only turns on if the matching tool is
# actually installed. If a tool is missing, that block just quietly skips —
# nothing breaks, nothing errors. So it's totally safe to keep this file
# sourced even before you've installed everything.
#
# Not on macOS? Install the tools with your own package manager, then source
# this file from your shell rc the same way — the guards do the rest.

# ── zoxide: smarter cd ──
# After you've visited a folder once, `z part-of-its-name` jumps straight back.
if command -v zoxide >/dev/null 2>&1; then
  eval "$(zoxide init zsh)"
fi

# ── eza: modern ls ──  (ls stays the REAL binary — agent-safe; pretty listings live on l/ll/lt)
# We deliberately DON'T alias `ls` itself. Tools and agents (including Claude
# Code) expect plain `ls` to behave exactly like the real thing, so we leave it
# untouched and put the pretty versions on their own short names instead.
if command -v eza >/dev/null 2>&1; then
  alias l='eza --group-directories-first --icons=auto'
  alias ll='eza -lah --git --group-directories-first --icons=auto'
  alias lt='eza --tree --level=2 --group-directories-first --icons=auto'
fi

# ── bat: cat with syntax highlighting ── (bat is pipe-safe: behaves like cat when piped)
# Aliasing `cat` to `bat` is safe because bat automatically acts like plain cat
# when its output is piped into another command — so scripts and pipelines keep
# working, and you only get the pretty highlighting when reading in your terminal.
if command -v bat >/dev/null 2>&1; then
  export BAT_STYLE=plain
  alias cat='bat'
fi

# ── git-delta: nothing to do here ──
# Delta isn't an alias — it's wired into git via `git config` (the onboard.sh
# installer offers to set that up for you). Just noting it so it's not a mystery.

# Reminder: run `tldr --update` once on a new machine to fetch the example cache.
