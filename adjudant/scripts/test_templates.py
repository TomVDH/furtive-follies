"""Tests every shipped template against the v3 contract.

These assertions are the contract from the design spec's "Template
specifications" section, in code. A template that drifts from the spec fails
here rather than in the vault six weeks later.

What counts as a template is the rule templates/README.md states: a file in
that directory is a note template when it opens with `---` frontmatter.
README.md, AGENTS.md, CLAUDE.md and GEMINI.md open with prose and are agent
context files `connect` copies into a code project, not notes.
"""

import re
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "skills" / "adjudant" / "templates"

# The fifteen kinds, and nothing else.
KINDS = {
    "project", "session", "decision", "task", "note",
    "doc", "source", "spec", "handoff", "index",
    "release", "dream", "component", "api", "schema",
}

# Every field name legal anywhere in the vault.
LEGAL_FIELDS = {
    "type", "created", "updated", "session", "status",
    "verified", "verified_by", "source", "superseded_by",
    "version", "spec", "category", "related",
    # A runbook, glossary, standard and bug log are all a `doc`. This is what
    # tells them apart, and what the retired `runbook` tag used to carry.
    "doc_kind",
}

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")


def _frontmatter(path: Path) -> list:
    """The `key: value` lines of a template's frontmatter.

    Blank lines, full-line `#` comments and list continuations are skipped,
    matching what the schema parser reads.
    """
    text = path.read_text()
    if not text.startswith("---\n"):
        return []
    end = text.index("\n---", 4)
    return [ln for ln in text[4:end].splitlines()
            if ln.strip() and not ln.lstrip().startswith("#") and _KEY_RE.match(ln)]


def _field_name(line: str) -> str:
    return line.split(":", 1)[0].strip()


def _comment(line: str) -> str:
    """The trailing `# ...` rule on a frontmatter line, without the hash."""
    m = re.search(r"\s+#\s*(.*)$", line)
    return m.group(1).strip() if m else ""


def _is_optional(line: str) -> bool:
    return _comment(line).startswith("optional")


def _body_lines(path: Path) -> list:
    text = path.read_text()
    return [ln for ln in text.split("\n---", 1)[-1].splitlines() if ln.strip()]


class TestEveryTemplate(unittest.TestCase):

    def _templates(self):
        for p in sorted(TEMPLATES.glob("*.md")):
            if not p.read_text().startswith("---\n"):
                continue          # README/AGENTS/CLAUDE/GEMINI: not note templates
            yield p

    def test_the_fifteen_kinds_and_no_sixteenth(self):
        declared = set()
        for p in self._templates():
            for line in _frontmatter(p):
                if _field_name(line) == "type":
                    declared.add(line.split(":", 1)[1].split("#")[0].strip())
        self.assertEqual(declared, KINDS)

    def test_only_legal_fields(self):
        for p in self._templates():
            for line in _frontmatter(p):
                name = _field_name(line)
                self.assertIn(name, LEGAL_FIELDS,
                              f"{p.name} declares unknown field '{name}'")

    def test_type_is_a_known_kind(self):
        for p in self._templates():
            fm = _frontmatter(p)
            types = [ln for ln in fm if _field_name(ln) == "type"]
            self.assertEqual(len(types), 1, f"{p.name} needs exactly one type:")
            value = types[0].split(":", 1)[1].split("#")[0].strip()
            self.assertIn(value, KINDS, f"{p.name} declares unknown kind '{value}'")

    def test_body_outweighs_frontmatter(self):
        # README rule 3: count the required fields, count the non-blank body
        # lines, and the body wins. Optional fields do not count, because rule
        # 1 keeps them out of the written file. Pre-v3, frontmatter was 68% of
        # every non-blank content line and iteration.md was 92%.
        for p in self._templates():
            fm = len([ln for ln in _frontmatter(p) if not _is_optional(ln)])
            body = len(_body_lines(p))
            self.assertLess(fm, body,
                            f"{p.name} is {fm} required frontmatter lines "
                            f"to {body} body lines")

    def test_no_pre_written_empty_values(self):
        # README rule 1: an optional field is omitted when it has no value.
        # `superseded_by: ""` and `related: []` are the pre-v3 habit that put
        # an empty value into 181 files of the vault this was measured against.
        for p in self._templates():
            for line in _frontmatter(p):
                value = re.sub(r"\s+#.*$", "", line.split(":", 1)[1]).strip()
                self.assertNotIn(value, ('""', "''", "[]", "{}"),
                                 f"{p.name}: {line.strip()} ships an empty value")

    def test_optional_fields_show_no_value(self):
        # The comment is the whole right hand side on an optional field.
        for p in self._templates():
            for line in _frontmatter(p):
                if not _is_optional(line):
                    continue
                value = re.sub(r"\s+#.*$", "", line.split(":", 1)[1]).strip()
                self.assertEqual(value, "",
                                 f"{p.name}: optional {_field_name(line)} "
                                 "carries a default")

    def test_dates_present_on_every_kind(self):
        # The date rule: created and updated on everything, no exceptions.
        for p in self._templates():
            names = {_field_name(ln) for ln in _frontmatter(p)}
            self.assertIn("created", names, f"{p.name} has no created:")
            self.assertIn("updated", names, f"{p.name} has no updated:")

    def test_no_em_dashes(self):
        # Validator 24 enforces this on the shipped tree; asserting it here
        # means a bad template fails in the suite too, not only at commit.
        for p in self._templates():
            self.assertNotIn("—", p.read_text(), f"{p.name} has an em dash")


