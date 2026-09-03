#!/usr/bin/env python3
"""Adjudant validators — fail-the-build drift defense.

Run from the plugin root (adjudant/). Exit 0 on pass, 1 on any failure.

Validators:
   1. harness-parity         — source/, .claude/, .gemini/ skill paths all resolve to skills/adjudant
   2. claude-md-imports-agents — templates/CLAUDE.md starts with @AGENTS.md
   3. template-schema-loads  — the templates parse into exactly the fifteen kinds, each vocabulary non-empty
   4. command-metadata-coherence — verbs in command-metadata.json match SKILL.md router
   5. plugin-version-set     — .claude-plugin/plugin.json has a non-empty version
   6. version-consistency     — plugin.json / command-metadata.json / SKILL.md (+ marketplace when present) versions all match
   7. reference-files-exist   — every reference/*.md named in command-metadata.json and the SKILL.md router exists
   8. verb-surfaces-generated — the ten verb-derived doc surfaces are rendered from command-metadata.json, not typed twice
   9. reference-doc-links     — every relative markdown link inside reference/*.md resolves on disk
  10. verb-description-length — command-metadata verb descriptions stay router-line short (≤ 220 chars)
  11. repo-helper-parity      — repo_walk/repo_scan/repo_tidy each exist with a matching test_*.py
  12. repo-standards-coverage — reference/repo-standards.md exists and names each detector category
  13. repo-tidy-preview-coherence — if repo-tidy preview dir exists, it has summary.md + changes.json + files/
  14. repo-tidy-backup-integrity   — repo-tidy backup subdirs with files carry at least one .legacy
  15. gitignore-includes-repo-tidy-dirs — .gitignore lists the repo-tidy dirs if either exists
  16. voice-lexicon                : no banned/glazing/shape terms in templates/, SKILL.md, reference/ (voice.md excepted); no em dashes in templates/
  17. board-template-markers       : templates/board.html exists, both BOARD_DATA markers present, seeded JSON parses and has columns, nothing fetched off-machine, no empty catch
  18. hooks-wiring                 : every hooks.json command resolves to an existing executable file under hooks/scripts/
  19. hook-zone-awareness          : no hook hardcodes projects/<slug>; each resolves zone-aware and gates the slug first
  20. base-dashboards              : shipped .base dashboard templates are structurally sound and schema-legal
  21. voice-patterns              : no named no-ai-slop sentence patterns in templates/, SKILL.md, reference/
  22. render-voice                : no banned lexicon or slop pattern in any string literal the helpers can print
  23. advisor-wiring              : the advisor's contract doc, SessionStart banner, and AGENTS.md marker stay wired
  24. place-zone-parity           : _place's lifecycle folder set matches _vault_walk.PROJECT_ZONES
  25. standards-structure-parity  : reference/vault-standards.md names every folder in KIND_FOLDER and PROJECT_ZONES

25 validators total.
"""

import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _profile  # noqa: E402
import _voice  # noqa: E402
import render_verb_surfaces  # noqa: E402
from _vault_walk import FIELD_SCHEMA, PROJECT_ZONES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "skills" / "adjudant"
TEMPLATES = CANONICAL / "templates"
REFERENCE = CANONICAL / "reference"
SCRIPTS = ROOT / "scripts"
HARNESS_DIRS = [
    ROOT / "source" / "skills" / "adjudant",
    ROOT / ".claude" / "skills" / "adjudant",
    ROOT / ".gemini" / "skills" / "adjudant",
]


class Result:
    def __init__(self):
        self.failures: list[str] = []
        self.passes: list[str] = []

    def add_pass(self, name: str) -> None:
        self.passes.append(name)

    def add_fail(self, name: str, detail: str) -> None:
        self.failures.append(f"{name}: {detail}")

    def report(self) -> int:
        for name in self.passes:
            print(f"  ✓ {name}")
        for failure in self.failures:
            print(f"  ✗ {failure}")
        if self.failures:
            print(f"\nFAIL — {len(self.failures)} validator(s) failed")
            return 1
        print(f"\nPASS — {len(self.passes)} validator(s) green")
        return 0


def validate_harness_parity(r: Result) -> None:
    name = "harness-parity"
    if not CANONICAL.is_dir() or CANONICAL.is_symlink():
        r.add_fail(name, f"skills/adjudant must be a real directory (the canonical skill location)")
        return
    for h in HARNESS_DIRS:
        if not h.is_symlink():
            r.add_fail(name, f"{h.relative_to(ROOT)} is not a symlink")
            return
        try:
            resolved = h.resolve()
            if resolved != CANONICAL.resolve():
                r.add_fail(
                    name,
                    f"{h.relative_to(ROOT)} resolves to {resolved}, expected {CANONICAL.resolve()}",
                )
                return
        except OSError as e:
            r.add_fail(name, f"{h.relative_to(ROOT)}: {e}")
            return
    r.add_pass(name)


def validate_claude_md_imports_agents(r: Result) -> None:
    name = "claude-md-imports-agents"
    f = TEMPLATES / "CLAUDE.md"
    if not f.exists():
        r.add_fail(name, "templates/CLAUDE.md missing")
        return
    lines = f.read_text().splitlines()
    first_nonempty = next((ln.strip() for ln in lines if ln.strip()), "")
    if first_nonempty != "@AGENTS.md":
        r.add_fail(name, f"first non-empty line is {first_nonempty!r}, expected '@AGENTS.md'")
        return
    r.add_pass(name)


