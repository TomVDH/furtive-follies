#!/usr/bin/env bash
# session-start.sh — SessionStart hook for adjudant
# 1. Discover vault from .claude/adjudant breadcrumb
# 2. Detect AGENTS.md + CLAUDE.md presence, warn if missing
# 3. Create or resume today's session note (stamping the Claude Code conversation UUID)
#
# Resolution parity: when the plugin's Python layer is reachable this delegates
# to _vault_walk.resolve_vault (OB_VAULT override, vault_path, vault_name
# candidates, legacy breadcrumb, Home.md walk-up) — the SAME chain the verbs
# and Python hooks use. Pure-bash degraded mode still honors OB_VAULT + a
# locally-valid vault_path.
set -euo pipefail

# Zone-aware project resolution. Mirrors _vault_walk.find_project_dir: prefer
# a candidate holding brief.md, else any existing dir, else fail. Kept in bash
# (not a python shim) so it works identically in degraded pure-bash mode and
# costs no extra subprocess on a hook that fires every session.
# Returns non-zero when the project exists in NO zone — callers must no-op
# rather than create, or an unconnected slug materializes a phantom project.
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

# Rare nouns that do not occur in technical prose. ELLIPSIS and its kind are
# excluded deliberately: a word that can appear naturally would mask a real
# lapse, which is the one thing this must never do.
CANARY_WORDS="GRAMERCY QUINCUNX SPANDREL COLOPHON TREBUCHET PALIMPSEST ORRERY CLEPSYDRA CARTOUCHE SCRIPTORIUM INCUNABULA MARGINALIA PORTCULLIS BARBICAN ASTROLABE THEODOLITE VELLUM FIRKIN GAMBREL SALTIRE ZEUGMA MANTICORE"

