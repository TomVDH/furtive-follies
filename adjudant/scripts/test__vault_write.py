"""Tests for _vault_write.py — the guard that makes clean net-subtractive.

The design defect this closes: every cleanup run wrote more into the vault
than it removed, and nothing in the code could tell the difference between
removing a tag and creating a report note. Now it can.
"""

import tempfile
import unittest
from pathlib import Path

from _vault_write import VaultCreateRefused, VaultWriteGuard


class TestGuard(unittest.TestCase):

    def test_rewrite_of_an_existing_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.md"
            p.write_text("old")
            with VaultWriteGuard(Path(t)) as g:
                g.rewrite(p, "new")
            self.assertEqual(p.read_text(), "new")

    def test_remove_is_allowed(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.md"
            p.write_text("x")
            with VaultWriteGuard(Path(t)) as g:
                g.remove(p)
            self.assertFalse(p.exists())

    def test_creating_a_new_vault_file_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with VaultWriteGuard(Path(t)) as g:
                with self.assertRaises(VaultCreateRefused):
                    g.rewrite(Path(t) / "new.md", "content")

    def test_rewrite_outside_the_root_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            outside = Path(t).parent / "escape.md"
            with VaultWriteGuard(Path(t)) as g:
                with self.assertRaises(VaultCreateRefused):
                    g.rewrite(outside, "x")

    def test_the_guard_counts_what_it_did(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / "a.md", Path(t) / "b.md"
            a.write_text("x")
            b.write_text("y")
            with VaultWriteGuard(Path(t)) as g:
                g.rewrite(a, "z")
                g.remove(b)
            self.assertEqual(g.rewritten, 1)
            self.assertEqual(g.removed, 1)
            self.assertEqual(g.created, 0)


if __name__ == "__main__":
    unittest.main()
