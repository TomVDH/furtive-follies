"""Tests for scripts/check_field_guide.py.

The field guide is 1.4 MB of embedded screenshots and it bakes the verb list
into markup. It is regenerated at a release boundary, never per change, so this
is a reporter and not a gate: it tells you the boundary has arrived.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_field_guide as cfg

REPO = Path(__file__).resolve().parent.parent

SAMPLE = """<h4>The adjudant has seven <span style="color:var(--zt-accent-1)">verbs</span></h4>
<div class="verbs">
  <div class="verb"><code>connect</code><span>Link a project to its vault</span></div>
  <div class="verb"><code>sync</code><span>Push current state to the vault</span></div>
  <div class="verb"><code>board</code><span>Drag-and-drop kanban</span></div>
</div>
"""


class TestParsing(unittest.TestCase):

    def test_reads_the_baked_verb_cards(self):
        self.assertEqual(cfg.baked_verbs(SAMPLE), ["connect", "sync", "board"])

    def test_reads_the_baked_count_word_across_the_span(self):
        # The number and the word "verbs" are separated by a styled span, which
        # is why a plain "seven verbs" search finds nothing.
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
                SAMPLE.replace("has seven", "has three"))
            self.assertEqual(cfg.report(root), [])

    def test_the_shipped_guide_is_checked_and_the_result_is_reported(self):
        # After task 9 the guide is five verbs behind. The test records that
        # rather than pretending otherwise: it asserts the checker runs and
        # returns a list, not that the list is empty.
        self.assertIsInstance(cfg.report(REPO), list)


if __name__ == "__main__":
    unittest.main()
