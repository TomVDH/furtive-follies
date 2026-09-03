"""Tests for the drift canary.

A codeword is stated once at session start and printed at the end of every
reply. When it stops appearing, the model has stopped honouring an instruction
it was given minutes ago, and nothing else in the session is trustworthy.

The rule the design rests on is that the word is NEVER restated. A per-turn
re-assertion would keep the model printing it and the canary would measure
nothing.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks" / "scripts"


def _run(payload: dict, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["TMPDIR"] = str(home)
    env.pop("OB_VAULT", None)
    return subprocess.run(
        ["python3", str(HOOKS / "stop-canary.py")],
        env=env, input=json.dumps(payload),
        capture_output=True, text=True, timeout=15)


class TestCanary(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _state(self, sid: str, **fields) -> Path:
        p = self.home / f"adjudant-canary-{sid}.json"
        base = {"word": "GRAMERCY", "turns": 0, "hits": 0,
                "misses": 0, "blocked": False}
        base.update(fields)
        p.write_text(json.dumps(base))
        return p

    def test_word_present_records_a_hit(self):
        p = self._state("s1")
        r = _run({"session_id": "s1",
                  "last_assistant_message": "Did the thing.\n\nGRAMERCY"}, self.home)
        self.assertEqual(r.returncode, 0)
        st = json.loads(p.read_text())
        self.assertEqual(st["hits"], 1)
        self.assertEqual(st["misses"], 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_first_miss_blocks(self):
        p = self._state("s2")
        r = _run({"session_id": "s2",
                  "last_assistant_message": "Did the thing."}, self.home)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("GRAMERCY", out["reason"])
        st = json.loads(p.read_text())
        self.assertEqual(st["misses"], 1)
        self.assertTrue(st["blocked"])

    def test_the_miss_survives_a_successful_block(self):
        # The signal must survive coercion. If a block makes the retry succeed
        # and the miss were then forgotten, the counter would read clean
        # through exactly the degradation it exists to catch.
        p = self._state("s3")
        _run({"session_id": "s3", "last_assistant_message": "no word"}, self.home)
        _run({"session_id": "s3", "last_assistant_message": "ok GRAMERCY"}, self.home)
        st = json.loads(p.read_text())
        self.assertEqual(st["misses"], 1)
        self.assertEqual(st["hits"], 1)

    def test_second_miss_reports_and_does_not_block(self):
        p = self._state("s4", blocked=True, misses=1)
        r = _run({"session_id": "s4", "last_assistant_message": "still no word"},
                 self.home)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")
        st = json.loads(p.read_text())
        self.assertEqual(st["misses"], 2)

    def test_the_word_must_be_near_the_end(self):
        # Quoting the instruction mid-message is not compliance.
        self._state("s5")
        r = _run({"session_id": "s5",
                  "last_assistant_message":
                      "I was told to end with GRAMERCY.\n\n" + ("filler line\n" * 40)},
                 self.home)
        self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_no_state_file_is_a_noop(self):
        r = _run({"session_id": "unknown", "last_assistant_message": "hi"}, self.home)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "")

    def test_hostile_session_id_writes_nothing(self):
        r = _run({"session_id": "../escape", "last_assistant_message": "hi"}, self.home)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(list(self.home.glob("**/*escape*")), [])

    def test_malformed_stdin_exits_zero(self):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.home)
        r = subprocess.run(["python3", str(HOOKS / "stop-canary.py")],
                           env=env, input="not json",
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0)


class TestTheWordIsStatedOnce(unittest.TestCase):

    def test_the_per_turn_hook_never_names_the_word(self):
        # The rule the whole design rests on. A re-assertion keeps the model
        # printing the word and the canary measures nothing.
        src = (HOOKS / "user-prompt-reminder.sh").read_text()
        self.assertNotIn("CANARY_WORDS", src)
        self.assertNotIn("canary word", src.lower())

    def test_session_start_emits_the_word_once(self):
        src = (HOOKS / "session-start.sh").read_text()
        self.assertEqual(src.count('"$canary_word"'), 1,
                         "the word reaches the context block more than once")


if __name__ == "__main__":
    unittest.main()