def validate_template_schema_loads(r: Result) -> None:
    """4. template-schema-loads — the templates parse into exactly the fifteen
    kinds, and every declared vocabulary is non-empty.

    This is the only validator the schema needs now. The six it replaces all
    checked that two declarations agreed; with one declaration the question
    cannot be asked, and the only remaining risk is a template that does not
    parse or a kind that quietly appears or disappears.
    """
    name = "template-schema-loads"
    expected = {
        "project", "session", "decision", "task", "note",
        "doc", "source", "spec", "handoff", "index",
        "release", "dream", "component", "api", "schema",
    }
    try:
        import _template_schema
        schema = _template_schema.load_schema(TEMPLATES)
        errors = _template_schema.schema_errors(TEMPLATES)
    except Exception as e:
        r.add_fail(name, f"templates do not parse: {e}")
        return
    # A file that does not parse no longer raises: it is skipped, so one stray
    # file cannot take the schema down and silently disable the write gate.
    # Skipped is not forgiven, though. This is where it gets said out loud.
    if errors:
        r.add_fail(name, "template(s) did not parse: " + "; ".join(errors))
        return
    got = set(schema)
    if got != expected:
        missing, extra = sorted(expected - got), sorted(got - expected)
        r.add_fail(name, f"kinds drifted - missing {missing}, unexpected {extra}")
        return
    # A status field with NO vocabulary is the hole the prover found: the
    # validator only ever rejected an EMPTY vocabulary, and a missing one is
    # not empty, so a one-word comment passed while enforcing nothing.
    for kind, spec in schema.items():
        fields = spec.get("required", frozenset()) | spec.get("optional", frozenset())
        if "status" in fields and not spec.get("vocab", {}).get("status"):
            r.add_fail(name, f"{kind}: status has no vocabulary, so any value "
                             "would be accepted. Write it as `a | b | c`.")
            return
    for kind, spec in schema.items():
        for field, values in spec.get("vocab", {}).items():
            if not values:
                r.add_fail(name, f"{kind}.{field} declares an empty vocabulary")
                return
    r.add_pass(name)


def validate_command_metadata_coherence(r: Result) -> None:
    name = "command-metadata-coherence"
    # ROOT is the plugin dir, so the metadata lives at ROOT/scripts/ directly
    meta_file = ROOT / "scripts" / "command-metadata.json"
    skill_file = CANONICAL / "SKILL.md"
    if not meta_file.exists() or not skill_file.exists():
        r.add_fail(name, f"missing {meta_file} or {skill_file}")
        return
    try:
        meta = json.loads(meta_file.read_text())
    except json.JSONDecodeError as e:
        r.add_fail(name, f"command-metadata.json invalid: {e}")
        return
    meta_verbs = {v["name"] for v in meta.get("verbs", [])}
    skill_text = skill_file.read_text()
    # Verbs in SKILL.md router table (rough match: lines starting with `| \`verb\` |`)
    skill_verbs = set(re.findall(r"\|\s+`(\w+)`\s+\|\s+`reference/", skill_text))
    if meta_verbs != skill_verbs:
        only_meta = meta_verbs - skill_verbs
        only_skill = skill_verbs - meta_verbs
        detail = []
        if only_meta:
            detail.append(f"in metadata not SKILL.md: {only_meta}")
        if only_skill:
            detail.append(f"in SKILL.md not metadata: {only_skill}")
        r.add_fail(name, "; ".join(detail))
        return
    r.add_pass(name)


def validate_plugin_version_set(r: Result) -> None:
    name = "plugin-version-set"
    f = ROOT / ".claude-plugin" / "plugin.json"
    if not f.exists():
        r.add_fail(name, f"{f.relative_to(ROOT)} missing")
        return
    try:
        data = json.loads(f.read_text())
    except json.JSONDecodeError as e:
        r.add_fail(name, f"plugin.json invalid: {e}")
        return
    version = data.get("version", "")
    if not version:
        r.add_fail(name, "version field empty or missing")
        return
    r.add_pass(name)


def _gitignore_active_entries(gi: Path) -> set[str]:
    """Active .gitignore lines — comments and `!` negations don't count as
    covering an entry (the old substring check was fooled by both)."""
    entries: set[str] = set()
    for ln in gi.read_text().splitlines():
        s = ln.strip()
        if s and not s.startswith("#") and not s.startswith("!"):
            entries.add(s)
    return entries


TIDY_PREVIEW_REQUIRED = ["summary.md", "changes.json"]


def _skill_frontmatter_version(skill_file: Path) -> str:
    """`version:` from the SKILL.md frontmatter BLOCK only — a body line that
    happens to start with `version:` must not be picked up."""
    if not skill_file.exists():
        return ""
    lines = skill_file.read_text().split("\n")
    if not lines or lines[0].rstrip() != "---":
        return ""
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if close is None:
        return ""
    for ln in lines[1:close]:
        m = re.match(r"^version:\s*(\S+)", ln)
        if m:
            return m.group(1)
    return ""


