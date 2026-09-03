"""Tests for scripts/check_field_guide.py.

The field guide bakes the verb list into markup and is regenerated at a
release boundary, never per change. So this is a reporter and not a gate: it
tells you the boundary has arrived.

The guide marks its verb region with a comment. The checker used to match one
visual shape instead, `<div class="verb">`, which meant a restyle that still
listed every verb correctly reported every verb missing. These tests use the
marker, because the marker is the contract.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_field_guide as cfg

REPO = Path(__file__).resolve().parent.parent

SAMPLE = """<h2>The seven verbs.</h2>
<!-- VERBS:GUIDE:START -->
<table><tbody>
  <tr><td><code>connect</code></td><td>Link a project to its vault</td></tr>
  <tr><td><code>sync</code></td><td>Push current state to the vault</td></tr>
  <tr><td><code>board</code></td><td>Drag-and-drop kanban</td></tr>
</tbody></table>
<!-- VERBS:GUIDE:END -->
<p>Elsewhere the guide writes <code>status</code>, outside the region.</p>
"""


class TestParsing(unittest.TestCase):

    def test_reads_the_verbs_from_the_marked_region(self):
        self.assertEqual(cfg.baked_verbs(SAMPLE), ["connect", "sync", "board"])

    def test_code_outside_the_region_is_not_a_verb(self):
        # The guide names verbs in its prose too. Only the region counts, or
        # every inline mention would read as a card.
        self.assertNotIn("status", cfg.baked_verbs(SAMPLE))

    def test_the_markup_inside_the_region_does_not_matter(self):
        # The point of the marker: restyle freely, keep the names in <code>.
        divs = SAMPLE.replace("<table><tbody>", "<div>").replace(
            "</tbody></table>", "</div>")
        self.assertEqual(cfg.baked_verbs(divs), ["connect", "sync", "board"])

    def test_reads_the_spelled_out_count(self):
        self.assertEqual(cfg.baked_count_word(SAMPLE), "seven")

    def test_absent_markup_reports_none_rather_than_raising(self):
        self.assertEqual(cfg.baked_verbs("<p>nothing here</p>"), [])
        self.assertIsNone(cfg.baked_count_word("<p>nothing here</p>"))


class TestReport(unittest.TestCase):

    def test_a_disagreeing_guide_is_reported_line_by_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adjudant" / "scripts").mkdir(parents=True)
            (root / "adjudant" / "scripts" / "command-metadata.json").write_text(
                '{"name": "adjudant", "version": "1.0.0", "verbs": ['
                '{"name": "connect"}, {"name": "board"}], "content_references": []}\n')
            (root / "field-guide.html").write_text(SAMPLE)
            lines = cfg.report(root)
            self.assertTrue(any("sync" in ln for ln in lines))
            self.assertTrue(any("seven" in ln for ln in lines))

    def test_an_agreeing_guide_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adjudant" / "scripts").mkdir(parents=True)
            (root / "adjudant" / "scripts" / "command-metadata.json").write_text(
                '{"name": "adjudant", "version": "1.0.0", "verbs": ['
                '{"name": "connect"}, {"name": "sync"}, {"name": "board"}],'
                ' "content_references": []}\n')
            (root / "field-guide.html").write_text(
                SAMPLE.replace("The seven verbs", "The three verbs"))
            self.assertEqual(cfg.report(root), [])

    def test_a_guide_with_no_region_says_so_plainly(self):
        # It must not report every verb missing when the truth is that it
        # cannot read the guide at all.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adjudant" / "scripts").mkdir(parents=True)
            (root / "adjudant" / "scripts" / "command-metadata.json").write_text(
                '{"name": "adjudant", "version": "1.0.0", "verbs": ['
                '{"name": "connect"}], "content_references": []}\n')
            (root / "field-guide.html").write_text("<p>a guide with no marker</p>")
            lines = cfg.report(root)
            self.assertEqual(len(lines), 1)
            self.assertIn("no VERBS:GUIDE region", lines[0])

    def test_the_shipped_guide_is_checked_and_the_result_is_reported(self):
        # After task 9 the guide is five verbs behind. The test records that
        # rather than pretending otherwise: it asserts the checker runs and
        # returns a list, not that the list is empty.
        self.assertIsInstance(cfg.report(REPO), list)


if __name__ == "__main__":
    unittest.main()
