"""Tests for _voice.py — the single source of truth for adjudant's voice.

Three surfaces enforce this contract and they must not drift apart:
repo docs (validate.py), vault writes (the PreToolUse gate), and rendered CLI
output (the render validator). One module, three consumers.

The lexicon merges no-ai-slop's banned words with adjudant's own. `harness` is
the one term that collides with legitimate technical vocabulary here, and the
exemption is recorded rather than left as an absence someone re-adds later.
"""

import re
import unittest
from pathlib import Path

import _voice


class TestLexicon(unittest.TestCase):

    def test_adjudant_originals_survive_the_merge(self):
        for term in ("delve", "leverage", "circle back", "synergy", "seamless"):
            self.assertIn(term, _voice.BANNED_LEXICON)

    def test_no_ai_slop_terms_are_present(self):
        for term in ("foster", "utilize", "robust", "paramount", "tapestry",
                     "transformative", "ever-evolving", "in order to",
                     "it's worth noting", "at its core"):
            self.assertIn(term, _voice.BANNED_LEXICON)

    def test_harness_is_exempt_with_a_recorded_reason(self):
        # 7 legitimate hits across SKILL.md and reference/ - Claude Code
        # harness, test harness. An absence would get quietly re-added.
        self.assertNotIn("harness", _voice.BANNED_LEXICON)
        self.assertIn("harness", _voice.TECHNICAL_EXEMPTIONS)
        self.assertTrue(_voice.TECHNICAL_EXEMPTIONS["harness"].strip())

    def test_no_duplicates(self):
        lowered = [t.lower() for t in _voice.BANNED_LEXICON]
        self.assertEqual(len(lowered), len(set(lowered)))

    def test_exemptions_are_never_also_banned(self):
        for term in _voice.TECHNICAL_EXEMPTIONS:
            self.assertNotIn(term, _voice.BANNED_LEXICON)


class TestScan(unittest.TestCase):

    def test_finds_a_banned_word(self):
        hits = _voice.scan("We should utilize the new parser.")
        self.assertIn(("lexicon", "utilize"), hits)

    def test_is_case_insensitive(self):
        self.assertTrue(_voice.scan("Utilize it."))

    def test_does_not_match_inside_a_longer_word(self):
        # `leveraged` is not `leverage`; the existing validator's word-boundary
        # semantics are preserved so the merge cannot widen matching.
        self.assertEqual(_voice.scan("The leveraged buyout closed."), [])

    def test_ignores_fenced_code(self):
        self.assertEqual(_voice.scan("```\nutilize()\n```\n"), [])

    def test_ignores_inline_code(self):
        self.assertEqual(_voice.scan("Call `utilize()` on it."), [])

    def test_finds_a_superficial_analysis_clause(self):
        hits = _voice.scan("The verb ships, highlighting the team's care.")
        self.assertIn("ing-analysis", [k for k, _ in hits])

    def test_finds_a_binary_contrast(self):
        hits = _voice.scan("The question isn't the model, it's the eval.")
        self.assertIn("binary-contrast", [k for k, _ in hits])

    def test_a_plain_label_colon_is_not_a_finding(self):
        # 20 false positives on real docs killed the colon-reveal check; it is
        # a judgment rule in voice.md prose, never a build failure.
        self.assertEqual(_voice.scan("Read-only views: check and sitrep."), [])


class TestBlockingSubset(unittest.TestCase):
    """The runtime gate blocks a vault write. That bar is higher than a
    validator's: a false positive there wedges the model mid-turn."""

    def test_blocking_phrases_are_a_subset_of_the_full_contract(self):
        for p in _voice.BLOCKING_PHRASES:
            self.assertTrue(
                p in _voice.BANNED_LEXICON or any(
                    p in group for group in (_voice.GLAZING_PHRASES,
                                             _voice.SHAPE_PHRASES)),
                f"{p!r} blocks writes but is in no documented list")

    def test_a_merely_banned_word_does_not_block_a_write(self):
        # `robust` is worth fixing at commit time, not worth refusing a note.
        self.assertEqual(_voice.scan_blocking("A robust parser."), [])

    def test_a_glazing_phrase_blocks(self):
        self.assertTrue(_voice.scan_blocking("Great question. Here is the note."))

    def test_blocking_ignores_code(self):
        self.assertEqual(_voice.scan_blocking("```\nGreat question\n```"), [])


class TestCorpusIsClean(unittest.TestCase):
    """The contract has to hold for adjudant's own docs, or it is decoration."""

    def test_shipped_reference_docs_pass_their_own_contract(self):
        ref = Path(__file__).resolve().parent.parent / "skills" / "adjudant"
        surfaces = ([ref / "SKILL.md"]
                    + sorted((ref / "templates").glob("*.md"))
                    + [p for p in sorted((ref / "reference").glob("*.md"))
                       if p.name != "voice.md"])
        offenders = []
        for f in surfaces:
            for kind, term in _voice.scan(f.read_text()):
                offenders.append(f"{f.name}: {kind}/{term}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
