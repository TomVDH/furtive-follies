#!/usr/bin/env python3
"""PostToolUse hook for adjudant: commit-gated session logging.

SELF-GATED on Bash tool calls: exits 0 unless the command is a `git commit`
(leading `cd ... && ` stripped) whose payload reports success. Any `if`
filter added in hooks.json is defense in depth, never a dependency. Then:

  1. Append `- HH:MM · commit: {subject}` to today's session log.
  2. On `release(<plugin>): vX.Y.Z` subjects, scaffold
     `projects/{slug}/releases/v{version}.md` from templates/release.md
     (frontmatter + title + commit body), never overwriting an existing note.
  3. Upsert one `- [[v{version}|v{version} ({plugin})]]` row into
     `releases/_index.md`, created in tidy's canonical shape when absent.

Fail open on the hook itself, fail closed on a bad vault; the index row is
written only after the release note verifiably exists.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Shared primitives live in <plugin>/scripts/. Same bootstrap as the other
# python hooks: a broken or mid-sync module only degrades its own capability.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
except Exception:  # pragma: no cover - defensive
    pass

try:
    from _vault_walk import find_project_dir, is_safe_slug, resolve_vault
    _RESOLVER = True
except Exception:  # pragma: no cover - degrade: breadcrumb vault_path only
    _RESOLVER = False

    def resolve_vault(_project_root, _env_vault=None):  # type: ignore
        return None

    # Both guards must survive a failed import (stdlib-free: this block runs
    # when imports are already failing).
    def find_project_dir(vault, slug):  # type: ignore
        cands = [vault / "projects" / slug,
                 vault / "projects" / "_fridge" / slug,
                 vault / "projects" / "_archive" / slug]
        for c in cands:
            if (c / "brief.md").is_file():
                return c
        for c in cands:
            if c.is_dir():
                return c
        return None

    def is_safe_slug(slug):  # type: ignore
        if not isinstance(slug, str) or not slug or len(slug) > 64:
            return False
        return slug[0] != "-" and all(
            c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)


TEMPLATE = Path(__file__).resolve().parents[2] / "skills" / "adjudant" / "templates" / "release.md"

# Leading `cd ... && ` segments (repeatable); [^&] keeps each strip inside
# its own segment even for quoted paths with spaces.
_CD_PREFIX_RE = re.compile(r"^\s*(?:cd\s+[^&]*&&\s*)+")
# Accepts the -c/-C global-option forms too: `git -C /repo commit -m ...` is a
# real commit and used to be silently dropped.
_PATH_ARG = r"(?:\"([^\"]+)\"|'([^']+)'|(\S+))"
_COMMIT_RE = re.compile(
    r"^git\s+(?:-c\s+\S+\s+|-C\s+(?:\"[^\"]+\"|'[^']+'|\S+)\s+)*commit\b")
# Flags that make `git commit` NOT commit. Logging these forged real records:
# `git commit --dry-run -m "release(x): v9.9.9"` scaffolded releases/v9.9.9.md.
_NO_COMMIT_FLAG_RE = re.compile(r"(?:^|\s)--(?:dry-run|short|porcelain|long)(?:\s|=|$)")
_CD_CAPTURE_RE = re.compile(r"^\s*cd\s+" + _PATH_ARG + r"\s*&&")
_GIT_C_RE = re.compile(r"^git\s+(?:-c\s+\S+\s+)*-C\s+" + _PATH_ARG + r"\s")
_RELEASE_RE = re.compile(r"^release\(([a-z0-9-]+)\): v(\d+\.\d+\.\d+)")
# Claude Code's own commit style: -m "$(cat <<'EOF' ... EOF\n)"
_HEREDOC_MSG_RE = re.compile(
    r'-m\s+"?\$\(\s*cat\s+<<-?\s*[\'"]?([A-Za-z_][A-Za-z0-9_]*)[\'"]?\s*\n'
    r'(.*?)\n\s*\1\s*\n\s*\)',
    re.S,
)
_QUOTED_MSG_RE = re.compile(r"-m\s+(?:\"([^\"]*)\"|'([^']*)')")
_EXIT_KEYS = ("exit_code", "exitCode", "returncode", "return_code", "code")


def read_breadcrumb(project_dir: Path) -> dict:
    """Read `.claude/adjudant` breadcrumb (`key: value` per line, YAML-ish).

    Format written by connect.py, `:` separator. Old `=` format
    (pre-v0.4.0) also tolerated for transition.
    """
    breadcrumb = project_dir / ".claude" / "adjudant"
    if not breadcrumb.exists():
        return {}
    info = {}
    for line in breadcrumb.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = ":" if ":" in line else ("=" if "=" in line else None)
        if not sep:
            continue
        k, v = line.split(sep, 1)
        info[k.strip()] = v.strip()
    return info


def response_indicates_success(resp) -> bool:
    """True when the payload carries no failure signal.

    Payload shapes vary across harness versions, so the gate is: any explicit
    failure marker (interrupted, is_error, success false, non-zero exit)
    means no; a missing tool_response means unverifiable, also no (rule 2:
    never claim an effect that was not verified).
    """
    if resp is None:
        return False
    if not isinstance(resp, dict):
        return True  # string shapes carry no failure signal
    if resp.get("interrupted"):
        return False
    if resp.get("is_error") or resp.get("isError"):
        return False
    if resp.get("success") is False:
        return False
    for key in _EXIT_KEYS:
        v = resp.get(key)
        if v is not None:
            try:
                return int(v) == 0
            except (TypeError, ValueError):
                return False
    return True


_LOG_SUBJECT_MAX = 200


def log_safe(subject: str) -> str:
    """A commit subject rendered safe to append to a vault session note.

    Subjects are author-controlled text landing in a wikilink-bearing markdown
    file: an unescaped `[[projects/x/y]]` became a LIVE link that `check` then
    scored as a broken wikilink. Neutralize the brackets, flatten any newline
    or carriage return (a forged second log line), and cap the length.
    """
    flat = " ".join(str(subject).split())
    flat = flat.replace("[[", "[ [").replace("]]", "] ]")
    if len(flat) > _LOG_SUBJECT_MAX:
        flat = flat[:_LOG_SUBJECT_MAX - 1].rstrip() + "…"
    return flat


def _first_group(m) -> str:
    """First non-None capture of the quoted/bare path alternation."""
    return next((g for g in m.groups() if g), "") if m else ""


def repo_dir_for(command: str, cmd: str, fallback: str) -> str:
    """Which repo the commit targeted: `cd X &&` prefix, `git -C X`, else cwd."""
    from_cd = _first_group(_CD_CAPTURE_RE.match(command))
    if from_cd:
        return from_cd
    from_c = _first_group(_GIT_C_RE.match(cmd))
    if from_c:
        return from_c
    return fallback


def commit_verified(repo_dir: str, subject: str) -> bool:
    """True when HEAD's subject equals `subject` — i.e. the commit landed.

    The payload cannot be trusted: the Bash tool_response carries no exit code,
    so `nothing to commit, working tree clean` used to be logged as a real
    commit. Ask git instead. Fails closed on any error (no repo, no git, HEAD
    unborn, timeout) — rule 2: never claim an effect that was not verified.
    """
    if not repo_dir or not subject:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_dir), "log", "-1", "--format=%s"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=3, check=False)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    return r.stdout.strip() == subject.strip()


def _messages_from_tokens(tokens: list) -> list:
    """Collect message arguments: -m, bundled forms like -am, --message[=X]."""
    msgs = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--message" or re.fullmatch(r"-[a-zA-Z]*m", t):
            if i + 1 < len(tokens):
                msgs.append(tokens[i + 1])
                i += 2
                continue
        elif t.startswith("--message="):
            msgs.append(t[len("--message="):])
        i += 1
    return msgs


def parse_commit_message(command: str) -> str:
    """Extract the commit message from the command's first -m argument(s).

    Heredoc form first (the common Claude Code style), then shlex tokens,
    then a plain quoted-string fallback for commands shlex rejects.
    """
    m = _HEREDOC_MSG_RE.search(command)
    if m:
        return m.group(2)
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    if tokens:
        msgs = _messages_from_tokens(tokens)
        if msgs:
            # git joins multiple -m arguments as separate paragraphs
            return "\n\n".join(msgs)
    m = _QUOTED_MSG_RE.search(command)
    if m:
        return m.group(1) or m.group(2) or ""
    return ""


def split_subject_body(message: str) -> tuple:
    """First line is the subject; the rest, minus leading blanks, the body."""
    lines = message.strip("\n").split("\n")
    subject = lines[0].strip()
    rest = lines[1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    return subject, "\n".join(rest).rstrip()


def _release_frontmatter(slug: str, version: str, today: str) -> str:
    """Frontmatter from templates/release.md, placeholders filled. Falls back
    to an inlined equivalent when the template is unreadable or has grown a
    placeholder this hook does not know."""
    try:
        m = re.match(r"^---\n(.*?\n)---\n", TEMPLATE.read_text(), re.S)
        if m:
            fm = (m.group(1)
                  .replace("{slug}", slug)
                  .replace("{X.Y.Z}", version)
                  .replace("{YYYY-MM-DD}", today))
            if "{" not in fm:
                return f"---\n{fm}---\n"
    except OSError:
        pass
    return (
        "---\n"
        "type: release\n"
        f"version: {version}\n"
        f"date: {today}\n"
        "tags:\n"
        "  - release\n"
        "---\n"
    )


def _release_note(slug: str, plugin: str, version: str, body: str, today: str) -> str:
    text = _release_frontmatter(slug, version, today)
    text += f"\n# v{version} ({plugin})\n"
    if body:
        text += f"\n{body}\n"
    return text


def _upsert_index(releases: Path, slug: str, plugin: str, version: str, today: str) -> None:
    """One `- [[vX.Y.Z|vX.Y.Z (plugin)]]` row, deduped; new index files take
    tidy's canonical shape so the next tidy pass has nothing to churn."""
    index = releases / "_index.md"
    row = f"- [[v{version}|v{version} ({plugin})]]"
    try:
        if index.exists():
            text = index.read_text()
            if f"[[v{version}|" in text or f"[[v{version}]]" in text:
                return
            if not text.endswith("\n"):
                text += "\n"
            index.write_text(text + row + "\n")
        else:
            index.write_text(
                "---\n"
                "type: index\n"
                f"updated: {today}\n"
                "tags:\n"
                "  - index\n"
                "---\n\n"
                "# Releases\n\n"
                "## Entries\n\n"
                + row + "\n"
            )
    except OSError:
        pass  # index upsert is best-effort; the note itself already exists


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    # --- Self-gate, cheapest checks first: this fires on EVERY Bash call ---
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    cmd = _CD_PREFIX_RE.sub("", command).lstrip()
    if not _COMMIT_RE.match(cmd):
        return 0
    # --dry-run and friends print what WOULD happen and commit nothing.
    if _NO_COMMIT_FLAG_RE.search(cmd):
        return 0
    if not response_indicates_success(payload.get("tool_response")):
        return 0
    subject, body = split_subject_body(parse_commit_message(cmd))
    if not subject:
        return 0  # editor-driven or amend-no-edit commit: no subject to log
    # Authoritative gate: ask git whether HEAD is actually this commit. The
    # payload check above is only a cheap pre-filter.
    if not commit_verified(
            repo_dir_for(command, cmd, os.environ.get("CLAUDE_PROJECT_DIR", "")),
            subject):
        return 0

    # --- Vault resolution, same 5-step chain as the verbs and other hooks ---
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return 0
    info = read_breadcrumb(Path(project_dir))
    slug = info.get("slug", "")
    # Repo-committed breadcrumb: reject non-kebab slugs before any path build.
    if slug and not is_safe_slug(slug):
        return 0
    if not slug:
        return 0
    vault = resolve_vault(Path(project_dir))
    if vault is None and not _RESOLVER:
        # Degraded mode keeps the shell hooks' precedence: OB_VAULT first,
        # then a locally-valid vault_path (same-vault invariant).
        ob = os.environ.get("OB_VAULT", "")
        p = Path(ob).expanduser() if ob else None
        if p is None or not p.is_dir():
            vault_path = info.get("vault_path", "")
            p = Path(vault_path).expanduser() if vault_path else None
        vault = p if (p is not None and p.is_dir()) else None
    if vault is None or not vault.is_dir():
        return 0  # stale breadcrumb: fail closed, never log to a phantom path
    # Zone-aware: shelf moves projects to _fridge/ and _archive/ without
    # touching the breadcrumb.
    project_root = find_project_dir(vault, slug)
    if project_root is None:
        return 0  # project exists in no zone: never materialize it
    if not project_root.is_dir():
        return 0  # stale slug: never materialize a phantom project chain

    now = datetime.now()  # single clock read: date and time can't straddle midnight
    today = now.strftime("%Y-%m-%d")
    ts = now.strftime("%H:%M")

    # --- Job 1: append the commit line (today's note, or the latest one
    # when the session straddles midnight, same discipline as vault-log) ---
    session_file = project_root / "sessions" / f"{today}.md"
    if not session_file.exists():
        try:
            # digit classes, not ?: a stray abcd-ef-gh.md must never win
            candidates = sorted((project_root / "sessions").glob(
                "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"))
        except OSError:
            candidates = []
        # Real dates not after today only (finding 19): a future-dated note
        # must never absorb appends; the digit glob admits impossible dates.
        for cand in reversed(candidates):
            try:
                datetime.strptime(cand.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if cand.stem <= today:
                session_file = cand
                break
    if session_file.exists():
        try:
            with session_file.open("a") as f:
                f.write(f"- {ts} · commit: {log_safe(subject)}\n")
        except OSError:
            pass  # log-write failure must not block the release scaffold

    # --- Jobs 2+3: release stub + index row, release subjects only ---
    rel = _RELEASE_RE.match(subject)
    if not rel:
        return 0
    plugin, version = rel.group(1), rel.group(2)
    releases = project_root / "releases"
    try:
        releases.mkdir(exist_ok=True)
    except OSError:
        return 0
    note = releases / f"v{version}.md"
    if not note.exists():
        try:
            note.write_text(_release_note(slug, plugin, version, body, today))
        except OSError:
            return 0  # never index a note that failed to write
    _upsert_index(releases, slug, plugin, version, today)
    return 0


if __name__ == "__main__":
    # A PostToolUse hook must never surface as a tool failure: whatever goes
    # wrong (future logic error, exotic I/O failure), exit 0.
    try:
        sys.exit(main())
    except Exception:  # pragma: no cover - last-resort guard
        sys.exit(0)