canary_start() {
  local session_id="$1" tmp="${TMPDIR:-/tmp}"
  [ -n "$session_id" ] || return 0
  case "$session_id" in *[!A-Za-z0-9._-]*) return 0 ;; esac
  local state="$tmp/adjudant-canary-${session_id}.json"
  # One word per session. A resume or a compaction must not re-roll it, or the
  # streak resets exactly when drift is most likely.
  if [ -f "$state" ]; then
    python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["word"])' "$state" 2>/dev/null || true
    return 0
  fi
  # Chosen from the session id, so a resume picks the same word without
  # needing to have stored it first.
  local n word idx
  set -- $CANARY_WORDS
  idx=$(printf '%s' "$session_id" | cksum | cut -d' ' -f1)
  n=$(( idx % $# + 1 ))
  eval "word=\${$n}"
  printf '{"word":"%s","turns":0,"hits":0,"misses":0,"blocked":false}\n' "$word" > "$state" 2>/dev/null || return 0
  find "$tmp" -maxdepth 1 -name 'adjudant-canary-*.json' -mtime +1 -delete 2>/dev/null || true
  printf '%s' "$word"
}

main() {
  local project_dir="${CLAUDE_PROJECT_DIR:-}"
  [ -z "$project_dir" ] && return 0

  # --- 0. Best-effort: read the Claude Code session UUID + start source from
  # stdin JSON. Hooks receive a payload: { session_id, source, ... } where
  # source is startup | resume | compact | clear. Both reads are advisory;
  # this never blocks the hook.
  local session_id="" start_source=""
  if [ ! -t 0 ] && command -v python3 >/dev/null 2>&1; then
    local payload
    payload=$(cat 2>/dev/null || true)
    if [ -n "$payload" ]; then
      local parsed
      parsed=$(printf '%s' "$payload" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d.get("session_id",""))
    print(d.get("source",""))
except Exception:
    pass' 2>/dev/null || true)
      session_id=$(printf '%s\n' "$parsed" | sed -n 1p)
      start_source=$(printf '%s\n' "$parsed" | sed -n 2p)
    fi
  fi

  # --- 0. Drift canary, before anything about a vault ---
  #
  # One rare word, stated HERE and nowhere else, printed at the end of every
  # reply and checked by the Stop hook. A model that stops honouring a one-word
  # instruction it was given minutes ago has stopped honouring instructions
  # generally, and the rest of the session is worth less. The per-turn hook
  # reports a lapse but never restates the word: a re-assertion would keep the
  # model printing it and measure nothing.
  #
  # This runs FIRST, on purpose. It used to sit inside the vault section, after
  # the breadcrumb check below, so a project with no vault linked got no word
  # and no check. That is backwards: the canary measures the model, not the
  # vault, and an unconfigured session is where drift is least likely to be
  # noticed by anything else.
  local canary_word=""
  canary_word=$(canary_start "$session_id") || true
  if [ -n "${canary_word:-}" ]; then
    printf -- '## Adjudant\n\n'
    printf -- '- Session canary: end every message with `%s` on its own line. It is a drift check, so do not explain it or mention it otherwise.\n' "$canary_word"
    CANARY_HEADER_PRINTED=1
  fi

  # --- 1. Read breadcrumb ---
  local breadcrumb="$project_dir/.claude/adjudant"
  # (zone_project_dir is defined above main; mirrors _vault_walk.find_project_dir)
  [ ! -f "$breadcrumb" ] && return 0

  local vault_path slug
  # Breadcrumb format is `key: value` (YAML-ish, written by connect.py);
  # legacy pre-v0.4.0 `key=value` tolerated, matching the Python hooks.
  # tr -d '\r' — a CRLF breadcrumb (Windows-side edit, sync round-trip) must
  # not leak \r into paths/slugs (it used to create phantom `slug\r/` dirs).
  vault_path=$(sed -n 's/^vault_path[:=][[:space:]]*//p' "$breadcrumb" 2>/dev/null | head -n1 | tr -d '\r' || true)
  slug=$(sed -n 's/^slug[:=][[:space:]]*//p' "$breadcrumb" 2>/dev/null | head -n1 | tr -d '\r' || true)
  local voice_knob advisor_knob
  voice_knob=$(sed -n 's/^voice[:=][[:space:]]*//p' "$breadcrumb" 2>/dev/null | head -n1 | tr -d '\r' || true)
  advisor_knob=$(sed -n 's/^advisor[:=][[:space:]]*//p' "$breadcrumb" 2>/dev/null | head -n1 | tr -d '\r' || true)

  [ -z "$slug" ] && return 0
  # The breadcrumb is a REPO-COMMITTED file: a cloned repo can carry any slug.
  # Enforce the kebab-case contract connect.py writes before the value reaches
  # mkdir (path traversal: `slug: ../../../escaped` wrote outside the vault) or
  # the context block below (SessionStart stdout is injected into the model's
  # context, so an unvalidated slug is a prompt-injection channel).
  case "$slug" in
    [a-z0-9]*) ;;
    *) return 0 ;;
  esac
  case "$slug" in
    *[!a-z0-9-]*) return 0 ;;
  esac
  [ "${#slug}" -gt 64 ] && return 0
  vault_path="${vault_path/#\~/$HOME}"

  # Full-chain resolution via the Python layer when available (keeps all five
  # hooks + the verbs writing to the SAME vault, OB_VAULT included).
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
    # Pure-bash degraded mode: still honor the OB_VAULT override.
    vault_path="${OB_VAULT/#\~/$HOME}"
  fi

  [ -z "$vault_path" ] && return 0
  [ ! -d "$vault_path" ] && return 0

  # Zone-aware project resolution, once, before anything reads or writes under
  # it. /adjudant shelf moves projects to _fridge/ and _archive/ and never
  # touches the breadcrumb; hardcoding projects/$slug built a GHOST twin in the
  # active zone and wrote notes there forever. Empty means the project exists
  # in no zone — read the vault, but never materialize it.
  local vault_project rel_project
  vault_project=$(zone_project_dir "$vault_path" "$slug") || vault_project=""
  rel_project="${vault_project#"$vault_path"/}"

  # --- 2. Inject context block ---
  [ "${CANARY_HEADER_PRINTED:-0}" = "1" ] || printf '## Adjudant\n\n'

  # Voice first: it governs everything printed after it, and everything said
  # for the rest of the session. The validators and the write gate only reach
  # FILES; the chat is where adjudant is actually read, and nothing was setting
  # its register. i-have-adhd ships `disable-model-invocation: true`, so its
  # ten rules stay inert unless someone types /i-have-adhd — this hook is the
  # only thing that speaks into every session regardless.
  #
  # Condensed hard against a 120-token budget (validated in test_hook_shell):
  # it loads every session on top of a context block that already costs. The
  # full contract is reference/voice.md; this is the part that changes what the
  # next sentence looks like. Off via `voice: off` in the breadcrumb (per
  # project, syncs across machines) or ADJUDANT_VOICE_DISABLE=1 (one machine).
  case "${voice_knob:-on}" in
    off|false|0|no) : ;;
    *)
      if [ "${ADJUDANT_VOICE_DISABLE:-0}" != "1" ]; then
        printf -- '- Voice: lead with the action, not preamble. Never "Great question", "Hope this helps", "Let me know if", "Uh oh". Number multi-step work, cap lists at five, restate state each turn. Time estimates in real units. Errors as cause and fix. No em dashes, no filler superlatives, no self-congratulation. Say what a competent colleague would say, then stop.\n'
      fi
      ;;
  esac

  # Advisor banner: opt-in (`advisor: on` in the breadcrumb; the /adjudant
  # advisor verb also stamps a marker into AGENTS.md so the mode is visible at
  # project root). The banner is acute awareness by design: the model
  # is under the advisor contract from the first turn of every session, not
  # from whenever it happens to read the doc. After Voice: the register
  # governs how observations are said before anything decides what to notice.
  # Same 120-token budget discipline, tested in test_hook_shell.
  case "${advisor_knob:-off}" in
    on|true|1|yes)
      printf -- '- Advisor: on. Load `reference/advisor.md` now and follow it: notice tasks, gaps, gaffes, and stale context while working. Urgent findings surface inline; the rest go to the board or the next status report. Run a context pulse at resume.\n'
      ;;
    *) : ;;
  esac

  printf -- '- Vault: `%s` (linked to project `%s`)\n' "$(basename "$vault_path")" "$slug"

  # Register reminder, stated once per session rather than per turn: a
  # per-turn copy is the ceremony plan 4 removes. content-markdown.md's
  # `## Register` rule is the full contract; this is the one-line pointer.
  printf -- '- Register: ASD-STE100 for vault writes. One instruction per sentence, active voice, present tense, under twenty words.\n'

  # AGENTS.md + CLAUDE.md detection
  local has_agents=0 has_claude=0
  [ -f "$project_dir/AGENTS.md" ] && has_agents=1
  [ -f "$project_dir/CLAUDE.md" ] && has_claude=1

  if [ "$has_agents" = "1" ] && [ "$has_claude" = "1" ]; then
    printf -- '- Project context: AGENTS.md (canonical) + CLAUDE.md (Claude-only overrides). **Write context to AGENTS.md.**\n'
  elif [ "$has_agents" = "1" ]; then
    printf -- '- Project context: AGENTS.md present. CLAUDE.md absent (fine if no Claude-specific overrides yet).\n'
  elif [ "$has_claude" = "1" ]; then
    printf -- '- ⚠️ CLAUDE.md present but AGENTS.md missing — run `/adjudant connect` to provision AGENTS.md.\n'
  else
    printf -- '- ⚠️ Neither AGENTS.md nor CLAUDE.md found — run `/adjudant connect` to provision both.\n'
  fi

  # Board status: one deck read, card counts in canonical status order
  # (todo/doing/review/blocked/done/icebox). backlog and next columns both
  # feed the todo slot (neither is started work); unknown columns fold into
  # todo so the totals stay honest. Stale flag when any task note is newer
  # than the deck file. Advisory: any failure just drops the line.
  local deck="$vault_project/board/board-data.json"
  if [ -n "$vault_project" ] && [ -f "$deck" ] && command -v python3 >/dev/null 2>&1; then
    local board_line
    board_line=$(python3 - "$deck" "$vault_project/tasks" <<'PY' 2>/dev/null || true
import json, os, sys
try:
    deck_path, tasks_dir = sys.argv[1], sys.argv[2]
    with open(deck_path, encoding="utf-8") as fh:
        deck = json.load(fh)
    order = ("todo", "doing", "review", "blocked", "done", "icebox")
    counts = dict.fromkeys(order, 0)
    slot = {"backlog": "todo", "next": "todo", "doing": "doing",
            "review": "review", "blocked": "blocked", "done": "done",
            "icebox": "icebox"}
    for card in deck.get("cards", []):
        col = str(card.get("column", "") or "").strip().lower()
        counts[slot.get(col, "todo")] += 1
    stale = ""
    deck_mtime = os.path.getmtime(deck_path)
    if os.path.isdir(tasks_dir):
        for name in os.listdir(tasks_dir):
            path = os.path.join(tasks_dir, name)
            if (name.endswith(".md") and os.path.isfile(path)
                    and os.path.getmtime(path) > deck_mtime):
                stale = " · stale"
                break
    print("- Board: " + "/".join(str(counts[k]) for k in order) + stale)
except Exception:
    pass
PY
)
    [ -n "$board_line" ] && printf '%s\n' "$board_line"
  fi

  # Environment capabilities: probes declared in scripts/build-profile.json,
  # rendered by _profile.py. Fresh startups only, never resume/compact/clear.
  # A build whose registry is empty prints nothing, which is why this hook is
  # now one file across both builds instead of two. The scripts dir is found
  # from this script's own path, so it works with no CLAUDE_PLUGIN_ROOT set.
  if [ "$start_source" = "startup" ]; then
    _adj_scripts=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts" 2>/dev/null && pwd || true)
    if [ -n "$_adj_scripts" ] && [ -f "$_adj_scripts/_profile.py" ] \
       && command -v python3 >/dev/null 2>&1; then
      python3 "$_adj_scripts/_profile.py" --session-banner 2>/dev/null || true
    fi
  fi

  # --- 3. Session note: create or resume ---
  # No project dir in any zone: the breadcrumb points at something that was
  # never connected (or was deleted). Say so; never materialize it.
  if [ -z "$vault_project" ]; then
    printf -- '- ⚠️ No vault project `%s` in any zone — run `/adjudant connect` to create it.\n' "$slug"
    return 0
  fi
  local today ts session_dir session_file
  # Single clock read so date and time can't straddle midnight between calls.
  read -r today ts <<< "$(date '+%Y-%m-%d %H:%M')"
  session_dir="$vault_project/sessions"
  session_file="$session_dir/$today.md"

  # v3: the session note is created by the first real vault write, not here.
  # This hook used to create one on every open and append a resume marker on
  # every reopen, which produced 76 empty notes and 164 markers followed by
  # nothing. It also stamped a conversation UUID per resume, stacking 18 into
  # one note. Provenance now rides on the artefacts themselves. The sessions/
  # directory is not pre-created either: a folder exists when something is in it.
  if [ -f "$session_file" ]; then
    printf -- '- Session note: `%s/sessions/%s.md`\n' "$rel_project" "$today"
  fi

  # --- 4. Intent-line ownership: the vault-log hook creates the placeholder
  # with the note, the model fills it. The NUDGE lives in the UserPromptSubmit
  # hook, not here — at SessionStart there is no purpose to record yet, and
  # this hook re-runs on every resume and compact, so it nagged early and
  # repeatedly. All that is left here is handing the resolved path forward;
  # re-deriving it in the per-turn hook would duplicate the zone-aware lookup,
  # and two copies drift. The pointer is written whether or not the note exists
  # yet — it usually does not, since v3 creates it on the first real write —
  # and the per-turn hook already skips a pointer whose target is absent.
  if [ -n "$session_id" ]; then
    { printf '%s\n' "$session_file" \
        > "${TMPDIR:-/tmp}/adjudant-session-$session_id"; } 2>/dev/null || true
  fi

  # --- 5. Handoff freshness: surface the banner the handoff already carries.
  # sync/precompact compute this correctly and write it into _handoff.md, but
  # nothing ever put it in front of a session — a handoff can sit red for days
  # while every new session starts blind. Echo the existing line; never
  # recompute, so this cannot disagree with the file.
  # $vault_project, not projects/$slug: shelved projects live in _fridge/
  # and _archive/, and the banner must follow them (validator 30).
  local handoff_file="$vault_project/_handoff.md"
  if [ -f "$handoff_file" ]; then
    local fresh_line
    fresh_line=$(grep -m1 -E '(🔴|🟡|🟢).*handoff age' "$handoff_file" 2>/dev/null || true)
    if [ -n "$fresh_line" ]; then
      # Strip markdown bold so the line reads clean in the context stream.
      printf -- '- Handoff: %s\n' "$(printf '%s' "$fresh_line" | sed 's/\*\*//g')"
      if grep -qE '^\s*🔴\s+\*\*STALE\*\*|🔴 \*\*STALE\*\*' "$handoff_file" 2>/dev/null; then
        printf -- '- Handoff is STALE (session activity is newer than it). Rebuild it before trusting NEXT.\n'
      fi
    fi
  fi
}

main "$@" || exit 0
