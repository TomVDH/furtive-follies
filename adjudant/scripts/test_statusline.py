"""The statusline shipped in adjudant/statusline/.

Real repositories, real worktrees, a throwaway HOME. The bar is rendered by
running the script the way Claude Code does (JSON on stdin) and reading the
line back with its escapes stripped. Skipped cleanly when jq is missing,
which is the one dependency the script has.

What is pinned here is behaviour a person verified in a terminal: the
worktree marker, the branch-rule glyph, the shim's resolution order, and the
absence of any path into the iCloud suitcase the script used to live in.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SL_DIR = PLUGIN_ROOT / "statusline"
STATUSLINE = SL_DIR / "statusline.sh"
SHIM = SL_DIR / "shim.sh"
INSTALL = SL_DIR / "install.sh"
REFRESHER = SL_DIR / "statusline-tokens-24h.sh"

_ANSI = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;[^\x07\x1b]*(?:\x07|\x1b\\)")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)



def _render(cwd: Path, home: Path, *, script: Path = STATUSLINE,
            extra_env: dict | None = None, sid: str = "test-sid",
            raw: bool = False, ctx_size: int | None = None,
            effort: str | None = "medium") -> str:
    payload = {
        "cwd": str(cwd),
        "workspace": {"current_dir": str(cwd), "project_dir": str(cwd)},
        "session_id": sid,
        "model": {"display_name": "Test", "id": "test"},
        "effort": {"level": effort},
        "context_window": {"used_percentage": 12},
    }
    if ctx_size is not None:
        payload["context_window"]["context_window_size"] = ctx_size
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["HOME"] = str(home)
    env["TMPDIR"] = str(home)
    env["ADJUDANT_STATUSLINE_NO_SPAWN"] = "1"
    env["ADJUDANT_PEER_COUNT"] = "0"
    env.pop("ADJUDANT_STATUSLINE", None)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(["bash", str(script)], input=json.dumps(payload),
                       env=env, capture_output=True, text=True, timeout=20)
    return r.stdout if raw else _plain(r.stdout)


def _link(url: str, label: str) -> str:
    """The OSC 8 wrap the script emits, byte for byte."""
    return f"\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\"


class _Repo(unittest.TestCase):
    """One temp HOME (with ~/.claude) and one git repo on main per test."""

    def setUp(self):
        if not shutil.which("jq"):
            self.skipTest("jq not installed; the statusline cannot parse stdin")
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.home = tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.repo = tmp / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        self._git("config", "commit.gpgsign", "false")
        (self.repo / "a.txt").write_text("one\n")
        (self.repo / ".gitignore").write_text(".worktrees/\n.claude/adjudant\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "first")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *a, at=None):
        # Inside a commit hook git exports GIT_INDEX_FILE and friends, and a
        # fixture git that inherits them writes into the REAL index. Found
        # when the twin's pre-commit ran this suite.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", "-C", str(at or self.repo), *a],
                              capture_output=True, text=True, env=env)

    def _beans(self, *rows):
        """rows: (id, type, status). Writes .beans.yml and one file per row."""
        (self.repo / ".beans.yml").write_text("beans:\n  path: .beans\n")
        d = self.repo / ".beans"
        d.mkdir(exist_ok=True)
        for bid, typ, st in rows:
            (d / f"{bid}--{bid}-slug.md").write_text(
                f"---\ntitle: {bid}\nstatus: {st}\ntype: {typ}\n---\n\nbody\n")
        # Beans are tracked, so a worktree created afterwards sees them.
        self._git("add", "-A")
        self._git("commit", "-qm", "beans")

    def _breadcrumb(self, tracker="beans"):
        (self.repo / ".claude").mkdir(exist_ok=True)
        (self.repo / ".claude" / "adjudant").write_text(
            f"vault_path: {self.home}/nope\nslug: demo\ntracker: {tracker}\n")

    def _worktree(self, bid):
        wt = self.repo / ".worktrees" / bid
        self._git("worktree", "add", "-q", str(wt), "-b", f"feature/{bid}")
        # The breadcrumb is git-ignored, so the rule says: copy it in. Without
        # it the gate stays shut and the worktree gets no glyph, ever.
        crumb = self.repo / ".claude" / "adjudant"
        if crumb.is_file():
            (wt / ".claude").mkdir(exist_ok=True)
            (wt / ".claude" / "adjudant").write_text(crumb.read_text())
        return wt

    def _bar(self, cwd=None, **kw):
        return _render(cwd or self.repo, self.home, **kw)


class TestShape(_Repo):

    def test_scripts_parse(self):
        for s in (STATUSLINE, SHIM, INSTALL, REFRESHER):
            r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{s.name}: {r.stderr}")

    def test_no_path_into_the_suitcase(self):
        # The script used to live in iCloud and call its refresher by an
        # absolute ~/.claude path. Both are gone: everything it needs sits
        # next to it.
        for s in (STATUSLINE, REFRESHER):
            text = s.read_text()
            self.assertNotIn("CloudDocs", text, s.name)
            self.assertNotIn("Mobile Documents", text, s.name)
            self.assertNotIn("$HOME/.claude/statusline-tokens", text, s.name)
            self.assertNotIn("$HOME/.claude/statusline-v2", text, s.name)
            self.assertNotIn("~/.claude/statusline-tokens", text, s.name)
        self.assertIn('"$(dirname "${BASH_SOURCE[0]}")/statusline-tokens-24h.sh"',
                      STATUSLINE.read_text())

    def test_renders_outside_a_repo(self):
        with tempfile.TemporaryDirectory() as d:
            out = self._bar(cwd=Path(d))
        self.assertIn("no git", out)
        self.assertIn("Test", out)

    def test_renders_a_clean_main(self):
        out = self._bar()
        self.assertIn("main", out)
        self.assertNotIn("no git", out)
        self.assertFalse(TestDriftGlyph._drift(out))

    def test_worktree_marker(self):
        wt = self._worktree("demo-ab12")
        self.assertIn("⑂", self._bar(cwd=wt))
        # Main checkout now shows ⑂N when worktrees exist (count indicator),
        # so only a repo with NO worktrees should be absent of ⑂.

    def test_no_worktree_marker_without_worktrees(self):
        self.assertNotIn("⑂", self._bar())

    def test_drift_paints_the_glyph_red_and_drops_the_bang(self):
        # The red is the message; it sits on the glyph, not in front of it.
        red = TestDriftGlyph.RED
        self._breadcrumb()
        self._beans(("demo-ab12", "task", "todo"))
        self._git("switch", "-qc", "feature/demo-ab12")
        out = self._bar(raw=True)
        self.assertIn(f"{red}⎇", out)
        self.assertNotIn("! ", _plain(out))
        self._beans(("demo-zz99", "feature", "completed"))
        wt = self._worktree("demo-zz99")
        out = self._bar(cwd=wt, raw=True)
        self.assertIn(f"{red}⑂", out)
        self.assertNotIn("! ", _plain(out))
        # A detached HEAD has no branch name, and the drift rules need one,
        # so it carries no mark of either kind: ⊘ and the hash, nothing red.
        self._git("switch", "-q", "main")
        self._beans(("demo-ip77", "feature", "in-progress"))
        self._git("checkout", "-q", "--detach")
        out = self._bar(raw=True)
        self.assertIn("⊘", _plain(out))
        self.assertNotIn("! ", _plain(out))
        self.assertNotIn(red, out.split("│")[1])

    def test_branch_glyph_on_a_regular_checkout(self):
        # The counterpart of ⑂: a plain checkout says so too, in the branch
        # white, so the two states read as a pair rather than mark-or-nothing.
        wt = self._worktree("demo-ab12")
        self.assertIn("⎇ main", self._bar())
        self.assertNotIn("⎇", self._bar(cwd=wt))
        self._git("checkout", "-q", "--detach")
        out = self._bar()
        self.assertIn("⊘", out)
        self.assertNotIn("⎇", out)

    def test_branch_name_links_to_the_checkout_folder(self):
        # Cmd+click on the name opens the folder the session is actually in:
        # the project dir on a plain checkout, the worktree dir inside one.
        wt = self._worktree("demo-ab12")
        self.assertIn(_link(f"file://{self.repo}", "main"),
                      self._bar(raw=True))
        self.assertIn(_link(f"file://{wt}", "feature/demo-ab12"),
                      self._bar(cwd=wt, raw=True))

    def test_extended_context_is_named_next_to_the_model(self):
        # context_window_size is 200000 by default and 1000000 on a model
        # with extended context. The bar says 1M then, and nothing otherwise:
        # not on the default size, not when the field is absent.
        self.assertIn("Test/1M", self._bar(ctx_size=1_000_000))
        self.assertNotIn("1M", self._bar(ctx_size=200_000))
        self.assertNotIn("1M", self._bar())

    def test_branch_link_percent_encodes_spaces(self):
        wt = self.repo / ".worktrees" / "demo ab12"
        self._git("worktree", "add", "-q", str(wt), "-b", "feature/sp")
        url = f"file://{wt}".replace(" ", "%20")
        self.assertIn(_link(url, "feature/sp"), self._bar(cwd=wt, raw=True))


class TestDriftGlyph(_Repo):
    """The checkout glyph (⎇ or ⑂) turns red when the repo breaks the
    branch rule; there is no separate mark. Gated on `tracker: beans`;
    other repos are never nagged."""

    RED = "\x1b[38;2;185;95;85m"

    @classmethod
    def _drift(cls, out: str) -> bool:
        # `out` is the raw bar. Drift is the glyph painted in the diff-red;
        # a healthy bar paints ⎇ white and ⑂ indigo.
        return f"{cls.RED}⎇" in out or f"{cls.RED}⑂" in out

    def _bar(self, cwd=None, **kw):
        kw.setdefault("raw", True)
        return super()._bar(cwd, **kw)

    def test_main_checkout_off_main_is_drift(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "task", "todo"))
        self._git("switch", "-qc", "feature/demo-ab12")
        self.assertTrue(self._drift(self._bar()))
        self._git("switch", "-q", "main")
        self.assertFalse(self._drift(self._bar()))

    def test_worktree_on_a_feature_branch_is_not_drift(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "in-progress"))
        wt = self._worktree("demo-ab12")
        out = self._bar(cwd=wt)
        self.assertIn("⑂", out)
        self.assertFalse(self._drift(out))
        # and the main checkout, on main with the branch present, is clean
        self.assertFalse(self._drift(self._bar()))

    def test_worktree_on_a_completed_bean_is_drift(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "completed"))
        wt = self._worktree("demo-ab12")
        self.assertTrue(self._drift(self._bar(cwd=wt)))

    def test_worktree_on_a_scrapped_bean_is_drift(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "scrapped"))
        wt = self._worktree("demo-ab12")
        self.assertTrue(self._drift(self._bar(cwd=wt)))

    def test_in_progress_feature_without_a_branch_is_drift(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "in-progress"))
        self.assertTrue(self._drift(self._bar()))
        self._git("branch", "feature/demo-ab12")
        self.assertFalse(self._drift(self._bar()))

    def test_in_progress_task_or_epic_asks_for_no_branch(self):
        self._breadcrumb()
        self._beans(("demo-t1", "task", "in-progress"), ("demo-e1", "epic", "in-progress"),
                    ("demo-b1", "bug", "in-progress"))
        self.assertFalse(self._drift(self._bar()))

    def test_no_nag_without_the_beans_tracker(self):
        self._beans(("demo-ab12", "feature", "in-progress"))
        self._git("switch", "-qc", "feature/demo-ab12")
        # no breadcrumb at all
        self.assertFalse(self._drift(self._bar()))
        # a vault-tracked repo
        self._breadcrumb(tracker="vault")
        self.assertFalse(self._drift(self._bar()))

    def test_worktree_without_its_own_breadcrumb_reads_the_main_checkouts(self):
        # .claude/adjudant is git-ignored, so a fresh worktree carries none and
        # the bar used to go dark there: no gate, no glyph, no vault. The
        # worktree's .git file names the main checkout, and the bar reads the
        # breadcrumb from there. A completed bean's worktree is drift, and the
        # glyph now shows without anyone copying anything in.
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "completed"))
        wt = self._worktree("demo-ab12")
        (wt / ".claude" / "adjudant").unlink()
        out = self._bar(cwd=wt)
        self.assertIn("⑂", out)
        self.assertTrue(self._drift(out))

    def test_worktree_whose_main_has_no_breadcrumb_stays_silent(self):
        # Nothing to fall back to: no breadcrumb anywhere, no gate, no glyph.
        self._beans(("demo-ab12", "feature", "completed"))
        wt = self._worktree("demo-ab12")
        self.assertFalse((wt / ".claude" / "adjudant").exists())
        self.assertFalse(self._drift(self._bar(cwd=wt)))

    def test_worktrees_own_breadcrumb_wins_over_the_main_checkouts(self):
        # A worktree that carries its own (a symlink from session-start, or a
        # copy) is read as-is; the fallback is for the absent case only.
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "completed"))
        wt = self._worktree("demo-ab12")
        (wt / ".claude" / "adjudant").write_text(
            f"vault_path: {self.home}/nope\nslug: demo\ntracker: vault\n")
        self.assertFalse(self._drift(self._bar(cwd=wt)))

    def test_no_nag_without_a_beans_project(self):
        self._breadcrumb()
        self._git("switch", "-qc", "feature/demo-ab12")
        self.assertFalse(self._drift(self._bar()))


class TestBeansFlash(_Repo):
    """A bean added, removed, closed or reopened flashes its delta next to
    the count for a few seconds, then the readout goes back to plain. State
    lives in the cache dir under HOME, keyed by beans dir; the first sight
    of a dir records and stays silent."""

    def _slot(self, out: str) -> str:
        seg = next((s for s in out.split("│") if "◍" in s), "")
        return seg.strip()

    def _paint(self, ttl="60"):
        return self._slot(self._bar(extra_env={"ADJUDANT_BEANS_FLASH_TTL": ttl}))

    def _write(self, bid, st="todo"):
        (self.repo / ".beans" / f"{bid}--{bid}-slug.md").write_text(
            f"---\ntitle: {bid}\nstatus: {st}\ntype: task\n---\n")

    def setUp(self):
        super().setUp()
        self._breadcrumb()
        self._beans(("demo-1", "task", "todo"), ("demo-2", "task", "todo"),
                    ("demo-3", "task", "todo"))

    def test_first_paint_is_silent_and_records(self):
        self.assertEqual(self._paint(), "↯ demo  ◍ 3")
        cache = list((self.home / ".claude" / "statusline-cache").glob("beans-*"))
        self.assertEqual(len(cache), 1)
        self.assertTrue(cache[0].read_text().startswith("3 0 3 "))

    def test_added_bean_flashes_plus(self):
        self._paint()
        self._write("demo-4")
        self.assertEqual(self._paint(), "↯ demo  ◍ 4  +1")
        # and keeps flashing on the next repaint inside the TTL
        self.assertEqual(self._paint(), "↯ demo  ◍ 4  +1")

    def test_two_added_at_once_counts_both(self):
        self._paint()
        self._write("demo-4"); self._write("demo-5")
        self.assertEqual(self._paint(), "↯ demo  ◍ 5  +2")

    def test_removed_bean_flashes_minus(self):
        self._paint()
        (self.repo / ".beans" / "demo-2--demo-2-slug.md").unlink()
        self.assertEqual(self._paint(), "↯ demo  ◍ 2  −1")

    def test_closed_bean_flashes_check(self):
        self._paint()
        self._write("demo-1", "completed")
        self.assertEqual(self._paint(), "↯ demo  ◍ 2  ✓1")

    def test_reopened_bean_flashes_arrow(self):
        self._write("demo-1", "completed")
        self._paint()
        self._write("demo-1", "todo")
        self.assertEqual(self._paint(), "↯ demo  ◍ 3  ↺1")

    def test_flash_expires(self):
        import time
        self._paint(ttl="1")
        self._write("demo-4")
        self.assertEqual(self._paint(ttl="1"), "↯ demo  ◍ 4  +1")
        time.sleep(1.2)
        self.assertEqual(self._paint(ttl="1"), "↯ demo  ◍ 4")

    def test_a_new_change_replaces_a_live_flash(self):
        self._paint()
        self._write("demo-4")
        self.assertEqual(self._paint(), "↯ demo  ◍ 4  +1")
        self._write("demo-4", "completed")
        self.assertEqual(self._paint(), "↯ demo  ◍ 3  ✓1")

    def test_unchanged_counts_do_not_flash_or_rewrite(self):
        self._paint()
        cache = next((self.home / ".claude" / "statusline-cache").glob("beans-*"))
        before = cache.stat().st_mtime_ns
        # an edit that changes no count is not news
        (self.repo / ".beans" / "demo-1--demo-1-slug.md").write_text(
            "---\ntitle: renamed\nstatus: todo\ntype: task\n---\n")
        self.assertEqual(self._paint(), "↯ demo  ◍ 3")
        self.assertEqual(cache.stat().st_mtime_ns, before)

    def test_last_open_bean_going_still_flashes(self):
        for b in ("demo-1", "demo-2", "demo-3"):
            self._write(b, "completed")
        self._write("demo-4")
        self._paint()
        (self.repo / ".beans" / "demo-4--demo-4-slug.md").unlink()
        self.assertEqual(self._paint(), "↯ demo  ◍ 0  −1")


class TestShim(_Repo):
    """~/.claude/statusline-v2.sh is a shim that execs the plugin copy."""

    def _fake_statusline(self, where: Path, tag: str) -> Path:
        where.mkdir(parents=True, exist_ok=True)
        f = where / "statusline.sh"
        f.write_text(f"#!/usr/bin/env bash\ncat >/dev/null\necho {tag}\n")
        return f

    def test_env_override_wins(self):
        f = self._fake_statusline(self.home / "override", "OVERRIDE")
        (self.home / ".claude" / "adjudant-statusline-path").write_text("/nope/statusline.sh\n")
        out = self._bar(script=SHIM, extra_env={"ADJUDANT_STATUSLINE": str(f)})
        self.assertEqual(out.strip(), "OVERRIDE")

    def test_pointer_is_followed(self):
        f = self._fake_statusline(self.home / "pointed", "POINTED")
        (self.home / ".claude" / "adjudant-statusline-path").write_text(f"{f}\n")
        self.assertEqual(self._bar(script=SHIM).strip(), "POINTED")

    def test_glob_fallback_picks_the_newest_version(self):
        cache = self.home / ".claude" / "plugins" / "cache" / "market" / "adjudant"
        self._fake_statusline(cache / "4.1.9" / "statusline", "OLD")
        self._fake_statusline(cache / "4.1.19" / "statusline", "NEW")
        self._fake_statusline(cache / "4.1.10" / "statusline", "MID")
        # a stale pointer that names a pruned version falls through
        (self.home / ".claude" / "adjudant-statusline-path").write_text(
            f"{cache}/4.1.8/statusline/statusline.sh\n")
        self.assertEqual(self._bar(script=SHIM).strip(), "NEW")

    def test_nothing_installed_says_so(self):
        out = self._bar(script=SHIM)
        self.assertIn("no adjudant statusline", out)

    def test_shim_runs_the_real_statusline(self):
        (self.home / ".claude" / "adjudant-statusline-path").write_text(f"{STATUSLINE}\n")
        out = self._bar(script=SHIM)
        self.assertIn("main", out)
        self.assertNotIn("no adjudant statusline", out)


class TestInstall(_Repo):

    def _install(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["HOME"] = str(self.home)
        return subprocess.run(["bash", str(INSTALL)], env=env,
                              capture_output=True, text=True, timeout=20)

    def test_installs_the_shim_and_the_pointer(self):
        r = self._install()
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = self.home / ".claude" / "statusline-v2.sh"
        self.assertEqual(dest.read_text(), SHIM.read_text())
        self.assertTrue(os.access(dest, os.X_OK))
        pointer = self.home / ".claude" / "adjudant-statusline-path"
        self.assertEqual(Path(pointer.read_text().strip()).resolve(), STATUSLINE.resolve())
        # no settings.json here, so it prints the block to add
        self.assertIn('"statusLine"', r.stdout)

    def test_moves_a_foreign_statusline_aside_and_overwrites_a_shim_in_place(self):
        dest = self.home / ".claude" / "statusline-v2.sh"
        dest.write_text("#!/bin/bash\necho old\n")
        self._install()
        baks = list((self.home / ".claude").glob("statusline-v2.sh.bak-*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_text(), "#!/bin/bash\necho old\n")
        # second run: the shim is recognised and overwritten, no second backup
        self._install()
        self.assertEqual(len(list((self.home / ".claude").glob("statusline-v2.sh.bak-*"))), 1)

    def test_silent_about_settings_when_already_wired(self):
        (self.home / ".claude" / "settings.json").write_text(
            '{"statusLine": {"type": "command", "command": "bash \\"$HOME/.claude/statusline-v2.sh\\""}}')
        r = self._install()
        self.assertNotIn('"statusLine"', r.stdout)


class TestPeerPresence(_Repo):
    """Circled digit ②③… shows when 2+ Claude Code sessions are active."""

    def test_no_peer_indicator_with_one_session(self):
        out = self._bar(extra_env={"ADJUDANT_PEER_COUNT": "1"})
        for g in ("②", "③", "④", "⑤"):
            self.assertNotIn(g, out)

    def test_two_sessions_shows_circled_two(self):
        out = self._bar(extra_env={"ADJUDANT_PEER_COUNT": "2"})
        self.assertIn("②", out)

    def test_five_sessions_shows_circled_five(self):
        out = self._bar(extra_env={"ADJUDANT_PEER_COUNT": "5"})
        self.assertIn("⑤", out)

    def test_nine_plus_caps_at_nine(self):
        out = self._bar(extra_env={"ADJUDANT_PEER_COUNT": "12"})
        self.assertIn("⑨+", out)

    def test_zero_sessions_shows_nothing(self):
        out = self._bar(extra_env={"ADJUDANT_PEER_COUNT": "0"})
        for g in ("②", "③", "④", "⑤", "⑨"):
            self.assertNotIn(g, out)


class TestWorktreeCount(_Repo):
    """Worktree count shows ⑂N in the git segment."""

    def test_no_worktrees_shows_no_count(self):
        out = self._bar()
        self.assertNotIn("⑂", out)

    def test_one_worktree_shows_marker_no_count(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "in-progress"))
        wt = self._worktree("demo-ab12")
        out = self._bar(cwd=wt)
        self.assertIn("⑂", out)
        self.assertNotIn("⑂2", out)

    def test_two_worktrees_shows_count(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "in-progress"),
                    ("demo-cd34", "feature", "in-progress"))
        wt1 = self._worktree("demo-ab12")
        self._worktree("demo-cd34")
        out = self._bar(cwd=wt1)
        self.assertIn("⑂2", out)

    def test_main_checkout_shows_worktree_count(self):
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "in-progress"))
        self._worktree("demo-ab12")
        out = self._bar()
        self.assertIn("⑂1", out)


class TestOpsFlash(_Repo):
    """The bolt. scripts/_ops_flash.py writes `<ts> <message>` per project.
    S0a takes the line over with ⚡ on the next repaint.
    It stamps `seen <ts>` and keeps the flash ten seconds from that.
    The writer runs for real here, CLI and bash helper, under a shared HOME.
    A key the two sides spell differently fails here, not on the user's bar."""

    WRITER = PLUGIN_ROOT / "scripts" / "_ops_flash.py"
    HELPER = PLUGIN_ROOT / "hooks" / "scripts" / "_ops_flash.sh"
    BOLT = "\x1b[38;2;100;140;185m⚡ "

    def setUp(self):
        super().setUp()
        # Claude Code hands the bar the physical path. macOS temp dirs are symlinks.
        # Paint with the physical path.
        self.root = self.repo.resolve()

    def _env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(self.home)
        env.pop("CLAUDE_PROJECT_DIR", None)
        return env

    def _flash(self, msg, project_dir=None, cwd=None):
        r = subprocess.run(["python3", str(self.WRITER), "--project-dir",
                            str(project_dir or self.root), msg],
                           env=self._env(), cwd=cwd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _files(self):
        return sorted((self.home / ".claude" / "statusline-cache").glob("ops-*"))

    def _paint(self, cwd=None, ttl="10", **kw):
        kw.setdefault("raw", True)
        return self._bar(cwd or self.root,
                         extra_env={"ADJUDANT_OPS_FLASH_TTL": ttl}, **kw)

    def test_a_flash_takes_the_whole_line(self):
        self._flash("commit feat(x): y")
        out = self._paint()
        self.assertTrue(out.startswith(self.BOLT + "commit feat(x): y"), out)
        self.assertEqual(out.count("\n"), 1)
        self.assertNotIn("│", _plain(out))

    def test_first_paint_stamps_seen(self):
        self._flash("pushed main")
        (f,) = self._files()
        self.assertEqual(len(f.read_text().splitlines()), 1)
        self._paint()
        lines = f.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[1], r"^seen \d+$")

    def test_shows_again_on_the_next_repaint(self):
        self._flash("tests 195 OK")
        self.assertIn("⚡ tests 195 OK", self._paint())
        (f,) = self._files()
        seen = f.read_text().splitlines()[1]
        self.assertIn("⚡ tests 195 OK", self._paint())
        # the seen stamp is written once, not moved on every repaint
        self.assertEqual(f.read_text().splitlines()[1], seen)

    def test_ttl_counts_from_first_sight_not_from_the_write(self):
        # Written 30s ago, never painted: it still shows.
        # The old rule expired it before anyone looked.
        self._flash("board: reseeded")
        (f,) = self._files()
        ts, rest = f.read_text().split(" ", 1)
        f.write_text(f"{int(ts) - 30} {rest}")
        self.assertIn("⚡ board: reseeded", self._paint(ttl="8"))

    def test_gone_after_the_ttl_from_seen(self):
        import time
        self._flash("commit x")
        self.assertIn("⚡ commit x", self._paint(ttl="1"))
        time.sleep(1.2)
        out = self._paint(ttl="1")
        self.assertNotIn("⚡", out)
        self.assertIn("⎇", _plain(out))  # the ordinary bar is back

    def test_a_newer_flash_replaces_and_restarts(self):
        # The clock is integer seconds. The gaps leave no ambiguity.
        # At the last paint: first sight 3-4s old, second sight 1-2s old.
        import time
        self._flash("first")
        self.assertIn("⚡ first", self._paint(ttl="3"))
        time.sleep(2.2)
        self._flash("second")
        self.assertIn("⚡ second", self._paint(ttl="3"))
        time.sleep(1.5)
        self.assertIn("⚡ second", self._paint(ttl="3"))

    def test_an_unseen_flash_past_max_age_is_dropped(self):
        self._flash("stale")
        (f,) = self._files()
        ts, rest = f.read_text().split(" ", 1)
        f.write_text(f"{int(ts) - 700} {rest}")
        out = self._paint()
        self.assertNotIn("⚡", out)
        self.assertEqual(len(f.read_text().splitlines()), 1)  # not stamped

    def test_key_agrees_for_a_path_with_spaces(self):
        d = Path(self._tmp.name).resolve() / "my repo"
        d.mkdir()
        self._flash("spaced", d)
        (f,) = self._files()
        self.assertNotIn(" ", f.name)
        self.assertIn("⚡ spaced", self._paint(cwd=d))

    def test_key_agrees_for_a_relative_path(self):
        self._flash("relative", ".", cwd=self.root)
        (f,) = self._files()
        self.assertNotEqual(f.name, "ops-.")
        self.assertIn("⚡ relative", self._paint())

    def test_key_agrees_for_a_worktree(self):
        # A worker in .worktrees/<bean> flashes. Both bars show it.
        self._breadcrumb()
        self._beans(("demo-ab12", "feature", "in-progress"))
        wt = self._worktree("demo-ab12")
        self._flash("from the worker", wt)
        self.assertEqual(len(self._files()), 1)
        self.assertIn("⚡ from the worker", self._paint(cwd=wt.resolve()))
        self.assertIn("⚡ from the worker", self._paint())

    def test_bash_helper_and_bar_agree(self):
        r = subprocess.run(
            ["bash", "-c", f'source "{self.HELPER}"; ops_flash "via bash" "$1"',
             "_", str(self.root)],
            env=self._env(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("⚡ via bash", self._paint())

    def test_no_flash_no_bolt(self):
        out = self._paint()
        self.assertNotIn("⚡", out)
        self.assertEqual(self._files(), [])


class TestEffortCodes(_Repo):
    """Effort renders as a short code: lo md hi xhi max.
    EFFORT colour for lo, md, hi. ULTRA purple plus a pip for xhi and max."""

    EFFORT = "\x1b[38;2;165;130;195m"
    ULTRA = "\x1b[38;2;170;105;240m"
    CODES = ("lo", "md", "hi", "xhi", "max")

    def _s4(self, out):
        # The model segment. Its index varies without a vault.
        return next(seg for seg in _plain(out).split("│") if "Test" in seg)

    def test_ordinary_levels_are_codes_in_the_effort_colour(self):
        for level, code in (("low", "lo"), ("medium", "md"), ("high", "hi")):
            out = self._bar(raw=True, effort=level)
            self.assertIn(f"{self.EFFORT}{code}\x1b[0m", out, level)
            self.assertNotIn("●", _plain(out), level)

    def test_xhigh_and_max_are_purple_with_a_pip(self):
        for level, code in (("xhigh", "xhi"), ("max", "max")):
            out = self._bar(raw=True, effort=level)
            self.assertIn(f"{self.ULTRA}{code}\x1b[0m {self.ULTRA}●\x1b[0m", out, level)
            self.assertIn(f"{code} ●", self._s4(out))

    def test_no_glyphs_remain(self):
        for level in ("low", "medium", "high", "xhigh", "max"):
            s4 = self._s4(self._bar(raw=True, effort=level))
            for glyph in ("·", "••", "⬥"):
                self.assertNotIn(glyph, s4, level)

    def test_unknown_or_absent_effort_renders_nothing(self):
        for level in ("", "turbo", None):
            s4 = self._s4(self._bar(raw=True, effort=level))
            self.assertNotRegex(s4, r"\b(lo|md|hi|xhi|max)\b")


class TestUltracode(_Repo):
    """The marker $TMPDIR/claude-ultracode-<session_id> paints the context bar purple.
    user-prompt-reminder.sh writes it. Only the reader is driven here."""

    ULTRA = "\x1b[38;2;170;105;240m"

    def _marker(self, sid="test-sid"):
        return self.home / f"claude-ultracode-{sid}"

    def test_marker_paints_the_context_bar_purple(self):
        self._marker().touch()
        out = self._bar(raw=True)
        self.assertIn(f"{self.ULTRA}▓", out)
        self.assertIn(f" {self.ULTRA}●\x1b[0m", out)

    def test_no_marker_no_purple(self):
        out = self._bar(raw=True)
        self.assertNotIn(f"{self.ULTRA}▓", out)
        self.assertNotIn("●", _plain(out))

    def test_marker_is_session_keyed(self):
        self._marker("someone-else").touch()
        self.assertNotIn(f"{self.ULTRA}▓", self._bar(raw=True))

    def test_removing_the_marker_ends_it(self):
        self._marker().touch()
        self.assertIn(f"{self.ULTRA}▓", self._bar(raw=True))
        self._marker().unlink()
        self.assertNotIn(f"{self.ULTRA}▓", self._bar(raw=True))


class TestSessionCost(_Repo):
    """Session cost from the JSON input renders as $N.NN."""

    def _cost_bar(self, cost_usd):
        payload = {
            "cwd": str(self.repo),
            "workspace": {"current_dir": str(self.repo), "project_dir": str(self.repo)},
            "session_id": "test-sid",
            "model": {"display_name": "Test", "id": "test"},
            "effort": {"level": "medium"},
            "context_window": {"used_percentage": 12},
            "cost": {"total_cost_usd": cost_usd},
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(self.home)
        env["ADJUDANT_STATUSLINE_NO_SPAWN"] = "1"
        env["ADJUDANT_PEER_COUNT"] = "0"
        r = subprocess.run(["bash", str(STATUSLINE)], input=json.dumps(payload),
                           env=env, capture_output=True, text=True, timeout=20)
        return _plain(r.stdout)

    def test_session_cost_renders(self):
        out = self._cost_bar(0.43)
        self.assertIn("$0.43", out)

    def test_session_cost_large(self):
        out = self._cost_bar(12.5)
        self.assertIn("$12.5", out)

    def test_session_cost_zero_hidden(self):
        out = self._cost_bar(0)
        self.assertNotIn("$0", out)

    def test_no_cost_field_hidden(self):
        out = self._bar()
        # default _render has no cost field
        self.assertNotIn("$0.00", out)


class TestBeanVelocity(_Repo):
    """Bean velocity sparkline in crunch flash, computed from archive mtimes."""

    def setUp(self):
        super().setUp()
        self._breadcrumb()
        self._beans(("demo-1", "task", "todo"))

    def _archive_bean(self, name, days_ago=0):
        archive = self.repo / ".beans" / "archive"
        archive.mkdir(exist_ok=True)
        f = archive / f"{name}--{name}-slug.md"
        f.write_text(f"---\ntitle: {name}\nstatus: completed\ntype: task\n---\n")
        if days_ago > 0:
            import time
            ts = time.time() - (days_ago * 86400)
            os.utime(f, (ts, ts))

    def test_velocity_cache_created(self):
        self._archive_bean("arc-1", days_ago=1)
        self._archive_bean("arc-2", days_ago=2)
        self._bar()
        cache = list((self.home / ".claude" / "statusline-cache").glob("velocity-*"))
        self.assertEqual(len(cache), 1)
        content = cache[0].read_text()
        self.assertIn(" ", content)

    def test_empty_archive_produces_no_sparkline(self):
        self._bar()
        cache = list((self.home / ".claude" / "statusline-cache").glob("velocity-*"))
        if cache:
            content = cache[0].read_text()
            parts = content.strip().split()
            self.assertTrue(len(parts) >= 2)
            self.assertEqual(parts[1], "-")


if __name__ == "__main__":
    unittest.main()
