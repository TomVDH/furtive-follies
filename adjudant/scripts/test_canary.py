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

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_per_turn_hook_never_names_the_word(self):
        # The rule the whole design rests on. A re-assertion keeps the model
        # printing the word and the canary measures nothing.
        src = (HOOKS / "user-prompt-reminder.sh").read_text()
        self.assertNotIn("CANARY_WORDS", src)
        self.assertNotIn("canary word", src.lower())

    def _start(self, project_dir: Path, sid: str) -> str:
        env = dict(os.environ)
        env["TMPDIR"] = str(self.home)
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
        env.pop("OB_VAULT", None)
        return subprocess.run(
            ["bash", str(HOOKS / "session-start.sh")], env=env,
            input=json.dumps({"session_id": sid, "source": "startup"}),
            capture_output=True, text=True, timeout=30).stdout

    def test_an_unlinked_project_still_gets_a_canary(self):
        # The bug this replaces: the canary was armed AFTER the breadcrumb
        # check, so a project with no vault linked got no word, no banner and
        # no drift check. The canary measures the MODEL, not the vault, and an
        # unconfigured session is where drift is least likely to be caught by
        # anything else.
        #
        # The old guard here read session-start.sh and counted a substring. It
        # passed throughout, because source text cannot tell you which branch
        # runs.
        bare = self.home / "unlinked"
        (bare / ".claude").mkdir(parents=True)
        out = self._start(bare, "unlinked-1")
        self.assertIn("Session canary:", out,
                      "a project with no vault got no drift check")
        armed = list(self.home.glob("adjudant-canary-*.json"))
        self.assertEqual(len(armed), 1, "no canary state was written")

    def test_the_word_in_the_banner_is_the_word_on_disk(self):
        import re
        bare = self.home / "p2"
        (bare / ".claude").mkdir(parents=True)
        out = self._start(bare, "match-1")
        m = re.search(r"end every message with `([A-Z]+)`", out)
        self.assertIsNotNone(m, f"no canary line in: {out[:200]!r}")
        state = json.loads(
            (self.home / "adjudant-canary-match-1.json").read_text())
        self.assertEqual(m.group(1), state["word"])

    def test_the_banner_header_appears_once(self):
        # The canary opens the block now, and the vault section has its own
        # header. A linked project must not get two.
        bare = self.home / "p3"
        (bare / ".claude").mkdir(parents=True)
        self.assertEqual(self._start(bare, "hdr-1").count("## Adjudant"), 1)

    def test_a_resume_keeps_the_same_word(self):
        bare = self.home / "p4"
        (bare / ".claude").mkdir(parents=True)
        first = self._start(bare, "resume-1")
        second = self._start(bare, "resume-1")
        import re
        w1 = re.search(r"with `([A-Z]+)`", first).group(1)
        w2 = re.search(r"with `([A-Z]+)`", second).group(1)
        self.assertEqual(w1, w2, "a resume re-rolled the word")


if __name__ == "__main__":
    unittest.main()
