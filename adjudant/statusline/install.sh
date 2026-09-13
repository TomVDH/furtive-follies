#!/usr/bin/env bash
# Install the adjudant statusline on this machine. Run once per machine, and
# again only if ~/.claude/statusline-v2.sh was replaced by something else.
#
#   bash adjudant/statusline/install.sh
#
# What it does:
#   1. Copies shim.sh to ~/.claude/statusline-v2.sh. A file that is already
#      the shim is overwritten in place; anything else (the old iCloud symlink,
#      a hand-written script) is moved aside as *.bak-<timestamp> first.
#   2. Writes ~/.claude/adjudant-statusline-path pointing at THIS checkout's
#      statusline.sh, so the bar works before the next session start refreshes
#      the pointer to the installed plugin copy.
#   3. Prints the settings.json block if statusLine.command is not already the
#      shim. It never edits settings.json.
set -eu
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="$HOME/.claude/statusline-v2.sh"
pointer="$HOME/.claude/adjudant-statusline-path"
mkdir -p "$HOME/.claude"

if [ -e "$dest" ] || [ -L "$dest" ]; then
  if ! grep -q 'adjudant-statusline-path' "$dest" 2>/dev/null; then
    bak="$dest.bak-$(date +%Y%m%d-%H%M%S)"
    mv "$dest" "$bak"
    echo "moved the previous statusline aside: $bak"
  fi
fi
cp "$here/shim.sh" "$dest"
chmod +x "$dest"
printf '%s\n' "$here/statusline.sh" > "$pointer"
echo "installed $dest"
echo "pointer  $pointer -> $here/statusline.sh"

settings="$HOME/.claude/settings.json"
if ! grep -q 'statusline-v2.sh' "$settings" 2>/dev/null; then
  cat <<'BLOCK'

Add this to ~/.claude/settings.json (not done for you):

  "statusLine": {
    "type": "command",
    "command": "bash \"$HOME/.claude/statusline-v2.sh\""
  }
BLOCK
fi