def validate_version_consistency(r: Result) -> None:
    name = "version-consistency"
    versions: dict[str, str] = {}
    # In-plugin sources (always present)
    try:
        versions["plugin.json"] = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text()).get("version", "")
        versions["command-metadata.json"] = json.loads((ROOT / "scripts" / "command-metadata.json").read_text()).get("version", "")
    except (json.JSONDecodeError, OSError) as e:
        r.add_fail(name, f"could not read a version source: {e}")
        return
    skill_file = CANONICAL / "SKILL.md"
    versions["SKILL.md"] = _skill_frontmatter_version(skill_file)
    # marketplace.json lives in the parent repo — only check when present (standalone installs won't have it)
    mk = ROOT.parent / ".claude-plugin" / "marketplace.json"
    if mk.is_file():
        try:
            entry = next((p for p in json.loads(mk.read_text()).get("plugins", []) if p.get("name") == "adjudant"), None)
            if entry is not None:
                versions["marketplace.json"] = entry.get("version", "")
        except json.JSONDecodeError:
            pass
    empties = [k for k, v in versions.items() if not v]
    if empties:
        r.add_fail(name, f"missing/empty version in: {empties}")
        return
    if len(set(versions.values())) != 1:
        r.add_fail(name, f"version mismatch: {versions}")
        return
    r.add_pass(name)


def _load_command_metadata() -> Path:
    """Locate command-metadata.json. ROOT is the plugin dir."""
    return ROOT / "scripts" / "command-metadata.json"


def validate_reference_files_exist(r: Result) -> None:
    """Every reference/*.md named in command-metadata.json or the SKILL.md
    router must exist on disk — a verb pointing at a missing runbook is dead."""
    name = "reference-files-exist"
    meta_file = _load_command_metadata()
    skill_file = CANONICAL / "SKILL.md"
    if not meta_file.exists() or not skill_file.exists():
        r.add_fail(name, f"missing {meta_file} or {skill_file}")
        return
    try:
        meta = json.loads(meta_file.read_text())
    except json.JSONDecodeError as e:
        r.add_fail(name, f"command-metadata.json invalid: {e}")
        return
    wanted: set[str] = set()
    for v in meta.get("verbs", []):
        ref = v.get("reference", "")
        if ref:
            wanted.add(ref)
    # reference/<file>.md paths cited in the SKILL.md router table
    wanted.update(re.findall(r"`(reference/[\w-]+\.md)`", skill_file.read_text()))
    missing = sorted(p for p in wanted if not (CANONICAL / p).is_file())
    if missing:
        r.add_fail(name, f"referenced files missing on disk: {missing}")
        return
    r.add_pass(name)


# Word to index. render_verb_surfaces owns the language table; this inverts it,
# so the two never disagree about what "six" means.
_NUMBER_WORDS = {word: n for n, word
                 in enumerate(render_verb_surfaces.NUMBER_WORDS) if n}

_VERB_COUNT_RE = re.compile(r"\b(\w+)\s+verbs\b", re.I)


def _miscounted_surfaces(expected: int) -> list[str]:
    """Spelled-out "<N> verbs" claims in prose no marker covers.

    The generated regions are right by construction; the sentences around them
    are not. adjudant's README says "with six verbs" in an opening paragraph
    outside every region, and the marketplace's own AGENTS.md said eleven verbs
    when there were thirteen. This is the half of the old parity validator that
    generation does not replace, so it stays.
    """
    surfaces: dict[str, str] = {}
    readme = ROOT / "README.md"
    if readme.is_file():
        surfaces["README.md"] = readme.read_text()
    pj = ROOT / ".claude-plugin" / "plugin.json"
    if pj.is_file():
        try:
            surfaces["plugin.json"] = json.loads(pj.read_text()).get("description", "")
        except json.JSONDecodeError:
            surfaces["plugin.json"] = ""
    mk = ROOT.parent / ".claude-plugin" / "marketplace.json"
    if mk.is_file():
        try:
            entry = next((p for p in json.loads(mk.read_text()).get("plugins", [])
                          if p.get("name") == "adjudant"), None)
            if entry is not None:
                surfaces["marketplace.json"] = entry.get("description", "")
        except json.JSONDecodeError:
            pass
    problems: list[str] = []
    for surface, text in surfaces.items():
        for m in _VERB_COUNT_RE.finditer(text):
            word = m.group(1).lower()
            if word in _NUMBER_WORDS and _NUMBER_WORDS[word] != expected:
                problems.append(
                    f"{surface} says '{word} verbs' but this build ships {expected}")
    return problems


def validate_verb_surfaces_generated(r: Result) -> None:
    """8. verb-surfaces-generated — the ten verb-derived doc surfaces are
    rendered from command-metadata.json, not typed twice.

    This used to compare copies: it checked that each verb name appeared in
    plugin.json, the README and the marketplace entry, and that any spelled-out
    "<N> verbs" agreed. Comparing copies is the weaker test, and it still let
    the marketplace's own AGENTS.md say eleven verbs when there were thirteen.
    Now there is one copy, and this fails when it is stale.
    """
    name = "verb-surfaces-generated"
    try:
        stale = render_verb_surfaces.apply(ROOT, check=True)
        meta = render_verb_surfaces.load_metadata(ROOT)
        expected = len(render_verb_surfaces.verbs_for(meta, _profile.audience()))
    except (render_verb_surfaces.SurfaceError, _profile.ProfileError) as exc:
        r.add_fail(name, f"could not render: {exc}")
        return
    problems: list[str] = []
    if stale:
        problems.append("stale surfaces (run scripts/render_verb_surfaces.py): "
                        + ", ".join(Path(p).name for p in stale))
    problems.extend(_miscounted_surfaces(expected))
    if problems:
        r.add_fail(name, "; ".join(problems))
        return
    r.add_pass(name)


