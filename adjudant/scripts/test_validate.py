"""Tests for adjudant/scripts/validate.py — the drift-defense validators.

Each test builds a minimal temp plugin tree and monkeypatches validate.py's
module-level path anchors (ROOT / CANONICAL / TEMPLATES / REFERENCE / HARNESS_DIRS)
to point at it, then drives one validator and inspects the Result.
"""

import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path

import validate
from validate import Result


def _build(root: Path, *, version: str = "1.0.0", verbs=("connect", "check")) -> Path:
    """Lay out a minimal, valid adjudant plugin tree under `root`. Returns the
    plugin dir (what ROOT should be patched to). marketplace.json lives at
    root/.claude-plugin (i.e. ROOT.parent), matching the real layout."""
    plugin = root / "adjudant"
    canonical = plugin / "skills" / "adjudant"
    (canonical / "templates").mkdir(parents=True)
    (canonical / "reference").mkdir(parents=True)

    rows = "\n".join(f"| `{v}` | `reference/{v}.md` | desc |" for v in verbs)
    (canonical / "SKILL.md").write_text(
        f"---\nname: adjudant\nversion: {version}\n---\n\n"
        f"| Verb | Loads | Purpose |\n|---|---|---|\n{rows}\n"
    )

    for v in verbs:
        (canonical / "reference" / f"{v}.md").write_text(f"# /adjudant {v}\n")

    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "adjudant", "version": version,
                    "description": f"verbs: {', '.join(verbs)}"}, indent=2) + "\n")
    (plugin / "README.md").write_text(f"# adjudant\n\nverbs: {', '.join(verbs)}\n")

    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts" / "command-metadata.json").write_text(
        json.dumps({"name": "adjudant", "version": version,
                    "verbs": [{"name": v, "reference": f"reference/{v}.md"} for v in verbs]},
                   indent=2) + "\n")

    for h in ("source", ".claude", ".gemini"):
        d = plugin / h / "skills"
        d.mkdir(parents=True)
        (d / "adjudant").symlink_to(Path("../../skills/adjudant"))

    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "adjudant", "version": version,
                                 "description": f"verbs: {', '.join(verbs)}"}]}, indent=2) + "\n")

    return plugin


