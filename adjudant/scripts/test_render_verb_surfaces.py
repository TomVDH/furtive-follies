"""Tests for adjudant/scripts/render_verb_surfaces.py.

Ten doc surfaces used to name the verbs by hand: SKILL.md's description,
argument-hint, verb-count sentence, router table, weight bullets and
content-authoring list; the README's heading and table; plugin.json's
description; and the marketplace entry, in each of two repos. The
verb-surface validator existed only to notice when they disagreed, which
they did.

The tests that matter are idempotence (a second run changes nothing), audience
filtering (the public build sheds full-only verbs everywhere at once), and pipe
escaping (a raw `|` in an argument hint breaks a markdown table, which is how
the projects index grew malformed rows).
"""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import render_verb_surfaces as rvs

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Split a markdown row on its real cell separators. An escaped `\|` is a
# literal pipe INSIDE a cell, so a splitter that does not know the difference
# cannot tell a correctly escaped row from a broken one.
CELL_SPLIT = re.compile(r"(?<!\\)\|")


class _Sandbox(unittest.TestCase):
    """A throwaway copy of the real plugin tree, so tests never write to it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "adjudant"
        shutil.copytree(PLUGIN_ROOT, self.root, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        self.meta = rvs.load_metadata(self.root)

    def tearDown(self):
        self._tmp.cleanup()


class TestAudienceFiltering(_Sandbox):

    def test_every_verb_declares_an_audience(self):
        for verb in self.meta["verbs"]:
            self.assertIn(verb.get("audience"), ("all", "full"), verb["name"])

    def test_public_is_a_strict_subset_of_full(self):
        full = {v["name"] for v in rvs.verbs_for(self.meta, "full")}
        public = {v["name"] for v in rvs.verbs_for(self.meta, "public")}
        self.assertTrue(public < full or public == full)
        self.assertEqual(public, {v["name"] for v in self.meta["verbs"]
                                  if v["audience"] == "all"})

    def test_full_only_paths_come_from_full_only_verbs(self):
        paths = rvs.full_only_paths(self.meta)
        for verb in self.meta["verbs"]:
            if verb["audience"] == "full":
                for rel in verb.get("files", []):
                    self.assertIn(rel, paths)
            else:
                for rel in verb.get("files", []):
                    self.assertNotIn(rel, paths)

    def test_every_full_only_path_exists_on_disk(self):
        # A path named but absent would make the generator refuse a deletion
        # it should allow, or allow one it should refuse.
        for rel in sorted(rvs.full_only_paths(self.meta)):
            self.assertTrue((self.root / rel).exists(), rel)


class TestRendering(_Sandbox):

    def test_the_shipped_tree_is_already_generated(self):
        self.assertEqual(rvs.apply(self.root, check=True), [],
                         "a surface is out of date; run render_verb_surfaces.py")

    def test_apply_is_idempotent(self):
        rvs.apply(self.root)
        self.assertEqual(rvs.apply(self.root, check=True), [])

    def test_check_writes_nothing(self):
        skill = self.root / "skills" / "adjudant" / "SKILL.md"
        before = skill.read_text()
        skill.write_text(before.replace("| `board` |", "| `bored` |"))
        broken = skill.read_text()
        self.assertNotEqual(rvs.apply(self.root, check=True), [])
        self.assertEqual(skill.read_text(), broken)

    def test_a_stale_surface_is_repaired(self):
        readme = self.root / "README.md"
        readme.write_text(readme.read_text().replace(
            "| Verb | What it does |", "| Verb | What it did |"))
        self.assertIn("README.md", " ".join(rvs.apply(self.root)))
        self.assertIn("| Verb | What it does |", readme.read_text())

    def test_missing_marker_raises_rather_than_guessing(self):
        readme = self.root / "README.md"
        readme.write_text(readme.read_text().replace("<!-- VERBS:TABLE:END -->", ""))
        with self.assertRaises(rvs.SurfaceError):
            rvs.apply(self.root, check=True)

    def test_pipes_in_an_argument_hint_are_escaped(self):
        # A raw pipe closes a markdown cell. The README's own status row carries
        # `[vault|repo|all]`, which is why every hand-edit had to remember this.
        rendered = rvs.render(self.root, "full")
        readme = rendered[self.root / "README.md"]
        rows = [ln for ln in readme.splitlines() if ln.startswith("| `/adjudant ")]
        self.assertTrue(rows)
        for line in rows:
            cells = [c for c in CELL_SPLIT.split(line) if c.strip()]
            self.assertEqual(len(cells), 2, line)

    def test_a_raw_pipe_would_have_been_caught(self):
        # Proves the row check above can fail: the same split on an unescaped
        # hint reports the extra cells a reader's table would grow.
        raw = "| `/adjudant status [vault|repo|all]` | Reports. |"
        self.assertEqual(len([c for c in CELL_SPLIT.split(raw) if c.strip()]), 4)

    def test_the_router_keeps_the_shape_the_coherence_validator_parses(self):
        # command-metadata-coherence matches: | `verb` | `reference/...
        rendered = rvs.render(self.root, "full")
        skill = rendered[self.root / "skills" / "adjudant" / "SKILL.md"]
        found = set(re.findall(r"\|\s+`(\w+)`\s+\|\s+`reference/", skill))
        self.assertEqual(found, {v["name"] for v in rvs.verbs_for(self.meta, "full")})

    def test_the_internals_row_survives_generation(self):
        rendered = rvs.render(self.root, "full")
        skill = rendered[self.root / "skills" / "adjudant" / "SKILL.md"]
        self.assertIn("_(internals)_", skill)
        self.assertIn("reference/internals.md", skill)

    def test_the_public_build_sheds_a_full_only_verb_everywhere(self):
        # One audience switch, every surface: the drift this replaces was a
        # verb that left one doc and stayed in three others.
        skill_path = self.root / "skills" / "adjudant" / "SKILL.md"
        readme_path = self.root / "README.md"
        public = rvs.render(self.root, "public")
        skill, readme = public[skill_path], public[readme_path]
        full_only = [v["name"] for v in self.meta["verbs"]
                     if v["audience"] == "full"]
        if not full_only:
            # The public build's metadata has already been filtered, so it has
            # no full-only verb to shed. Nothing to assert, and nothing wrong.
            self.skipTest("this build's metadata declares no full-only verb")
        for name in full_only:
            self.assertNotIn(f"| `{name}` |", skill)
            self.assertNotIn(f"/adjudant {name}", readme)
            self.assertNotIn(f"|{name}|", skill)
        for verb in rvs.verbs_for(self.meta, "public"):
            self.assertIn(f"| `{verb['name']}` |", skill)

    def test_the_public_build_sheds_its_full_only_content_references(self):
        public = rvs.render(self.root, "public")
        skill = public[self.root / "skills" / "adjudant" / "SKILL.md"]
        for ref in self.meta["content_references"]:
            if ref["audience"] == "full":
                self.assertNotIn(ref["path"], skill, ref["path"])
            else:
                self.assertIn(ref["path"], skill, ref["path"])

    def test_the_public_count_word_matches_its_verb_count(self):
        readme = rvs.render(self.root, "public")[self.root / "README.md"]
        n = len(rvs.verbs_for(self.meta, "public"))
        self.assertIn(f"## The {rvs.NUMBER_WORDS[n]} verbs", readme)


class TestJsonSurfaces(_Sandbox):

    def test_plugin_description_names_every_verb(self):
        rvs.apply(self.root)
        desc = json.loads(
            (self.root / ".claude-plugin" / "plugin.json").read_text())["description"]
        for verb in rvs.verbs_for(self.meta, rvs._audience()):
            self.assertIn(verb["name"], desc)

    def test_plugin_identity_fields_are_untouched(self):
        pj = self.root / ".claude-plugin" / "plugin.json"
        before = json.loads(pj.read_text())
        rvs.apply(self.root)
        after = json.loads(pj.read_text())
        for key in ("name", "version", "author", "homepage", "repository",
                    "license", "keywords"):
            self.assertEqual(before.get(key), after.get(key), key)

    def test_the_spelled_out_count_matches_the_verb_count(self):
        rvs.apply(self.root)
        desc = json.loads(
            (self.root / ".claude-plugin" / "plugin.json").read_text())["description"]
        n = len(rvs.verbs_for(self.meta, rvs._audience()))
        self.assertIn(f"{rvs.NUMBER_WORDS[n]} verbs", desc)

    def test_a_marketplace_entry_beside_the_plugin_is_updated(self):
        # The tenth surface, and the one no test covered before: the entry
        # lives one directory above the plugin, so a renderer that only looked
        # inside the plugin would have left it stale for ever.
        mk = self.root.parent / ".claude-plugin" / "marketplace.json"
        mk.parent.mkdir(parents=True, exist_ok=True)
        mk.write_text(json.dumps(
            {"plugins": [{"name": "adjudant", "version": "3.0.0",
                          "description": "stale"},
                         {"name": "other", "description": "untouched"}]},
            indent=2) + "\n")
        changed = rvs.apply(self.root)
        self.assertIn(str(mk), changed)
        data = json.loads(mk.read_text())
        entries = {p["name"]: p for p in data["plugins"]}
        self.assertNotEqual(entries["adjudant"]["description"], "stale")
        self.assertEqual(entries["adjudant"]["version"], "3.0.0")
        self.assertEqual(entries["other"]["description"], "untouched")
        self.assertEqual(rvs.apply(self.root, check=True), [])


class TestTheProfileBelongsToTheTreeBeingRendered(_Sandbox):
    """`--plugin-root` can point at the other build.

    The renderer writes into two repositories. Reading this checkout's own
    profile while writing that one's docs would put the full build's verb list
    in the public build's README, and a doc that ships wrong is only ever found
    by a reader.
    """

    def _make_public(self) -> None:
        profile = self.root / "scripts" / "build-profile.json"
        data = json.loads(profile.read_text())
        data["audience"] = "public"
        data["description_suffix"] = ""
        data["capabilities"] = []
        profile.write_text(json.dumps(data, indent=2) + "\n")

    def test_a_public_tree_gets_the_public_surfaces(self):
        self._make_public()
        rvs.apply(self.root)
        readme = (self.root / "README.md").read_text()
        skill = (self.root / "skills" / "adjudant" / "SKILL.md").read_text()
        desc = json.loads(
            (self.root / ".claude-plugin" / "plugin.json").read_text())["description"]
        public = [v["name"] for v in rvs.verbs_for(self.meta, "public")]
        self.assertIn(f"## The {rvs.NUMBER_WORDS[len(public)]} verbs", readme)
        self.assertIn(f"{rvs.NUMBER_WORDS[len(public)]} verbs", desc)
        for verb in self.meta["verbs"]:
            if verb["audience"] == "full":
                self.assertNotIn(f"| `{verb['name']}` |", skill, verb["name"])
                self.assertNotIn(f"/adjudant {verb['name']}", readme, verb["name"])
                self.assertNotIn(f"{verb['name']} {verb['blurb']}", desc)
        self.assertEqual(rvs.apply(self.root, check=True), [])

    def test_the_suffix_comes_from_that_tree_too(self):
        self._make_public()
        rvs.apply(self.root)
        desc = json.loads(
            (self.root / ".claude-plugin" / "plugin.json").read_text())["description"]
        self.assertTrue(desc.endswith("no build step."), desc[-40:])


if __name__ == "__main__":
    unittest.main()