# [text](target) with a non-empty path part (pure-#anchor links don't match)
MD_LOCAL_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+(?:#[^)\s]*)?)\)")
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _strip_fences_and_code(text: str) -> str:
    """Prose-only view of a markdown doc: fenced blocks and inline code spans
    removed. Fences are tracked line-based (a delimiter is a line whose lstrip
    starts with ```), NOT regex-paired — a mid-line ```` ```mermaid ```` code
    span or an unclosed trailing fence must not desynchronize the stripping.
    An unclosed fence is treated as fenced to EOF."""
    out: list[str] = []
    in_fence = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(ln)
    return INLINE_CODE_RE.sub("", "\n".join(out))


def validate_reference_doc_links(r: Result) -> None:
    """Every relative markdown link inside reference/*.md must resolve on disk.

    Catches the dead-companion-file class (a doc pointing at a
    references/GENERATION_RULES.md that never shipped). External links
    (any scheme:) are skipped; fenced blocks and inline code spans are
    stripped first so syntax examples like `[text](path.md)` can't
    false-positive."""
    name = "reference-doc-links"
    if not REFERENCE.is_dir():
        r.add_fail(name, f"{REFERENCE.relative_to(ROOT)} missing")
        return
    problems: list[str] = []
    for f in sorted(REFERENCE.glob("*.md")):
        text = _strip_fences_and_code(f.read_text())
        for m in MD_LOCAL_LINK_RE.finditer(text):
            target = m.group(1)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                continue  # http:, https:, mailto:, obsidian:, …
            path_part = target.split("#", 1)[0]
            if path_part and not (f.parent / path_part).exists():
                problems.append(f"{f.name} → {target}")
    if problems:
        r.add_fail(name, "dead relative links: " + "; ".join(problems))
        return
    r.add_pass(name)


MAX_VERB_DESCRIPTION = 220


def validate_verb_description_length(r: Result) -> None:
    """Verb descriptions are router lines, not runbooks — detail belongs in the
    verb's reference/*.md (the plugin's own progressive-disclosure doctrine).
    The cap keeps them from re-growing release by release."""
    name = "verb-description-length"
    meta_file = _load_command_metadata()
    try:
        verbs = json.loads(meta_file.read_text()).get("verbs", [])
    except (OSError, json.JSONDecodeError) as e:
        r.add_fail(name, f"could not read command-metadata.json: {e}")
        return
    too_long = [
        f"{v.get('name')} ({len(v.get('description', ''))} chars)"
        for v in verbs
        if len(v.get("description", "")) > MAX_VERB_DESCRIPTION
    ]
    if too_long:
        r.add_fail(
            name,
            f"descriptions over {MAX_VERB_DESCRIPTION} chars "
            f"(move detail to the verb's reference/*.md): " + ", ".join(too_long),
        )
        return
    r.add_pass(name)


def validate_repo_helper_parity(r: Result) -> None:
    """The repo-target trio mirrors the vault trio: each helper ships with a
    paired test module (the plugin's helper/test doctrine)."""
    name = "repo-helper-parity"
    scripts = ROOT / "scripts"
    missing = []
    for base in ("repo_walk", "repo_scan", "repo_tidy"):
        if not (scripts / f"{base}.py").is_file():
            missing.append(f"{base}.py")
        if not (scripts / f"test_{base}.py").is_file():
            missing.append(f"test_{base}.py")
    if missing:
        r.add_fail(name, f"missing repo helper/test files: {missing}")
        return
    r.add_pass(name)


REPO_STANDARD_CATEGORIES = ("version coherence", "symlink integrity", "context files", "plan age", "registration")


def validate_repo_standards_coverage(r: Result) -> None:
    """reference/repo-standards.md is the single source of truth for the repo
    detector categories — it must exist and name each one."""
    name = "repo-standards-coverage"
    f = REFERENCE / "repo-standards.md"
    if not f.is_file():
        r.add_fail(name, "reference/repo-standards.md missing")
        return
    text = f.read_text().lower()
    missing = [c for c in REPO_STANDARD_CATEGORIES if c not in text]
    if missing:
        r.add_fail(name, f"repo-standards.md missing categories: {missing}")
        return
    r.add_pass(name)


def validate_repo_tidy_preview_coherence(r: Result) -> None:
    name = "repo-tidy-preview-coherence"
    preview = ROOT / ".adjudant-repo-tidy-preview"
    if not preview.is_dir():
        r.add_pass(name)
        return
    missing = [f for f in TIDY_PREVIEW_REQUIRED if not (preview / f).is_file()]
    if missing:
        r.add_fail(name, f"repo-tidy preview dir missing required files: {missing}")
        return
    if not (preview / "files").is_dir():
        r.add_fail(name, "repo-tidy preview dir missing files/ subdir")
        return
    r.add_pass(name)


def validate_repo_tidy_backup_integrity(r: Result) -> None:
    name = "repo-tidy-backup-integrity"
    backup_root = ROOT / ".adjudant-repo-tidy-backup"
    if not backup_root.is_dir():
        r.add_pass(name)
        return
    for subdir in backup_root.iterdir():
        if subdir.is_dir():
            files = [p for p in subdir.rglob("*") if p.is_file()]
            if not files:
                continue
            has_legacy = any(p.name.endswith(".legacy") for p in files)
            if not has_legacy:
                r.add_fail(name, f"repo-tidy backup dir {subdir.name} has files but no .legacy: {[p.name for p in files]}")
                return
    r.add_pass(name)


def validate_gitignore_includes_repo_tidy_dirs(r: Result) -> None:
    name = "gitignore-includes-repo-tidy-dirs"
    preview = ROOT / ".adjudant-repo-tidy-preview"
    backup = ROOT / ".adjudant-repo-tidy-backup"
    if not preview.is_dir() and not backup.is_dir():
        r.add_pass(name)
        return
    gi = ROOT / ".gitignore"
    if not gi.is_file():
        gi = ROOT.parent / ".gitignore"
    if not gi.is_file():
        r.add_fail(name, "repo-tidy directories exist but .gitignore is missing")
        return
    entries = _gitignore_active_entries(gi)
    required = []
    if preview.is_dir():
        required.append(".adjudant-repo-tidy-preview/")
    if backup.is_dir():
        required.append(".adjudant-repo-tidy-backup/")
    missing = [e for e in required if e not in entries]
    if missing:
        r.add_fail(name, f".gitignore missing entries: {missing}")
        return
    r.add_pass(name)


_BASE_TOP_KEYS = {"filters", "formulas", "properties", "summaries", "views"}
_BASE_BARE_PROP_RE = re.compile(r"(?m)^\s+-\s+([a-z_][\w.]*)\s*$")
_BASE_GROUPBY_PROP_RE = re.compile(r"(?m)^\s+property:\s*([\w.]+)\s*$")


def _base_template_problems(text: str, legal_props: set) -> list[str]:
    """Structural problems in a shipped .base dashboard template.

    Regex-level (stdlib rule: no YAML dependency): known top-level keys,
    filters + views present, and every bare property referenced in an
    order/groupBy/sort position is a FIELD_SCHEMA field, a `file.*`
    builtin, or a declared `formula.*` - so a schema rename can never
    silently orphan a dashboard column."""
    problems: list[str] = []
    if "filters" not in text:
        problems.append("no filters block")
    if "views:" not in text:
        problems.append("no views block")
    for m in re.finditer(r"(?m)^([A-Za-z_][\w-]*):", text):
        if m.group(1) not in _BASE_TOP_KEYS:
            problems.append(f"unknown top-level key {m.group(1)!r}")
    declared_formulas = set(re.findall(r"(?m)^\s{2}([\w-]+):", text)) if "formulas:" in text else set()
    refs = [m.group(1) for m in _BASE_BARE_PROP_RE.finditer(text)]
    refs += [m.group(1) for m in _BASE_GROUPBY_PROP_RE.finditer(text)]
    for prop in refs:
        if prop.startswith("file."):
            continue
        if prop.startswith("formula."):
            if prop.split(".", 1)[1] not in declared_formulas:
                problems.append(f"undeclared formula {prop!r}")
            continue
        if prop not in legal_props:
            problems.append(f"property {prop!r} is not a FIELD_SCHEMA field")
    return problems


def validate_base_dashboards(r: Result) -> None:
    """27. base-dashboards — every shipped .base dashboard template is
    structurally sound and references only schema-legal properties."""
    name = "base-dashboards"
    src = TEMPLATES / "bases"
    if not src.is_dir():
        r.add_fail(name, "templates/bases/ missing")
        return
    files = sorted(src.glob("dashboard-*.base"))
    expected = {"dashboard-sessions.base", "dashboard-decisions.base",
                "dashboard-tasks.base", "dashboard-freshness.base"}
    if {f.name for f in files} != expected:
        r.add_fail(name, f"expected {sorted(expected)}, found {[f.name for f in files]}")
        return
    legal: set = set()
    for spec in FIELD_SCHEMA.values():
        legal |= spec["required"] | spec["optional"]
    for f in files:
        problems = _base_template_problems(f.read_text(), legal)
        if problems:
            r.add_fail(name, f"{f.name}: {problems[0]}")
            return
    r.add_pass(name)


def validate_hook_zone_awareness(r: Result) -> None:
    """26. hook-zone-awareness — no hook may hardcode projects/<slug>.

    Audit 2026-07-27: every hook built `{vault}/projects/{slug}` directly while
    /adjudant shelf moves projects to `_fridge/` and `_archive/` without
    touching the breadcrumb. A shelved project therefore grew a GHOST twin in
    the active zone that hooks wrote to forever, while writes to the real
    project were silently dropped. Hooks must resolve via find_project_dir
    (python) or zone_project_dir (bash), and gate the slug first.

    v3: the lifecycle is four named folders (active/paused/finished/archive)
    probed before the two pre-v3 shapes (bare, _fridge/_archive) still on
    disk until triage runs. The exemption below already strips a resolver's
    own function body before scanning for the `projects/<slug>` offender
    shape, so the four folder names appearing there as literals is not drift.
    """
    name = "hook-zone-awareness"
    scripts = sorted((ROOT / "hooks" / "scripts").glob("*"))
    offenders: list[str] = []
    missing_guard: list[str] = []
    for s in scripts:
        if s.suffix not in (".py", ".sh"):
            continue
        text = s.read_text()
        body = text
        if s.suffix == ".py":
            # The degraded-mode fallback legitimately spells the candidates
            # out; only flag construction OUTSIDE a find_project_dir def.
            body = re.sub(r"def find_project_dir\(.*?\n(?=\n{2}|\Z)", "", text, flags=re.S)
            if re.search(r'"projects"\s*/\s*slug', body):
                offenders.append(s.name)
            uses_slug_path = "slug" in body and 'vault' in body
            if uses_slug_path and "find_project_dir" not in text:
                offenders.append(s.name)
            if uses_slug_path and "is_safe_slug" not in text:
                missing_guard.append(s.name)
        else:
            body = re.sub(r"zone_project_dir\(\) \{.*?\n\}\n", "", text, flags=re.S)
            # Comments describing the fix legitimately name the old shape.
            body = "\n".join(ln for ln in body.splitlines()
                             if not ln.lstrip().startswith("#"))
            if re.search(r'projects/\$slug', body):
                offenders.append(s.name)
            if "$slug" in body and "zone_project_dir" not in text:
                offenders.append(s.name)
    if offenders:
        r.add_fail(name, f"hooks hardcode projects/<slug>: {sorted(set(offenders))}")
        return
    if missing_guard:
        r.add_fail(name, f"hooks build paths from an unvalidated slug: {sorted(set(missing_guard))}")
        return
    r.add_pass(name)


VOICE_MD = REFERENCE / "voice.md"

# The banned lexicon. Lives HERE, not in voice.md: this validator is the
# mechanical enforcer, so spending the list in model context every session
# bought nothing. voice.md points at this constant.
#
# Terms are matched case-insensitively as whole words (see the pattern build
# in validate_voice_lexicon). Add a term by adding a tuple entry.
# Merged with the no-ai-slop skill's banned list and owned by `_voice`, so the
# three surfaces that enforce this contract (repo docs here, vault writes in
# the PreToolUse gate, rendered output in validator 34) cannot drift apart.
BANNED_LEXICON: tuple[str, ...] = _voice.BANNED_LEXICON


def _parse_voice_lists() -> tuple[list[str], list[str], list[str]]:
    """(banned_lexicon, glazing, shape_phrases).

    Glazing and shape phrases are still bullets parsed from reference/voice.md;
    a trailing parenthetical on a bullet is a note, stripped before matching.
    The banned lexicon comes from BANNED_LEXICON above: it was pure enforceable
    detail, so it moved to the enforcer rather than being re-read every session.
    """
    lists: dict[str, list[str]] = {"banned": [], "glazing": [], "shape": []}
    current = None
    for line in VOICE_MD.read_text().splitlines():
        if line.startswith("## "):
            h = line[3:].strip().lower()
            current = ("banned" if h.startswith("banned lexicon")
                       else "glazing" if h.startswith("glazing")
                       else "shape" if h.startswith("shape phrases") else None)
            continue
        m = re.match(r"^-\s+(.+)$", line.strip())
        if m and current:
            term = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(1).strip())
            lists[current].append(term)
    return list(BANNED_LEXICON), lists["glazing"], lists["shape"]


