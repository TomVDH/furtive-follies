"""Tests for the Beans adapter.

HERMETIC BY CONSTRUCTION. Every test that needs the binary builds a fake
`beans` on a temporary PATH, so the suite behaves identically on a machine with
Beans installed and one without. A test that shelled out to the real binary
would pass here and fail on the other machine, which is the exact class of bug
this module exists to prevent.

The four-way table from the design is the spine of this file: repo-owned or
not, binary present or not, and one named test per cell.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _beans  # noqa: E402


def _fake_beans(bindir: Path, *, stdout: str = "[]", rc: int = 0,
                stderr: str = "", record: Path = None) -> None:
    """Write an executable `beans` into `bindir` that answers predictably."""
    bindir.mkdir(parents=True, exist_ok=True)
    rec = f'printf "%s\\n" "$*" >> {json.dumps(str(record))}\n' if record else ""
    script = (
        "#!/bin/sh\n"
        f"{rec}"
        f"printf '%b' {json.dumps(stdout)}\n"
        f"printf '%b' {json.dumps(stderr)} >&2\n"
        f"exit {rc}\n"
    )
    p = bindir / "beans"
    p.write_text(script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


class _Env:
    """PATH and CLAUDE_PROJECT_DIR restored on exit, always."""

    def __init__(self, path=None, project_dir=None):
        self._path, self._pd = path, project_dir

    def __enter__(self):
        self._old = (os.environ.get("PATH"), os.environ.get("CLAUDE_PROJECT_DIR"))
        if self._path is not None:
            os.environ["PATH"] = str(self._path)
        if self._pd is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = str(self._pd)
        else:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        return self

    def __exit__(self, *exc):
        path, pd = self._old
        if path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = path
        if pd is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = pd
        return False


def _repo(root: Path, tracker: str = None) -> Path:
    """A code root with a breadcrumb, optionally naming a tracker."""
    crumb = root / ".claude" / "adjudant"
    crumb.parent.mkdir(parents=True, exist_ok=True)
    body = "vault_path: /nowhere\nslug: demo\nmode: project\n"
    if tracker:
        body += f"tracker: {tracker}\n"
    crumb.write_text(body)
    return root


BEAN = {
    "id": "beans-a1b2", "slug": "ship-it", "path": "beans-a1b2--ship-it.md",
    "title": "Ship the thing", "status": "todo", "type": "feature",
    "priority": "high", "etag": "e-1",
}


class TestTheTwoFactsAreNeverConflated(unittest.TestCase):
    """owns() asks the repo, available() asks the machine. See the design's
    four-way table: every cell has a named test here."""

    def test_owns_reads_the_breadcrumb_and_never_runs_the_binary(self):
        # The guard: owns() must work on a machine with no beans at all, or a
        # Beans-owned repo becomes an unrecognised repo on the other machine.
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), "beans")
            with _Env(path="/nonexistent-bin"):
                self.assertFalse(_beans.available())
                self.assertTrue(_beans.owns(root))

    def test_owns_is_false_without_a_tracker_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_beans.owns(_repo(Path(tmp))))

    def test_owns_is_false_for_tracker_vault_the_escape_hatch(self):
        # `tracker: vault` in a repo that HAS beans is the documented way to
        # hand the work items back to the vault.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_beans.owns(_repo(Path(tmp), "vault")))

    def test_owns_of_none_is_false(self):
        self.assertFalse(_beans.owns(None))

    # ── the four-way table ──────────────────────────────────────────────────

    def test_cell_vault_repo_no_binary_is_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), "vault")
            with _Env(path="/nonexistent-bin"):
                self.assertEqual(_beans.unreachable_reason(root), "")

    def test_cell_vault_repo_with_binary_is_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), "vault")
            _fake_beans(Path(tmp) / "bin")
            with _Env(path=Path(tmp) / "bin"):
                self.assertEqual(_beans.unreachable_reason(root), "")

    def test_cell_beans_repo_with_binary_is_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), "beans")
            _fake_beans(Path(tmp) / "bin", stdout="[]", rc=0)
            with _Env(path=Path(tmp) / "bin"):
                self.assertEqual(_beans.unreachable_reason(root), "")

    def test_cell_beans_repo_no_binary_refuses_and_names_both_ways_out(self):
        # The dangerous cell. It must REFUSE, and the message must carry the
        # two things a person can do about it.
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), "beans")
            with _Env(path="/nonexistent-bin"):
                why = _beans.unreachable_reason(root)
            self.assertTrue(why)
            self.assertIn("not on PATH", why)
            self.assertIn("tracker: vault", why)

    def test_a_beans_repo_whose_cli_errors_is_also_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _repo(Path(tmp), "beans")
            _fake_beans(Path(tmp) / "bin", stdout="", rc=1,
                        stderr="no .beans directory found")
            with _Env(path=Path(tmp) / "bin"):
                why = _beans.unreachable_reason(root)
            self.assertIn("no .beans directory found", why)


class TestCodeRootResolution(unittest.TestCase):

    def test_env_wins_when_it_carries_a_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_root = _repo(Path(tmp) / "env", "beans")
            hint = _repo(Path(tmp) / "hint", "vault")
            with _Env(project_dir=env_root):
                self.assertEqual(_beans.code_root_from(hint), env_root)

    def test_hint_is_used_when_the_env_has_no_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp:
            hint = _repo(Path(tmp) / "hint", "beans")
            with _Env(project_dir=Path(tmp) / "bare"):
                self.assertEqual(_beans.code_root_from(hint), hint)

    def test_no_breadcrumb_anywhere_is_none_not_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _Env(project_dir=Path(tmp)):
                self.assertIsNone(_beans.code_root_from(Path(tmp)))


class TestDetectDoesNotPayForAnObviousNo(unittest.TestCase):

    def test_no_config_upward_means_no_subprocess(self):
        # The guard: running the binary unconditionally doubled the suite.
        # A recorded invocation here means the stat walk stopped guarding.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            log = Path(tmp) / "calls.log"
            _fake_beans(Path(tmp) / "bin", record=log)
            with _Env(path=Path(tmp) / "bin"):
                self.assertFalse(_beans.detect(root))
            self.assertFalse(log.exists(), "detect ran the binary with no .beans.yml")

    def test_config_present_lets_the_cli_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".beans.yml").write_text("project:\n  name: t\n")
            _fake_beans(Path(tmp) / "bin", stdout="[]", rc=0)
            with _Env(path=Path(tmp) / "bin"):
                self.assertTrue(_beans.detect(root))


class TestTheCardMapping(unittest.TestCase):

    def test_a_bean_becomes_a_card_without_translation(self):
        card = _beans.to_card(BEAN)
        self.assertEqual(card["id"], "beans-a1b2")
        self.assertEqual(card["title"], "Ship the thing")
        self.assertEqual(card["column"], "todo")      # status IS the lane
        self.assertEqual(card["category"], "feature")  # type IS the category
        self.assertEqual(card["beansEtag"], "e-1")
        self.assertEqual(card["source"], "beans")

    def test_priority_rides_only_when_it_is_not_normal(self):
        self.assertEqual(_beans.to_card(BEAN)["priority"], "high")
        plain = dict(BEAN, priority="normal")
        self.assertNotIn("priority", _beans.to_card(plain))

    def test_parent_is_its_own_field_not_a_generic_reference(self):
        # It used to be folded into `related`. The opened card labels a parent,
        # and a labelled relation plus an unlabelled copy of the same id in a
        # reference list is one fact said twice.
        card = _beans.to_card(dict(BEAN, parent="beans-zz"))
        self.assertEqual(card["parent"], "beans-zz")
        self.assertEqual(card["related"], [])
        self.assertNotIn("parent", _beans.to_card(BEAN))

    def test_every_property_a_bean_carries_reaches_the_card(self):
        # The opened card is where a person goes to see the whole item, so a
        # field that stops at this adapter is a field they cannot see anywhere.
        # `body` was the worst of them: every bean had one and every card
        # showed an empty note.
        loaded = dict(
            BEAN, body="  A body.  ", tags=["frontend", "regression"],
            parent="beans-p1", blocked_by=["beans-b1"], blocking=["beans-b2"],
            created_at="2026-09-10T03:01:09Z", updated_at="2026-09-10T04:00:00Z",
        )
        card = _beans.to_card(loaded)
        self.assertEqual(card["notes"], "A body.")
        self.assertEqual(card["tags"], ["frontend", "regression"])
        self.assertEqual(card["parent"], "beans-p1")
        self.assertEqual(card["blockedBy"], ["beans-b1"])
        self.assertEqual(card["blocking"], ["beans-b2"])
        self.assertEqual(card["slug"], BEAN["slug"])
        self.assertEqual(card["beanPath"], BEAN["path"])
        self.assertEqual(card["createdAt"], "2026-09-10T03:01:09Z")
        self.assertEqual(card["updatedAt"], "2026-09-10T04:00:00Z")

    def test_an_absent_property_writes_no_key(self):
        # Same rule `priority` already followed: the key's presence is the
        # signal, so the opened card never grows a row with nothing in it.
        card = _beans.to_card(BEAN)
        for key in ("tags", "parent", "blockedBy", "blocking"):
            self.assertNotIn(key, card, key)
        # a non-list where a list belongs is ignored, not crashed on
        self.assertNotIn("tags", _beans.to_card(dict(BEAN, tags="not-a-list")))

    def test_the_body_is_asked_for(self):
        # `beans list --json` omits bodies. Without --full every card arrived
        # with an empty note while every bean had one, and reading them back
        # with `beans show` per bean would have been an N+1.
        import inspect
        self.assertIn('"--full"', inspect.getsource(_beans.list_beans))

    def test_lanes_are_beans_own_vocabulary(self):
        self.assertEqual(_beans.STATUSES,
                         ("draft", "todo", "in-progress", "completed", "scrapped"))
        self.assertEqual([c["id"] for c in _beans.COLUMNS], list(_beans.STATUSES))

    def test_terminal_lanes_carry_their_markers(self):
        by_id = {c["id"]: c for c in _beans.COLUMNS}
        self.assertEqual(by_id["completed"]["stamp"], "BUILT")
        self.assertEqual(by_id["scrapped"]["stampTone"], "parked")


class TestTheBinaryIsDrivenSafely(unittest.TestCase):

    def test_list_returns_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            _fake_beans(Path(tmp) / "bin", stdout=json.dumps([BEAN]))
            with _Env(path=Path(tmp) / "bin"):
                res = _beans.list_beans(Path(tmp))
            self.assertTrue(res.ok)
            self.assertEqual(res.value[0]["id"], "beans-a1b2")

    def test_unreadable_json_is_a_reason_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            _fake_beans(Path(tmp) / "bin", stdout="not json at all")
            with _Env(path=Path(tmp) / "bin"):
                res = _beans.list_beans(Path(tmp))
            self.assertFalse(res.ok)
            self.assertIn("unreadable JSON", res.reason)

    def test_a_nonzero_exit_carries_its_first_stderr_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            _fake_beans(Path(tmp) / "bin", stdout="", rc=1,
                        stderr="Error: no .beans directory found\nUsage:\n")
            with _Env(path=Path(tmp) / "bin"):
                res = _beans.list_beans(Path(tmp))
            self.assertFalse(res.ok)
            self.assertEqual(res.reason, "Error: no .beans directory found")

    def test_a_missing_binary_is_a_reason_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _Env(path="/nonexistent-bin"):
                res = _beans.list_beans(Path(tmp))
            self.assertFalse(res.ok)
            self.assertIn("not on PATH", res.reason)

    def test_set_status_passes_the_etag_as_if_match(self):
        # The guard: without --if-match there is no compare-and-swap, and the
        # board is back to guessing which store moved.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.log"
            _fake_beans(Path(tmp) / "bin", stdout=json.dumps({"etag": "e-2"}),
                        record=log)
            with _Env(path=Path(tmp) / "bin"):
                res = _beans.set_status(Path(tmp), "beans-a1b2", "completed", "e-1")
            self.assertTrue(res.ok)
            called = log.read_text()
            self.assertIn("--if-match e-1", called)
            self.assertIn("--status completed", called)

    def test_set_status_refuses_a_status_beans_does_not_have(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.log"
            _fake_beans(Path(tmp) / "bin", record=log)
            with _Env(path=Path(tmp) / "bin"):
                res = _beans.set_status(Path(tmp), "beans-a1b2", "backlog", "e-1")
            self.assertFalse(res.ok)
            self.assertIn("not a Beans status", res.reason)
            self.assertFalse(log.exists(), "an invalid status still ran the binary")

    def test_create_refuses_an_empty_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls.log"
            _fake_beans(Path(tmp) / "bin", record=log)
            with _Env(path=Path(tmp) / "bin"):
                res = _beans.create(Path(tmp), "   ")
            self.assertFalse(res.ok)
            self.assertFalse(log.exists())

    def test_stdin_is_closed_on_every_invocation(self):
        # The guard, and it is not theoretical: without DEVNULL the suite went
        # from 40s to past 600s because a subcommand inherited a live stdin.
        src = Path(_beans.__file__).read_text()
        self.assertIn("stdin=subprocess.DEVNULL", src)

    def test_the_binary_is_never_run_through_a_shell(self):
        # Read the CALLS, not the prose: the module's own docstring contains
        # the words "Never `shell=True`", which a substring test cannot tell
        # apart from actually passing it.
        import ast
        tree = ast.parse(Path(_beans.__file__).read_text())
        offenders = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "shell" and not (
                isinstance(kw.value, ast.Constant) and kw.value.value is False)
        ]
        self.assertEqual(offenders, [], f"shell= passed at lines {offenders}")


class TestTheVaultMirror(unittest.TestCase):

    def test_only_open_beans_reach_the_mirror(self):
        rows = [BEAN,
                dict(BEAN, id="b-done", status="completed"),
                dict(BEAN, id="b-drop", status="scrapped")]
        text = _beans.render_mirror(rows, "2026-09-09")
        self.assertIn("beans-a1b2", text)
        self.assertNotIn("b-done", text)
        self.assertNotIn("b-drop", text)
        self.assertIn("1 open of 3 total.", text)

    def test_the_mirror_carries_no_etag(self):
        # etags are machine state; in the file they would churn every run and
        # tell a reader nothing.
        self.assertNotIn("e-1", _beans.render_mirror([BEAN], "2026-09-09"))

    def test_the_mirror_is_ordered_so_it_diffs(self):
        rows = [dict(BEAN, id="b-2", status="todo"),
                dict(BEAN, id="b-1", status="draft")]
        first = _beans.render_mirror(rows, "2026-09-09")
        second = _beans.render_mirror(list(reversed(rows)), "2026-09-09")
        self.assertEqual(first, second)
        self.assertLess(first.index("b-1"), first.index("b-2"))  # lane order

    def test_an_empty_mirror_says_so(self):
        self.assertIn("Nothing open.", _beans.render_mirror([], "2026-09-09"))

    def test_writing_an_unchanged_mirror_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            self.assertTrue(_beans.write_mirror(proj, [BEAN], "2026-09-09"))
            before = (proj / _beans.MIRROR_NAME).stat().st_mtime_ns
            self.assertFalse(_beans.write_mirror(proj, [BEAN], "2026-09-09"))
            self.assertEqual((proj / _beans.MIRROR_NAME).stat().st_mtime_ns, before)

    def test_the_mirror_validates_against_the_note_schema(self):
        from _vault_walk import schema_drift_for_text
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            _beans.write_mirror(proj, [BEAN], "2026-09-09")
            text = (proj / _beans.MIRROR_NAME).read_text()
            drift = schema_drift_for_text(text, _beans.MIRROR_NAME) or {}
            self.assertFalse(drift.get("missing_required"))
            self.assertFalse(drift.get("type_conflict"))


if __name__ == "__main__":
    unittest.main()


class TestBeansIsOfferedNeverAssumed(unittest.TestCase):
    """connect must not decide who owns the work items. Finding a beans project
    is evidence, not consent: a repo can carry one and still want its cards in
    the vault, and a re-connect must never switch that behind the user."""

    def _connect(self, root, vault, **kw):
        import connect
        return connect.write_breadcrumb(root, vault, "V", "demo", kw.get("tracker"))

    def _tracker_of(self, root):
        return _beans._breadcrumb_tracker(root)

    def test_a_beans_repo_still_defaults_to_the_vault(self):
        # The guard. Auto-detecting here would take the choice away and would
        # silently repoint every board in a repo that merely has beans.
        with tempfile.TemporaryDirectory() as tmp:
            root, vault = Path(tmp) / "r", Path(tmp) / "V"
            (root / ".claude").mkdir(parents=True); vault.mkdir()
            (root / ".beans").mkdir(); (root / ".beans.yml").write_text("beans:\n  path: .beans\n")
            _fake_beans(Path(tmp) / "bin")
            with _Env(path=Path(tmp) / "bin"):
                self._connect(root, vault)
            self.assertEqual(self._tracker_of(root), "vault")

    def test_the_users_choice_is_what_lands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, vault = Path(tmp) / "r", Path(tmp) / "V"
            (root / ".claude").mkdir(parents=True); vault.mkdir()
            self._connect(root, vault, tracker="beans")
            self.assertEqual(self._tracker_of(root), "beans")

    def test_a_reconnect_never_switches_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, vault = Path(tmp) / "r", Path(tmp) / "V"
            (root / ".claude").mkdir(parents=True); vault.mkdir()
            (root / ".beans").mkdir(); (root / ".beans.yml").write_text("beans:\n  path: .beans\n")
            self._connect(root, vault, tracker="vault")
            _fake_beans(Path(tmp) / "bin")
            with _Env(path=Path(tmp) / "bin"):
                self._connect(root, vault)          # no --tracker: keep what is written
            self.assertEqual(self._tracker_of(root), "vault")

    def test_the_contract_asks_rather_than_answers(self):
        import connect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            (root / ".claude").mkdir(parents=True)
            (root / ".beans").mkdir(); (root / ".beans.yml").write_text("beans:\n  path: .beans\n")
            offer = connect._tracker_offer(root)
            self.assertTrue(offer["beans_project"])
            self.assertEqual(offer["default"], "vault")       # never beans by default
            self.assertIn("beans", offer["options"])
            self.assertIsNotNone(offer["ask"])                # there IS a question

    def test_a_repo_without_beans_is_not_asked(self):
        import connect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "r"
            (root / ".claude").mkdir(parents=True)
            offer = connect._tracker_offer(root)
            self.assertFalse(offer["beans_project"])
            self.assertEqual(offer["options"], ["vault"])
            self.assertIsNone(offer["ask"])


class TestTheBoardStaysOptIn(unittest.TestCase):
    """A board is born by running /adjudant board, and by nothing else. Beans
    must not have quietly changed that."""

    def test_no_hook_creates_a_board_that_does_not_exist(self):
        # sessionend is the only ambient caller, and it is guarded on the deck
        # already being there. Losing that guard would scaffold a board into
        # every beans repo at the end of every session.
        hooks = Path(__file__).resolve().parent.parent / "hooks" / "scripts"
        text = (hooks / "sessionend.sh").read_text()
        # Anchor on the INVOCATION, not the `-f ... board_bridge.py` existence
        # check: the deck guard sits between the two.
        i = text.index("--ensure-only")
        window = text[max(0, i - 500):i]
        self.assertIn("board-data.json", window,
                      "sessionend calls board_bridge without checking the deck exists")

    def test_ensure_board_reports_rather_than_creates_when_beans_is_unreachable(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "V"; proj = vault / "projects" / "demo"
            proj.mkdir(parents=True)
            (proj / "brief.md").write_text("---\ntype: brief\nstatus: active\n---\n# D\n")
            root = Path(tmp) / "r"; (root / ".claude").mkdir(parents=True)
            (root / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nslug: demo\nmode: project\ntracker: beans\n")
            with _Env(path="/nonexistent-bin"):
                verdict = board.ensure_board(proj, code_root=root)
            self.assertEqual(verdict, "tracker-unreachable")
            self.assertFalse((proj / "board").exists(), "a blocked repo still got a board")


class TestTheVerbSeesWhatTheHooksSee(unittest.TestCase):
    """`board.cmd_scaffold` must hand `scaffold_one` the code root.

    Every ambient caller already did. The verb a person types did not, so
    `/adjudant board` in a Beans-owned repo rebuilt the deck from tasks/ and
    replaced a deck of beans with a deck of task notes. Found 2026-09-09 on a
    live repo with 72 beans and 71 task notes.
    """

    def _fixture(self, tmp: Path, *, slug: str = "demo", projects=("demo",)):
        """A vault, one code root, and a fake beans that answers with one bean."""
        vault = tmp / "V"
        for name in projects:
            proj = vault / "projects" / name
            proj.mkdir(parents=True)
            (proj / "brief.md").write_text("---\ntype: brief\nstatus: active\n---\n# D\n")
            (proj / "tasks").mkdir()
            (proj / "tasks" / "a-task-note.md").write_text(
                "---\ntype: task\nstatus: next\n---\n\n## Task\n\nFrom the vault.\n")
        root = tmp / "r"
        (root / ".claude").mkdir(parents=True)
        (root / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nslug: {slug}\nmode: project\ntracker: beans\n")
        (root / ".beans.yml").write_text("beans:\n  path: .beans\n")
        bindir = tmp / "bin"
        _fake_beans(bindir, stdout=json.dumps([BEAN]))
        return vault, root, bindir

    def _deck(self, proj: Path) -> dict:
        return json.loads((proj / "board" / "board-data.json").read_text())

    def test_scaffold_seeds_from_beans_in_a_beans_owned_repo(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            vault, root, bindir = self._fixture(Path(tmp))
            with _Env(path=str(bindir), project_dir=root):
                rc = board.cli_main(["scaffold", "--project-dir", str(root)])
            self.assertEqual(rc, 0)
            deck = self._deck(vault / "projects" / "demo")
            self.assertEqual(deck.get("tracker"), "beans")
            self.assertEqual([c["source"] for c in deck["cards"]], ["beans"],
                             "the verb rebuilt the deck from tasks/, not from beans")

    def test_force_alone_is_not_refused_in_a_beans_owned_repo(self):
        # build_deck ignores from_tasks when beans owns the repo, so there is
        # no empty deck for the guard to protect against.
        import board
        with tempfile.TemporaryDirectory() as tmp:
            vault, root, bindir = self._fixture(Path(tmp))
            proj = vault / "projects" / "demo"
            with _Env(path=str(bindir), project_dir=root):
                self.assertEqual(board.cli_main(["scaffold", "--project-dir", str(root)]), 0)
                rc = board.cli_main(["scaffold", "--project-dir", str(root), "--force"])
            self.assertEqual(rc, 0, "--force was refused in the one repo that needs it")
            deck = self._deck(proj)
            self.assertEqual(deck.get("tracker"), "beans")

    def test_all_never_hands_one_repos_code_root_to_another_project(self):
        # --all walks projects it did not resolve from a breadcrumb. Passing
        # this repo's code root to every one of them would mark them all
        # Beans-owned and seed them all from THIS repo's beans.
        import board
        with tempfile.TemporaryDirectory() as tmp:
            vault, root, bindir = self._fixture(
                Path(tmp), slug="demo", projects=("demo", "other"))
            with _Env(path=str(bindir), project_dir=root):
                rc = board.cli_main(["scaffold", "--all", "--vault", str(vault),
                                     "--project-dir", str(root), "--from-tasks"])
            self.assertEqual(rc, 0)
            mine = self._deck(vault / "projects" / "demo")
            theirs = self._deck(vault / "projects" / "other")
            self.assertEqual(mine.get("tracker"), "beans")
            self.assertIsNone(theirs.get("tracker"),
                              "a foreign project was marked Beans-owned")
            self.assertNotIn("beans", [c.get("source") for c in theirs["cards"]])

    def test_breadcrumb_slug_reads_the_slug_and_is_empty_without_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_beans.breadcrumb_slug(_repo(Path(tmp), "beans")), "demo")
            self.assertEqual(_beans.breadcrumb_slug(None), "")
            self.assertEqual(_beans.breadcrumb_slug(Path(tmp) / "nowhere"), "")
class TestTheVaultOperationLight(unittest.TestCase):
    """`⊙` in front of the statusline's bolt means adjudant just wrote to the
    vault. It cannot mean "a write is happening now": a hook's write finishes in
    milliseconds and the statusline paints once per turn, so the signal is a
    timestamp plus a linger window on the reader's side."""

    def _marker(self, root, sid=""):
        name = f"adjudant-vault-write-{sid}" if sid else "adjudant-vault-write"
        return Path(root) / name

    def test_it_writes_a_timestamp_the_reader_can_age(self):
        import time
        from _vault_walk import mark_vault_write
        with tempfile.TemporaryDirectory() as tmp:
            with _Env(path=os.environ["PATH"]):
                os.environ["TMPDIR"] = tmp
                try:
                    mark_vault_write("sess-1")
                    m = self._marker(tmp, "sess-1")
                    self.assertTrue(m.is_file())
                    self.assertLess(abs(int(m.read_text()) - int(time.time())), 5)
                finally:
                    os.environ.pop("TMPDIR", None)

    def test_it_is_session_keyed_so_one_line_does_not_light_another(self):
        from _vault_walk import mark_vault_write
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TMPDIR"] = tmp
            try:
                mark_vault_write("sess-a")
                self.assertTrue(self._marker(tmp, "sess-a").is_file())
                self.assertFalse(self._marker(tmp, "sess-b").is_file())
            finally:
                os.environ.pop("TMPDIR", None)

    def test_an_unwritable_tmpdir_is_silent_not_an_error(self):
        # The guard. A statusline nicety must never be able to fail a vault
        # write that already landed.
        from _vault_walk import mark_vault_write
        os.environ["TMPDIR"] = "/nonexistent-dir-for-the-marker"
        try:
            mark_vault_write("sess-x")      # must not raise
        finally:
            os.environ.pop("TMPDIR", None)

    def test_the_documenting_paths_mark(self):
        # Each site imports lazily and swallows everything, so assert the call
        # is present rather than reaching through three layers of hook payload.
        root = Path(__file__).resolve().parent.parent
        for rel in ("hooks/scripts/posttooluse-vault-log.py",
                    "hooks/scripts/posttooluse-commit-log.py"):
            text = (root / rel).read_text()
            self.assertIn("_mark_vault_write(", text, rel)
            self.assertIn("from _vault_walk import mark_vault_write", text, rel)
        self.assertIn("mark_vault_write(", (root / "scripts" / "status.py").read_text())

    def test_marking_never_reaches_the_beans_binary(self):
        # It is a file touch, not a tracker query. A subprocess here would put
        # 50ms on every ambient write.
        import inspect
        from _vault_walk import mark_vault_write
        src = inspect.getsource(mark_vault_write)
        self.assertNotIn("subprocess", src)
        self.assertNotIn("beans", src)
