"""Tests for _template_schema.py, the parser that makes templates the schema.

The test that matters most is test_deleting_a_field_changes_the_schema: if it
ever fails, a second declaration has crept back in and the whole design has
regressed.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from _template_schema import (
    VOCAB_FOR_TYPE,
    schema_errors,
    FIELD_SCHEMA,
    HEADINGS_FOR_TYPE,
    STATUS_VALUES_FOR_TYPE,
    TEMPLATES_DIR,
    load_schema,
)


class TestParsing(unittest.TestCase):

    def _write(self, tmp: Path, name: str, text: str) -> None:
        (tmp / name).write_text(text)

    def test_bare_field_is_required(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        "---\ntype: thing\ncreated: x\n---\n\n# T\n\nbody\n")
            s = load_schema(tmp)
            self.assertEqual(s["thing"]["required"], frozenset({"type", "created"}))
            self.assertEqual(s["thing"]["optional"], frozenset())

    def test_optional_comment_makes_it_optional(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        '---\ntype: thing\nnote:    # optional\n---\n\n# T\n\nbody\n')
            s = load_schema(tmp)
            self.assertEqual(s["thing"]["required"], frozenset({"type"}))
            self.assertEqual(s["thing"]["optional"], frozenset({"note"}))

    def test_a_quoted_value_does_not_swallow_the_comment(self):
        """The bug the other frontmatter parser in this repo has.

        `_vault_walk._parse_minimal_yaml` strips a trailing comment only when
        the value is unquoted, so it reads `superseded_by: ""   # optional` as
        the literal string `""   # optional`. The comment is the rule here, so
        it is found whatever the value looks like.
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        '---\ntype: thing\nnote: ""    # optional\n---\n\n# T\n\nbody\n')
            s = load_schema(tmp)
            self.assertEqual(s["thing"]["optional"], frozenset({"note"}))

    def test_pipe_comment_is_a_required_vocabulary(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        "---\ntype: thing\nstatus: a    # a | b | c\n---\n\n# T\n\nbody\n")
            s = load_schema(tmp)
            self.assertIn("status", s["thing"]["required"])
            self.assertEqual(s["thing"]["vocab"]["status"], ("a", "b", "c"))

    def test_optional_vocabulary(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        "---\ntype: thing\nk: a    # optional: a | b\n---\n\n# T\n\nbody\n")
            s = load_schema(tmp)
            self.assertIn("k", s["thing"]["optional"])
            self.assertEqual(s["thing"]["vocab"]["k"], ("a", "b"))

    def test_headings_are_collected(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        "---\ntype: thing\n---\n\n# T\n\n## One\n\nx\n\n## Two\n\ny\n")
            s = load_schema(tmp)
            self.assertEqual(s["thing"]["headings"], ("One", "Two"))

    def test_conditional_headings_are_separated(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        "---\ntype: thing\n---\n\n# T\n\n## Always\n\nx\n\n"
                        "## Sometimes\n<!-- when: coding, plugin -->\n\ny\n")
            s = load_schema(tmp)
            self.assertEqual(s["thing"]["headings"], ("Always",))
            self.assertEqual(s["thing"]["conditional"]["Sometimes"], ("coding", "plugin"))

    def test_a_placeholder_heading_is_not_a_required_section(self):
        """README: braces mark a span a writer replaces.

        doc.md's `## {Section}` names the sections a writer chooses. Collecting
        it would require every doc to carry a heading literally called
        `{Section}`.
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "thing.md",
                        "---\ntype: thing\n---\n\n# T\n\n## {Section}\n\nx\n\n## Real\n\ny\n")
            s = load_schema(tmp)
            self.assertEqual(s["thing"]["headings"], ("Real",))

    def test_two_files_one_kind_must_agree(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "a.md", "---\ntype: index\nx: 1\n---\n\n# A\n\nbody\n")
            self._write(tmp, "b.md", "---\ntype: index\nx: 2\n---\n\n# B\n\nbody\n")
            load_schema(tmp)   # same key set: fine

    def test_two_files_one_kind_disagreeing_is_recorded_not_raised(self):
        # This used to raise. It must not: raising out of load_schema took the
        # whole schema down, which took _vault_walk down, which made the write
        # gate allow everything silently. A disagreement is still never
        # silently merged, it is reported and the first shape stands.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "a.md", "---\ntype: index\nx: 1\n---\n\n# A\n\nbody\n")
            self._write(tmp, "b.md", "---\ntype: index\ny: 2\n---\n\n# B\n\nbody\n")
            self._write(tmp, "c.md", "---\ntype: note\nz: 3\n---\n\n# C\n\nbody\n")
            schema = load_schema(tmp)
            self.assertIn("note", schema, "a disagreement took out an unrelated kind")
            errors = schema_errors(tmp)
            self.assertTrue(any("different fields" in e for e in errors),
                            f"the disagreement was silently merged: {errors}")

    def test_two_shapes_of_one_kind_keep_only_the_headings_they_share(self):
        """README: a heading belongs to the template, not to the kind.

        A kind with two templates has two legal shapes and a file matches one
        of them, so the only heading the kind can require is one both shapes
        carry. The union would fail every file of either shape.
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "a.md",
                        "---\ntype: index\n---\n\n# A\n\n## Shared\n\nx\n\n## Only A\n\ny\n")
            self._write(tmp, "b.md",
                        "---\ntype: index\n---\n\n# B\n\n## Shared\n\nx\n\n## Only B\n\ny\n")
            s = load_schema(tmp)
            self.assertEqual(s["index"]["headings"], ("Shared",))

    def test_readme_is_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "README.md", "# Templates\n\nprose, no frontmatter\n")
            self._write(tmp, "thing.md", "---\ntype: thing\n---\n\n# T\n\nbody\n")
            self.assertEqual(set(load_schema(tmp)), {"thing"})

    def test_a_file_with_no_frontmatter_is_not_a_template(self):
        """The rule README.md states, and the reason AGENTS.md parses as nothing.

        templates/ also ships the agent-context files `connect` copies into a
        code project. They open with prose, so they declare no kind.
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write(tmp, "AGENTS.md", "# {Project Name}\n\nprose\n")
            self._write(tmp, "thing.md", "---\ntype: thing\n---\n\n# T\n\nbody\n")
            self.assertEqual(set(load_schema(tmp)), {"thing"})


class TestShippedSchema(unittest.TestCase):

    def test_all_fifteen_kinds_load(self):
        expected = {
            "project", "session", "decision", "task", "note",
            "doc", "source", "spec", "handoff", "index",
            "release", "dream", "component", "api", "schema",
        }
        self.assertEqual(set(FIELD_SCHEMA), expected)

    def test_status_vocabularies_are_derived(self):
        self.assertEqual(STATUS_VALUES_FOR_TYPE["decision"],
                         ("active", "superseded", "reversed"))
        self.assertEqual(STATUS_VALUES_FOR_TYPE["task"],
                         ("backlog", "next", "doing", "review",
                          "done", "icebox", "dropped"))
        self.assertEqual(STATUS_VALUES_FOR_TYPE["spec"],
                         ("draft", "agreed", "superseded"))

    def test_optional_fields_are_derived(self):
        self.assertEqual(FIELD_SCHEMA["decision"]["required"],
                         frozenset({"type", "created", "updated", "status"}))
        self.assertEqual(FIELD_SCHEMA["decision"]["optional"],
                         frozenset({"superseded_by", "session"}))

    def test_headings_come_from_the_templates(self):
        self.assertEqual(HEADINGS_FOR_TYPE["decision"], ("Why", "Consequence"))
        # doc.md's one heading is `## {Section}`, a placeholder.
        self.assertEqual(HEADINGS_FOR_TYPE["doc"], ())
        # home.md and index-project.md share no heading, so index requires none.
        self.assertEqual(HEADINGS_FOR_TYPE["index"], ())
        # brief.md's two conditional sections are not required of every project.
        self.assertEqual(HEADINGS_FOR_TYPE["project"], ("Where things are",))

    def test_deleting_a_field_changes_the_schema(self):
        """The whole design in one test.

        Removing a line from a template must change what the schema accepts,
        with no Python edit anywhere. If this fails, a second declaration
        exists and the pre-v3 drift is back.
        """
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for p in TEMPLATES_DIR.glob("*.md"):
                shutil.copy2(p, tmp / p.name)
            before = load_schema(tmp)
            self.assertIn("status", before["decision"]["required"])

            target = tmp / "decision.md"
            target.write_text("\n".join(
                ln for ln in target.read_text().splitlines()
                if not ln.startswith("status:")) + "\n")

            after = load_schema(tmp)
            self.assertNotIn("status", after["decision"]["required"])


class TestEveryVocabularyIsExported(unittest.TestCase):
    """STATUS_VALUES_FOR_TYPE exported one vocabulary and dropped the rest, so
    `verified_by: banana` passed everything while the template plainly said
    `tested | read | docs`. A parsed vocabulary nothing reads is a rule that
    does not exist.
    """

    def test_verified_by_is_exported_for_every_kind_that_declares_it(self):
        for kind in ("api", "schema", "component", "spec", "doc", "source"):
            self.assertEqual(VOCAB_FOR_TYPE[kind]["verified_by"],
                             ("tested", "read", "docs"), kind)

    def test_status_is_in_the_same_map(self):
        self.assertEqual(VOCAB_FOR_TYPE["spec"]["status"],
                         ("draft", "agreed", "superseded"))

    def test_doc_kind_is_a_parsed_vocabulary(self):
        self.assertEqual(VOCAB_FOR_TYPE["doc"]["doc_kind"],
                         ("runbook", "glossary", "standard", "bug-log"))
        self.assertIn("doc_kind", FIELD_SCHEMA["doc"]["optional"])
        self.assertNotIn("doc_kind", FIELD_SCHEMA["doc"]["required"])


if __name__ == "__main__":
    unittest.main()


class TestOneBadFileCannotDisableTheSchema(unittest.TestCase):
    """An adversarial prover refuted the write gate after plan 2 landed.

    Before the inversion FIELD_SCHEMA was a literal dict and no file could take
    it out. After it, enforcement depended on all sixteen markdown files
    parsing, in a directory the design explicitly invites people to edit. A
    stray scratch file, or one deleted `---`, made load_schema raise at module
    scope, which made _vault_walk unimportable, which made the PreToolUse gate
    hit `except Exception: _READY = False` and allow every write, silently,
    with nothing on stderr.

    Proven by the prover against real payloads: with one stray file in
    templates/, all four canonical rejects flipped BLOCK to ALLOW.

    A bad file must cost you that file, never the schema.
    """

    def _real_copy(self, tmp: Path) -> Path:
        import shutil
        d = tmp / "templates"
        d.mkdir()
        for p in TEMPLATES_DIR.glob("*.md"):
            shutil.copy2(p, d / p.name)
        return d

    def test_a_stray_unparseable_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._real_copy(Path(t))
            (d / "zz-scratch.md").write_text("---\ntitle: scratch\n---\n\nnotes\n")
            schema = load_schema(d)          # must not raise
            self.assertEqual(len(schema), 15,
                             "a stray file changed the number of kinds")
            self.assertIn("decision", schema)

    def test_a_broken_fence_costs_only_that_kind(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._real_copy(Path(t))
            broken = d / "decision.md"
            broken.write_text(broken.read_text().replace("---\n\n# ", "\n\n# ", 1))
            schema = load_schema(d)          # must not raise
            self.assertIn("task", schema, "one bad file took out the others")

    def test_parse_failures_are_recorded_not_swallowed(self):
        with tempfile.TemporaryDirectory() as t:
            d = self._real_copy(Path(t))
            (d / "zz-scratch.md").write_text("---\ntitle: scratch\n---\n\nx\n")
            errors = schema_errors(d)
            self.assertTrue(any("zz-scratch" in e for e in errors),
                            f"the failure was silent: {errors}")

    def test_an_empty_directory_raises_rather_than_enforcing_nothing(self):
        # The missing-directory case did not even raise: glob returned nothing,
        # FIELD_SCHEMA became {}, and the gate ran "successfully" while
        # enforcing nothing at all. An empty schema is a broken install.
        with tempfile.TemporaryDirectory() as t:
            empty = Path(t) / "gone"
            empty.mkdir()
            with self.assertRaises(ValueError):
                load_schema(empty)

    def test_a_missing_directory_raises(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                load_schema(Path(t) / "does-not-exist")


class TestSingleValueVocabulary(unittest.TestCase):
    """A one-word comment reads as the strictest possible rule and enforced
    nothing, because _parse_rule only built a vocabulary when it saw a pipe.

    Found by an adversarial prover: with `status: active  # active` in
    decision.md, 'decision' vanished from STATUS_VALUES_FOR_TYPE, a note
    written with `status: banana` came back clean, and validate.py still said
    29 green, because the validator only rejected an EMPTY vocabulary and a
    missing one is not empty.
    """

    def _w(self, tmp: Path, name: str, text: str) -> None:
        (tmp / name).write_text(text)

    def test_a_single_value_comment_is_a_vocabulary_of_one(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._w(tmp, "thing.md",
                    "---\ntype: thing\nstatus: active    # active\n---\n\n# T\n\nbody\n")
            s = load_schema(tmp)
            self.assertEqual(s["thing"]["vocab"].get("status"), ("active",))

    def test_a_single_value_optional_comment_still_binds(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._w(tmp, "thing.md",
                    "---\ntype: thing\nk: a    # optional: a\n---\n\n# T\n\nbody\n")
            s = load_schema(tmp)
            self.assertIn("k", s["thing"]["optional"])
            self.assertEqual(s["thing"]["vocab"].get("k"), ("a",))

    def test_prose_after_a_field_is_not_a_vocabulary(self):
        # The comment convention must not turn every explanatory note into an
        # enum of its own words.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._w(tmp, "thing.md",
                    "---\ntype: thing\nupdated: 2026-01-01    # bumped on every write\n"
                    "---\n\n# T\n\nbody\n")
            s = load_schema(tmp)
            self.assertIsNone(s["thing"]["vocab"].get("updated"))