class TestRecordTemplates(unittest.TestCase):

    def test_decision_has_one_status_axis(self):
        fm = _frontmatter(TEMPLATES / "decision.md")
        status = [ln for ln in fm if _field_name(ln) == "status"][0]
        self.assertIn("active | superseded | reversed", status)
        for gone in ("implemented", "deferred"):
            self.assertNotIn(gone, status,
                             "decision status mixes force with progress again")

    def test_task_has_seven_statuses_and_dates(self):
        text = (TEMPLATES / "task.md").read_text()
        fm = _frontmatter(TEMPLATES / "task.md")
        status = [ln for ln in fm if _field_name(ln) == "status"][0]
        for value in ("backlog", "next", "doing", "review", "done", "icebox", "dropped"):
            self.assertIn(value, status, f"task status missing {value}")
        names = {_field_name(ln) for ln in fm}
        self.assertIn("created", names)
        self.assertIn("updated", names)
        self.assertIn("## Done when", text,
                      "task has no closing test; 44 real cards could not be closed")

    def test_session_is_one_field_plus_dates(self):
        names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / "session.md")}
        self.assertEqual(names, {"type", "created", "updated"})
        self.assertNotIn("session_id", names,
                         "one note carried 18 conversation UUIDs")

    def test_note_has_no_verified(self):
        names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / "note.md")}
        self.assertNotIn("verified", names,
                         "a note is a thought and cannot be re-checked")

    def test_source_records_where_it_came_from(self):
        names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / "source.md")}
        self.assertIn("source", names)
        self.assertIn("verified", names)


