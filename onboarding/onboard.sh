#!/usr/bin/env bash
# onboard.sh — the friendly "CLI on-ramp" installer
# =================================================
# Hi! This little script sets up a small, gentle set of terminal tools for
# people who mostly DON'T live in a terminal. It's safe, chatty, and idempotent
# — meaning you can run it as many times as you like and it won't make a mess or
# double up on anything.
#
# What it does, in plain English:
#   1. Checks you have Homebrew (the macOS app-store-for-the-terminal).
#   2. Installs the friendly tools from Brewfile.casual.
#   3. Adds ONE line to your ~/.zshrc so the nice shortcuts turn on.
#   4. Offers to make your git diffs beautiful (git-delta).
#   5. Tells you the next steps inside Claude Code.
#
# Try it safely first with:   bash onboard.sh --check
# That's a dry run: it prints what it WOULD do and changes absolutely nothing.
#
# Not on macOS? This script leans on Homebrew, which is macOS/Linuxbrew only.
# You can still get everything: install the tools from Brewfile.casual with your
# own package manager (apt, dnf, pacman, ...), then manually add this line to
# your shell rc file:
#     [ -f "/full/path/to/onboarding/casual-qol.zsh" ] && source "/full/path/to/onboarding/casual-qol.zsh"

# ── Safety first ──────────────────────────────────────────────────────────────
# -e  : stop if any command fails
# -u  : stop if we use a variable that was never set
# -o pipefail : a failure anywhere in a pipe counts as a failure
set -euo pipefail

# Figure out WHERE this script lives, so it works no matter where the repo was
# cloned or how the script was invoked. Everything is anchored to this.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"        # the repo root is one level up
BREWFILE="$SCRIPT_DIR/Brewfile.casual"           # the bundle of tools to install
QOL_FILE="$SCRIPT_DIR/casual-qol.zsh"            # the shortcuts we source into zsh
ZSHRC="$HOME/.zshrc"                              # the shell config we gently edit

# ── Flags: --check (dry run) and --help ────────────────────────────────────────
# We detect these early so nothing side-effecting happens in --check mode.
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --check)
      CHECK=1
      ;;
    --help|-h)
      cat <<'EOF'
onboard.sh — set up the friendly CLI tools

Usage:
  bash onboard.sh            Run the setup for real (asks before big changes).
  bash onboard.sh --check    Dry run: print what WOULD happen, change nothing.
  bash onboard.sh --help     Show this help.

Safe to run more than once — it never duplicates changes.
EOF
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$arg" >&2
      printf 'Try: bash onboard.sh --help\n' >&2
      exit 1
      ;;
  esac
done

# ── Pretty output helpers (degrade gracefully if `gum` isn't installed yet) ─────
# On a fresh machine gum probably ISN'T installed yet — that's fine. These
# wrappers use gum when it's around for nice styling, and fall back to plain
# printf/read otherwise. We never hard-depend on gum.
HAVE_GUM=0
if command -v gum >/dev/null 2>&1; then
  HAVE_GUM=1
fi

# say <message...> : print a friendly line (styled if gum is available)
say() {
  if [ "$HAVE_GUM" -eq 1 ]; then
    gum style --foreground 212 "$*"
  else
    printf '%s\n' "$*"
  fi
}

# header <title...> : print a section header that stands out a little
header() {
  if [ "$HAVE_GUM" -eq 1 ]; then
    gum style --bold --border rounded --padding "0 1" --foreground 51 "$*"
  else
    printf '\n== %s ==\n' "$*"
  fi
}

# confirm <question...> : ask a yes/no question, return 0 for yes, 1 for no.
# In --check mode we never actually prompt — we just say we'd ask and answer no,
# so the dry run stays completely non-interactive and side-effect-free.
confirm() {
  if [ "$CHECK" -eq 1 ]; then
    printf '   (dry run) would ask: %s\n' "$*"
    return 1
  fi
  if [ "$HAVE_GUM" -eq 1 ]; then
    gum confirm "$*"
  else
    # Plain fallback: read a line, treat y/yes (any case) as yes.
    local reply
    printf '%s [y/N] ' "$*"
    read -r reply || reply=""
    case "$reply" in
      [yY]|[yY][eE][sS]) return 0 ;;
      *) return 1 ;;
    esac
  fi
}

# ── Welcome ─────────────────────────────────────────────────────────────────────
header "Welcome to the casual CLI on-ramp"
say "This sets up a few gentle terminal tools. Nothing scary, promise."
if [ "$CHECK" -eq 1 ]; then
  say "Running in --check mode: I'll only DESCRIBE what I'd do. No changes."
fi