def validate_voice_lexicon(r: Result) -> None:
    """23. voice-lexicon: no banned/glazing/shape terms in templates/, SKILL.md,
    reference/ (voice.md excepted); no em dashes in templates/.

    Fenced blocks and inline code spans are exempt from the lexicon scan:
    code is syntax, not prose (e.g. mermaid's `journey` diagram keyword).
    The em-dash arm stays on raw text; templates are vault-bound prose."""
    name = "voice-lexicon"
    if not VOICE_MD.is_file():
        r.add_fail(name, "reference/voice.md missing")
        return
    banned, glazing, shape = _parse_voice_lists()
    # `banned` is the BANNED_LEXICON constant above; glazing and shape phrases
    # are still bullets in voice.md. The emptiness guard covers all three alike.
    if not banned or not glazing or not shape:
        r.add_fail(name, "voice.md lists are empty")
        return
    surfaces = ([CANONICAL / "SKILL.md"]
                + sorted(TEMPLATES.glob("*.md"))
                + [p for p in sorted(REFERENCE.glob("*.md")) if p.name != "voice.md"])
    patterns = [(t, re.compile(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", re.IGNORECASE))
                for t in banned + glazing + shape]
    hits: list[str] = []
    for f in surfaces:
        text = _strip_fences_and_code(f.read_text())
        for term, rx in patterns:
            if rx.search(text):
                hits.append(f"{f.relative_to(ROOT)}: {term!r}")
    for t in sorted(TEMPLATES.glob("*.md")):
        if "—" in t.read_text():
            hits.append(f"{t.relative_to(ROOT)}: em dash")
    if hits:
        shown = "; ".join(hits[:8])
        more = f" (+{len(hits) - 8} more)" if len(hits) > 8 else ""
        r.add_fail(name, shown + more)
    else:
        r.add_pass(name)


def _voice_surfaces() -> list[Path]:
    """Every prose surface adjudant ships. voice.md quotes the contract, so it
    is the one file exempt from it."""
    return ([CANONICAL / "SKILL.md"]
            + sorted(TEMPLATES.glob("*.md"))
            + [p for p in sorted(REFERENCE.glob("*.md")) if p.name != "voice.md"])


def validate_voice_patterns(r: Result) -> None:
    """28. voice-patterns — the named no-ai-slop sentence patterns.

    The lexicon (validator 23) catches words. This catches shapes: superficial
    `-ing` analysis clauses, binary contrasts, importance puffery, weasel
    attribution, recap endings, rhetorical setups, faux-insight setups and
    throat-clearing. Every pattern in `_voice.SLOP_PATTERNS` was measured
    against these same files and scored zero false positives before it was
    admitted; the colon-reveal pattern scored 20 and stayed a judgment rule.
    """
    name = "voice-patterns"
    if not _voice.SLOP_PATTERNS:
        r.add_fail(name, "_voice.SLOP_PATTERNS is empty")
        return
    hits: list[str] = []
    for f in _voice_surfaces():
        for kind, matched in _voice.scan(f.read_text()):
            if kind != "lexicon":
                hits.append(f"{f.relative_to(ROOT)}: {kind} ({matched!r})")
    if hits:
        shown = "; ".join(hits[:6])
        more = f" (+{len(hits) - 6} more)" if len(hits) > 6 else ""
        r.add_fail(name, shown + more)
    else:
        r.add_pass(name)


def validate_render_voice(r: Result) -> None:
    """29. render-voice — the voice contract reaches rendered CLI output.

    voice.md has always described the shape of what the verbs print, and
    nothing checked it: the contract bound the docs about the code, not the
    code. Every string literal in the helpers is prose a user reads, so it is
    held to the same lexicon and patterns. Scanned via `ast` rather than regex
    over source, so a banned word in a comment or an identifier is not a hit -
    only text that can actually be printed.
    """
    name = "render-voice"
    # `_voice` defines the lexicon and `validate` reports it: both legitimately
    # contain every banned term as data.
    skip = {"_voice.py", "validate.py"}
    hits: list[str] = []
    for f in sorted(SCRIPTS.glob("*.py")):
        if f.name.startswith("test_") or f.name in skip:
            continue
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, OSError) as exc:
            r.add_fail(name, f"{f.name} did not parse: {exc}")
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for kind, matched in _voice.scan(node.value):
                    hits.append(f"{f.name}:{node.lineno}: {kind} ({matched!r})")
    if hits:
        shown = "; ".join(hits[:6])
        more = f" (+{len(hits) - 6} more)" if len(hits) > 6 else ""
        r.add_fail(name, shown + more)
    else:
        r.add_pass(name)


def validate_advisor_wiring(r: Result) -> None:
    """30. advisor-wiring — the opt-in advisor's three surfaces stay wired.

    The mode's whole design is visible state: a contract doc, a SessionStart
    banner that names it, and an AGENTS.md marker the toggle stamps. Any one
    of them silently dropping out leaves a mode that claims to watch and does
    not — the worst version, since the user opted in expecting eyes.

    The toggle moved into `status.py` when the advisor verb folded into
    `status`; the mode itself did not move, so all three surfaces still have
    to agree.
    """
    name = "advisor-wiring"
    problems: list[str] = []
    doc = ROOT / "skills" / "adjudant" / "reference" / "advisor.md"
    if not doc.is_file():
        problems.append("reference/advisor.md missing")
    hook = ROOT / "hooks" / "scripts" / "session-start.sh"
    hook_text = hook.read_text() if hook.is_file() else ""
    if "advisor_knob" not in hook_text or "reference/advisor.md" not in hook_text:
        problems.append("session-start.sh no longer reads the advisor knob "
                        "and points the banner at reference/advisor.md")
    helper = ROOT / "scripts" / "status.py"
    helper_text = helper.read_text() if helper.is_file() else ""
    if "AGENTS_MARKER_PREFIX" not in helper_text:
        problems.append("status.py lost the advisor's AGENTS.md marker")
    if problems:
        r.add_fail(name, "; ".join(problems))
        return
    r.add_pass(name)


BOARD_DATA_RE = re.compile(r"/\*BOARD_DATA_START\*/(.*?)/\*BOARD_DATA_END\*/", re.DOTALL)
# A subresource fetched from off-machine: src=/href= with a scheme or a
# protocol-relative //, the same inside a CSS url(), or any @import.
BOARD_EXTERNAL_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//|@import""",
    re.IGNORECASE,
)
# An error nobody will ever see. persistLocal() carried the only one of these
# and it cost every drag made while storage was blocked.
BOARD_EMPTY_CATCH_RE = re.compile(r"catch\s*\(\s*\w*\s*\)\s*\{\s*\}")


