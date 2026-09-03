"""Tests for _render.py, the single writer for every mechanical vault write.

Six code paths hand-built markdown from string literals, and four carried a
hardcoded copy of a template for when the file was missing. Each was a second
declaration waiting to drift. The one in board_bridge had drifted far enough
that a captured task carried `code: ""` and `note: ""`, fields no v3 kind has,
and the shipped template's guidance comments needed stripping before the card
ids were usable.

A missing template raises here. A loud failure is recoverable; the quiet
plausible substitute is what filled the vault.
"""

import unittest

from _render import frontmatter, render


class TestRender(unittest.TestCase):

    def test_required_fields_are_filled(self):
        out = render("decision", {
            "created": "2026-09-01", "updated": "2026-09-01", "status": "active"})
        self.assertIn("type: decision", out)
        self.assertIn("created: 2026-09-01", out)
        self.assertIn("status: active", out)

    def test_empty_optional_fields_are_omitted(self):
        # 181 fields in the real vault held nothing but an empty string.
        out = render("decision", {
            "created": "2026-09-01", "updated": "2026-09-01", "status": "active",
            "superseded_by": "", "session": ""})
        self.assertNotIn("superseded_by:", out)
        self.assertNotIn("session:", out)

    def test_present_optional_fields_are_kept(self):
        out = render("decision", {
            "created": "2026-09-01", "updated": "2026-09-01",
            "status": "active", "session": "4f2a"})
        self.assertIn("session: 4f2a", out)

    def test_no_guidance_comments_survive(self):
        # The frontmatter parser keeps a trailing comment on a quoted value,
        # which is how template guidance ended up poisoning card ids.
        out = render("task", {
            "created": "2026-09-01", "updated": "2026-09-01", "status": "doing"})
        front = out.split("---")[1]
        self.assertNotIn("#", front)

    def test_body_placeholders_are_substituted(self):
        out = render("decision",
                     {"created": "2026-09-01", "updated": "2026-09-01",
                      "status": "active"},
                     {"What was decided": "Bucket-A tags go"})
        self.assertIn("# Bucket-A tags go", out)

    def test_unsubstituted_placeholders_survive_for_a_human(self):
        out = render("decision", {
            "created": "2026-09-01", "updated": "2026-09-01", "status": "active"})
        self.assertIn("{What was decided}", out)

    def test_missing_template_raises(self):
        with self.assertRaises(FileNotFoundError):
            render("no-such-kind", {})

    def test_every_shipped_kind_renders(self):
        from _template_schema import FIELD_SCHEMA
        for kind in FIELD_SCHEMA:
            out = render(kind, {"created": "2026-09-01", "updated": "2026-09-01"})
            self.assertTrue(out.startswith("---\n"), kind)


class TestNoValuelessLines(unittest.TestCase):
    """README rule 1, enforced: a written file never carries an empty field."""

    def test_no_optional_field_is_written_bare(self):
        from _template_schema import FIELD_SCHEMA
        for kind in FIELD_SCHEMA:
            out = render(kind, {"created": "2026-09-01", "updated": "2026-09-01"})
            front = out.split("\n---", 1)[0]
            for line in front.splitlines()[1:]:
                if not line.strip():
                    continue
                key, _, value = line.partition(":")
                self.assertTrue(value.strip(), f"{kind}: `{line}` has no value")
                self.assertNotIn('""', value, f"{kind}: `{line}` is an empty string")


class TestFrontmatterOnly(unittest.TestCase):
    """A writer that supplies its own body still gets its frontmatter here.

    The handoff mirror mirrors `.remember/`; it does not want the
    template's body, and it used to declare the shape inline instead.
    """

    def test_frontmatter_is_a_closed_block(self):
        out = frontmatter("handoff", {"created": "2026-09-01", "updated": "2026-09-01"})
        self.assertEqual(out, "---\ntype: handoff\ncreated: 2026-09-01\n"
                              "updated: 2026-09-01\n---\n")

    def test_frontmatter_carries_no_body(self):
        out = frontmatter("decision", {
            "created": "2026-09-01", "updated": "2026-09-01", "status": "active"})
        self.assertNotIn("What was decided", out)

    def test_frontmatter_missing_template_raises(self):
        with self.assertRaises(FileNotFoundError):
            frontmatter("no-such-kind", {})


class TestKindComesFromTypeNotFilename(unittest.TestCase):

    def test_project_renders_from_brief_md(self):
        # The filename is `brief.md`; the kind is `project`. Nothing may key
        # a template off its filename.
        out = render("project", {"created": "2026-09-01", "updated": "2026-09-01",
                                 "verified": "2026-09-01"})
        self.assertIn("type: project", out)
        self.assertIn("verified_by: read", out)

    def test_agent_context_files_declare_no_kind(self):
        # templates/ also ships AGENTS.md, CLAUDE.md and GEMINI.md, which have
        # no frontmatter. They must never be mistaken for a note template.
        for kind in ("AGENTS", "CLAUDE", "GEMINI", "README"):
            with self.assertRaises(FileNotFoundError):
                render(kind, {})


if __name__ == "__main__":
    unittest.main()
