"""Tests for the drift canary.

A codeword is stated once at session start and printed at the end of every
reply. When it stops appearing, the model has stopped honouring an instruction
it was given minutes ago, and nothing else in the session is trustworthy.

Two rules hold the design up, and both are guarded at the source level here
because both are the kind a well-meaning implementer restores.

The word is NEVER restated. A per-turn re-assertion would keep the model
printing it and the canary would measure nothing.

The result NEVER reaches the model. Not as a Stop-hook block, not as a per-turn
line. Both existed and both had to go: the model read the tally and wound the
session down on its own, before the user had decided anything. The canary is
read by a person, from the file on disk.
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
                "misses": 0, "streak": 0, "max_streak": 0}
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

    def test_a_miss_is_recorded_and_says_nothing(self):
        p = self._state("s2")
        r = _run({"session_id": "s2",
                  "last_assistant_message": "Did the thing."}, self.home)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "",
                         "a miss reached the model; it must only be recorded")
        st = json.loads(p.read_text())
        self.assertEqual(st["misses"], 1)
        self.assertEqual(st["streak"], 1)

    def test_the_hook_is_silent_on_every_path(self):
        # The whole point of the change. There is no turn, and no history of
        # turns, that makes this hook speak.
        self._state("s2b")
        for message in ("ok GRAMERCY", "no word", "still no word",
                        "and again", "ok GRAMERCY"):
            r = _run({"session_id": "s2b", "last_assistant_message": message},
                     self.home)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "", f"spoke on {message!r}")

    def test_a_hit_after_a_miss_keeps_the_miss(self):
        # The signal must survive recovery. If a later hit erased the miss the
        # counter would read clean through exactly the degradation it exists
        # to catch. The streak resets; the totals do not.
        p = self._state("s3")
        _run({"session_id": "s3", "last_assistant_message": "no word"}, self.home)
        _run({"session_id": "s3", "last_assistant_message": "ok GRAMERCY"}, self.home)
        st = json.loads(p.read_text())
        self.assertEqual(st["misses"], 1)
        self.assertEqual(st["hits"], 1)
        self.assertEqual(st["streak"], 0)
        self.assertEqual(st["max_streak"], 1)

    def test_consecutive_misses_build_a_streak(self):
        # A run is what separates drift from one stray turn. The totals alone
        # cannot tell them apart, which is why the run is counted.
        p = self._state("s3b")
        for _ in range(3):
            _run({"session_id": "s3b", "last_assistant_message": "nope"}, self.home)
        st = json.loads(p.read_text())
        self.assertEqual(st["streak"], 3)
        self.assertEqual(st["max_streak"], 3)
        self.assertEqual(st["misses"], 3)

    def test_a_hit_resets_the_streak_but_not_the_max(self):
        p = self._state("s4")
        for message in ("nope", "nope again", "ok GRAMERCY"):
            _run({"session_id": "s4", "last_assistant_message": message}, self.home)
        st = json.loads(p.read_text())
        self.assertEqual(st["streak"], 0)
        self.assertEqual(st["max_streak"], 2)
        self.assertEqual(st["misses"], 2)
        self.assertEqual(st["hits"], 1)

    def test_the_word_must_be_near_the_end(self):
        # Quoting the instruction mid-message is not compliance.
        p = self._state("s5")
        r = _run({"session_id": "s5",
                  "last_assistant_message":
                      "I was told to end with GRAMERCY.\n\n" + ("filler line\n" * 40)},
                 self.home)
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(json.loads(p.read_text())["misses"], 1)

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


class TestNothingSpeaksToTheModel(unittest.TestCase):
    """Source-level guards for the two rules the design rests on.

    Behaviour tests catch a regression once it runs. These catch it in the
    diff, which is where both of these were introduced in the first place.
    """

    def test_the_per_turn_hook_has_no_canary_code(self):
        # Two rules in one assertion. The hook must not restate the word (a
        # re-assertion keeps the model printing it and the canary measures
        # nothing), and it must not report the tally (the model read the
        # report and wound the session down on its own). Neither survives if
        # the word "canary" cannot appear in the file at all.
        src = (HOOKS / "user-prompt-reminder.sh").read_text()
        self.assertNotIn("canary", src.lower(),
                         "the per-turn hook is speaking about the canary again")

    def test_the_stop_hook_cannot_steer_the_model(self):
        # `decision` is the only key by which a Stop hook can block or direct
        # a reply, and a print is the only way anything leaves this hook.
        src = (HOOKS / "stop-canary.py").read_text()
        self.assertNotIn("decision", src,
                         "the Stop hook can steer the model again")
        self.assertNotIn("print(", src,
                         "the Stop hook writes to stdout again")


class TestTheWordIsStatedOnce(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

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

    def test_the_state_file_records_streaks_and_no_block_flag(self):
        # session-start.sh writes the schema stop-canary.py then updates. The
        # two files are edited apart, so the shape is asserted here.
        bare = self.home / "p5"
        (bare / ".claude").mkdir(parents=True)
        self._start(bare, "schema-1")
        state = json.loads(
            (self.home / "adjudant-canary-schema-1.json").read_text())
        self.assertEqual(set(state) - {"word"},
                         {"turns", "hits", "misses", "streak", "max_streak"})
        self.assertNotIn("blocked", state, "the block flag came back")

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
