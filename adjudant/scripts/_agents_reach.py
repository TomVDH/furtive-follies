#!/usr/bin/env python3
"""Adjudant's one reach outside the vault: is AGENTS.md still true?

AGENTS.md is canonical and harness-agnostic, CLAUDE.md imports it, GEMINI.md
does the same for agy, and the vault contains none of them. It is the first
thing every agent reads, and nothing keeps it current.

One project's AGENTS.md carries five false statements: traps about a module
deleted on 2026-08-23, and a rule described as "enforced mechanically" by a
script that does not exist. Three of the five are detectable without adjudant
knowing anything about the project, because they name things that are not
there. This repo's own AGENTS.md is not exempt from the same drift — it is
read exactly as written here, on every run, so a stale claim about this repo
would show up the same way a stale claim about any other project does.

Two checks, both read-only. No frontmatter is added and nothing is rewritten:
the file belongs to the person who wrote it, and a context file adjudant
edits is a context file nobody trusts.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

# A context file unchanged across this many commits has stopped describing the
# code it sits beside. Reported, never enforced.
AGENTS_STALE_COMMITS = 30

# Extensions that make a token file-shaped. Wide on purpose: the parent-exists
# rule below carries the precision, so a narrow list here only created blind
# spots. The old fifteen entries could not see Go, Rust, Ruby, Java, C, JSX,
# SCSS, SQL, CSV, SVG, PDF or XML, which is most of most repositories.
_PATH_EXTS = (
    ".py", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat",
    ".md", ".markdown", ".rst", ".txt", ".adoc",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".lock", ".xml", ".csv", ".tsv", ".sql", ".graphql", ".proto",
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".go", ".rs", ".rb", ".java", ".kt", ".swift", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".cs", ".php", ".pl", ".lua", ".r", ".scala", ".ex",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ico",
)

# Extensionless filenames a repository really does carry.
_BARE_FILENAMES = {
    "Makefile", "Dockerfile", "Justfile", "Rakefile", "Gemfile", "Procfile",
    "LICENSE", "NOTICE", "CODEOWNERS", "Brewfile", "Vagrantfile",
}

# Characters that mark a token as a pattern, a variable or a placeholder
# rather than a path on this disk. The ellipsis is here because a document
# abbreviates a long path with it: `~/…/Projects/IDE/` is prose, not a claim.
_NOT_A_PATH = set("<>{}*?|$\"'()[]…")

# Never walked when git cannot list the tree.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_FENCE_RE = re.compile(r"^\s*```")


def _looks_like_a_path(token: str) -> bool:
    """True when a token is shaped like a file or directory name.

    Shape alone can never decide whether a token is a path CLAIM. `Three.js`
    and `Vue.js` are libraries; `npm test -- auth.spec.ts` is a command;
    `skill-name/SKILL.md` appears in a document that says the file must NOT be
    there. Measured on 35 real AGENTS.md files, deciding on shape alone
    reported 304 findings of which at least a third named things that exist.

    So this function only asks "could this be a filename", and `agents_reach`
    decides whether it is a claim, by requiring the parent directory to exist.
    """
    t = token.strip().rstrip(".,;:")
    if not t or t.startswith("-"):
        return False
    # Test the URL scheme BEFORE stripping slashes: rstrip("/") turned
    # `file://` into `file:` and let it through as a path.
    if "://" in t:
        return False
    # A command is not a path. `go test -- x/y.sh` and `bash x/y.sh` both end
    # in something path-shaped and can never resolve. But a real path may
    # contain spaces -- this very repository lives under "ZenaTech CC Space" --
    # so a space is allowed only behind an explicit path prefix. A command
    # begins with its program name; a path with spaces is written absolute.
    if any(c.isspace() for c in t) and not t.startswith(("/", "~/", "./", "../")):
        return False
    t = t.rstrip("/")
    if not t or t in (".", ".."):
        return False
    if any(c in _NOT_A_PATH for c in t):
        return False
    if t.startswith(("./", "../", "~/")):
        return True
    # A leading slash is usually a ROUTE, not a claim about this disk.
    # `/hero.html` and `/adjudant` were both checked at the filesystem root,
    # which is how one static site scored 74% false. A real absolute path is
    # told apart by asking the disk: its first component is a directory at /.
    if t.startswith("/"):
        first = t.split("/", 2)[1] if len(t.split("/", 2)) > 1 else ""
        try:
            if not first or not Path("/" + first).is_dir():
                return False
        except OSError:
            return False
        return True
    last = t.split("/")[-1]
    if not last or last.startswith("."):
        # A dotfile is a real name (.env, .gitignore) but only with a parent
        # or an explicit prefix, both handled above.
        return "/" in t
    return last.endswith(_PATH_EXTS) or last in _BARE_FILENAMES or token.strip().endswith("/")


# Beyond this many tracked files the index is not worth building. No repo that
# keeps an AGENTS.md a person maintains by hand comes near it.
_INDEX_FILE_CAP = 50_000

# A copy of a path is not the path. Vendored trees, fixtures and installed
# packages all preserve the tail of a real path, which is exactly why they
# satisfied a claim about a file that was not there.
_NOT_A_SOURCE_OF_TRUTH = {
    "node_modules", "vendor", "third_party", "thirdparty", "fixtures",
    "__pycache__", ".venv", "venv", "site-packages", "dist", "build",
    ".git", ".tox", ".mypy_cache", ".pytest_cache",
}


def _toplevel(code_root: Path) -> Path:
    """The git root, or code_root outside a repository.

    `git ls-files` reports paths relative to the DIRECTORY IT RUNS IN, not the
    repository root. Run from a subdirectory it strips the prefix a reader
    naturally writes, so `acme-crm/preview` failed to resolve while the
    directory sat right there. Eight worktrees, 100% wrong.
    """
    out = _git(code_root, "rev-parse", "--show-toplevel")
    if not out:
        return code_root
    try:
        top = Path(out)
        return top if top.is_dir() else code_root
    except (OSError, ValueError):
        return code_root


def _repo_index(root: Path) -> Optional[dict]:
    """Every real path under `root`, grouped by last component.

    Returns None when no index could be built. None means SILENCE, not a
    fallback: resolving against the root alone was measured at 61% wrong, so
    reverting to it when the index is unavailable would quietly restore the
    defect this replaced.
    """
    listing = _git(root, "ls-files", "-z")
    rels = []
    if listing is not None:
        rels = [r for r in listing.split("\0") if r]
    else:
        try:
            for f in root.rglob("*"):
                if any(part in _NOT_A_SOURCE_OF_TRUTH for part in f.parts):
                    continue
                if not f.is_file():
                    continue
                rels.append(f.relative_to(root).as_posix())
                if len(rels) > _INDEX_FILE_CAP:
                    return None
        except OSError:
            return None
    if len(rels) > _INDEX_FILE_CAP:
        return None
    index: dict = {}
    for rel in rels:
        parts = tuple(rel.split("/"))
        if any(seg in _NOT_A_SOURCE_OF_TRUTH for seg in parts):
            continue
        for depth in range(1, len(parts) + 1):
            head = parts[:depth]
            index.setdefault(head[-1], set()).add(head)
    return index


def _resolves(token: str, root: Path, index: dict) -> bool:
    """True when some real path under `root` ends with this token.

    Every candidate is confirmed against the FILESYSTEM. `git ls-files` reads
    the index, so a file deleted but not yet staged still appeared there, and
    the suffix match then overrode a correct filesystem answer.
    """
    parts = tuple(p for p in token.split("/") if p and p != ".")
    if not parts:
        return False
    for cand in index.get(parts[-1], ()):
        if len(cand) >= len(parts) and cand[-len(parts):] == parts:
            if (root / Path(*cand)).exists():
                return True
    return False


def _parent_exists(token: str, roots: tuple) -> bool:
    """True when the directory this token claims to sit in is really there.

    This is what makes a token a CLAIM rather than a word. `Three.js` names no
    directory. `push/PR` names `push/`, which does not exist. A document
    saying a file must NOT be at `skill-name/SKILL.md` names no such folder.
    But `scripts/enforce-tags.sh`, in a repo that has `scripts/`, is a claim
    about a real place, and that is the finding worth making.

    The cost is stated plainly: a claim whose whole parent tree is gone is not
    reported. Reporting it would mean reporting every prose token too.
    """
    head = token.rsplit("/", 1)[0] if "/" in token else ""
    if not head:
        return False
    for root in roots:
        try:
            if (root / head).is_dir():
                return True
        except OSError:
            continue
    return False


def _clean(token: str) -> str:
    return token.strip().rstrip(".,;:").rstrip("/")


def named_paths(text: str) -> list:
    """Every path-shaped token the text names, as `(line_number, token)`.

    Three sources, and only three, so prose is never mined for filenames:
      - an inline backtick span, taken whole so a path with spaces survives
      - a markdown link target
      - a line inside a fenced block, split on whitespace

    Duplicates on one line are reported once; the same token on two lines is
    reported twice, because both lines make the claim.
    """
    out: list = []
    in_fence = False
    for lineno, line in enumerate(text.split("\n"), start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        seen_on_line: set = set()
        candidates: list = []
        if in_fence:
            candidates.extend(line.split())
        else:
            candidates.extend(_BACKTICK_RE.findall(line))
            candidates.extend(_MD_LINK_RE.findall(line))
        for raw in candidates:
            if not _looks_like_a_path(raw):
                continue
            token = _clean(raw)
            if token in seen_on_line:
                continue
            seen_on_line.add(token)
            out.append((lineno, token))
    return out


def _git(code_root: Path, *args: str) -> Optional[str]:
    """One git call, or None. Never raises, never blocks longer than 5s."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(code_root), *args],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def agents_reach(code_root: Path) -> dict:
    """Every path AGENTS.md names, checked, plus commits since it changed.

    `missing` holds the tokens that resolve to nothing. `commits_since_change`
    is None outside a git repository, which is a fact about the environment
    and not a finding.
    """
    agents = code_root / "AGENTS.md"
    try:
        text = agents.read_text(errors="replace")
    except OSError:
        return {"present": False, "missing": [], "checked": 0,
                "last_changed": None, "commits_since_change": None}

    missing: list = []
    tokens = named_paths(text)
    top = _toplevel(code_root)
    roots = (code_root,) if top == code_root else (code_root, top)
    index = _repo_index(top)
    for lineno, token in tokens:
        if token.startswith("~") or token.startswith("/"):
            # A home or absolute path names one place and only that place, so
            # there is nothing to resolve and nothing to infer. The shape test
            # has already rejected site routes, which is what made checking a
            # leading slash unsafe before.
            if not Path(token).expanduser().exists():
                missing.append({"line": lineno, "token": token})
            continue
        if index is None:
            continue          # silence beats guessing; see _repo_index
        if any((r / token).exists() for r in roots):
            continue
        if _resolves(token, top, index):
            continue
        if not _parent_exists(token, roots):
            continue          # names no location, so makes no checkable claim
        missing.append({"line": lineno, "token": token})

    last_sha = _git(code_root, "log", "-1", "--format=%H", "--", "AGENTS.md")
    last_changed = _git(code_root, "log", "-1", "--format=%cs", "--", "AGENTS.md")
    commits_since: Optional[int] = None
    if last_sha:
        counted = _git(code_root, "rev-list", "--count", f"{last_sha}..HEAD")
        if counted is not None:
            try:
                commits_since = int(counted)
            except ValueError:
                commits_since = None

    return {
        "present": True,
        "missing": missing,
        "checked": len(tokens),
        "last_changed": last_changed,
        "commits_since_change": commits_since,
    }
