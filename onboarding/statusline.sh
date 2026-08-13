#!/usr/bin/env bash
# statusline.sh — a small, friendly Claude Code statusline for the furtive-follies
# package. It shows three things, left to right:
#
#   main*            the git branch, with * when you have uncommitted changes
#   ~ my-app         the vault this project is linked to (adjudant), if any
#   Opus 4.8  ▓▓▓░░ 38%   the model, and how full the context window is
#
# WHY context% matters: the bar fills as the chat gets long. When it's near
# full, Claude compacts the conversation. Watching it helps you keep sessions
# (and token cost) in check — the whole point of this bundle.
#
# PORTABLE: works on macOS, Linux, and Windows/WSL. It needs `git`, and reads
# the status JSON with `jq` or `python3` — if you have neither, it quietly shows
# just the git part. It never blocks and never writes anything.
#
# ENABLE IT: add this to ~/.claude/settings.json (the field guide has a copy
# button for the exact line):
#   "statusLine": { "type": "command", "command": "bash /ABSOLUTE/PATH/TO/statusline.sh" }

input=$(cat)

# ── palette (256-color, renders the same on every terminal) ──
R=$'\033[0m'; DIM=$'\033[2m'
C_BRANCH=$'\033[38;5;250m'; C_DIRTY=$'\033[38;5;179m'
C_VAULT=$'\033[38;5;74m';   C_MUTE=$'\033[38;5;244m'
C_MODEL=$'\033[38;5;250m'
C_OK=$'\033[38;5;108m'; C_WARN=$'\033[38;5;179m'; C_HOT=$'\033[38;5;174m'
SEP="  ${DIM}|${R}  "

# ── read the fields we need from the JSON on stdin ──
# Prefer jq; fall back to python3; if neither is present, degrade to git-only.
# Fields are joined on \037 (the "unit separator"), NOT a tab: a tab is IFS
# whitespace, so `read` would collapse empty leading fields and shift values
# into the wrong variables. \037 is not whitespace, so empty fields hold their
# place. jq and python emit the byte from the \u001f escape; bash reads it via
# IFS=$'\037'.
cwd=""; proj=""; model=""; used=""
if command -v jq >/dev/null 2>&1; then
  IFS=$'\037' read -r cwd proj model used < <(
    printf '%s' "$input" | jq -r '[
      (.workspace.current_dir // .cwd // ""),
      (.workspace.project_dir // .workspace.current_dir // .cwd // ""),
      (.model.display_name // .model.id // ""),
      ((.context_window.used_percentage // "") | tostring)
    ] | join("\u001f")' 2>/dev/null)
elif command -v python3 >/dev/null 2>&1; then
  IFS=$'\037' read -r cwd proj model used < <(
    printf '%s' "$input" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
w=d.get("workspace",{}) or {}
cwd=w.get("current_dir") or d.get("cwd") or ""
proj=w.get("project_dir") or cwd
m=d.get("model",{}) or {}
model=m.get("display_name") or m.get("id") or ""
c=d.get("context_window",{}) or {}
used=c.get("used_percentage")
print("\u001f".join([cwd,proj,model,"" if used is None else str(used)]))' 2>/dev/null)
fi
[ -z "$cwd" ] && cwd=$(pwd)
[ -z "$proj" ] && proj="$cwd"

# ── segment 1: git branch + dirty marker ──
seg_git=""
if br=$(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null); then
  [ "$br" = "HEAD" ] && br="(detached)"
  dirty=""
  git -C "$cwd" diff --quiet --ignore-submodules 2>/dev/null || dirty="*"
  git -C "$cwd" diff --cached --quiet --ignore-submodules 2>/dev/null || dirty="*"
  col="$C_BRANCH"; [ -n "$dirty" ] && col="$C_DIRTY"
  seg_git="${col}${br}${dirty}${R}"
fi

# ── segment 2: vault (adjudant breadcrumb) — slug, plus a tidy-in-progress note ──
# Only kept-verb signals appear here: the linked vault's slug, and whether a
# `tidy` preview is waiting. Nothing is read from the vault itself, so it stays
# fast and portable.
seg_vault=""
bc="${proj}/.claude/adjudant"
if [ -f "$bc" ]; then
  slug=$(awk -F': ' '/^slug:/{gsub(/[ \t]+$/,"",$2); print $2; exit}' "$bc" 2>/dev/null)
  seg_vault="${C_VAULT}~ ${slug:-vault}${R}"
  [ -d "${proj}/.adjudant-tidy-preview" ] && seg_vault="${seg_vault}${C_MUTE} . tidying${R}"
fi

# ── segment 3: model + context-window fill ──
seg_model=""
[ -n "$model" ] && seg_model="${C_MODEL}${model}${R}"
if [ -n "$used" ]; then
  u=$(printf '%.0f' "$used" 2>/dev/null || echo 0)
  [ "$u" -lt 0 ] 2>/dev/null && u=0
  [ "$u" -gt 100 ] 2>/dev/null && u=100
  filled=$(( u * 8 / 100 )); bar=""
  i=0; while [ "$i" -lt "$filled" ]; do bar="${bar}▓"; i=$((i+1)); done
  while [ "$i" -lt 8 ]; do bar="${bar}░"; i=$((i+1)); done
  c="$C_OK"; [ "$u" -ge 60 ] && c="$C_WARN"; [ "$u" -ge 85 ] && c="$C_HOT"
  seg_model="${seg_model:+${seg_model}  }${c}${bar} ${u}%${R}"
fi

# ── render: join non-empty segments with a padded divider ──
out="$seg_git"
[ -n "$seg_vault" ] && out="${out:+${out}${SEP}}${seg_vault}"
[ -n "$seg_model" ] && out="${out:+${out}${SEP}}${seg_model}"
printf '%b\n' "$out"