def validate_board_template_markers(r: Result) -> None:
    """24. board-template-markers: templates/board.html exists, both BOARD_DATA
    markers are present, the seeded JSON between them parses and carries at
    least one column, the file fetches nothing from off-machine, and it
    swallows no error silently.

    The first two keep a markerless or corrupt template from failing every
    scaffold/reseed at hook time. The last three are properties of the shipped
    artefact rather than of any one code path, so they belong in the build:
    the board is served from disk and must work fully offline, `normalize()`
    refuses a deck with no lanes (a seed that lost its columns would ship a
    board that paints an error instead of the starter deck), and an empty
    catch block is how a failed save became invisible in the first place.
    """
    name = "board-template-markers"
    tpl = TEMPLATES / "board.html"
    if not tpl.is_file():
        r.add_fail(name, "templates/board.html missing")
        return
    text = tpl.read_text()
    m = BOARD_DATA_RE.search(text)
    if not m:
        r.add_fail(name, "BOARD_DATA_START/END markers missing from board.html")
        return
    try:
        seed = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        r.add_fail(name, f"seeded JSON between BOARD_DATA markers does not parse: {e}")
        return
    if not isinstance(seed, dict) or not isinstance(seed.get("columns"), list) or not seed["columns"]:
        r.add_fail(name, "seeded deck has no columns: the shipped board would "
                         "render an error instead of the starter deck")
        return
    external = BOARD_EXTERNAL_RE.search(text)
    if external:
        r.add_fail(name, "board.html references something off-machine "
                         f"({external.group(0)!r}): the board is served from disk "
                         "and must work fully offline")
        return
    if BOARD_EMPTY_CATCH_RE.search(text):
        r.add_fail(name, "board.html has an empty catch block: a swallowed "
                         "error is how a failed save became invisible")
        return
    r.add_pass(name)