# ── Step 1: Homebrew ────────────────────────────────────────────────────────────
# We do NOT auto-install Homebrew. Its installer needs your password and network
# access, and it's the kind of thing you should run yourself with eyes open.
header "Step 1 of 5 — Check for Homebrew"
if command -v brew >/dev/null 2>&1; then
  say "Homebrew is installed. Great — moving on."
else
  say "Homebrew isn't installed yet, and I won't install it for you"
  say "(it needs your password and network access — better you run it)."
  printf '\nCopy-paste this into your terminal to install Homebrew:\n\n'
  printf '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"\n\n'
  say "Then re-run me:  bash \"$SCRIPT_DIR/onboard.sh\""
  # Exit gracefully — this isn't an error, it's just "come back after Homebrew".
  exit 0
fi

# ── Step 2: Install the friendly tools ──────────────────────────────────────────
header "Step 2 of 5 — Install the tools"
say "These come from: $BREWFILE"
say "It's completely safe to re-run — Homebrew skips anything you already have."
if [ "$CHECK" -eq 1 ]; then
  printf '   (dry run) would run: brew bundle --file="%s"\n' "$BREWFILE"
else
  brew bundle --file="$BREWFILE"
  say "Tools are ready."
fi

# ── Step 3: Wire the shortcuts into your shell ──────────────────────────────────
# We append exactly ONE guarded source line to ~/.zshrc, and only if it isn't
# already there (grep -qF makes this idempotent — run us twice, no duplicates).
header "Step 3 of 5 — Turn on the shortcuts"

# The exact two-line block we want present in ~/.zshrc.
QOL_MARKER='# casual-qol (furtive-follies onboarding) — quality-of-life aliases, all guarded'
QOL_SOURCE="[ -f \"$QOL_FILE\" ] && source \"$QOL_FILE\""

if [ -f "$ZSHRC" ] && grep -qF "$QOL_SOURCE" "$ZSHRC"; then
  say "Your ~/.zshrc already sources casual-qol.zsh — nothing to do here."
else
  if [ "$CHECK" -eq 1 ]; then
    printf '   (dry run) would back up ~/.zshrc, then append:\n'
    printf '     %s\n' "$QOL_MARKER"
    printf '     %s\n' "$QOL_SOURCE"
  else
    # Back up first, using a timestamp so we never clobber an old backup.
    if [ -f "$ZSHRC" ]; then
      backup="$ZSHRC.bak-$(date +%s)"
      cp "$ZSHRC" "$backup"
      say "Backed up your ~/.zshrc to: $backup"
    fi
    # Append the guarded block. The blank line keeps things tidy.
    {
      printf '\n%s\n' "$QOL_MARKER"
      printf '%s\n' "$QOL_SOURCE"
    } >> "$ZSHRC"
    say "Added the shortcuts to your ~/.zshrc."
    say "Open a new terminal (or run: source ~/.zshrc) to feel the difference."
  fi
fi

# ── Step 4: Beautiful git diffs with git-delta ──────────────────────────────────
# This one is opt-in because it changes your global git config. We ask first.
header "Step 4 of 5 — Prettier git diffs (optional)"
if confirm "Set up git-delta so 'git diff' looks beautiful?"; then
  # These write to ~/.gitconfig via --global. Guarded behind the confirm above,
  # and skipped entirely in --check mode (confirm() returns no in dry run).
  git config --global core.pager delta
  git config --global interactive.diffFilter "delta --color-only"
  git config --global delta.navigate true          # use n / N to move between diffs
  git config --global delta.line-numbers true       # show line numbers in diffs
  git config --global merge.conflictStyle zdiff3     # clearer merge conflicts
  say "Done — your git diffs are now powered by delta."
else
  say "Skipping git-delta setup. You can run me again anytime to enable it."
fi

# ── Step 5: What to do next inside Claude Code ──────────────────────────────────
header "Step 5 of 5 — Next steps in Claude Code"
say "Open Claude Code and run these, one at a time:"
printf '\n'
printf '   /plugin marketplace add TomVDH/furtive-follies\n'
printf '   /plugin install adjudant\n'
printf '   /adjudant connect\n'
printf '\n'
say "Heads up: this repo is PRIVATE, so you'll need GitHub access first."
say "If you haven't already, sign in with:  gh auth login"

# ── Friendly closing ────────────────────────────────────────────────────────────
header "All set!"
say "You're done. Enjoy the calmer, friendlier terminal."
# A tiny calming easter-egg: grow a bonsai on the way out, IF cbonsai exists and
# we're not in dry-run mode. Guarded so a missing tool just skips silently.
if [ "$CHECK" -eq 0 ] && command -v cbonsai >/dev/null 2>&1; then
  cbonsai -p
fi
