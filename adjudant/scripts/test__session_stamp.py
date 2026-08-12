"""Tests for _session_stamp.py — session_id list + source_session scalar."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _session_stamp import add_to_session_id_list, stamp_source_session

UUID1 = "2ada03ff-687f-4a82-9e1f-1234567890ab"
UUID2 = "abcd1234-5678-90ef-1234-567890abcdef"


class TestSessionIdList(unittest.TestCase):

    def _session(self, tmp: Path, frontmatter: str) -> Path:
        f = tmp / "2026-06-26.md"
        f.write_text(f"---\n{frontmatter}---\n\n> intent\n\n## Log\n")
        return f

    def test_creates_session_id_block_when_field_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._session(Path(tmp), "type: session\ndate: 2026-06-26\ntags:\n  - session\n")
            self.assertTrue(add_to_session_id_list(f, UUID1))
            text = f.read_text()
            self.assertIn("session_id:", text)
            self.assertIn(f"  - {UUID1}", text)
            # Body preserved
            self.assertIn("## Log", text)
            self.assertIn("> intent", text)

    def test_fills_inline_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._session(Path(tmp), "type: session\nsession_id: []\ntags:\n  - session\n")
            self.assertTrue(add_to_session_id_list(f, UUID1))
            text = f.read_text()
            self.assertIn(f"  - {UUID1}", text)
            self.assertNotIn("session_id: []", text)

    def test_appends_to_existing_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._session(Path(tmp), f"type: session\nsession_id:\n  - {UUID1}\ntags:\n  - session\n")
            self.assertTrue(add_to_session_id_list(f, UUID2))
            text = f.read_text()
            self.assertIn(f"  - {UUID1}", text)
            self.assertIn(f"  - {UUID2}", text)

    def test_idempotent_when_uuid_already_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._session(Path(tmp), f"type: session\nsession_id:\n  - {UUID1}\ntags:\n  - session\n")
            before = f.read_text()
            self.assertFalse(add_to_session_id_list(f, UUID1))
            self.assertEqual(f.read_text(), before)

    def test_inline_list_with_items_converts_to_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._session(Path(tmp), f"type: session\nsession_id: [{UUID1}]\ntags:\n  - session\n")
            self.assertTrue(add_to_session_id_list(f, UUID2))
            text = f.read_text()
            self.assertIn("session_id:\n", text)
            self.assertIn(f"  - {UUID1}", text)
            self.assertIn(f"  - {UUID2}", text)
            self.assertNotIn("[", text.split("---")[1])  # no inline bracket left in fm

    def test_refuses_empty_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._session(Path(tmp), "type: session\ntags:\n  - session\n")
            self.assertFalse(add_to_session_id_list(f, ""))

    def test_no_frontmatter_safe_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "bare.md"
            f.write_text("plain markdown, no frontmatter\n")
            self.assertFalse(add_to_session_id_list(f, UUID1))

    def test_missing_file_safe_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(add_to_session_id_list(Path(tmp) / "nope.md", UUID1))


    def test_quoted_block_item_dedupes(self):
        # Regression: `- "uuid"` evaded the idempotency check -> duplicate rows
        with tempfile.TemporaryDirectory() as tmp:
            f = self._session(Path(tmp), f'type: session\nsession_id:\n  - "{UUID1}"\ntags:\n  - session\n')
            self.assertFalse(add_to_session_id_list(f, UUID1))
            self.assertEqual(f.read_text().count(UUID1), 1)


class TestSourceSessionStamp(unittest.TestCase):

    def _decision(self, tmp: Path, name: str = "2026-06-26-pick-x.md") -> Path:
        d = tmp / "decisions"
        d.mkdir()
        f = d / name
        f.write_text(
            "---\ntype: decision\nproject: \"[[projects/x/brief|x]]\"\n"
            "status: active\ntags:\n  - decision\n---\n\n## Decision\n\nBody.\n"
        )
        return f

    def test_stamps_decision_with_source_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._decision(Path(tmp))
            self.assertTrue(stamp_source_session(f, UUID1))
            text = f.read_text()
            self.assertIn(f"source_session: {UUID1}", text)
            # Blank line before body preserved
            self.assertIn("---\n\n## Decision", text)

    def test_idempotent_when_already_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._decision(Path(tmp))
            self.assertTrue(stamp_source_session(f, UUID1))
            before = f.read_text()
            self.assertFalse(stamp_source_session(f, UUID2))
            self.assertEqual(f.read_text(), before)

    def test_skips_session_note_in_sessions_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "sessions"
            d.mkdir()
            f = d / "2026-06-26.md"
            f.write_text("---\ntype: session\ntags:\n  - session\n---\nbody\n")
            self.assertFalse(stamp_source_session(f, UUID1))

    def test_skips_handoff_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("_handoff.md", "_index.md", "_index-projects.md", "_iteration.md"):
                f = Path(tmp) / name
                f.write_text(f"---\ntype: x\n---\nbody\n")
                self.assertFalse(stamp_source_session(f, UUID1), f"should skip {name}")

    def test_no_frontmatter_safe_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "decisions" / "no-fm.md"
            f.parent.mkdir()
            f.write_text("plain markdown, no frontmatter\n")
            self.assertFalse(stamp_source_session(f, UUID1))

    def test_empty_uuid_safe_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "notes" / "n.md"
            f.parent.mkdir()
            f.write_text("---\ntype: note\n---\n\nbody\n")
            self.assertFalse(stamp_source_session(f, ""))

    def test_missing_file_safe_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(stamp_source_session(Path(tmp) / "notes" / "nope.md", UUID1))

    def test_preserves_existing_fields_and_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "notes" / "n.md"
            f.parent.mkdir()
            f.write_text(
                "---\ntype: note\nproject: \"[[projects/x/brief|x]]\"\n"
                "tags:\n  - note\n---\n\n# Title\n\nLine 1\nLine 2\n"
            )
            self.assertTrue(stamp_source_session(f, UUID1))
            text = f.read_text()
            for line in ("type: note", "project: \"[[projects/x/brief|x]]\"",
                         "tags:", "  - note", "# Title", "Line 1", "Line 2"):
                self.assertIn(line, text)
            self.assertIn(f"source_session: {UUID1}", text)


class TestByteSafetyAndConcurrency(unittest.TestCase):
    """Audit findings 18/25/31: lost updates under concurrency, CRLF newline
    translation, decode/permission raises against the safe-skip contract,
    symlinked targets, and the EOF-fence grammar drift."""

    def test_concurrent_session_id_stamps_lose_no_uuid(self):
        # Finding 18: bare read-modify-write measured 19 of 30 concurrent
        # session-id updates lost. Four real processes, eight stamps each,
        # barrier-released: all 32 must land.
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            f = tmpp / "2026-06-26.md"
            f.write_text("---\ntype: session\ndate: 2026-06-26\n---\n\n## Log\n")
            runner = tmpp / "stamp.py"
            runner.write_text(
                "import os, sys, time\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                "from pathlib import Path\n"
                "from _session_stamp import add_to_session_id_list\n"
                "f, tag, go = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n"
                "while not os.path.exists(go):\n"
                "    time.sleep(0.001)\n"
                "for i in range(8):\n"
                "    add_to_session_id_list(f, f'{tag}-uuid-{i:04d}')\n")
            go = tmpp / "go"
            procs = [subprocess.Popen(
                        [sys.executable, str(runner), str(f), f"p{n}", str(go)])
                     for n in range(4)]
            go.write_text("")
            for p in procs:
                self.assertEqual(p.wait(), 0)
            stamped = [ln for ln in f.read_text().splitlines()
                       if ln.strip().startswith("- p")]
            self.assertEqual(len(stamped), 32,
                             f"{32 - len(stamped)} stamped UUIDs lost")

    def test_crlf_session_file_is_skipped_untouched(self):
        # Finding 25: read_text() translated CRLF so the file was stamped and
        # rewritten entirely as LF. A CRLF file is not adjudant-shaped: skip,
        # byte-identical.
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "2026-06-26.md"
            raw = b"---\r\ntype: session\r\ndate: 2026-06-26\r\n---\r\n\r\n## Log\r\n"
            f.write_bytes(raw)
            self.assertFalse(add_to_session_id_list(f, UUID1))
            self.assertEqual(f.read_bytes(), raw)

    def test_crlf_source_file_is_skipped_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "note.md"
            raw = b"---\r\ntype: note\r\n---\r\nbody\r\n"
            f.write_bytes(raw)
            self.assertFalse(stamp_source_session(f, UUID1))
            self.assertEqual(f.read_bytes(), raw)

    def test_non_utf8_file_safe_skips_on_direct_call(self):
        # Finding 25: UnicodeDecodeError escaped the direct-call API against
        # the module's documented safe-skip contract.
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "note.md"
            f.write_bytes(b"---\ntype: note\n---\ncaf\xe9\n")
            self.assertFalse(stamp_source_session(f, UUID1))
            self.assertEqual(f.read_bytes(), b"---\ntype: note\n---\ncaf\xe9\n")

    def test_unwritable_location_safe_skips_on_direct_call(self):
        # Finding 25: PermissionError escaped the direct-call API. A write
        # that cannot land is a safe-skip, and the file stays untouched.
        import os
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "locked"
            sub.mkdir()
            f = sub / "note.md"
            f.write_text("---\ntype: note\n---\nbody\n")
            os.chmod(sub, 0o555)
            try:
                self.assertFalse(stamp_source_session(f, UUID1))
                self.assertEqual(f.read_text(), "---\ntype: note\n---\nbody\n")
            finally:
                os.chmod(sub, 0o755)

    def test_symlinked_target_is_refused(self):
        # Finding 31: a stamp through a symlink writes wherever it points;
        # only the hook flow had a containment guard. Direct calls refuse.
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            real = tmpp / "real-note.md"
            real.write_text("---\ntype: note\n---\nbody\n")
            link = tmpp / "link-note.md"
            link.symlink_to(real)
            self.assertFalse(stamp_source_session(link, UUID1))
            self.assertNotIn("source_session", real.read_text())

    def test_fence_at_eof_without_trailing_newline_is_stampable(self):
        # Finding 31: parse_frontmatter accepts a closing fence at EOF with no
        # trailing newline, but the stamp grammar did not - two grammars
        # drifted. Stamping must succeed and leave a parseable file.
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "decision.md"
            f.write_text("---\ntype: decision\n---")
            self.assertTrue(stamp_source_session(f, UUID1))
            text = f.read_text()
            self.assertIn(f"source_session: {UUID1}", text)
            self.assertTrue(text.startswith("---\n"))


if __name__ == "__main__":
    unittest.main()
