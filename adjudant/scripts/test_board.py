"""Tests for adjudant/scripts/board.py."""

import contextlib
import io
import json
import math
import re
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from board import (
    BACKUP_DIR_NAME,
    DECK_VERSION,
    DEFAULT_COLUMNS,
    STATUS_TO_COLUMN,
    column_for_status,
    _as_list,
    _first_heading,
    _status_line,
    build_deck,
    cards_from_tasks,
    emit_html,
    enumerate_projects,
    merge_deck,
    scaffold_one,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _scaffold(*args, **kwargs) -> tuple[int, str]:
    """scaffold_one with stdout/stderr captured — keeps the unittest output
    clean and lets tests assert on warnings. Returns (rc, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = scaffold_one(*args, **kwargs)
    return rc, err.getvalue()


def _make_project(root: Path, slug: str, *, brief: bool = True) -> Path:
    """A minimal vault project dir under {root}/projects/{slug}."""
    p = root / "projects" / slug
    p.mkdir(parents=True, exist_ok=True)
    if brief:
        _write(p / "brief.md", f"---\ntype: project\nproject_type: coding\n---\n# {slug}\n")
    return p


def _backups(dest: Path) -> list[Path]:
    """Every rotated deck backup under {dest}/.bak, oldest name first."""
    bak = dest / BACKUP_DIR_NAME
    return sorted(bak.glob("board-data-*.json")) if bak.is_dir() else []


def _ensure(*args, **kwargs) -> str:
    """ensure_board with stdout/stderr captured (scaffold_one prints)."""
    from board import ensure_board
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return ensure_board(*args, **kwargs)


class TestHelpers(unittest.TestCase):

    def test_as_list_forms(self):
        self.assertEqual(_as_list(None), [])
        self.assertEqual(_as_list("SPEC-1"), ["SPEC-1"])
        self.assertEqual(_as_list(["a", "b"]), ["a", "b"])

    def test_as_list_strips_wikilinks(self):
        self.assertEqual(_as_list("[[2026-06-09-canon|Form canon]]"), ["Form canon"])
        self.assertEqual(_as_list("[[SPEC-012]]"), ["SPEC-012"])

    def test_first_heading(self):
        self.assertEqual(_first_heading("intro\n# Title here\nmore"), "Title here")
        self.assertIsNone(_first_heading("no heading at all"))

    def test_the_board_vocabulary_is_the_template_vocabulary(self):
        # The template file is the only declaration of a kind shape. board.py
        # used to hand-declare 26 aliases beside it, which is the "declared
        # twice always drifts" failure the redesign exists to remove: the hand
        # list had grown five spellings of done and had never gained `dropped`.
        from _template_schema import load_schema
        declared = set(load_schema()["task"]["vocab"]["status"])
        self.assertEqual(
            set(STATUS_TO_COLUMN) - {"blocked"}, declared,
            "the board reads the task template vocabulary, plus one alias")
        self.assertEqual(STATUS_TO_COLUMN["blocked"], "review",
                         "blocked is the single documented alias")

    def test_every_status_has_its_own_column(self):
        # Seven statuses, seven columns. `dropped` had no column at all, so a
        # dropped card was displayed as backlog: planned work nobody planned.
        from _template_schema import load_schema
        declared = set(load_schema()["task"]["vocab"]["status"])
        lanes = {c["id"] for c in DEFAULT_COLUMNS}
        self.assertEqual(lanes, declared)

    def test_an_off_vocabulary_status_is_not_filed_as_backlog(self):
        # board.py:205 read `.get(status, "backlog")`. That is how `obsolete`
        # became invisible work: it is named in the design doc as the reason
        # off-vocabulary values are reported and never coerced.
        self.assertNotEqual(column_for_status("obsolete"), "backlog")
        self.assertNotEqual(column_for_status("wip"), "doing",
                            "a retired alias is unknown now, not silently mapped")


class TestCardsFromTasks(unittest.TestCase):

    def test_maps_frontmatter_to_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "tasks" / "cf-03.md",
                "---\ncode: CF-03\nstatus: doing\ncategory: provisioner\n"
                "related:\n  - \"[[SPEC-012]]\"\nnote: a note\n---\n\n# De-hardcode engine\n",
            )
            _write(root / "tasks" / "_index.md", "# idx")  # skipped
            cards = cards_from_tasks(root)
            self.assertEqual(len(cards), 1)
            c = cards[0]
            self.assertEqual(c["id"], "CF-03")
            self.assertEqual(c["column"], "doing")
            self.assertEqual(c["category"], "provisioner")
            self.assertEqual(c["related"], ["SPEC-012"])
            self.assertEqual(c["title"], "De-hardcode engine")
            self.assertEqual(c["notes"], "a note")

    def test_no_tasks_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cards_from_tasks(Path(tmp)), [])

    def test_template_guidance_comments_do_not_poison_card(self):
        # A task note pasted verbatim from a template that carried inline
        # guidance comments on QUOTED value lines: the minimal YAML parser
        # keeps quotes and comment as the raw value, so the card builder must
        # re-clean the scalar before it becomes a card field (id above all).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "tasks" / "ship-it.md",
                "---\n"
                "type: task\n"
                'status: "doing"  # todo | doing | review | blocked | done | icebox\n'
                'category: ""     # optional: board colour group (build, docs, infra, chore, ...)\n'
                'code: ""         # optional: short card id cross-linking specs, handoffs, commits\n'
                'note: ""         # optional: one-line board annotation\n'
                "---\n\n# Ship it\n",
            )
            card = cards_from_tasks(root)[0]
            self.assertEqual(card["id"], "ship-it")      # empty code cleans to the stem
            self.assertEqual(card["column"], "doing")    # quoted status still maps
            self.assertEqual(card["category"], "task")   # empty category falls back
            self.assertEqual(card["notes"], "")

    def test_populated_quoted_value_with_trailing_comment_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "tasks" / "x.md",
                '---\ncode: "X-01"  # short card id\nstatus: doing\n---\n# X\n',
            )
            card = cards_from_tasks(root)[0]
            self.assertEqual(card["id"], "X-01")
            self.assertEqual(card["column"], "doing")

    def test_hash_inside_quoted_value_survives(self):
        # A quoted value containing # prose (no trailing comment) must pass
        # through untouched: only the quoted-then-comment shape is re-cleaned.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "tasks" / "y.md",
                '---\nnote: "see PR #42"\nstatus: backlog\n---\n# Y\n',
            )
            card = cards_from_tasks(root)[0]
            self.assertEqual(card["notes"], "see PR #42")

    def test_skips_type_tasks_roadmap_file(self):
        # a `type: tasks` roadmap/index file must NOT become a card (the
        # real-vault oz-floer shape) — only per-card task notes do.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "tasks" / "tasks.md", "---\ntype: tasks\nproject: oz\n---\n# Roadmap\n- [ ] a\n")
            _write(root / "tasks" / "real.md", "---\ncode: R-1\nstatus: doing\n---\n# Real card\n")
            cards = cards_from_tasks(root)
            self.assertEqual([c["id"] for c in cards], ["R-1"])


class TestDeckFields(unittest.TestCase):

    def test_build_deck_emits_standard_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            deck = build_deck(Path(tmp) / "my-proj", from_tasks=False, title="My Proj")
            self.assertEqual(deck["version"], DECK_VERSION)
            self.assertEqual(deck["boardId"], "my-proj")  # defaults to dir name
            self.assertEqual(deck["subtitle"], "Work-order board")
            self.assertTrue(deck["updated"])  # stamped with a date
            self.assertEqual(deck["title"], "My Proj")

    def test_build_deck_board_id_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            deck = build_deck(Path(tmp) / "x", from_tasks=False, title="T", board_id="slug-9")
            self.assertEqual(deck["boardId"], "slug-9")


class TestEnumerateProjects(unittest.TestCase):

    def test_filesystem_truth_skips_underscore_and_briefless(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root, "beta")
            _make_project(root, "alpha")
            (root / "projects" / "_portfolio").mkdir(parents=True)       # underscore → skip
            (root / "projects" / ".obsidian").mkdir(parents=True)        # dot → skip
            (root / "projects" / "scratch").mkdir(parents=True)          # no brief → skip
            _write(root / "projects" / "_index.md", "# idx")
            got = [slug for slug, _ in enumerate_projects(root)]
            self.assertEqual(got, ["alpha", "beta"])  # sorted, real only

    def test_tolerant_of_malformed_index(self):
        # discovery is filesystem-based; a messy _index.md never affects it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_project(root, "a")
            _write(
                root / "projects" / "_index.md",
                "| Project |\n|---|\n| [[projects/ghost/brief|ghost]] |\n"
                "| [[b/brief\\|b]] |\n| — |\n",
            )
            self.assertEqual([s for s, _ in enumerate_projects(root)], ["a"])

    def test_no_projects_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(enumerate_projects(Path(tmp)), [])


class TestMergeDeck(unittest.TestCase):

    def _deck(self, cards, **kw):
        d = {"version": 1, "boardId": "p", "title": "P", "subtitle": "s",
             "updated": "2026-01-01", "columns": [], "categories": [], "cards": cards}
        d.update(kw)
        return d

    def test_preserves_dragged_column(self):
        existing = self._deck([{"id": "X-1", "title": "old", "column": "done", "category": "build", "notes": ""}])
        fresh = self._deck([{"id": "X-1", "title": "new", "column": "backlog", "category": "build", "notes": ""}])
        out = merge_deck(existing, fresh)
        card = out["cards"][0]
        self.assertEqual(card["column"], "done")   # drag state preserved
        self.assertEqual(card["title"], "new")     # task-owned field re-seeded

    def test_adds_new_task_card(self):
        existing = self._deck([{"id": "X-1", "column": "doing", "category": "build", "notes": ""}])
        fresh = self._deck([
            {"id": "X-1", "column": "backlog", "category": "build", "notes": ""},
            {"id": "X-2", "column": "next", "category": "docs", "notes": ""},
        ])
        out = merge_deck(existing, fresh)
        by_id = {c["id"]: c for c in out["cards"]}
        self.assertEqual(by_id["X-1"]["column"], "doing")   # preserved
        self.assertEqual(by_id["X-2"]["column"], "next")    # new, task-derived

    def test_task_seeded_orphan_goes_to_icebox_not_deleted(self):
        # source: task ⇒ the backing tasks/ note disappeared → park in icebox
        existing = self._deck([{"id": "X-9", "column": "done", "category": "build",
                                "notes": "keep", "source": "task"}])
        fresh = self._deck([{"id": "X-1", "column": "backlog", "category": "build", "notes": ""}])
        out = merge_deck(existing, fresh)
        by_id = {c["id"]: c for c in out["cards"]}
        self.assertIn("X-9", by_id)
        self.assertEqual(by_id["X-9"]["column"], "icebox")
        self.assertEqual(by_id["X-9"]["notes"], "keep")

    def test_hand_added_card_keeps_its_column_on_reseed(self):
        # No task provenance ⇒ a card added via the board UI — refresh must
        # NOT drag it to icebox (regression: it was relocated every re-seed).
        existing = self._deck([{"id": "hand-1", "column": "doing", "category": "build", "notes": ""}])
        fresh = self._deck([{"id": "X-1", "column": "backlog", "category": "build", "notes": ""}])
        out = merge_deck(existing, fresh)
        by_id = {c["id"]: c for c in out["cards"]}
        self.assertIn("hand-1", by_id)
        self.assertEqual(by_id["hand-1"]["column"], "doing")

    def test_cards_from_tasks_stamp_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            _write(proj / "tasks" / "x-1.md", "---\ntype: task\nstatus: next\n---\n\n# Do X\n")
            cards = cards_from_tasks(proj)
            self.assertEqual(cards[0]["source"], "task")

    def test_preserves_board_title_and_local_notes(self):
        existing = self._deck([{"id": "X-1", "column": "doing", "category": "b", "notes": "my annotation"}],
                              title="Custom Title")
        fresh = self._deck([{"id": "X-1", "column": "backlog", "category": "b", "notes": ""}],
                           title="Auto Title")
        out = merge_deck(existing, fresh)
        self.assertEqual(out["title"], "Custom Title")           # deck title preserved
        self.assertEqual(out["cards"][0]["notes"], "my annotation")  # local note preserved


class TestMergeDeckPassThrough(unittest.TestCase):
    """Audit 2026-07-30 finding 1: merge_deck keyed the on-disk deck into a
    plain dict on `str(card.get("id"))`, so two cards sharing an id (or two
    id-less cards, both keying to the string "None") collapsed to one and the
    losers were SILENTLY DELETED. reference/board.md invites hand-editing the
    deck and promises a card is never deleted."""

    def _deck(self, cards, **kw):
        d = {"version": 1, "boardId": "p", "title": "P", "subtitle": "s",
             "updated": "2026-01-01", "columns": [], "categories": [], "cards": cards}
        d.update(kw)
        return d

    def _merge(self, existing, fresh):
        """merge_deck with stderr captured. Returns (deck, stderr)."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = merge_deck(existing, fresh)
        return out, err.getvalue()

    def test_id_less_hand_added_cards_all_survive(self):
        existing = self._deck([
            {"id": "T-1", "column": "doing", "category": "build", "notes": "", "source": "task"},
            {"title": "call the vendor", "column": "next", "notes": "ref PO-114"},
            {"title": "renew the cert", "column": "next", "notes": "expires 2026-08-14"},
        ])
        fresh = self._deck([
            {"id": "T-1", "column": "backlog", "category": "build", "notes": "", "source": "task"},
            {"id": "T-2", "column": "backlog", "category": "build", "notes": "", "source": "task"},
        ])
        out, _ = self._merge(existing, fresh)
        titles = [c.get("title") for c in out["cards"]]
        self.assertIn("call the vendor", titles)
        self.assertIn("renew the cert", titles)
        # verbatim: an id-less card can never be a merge partner, so nothing
        # about it may change (including the icebox relocation).
        kept = [c for c in out["cards"] if c.get("title") == "call the vendor"]
        self.assertEqual(kept, [{"title": "call the vendor", "column": "next", "notes": "ref PO-114"}])

    def test_duplicate_ids_on_disk_all_survive_with_warning(self):
        existing = self._deck([
            {"id": "T-1", "title": "first", "column": "doing", "category": "build", "notes": "keep me"},
            {"id": "T-1", "title": "second", "column": "review", "category": "build", "notes": "me too"},
        ])
        fresh = self._deck([
            {"id": "T-1", "title": "from the task note", "column": "backlog",
             "category": "build", "notes": "", "source": "task"},
        ])
        out, err = self._merge(existing, fresh)
        self.assertEqual(len(out["cards"]), 2, f"no card may be dropped: {out['cards']}")
        notes = sorted(c.get("notes", "") for c in out["cards"])
        self.assertEqual(notes, ["keep me", "me too"])
        self.assertIn("duplicate card id 'T-1'", err)

    def test_duplicate_orphan_ids_all_survive(self):
        # Both copies carry task provenance and the task is gone: the merge
        # partner is iceboxed as documented, the extra copy still survives.
        existing = self._deck([
            {"id": "T-9", "title": "first", "column": "done", "notes": "a", "source": "task"},
            {"id": "T-9", "title": "second", "column": "doing", "notes": "b", "source": "task"},
        ])
        fresh = self._deck([{"id": "T-1", "column": "backlog", "notes": "", "source": "task"}])
        out, _ = self._merge(existing, fresh)
        self.assertEqual(len([c for c in out["cards"] if c.get("id") == "T-9"]), 2)

    def test_ensure_board_reseed_keeps_hand_added_cards(self):
        # The audit's end-to-end reproduction: a hand-edited deck, then one new
        # task note lands and the PostToolUse hook fires ensure_board.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            for n in (1, 2, 3):
                _write(proj / "tasks" / f"t{n}.md", f"---\ncode: T-{n}\nstatus: backlog\n---\n# Task {n}\n")
            _ensure(proj)
            data_path = proj / "board" / "board-data.json"
            deck = json.loads(data_path.read_text())
            deck["cards"].append({"title": "call the vendor", "column": "next", "notes": "ref PO-114"})
            deck["cards"].append({"title": "renew the cert", "column": "next", "notes": "expires 2026-08-14"})
            data_path.write_text(json.dumps(deck, indent=2) + "\n")

            _write(proj / "tasks" / "t4.md", "---\ncode: T-4\nstatus: backlog\n---\n# Task 4\n")
            self.assertEqual(_ensure(proj), "reseeded")

            after = json.loads(data_path.read_text())["cards"]
            titles = [c.get("title") for c in after]
            self.assertIn("call the vendor", titles)
            self.assertIn("renew the cert", titles)
            self.assertIn("T-4", [c.get("id") for c in after])


class TestScaffoldOne(unittest.TestCase):

    def _seed_tasks(self, proj, specs):
        for fname, fm in specs.items():
            _write(proj / "tasks" / fname, fm)

    def test_merge_refresh_without_clobber_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            self._seed_tasks(proj, {
                "t1.md": "---\ncode: T-1\nstatus: backlog\ncategory: build\n---\n# One\n",
                "t2.md": "---\ncode: T-2\nstatus: next\ncategory: docs\n---\n# Two\n",
            })
            dest = proj / "board"
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            data_path = dest / "board-data.json"
            deck = json.loads(data_path.read_text())
            self.assertEqual({c["id"]: c["column"] for c in deck["cards"]},
                             {"T-1": "backlog", "T-2": "next"})

            # user drags T-1 to doing
            deck["cards"][0]["column"] = "doing"
            data_path.write_text(json.dumps(deck))

            # tasks change: T-2 removed, T-3 added, T-1 retitled
            (proj / "tasks" / "t2.md").unlink()
            self._seed_tasks(proj, {
                "t1.md": "---\ncode: T-1\nstatus: backlog\ncategory: build\n---\n# One renamed\n",
                "t3.md": "---\ncode: T-3\nstatus: review\ncategory: build\n---\n# Three\n",
            })
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            deck = json.loads(data_path.read_text())
            by_id = {c["id"]: c for c in deck["cards"]}
            self.assertEqual(by_id["T-1"]["column"], "doing")          # drag preserved
            self.assertEqual(by_id["T-1"]["title"], "One renamed")     # task-owned re-seed
            self.assertEqual(by_id["T-2"]["column"], "icebox")         # orphan parked
            self.assertEqual(by_id["T-3"]["column"], "review")         # new card
            self.assertTrue((dest / "board.html").is_file())

    def test_force_rebuild_discards_drag_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            self._seed_tasks(proj, {"t1.md": "---\ncode: T-1\nstatus: backlog\n---\n# One\n"})
            dest = proj / "board"
            _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
            data_path = dest / "board-data.json"
            deck = json.loads(data_path.read_text())
            deck["cards"][0]["column"] = "done"
            data_path.write_text(json.dumps(deck))
            _scaffold(proj, dest, from_tasks=True, data=None, force=True, title=None, board_id="proj")
            deck = json.loads(data_path.read_text())
            self.assertEqual(deck["cards"][0]["column"], "backlog")    # reset to status

    def test_empty_tasks_yields_starter_deck(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj).mkdir(parents=True)
            dest = proj / "board"
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            deck = json.loads((dest / "board-data.json").read_text())
            self.assertEqual(deck["cards"], [])
            self.assertIn("build", deck["categories"])

    def test_without_from_tasks_keeps_existing_deck(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            dest = proj / "board"
            dest.mkdir(parents=True)
            (dest / "board-data.json").write_text(json.dumps(
                {"title": "Keep", "cards": [{"id": "K-1", "column": "doing"}], "columns": [], "categories": []}))
            _scaffold(proj, dest, from_tasks=False, data=None, force=False, title=None, board_id="proj")
            deck = json.loads((dest / "board-data.json").read_text())
            self.assertEqual(deck["title"], "Keep")
            self.assertEqual(deck["cards"][0]["column"], "doing")
            self.assertEqual(deck["boardId"], "proj")   # backfilled


class TestBuildDeck(unittest.TestCase):

    def test_empty_deck_has_default_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            deck = build_deck(Path(tmp), from_tasks=False, title="T")
            self.assertEqual(deck["title"], "T")
            self.assertEqual(deck["cards"], [])
            # Derived, not hardcoded: the number of lanes follows the task
            # template status vocabulary, and a literal here would be a
            # second declaration of it.
            from _template_schema import load_schema
            self.assertEqual(len(deck["columns"]),
                             len(load_schema()["task"]["vocab"]["status"]))
            self.assertIn("build", deck["categories"])

    def test_categories_derived_from_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "tasks" / "a.md", "---\nstatus: done\ncategory: form\n---\n# A\n")
            _write(root / "tasks" / "b.md", "---\nstatus: next\ncategory: spec\n---\n# B\n")
            deck = build_deck(root, from_tasks=True, title="T")
            self.assertEqual(set(deck["categories"]), {"form", "spec"})
            self.assertEqual(len(deck["cards"]), 2)


class TestEmitHtml(unittest.TestCase):

    def test_injects_deck_between_markers(self):
        deck = {"title": "Z", "columns": [], "categories": ["x"], "cards": [{"id": "Q-1"}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.html"
            emit_html(deck, out)
            html = out.read_text()
            self.assertIn("BOARD_DATA_START", html)
            self.assertIn("Q-1", html)
            # the injected JSON is parseable back out
            start = html.index("/*BOARD_DATA_START*/") + len("/*BOARD_DATA_START*/")
            end = html.index("/*BOARD_DATA_END*/")
            self.assertEqual(json.loads(html[start:end])["title"], "Z")

    def test_escapes_script_breakout(self):
        # A task title containing </script> must not close the injected
        # script block (broken page / stored XSS on the local board).
        hostile = "pwn </script><script>alert(1)</script>"
        deck = {"title": hostile, "columns": [], "categories": [], "cards": []}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.html"
            emit_html(deck, out)
            html = out.read_text()
            start = html.index("/*BOARD_DATA_START*/") + len("/*BOARD_DATA_START*/")
            end = html.index("/*BOARD_DATA_END*/")
            payload = html[start:end]
            self.assertNotIn("</script>", payload)           # nothing can close the tag
            self.assertNotIn("<!--", payload)                # no comment-opener either
            self.assertEqual(json.loads(payload)["title"], hostile)  # round-trips intact


class TestForceSafety(unittest.TestCase):

    def _seeded_board(self, tmp: Path) -> tuple[Path, Path]:
        proj = tmp / "proj"
        _write(proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: doing\n---\n# One\n")
        dest = proj / "board"
        rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
        assert rc == 0
        return proj, dest

    def test_force_without_from_tasks_refused(self):
        # `--force` alone over an existing board would wipe it to an empty
        # starter deck — must refuse instead of destroying data.
        with tempfile.TemporaryDirectory() as tmp:
            proj, dest = self._seeded_board(Path(tmp))
            before = (dest / "board-data.json").read_text()
            rc, err = _scaffold(proj, dest, from_tasks=False, data=None, force=True, title=None, board_id="proj")
            self.assertEqual(rc, 1)
            self.assertIn("refusing", err)
            self.assertEqual((dest / "board-data.json").read_text(), before)  # untouched

    def test_data_overwrite_backs_up_existing_deck(self):
        # Audit 2026-07-27 finding 11: BOTH the refusal guard and the .bak
        # escape hatch were gated on `force`, so `scaffold --data foo.json`
        # replaced a live deck (cards, custom lane, title) with no backup.
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            proj, dest = self._seeded_board(tmpp)
            deck = json.loads((dest / "board-data.json").read_text())
            deck["cards"][0]["column"] = "done"          # user drag state
            deck["columns"].append({"id": "waiting", "name": "Waiting"})
            (dest / "board-data.json").write_text(json.dumps(deck))

            injected = tmpp / "injected.json"
            injected.write_text(json.dumps({"columns": [], "cards": []}))
            rc, _ = _scaffold(proj, dest, from_tasks=False, data=str(injected),
                              force=False, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            baks = _backups(dest)
            self.assertEqual(len(baks), 1, "--data over an existing deck must back it up")
            saved = json.loads(baks[0].read_text())
            self.assertEqual(saved["cards"][0]["column"], "done")
            self.assertIn("waiting", [c["id"] for c in saved["columns"]])

    def test_force_backs_up_existing_deck(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, dest = self._seeded_board(Path(tmp))
            deck = json.loads((dest / "board-data.json").read_text())
            deck["cards"][0]["column"] = "done"  # user drag state
            (dest / "board-data.json").write_text(json.dumps(deck))
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=True, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            baks = _backups(dest)
            self.assertEqual(len(baks), 1, "--force must back up the deck it discards")
            self.assertEqual(json.loads(baks[0].read_text())["cards"][0]["column"], "done")

    def test_second_replace_does_not_destroy_the_first_backup(self):
        # Audit 2026-07-30 finding 2: the backup was one fixed filename with no
        # rotation, so replace #2 overwrote the only copy of the real deck with
        # the already-destroyed one.
        with tempfile.TemporaryDirectory() as tmp:
            proj, dest = self._seeded_board(Path(tmp))
            deck = json.loads((dest / "board-data.json").read_text())
            deck["cards"][0]["column"] = "doing"
            deck["cards"][0]["notes"] = "PRECIOUS"
            deck["columns"].append({"id": "parking", "name": "Parking"})
            (dest / "board-data.json").write_text(json.dumps(deck))

            for _ in range(2):
                rc, _ = _scaffold(proj, dest, from_tasks=True, data=None,
                                  force=True, title=None, board_id="proj")
                self.assertEqual(rc, 0)

            saved = [json.loads(p.read_text()) for p in _backups(dest)]
            self.assertTrue(saved, "a replace must leave a recoverable backup")
            precious = [d for d in saved
                        if d["cards"][0].get("notes") == "PRECIOUS"
                        and "parking" in [c["id"] for c in d["columns"]]]
            self.assertTrue(
                precious,
                "the ORIGINAL deck must still be recoverable after a second replace; "
                f"backups held {[[c.get('notes') for c in d['cards']] for d in saved]}")

    def test_failed_data_read_leaves_backups_untouched(self):
        # A typo'd --data exits 1 having written nothing, so it must not have
        # spent the backup either.
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            proj, dest = self._seeded_board(tmpp)
            good = tmpp / "good.json"
            good.write_text(json.dumps({"cards": [{"id": "G-1", "column": "next"}]}))
            rc, _ = _scaffold(proj, dest, from_tasks=False, data=str(good),
                              force=False, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            before = {p.name: p.read_bytes() for p in _backups(dest)}
            self.assertTrue(before, "precondition: the good replace made a backup")
            deck_before = (dest / "board-data.json").read_bytes()

            rc, err = _scaffold(proj, dest, from_tasks=False, data=str(tmpp / "nope.json"),
                                force=False, title=None, board_id="proj")
            self.assertEqual(rc, 1)
            self.assertIn("could not read deck", err)
            self.assertEqual({p.name: p.read_bytes() for p in _backups(dest)}, before,
                             "a failed --data must leave every backup byte-identical")
            self.assertEqual((dest / "board-data.json").read_bytes(), deck_before)

    def test_backups_are_rotated(self):
        # Bounded growth in a synced vault: the newest BACKUP_KEEP survive.
        from board import BACKUP_KEEP
        with tempfile.TemporaryDirectory() as tmp:
            proj, dest = self._seeded_board(Path(tmp))
            bak_dir = dest / BACKUP_DIR_NAME
            bak_dir.mkdir(parents=True, exist_ok=True)
            for n in range(BACKUP_KEEP + 3):
                (bak_dir / f"board-data-2020010{n}-000000.json").write_text("{}")
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None,
                              force=True, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            names = sorted(p.name for p in _backups(dest))
            self.assertEqual(len(names), BACKUP_KEEP, names)
            self.assertNotIn("board-data-20200100-000000.json", names)  # oldest pruned

    def test_the_bak_dir_stays_inside_the_project(self):
        # The decision, pinned. v3 moved every scratch tree out of the vault
        # ($TMPDIR/adjudant/{key}/{kind}); the deck backup deliberately did not
        # follow. It is not scratch: a deck holds cards hand-added straight to
        # board-data.json and lane placement that exists nowhere else, it is
        # only ever written by a destructive command the user typed
        # (`--force`/`--data`, never the ambient reseed), and the vault is what
        # syncs across machines while $TMPDIR does not — an undo record that
        # evaporates before the mistake is noticed on the other machine is not
        # an undo record. Reversing this means moving the doc with the code.
        with tempfile.TemporaryDirectory() as tmp:
            proj, dest = self._seeded_board(Path(tmp))
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None,
                              force=True, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            baks = _backups(dest)
            self.assertEqual(len(baks), 1)
            self.assertEqual(baks[0].parent, dest / BACKUP_DIR_NAME)
            self.assertTrue(baks[0].name.startswith("board-data-"))

    def test_the_state_contract_names_this_exception(self):
        contract = (Path(__file__).resolve().parent.parent / "skills" /
                    "adjudant" / "reference" / "state-contract.md").read_text()
        self.assertIn("board/.bak/", contract)
        self.assertIn("backup_deck", contract)

    def test_non_object_deck_friendly_error(self):
        # Valid JSON that isn't an object (null/[]) must hit the same friendly
        # error path, not an AttributeError traceback.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            dest = proj / "board"
            dest.mkdir(parents=True)
            for payload in ("null", "[]"):
                (dest / "board-data.json").write_text(payload)
                rc, err = _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
                self.assertEqual(rc, 1, payload)
                self.assertIn("could not read deck", err)

    def test_corrupt_deck_friendly_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            dest = proj / "board"
            dest.mkdir(parents=True)
            (dest / "board-data.json").write_text("{not json")
            rc, err = _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
            self.assertEqual(rc, 1)
            self.assertIn("could not read deck", err)


class TestMergePreservesColumns(unittest.TestCase):

    def test_custom_columns_survive_reseed(self):
        # A user-added 'qa' lane (and the card dragged into it) must survive a
        # --from-tasks re-scaffold; columns are user-ownable deck data.
        custom_cols = [{"id": "backlog", "name": "Backlog"}, {"id": "qa", "name": "QA"}]
        existing = {"title": "P", "columns": custom_cols, "categories": [],
                    "cards": [{"id": "X-1", "column": "qa", "category": "build", "notes": ""}]}
        fresh = {"title": "P", "columns": [{"id": "backlog", "name": "Backlog"}], "categories": [],
                 "cards": [{"id": "X-1", "column": "backlog", "category": "build", "notes": ""}]}
        out = merge_deck(existing, fresh)
        self.assertEqual(out["columns"], custom_cols)
        self.assertEqual(out["cards"][0]["column"], "qa")


class TestDuplicateIds(unittest.TestCase):

    def test_duplicate_codes_disambiguated_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "tasks" / "a.md", "---\ncode: T-1\nstatus: doing\n---\n# A\n")
            _write(root / "tasks" / "b.md", "---\ncode: T-1\nstatus: next\n---\n# B\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cards = cards_from_tasks(root)
            ids = [c["id"] for c in cards]
            self.assertEqual(len(ids), len(set(ids)), f"ids must be unique, got {ids}")
            self.assertIn("T-1", ids)
            self.assertIn("b", ids)  # collision falls back to the filename stem
            self.assertIn("duplicate card id 'T-1'", err.getvalue())

    def test_warning_names_the_final_id_when_stem_also_collides(self):
        # a.md has code 'b'; b.md also claims 'b' → b.md becomes 'b~2' and the
        # warning must say so (not the intermediate stem).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "tasks" / "a.md", "---\ncode: b\nstatus: doing\n---\n# A\n")
            _write(root / "tasks" / "b.md", "---\ncode: b\nstatus: next\n---\n# B\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cards = cards_from_tasks(root)
            ids = sorted(c["id"] for c in cards)
            self.assertEqual(ids, ["b", "b~2"])
            self.assertIn("using 'b~2' for b.md", err.getvalue())


class TestEnumerateZones(unittest.TestCase):

    def test_sees_fridge_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            for zone, slug in (("", "a"), ("_fridge", "b"), ("_archive", "c")):
                d = vault / "projects" / zone / slug if zone else vault / "projects" / slug
                d.mkdir(parents=True)
                (d / "brief.md").write_text("---\ntype: project\n---\n")
            slugs = [s for s, _ in enumerate_projects(vault)]
            self.assertEqual(slugs, ["a", "b", "c"])


class TestStatus(unittest.TestCase):

    def test_status_line_counts_and_unknown_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            _write(proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: doing\n---\n# One\n")
            _write(proj / "tasks" / "t2.md", "---\ncode: T-2\nstatus: done\n---\n# Two\n")
            dest = proj / "board"
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=False, title=None, board_id="proj")
            self.assertEqual(rc, 0)
            line, ok = _status_line("proj", dest)
            self.assertTrue(ok)
            self.assertIn("doing:1", line)
            self.assertIn("done:1", line)
            self.assertIn("2 cards", line)
            # a card in a removed column surfaces as a warning, not silence
            deck = json.loads((dest / "board-data.json").read_text())
            deck["cards"][0]["column"] = "qa"
            (dest / "board-data.json").write_text(json.dumps(deck))
            line, ok = _status_line("proj", dest)
            self.assertTrue(ok)
            self.assertIn("unknown column 'qa'", line)

    def test_status_all_rejects_dest(self):
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = cli_main(["status", "--all", "--dest", "somewhere", "--vault", tmp])
            self.assertEqual(rc, 1)
            self.assertIn("--dest cannot be combined with --all", err.getvalue())

    def test_status_missing_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            line, ok = _status_line("ghost", Path(tmp) / "board")
            self.assertFalse(ok)
            self.assertIn("no board", line)


class TestEnsureBoard(unittest.TestCase):
    """Board birth + reseed for ambient callers: the ensure_board contract."""

    def test_ensure_board_no_tasks_writes_nothing(self):
        # Only _index.md and a `type: tasks` roadmap: no real task notes, so
        # the project never grows board files.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            _write(proj / "tasks" / "_index.md", "# idx")
            _write(proj / "tasks" / "tasks.md", "---\ntype: tasks\n---\n# Roadmap\n- [ ] a\n")
            self.assertEqual(_ensure(proj), "no-tasks")
            self.assertFalse((proj / "board").exists())

    def test_ensure_board_creates_on_first_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            _write(proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: backlog\n---\n# One\n")
            self.assertEqual(_ensure(proj), "created")
            deck = json.loads((proj / "board" / "board-data.json").read_text())
            self.assertEqual([c["id"] for c in deck["cards"]], ["T-1"])
            self.assertTrue((proj / "board" / "board.html").is_file())

    def test_ensure_board_reseed_preserves_dragged_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            _write(proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: backlog\n---\n# One\n")
            self.assertEqual(_ensure(proj), "created")
            data_path = proj / "board" / "board-data.json"
            deck = json.loads(data_path.read_text())
            deck["cards"][0]["column"] = "doing"    # user drags T-1
            data_path.write_text(json.dumps(deck))
            _write(proj / "tasks" / "t2.md", "---\ncode: T-2\nstatus: review\n---\n# Two\n")
            self.assertEqual(_ensure(proj), "reseeded")
            by_id = {c["id"]: c for c in json.loads(data_path.read_text())["cards"]}
            self.assertEqual(by_id["T-1"]["column"], "doing")   # drag survives
            self.assertEqual(by_id["T-2"]["column"], "review")  # new card lands

    def test_ensure_board_idempotent_no_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            _write(proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: backlog\n---\n# One\n")
            self.assertEqual(_ensure(proj), "created")
            data_path = proj / "board" / "board-data.json"
            before = data_path.read_text()
            self.assertEqual(_ensure(proj), "no-change")
            # untouched, byte for byte: ambient callers must not churn the deck
            self.assertEqual(data_path.read_text(), before)

    def test_ensure_cli_flag(self):
        # hooks call `python3 board.py --ensure --project-dir X`; the verdict
        # is the last stdout line.
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            _write(proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: backlog\n---\n# One\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["--ensure", "--project-dir", str(proj)])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue().strip().splitlines()[-1], "created")
            self.assertTrue((proj / "board" / "board-data.json").is_file())


class TestZoneAwareProjectFlag(unittest.TestCase):

    def test_scaffold_project_finds_fridged_project(self):
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pdir = vault / "projects" / "_fridge" / "p"
            _write(pdir / "brief.md", "---\ntype: project\nproject_type: coding\n---\n# P\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scaffold", "--project", "p", "--vault", str(vault)])
            self.assertEqual(rc, 0)
            self.assertTrue((pdir / "board" / "board-data.json").is_file())
            # No duplicate scaffolded into the live zone
            self.assertFalse((vault / "projects" / "p").exists())


class TestUndecodableTaskNote(unittest.TestCase):
    """Audit 2026-07-30 finding 4. cards_from_tasks read with
    errors="replace" and wrote the decoded result straight into
    board-data.json and board.html. U+FFFD landed in the card ID, so the
    corruption was not self-healing: fixing the note yields a NEW id and
    merge_deck's "never deleted" orphan rule iceboxes the mojibake card
    forever. Same trap sync and shelf already closed (5bd7164, 1934eac)."""

    LATIN1 = "---\ncode: \"DÉP-1\"\nstatus: backlog\n---\n# Déploiement\n".encode("latin-1")

    def test_latin1_task_note_is_skipped_with_a_named_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / "tasks").mkdir(parents=True)
            (proj / "tasks" / "deploiement.md").write_bytes(self.LATIN1)
            _write(proj / "tasks" / "ok.md", "---\ncode: T-1\nstatus: backlog\n---\n# Fine\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cards = cards_from_tasks(proj)
            self.assertEqual([c["id"] for c in cards], ["T-1"],
                             "the undecodable note must be skipped, not mojibake'd")
            self.assertIn("deploiement.md", err.getvalue())
            self.assertIn("not valid UTF-8", err.getvalue())

    def test_no_replacement_char_reaches_the_deck_or_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / "tasks").mkdir(parents=True)
            (proj / "tasks" / "deploiement.md").write_bytes(self.LATIN1)
            _write(proj / "tasks" / "ok.md", "---\ncode: T-1\nstatus: backlog\n---\n# Fine\n")
            dest = proj / "board"
            rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=False,
                              title=None, board_id="proj")
            self.assertEqual(rc, 0)
            deck_text = (dest / "board-data.json").read_text()
            self.assertNotIn("�", deck_text)
            self.assertNotIn("�", (dest / "board.html").read_text())
            self.assertEqual([c["id"] for c in json.loads(deck_text)["cards"]], ["T-1"])

    def test_no_zombie_card_after_the_note_is_re_saved_as_utf8(self):
        # The permanent-Icebox zombie: mojibake id, then the author fixes the
        # encoding, and the old id can never be cleared because orphans are
        # iceboxed rather than deleted.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / "tasks").mkdir(parents=True)
            note = proj / "tasks" / "deploiement.md"
            note.write_bytes(self.LATIN1)
            _write(proj / "tasks" / "ok.md", "---\ncode: T-1\nstatus: backlog\n---\n# Fine\n")
            _ensure(proj)
            note.write_text("---\ncode: \"DÉP-1\"\nstatus: backlog\n---\n# Déploiement\n")
            _ensure(proj)
            cards = json.loads((proj / "board" / "board-data.json").read_text())["cards"]
            ids = [c["id"] for c in cards]
            self.assertEqual(sorted(ids), ["DÉP-1", "T-1"], f"zombie card left behind: {ids}")


class TestProjectSlugTraversal(unittest.TestCase):
    """Audit 2026-07-30 finding 3. Commit 953b5e5 gated the BREADCRUMB slug on
    the verb path; `--project SLUG` was still fed straight into
    find_project_dir / `{vault}/projects/{slug}` with no gate and no
    containment check, so a traversal slug wrote a whole board outside the
    vault. Card titles come from tasks/*.md, so the written content is
    attacker-influenceable."""

    @staticmethod
    def _census(root: Path, skip: Path) -> dict[str, bytes]:
        """Byte-exact census of everything under `root` except the `skip`
        subtree. Any file created, deleted, or rewritten shows up as a diff."""
        out: dict[str, bytes] = {}
        for p in sorted(root.rglob("*")):
            if p == skip or skip in p.parents:
                continue
            out[str(p)] = p.read_bytes() if p.is_file() else b"<dir>"
        return out

    def _fixture(self, root: Path) -> tuple[Path, Path, str]:
        vault = root / "vault"
        (vault / "projects").mkdir(parents=True)
        _write(vault / "Home.md", "---\ntype: vault-home\nupdated: 2026-01-01\n---\n")
        _make_project(vault, "demo")
        # The escape target exists, so the `pdir.is_dir()` check passes and the
        # write goes through — this is the audit's fixture shape.
        outside = root / "scratchpad" / "outside"
        outside.mkdir(parents=True)
        _write(outside / "keep.txt", "untouched\n")
        return vault, outside, "../../scratchpad/outside"

    def test_scaffold_project_slug_traversal_writes_nothing_outside_the_vault(self):
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault, outside, slug = self._fixture(root)
            before = self._census(root, skip=vault)
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                rc = cli_main(["scaffold", "--vault", str(vault), "--project", slug, "--from-tasks"])
            self.assertEqual(rc, 1, "a traversal slug must be refused")
            self.assertIn("error:", err.getvalue())
            self.assertEqual(self._census(root, skip=vault), before,
                             "nothing outside the vault may be created or rewritten")
            self.assertFalse((outside / "board").exists())

    def test_symlinked_zone_dir_cannot_smuggle_the_board_out(self):
        # A perfectly safe-looking slug whose {vault}/projects/<slug> is a
        # symlink out of the vault: the string rule cannot see this, only
        # containment on the resolved dir can.
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault, outside, _slug = self._fixture(root)
            _write(outside / "brief.md", "---\ntype: project\n---\n# outside\n")
            (vault / "projects" / "escaped").symlink_to(outside, target_is_directory=True)
            before = self._census(root, skip=vault)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scaffold", "--vault", str(vault), "--project", "escaped", "--from-tasks"])
            self.assertEqual(rc, 1)
            self.assertEqual(self._census(root, skip=vault), before)
            self.assertFalse((outside / "board").exists())

    def test_symlinked_zone_dir_is_refused_by_status_too(self):
        # status is read-only, so nothing downstream re-checks containment:
        # the resolved-dir guard is the only thing standing between a
        # safe-looking slug and a deck read from outside the vault.
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault, outside, _slug = self._fixture(root)
            _write(outside / "board" / "board-data.json",
                   json.dumps({"columns": [{"id": "backlog", "name": "Backlog"}], "cards": []}))
            (vault / "projects" / "escaped").symlink_to(outside, target_is_directory=True)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["status", "--vault", str(vault), "--project", "escaped"])
            self.assertEqual(rc, 1, "status must not read a deck from outside the vault")

    def test_slug_gate_rejects_traversal_before_any_path_is_built(self):
        from board import _project_dir_for_slug
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault, _outside, slug = self._fixture(root)
            self.assertIsNone(_project_dir_for_slug(vault, slug))
            self.assertIsNone(_project_dir_for_slug(vault, "/etc"))
            self.assertIsNone(_project_dir_for_slug(vault, "Has Spaces"))
            self.assertEqual(_project_dir_for_slug(vault, "demo"), vault / "projects" / "demo")

    def test_status_project_slug_traversal_refused(self):
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault, _outside, slug = self._fixture(root)
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                rc = cli_main(["status", "--vault", str(vault), "--project", slug])
            self.assertEqual(rc, 1)
            self.assertIn("slug", err.getvalue())

    def test_absolute_project_slug_refused(self):
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault, outside, _slug = self._fixture(root)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scaffold", "--vault", str(vault), "--project", str(outside)])
            self.assertEqual(rc, 1)
            self.assertFalse((outside / "board").exists())

    def test_good_slug_still_scaffolds(self):
        # The gate must not cost the normal path.
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault, _outside, _slug = self._fixture(root)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scaffold", "--vault", str(vault), "--project", "demo"])
            self.assertEqual(rc, 0)
            self.assertTrue((vault / "projects" / "demo" / "board" / "board-data.json").is_file())


class TestScaffoldContainment(unittest.TestCase):
    """Defense in depth under the slug gates: scaffold_one itself refuses to
    write a board outside the project it belongs to. The one exception is a
    `dest` the operator typed (`--dest`), which reference/board.md explicitly
    allows to point at a code repo."""

    def _proj(self, root: Path) -> Path:
        proj = root / "vault" / "projects" / "demo"
        _write(proj / "brief.md", "---\ntype: project\n---\n# demo\n")
        _write(proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: backlog\n---\n# One\n")
        return proj

    def test_default_dest_outside_the_project_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            proj = self._proj(root)
            rogue = root / "elsewhere" / "board"
            rc, err = _scaffold(proj, rogue, from_tasks=True, data=None, force=False,
                                title=None, board_id="demo")
            self.assertEqual(rc, 1)
            self.assertIn("outside", err)
            self.assertFalse(rogue.exists(), "not even the directory may be created")

    def test_project_dir_outside_the_vault_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "vault" / "projects").mkdir(parents=True)
            rogue_proj = root / "scratchpad" / "outside"
            _write(rogue_proj / "tasks" / "t1.md", "---\ncode: T-1\nstatus: backlog\n---\n# One\n")
            rc, err = _scaffold(rogue_proj, rogue_proj / "board", from_tasks=True, data=None,
                                force=False, title=None, board_id="outside",
                                vault_root=root / "vault")
            self.assertEqual(rc, 1)
            self.assertIn("outside", err)
            self.assertFalse((rogue_proj / "board").exists())

    def test_explicit_dest_may_target_a_code_repo(self):
        # reference/board.md:35 documents `--dest <repo>/_docs/board`. The
        # containment rule must not break it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            proj = self._proj(root)
            repo_dest = root / "repo" / "_docs" / "board"
            rc, err = _scaffold(proj, repo_dest, from_tasks=True, data=None, force=False,
                                title=None, board_id="demo", vault_root=root / "vault",
                                dest_explicit=True)
            self.assertEqual(rc, 0, err)
            self.assertTrue((repo_dest / "board-data.json").is_file())

    def test_cli_dest_flag_still_reaches_a_code_repo(self):
        from board import cli_main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            _write(vault / "Home.md", "---\ntype: vault-home\nupdated: 2026-01-01\n---\n")
            self._proj(root)
            repo_dest = root / "repo" / "_docs" / "board"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scaffold", "--vault", str(vault), "--project", "demo",
                               "--dest", str(repo_dest), "--from-tasks"])
            self.assertEqual(rc, 0)
            self.assertTrue((repo_dest / "board-data.json").is_file())


class TestConcurrentDeckWrites(unittest.TestCase):
    """Audit 2026-07-30 finding 5 (tier 4). scaffold_one reads the deck, merges,
    renders, then blind-writes: an unlocked read-modify-write ending in a
    truncating write_text. A prior audit MEASURED 35 torn or empty deck reads
    in 20 seconds with two writers. The deck is written by the verb and by
    SessionEnd's `board_bridge --ensure-only` reseed, so concurrent writers are
    the normal case. (A third writer, the PostToolUse nudge on every task-note
    Write/Edit, went with the auto-seed it carried; the race it opened is the
    same one, so this test still measures it.)

    Real processes, no sleeps: children spin on a barrier file so they are
    already warm and hit the window together."""

    SCRIPTS = str(Path(__file__).resolve().parent)

    def _runner(self, tmp: Path) -> Path:
        runner = tmp / "runner.py"
        runner.write_text(
            "import os, sys, time\n"
            f"sys.path.insert(0, {self.SCRIPTS!r})\n"
            "import board\n"
            "mode, proj, go = sys.argv[1], sys.argv[2], sys.argv[3]\n"
            "while not os.path.exists(go):\n"
            "    time.sleep(0.001)\n"
            "if mode == 'ensure':\n"
            "    sys.exit(board.cli_main(['--ensure', '--project-dir', proj]))\n"
            "if mode == 'replace':\n"
            "    sys.exit(board.cli_main(['scaffold', '--project-dir', proj,\n"
            "                             '--data', sys.argv[4]]))\n"
            "rc = 0\n"
            "for _ in range(int(sys.argv[4])):\n"
            "    rc |= board.cli_main(['scaffold', '--project-dir', proj, '--data', sys.argv[5]])\n"
            "sys.exit(rc)\n")
        return runner

    def _project(self, tmp: Path, *, tasks: int) -> Path:
        proj = tmp / "proj"
        for n in range(tasks):
            _write(proj / "tasks" / f"t{n:04d}.md",
                   f"---\ncode: T-{n:04d}\nstatus: backlog\n---\n# Task {n} "
                   f"{'padding ' * 12}\n")
        return proj

    def test_a_reader_never_sees_a_torn_deck(self):
        # Atomicity: a reader must see the whole old file or the whole new one.
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            proj = self._project(tmpp, tasks=400)
            dest = proj / "board"
            _ensure(proj)
            data_path = dest / "board-data.json"
            big = tmpp / "big.json"
            big.write_text(data_path.read_text())
            self.assertGreater(big.stat().st_size, 100_000, "window must be wide enough to sample")

            go = tmpp / "go"
            runner = self._runner(tmpp)
            procs = [subprocess.Popen(
                [sys.executable, str(runner), "loop", str(proj), str(go), "12", str(big)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
            go.write_text("")
            torn, ok = [], 0
            while any(p.poll() is None for p in procs):
                try:
                    raw = data_path.read_bytes()
                except FileNotFoundError:
                    torn.append("missing")
                    continue
                try:
                    json.loads(raw)
                    ok += 1
                except json.JSONDecodeError:
                    torn.append(f"{len(raw)} bytes")
            for p in procs:
                p.wait()
            self.assertEqual(torn, [], f"{len(torn)} torn/empty reads: {torn[:5]}")
            self.assertGreater(ok, 20, "the reader must actually have sampled the window")

    def test_the_whole_read_merge_write_runs_under_one_lock(self):
        # Lost updates: atomicity alone does not serialise two read-modify-write
        # cycles, and locking only the write serialises nothing. Probed the way
        # the audit reproduced the bug: from inside the window, since
        # render_template is called after the deck read and before the write.
        # No timing, no sleeps — the probe runs synchronously in the window.
        import subprocess
        import board
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            proj = self._project(tmpp, tasks=3)
            dest = proj / "board"
            _ensure(proj)
            data_path = dest / "board-data.json"
            probe = tmpp / "probe.py"
            probe.write_text(
                "import fcntl, sys\n"
                f"sys.path.insert(0, {self.SCRIPTS!r})\n"
                "from pathlib import Path\n"
                "from _vault_walk import lock_path_for\n"
                "fh = open(lock_path_for(Path(sys.argv[1])), 'a+')\n"
                "try:\n"
                "    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "    print('free')\n"
                "except BlockingIOError:\n"
                "    print('held')\n")

            real = board.render_template
            seen = []

            def spy(deck):
                seen.append(subprocess.run([sys.executable, str(probe), str(data_path)],
                                           capture_output=True, text=True).stdout.strip())
                return real(deck)

            _write(proj / "tasks" / "t9999.md", "---\ncode: T-9999\nstatus: backlog\n---\n# Late\n")
            with unittest.mock.patch.object(board, "render_template", spy):
                rc, _ = _scaffold(proj, dest, from_tasks=True, data=None, force=False,
                                  title=None, board_id="proj")
            self.assertEqual(rc, 0)
            self.assertEqual(seen, ["held"],
                             "another process could enter the read-merge-write window")


class TestTemplateDriftReemit(unittest.TestCase):
    """Finding 24: ensure_board's no-change path never re-rendered board.html,
    so a plugin upgrade shipping a new template left quiet projects serving
    stale HTML forever. The rendered page carries a template stamp; on drift
    (or a vanished html) the page is re-emitted without touching the deck."""

    def _seeded(self, tmp: Path) -> Path:
        project = _make_project(tmp, "demo")
        _write(project / "tasks" / "t-01.md",
               "---\ncode: T-01\nstatus: backlog\n---\n\n# First task\n")
        self.assertEqual(_ensure(project), "created")
        return project

    def test_rendered_html_carries_template_stamp(self):
        import board
        deck = {"title": "x", "cards": [], "columns": [], "categories": {}}
        html = board.render_template(deck)
        self.assertRegex(html, r"adjudant-template [0-9a-f]{16}")

    def test_no_change_reemits_html_when_template_drifts(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp))
            deck_path = project / "board" / "board-data.json"
            deck_before = deck_path.read_bytes()
            new_tpl = Path(tmp) / "board-v2.html"
            new_tpl.write_text(board.TEMPLATE.read_text()
                               + "\n<!-- v2-sentinel -->\n")
            with unittest.mock.patch.object(board, "TEMPLATE", new_tpl):
                self.assertEqual(_ensure(project), "html-refreshed")
            html = (project / "board" / "board.html").read_text()
            self.assertIn("v2-sentinel", html)
            self.assertEqual(deck_path.read_bytes(), deck_before,
                             "an html-only refresh must not touch the deck")

    def test_no_change_stays_quiet_when_template_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp))
            html_path = project / "board" / "board.html"
            before = html_path.read_bytes()
            self.assertEqual(_ensure(project), "no-change")
            self.assertEqual(html_path.read_bytes(), before)

    def test_missing_html_recreated_on_no_change_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp))
            html_path = project / "board" / "board.html"
            html_path.unlink()
            self.assertEqual(_ensure(project), "html-refreshed")
            self.assertTrue(html_path.is_file())


KANBAN_DECK = {
    "title": "Demo",
    "columns": [{"id": "backlog", "name": "Backlog"},
                {"id": "doing", "name": "Doing"},
                {"id": "done", "name": "Done"}],
    "cards": [
        {"id": "T-1", "title": "First task", "column": "backlog"},
        {"id": "T-2", "title": "Ship it", "column": "done"},
        {"id": "T-3", "title": "In flight", "column": "doing"},
    ],
}


class TestKanbanRender(unittest.TestCase):
    """Tranche 2A: the deck renders in the de-facto obsidian-kanban format,
    and the plugin's own state in an existing file survives every rewrite."""

    def test_render_shape(self):
        import board
        md = board.render_kanban(KANBAN_DECK)
        self.assertTrue(md.startswith("---\nkanban-plugin: board\n---\n"))
        # Lanes in deck order
        b = md.index("## Backlog"); d = md.index("## Doing"); n = md.index("## Done")
        self.assertTrue(b < d < n)
        self.assertIn("- [ ] **T-1** First task", md)
        self.assertIn("- [ ] **T-3** In flight", md)
        # done lane cards are checked
        self.assertIn("- [x] **T-2** Ship it", md)

    def test_settings_archive_and_unknown_blocks_preserved(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kanban.md"
            settings = "%% kanban:settings\n```\n{\"kanban-plugin\":\"board\",\"lane-width\":300}\n```\n%%"
            archive = "***\n\n## Archive\n\n- [x] **OLD-1** ancient history\n"
            unknown = "%% my-other-plugin\nstate the plugin owns\n%%"
            path.write_text("---\nkanban-plugin: board\n---\n\n## Backlog\n\n- [ ] **T-1** stale\n\n"
                            + archive + "\n" + unknown + "\n" + settings + "\n")
            board.write_kanban(KANBAN_DECK, path)
            out = path.read_text()
            self.assertIn(settings, out)
            self.assertIn(unknown, out)
            self.assertIn("## Archive\n\n- [x] **OLD-1** ancient history", out)
            # lanes regenerated from the deck, stale card gone from the lanes
            self.assertIn("- [ ] **T-1** First task", out)
            self.assertNotIn("**T-1** stale", out)

    def test_fresh_write_has_no_preserved_sections(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kanban.md"
            board.write_kanban(KANBAN_DECK, path)
            out = path.read_text()
            self.assertNotIn("%% kanban:settings", out)
            self.assertNotIn("## Archive", out)


class TestKanbanLifecycle(unittest.TestCase):
    """Tranche 2A: born explicitly, refreshed ambiently, read back gated on
    mtime - the kanban file is a peer drag surface, never a second truth."""

    def _seeded(self, tmp: Path, *, kanban: bool = False) -> Path:
        project = _make_project(tmp, "demo")
        _write(project / "tasks" / "t-01.md",
               "---\ncode: T-01\nstatus: backlog\n---\n\n# First task\n")
        if kanban:
            rc, _ = _scaffold(project, project / "board", from_tasks=True,
                              data=None, force=False, title=None,
                              board_id=None, kanban=True)
            self.assertEqual(rc, 0)
        else:
            self.assertEqual(_ensure(project), "created")
        return project

    def test_kanban_born_only_with_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp))
            self.assertFalse((project / "board" / "kanban.md").exists())
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp), kanban=True)
            kb = project / "board" / "kanban.md"
            self.assertTrue(kb.is_file())
            self.assertIn("**T-01** First task", kb.read_text())

    def test_ambient_reseed_refreshes_existing_kanban(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp), kanban=True)
            _write(project / "tasks" / "t-02.md",
                   "---\ncode: T-02\nstatus: doing\n---\n\n# Second task\n")
            self.assertEqual(_ensure(project), "reseeded")
            self.assertIn("**T-02** Second task",
                          (project / "board" / "kanban.md").read_text())

    def test_kanban_drag_reads_back_when_newer(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp), kanban=True)
            kb = project / "board" / "kanban.md"
            deck_path = project / "board" / "board-data.json"
            # Simulate an Obsidian drag: move T-01 from Backlog to Doing
            text = kb.read_text()
            text = text.replace("- [ ] **T-01** First task\n", "")
            text = text.replace("## Doing\n", "## Doing\n\n- [ ] **T-01** First task\n")
            kb.write_text(text)
            future = deck_path.stat().st_mtime + 60
            os.utime(kb, (future, future))
            self.assertEqual(_ensure(project), "reseeded")
            deck = json.loads(deck_path.read_text())
            card = [c for c in deck["cards"] if c["id"] == "T-01"][0]
            self.assertEqual(card["column"], "doing")

    def test_kanban_older_than_deck_is_ignored(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            project = self._seeded(Path(tmp), kanban=True)
            kb = project / "board" / "kanban.md"
            deck_path = project / "board" / "board-data.json"
            text = kb.read_text().replace(
                "- [ ] **T-01** First task\n", "")
            kb.write_text(text.replace("## Doing\n",
                                       "## Doing\n\n- [ ] **T-01** First task\n"))
            past = deck_path.stat().st_mtime - 60
            os.utime(kb, (past, past))
            self.assertEqual(_ensure(project), "no-change")
            deck = json.loads(deck_path.read_text())
            card = [c for c in deck["cards"] if c["id"] == "T-01"][0]
            self.assertEqual(card["column"], "backlog")

    def test_archived_cards_never_count_as_placement(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            kb = Path(tmp) / "kanban.md"
            kb.write_text("---\nkanban-plugin: board\n---\n\n## Backlog\n\n"
                          "- [ ] **T-1** live\n\n***\n\n## Archive\n\n"
                          "- [x] **T-9** buried\n")
            got = board.read_kanban_placement(kb)
            self.assertEqual(got, {"T-1": "Backlog"})

    def test_malformed_kanban_is_skipped(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            kb = Path(tmp) / "kanban.md"
            kb.write_bytes(b"\xff\xfe not text")
            self.assertEqual(board.read_kanban_placement(kb), {})

    def test_kanban_write_prints_obsidian_uri(self):
        # Tranche 2C: the affordance is a printed obsidian:// URI, vault-name
        # + vault-relative path, URL-encoded. Print-only, nothing opens.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "My Vault"
            project = vault / "projects" / "demo"
            (project / "tasks").mkdir(parents=True)
            _write(project / "tasks" / "t-01.md",
                   "---\ncode: T-01\nstatus: backlog\n---\n\n# First\n")
            _write(project / "brief.md",
                   "---\ntype: project\nproject_type: coding\n---\n# demo\n")
            rc, err = _scaffold(project, project / "board", from_tasks=True,
                                data=None, force=False, title=None,
                                board_id=None, vault_root=vault, kanban=True)
            self.assertEqual(rc, 0)
            self.assertIn("obsidian://open?vault=My%20Vault"
                          "&file=projects%2Fdemo%2Fboard%2Fkanban.md", err)


# ── templates/board.html: STRUCTURAL guards ────────────────────────────────
#
# READ THIS BEFORE TRUSTING ANYTHING BELOW.
#
# The board template is JavaScript inside a stdlib-only Python plugin. There is
# no JS test runner here and there must not be one, so these tests assert on the
# template's TEXT: that a role attribute is written, that no empty catch block
# came back, that the persisted shape is the moves map rather than the whole
# deck. They are STRUCTURAL, not BEHAVIOURAL. A green run here says the code
# that implements a behaviour is still present and still shaped the way it was
# when the behaviour was verified in a real browser. It does NOT say the board
# works. Every behaviour these guard was driven in Chromium against a scaffolded
# fixture on 2026-07-31 and the observations are in
# .superpowers/sdd/2026-07-28-adjudant-token-discipline/board-html-fix-report.md.
# Re-run that browser pass when you change the template; do not let a green
# suite here stand in for it.

def _template_text() -> str:
    from board import TEMPLATE
    return TEMPLATE.read_text()


def _js_function(src: str, name: str) -> str:
    """The body of `function name(` / `async function name(` by brace matching.

    Lets a guard assert about one function rather than about the whole file,
    so "writeDisk re-reads before it writes" cannot be satisfied by an
    unrelated getFile() call somewhere else in the template.
    """
    for opener in (f"async function {name}(", f"function {name}("):
        i = src.find(opener)
        if i == -1:
            continue
        j = src.index("{", i)
        depth = 0
        for k in range(j, len(src)):
            if src[k] == "{":
                depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0:
                    return src[j:k + 1]
        break
    raise AssertionError(f"function {name} not found in the board template")


def _oklch_to_srgb(lightness: float, chroma: float, hue_deg: float) -> tuple:
    """OKLCH to gamma-encoded sRGB. Pure stdlib: the contrast guards below need
    real numbers, and pulling in a colour library for four constants is not a
    trade this repo makes."""
    h = math.radians(hue_deg)
    a, b = chroma * math.cos(h), chroma * math.sin(h)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    lin = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )
    out = []
    for v in lin:
        v = max(0.0, min(1.0, v))
        out.append(12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)
    return tuple(out)


def _contrast(c1: tuple, c2: tuple) -> float:
    """WCAG 2.x contrast ratio between two gamma-encoded sRGB triples."""
    def lum(c):
        chan = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]
    hi, lo = max(lum(c1), lum(c2)), min(lum(c1), lum(c2))
    return (hi + 0.05) / (lo + 0.05)


def _token(src: str, name: str, *, dark: bool = False) -> tuple:
    """The OKLCH value of a `--token:` declaration, as sRGB.

    `dark` reads the value inside the prefers-color-scheme block instead.
    """
    body = src
    if dark:
        i = src.index("@media (prefers-color-scheme: dark)")
        body = src[i:src.index("}\n  }", i)]
    m = re.search(rf"{re.escape(name)}\s*:\s*oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*\)", body)
    if not m:
        raise AssertionError(f"{name} not found as an oklch() value ({'dark' if dark else 'light'})")
    return _oklch_to_srgb(float(m.group(1)) / 100, float(m.group(2)), float(m.group(3)))


class TestTemplateSavesHonestly(unittest.TestCase):
    """Structural guards for the persistence rewrite. Behaviour verified in
    Chromium; see the module comment above."""

    def setUp(self):
        self.src = _template_text()

    def test_no_silently_swallowed_error_anywhere_in_the_template(self):
        # persistLocal() used to eat every storage failure in `catch(e){}`, so a
        # drag rendered as committed while nothing was written. An empty catch
        # is the shape of that bug; the template must never carry one again.
        empty = re.findall(r"catch\s*\(\s*\w*\s*\)\s*\{\s*\}", self.src)
        self.assertEqual(empty, [], f"empty catch block(s) back in board.html: {empty}")

    def test_a_failed_save_is_reported_and_the_move_is_put_back(self):
        body = _js_function(self.src, "applyMove")
        self.assertIn("await persist()", body)
        # rollback: the pre-move column and the pre-move override set restore
        self.assertIn("card.column=from", body)
        self.assertIn("moves=JSON.parse(undo)", body)
        # and it is said out loud, in the page and to a screen reader
        self.assertIn("notice(", body)
        self.assertIn("announce(", body)
        self.assertIn('id="notice"', self.src)
        self.assertIn('role="alert"', self.src)

    def test_storage_write_reports_its_failure_rather_than_returning_true(self):
        body = _js_function(self.src, "writeMoves")
        self.assertIn("ok:false", body)
        self.assertIn("QuotaExceededError", body)
        self.assertIn("catch", body)

    def test_persist_needs_a_store_to_confirm_before_a_move_counts(self):
        body = _js_function(self.src, "persist")
        self.assertIn("local.ok||disk.ok", body.replace(" ", ""))
        self.assertIn("unsaved", body)

    def test_only_the_moves_are_persisted_never_the_whole_deck(self):
        # The old shape (`setItem(LS_KEY, JSON.stringify(state))`) is what made a
        # re-scaffolded title permanently invisible: the stale blob won and the
        # page had no way back. Titles/categories/refs must re-seed every load.
        body = _js_function(self.src, "writeMoves")
        self.assertIn("moves:moves", body.replace(" ", ""))
        self.assertNotIn("JSON.stringify(state)", self.src)
        self.assertNotRegex(self.src, r"setItem\(\s*LS_KEY\s*,\s*JSON\.stringify\(\s*state")

    def test_the_rev_guard_that_keyed_only_on_the_id_set_is_gone(self):
        # deckRev() hashed the SET of card ids, so re-seeding a title, category
        # or related list changed nothing the browser could see.
        self.assertNotIn("deckRev", self.src)
        self.assertNotIn('":rev")===', self.src)

    def test_a_pre_existing_whole_deck_blob_is_migrated_not_dropped(self):
        body = _js_function(self.src, "movesFromLegacyDeck")
        self.assertIn("SEED.cards", body)
        self.assertIn("from:seed[id]", body.replace(" ", ""))

    def test_the_disk_write_re_reads_the_file_first(self):
        # createWritable() truncates. python reseeds the same file, and another
        # tab may own it: writing this tab's boot-time snapshot blind drops
        # every card and lane that landed since.
        body = _js_function(self.src, "writeDisk")
        read_at = body.index("getFile()")
        write_at = body.index("createWritable()")
        self.assertLess(read_at, write_at, "writeDisk writes before it re-reads")
        self.assertIn("mergeMoves(disk,moves)", body.replace(" ", ""))

    def test_another_tab_is_adopted_instead_of_being_clobbered(self):
        self.assertIn('addEventListener("storage"', self.src)
        self.assertIn('addEventListener("visibilitychange"', self.src)

    def test_an_override_is_expressed_against_the_deck_as_it_stands_now(self):
        # A frozen base stops matching the moment the deck absorbs the move, and
        # the NEXT move of that same card is then silently dropped. Verified in
        # the browser: four consecutive moves of one card, all landing on disk.
        body = _js_function(self.src, "recordMove")
        self.assertIn("baseCols[key]", body)
        write = _js_function(self.src, "writeDisk")
        self.assertIn("baseCols=out.cards.map", write.replace(" ", ""))


class TestTemplateIsOperableWithoutAMouse(unittest.TestCase):
    """Structural guards for the accessibility pass. Behaviour verified in
    Chromium; see the module comment above."""

    def setUp(self):
        self.src = _template_text()

    def test_lanes_and_cards_carry_roles_and_names(self):
        for needle in ('setAttribute("role","list")',
                       'setAttribute("role","listitem")',
                       'setAttribute("role","group")',
                       'setAttribute("role","heading")',
                       'setAttribute("aria-labelledby"',
                       'setAttribute("aria-label"'):
            self.assertIn(needle, self.src.replace(", ", ","), needle)

    def test_a_move_is_announced_in_a_live_region(self):
        self.assertIn('id="live"', self.src)
        self.assertIn('role="status"', self.src)
        self.assertIn('aria-live="polite"', self.src)
        self.assertIn("announce(", _js_function(self.src, "applyMove"))

    def test_the_legend_filter_is_a_button_not_a_bare_span(self):
        # It was a <span> with a click handler: no focus, no keydown, no state.
        # The footer tells people to use it.
        body = _js_function(self.src, "render")
        self.assertIn('el("button","k"', body.replace(", ", ","))
        self.assertIn('setAttribute("aria-pressed"', body.replace(", ", ","))

    def test_the_focus_ring_never_uses_the_category_hue(self):
        # The eight palette hues measure 1.65:1 to 2.28:1 on --surface, under
        # the 3:1 that SC 1.4.11 requires of a focus indicator.
        m = re.search(r"\.ticket:focus-visible\{([^}]*)\}", self.src)
        self.assertIsNotNone(m, ".ticket:focus-visible rule missing")
        self.assertNotIn("var(--c)", m.group(1))
        self.assertIn("var(--text)", m.group(1))

    def test_faint_text_clears_4_5_to_1_in_both_colour_schemes(self):
        # --text-faint carries the lane counts, the empty-lane markers, the save
        # status and the whole footer, all at 11px, which is far below the
        # large-text threshold, so 4.5:1 applies. It measured 3.43:1.
        for dark in (False, True):
            faint = _token(self.src, "--text-faint", dark=dark)
            for surface in ("--bg", "--surface", "--surface-2"):
                ratio = _contrast(faint, _token(self.src, surface, dark=dark))
                self.assertGreaterEqual(
                    round(ratio, 2), 4.5,
                    f"--text-faint on {surface} is {ratio:.2f}:1 "
                    f"({'dark' if dark else 'light'}), under WCAG 1.4.3")

    def test_a_touch_only_device_has_a_way_to_move_a_card(self):
        # HTML5 drag events do not fire from touch, and the bracket keys need a
        # keyboard: without this the board is silently read-only on a phone.
        body = _js_function(self.src, "tapToMove")
        self.assertIn("(pointer: coarse)", body)
        self.assertIn("(hover: hover)", body)
        self.assertIn("tapToMove()", _js_function(self.src, "render"))
        self.assertIn("tapToMove()", _js_function(self.src, "ticketNode"))


class TestTemplateRendersOnlyWhatItCanVouchFor(unittest.TestCase):
    """Structural guards for the correctness pass. Behaviour verified in
    Chromium; see the module comment above."""

    def setUp(self):
        self.src = _template_text()

    def test_a_deck_colour_cannot_make_the_offline_board_fetch(self):
        # A category colour lands in a custom property backing
        # `background:var(--c)`. A deck carrying url(https://…) made a board
        # that is supposed to be fully offline issue a real network request.
        body = _js_function(self.src, "safeColor")
        self.assertIn('CSS.supports("color"', body)
        self.assertIn("FETCHING_CSS", body)
        self.assertRegex(self.src, r"FETCHING_CSS\s*=\s*/[^/]*url")
        self.assertIn("safeColor(colors[n]", _js_function(self.src, "normalize"))

    def test_a_deck_missing_a_key_fails_visibly_instead_of_rendering_empty(self):
        body = _js_function(self.src, "normalize")
        self.assertIn("deck root is not a JSON object", body)
        self.assertIn("deck has no columns", body)
        self.assertIn("Array.isArray(d.cards)", body)
        self.assertIn("fatal(e)", _js_function(self.src, "boot"))
        self.assertIn("<noscript>", self.src)
        # an unrendered page must not read as an empty one
        self.assertIn('<b id="orderCount">—</b>', self.src)
        self.assertIn('<b id="stageCount">—</b>', self.src)

    def test_the_unfiled_lane_never_paints_a_drop_it_will_refuse(self):
        body = _js_function(self.src, "render")
        self.assertIn("if(droppable){", body.replace(" ", ""))
        self.assertNotIn('col.id!=="__unfiled"', body)
        self.assertIn("droppable:false", body.replace(" ", ""))

    def test_terminal_lane_treatment_comes_from_the_column_not_two_literals(self):
        # merge_deck deliberately preserves renamed and added lanes across a
        # reseed; a lane renamed from `done` to `shipped` lost its stamp and its
        # muted treatment because both keyed on the literal ids.
        render = _js_function(self.src, "render")
        self.assertNotIn('col.id==="done"', render)
        self.assertNotIn('col.id==="icebox"', render)
        self.assertIn("col.stamp", _js_function(self.src, "laneStamp"))
        self.assertIn("col.muted", _js_function(self.src, "laneMuted"))

    def test_a_drag_moves_the_card_that_was_dragged_not_the_first_id_match(self):
        # With duplicate ids, .find(c=>c.id===id) moved the wrong card.
        self.assertNotIn("state.cards.find(c=>c.id===id)", self.src)
        ticket = _js_function(self.src, "ticketNode")
        self.assertIn('setData("text/plain",String(key))', ticket.replace(", ", ","))
        render = _js_function(self.src, "render")
        self.assertIn("Number(ev.dataTransfer.getData", render)
        self.assertIn("Number.isInteger(key)", render)
        self.assertIn("duplicateIds", _js_function(self.src, "normalize"))


class TestDeckToTaskWriteBack(unittest.TestCase):
    """v1.0.0: a drag on ANY board surface must reach the vault.

    Before this, merge_deck's dragged-column-wins rule meant a card moved in
    the browser or in Obsidian diverged from its task note permanently and
    silently: the deck said done, the note said todo, and check/dream/sitrep
    all read the note. The board lied about the vault."""

    def _project(self, tmp: Path, status: str = "todo") -> Path:
        project = _make_project(tmp, "demo")
        _write(project / "tasks" / "t-01.md",
               f"---\ntype: task\ncode: T-01\nstatus: {status}\ncategory: build\n"
               f"tags:\n  - task\n---\n\n# Ship the thing\n\nbody line\n")
        return project

    def _note(self, project: Path) -> Path:
        return project / "tasks" / "t-01.md"

    def _status_of(self, project: Path) -> str:
        for ln in self._note(project).read_text().splitlines():
            if ln.startswith("status:"):
                return ln.split(":", 1)[1].strip()
        return ""

    def _drag(self, project: Path, column: str) -> dict:
        deck_p = project / "board" / "board-data.json"
        deck = json.loads(deck_p.read_text())
        deck["cards"][0]["column"] = column
        deck_p.write_text(json.dumps(deck, indent=2))
        return deck

    def _edit_note(self, project: Path, status: str) -> None:
        """Set `status:` the way a human editing in Obsidian would."""
        note = self._note(project)
        note.write_text(re.sub(r"(?m)^status:.*$", f"status: {status}",
                               note.read_text()))

    def test_drag_writes_canonical_status_back(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            self.assertEqual(_ensure(project), "created")
            deck = self._drag(project, "done")
            changed = board.sync_deck_to_tasks(project, deck)
            self.assertEqual(len(changed), 1)
            self.assertEqual(self._status_of(project), "done")

    def test_next_lane_writes_next(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            _ensure(project)
            deck = self._drag(project, "next")
            board.sync_deck_to_tasks(project, deck)
            self.assertEqual(self._status_of(project), "next")

    def test_blocked_survives_the_review_lane(self):
        # `blocked` already maps to the review column, so a card sitting in
        # review is NOT divergent - rewriting it to `review` would silently
        # destroy the distinction the author made.
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), status="blocked")
            _ensure(project)
            deck = json.loads((project / "board" / "board-data.json").read_text())
            self.assertEqual(deck["cards"][0]["column"], "review")
            self.assertEqual(board.sync_deck_to_tasks(project, deck), [])
            self.assertEqual(self._status_of(project), "blocked")

    def test_an_unknown_status_is_never_rewritten_on_disk(self):
        # The aliases are retired, so `wip` is now off-vocabulary. What must
        # NOT happen is a silent rewrite: truth.py reports the value and a
        # person fixes it. A tool that quietly corrected the file would hide
        # the very drift the report exists to surface.
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), status="wip")
            _ensure(project)
            deck = json.loads((project / "board" / "board-data.json").read_text())
            board.sync_deck_to_tasks(project, deck)
            self.assertEqual(self._status_of(project), "wip",
                             "an off-vocabulary status stays exactly as written")

    def test_the_unknown_status_reaches_the_card_verbatim(self):
        # So the board can show what is actually written, rather than a lane
        # name that was never in the file.
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), status="obsolete")
            cards = board.cards_from_tasks(project)
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["taskStatus"], "obsolete")

    def test_no_divergence_leaves_the_file_byte_identical(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            _ensure(project)
            before = self._note(project).read_bytes()
            deck = json.loads((project / "board" / "board-data.json").read_text())
            self.assertEqual(board.sync_deck_to_tasks(project, deck), [])
            self.assertEqual(self._note(project).read_bytes(), before)

    def test_rewrite_preserves_other_fields_and_body(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            _ensure(project)
            deck = self._drag(project, "icebox")
            board.sync_deck_to_tasks(project, deck)
            text = self._note(project).read_text()
            for keep in ("type: task", "code: T-01", "category: build",
                         "# Ship the thing", "body line", "  - task"):
                self.assertIn(keep, text)
            self.assertEqual(text.count("status:"), 1)

    def test_hand_added_card_never_creates_a_note(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            _ensure(project)
            deck = json.loads((project / "board" / "board-data.json").read_text())
            deck["cards"].append({"id": "HAND-1", "title": "typed on the board",
                                  "column": "doing"})
            self.assertEqual(board.sync_deck_to_tasks(project, deck), [])
            self.assertFalse((project / "tasks" / "HAND-1.md").exists())

    def test_undecodable_note_is_skipped_not_fatal(self):
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            _ensure(project)
            deck = self._drag(project, "done")
            (project / "tasks" / "broken.md").write_bytes(b"---\ntype: task\n\xff\xfe\n")
            changed = board.sync_deck_to_tasks(project, deck)
            self.assertEqual(len(changed), 1)   # the good one still syncs

    def test_ensure_reports_the_write_back_instead_of_no_change(self):
        # The lie this fixes: a drag with no task-file change reseeded to
        # "no-change" while the note went stale.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            _ensure(project)
            self._drag(project, "done")
            verdict = _ensure(project)
            self.assertEqual(verdict, "tasks-synced")
            self.assertEqual(self._status_of(project), "done")

    def test_a_note_edited_in_obsidian_is_not_reverted(self):
        # The inverse of the v1.0.0 bug, and worse because it WRITES: the
        # deck's column outranked a fresh human edit, so marking a task done
        # in Obsidian was silently rolled back by the next ambient hook.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), status="doing")
            _ensure(project)
            self._edit_note(project, "done")
            _ensure(project)
            self.assertEqual(self._status_of(project), "done")

    def test_a_note_edit_moves_the_card_on_the_board(self):
        # Same event, other store: the board must follow the note, not
        # discard it. Reverting the note and leaving the deck put is what
        # made the two stores disagree permanently.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), status="doing")
            _ensure(project)
            self._edit_note(project, "done")
            _ensure(project)
            deck = json.loads((project / "board" / "board-data.json").read_text())
            self.assertEqual(deck["cards"][0]["column"], "done")

    def test_a_drag_still_wins_when_the_note_did_not_move(self):
        # Regression guard on v1.0.0's actual fix: teaching the note to win
        # must not cost the board its own authority over a real drag.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), status="backlog")
            _ensure(project)
            self._drag(project, "done")
            _ensure(project)
            self.assertEqual(self._status_of(project), "done")

    def test_a_legacy_deck_without_provenance_writes_nothing(self):
        # A deck seeded before provenance existed cannot say WHICH store
        # moved. An unattended write path with no preview and no backup must
        # not guess: leave both stores alone and record the ancestor instead.
        import board
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), status="done")
            _ensure(project)
            deck_p = project / "board" / "board-data.json"
            deck = json.loads(deck_p.read_text())
            deck["cards"][0].pop("taskStatus", None)     # pre-provenance deck
            deck["cards"][0]["column"] = "doing"
            deck_p.write_text(json.dumps(deck, indent=2))
            self.assertEqual(board.sync_deck_to_tasks(project, deck), [])
            self.assertEqual(self._status_of(project), "done")

    def test_kanban_drag_reaches_the_task_note(self):
        # The full chain: Obsidian drag -> kanban.md -> deck -> task note.
        import os
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            rc, _ = _scaffold(project, project / "board", from_tasks=True,
                              data=None, force=False, title=None,
                              board_id=None, kanban=True)
            self.assertEqual(rc, 0)
            kb = project / "board" / "kanban.md"
            deck_p = project / "board" / "board-data.json"
            text = kb.read_text().replace("- [ ] **T-01** Ship the thing\n", "")
            kb.write_text(text.replace("## Doing\n",
                                       "## Doing\n\n- [ ] **T-01** Ship the thing\n"))
            future = deck_p.stat().st_mtime + 60
            os.utime(kb, (future, future))
            _ensure(project)
            self.assertEqual(self._status_of(project), "doing")


if __name__ == "__main__":
    unittest.main()
