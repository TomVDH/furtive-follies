"""Tests for adjudant/scripts/_agents_reach.py.

AGENTS.md is the first thing every agent reads and nothing keeps it true. Of
five false statements in one project's file, three were detectable without
adjudant knowing anything about the project: they named things that are not
there.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from _agents_reach import AGENTS_STALE_COMMITS, agents_reach, named_paths


class TestNamedPaths(unittest.TestCase):

    def test_backticked_paths(self):
        text = "Run `scripts/validate.py` and read `docs/plan.md`.\n"
        self.assertEqual([t for _n, t in named_paths(text)],
                         ["scripts/validate.py", "docs/plan.md"])

    def test_a_backticked_path_with_spaces_survives_whole(self):
        text = "Rules live in `~/Library/Mobile Documents/claude/hookify/`.\n"
        self.assertEqual([t for _n, t in named_paths(text)],
                         ["~/Library/Mobile Documents/claude/hookify"])

    def test_markdown_link_targets(self):
        text = "See [the spec](docs/specs/thing.md).\n"
        self.assertEqual([t for _n, t in named_paths(text)],
                         ["docs/specs/thing.md"])

    def test_fenced_code_is_tokenised_on_whitespace(self):
        text = "```bash\npython3 adjudant/scripts/validate.py --all\n```\n"
        self.assertEqual([t for _n, t in named_paths(text)],
                         ["adjudant/scripts/validate.py"])

    def test_urls_placeholders_and_globs_are_not_paths(self):
        text = ("`https://example.test/a.md` `<plugin-name>/plugin.json` "
                "`{slug}/brief.md` `hooks/*.py` `$HOME/x.sh` `--flag`\n")
        self.assertEqual(named_paths(text), [])

    def test_bare_words_are_not_paths(self):
        text = "Install `pre-commit`, then run `make`.\n"
        self.assertEqual(named_paths(text), [])

    def test_a_bare_filename_with_a_known_extension_counts(self):
        self.assertEqual([t for _n, t in named_paths("Read `AGENTS.md`.\n")],
                         ["AGENTS.md"])

    def test_line_numbers_are_reported(self):
        text = "intro\n\nRun `scripts/go.sh`.\n"
        self.assertEqual(named_paths(text), [(3, "scripts/go.sh")])

    def test_each_token_is_reported_once_per_line(self):
        text = "`a/b.py` and `a/b.py` again\n"
        self.assertEqual(named_paths(text), [(1, "a/b.py")])


class TestAgentsReach(unittest.TestCase):

    def test_absent_agents_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = agents_reach(Path(tmp))
            self.assertFalse(out["present"])
            self.assertEqual(out["missing"], [])
            self.assertEqual(out["checked"], 0)

    def test_reports_only_what_is_not_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "real.py").write_text("x")
            (root / "AGENTS.md").write_text(
                "Run `scripts/real.py`.\n\n"
                "Enforced mechanically by `scripts/enforce-branch-rule.sh`.\n")
            out = agents_reach(root)
            self.assertTrue(out["present"])
            self.assertEqual(out["checked"], 2)
            self.assertEqual([m["token"] for m in out["missing"]],
                             ["scripts/enforce-branch-rule.sh"])
            self.assertEqual(out["missing"][0]["line"], 3)

    def test_a_named_directory_counts_as_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adjudant").mkdir()
            (root / "AGENTS.md").write_text("The plugin lives in `adjudant/`.\n")
            self.assertEqual(agents_reach(root)["missing"], [])

    def test_an_absolute_path_is_checked_as_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                f"Rules live in `{root}/nowhere/rules.sh`.\n")
            self.assertEqual([m["token"] for m in agents_reach(root)["missing"]],
                             [f"{root}/nowhere/rules.sh"])

    def test_commit_count_in_a_real_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.test",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.test",
                   "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root)}

            def git(*args):
                subprocess.run(["git", "-C", str(root), *args],
                               env=env, capture_output=True, check=True)

            git("init", "-q", "-b", "main")
            (root / "AGENTS.md").write_text("Read `AGENTS.md`.\n")
            git("add", "AGENTS.md")
            git("commit", "-q", "-m", "a")
            for i in range(3):
                (root / f"f{i}.txt").write_text("x")
                git("add", f"f{i}.txt")
                git("commit", "-q", "-m", f"f{i}")
            out = agents_reach(root)
            self.assertEqual(out["commits_since_change"], 3)
            self.assertRegex(out["last_changed"], r"^\d{4}-\d{2}-\d{2}$")

    def test_outside_a_repo_the_commit_count_is_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Read `AGENTS.md`.\n")
            out = agents_reach(root)
            self.assertIsNone(out["commits_since_change"])
            self.assertIsNone(out["last_changed"])

    def test_nothing_is_written_to_agents_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = "Run `scripts/gone.py`.\n"
            (root / "AGENTS.md").write_text(original)
            agents_reach(root)
            self.assertEqual((root / "AGENTS.md").read_text(), original)

    def test_the_stale_threshold_is_named(self):
        self.assertEqual(AGENTS_STALE_COMMITS, 30)


class TestPrecisionBeatsRecall(unittest.TestCase):
    """The redesign's thesis, applied to the one check that reaches outside.

    dream produced 602 candidates and zero true positives by being generous.
    This check was written generous in the same way: run against this repo's
    own AGENTS.md it called 21 of 34 tokens missing, and every one of the 21
    was a true statement. A check in the wrong-now band that is wrong 61% of
    the time trains the reader to skip the band.

    Two causes, both tested here. It mined English prose that happened to
    contain a slash, and it resolved every token against the repo root even
    when the file plainly said the path was relative to a plugin directory.
    """

    def _repo(self):
        """This repo, or None when the tests run from a copied tree."""
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / "AGENTS.md").is_file() and (parent / ".git").exists():
                return parent
        return None

    def test_prose_with_a_slash_is_not_a_path(self):
        # Every one of these has the shape of real AGENTS.md prose: two
        # English words joined by a slash, which is not a path.
        text = ("The `acme/toolkit` repo. A vault `editor/writer`. "
                "A `Crew/persona` layer. Direct `push/PR` is not used.\n")
        self.assertEqual(named_paths(text), [],
                         "a slash between two English words is not a path")

    def test_a_slash_command_is_not_an_absolute_path(self):
        self.assertEqual(named_paths("Invoke `/adjudant` with a verb.\n"), [],
                         "a slash command is not a path on this disk")

    def test_a_basename_that_exists_deeper_is_not_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "marketplace.json").write_text("{}")
            (root / "AGENTS.md").write_text("Versions live in `marketplace.json`.\n")
            self.assertEqual(agents_reach(root)["missing"], [],
                             "the doc named the file by its basename, and it exists")

    def test_a_path_relative_to_a_plugin_dir_is_not_missing(self):
        # AGENTS.md says "<plugin>/scripts/validate.py" by writing
        # `scripts/validate.py` under a heading about plugin layout.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adjudant" / "scripts").mkdir(parents=True)
            (root / "adjudant" / "scripts" / "validate.py").write_text("#\n")
            (root / "AGENTS.md").write_text("Run `scripts/validate.py`.\n")
            self.assertEqual(agents_reach(root)["missing"], [],
                             "the path resolves under a plugin directory")

    def test_a_genuinely_missing_script_is_still_reported(self):
        # The check must not go silent to buy its precision. This is the case
        # the design doc names: AGENTS.md describing a rule as enforced by a
        # script that is not there.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "other.sh").write_text("#\n")
            (root / "AGENTS.md").write_text(
                "Enforced mechanically by `scripts/enforce-tags.sh`.\n")
            missing = [m["token"] for m in agents_reach(root)["missing"]]
            self.assertEqual(missing, ["scripts/enforce-tags.sh"])

    def test_the_stated_cost_a_vanished_parent_is_not_reported(self):
        # The limit, asserted so it stays visible rather than being discovered
        # later. When the whole parent directory is gone the token is
        # indistinguishable from a hypothetical path in prose, and one real
        # CLAUDE.md produced 24 such false findings in a single file, including
        # two where the document said the file must NOT exist. Reporting this
        # case back would reopen that.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "Enforced by `scripts/enforce-tags.sh`.\n"
                "Never put a file at `skill-name/SKILL.md`.\n")
            self.assertEqual(agents_reach(root)["missing"], [],
                             "no parent directory, so no checkable claim")

    def test_a_script_that_moved_is_still_reported(self):
        # Same basename, different parent. The doc claim about WHERE it lives
        # is false, and that is the drift worth reporting.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            (root / "tools" / "build.sh").write_text("#\n")
            (root / "scripts").mkdir()
            (root / "scripts" / "still-here.sh").write_text("#\n")
            (root / "AGENTS.md").write_text("Run `scripts/build.sh`.\n")
            missing = [m["token"] for m in agents_reach(root)["missing"]]
            self.assertEqual(missing, ["scripts/build.sh"],
                             "a suffix match is component-wise, not basename-wise")

    def test_a_vendored_copy_does_not_satisfy_a_claim(self):
        # node_modules, vendor and fixtures all preserve the tail of a real
        # path, which is why a suffix match alone accepted them as proof.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for junk in ("node_modules/pkg", "vendor/thirdparty", "tests/fixtures"):
                d = root / junk / "scripts"
                d.mkdir(parents=True)
                (d / "build.sh").write_text("#\n")
            (root / "scripts").mkdir()
            (root / "scripts" / "still-here.sh").write_text("#\n")
            (root / "AGENTS.md").write_text("Run `scripts/build.sh`.\n")
            missing = [m["token"] for m in agents_reach(root)["missing"]]
            self.assertEqual(missing, ["scripts/build.sh"])

    def test_a_route_is_not_checked_at_the_filesystem_root(self):
        # `/hero.html` in a static site AGENTS.md was tested at /hero.html.
        # One real project scored 74% false entirely on this.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "Landing is `/hero.html`, the shelf is `/shelf.html`, "
                "and the command is `/adjudant`.\n")
            self.assertEqual(agents_reach(root)["missing"], [])

    def test_a_library_name_is_not_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "```\nBuilt with Three.js and Vue.js on Node.js\n```\n")
            self.assertEqual(agents_reach(root)["missing"], [])

    def test_a_shell_command_is_not_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "Run `npm test -- auth.spec.ts` then `bash tools/check.sh`.\n")
            self.assertEqual(agents_reach(root)["missing"], [])

    def test_a_path_containing_spaces_is_still_a_path(self):
        # This plugin is developed under a path containing a space, so
        # rejecting every token with a space to kill shell commands would
        # have blinded the check to its own home.
        import _agents_reach as ar
        self.assertTrue(ar._looks_like_a_path("~/Docs/Cloud Storage/a.md"))
        self.assertFalse(ar._looks_like_a_path("npm test -- auth.spec.ts"))

    def test_a_file_url_is_not_a_path(self):
        # rstrip("/") ran before the URL test, so `file://` survived as `file:`.
        import _agents_reach as ar
        self.assertFalse(ar._looks_like_a_path("file://"))
        self.assertFalse(ar._looks_like_a_path("https://example.com/a.md"))

    def test_an_abbreviated_path_in_prose_is_not_a_claim(self):
        import _agents_reach as ar
        self.assertFalse(ar._looks_like_a_path("~/\u2026/Projects/IDE/"))

    def test_a_deleted_but_unstaged_file_is_reported(self):
        # git ls-files reads the INDEX. A file deleted but not yet staged was
        # still listed there, and the suffix match then overrode a correct
        # filesystem answer.
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*a):
                subprocess.run(["git", "-C", str(root), *a],
                               capture_output=True, check=False)
            git("init", "-q")
            git("config", "user.email", "t@e")
            git("config", "user.name", "t")
            (root / "scripts").mkdir()
            (root / "scripts" / "build.sh").write_text("#\n")
            (root / "scripts" / "keep.sh").write_text("#\n")
            (root / "AGENTS.md").write_text("Run `scripts/build.sh`.\n")
            git("add", "-A")
            git("commit", "-qm", "x")
            (root / "scripts" / "build.sh").unlink()
            missing = [m["token"] for m in agents_reach(root)["missing"]]
            self.assertEqual(missing, ["scripts/build.sh"],
                             "the filesystem is the truth, not the git index")

    def test_an_untracked_but_real_file_is_not_reported(self):
        # The same true statement used to flip between wrong-now and clean
        # purely on staging state.
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "-C", str(root), "init", "-q"],
                           capture_output=True, check=False)
            (root / "scripts").mkdir()
            (root / "scripts" / "validate.py").write_text("#\n")
            (root / "AGENTS.md").write_text("Run `scripts/validate.py`.\n")
            self.assertEqual(agents_reach(root)["missing"], [])

    def test_this_repo_own_agents_md_reports_nothing_missing(self):
        # The outcome test. Every claim in this file was verified true by
        # hand on 2026-09-02; anything reported here is a false positive.
        repo = self._repo()
        if repo is None:
            self.skipTest("not running inside the repo")
        out = agents_reach(repo)
        self.assertGreater(out["checked"], 10, "harvesting must not collapse")
        self.assertEqual(
            [m["token"] for m in out["missing"]], [],
            "every path this repo's AGENTS.md names does exist")


if __name__ == "__main__":
    unittest.main()
