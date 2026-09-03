"""Tests for renest_memory.py — undo the frontmatter flattening.

A Claude Code auto-memory note is `name` / `description` / `metadata.type`.
Something flattened `metadata.type` up to a top-level `type:` on 9 of 50 files
in one real vault, which made adjudant read them as project briefs. The value was
PRESERVED by that flattening, so the repair is a mechanical re-nest, not a
reconstruct-from-content — provided nothing strips `name:`/`description:`
first.

The bar for touching a file is deliberately high: this runs over a real vault.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "renest_memory.py"

FLAT = """---
name: prefers-agents-md
description: Canonical repo context lives in AGENTS.md, not CLAUDE.md
type: project
---

We keep canonical repo context in AGENTS.md. **Why:** Claude-only files fork.
"""

NESTED = """---
name: prefers-agents-md
description: Canonical repo context lives in AGENTS.md, not CLAUDE.md
metadata:
  type: project
---

We keep canonical repo context in AGENTS.md. **Why:** Claude-only files fork.
"""

REAL_BRIEF = """---
type: project
project_type: coding
slug: demo
aliases:
  - Demo
status: active
created: 2026-01-01
updated: 2026-01-02
tags:
  - project
---

# Demo
"""


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=30)


class _Harness(unittest.TestCase):

    def _dir(self, tmp, **files):
        d = Path(tmp)
        for name, text in files.items():
            p = d / name.replace("__", "/")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        return d


class TestDetection(_Harness):

    def test_flattened_memory_is_renested(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": FLAT})
            r = _run("apply", str(d))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((d / "memory" / "a.md").read_text(), NESTED)

    def test_a_real_project_brief_is_never_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"brief.md": REAL_BRIEF})
            _run("apply", str(d))
            self.assertEqual((d / "brief.md").read_text(), REAL_BRIEF)

    def test_a_file_that_already_has_metadata_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": NESTED})
            _run("apply", str(d))
            self.assertEqual((d / "memory" / "a.md").read_text(), NESTED)

    def test_name_and_description_without_type_is_left_alone(self):
        # Nothing was flattened here; there is no type to put back.
        src = "---\nname: x\ndescription: y\n---\n\nBody.\n"
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": src})
            _run("apply", str(d))
            self.assertEqual((d / "memory" / "a.md").read_text(), src)

    def test_an_unrecognised_type_value_is_left_alone(self):
        # metadata.type's vocabulary is fixed; `type: decision` on a file with
        # name/description is some other problem, not this one.
        src = "---\nname: x\ndescription: y\ntype: decision\n---\n\nBody.\n"
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": src})
            _run("apply", str(d))
            self.assertEqual((d / "memory" / "a.md").read_text(), src)

    def test_every_metadata_type_value_is_handled(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = {f"memory__{t}.md": FLAT.replace("type: project", f"type: {t}")
                     for t in ("user", "feedback", "project", "reference")}
            d = self._dir(tmp, **files)
            _run("apply", str(d))
            for t in ("user", "feedback", "project", "reference"):
                self.assertIn(f"metadata:\n  type: {t}\n",
                              (d / "memory" / f"{t}.md").read_text())


class TestSafety(_Harness):

    def test_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": FLAT})
            r = _run("preview", str(d))
            self.assertEqual((d / "memory" / "a.md").read_text(), FLAT)
            self.assertIn("a.md", r.stdout)
            self.assertFalse(list(d.glob(".renest-backup*")))

    def test_apply_backs_up_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": FLAT})
            _run("apply", str(d))
            backups = list(d.glob(".renest-backup/**/a.md"))
            self.assertEqual(len(backups), 1, "no backup written")
            self.assertEqual(backups[0].read_text(), FLAT)

    def test_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": FLAT})
            _run("apply", str(d))
            once = (d / "memory" / "a.md").read_text()
            r = _run("apply", str(d))
            self.assertEqual((d / "memory" / "a.md").read_text(), once)
            self.assertIn("0", r.stdout)

    def test_body_and_field_order_survive_byte_for_byte(self):
        src = ("---\ndescription: y\nname: x\ntype: user\n---\n\n"
               "Body with `code`, a [[wikilink]], and trailing space.  \n\n"
               "## Heading\n")
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": src})
            _run("apply", str(d))
            out = (d / "memory" / "a.md").read_text()
            self.assertTrue(out.startswith("---\ndescription: y\nname: x\n"))
            self.assertIn("Body with `code`, a [[wikilink]], and trailing space.  \n",
                          out)
            self.assertNotIn("\ntype: user\n---", out)

    def test_crlf_line_endings_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = d / "memory" / "a.md"
            p.parent.mkdir(parents=True)
            p.write_bytes(FLAT.replace("\n", "\r\n").encode())
            _run("apply", str(d))
            raw = p.read_bytes()
            self.assertNotIn(b"\r\r", raw)
            self.assertIn(b"metadata:\r\n  type: project\r\n", raw)

    def test_a_backup_dir_is_never_rescanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, **{"memory__a.md": FLAT})
            _run("apply", str(d))
            r = _run("apply", str(d))
            self.assertNotIn(".renest-backup", r.stdout)


if __name__ == "__main__":
    unittest.main()