PLUGIN_ROOT_PATH_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}(/[^\"'\s]+)")


def validate_hooks_wiring(r: Result) -> None:
    """25. hooks-wiring: every command in hooks/hooks.json resolves to an
    existing executable file under hooks/scripts/ after ${CLAUDE_PLUGIN_ROOT}
    substitution. Dead wiring cannot stay green."""
    name = "hooks-wiring"
    hooks_file = ROOT / "hooks" / "hooks.json"
    if not hooks_file.is_file():
        r.add_fail(name, "hooks/hooks.json missing")
        return
    try:
        data = json.loads(hooks_file.read_text())
    except json.JSONDecodeError as e:
        r.add_fail(name, f"hooks.json invalid: {e}")
        return
    scripts_dir = (ROOT / "hooks" / "scripts").resolve()
    problems: list[str] = []
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries or []:
            for hook in entry.get("hooks") or []:
                command = str(hook.get("command", ""))
                m = PLUGIN_ROOT_PATH_RE.search(command)
                if not m:
                    problems.append(f"{event}: no ${{CLAUDE_PLUGIN_ROOT}} path in {command!r}")
                    continue
                script = (ROOT / m.group(1).lstrip("/")).resolve()
                rel = f"{event}: {m.group(1)}"
                if not script.is_file():
                    problems.append(f"{rel} does not exist")
                elif scripts_dir not in script.parents:
                    problems.append(f"{rel} is not under hooks/scripts/")
                elif not os.access(script, os.X_OK):
                    problems.append(f"{rel} is not executable")
    if problems:
        r.add_fail(name, "; ".join(problems))
        return
    r.add_pass(name)


def validate_place_zone_parity(r: Result) -> None:
    """24. place-zone-parity — _place's lifecycle folder set matches _vault_walk.

    _place.py duplicates the four folder names on purpose: a hook in degraded
    mode imports it without _vault_walk. Every duplicate drifts unless
    something compares them, which is the lesson the 110-key frontmatter
    taught.
    """
    name = "place-zone-parity"
    from _place import _LIFECYCLE_FOLDERS
    if set(_LIFECYCLE_FOLDERS) != set(PROJECT_ZONES):
        r.add_fail(name, f"_place {sorted(_LIFECYCLE_FOLDERS)} vs "
                         f"_vault_walk {sorted(PROJECT_ZONES)}")
        return
    r.add_pass(name)


def validate_standards_structure_parity(r: Result) -> None:
    """25. standards-structure-parity — the standards doc names every folder.

    The doc used to restate every field rule in prose, which made it a second
    declaration that drifted from the templates. It now links to them, so the
    one thing it still states alone is the folder layout — and that is what
    this holds.
    """
    name = "standards-structure-parity"
    from _place import KIND_FOLDER
    doc = REFERENCE / "vault-standards.md"
    if not doc.is_file():
        r.add_fail(name, "reference/vault-standards.md missing")
        return
    text = doc.read_text(errors="replace")
    missing = [f"{f}/" for f in sorted(set(KIND_FOLDER.values()) - {""})
               if f"{f}/" not in text]
    missing += [f"{z}/" for z in PROJECT_ZONES if f"{z}/" not in text]
    if missing:
        r.add_fail(name, "vault-standards.md omits: " + ", ".join(missing))
        return
    if "required:" in text:
        r.add_fail(name, "vault-standards.md restates a template's field set")
        return
    r.add_pass(name)


def main() -> int:
    print(f"adjudant validators — running from {ROOT}")
    r = Result()
    validate_harness_parity(r)
    validate_claude_md_imports_agents(r)
    validate_template_schema_loads(r)
    validate_command_metadata_coherence(r)
    validate_plugin_version_set(r)
    validate_version_consistency(r)
    validate_reference_files_exist(r)
    validate_verb_surfaces_generated(r)
    validate_reference_doc_links(r)
    validate_verb_description_length(r)
    validate_repo_helper_parity(r)
    validate_repo_standards_coverage(r)
    validate_repo_tidy_preview_coherence(r)
    validate_repo_tidy_backup_integrity(r)
    validate_gitignore_includes_repo_tidy_dirs(r)
    validate_voice_lexicon(r)
    validate_board_template_markers(r)
    validate_hooks_wiring(r)
    validate_hook_zone_awareness(r)
    validate_base_dashboards(r)
    validate_voice_patterns(r)
    validate_render_voice(r)
    validate_advisor_wiring(r)
    validate_place_zone_parity(r)
    validate_standards_structure_parity(r)
    return r.report()


if __name__ == "__main__":
    sys.exit(main())
