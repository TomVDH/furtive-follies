"""Tests for adjudant/scripts/_cost.py."""

import json
import tempfile
import unittest
from pathlib import Path

from _cost import (
    DEFAULT_WARN_TOKENS,
    VALID_WEIGHTS,
    breadcrumb_int,
    cost_block,
    est_tokens,
    read_threshold,
    stat_walk,
    verb_weights,
)


class TestEstTokens(unittest.TestCase):

    def test_bytes_div_4(self):
        self.assertEqual(est_tokens(400), 100)

    def test_zero_and_negative(self):
        self.assertEqual(est_tokens(0), 0)
        self.assertEqual(est_tokens(-10), 0)


class TestStatWalk(unittest.TestCase):

    def test_counts_md_only_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("x" * 100)
            (root / "b.md").write_text("y" * 50)
            (root / "c.py").write_text("z" * 999)
            files, n_bytes = stat_walk(root)
            self.assertEqual(files, 2)
            self.assertEqual(n_bytes, 150)

    def test_skips_default_dirs_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".git" / "x.md").write_text("skip")
            (root / "_legacy").mkdir()
            (root / "_legacy" / "y.md").write_text("skip")
            (root / "keep.md").write_text("1234")
            files, n_bytes = stat_walk(root)
            self.assertEqual(files, 1)
            self.assertEqual(n_bytes, 4)

    def test_exts_parameter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("aaaa")
            (root / "b.py").write_text("bbbb")
            (root / "c.json").write_text("cccc")
            files, n_bytes = stat_walk(root, exts=(".md", ".py", ".json"))
            self.assertEqual(files, 3)
            self.assertEqual(n_bytes, 12)

    def test_missing_root(self):
        self.assertEqual(stat_walk(Path("/nonexistent-adjudant-test")), (0, 0))


class TestThreshold(unittest.TestCase):

    def test_default_when_no_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_threshold(Path(tmp)), DEFAULT_WARN_TOKENS)

    def test_default_when_none(self):
        self.assertEqual(read_threshold(None), DEFAULT_WARN_TOKENS)

    def test_breadcrumb_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".claude" / "adjudant").write_text(
                "vault_name: V\nslug: s\ncost_warn_tokens: 90000\n")
            self.assertEqual(read_threshold(root), 90000)

    def test_breadcrumb_garbage_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".claude" / "adjudant").write_text("cost_warn_tokens: lots\n")
            self.assertEqual(read_threshold(root), DEFAULT_WARN_TOKENS)

    def test_breadcrumb_int_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".claude" / "adjudant").write_text("stale_after_days: 45\n")
            self.assertEqual(breadcrumb_int(root, "stale_after_days", 30), 45)
            self.assertEqual(breadcrumb_int(root, "missing_key", 7), 7)


class TestCostBlock(unittest.TestCase):

    def test_below_threshold(self):
        block = cost_block(10, 4000, 30000)
        self.assertEqual(block, {
            "est_read_tokens": 1000, "files": 10, "bytes": 4000,
            "threshold": 30000, "warn": False,
        })

    def test_at_threshold_warns(self):
        block = cost_block(1, 120000, 30000)
        self.assertEqual(block["est_read_tokens"], 30000)
        self.assertTrue(block["warn"])


class TestVerbWeights(unittest.TestCase):

    def test_every_verb_has_valid_weight(self):
        weights = verb_weights()
        meta = json.loads(
            (Path(__file__).resolve().parent / "command-metadata.json").read_text())
        self.assertEqual(set(weights), {v["name"] for v in meta["verbs"]})
        for verb, w in weights.items():
            self.assertIn(w, VALID_WEIGHTS, f"{verb} has invalid weight {w!r}")

    def test_locked_heavy_set(self):
        weights = verb_weights()
        self.assertEqual(weights["dream"], "heavy")
        self.assertEqual(weights["connect"], "light")


if __name__ == "__main__":
    unittest.main()
