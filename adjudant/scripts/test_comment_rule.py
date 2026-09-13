"""The code-comments rule and the two hooks that inject it.

The rule lives once, in hooks/scripts/_comment_rule.txt.
session-start.sh prints it in the Adjudant block on every start.
user-prompt-reminder.sh prints it on every prompt.
Re-injection is the design: one statement is lost at the next compact.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks" / "scripts"
RULE_FILE = HOOKS / "_comment_rule.txt"
RULE = RULE_FILE.read_text().strip()


def _env(home: Path, project: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["TMPDIR"] = str(home)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env.pop("OB_VAULT", None)
    env.pop("ADJUDANT_REMINDER_DISABLE", None)
    env.pop("ADJUDANT_VOICE_DISABLE", None)
    return env


def _start(home: Path, project: Path, source: str, sid: str = "s1") -> str:
    r = subprocess.run(["bash", str(HOOKS / "session-start.sh")],
                       env=_env(home, project),
                       input=json.dumps({"session_id": sid, "source": source}),
                       capture_output=True, text=True, timeout=30)
    return r.stdout


def _prompt(home: Path, project: Path, prompt: str, sid: str = "s1") -> str:
    r = subprocess.run(["bash", str(HOOKS / "user-prompt-reminder.sh")],
                       env=_env(home, project),
                       input=json.dumps({"session_id": sid, "prompt": prompt}),
                       capture_output=True, text=True, timeout=15)
    return r.stdout


def _linked(tmp: Path, extra: str = "") -> tuple[Path, Path]:
    home = tmp / "home"
    (home / "vault" / "projects" / "demo").mkdir(parents=True)
    project = tmp / "code"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "adjudant").write_text(
        f"vault_path: {home / 'vault'}\nslug: demo\n{extra}")
    return home, project


class TestRuleText(unittest.TestCase):

    def test_one_line(self):
        lines = [l for l in RULE_FILE.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertTrue(RULE.startswith("Code comments: ASD-STE100, double density."))

    def test_carries_the_twelve_word_ceiling(self):
        self.assertIn("Twelve words or fewer.", RULE)

    def test_obeys_its_own_ceiling(self):
        sentences = [x.strip() for x in RULE.split(".") if x.strip()]
        self.assertGreaterEqual(len(sentences), 5)
        for sentence in sentences:
            self.assertLessEqual(len(sentence.split()), 12, sentence)

    def test_the_hooks_read_the_file_and_do_not_embed_it(self):
        for name in ("session-start.sh", "user-prompt-reminder.sh"):
            src = (HOOKS / name).read_text()
            self.assertIn("_comment_rule.txt", src, name)
            self.assertNotIn("double density", src, name)


class TestSessionStart(unittest.TestCase):

    def test_prints_the_rule_on_startup_resume_and_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = _linked(Path(tmp))
            for source in ("startup", "resume", "compact"):
                out = _start(home, project, source)
                self.assertIn("## Adjudant", out, source)
                self.assertIn(f"- {RULE}\n", out, source)

    def test_rule_follows_the_voice_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = _linked(Path(tmp))
            out = _start(home, project, "startup")
            self.assertLess(out.index("- Voice:"), out.index(f"- {RULE}"))

    def test_voice_off_keeps_the_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = _linked(Path(tmp), "voice: off\n")
            out = _start(home, project, "startup")
            self.assertNotIn("- Voice:", out)
            self.assertIn(f"- {RULE}\n", out)


class TestPromptHook(unittest.TestCase):

    def test_prints_the_rule_on_every_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = _linked(Path(tmp))
            for prompt in ("fix the parser", "now the tests", "and the docs"):
                out = _prompt(home, project, prompt)
                self.assertTrue(out.startswith(f"[adjudant] {RULE}\n"), out)

    def test_unlinked_project_gets_it_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir()
            project = Path(tmp) / "code"; project.mkdir()
            out = _prompt(home, project, "hello")
            self.assertEqual(out, f"[adjudant] {RULE}\n")

    def test_no_marker_is_written_for_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"; home.mkdir()
            project = Path(tmp) / "code"; project.mkdir()
            _prompt(home, project, "hello")
            _prompt(home, project, "hello again")
            self.assertEqual([p.name for p in home.iterdir()], [])


if __name__ == "__main__":
    unittest.main()