class TestDocFamily(unittest.TestCase):

    DOC_FAMILY = ("brief", "doc", "spec", "component", "api", "schema", "source")

    def test_every_doc_family_template_has_verified(self):
        # verified: is what separates a page that claims something about the
        # world from a note that is just a thought.
        for name in self.DOC_FAMILY:
            names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / f"{name}.md")}
            self.assertIn("verified", names, f"{name}.md has no verified:")
            self.assertIn("verified_by", names, f"{name}.md has no verified_by:")

    def test_verified_by_vocabulary_is_uniform(self):
        for name in self.DOC_FAMILY:
            line = [ln for ln in _frontmatter(TEMPLATES / f"{name}.md")
                    if _field_name(ln) == "verified_by"][0]
            for value in ("tested", "read", "docs"):
                self.assertIn(value, line, f"{name}.md verified_by missing {value}")

    def test_brief_has_no_status_field(self):
        # The zone folder is the status; a second answer can disagree with it.
        names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / "brief.md")}
        self.assertNotIn("status", names)
        self.assertNotIn("slug", names)
        self.assertNotIn("aliases", names)

    def test_brief_declares_kind_project_not_its_filename(self):
        # brief.md is the one template whose filename is not its kind, which
        # is why the parser reads type: and never the stem.
        fm = _frontmatter(TEMPLATES / "brief.md")
        value = [ln for ln in fm if _field_name(ln) == "type"][0]
        self.assertIn("project", value)

    def test_brief_marks_conditional_sections(self):
        text = (TEMPLATES / "brief.md").read_text()
        self.assertIn("<!-- when: coding, plugin -->", text)

    def test_the_four_brief_variants_are_gone(self):
        for variant in ("coding", "knowledge", "plugin", "tinkerage"):
            self.assertFalse((TEMPLATES / f"project-brief-{variant}.md").exists(),
                             f"project-brief-{variant}.md survived")

    def test_spec_has_three_statuses_and_scope_bounds(self):
        text = (TEMPLATES / "spec.md").read_text()
        status = [ln for ln in _frontmatter(TEMPLATES / "spec.md")
                  if _field_name(ln) == "status"][0]
        for value in ("draft", "agreed", "superseded"):
            self.assertIn(value, status)
        self.assertIn("## Out of scope", text,
                      "the section that makes a spec unambiguous")
        self.assertIn("## Done when", text)

    def test_component_declares_the_generated_half(self):
        text = (TEMPLATES / "component.md").read_text()
        names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / "component.md")}
        self.assertIn("source", names,
                      "no way to mark a page a script owns")
        for heading in ("## Diagram", "## Schema", "## Code"):
            self.assertIn(heading, text)


class TestGeneratedTemplates(unittest.TestCase):

    def test_handoff_keeps_the_statusline_banner_shape(self):
        # The statusline greps this line. It is the one place emoji are
        # meaning rather than decoration, and reference/state-contract.md
        # rule 1 holds the whole line, `· NEXT:` included, to its exact form.
        text = (TEMPLATES / "handoff.md").read_text()
        self.assertIn("handoff age:", text)
        self.assertIn("· NEXT:", text)
        for section in ("## Where I left off", "## Next", "## Context"):
            self.assertIn(section, text)

    def test_handoff_is_not_a_mirror(self):
        # 7 of 12 real handoffs were a banner and an empty body, because the
        # remember file they mirrored was empty.
        text = (TEMPLATES / "handoff.md").read_text()
        self.assertNotIn("Mirrored from", text)
        names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / "handoff.md")}
        self.assertNotIn("source", names)

    def test_dream_leads_with_a_machine_readable_count(self):
        # `N drift items` is the phrase check._DRIFT_HEADER_RE parses and the
        # statusline greps (state-contract rule 2). Any other wording leaves
        # both consumers with no count.
        text = (TEMPLATES / "dream.md").read_text()
        self.assertIn("drift items", text)
        self.assertIn("## Dismissed", text,
                      "dismissals must persist or the same finding returns")
        self.assertIn("Suppress until", text)

    def test_release_has_context_and_pointers(self):
        text = (TEMPLATES / "release.md").read_text()
        for section in ("## Changes", "## Pointers"):
            self.assertIn(section, text)
        names = {_field_name(ln) for ln in _frontmatter(TEMPLATES / "release.md")}
        self.assertIn("version", names)

    def test_retired_templates_are_gone(self):
        for name in ("dream-report", "memory", "iteration",
                     "_index-collection", "_index-projects"):
            self.assertFalse((TEMPLATES / f"{name}.md").exists(),
                             f"{name}.md survived")

    def test_index_templates_agree_on_their_kind(self):
        for name in ("home", "index-project"):
            fm = _frontmatter(TEMPLATES / f"{name}.md")
            value = [ln for ln in fm if _field_name(ln) == "type"][0]
            self.assertIn("index", value)

    def test_two_templates_of_one_kind_declare_the_same_fields(self):
        # README: "When two files declare the same kind they must declare the
        # same fields." The parser raises otherwise, so this is where it shows.
        shapes = []
        for name in ("home", "index-project"):
            fm = _frontmatter(TEMPLATES / f"{name}.md")
            shapes.append((frozenset(_field_name(ln) for ln in fm if not _is_optional(ln)),
                           frozenset(_field_name(ln) for ln in fm if _is_optional(ln))))
        self.assertEqual(shapes[0], shapes[1])


if __name__ == "__main__":
    unittest.main()
