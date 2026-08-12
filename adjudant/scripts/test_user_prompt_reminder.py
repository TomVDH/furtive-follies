"""Tests for hooks/scripts/user-prompt-reminder.sh — the smart-fire reminder.

Finding 31: the keyword regex fired on everyday English ("give me a brief
summary", "good decision"), and each session leaked one marker file into
TMPDIR forever. Precision over recall: distinctive words and phrase forms
only, and stale markers are swept when a new one is written.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "scripts" / "user-prompt-reminder.sh"


class _ReminderHarness(unittest.TestCase):

    def _run(self, prompt: str, tmp: Path, session_id: str = "sess-1") -> str:
        """Run the hook against an UNLINKED project dir; returns stdout."""
        project = tmp / "code"
        project.mkdir(exist_ok=True)
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env["TMPDIR"] = str(tmp)
        env.pop("ADJUDANT_REMINDER_DISABLE", None)
        proc = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"session_id": session_id, "prompt": prompt}),
            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0)
        return proc.stdout


class TestKeywordPrecision(_ReminderHarness):

    def test_fires_on_vault_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run("put this in the vault please", Path(tmp))
            self.assertIn("adjudant", out)

    def test_silent_on_brief_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run("give me a brief summary of the diff", Path(tmp))
            self.assertEqual(out, "")

    def test_silent_on_good_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run("good decision, ship it", Path(tmp))
            self.assertEqual(out, "")

    def test_fires_on_this_decision_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run("record this decision somewhere", Path(tmp))
            self.assertIn("adjudant", out)


class TestMarkerHygiene(_ReminderHarness):

    def test_stale_markers_are_swept_on_fire(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            stale = tmpp / "adjudant-reminder-old-session"
            stale.write_text("")
            two_days_ago = time.time() - 2 * 86400
            os.utime(stale, (two_days_ago, two_days_ago))
            out = self._run("note this in the vault", tmpp, session_id="sess-9")
            self.assertIn("adjudant", out)
            self.assertTrue((tmpp / "adjudant-reminder-sess-9").exists())
            self.assertFalse(stale.exists(),
                             "markers from past sessions must be swept")


PLACEHOLDER = "{One-line intent. Frozen after first write.}"


class TestIntentNag(unittest.TestCase):
    """The intent-line nag moved here from SessionStart.

    At SessionStart it fired before the session had a purpose to record, and
    re-fired on every resume and compact - twice in three hours, both times
    too early to act on. A prompt has been submitted by the time this hook
    runs, so the purpose exists; and it stops the moment the placeholder is
    replaced, which is what makes it self-extinguishing rather than nagging.
    """

    def _run(self, tmp: Path, prompt: str = "carry on",
             session_id: str = "sess-i", note: str = PLACEHOLDER,
             pointer: bool = True) -> str:
        project = tmp / "code"
        (project / ".claude").mkdir(parents=True, exist_ok=True)
        (project / ".claude" / "adjudant").write_text(
            "vault_path: %s\nslug: demo\n" % (tmp / "vault"))
        sessions = tmp / "vault" / "projects" / "demo" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        session_file = sessions / "2026-08-04.md"
        session_file.write_text("# Session\n\n## Intent\n\n> %s\n" % note)
        if pointer:
            (tmp / ("adjudant-session-" + session_id)).write_text(
                str(session_file) + "\n")
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env["TMPDIR"] = str(tmp)
        env.pop("ADJUDANT_REMINDER_DISABLE", None)
        proc = subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"session_id": session_id, "prompt": prompt}),
            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(proc.returncode, 0)
        return proc.stdout

    def test_silent_on_the_first_prompt(self):
        # The complaint: it fired before the purpose was settled.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(Path(tmp)), "")

    def test_nags_once_the_session_has_a_purpose(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            self._run(tmpp)
            self.assertIn("Intent line", self._run(tmpp))

    def test_self_extinguishes_once_the_intent_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            self._run(tmpp, note="Close the four field-report findings.")
            self.assertEqual(
                self._run(tmpp, note="Close the four field-report findings."), "")

    def test_nags_at_most_once_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            self._run(tmpp)
            self.assertIn("Intent line", self._run(tmpp))
            self.assertEqual(self._run(tmpp), "")

    def test_silent_when_no_session_note_was_resolved(self):
        # SessionStart no-ops on an unresolvable vault; this must not invent
        # a nag about a file nobody located.
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            self._run(tmpp, pointer=False)
            self.assertEqual(self._run(tmpp, pointer=False), "")

    def test_a_linked_project_still_gets_no_connect_reminder(self):
        # The two nags have inverse audiences; carrying both must not cross
        # them over.
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            self._run(tmpp)
            self.assertNotIn("/adjudant connect",
                             self._run(tmpp, prompt="put this in the vault"))


if __name__ == "__main__":
    unittest.main()
