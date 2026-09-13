"""The ops flash writer, scripts/_ops_flash.py.

The key is the contract. Writer and reader must build the same filename.
A relative `--project-dir .` once keyed the flash under `.`.
test_statusline.TestOpsFlash covers the reader half of the agreement.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
HOOKS = SCRIPTS.parent / "hooks" / "scripts"

import _ops_flash  # noqa: E402


class TestKey(unittest.TestCase):

    def test_slashes_and_spaces_fold(self):
        self.assertEqual(_ops_flash.flash_key("/Users/t/My Repo"), "_Users_t_My-Repo")

    def test_only_the_last_120_characters_survive(self):
        long = "/" + "a" * 200
        k = _ops_flash.flash_key(long)
        self.assertEqual(len(k), 120)
        self.assertEqual(k, "a" * 120)

    def test_relative_path_keys_as_its_absolute_self(self):
        with tempfile.TemporaryDirectory() as tmp:
            here = Path(tmp).resolve()
            old = os.getcwd()
            os.chdir(here)
            try:
                self.assertEqual(_ops_flash.flash_key("."), _ops_flash.flash_key(here))
                self.assertNotEqual(_ops_flash.flash_key("."), ".")
            finally:
                os.chdir(old)

    def test_symlinked_path_keys_as_the_physical_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp).resolve() / "real"
            real.mkdir()
            link = Path(tmp).resolve() / "link"
            link.symlink_to(real)
            self.assertEqual(_ops_flash.flash_key(link), _ops_flash.flash_key(real))

    def test_a_worktree_keys_under_its_main_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp).resolve() / "main"
            (main / ".git").mkdir(parents=True)
            wt = Path(tmp).resolve() / "wt"
            (wt / "sub").mkdir(parents=True)
            (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/wt\n")
            self.assertEqual(_ops_flash.flash_key(wt), _ops_flash.flash_key(main))
            # and from a subdirectory inside the worktree
            self.assertEqual(_ops_flash.flash_key(wt / "sub"), _ops_flash.flash_key(main))

    def test_a_submodule_pointer_is_not_folded(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp).resolve() / "sub"
            sub.mkdir()
            (sub / ".git").write_text("gitdir: ../.git/modules/sub\n")
            self.assertEqual(_ops_flash.main_checkout(str(sub)), str(sub))

    def test_a_real_worktree_folds_to_the_string_the_bar_folds_to(self):
        # Same .git file, same strip. Both sides read what git wrote.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve() / "repo"
            repo.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], env=env,
                                          capture_output=True, text=True)
            g("init", "-q", "-b", "main")
            g("config", "user.email", "t@t"); g("config", "user.name", "t")
            g("config", "commit.gpgsign", "false")
            (repo / "a").write_text("a"); g("add", "-A"); g("commit", "-qm", "one")
            wt = repo / ".worktrees" / "x"
            g("worktree", "add", "-q", str(wt), "-b", "feature/x")
            pointer = (wt / ".git").read_text().split(":", 1)[1].strip()
            expect = pointer.rsplit("/.git/worktrees/", 1)[0]
            self.assertEqual(_ops_flash.main_checkout(str(wt.resolve())), expect)
            self.assertEqual(_ops_flash.flash_key(wt), _ops_flash.flash_key(expect))


class TestWriter(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve() / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.project = Path(self._tmp.name).resolve() / "proj"
        self.project.mkdir()
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home
        self._tmp.cleanup()

    def _file(self) -> Path:
        return self.home / ".claude" / "statusline-cache" / f"ops-{_ops_flash.flash_key(self.project)}"

    def test_writes_ts_and_message_on_one_line(self):
        before = int(time.time())
        self.assertTrue(_ops_flash.ops_flash("board: reseeded", self.project))
        ts, msg = self._file().read_text().split(" ", 1)
        self.assertGreaterEqual(int(ts), before)
        self.assertEqual(msg, "board: reseeded\n")

    def test_creates_the_cache_dir_under_an_existing_claude_dir(self):
        self.assertFalse((self.home / ".claude" / "statusline-cache").exists())
        _ops_flash.ops_flash("x", self.project)
        self.assertTrue((self.home / ".claude" / "statusline-cache").is_dir())

    def test_no_claude_dir_means_no_write(self):
        (self.home / ".claude").rmdir()
        self.assertFalse(_ops_flash.ops_flash("x", self.project))
        self.assertFalse((self.home / ".claude").exists())

    def test_a_newer_flash_replaces_the_older_and_drops_its_seen_line(self):
        _ops_flash.ops_flash("first", self.project)
        with self._file().open("a") as f:
            f.write("seen 1\n")
        _ops_flash.ops_flash("second", self.project)
        lines = self._file().read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith(" second"))

    def test_message_is_flattened_to_one_line(self):
        _ops_flash.ops_flash("tests\n195   OK", self.project)
        self.assertEqual(self._file().read_text().split(" ", 1)[1], "tests 195 OK\n")

    def test_empty_message_writes_nothing(self):
        self.assertFalse(_ops_flash.ops_flash("   ", self.project))
        self.assertFalse(self._file().exists())

    def test_defaults_to_claude_project_dir(self):
        old = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.project)
        try:
            _ops_flash.ops_flash("from a hook")
        finally:
            if old is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = old
        self.assertTrue(self._file().exists())

    def test_cli_writes_the_same_file(self):
        r = subprocess.run(["python3", str(SCRIPTS / "_ops_flash.py"),
                            "--project-dir", str(self.project), "PR", "#7", "opened"],
                           env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._file().read_text().endswith(" PR #7 opened\n"))

    def test_bash_helper_delegates_to_the_writer(self):
        # The helper is sourced, then called with a message and a dir.
        r = subprocess.run(
            ["bash", "-c", f'source "{HOOKS / "_ops_flash.sh"}"; ops_flash "via bash" "$1"',
             "_", str(self.project)],
            env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._file().read_text().endswith(" via bash\n"))

    def test_bash_helper_falls_back_to_claude_project_dir(self):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        r = subprocess.run(
            ["bash", "-c", f'source "{HOOKS / "_ops_flash.sh"}"; ops_flash "hooked"'],
            env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._file().read_text().endswith(" hooked\n"))

    def test_bash_helper_without_a_dir_writes_nothing(self):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        subprocess.run(
            ["bash", "-c", f'source "{HOOKS / "_ops_flash.sh"}"; ops_flash "nowhere"'],
            env=env, capture_output=True, text=True)
        self.assertEqual(list((self.home / ".claude").rglob("ops-*")), [])


if __name__ == "__main__":
    unittest.main()