class _PatchedTree(unittest.TestCase):
    """Base: build a valid tree in a temp dir and point validate.* at it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.plugin = _build(root)
        self._orig = {k: getattr(validate, k)
                      for k in ("ROOT", "CANONICAL", "TEMPLATES", "REFERENCE", "HARNESS_DIRS")}
        validate.ROOT = self.plugin
        validate.CANONICAL = self.plugin / "skills" / "adjudant"
        validate.TEMPLATES = validate.CANONICAL / "templates"
        validate.REFERENCE = validate.CANONICAL / "reference"
        validate.HARNESS_DIRS = [self.plugin / h / "skills" / "adjudant"
                                 for h in ("source", ".claude", ".gemini")]

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(validate, k, v)
        self._tmp.cleanup()


class TestHarnessParity(_PatchedTree):

    def test_passes_when_symlinks_resolve(self):
        r = Result()
        validate.validate_harness_parity(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_one_harness_is_real_dir(self):
        # Replace the .claude symlink with a real directory
        link = self.plugin / ".claude" / "skills" / "adjudant"
        link.unlink()
        link.mkdir()
        r = Result()
        validate.validate_harness_parity(r)
        self.assertTrue(any("harness-parity" in f for f in r.failures))


class TestVersionConsistency(_PatchedTree):

    def test_passes_at_lockstep(self):
        r = Result()
        validate.validate_version_consistency(r)
        self.assertEqual(r.failures, [])

    def test_fails_on_single_file_mismatch(self):
        pj = self.plugin / ".claude-plugin" / "plugin.json"
        pj.write_text(json.dumps({"name": "adjudant", "version": "9.9.9"}) + "\n")
        r = Result()
        validate.validate_version_consistency(r)
        self.assertTrue(any("version-consistency" in f for f in r.failures))


class TestReferenceFilesExist(_PatchedTree):

    def test_passes_when_all_references_exist(self):
        r = Result()
        validate.validate_reference_files_exist(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_a_reference_file_is_missing(self):
        (self.plugin / "skills" / "adjudant" / "reference" / "check.md").unlink()
        r = Result()
        validate.validate_reference_files_exist(r)
        self.assertTrue(any("reference-files-exist" in f for f in r.failures))


class TestVerbSurfacesGenerated(unittest.TestCase):
    """8. Runs against the REAL tree: the fixture in _build() has no markers,
    and a validator that only ever sees a fixture proves nothing about what
    ships."""

    def _copy_of_the_real_tree(self, tmp: str) -> Path:
        import shutil as _sh
        real = Path(__file__).resolve().parent.parent
        fake = Path(tmp) / "adjudant"
        _sh.copytree(real, fake, symlinks=True,
                     ignore=_sh.ignore_patterns("__pycache__", ".pytest_cache"))
        return fake

    def _run_against(self, fake: Path) -> Result:
        orig = validate.ROOT
        validate.ROOT = fake
        try:
            r = Result()
            validate.validate_verb_surfaces_generated(r)
        finally:
            validate.ROOT = orig
        return r

    def test_the_shipped_surfaces_are_current(self):
        r = Result()
        validate.validate_verb_surfaces_generated(r)
        self.assertIn("verb-surfaces-generated", r.passes, r.failures)

    def test_a_stale_surface_fails(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory() as tmp:
            fake = self._copy_of_the_real_tree(tmp)
            readme = fake / "README.md"
            readme.write_text(readme.read_text().replace(
                "| Verb | What it does |", "| Verb | What it once did |"))
            r = self._run_against(fake)
            self.assertTrue(any("verb-surfaces-generated" in f for f in r.failures))

    def _shipped(self) -> tuple[int, str]:
        """(count, word) for THIS build. The public build ships fewer verbs
        than this one, and a test that spells either number out is a test that
        only passes in the tree it was written in."""
        import render_verb_surfaces as rvs
        meta = rvs.load_metadata(validate.ROOT)
        n = len(rvs.verbs_for(meta, validate._profile.audience()))
        return n, rvs.NUMBER_WORDS[n]

    def test_a_wrong_count_outside_every_region_still_fails(self):
        # The escape class the old parity validator existed for, and the one
        # generation cannot see: prose that names a verb count no marker
        # covers. It used to edit the README's own opening sentence, which
        # tied the test to whether that sentence still carried a count — it no
        # longer does, because a hand-typed count beside a generated one is the
        # second declaration this whole design removes. The count is written in
        # here now, so the check holds however the shipped prose is worded.
        import tempfile as _tf
        with _tf.TemporaryDirectory() as tmp:
            fake = self._copy_of_the_real_tree(tmp)
            readme = fake / "README.md"
            readme.write_text(readme.read_text()
                              + "\nAdjudant ships with nine verbs.\n")
            r = self._run_against(fake)
            n, _ = self._shipped()
            self.assertTrue(any(f"says 'nine verbs' but this build ships {n}" in f
                                for f in r.failures), r.failures)

    def test_a_correct_count_in_prose_is_accepted(self):
        # The other half: the check is about the count being wrong, not about
        # prose mentioning one.
        import tempfile as _tf
        with _tf.TemporaryDirectory() as tmp:
            fake = self._copy_of_the_real_tree(tmp)
            readme = fake / "README.md"
            _, word = self._shipped()
            readme.write_text(readme.read_text()
                              + f"\nAdjudant ships with {word} verbs.\n")
            r = self._run_against(fake)
            self.assertIn("verb-surfaces-generated", r.passes, r.failures)


class TestCommandMetadataCoherence(_PatchedTree):

    def test_passes_when_verbs_match(self):
        r = Result()
        validate.validate_command_metadata_coherence(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_metadata_has_extra_verb(self):
        meta = self.plugin / "scripts" / "command-metadata.json"
        data = json.loads(meta.read_text())
        data["verbs"].append({"name": "ghost"})  # not in SKILL.md router
        meta.write_text(json.dumps(data) + "\n")
        r = Result()
        validate.validate_command_metadata_coherence(r)
        self.assertTrue(any("command-metadata-coherence" in f for f in r.failures))


class TestClaudeMdImportsAgents(_PatchedTree):

    def test_passes_when_first_line_is_import(self):
        (validate.TEMPLATES / "CLAUDE.md").write_text("\n@AGENTS.md\n\n# Overrides\n")
        r = Result()
        validate.validate_claude_md_imports_agents(r)
        self.assertEqual(r.failures, [])

    def test_fails_on_wrong_first_line(self):
        (validate.TEMPLATES / "CLAUDE.md").write_text("# CLAUDE\n@AGENTS.md\n")
        r = Result()
        validate.validate_claude_md_imports_agents(r)
        self.assertTrue(any("claude-md-imports-agents" in f for f in r.failures))

    def test_fails_when_missing(self):
        r = Result()
        validate.validate_claude_md_imports_agents(r)
        self.assertTrue(any("claude-md-imports-agents" in f for f in r.failures))


class TestTemplateSchemaLoads(_PatchedTree):
    """The one validator that replaced the six.

    It drives the real parser over whatever TEMPLATES points at, so a template
    that stops parsing, or a kind that appears or disappears, fails the build
    at the only place the schema is declared.
    """

    REAL = Path(__file__).resolve().parent.parent / "skills" / "adjudant" / "templates"

    def _ship(self, skip=()):
        for src in sorted(self.REAL.glob("*.md")):
            if src.name in skip:
                continue
            (validate.TEMPLATES / src.name).write_text(src.read_text())

    def test_passes_on_the_shipped_templates(self):
        self._ship()
        r = Result()
        validate.validate_template_schema_loads(r)
        self.assertEqual(r.failures, [], r.failures)
        self.assertIn("template-schema-loads", r.passes)

    def test_fails_when_a_kind_disappears(self):
        self._ship(skip=("decision.md",))
        r = Result()
        validate.validate_template_schema_loads(r)
        self.assertTrue(any("'decision'" in f for f in r.failures), r.failures)

    def test_fails_when_a_kind_appears(self):
        self._ship()
        (validate.TEMPLATES / "memory.md").write_text(
            "---\ntype: memory\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n\nbody\n")
        r = Result()
        validate.validate_template_schema_loads(r)
        self.assertTrue(any("'memory'" in f for f in r.failures), r.failures)

    def test_fails_when_a_template_stops_parsing(self):
        # Asserts the behaviour, not the wording. An unparseable template no
        # longer raises out of load_schema (that took the whole schema down and
        # silently disabled the write gate); it is skipped and recorded, and
        # THIS is where being skipped gets said out loud.
        self._ship()
        (validate.TEMPLATES / "note.md").write_text("---\ntype:\n---\n\nbody\n")
        r = Result()
        validate.validate_template_schema_loads(r)
        self.assertTrue(r.failures, "a broken template passed validation")
        self.assertTrue(any("note.md" in f for f in r.failures),
                        f"the failure did not name the broken file: {r.failures}")

    def test_fails_when_a_stray_file_cannot_parse(self):
        # The case that started this: a scratch file dropped into templates/
        # used to disable enforcement everywhere. Now it costs only itself,
        # and the validator still refuses to call the tree clean.
        self._ship()
        (validate.TEMPLATES / "zz-scratch.md").write_text("---\ntitle: s\n---\n\nx\n")
        r = Result()
        validate.validate_template_schema_loads(r)
        self.assertTrue(any("zz-scratch.md" in f for f in r.failures), r.failures)


class TestPluginVersionSet(_PatchedTree):

    def test_passes_with_version(self):
        r = Result()
        validate.validate_plugin_version_set(r)
        self.assertEqual(r.failures, [])

    def test_fails_on_empty_version(self):
        pj = self.plugin / ".claude-plugin" / "plugin.json"
        pj.write_text(json.dumps({"name": "adjudant", "version": ""}) + "\n")
        r = Result()
        validate.validate_plugin_version_set(r)
        self.assertTrue(any("plugin-version-set" in f for f in r.failures))


class TestSkillFrontmatterVersion(_PatchedTree):

    def test_body_version_line_not_picked_up(self):
        # A body line starting `version:` must not shadow the frontmatter value
        skill = self.plugin / "skills" / "adjudant" / "SKILL.md"
        skill.write_text(
            "---\nname: adjudant\nversion: 1.0.0\n---\n\n"
            "# adjudant\n\nversion: 9.9.9 is mentioned in prose here\n"
            "| `connect` | `reference/connect.md` | d |\n"
            "| `check` | `reference/check.md` | d |\n"
        )
        self.assertEqual(validate._skill_frontmatter_version(skill), "1.0.0")


class TestReferenceDocLinks(_PatchedTree):

    def test_passes_on_valid_and_external_links(self):
        ref = self.plugin / "skills" / "adjudant" / "reference"
        (ref / "companion.md").write_text("# companion\n")
        (ref / "connect.md").write_text(
            "See [companion](companion.md) and [anchor](companion.md#top).\n"
            "External: [mermaid](https://mermaid.js.org/) and [uri](obsidian://open?vault=x).\n"
            "Pure anchor: [here](#section).\n"
            "```\nfenced [dead](inside-fence.md) links are ignored\n```\n")
        r = Result()
        validate.validate_reference_doc_links(r)
        self.assertEqual(r.failures, [])

    def test_fails_on_dead_relative_link(self):
        ref = self.plugin / "skills" / "adjudant" / "reference"
        (ref / "connect.md").write_text("[rules](references/GENERATION_RULES.md)\n")
        r = Result()
        validate.validate_reference_doc_links(r)
        self.assertEqual(len(r.failures), 1)
        self.assertIn("GENERATION_RULES.md", r.failures[0])

    def test_inline_fence_mention_does_not_desync_stripping(self):
        # A mid-line ```` ```mermaid ```` code span must not pair with a real
        # fence delimiter: the prose dead link after it must still be caught,
        # and a syntax-example link INSIDE a real fence must stay exempt.
        ref = self.plugin / "skills" / "adjudant" / "reference"
        (ref / "connect.md").write_text(
            "Obsidian renders ```` ```mermaid ```` blocks natively.\n"
            "A dead prose link: [dead](missing-a.md)\n"
            "```mermaid\n"
            "flowchart LR\n"
            "  a[see [ex](missing-in-fence.md)]\n"
            "```\n"
            "More prose: [dead2](missing-b.md)\n")
        r = Result()
        validate.validate_reference_doc_links(r)
        self.assertEqual(len(r.failures), 1)
        self.assertIn("missing-a.md", r.failures[0])
        self.assertIn("missing-b.md", r.failures[0])
        self.assertNotIn("missing-in-fence.md", r.failures[0])

    def test_unclosed_fence_treated_as_fenced_to_eof(self):
        ref = self.plugin / "skills" / "adjudant" / "reference"
        (ref / "connect.md").write_text(
            "Prose [dead](missing-a.md)\n"
            "```\n"
            "unclosed fence [x](missing-in-fence.md)\n")
        r = Result()
        validate.validate_reference_doc_links(r)
        self.assertEqual(len(r.failures), 1)
        self.assertIn("missing-a.md", r.failures[0])
        self.assertNotIn("missing-in-fence.md", r.failures[0])


class TestVerbDescriptionLength(_PatchedTree):

    def _write_meta(self, desc: str) -> None:
        (self.plugin / "scripts" / "command-metadata.json").write_text(
            json.dumps({"name": "adjudant", "version": "1.0.0",
                        "verbs": [{"name": "connect", "description": desc,
                                   "reference": "reference/connect.md"}]}) + "\n")

    def test_passes_at_cap(self):
        self._write_meta("x" * 220)
        r = Result()
        validate.validate_verb_description_length(r)
        self.assertEqual(r.failures, [])

    def test_fails_over_cap(self):
        self._write_meta("x" * 300)
        r = Result()
        validate.validate_verb_description_length(r)
        self.assertEqual(len(r.failures), 1)
        self.assertIn("connect (300 chars)", r.failures[0])
        self.assertIn("reference/*.md", r.failures[0])


class TestRepoHelperParity(_PatchedTree):

    def _make_helpers(self):
        scripts = self.plugin / "scripts"
        for base in ("repo_walk", "repo_scan", "repo_tidy"):
            (scripts / f"{base}.py").write_text("# helper\n")
            (scripts / f"test_{base}.py").write_text("# test\n")

    def test_passes_when_all_present(self):
        self._make_helpers()
        r = Result()
        validate.validate_repo_helper_parity(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_a_test_is_missing(self):
        self._make_helpers()
        (self.plugin / "scripts" / "test_repo_scan.py").unlink()
        r = Result()
        validate.validate_repo_helper_parity(r)
        self.assertTrue(any("repo-helper-parity" in f for f in r.failures))
        self.assertIn("test_repo_scan.py", r.failures[0])


class TestRepoStandardsCoverage(_PatchedTree):

    def _write_standards(self, text):
        (self.plugin / "skills" / "adjudant" / "reference" / "repo-standards.md").write_text(text)

    def test_passes_with_all_categories(self):
        self._write_standards(
            "version coherence\nsymlink integrity\ncontext files\nplan age\nregistration\n")
        r = Result()
        validate.validate_repo_standards_coverage(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_category_missing(self):
        self._write_standards("version coherence\ncontext files\nplan age\nregistration\n")
        r = Result()
        validate.validate_repo_standards_coverage(r)
        self.assertTrue(any("repo-standards-coverage" in f for f in r.failures))
        self.assertIn("symlink integrity", r.failures[0])

    def test_fails_when_file_absent(self):
        r = Result()
        validate.validate_repo_standards_coverage(r)
        self.assertTrue(any("repo-standards-coverage" in f for f in r.failures))


class TestRepoTidyPreviewCoherence(_PatchedTree):

    def test_passes_when_coherent(self):
        d = self.plugin / ".adjudant-repo-tidy-preview"
        (d / "files").mkdir(parents=True)
        (d / "summary.md").write_text("# s\n")
        (d / "changes.json").write_text("{}\n")
        r = Result()
        validate.validate_repo_tidy_preview_coherence(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_files_dir_missing(self):
        d = self.plugin / ".adjudant-repo-tidy-preview"
        d.mkdir(parents=True)
        (d / "summary.md").write_text("# s\n")
        (d / "changes.json").write_text("{}\n")
        r = Result()
        validate.validate_repo_tidy_preview_coherence(r)
        self.assertTrue(any("repo-tidy-preview-coherence" in f for f in r.failures))


class TestRepoTidyBackupIntegrity(_PatchedTree):

    def test_passes_with_legacy_file(self):
        d = self.plugin / ".adjudant-repo-tidy-backup" / "20260707-000000"
        d.mkdir(parents=True)
        (d / "alpha__source__skills__alpha.legacy").write_text("prior\n")
        r = Result()
        validate.validate_repo_tidy_backup_integrity(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_files_but_no_legacy(self):
        d = self.plugin / ".adjudant-repo-tidy-backup" / "20260707-000000"
        d.mkdir(parents=True)
        (d / "note.txt").write_text("not a backup\n")
        r = Result()
        validate.validate_repo_tidy_backup_integrity(r)
        self.assertTrue(any("repo-tidy-backup-integrity" in f for f in r.failures))


class TestGitignoreIncludesRepoTidyDirs(_PatchedTree):

    def test_passes_with_entry(self):
        (self.plugin / ".adjudant-repo-tidy-preview").mkdir()
        (self.plugin / ".gitignore").write_text(".adjudant-repo-tidy-preview/\n")
        r = Result()
        validate.validate_gitignore_includes_repo_tidy_dirs(r)
        self.assertEqual(r.failures, [])

    def test_fails_when_missing(self):
        (self.plugin / ".adjudant-repo-tidy-backup").mkdir()
        (self.plugin / ".gitignore").write_text("# nothing\n")
        r = Result()
        validate.validate_gitignore_includes_repo_tidy_dirs(r)
        self.assertTrue(any("gitignore-includes-repo-tidy-dirs" in f for f in r.failures))


class TestVoiceLexicon(unittest.TestCase):

    def test_parse_voice_lists(self):
        banned, glazing, shape = validate._parse_voice_lists()
        self.assertIn("forward-thinking", banned)
        self.assertIn("leverage", banned)          # qualifier stripped
        self.assertIn("You're absolutely right", glazing)

    def test_parse_voice_lists_includes_shape_phrases(self):
        _banned, _glazing, shape = validate._parse_voice_lists()
        self.assertIn("Hope this helps", shape)
        self.assertIn("Let me know if", shape)
        self.assertIn("Uh oh", shape)
        self.assertIn("Happy to clarify", shape)
        self.assertIn("Feel free to ask", shape)
        self.assertIn("Great question", shape)

    def test_validator_passes_on_repo(self):
        r = validate.Result()
        validate.validate_voice_lexicon(r)
        self.assertEqual(r.failures, [], r.failures)

    def test_code_spans_are_exempt(self):
        # Code is syntax, not prose: a banned term inside a fenced block or an
        # inline code span must not fail the validator; the same term in prose must.
        with tempfile.TemporaryDirectory() as tmp:
            plugin = _build(Path(tmp))
            canonical = plugin / "skills" / "adjudant"
            (canonical / "reference" / "voice.md").write_text(
                "# Voice\n\n## Banned lexicon\n\n- seamless\n\n"
                "## Glazing phrases\n\n- Great question\n\n"
                "## Shape phrases\n\n- Hope this helps\n"
            )
            orig = {k: getattr(validate, k)
                    for k in ("ROOT", "CANONICAL", "TEMPLATES", "REFERENCE", "VOICE_MD")}
            try:
                validate.ROOT = plugin
                validate.CANONICAL = canonical
                validate.TEMPLATES = canonical / "templates"
                validate.REFERENCE = canonical / "reference"
                validate.VOICE_MD = canonical / "reference" / "voice.md"
                doc = canonical / "reference" / "check.md"
                doc.write_text(
                    "# check\n\n```mermaid\nseamless\n```\n\nUse `seamless` in code.\n")
                r = Result()
                validate.validate_voice_lexicon(r)
                self.assertEqual(r.failures, [], r.failures)
                doc.write_text("# check\n\nA seamless experience.\n")
                r = Result()
                validate.validate_voice_lexicon(r)
                self.assertTrue(any("voice-lexicon" in f for f in r.failures))
                # Shape phrases are matched the same way as the other lists.
                doc.write_text("# check\n\nHope this helps with the render.\n")
                r = Result()
                validate.validate_voice_lexicon(r)
                self.assertTrue(any("Hope this helps" in f for f in r.failures))
            finally:
                for k, v in orig.items():
                    setattr(validate, k, v)


class TestBoardTemplateMarkersOnRepo(unittest.TestCase):

    def test_validator_passes_on_repo(self):
        r = validate.Result()
        validate.validate_board_template_markers(r)
        self.assertEqual(r.failures, [], r.failures)
        self.assertIn("board-template-markers", r.passes)


class TestBoardTemplateMarkers(_PatchedTree):

    _GOOD = ('<html><script>const DECK = /*BOARD_DATA_START*/'
             '{"columns": [{"id": "backlog", "name": "Backlog"}], "cards": []}'
             '/*BOARD_DATA_END*/;</script></html>')

    def _write(self, text):
        (validate.TEMPLATES / "board.html").write_text(text)
        r = Result()
        validate.validate_board_template_markers(r)
        return r

    def test_fails_when_template_missing(self):
        r = Result()
        validate.validate_board_template_markers(r)
        self.assertTrue(any("board-template-markers" in f for f in r.failures))

    def test_passes_with_markers_and_valid_json(self):
        r = self._write(self._GOOD)
        self.assertEqual(r.failures, [], r.failures)

    def test_fails_when_markers_absent(self):
        r = self._write("<html>no markers</html>")
        self.assertTrue(any("marker" in f.lower() for f in r.failures))

    def test_fails_when_seed_json_broken(self):
        r = self._write("<script>/*BOARD_DATA_START*/{not json}/*BOARD_DATA_END*/</script>")
        self.assertTrue(any("board-template-markers" in f for f in r.failures))

    def test_fails_when_the_seeded_deck_has_no_columns(self):
        # normalize() refuses a deck with no lanes, so a seed that lost its
        # columns would ship a board that paints an error state.
        r = self._write(self._GOOD.replace(
            '"columns": [{"id": "backlog", "name": "Backlog"}], ', ""))
        self.assertTrue(any("no columns" in f for f in r.failures), r.failures)

    def test_fails_on_anything_fetched_off_machine(self):
        # The board is served from disk and must work fully offline.
        for external in ('<script src="https://cdn.example/x.js"></script>',
                         '<link href="//fonts.example/f.css" rel="stylesheet">',
                         "<style>body{background:url(https://evil.example/p.png)}</style>",
                         '<style>@import "https://evil.example/x.css";</style>'):
            with self.subTest(external=external):
                r = self._write(self._GOOD + external)
                self.assertTrue(any("off-machine" in f for f in r.failures), r.failures)

    def test_fails_on_an_empty_catch_block(self):
        r = self._write(self._GOOD + "<script>try{x()}catch(e){}</script>")
        self.assertTrue(any("empty catch" in f for f in r.failures), r.failures)


class TestHooksWiringOnRepo(unittest.TestCase):

    def test_validator_passes_on_repo(self):
        r = validate.Result()
        validate.validate_hooks_wiring(r)
        self.assertEqual(r.failures, [], r.failures)
        self.assertIn("hooks-wiring", r.passes)


class TestHooksWiring(_PatchedTree):

    def _wire(self, script_name="a.py", *, command=None, executable=True, create=True):
        hooks_dir = self.plugin / "hooks"
        scripts = hooks_dir / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        if create:
            script = scripts / script_name
            script.write_text("#!/usr/bin/env python3\n")
            if executable:
                script.chmod(0o755)
        cmd = command or f'python3 "${{CLAUDE_PLUGIN_ROOT}}/hooks/scripts/{script_name}"'
        (hooks_dir / "hooks.json").write_text(json.dumps(
            {"hooks": {"PostToolUse": [{"matcher": "Write", "hooks": [
                {"type": "command", "command": cmd, "timeout": 5}]}]}}))

    def test_passes_when_command_resolves(self):
        self._wire()
        r = Result()
        validate.validate_hooks_wiring(r)
        self.assertEqual(r.failures, [], r.failures)

    def test_fails_when_script_missing(self):
        self._wire(create=False)
        r = Result()
        validate.validate_hooks_wiring(r)
        self.assertTrue(any("hooks-wiring" in f for f in r.failures))

    def test_fails_when_script_not_executable(self):
        self._wire(executable=False)
        r = Result()
        validate.validate_hooks_wiring(r)
        self.assertTrue(any("executable" in f for f in r.failures))

    def test_fails_when_path_outside_hooks_scripts(self):
        self._wire(command='python3 "${CLAUDE_PLUGIN_ROOT}/scripts/board.py"')
        r = Result()
        validate.validate_hooks_wiring(r)
        self.assertTrue(any("hooks-wiring" in f for f in r.failures))

    def test_fails_when_hooks_json_missing(self):
        r = Result()
        validate.validate_hooks_wiring(r)
        self.assertTrue(any("hooks-wiring" in f for f in r.failures))


_ZONE_PY_OK = '''#!/usr/bin/env python3
"""A zone-aware python hook."""
from _vault_walk import find_project_dir, is_safe_slug, resolve_vault


def main():
    slug = read_breadcrumb(project_dir).get("slug", "")
    if not slug or not is_safe_slug(slug):
        return 0
    vault = resolve_vault(project_dir)
    project_root = find_project_dir(vault, slug)
    return 0
'''

# Hardcodes the active zone. find_project_dir stays in the import line, so the
# "never resolved zone-aware" rule cannot be what fires: this pins the
# projects/<slug> construction rule on its own.
_ZONE_PY_HARDCODED = _ZONE_PY_OK.replace(
    "project_root = find_project_dir(vault, slug)",
    'project_root = vault / "projects" / slug')

# Builds a path from the slug without ever calling find_project_dir.
_ZONE_PY_NO_RESOLVER = _ZONE_PY_OK.replace(
    "from _vault_walk import find_project_dir, is_safe_slug, resolve_vault",
    "from _vault_walk import is_safe_slug, resolve_vault, safe_project_root").replace(
    "project_root = find_project_dir(vault, slug)",
    "project_root = safe_project_root(vault, slug)")

# Zone-aware, but the repo-committed slug reaches the path unvalidated.
_ZONE_PY_NO_SLUG_GUARD = _ZONE_PY_OK.replace(
    "from _vault_walk import find_project_dir, is_safe_slug, resolve_vault",
    "from _vault_walk import find_project_dir, resolve_vault").replace(
    "    if not slug or not is_safe_slug(slug):\n        return 0\n", "")

_ZONE_SH_OK = '''#!/usr/bin/env bash
zone_project_dir() {
  local vault="$1" slug="$2"
  for zone in "" "_fridge" "_archive"; do
    echo "$vault/projects/$zone/$slug"
  done
}
proot="$(zone_project_dir "$vault" "$slug")"
'''

_ZONE_SH_HARDCODED = _ZONE_SH_OK.replace(
    'proot="$(zone_project_dir "$vault" "$slug")"',
    'proot="$vault/projects/$slug"')

_ZONE_SH_NO_HELPER = '''#!/usr/bin/env bash
slug="$(sed -n 's/^slug: //p' "$breadcrumb")"
proot="$vault/active/$slug"
'''


class TestHookZoneAwarenessOnRepo(unittest.TestCase):

    def test_validator_passes_on_repo(self):
        r = validate.Result()
        validate.validate_hook_zone_awareness(r)
        self.assertEqual(r.failures, [], r.failures)
        self.assertIn("hook-zone-awareness", r.passes)


class TestHookZoneAwareness(_PatchedTree):
    """Validator 30. Added 2026-07-27 after shelf moved projects to _fridge/
    and _archive/ without touching the breadcrumb: every hook that hardcoded
    projects/<slug> grew a phantom active-zone twin and dropped every write to
    the real project."""

    def _hook(self, name: str, text: str) -> None:
        d = self.plugin / "hooks" / "scripts"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)

    def test_passes_on_zone_aware_hooks(self):
        self._hook("a.py", _ZONE_PY_OK)
        self._hook("b.sh", _ZONE_SH_OK)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertEqual(r.failures, [], r.failures)
        self.assertIn("hook-zone-awareness", r.passes)

    def test_fails_when_python_hook_hardcodes_the_active_zone(self):
        self._hook("a.py", _ZONE_PY_HARDCODED)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertTrue(any("a.py" in f and "hardcode" in f for f in r.failures),
                        r.failures)

    def test_fails_when_python_hook_never_resolves_zone_aware(self):
        self._hook("a.py", _ZONE_PY_NO_RESOLVER)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertTrue(any("a.py" in f for f in r.failures), r.failures)

    def test_fails_when_python_hook_skips_the_slug_guard(self):
        self._hook("a.py", _ZONE_PY_NO_SLUG_GUARD)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertTrue(any("unvalidated slug" in f for f in r.failures), r.failures)

    def test_fails_when_shell_hook_hardcodes_the_active_zone(self):
        self._hook("b.sh", _ZONE_SH_HARDCODED)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertTrue(any("b.sh" in f and "hardcode" in f for f in r.failures),
                        r.failures)

    def test_fails_when_shell_hook_skips_zone_project_dir(self):
        self._hook("b.sh", _ZONE_SH_NO_HELPER)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertTrue(any("b.sh" in f for f in r.failures), r.failures)

    def test_one_bad_hook_fails_the_whole_validator(self):
        # A green neighbour must not mask an offender.
        self._hook("a.py", _ZONE_PY_OK)
        self._hook("b.sh", _ZONE_SH_HARDCODED)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertTrue(any("b.sh" in f for f in r.failures), r.failures)
        self.assertNotIn("hook-zone-awareness", r.passes)

    def test_non_script_files_are_ignored(self):
        # Only .py and .sh are hooks. Prose that quotes the old shape is fine.
        self._hook("README.md", 'the old shape was vault / "projects" / slug\n')
        self._hook("a.py", _ZONE_PY_OK)
        r = Result()
        validate.validate_hook_zone_awareness(r)
        self.assertEqual(r.failures, [], r.failures)


class TestSkillSplit(unittest.TestCase):
    """v0.17.0 token discipline: background tables live in internals.md, not
    in the always-loaded router."""

    SKILL = Path(__file__).resolve().parent.parent / "skills" / "adjudant" / "SKILL.md"
    INTERNALS = (Path(__file__).resolve().parent.parent / "skills" / "adjudant"
                 / "reference" / "internals.md")

    def test_internals_exists_and_holds_the_tables(self):
        text = self.INTERNALS.read_text()
        self.assertIn("posttooluse-vault-log.py", text)   # hooks table
        self.assertIn("board_bridge.py", text)            # helper layer table
        self.assertIn("build-profile.json", text)         # environment awareness

    def test_skill_sheds_the_background_tables(self):
        text = self.SKILL.read_text()
        self.assertNotIn("posttooluse-vault-log.py", text)
        self.assertNotIn("board_bridge.py", text)

    def test_skill_still_routes_and_points_at_internals(self):
        # The verb list comes from this build's metadata, not from a tuple
        # typed here: `draw` is full-only, so a hard-coded list makes this test
        # fail in the public build for a reason that is not a defect.
        import render_verb_surfaces as rvs
        import _profile
        text = self.SKILL.read_text()
        meta = rvs.load_metadata(self.SKILL.parent.parent.parent)
        shipped = rvs.verbs_for(meta, _profile.audience())
        self.assertTrue(shipped, "this build ships no verbs at all")
        for verb in shipped:
            self.assertIn(f"`{verb['name']}`", text)
        self.assertIn("reference/internals.md", text)

    def test_skill_within_token_budget(self):
        # bytes // 4, the repo's own estimator. Target from the design spec.
        est = len(self.SKILL.read_text()) // 4
        self.assertLess(est, 2000, f"SKILL.md is ~{est} tok, budget 2000")

    def test_advisor_contract_within_budget(self):
        # Loads into every advisor-on session; same discipline as voice.md.
        doc = self.INTERNALS.parent / "advisor.md"
        est = len(doc.read_text()) // 4
        self.assertLess(est, 900, f"advisor.md is ~{est} tok, budget 900")


class TestDocTrim(unittest.TestCase):
    """v0.17.0: enforceable detail lives with its enforcer, not in prose."""

    REF = Path(__file__).resolve().parent.parent / "skills" / "adjudant" / "reference"

    def test_vault_standards_within_budget(self):
        est = len((self.REF / "vault-standards.md").read_text()) // 4
        # 2500, not the 1800 originally planned. That number was estimated before
        # the rewrite; three drafts measured a floor of 2446 with every
        # hand-authoring answer still present. Lower means deleting unenforced
        # guidance, or splitting the file and breaking the section citations in
        # clean.py, _vault_walk.py and board_bridge.py.
        self.assertLess(est, 2500, f"vault-standards.md is ~{est} tok, budget 2500")

    def test_voice_within_budget(self):
        est = len((self.REF / "voice.md").read_text()) // 4
        self.assertLess(est, 780, f"voice.md is ~{est} tok, budget 780")

    def test_voice_keeps_the_judgement_content(self):
        text = (self.REF / "voice.md").read_text()
        for keeper in ("ELI5", "ELI12", "ELICTO", "pushback"):
            self.assertIn(keeper, text)

    def test_lexicon_still_enforced_after_the_move(self):
        import validate
        self.assertTrue(hasattr(validate, "BANNED_LEXICON"))
        self.assertIn("delve", [w.lower() for w in validate.BANNED_LEXICON])


class TestModuleDocstringRoster(unittest.TestCase):
    """The module docstring is the roster people read before opening the file.
    It listed 30 validators under a trailer that still said 29, because adding
    a validator touches three places and the trailer is the easiest to miss.
    """

    def _doc(self) -> str:
        return validate.__doc__ or ""

    def _listed(self) -> list[tuple[int, str]]:
        return [(int(n), name) for n, name in
                re.findall(r"^\s*(\d+)\.\s+([a-z0-9-]+)", self._doc(), re.M)]

    def _called(self) -> list[str]:
        src = inspect.getsource(validate.main)
        return [n.replace("_", "-") for n in
                re.findall(r"^\s*validate_([a-z0-9_]+)\(r\)", src, re.M)]

    def test_trailer_count_matches_the_list(self):
        m = re.search(r"^(\d+) validators total\.$", self._doc(), re.M)
        self.assertIsNotNone(m, "the docstring must state a total")
        self.assertEqual(int(m.group(1)), len(self._listed()))

    def test_list_matches_what_main_actually_runs(self):
        listed = self._listed()
        self.assertEqual([n for n, _ in listed], list(range(1, len(listed) + 1)),
                         "the roster must be numbered 1..N with no gaps")
        self.assertEqual([name for _, name in listed], self._called(),
                         "roster names and order must match main()'s calls")


if __name__ == "__main__":
    unittest.main()

class TestAdvisorWiring(unittest.TestCase):
    """30. advisor-wiring - the opt-in advisor's three surfaces stay wired:
    the contract doc exists, the SessionStart banner names it, and the toggle
    still stamps AGENTS.md. Any one of them silently dropping out leaves a
    mode that claims to watch and does not."""

    def test_passes_on_the_real_tree(self):
        r = validate.Result()
        validate.validate_advisor_wiring(r)
        self.assertIn("advisor-wiring", r.passes)

    def test_fails_when_the_banner_is_stripped(self):
        import tempfile as _tf
        real_hook = (Path(__file__).resolve().parent.parent
                     / "hooks" / "scripts" / "session-start.sh")
        with _tf.TemporaryDirectory() as tmp:
            fake_root = Path(tmp)
            (fake_root / "hooks" / "scripts").mkdir(parents=True)
            stripped = "\n".join(l for l in real_hook.read_text().splitlines()
                                  if "Advisor" not in l and "advisor" not in l)
            (fake_root / "hooks" / "scripts" / "session-start.sh").write_text(stripped)
            ref = fake_root / "skills" / "adjudant" / "reference"
            ref.mkdir(parents=True)
            (ref / "advisor.md").write_text("# Advisor\n")
            (fake_root / "scripts").mkdir()
            (fake_root / "scripts" / "status.py").write_text(
                'AGENTS_MARKER_PREFIX = "**Adjudant advisor: on**"\n')
            orig = validate.ROOT
            validate.ROOT = fake_root
            try:
                r = validate.Result()
                validate.validate_advisor_wiring(r)
            finally:
                validate.ROOT = orig
            self.assertTrue(any("advisor-wiring" in f for f in r.failures))


class TestPlaceZoneParity(unittest.TestCase):
    """24. place-zone-parity - _place duplicates the four lifecycle folder
    names so a degraded hook can import it without _vault_walk. The outcome
    that matters is that a drifted copy fails the build, not that the happy
    path prints a tick."""

    def test_passes_on_the_real_tree(self):
        r = validate.Result()
        validate.validate_place_zone_parity(r)
        self.assertIn("place-zone-parity", r.passes)
        self.assertEqual(r.failures, [])

    def test_fails_when_the_two_lists_drift(self):
        import _place
        orig = _place._LIFECYCLE_FOLDERS
        _place._LIFECYCLE_FOLDERS = frozenset({"active", "paused", "shelved"})
        try:
            r = validate.Result()
            validate.validate_place_zone_parity(r)
        finally:
            _place._LIFECYCLE_FOLDERS = orig
        self.assertTrue(any("place-zone-parity" in f for f in r.failures))
        self.assertNotIn("place-zone-parity", r.passes,
                         "a drifted list reported a pass alongside the fail")


class TestParityValidatorsRemoved(unittest.TestCase):
    """The six validators that existed only to compare two declarations.

    Each one asked whether a Python constant, a template and a prose section
    agreed. The template is the only declaration now, so the question has no
    second half to ask about. One validator replaces all six: the templates
    parse into exactly the fifteen kinds.
    """

    def test_the_six_are_gone(self):
        src = Path(validate.__file__).read_text()
        for name in ("template-coverage", "status-vocabulary",
                     "task-status-vocabulary", "decision-status-vocabulary",
                     "template-schema-parity", "freshness-vocabulary"):
            self.assertNotIn(name, src,
                             f"{name} survived; it checks a second declaration "
                             "that no longer exists")

    def test_the_replacement_exists(self):
        src = Path(validate.__file__).read_text()
        self.assertIn("template-schema-loads", src)

    def test_declared_count_matches_reality(self):
        src = Path(validate.__file__).read_text()
        declared = int(re.search(r"(\d+) validators total", src).group(1))
        listed = len(re.findall(r"^\s*\d+\. [a-z-]+", src, re.M))
        self.assertEqual(declared, listed)


class TestPortIsSunset(unittest.TestCase):

    def test_no_port_source_survives(self):
        scripts = Path(validate.__file__).parent
        for name in ("port.py", "test_port.py"):
            self.assertFalse((scripts / name).exists(), f"{name} survived")

    def test_no_port_verb_registered(self):
        meta = json.loads((Path(validate.__file__).parent / "command-metadata.json").read_text())
        self.assertNotIn("port", [v["name"] for v in meta["verbs"]])

    def test_port_validators_are_gone(self):
        src = Path(validate.__file__).read_text()
        for name in ("port-preview-coherence", "port-backup-integrity",
                     "gitignore-includes-port-dirs"):
            self.assertNotIn(name, src, f"{name} validates a deleted verb")


class TestShelfIsSunset(unittest.TestCase):

    def test_no_shelf_source_survives(self):
        scripts = Path(validate.__file__).parent
        for name in ("shelf.py", "test_shelf.py"):
            self.assertFalse((scripts / name).exists(), f"{name} survived")

    def test_no_vault_wide_link_rewrite_remains(self):
        # Roughly fifty lines of shelf.py whose only job was repairing the
        # decision to put the lifecycle folder in every link. Plan 4 takes the
        # folder out of the links, so the repair has nothing left to repair.
        # The identifiers are the plan keys the rewrite was built and applied
        # through, not the name the plan guessed at.
        scripts = Path(validate.__file__).parent
        for py in scripts.glob("*.py"):
            if py.name.startswith("test_"):
                continue
            src = py.read_text()
            for marker in ("old_link_prefix", "new_link_prefix", "link_rewrites"):
                self.assertNotIn(marker, src,
                                 f"{py.name} still rewrites links vault-wide")


class TestStandardsStructureParity(unittest.TestCase):
    """The standards doc restated every rule in prose, and prose drifts. It
    now links to templates instead, and this holds the one thing it still has
    to state itself: the folder layout."""

    def test_the_standards_doc_names_every_folder(self):
        from _place import KIND_FOLDER
        from _vault_walk import PROJECT_ZONES
        text = (Path(__file__).resolve().parent.parent / "skills" / "adjudant"
                / "reference" / "vault-standards.md").read_text()
        for folder in sorted(set(KIND_FOLDER.values()) - {""}):
            self.assertIn(f"{folder}/", text, f"vault-standards omits {folder}/")
        for zone in PROJECT_ZONES:
            self.assertIn(f"{zone}/", text, f"vault-standards omits {zone}/")

    def test_the_standards_doc_does_not_restate_a_template(self):
        text = (Path(__file__).resolve().parent.parent / "skills" / "adjudant"
                / "reference" / "vault-standards.md").read_text()
        self.assertNotIn("required:", text,
                         "a field table here is a second declaration; link to "
                         "the template instead")

    def test_the_markdown_doc_states_one_rule_per_element(self):
        text = (Path(__file__).resolve().parent.parent / "skills" / "adjudant"
                / "reference" / "content-markdown.md").read_text()
        for element in ("Headings", "Lists", "Emphasis", "Code", "Tables",
                        "Callouts", "Links", "Mermaid", "Emoji", "Register"):
            self.assertIn(f"## {element}", text, f"no rule for {element}")


if __name__ == "__main__":
    unittest.main()
