"""Tests for adjudant/scripts/_lifecycle.py — the guided triage.

27 projects sit flat. The verb that moved them went unused for a year because
nothing ever asked. This asks once per project and moves nothing on its own.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from _lifecycle import TriageEntry, apply_move, triage_plan


def _mk(vault: Path, slug: str, zone: str = "active", status: str = None,
        sessions=()) -> Path:
    pdir = (vault / "projects" / zone / slug) if zone else (vault / "projects" / slug)
    pdir.mkdir(parents=True, exist_ok=True)
    fm = "---\ntype: project\nupdated: 2026-09-01\n"
    if status:
        fm += f"status: {status}\n"
    (pdir / "brief.md").write_text(fm + f"---\n\n# {slug}\n")
    if sessions:
        (pdir / "sessions").mkdir(exist_ok=True)
        for d in sessions:
            (pdir / "sessions" / f"{d}.md").write_text("---\ntype: session\n---\n")
    return pdir


class TestTriagePlan(unittest.TestCase):

    def test_one_entry_per_project_and_nothing_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            for i in range(27):
                _mk(vault, f"p{i:02d}", sessions=["2026-08-30"])
            before = sorted(str(p) for p in (vault / "projects").rglob("brief.md"))
            plan = triage_plan(vault, date(2026, 9, 1))
            self.assertEqual(len(plan), 27)
            after = sorted(str(p) for p in (vault / "projects").rglob("brief.md"))
            self.assertEqual(before, after, "triage moved something")

    def test_entry_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p", sessions=["2026-08-30"])
            entry = triage_plan(vault, date(2026, 9, 1))[0]
            self.assertIsInstance(entry, TriageEntry)
            self.assertEqual(entry.slug, "p")
            self.assertEqual(entry.zone, "active")
            self.assertEqual(entry.suggested, "active")
            self.assertEqual(entry.last_session, "2026-08-30")
            self.assertEqual(entry.days_quiet, 2)

    def test_quiet_active_project_is_suggested_paused(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p", sessions=["2026-06-01"])
            entry = triage_plan(vault, date(2026, 9, 1))[0]
            self.assertEqual(entry.suggested, "paused")
            self.assertIn("92 days", entry.reason)

    def test_boundary_at_thirty_days_suggests_paused(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p", sessions=["2026-08-02"])
            entry = triage_plan(vault, date(2026, 9, 1))[0]
            self.assertEqual(entry.days_quiet, 30)
            self.assertEqual(entry.suggested, "paused")

    def test_a_project_with_no_sessions_gets_a_prompt_and_no_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p")
            entry = triage_plan(vault, date(2026, 9, 1))[0]
            self.assertIsNone(entry.days_quiet)
            self.assertEqual(entry.suggested, "active")
            self.assertIn("no session", entry.reason)

    def test_an_unmigrated_project_is_suggested_its_mapped_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "cold", zone="_fridge", status="fridge",
                sessions=["2026-08-30"])
            _mk(vault, "shipped", zone="_archive", status="done",
                sessions=["2026-08-30"])
            _mk(vault, "bare", zone="", status="active", sessions=["2026-08-30"])
            by_slug = {e.slug: e for e in triage_plan(vault, date(2026, 9, 1))}
            self.assertEqual(by_slug["cold"].suggested, "paused")
            self.assertEqual(by_slug["shipped"].suggested, "finished")
            self.assertEqual(by_slug["bare"].suggested, "active")
            for e in by_slug.values():
                self.assertIn("not in a lifecycle folder", e.reason)

    def test_a_legacy_status_outranks_the_folder_alias(self):
        # projects/{slug} with `status: done` belongs in finished/, not active/.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p", zone="", status="done", sessions=["2026-08-30"])
            self.assertEqual(triage_plan(vault, date(2026, 9, 1))[0].suggested,
                             "finished")

    def test_quiet_paused_and_finished_projects_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "a", zone="paused", sessions=["2024-01-01"])
            _mk(vault, "b", zone="finished", sessions=["2024-01-01"])
            _mk(vault, "c", zone="archive", sessions=["2024-01-01"])
            for e in triage_plan(vault, date(2026, 9, 1)):
                self.assertEqual(e.suggested, e.zone, e.slug)

    def test_sorted_by_zone_then_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "z", zone="active")
            _mk(vault, "a", zone="active")
            _mk(vault, "m", zone="archive")
            self.assertEqual([e.slug for e in triage_plan(vault, date(2026, 9, 1))],
                             ["a", "z", "m"])


class TestApplyMove(unittest.TestCase):

    def test_moves_one_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p", sessions=["2026-06-01"])
            new = apply_move(vault, "p", "paused")
            self.assertEqual(new, vault / "projects" / "paused" / "p")
            self.assertTrue((new / "brief.md").is_file())
            self.assertFalse((vault / "projects" / "active" / "p").exists())

    def test_moves_an_unmigrated_project_into_a_named_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p", zone="_fridge")
            new = apply_move(vault, "p", "paused")
            self.assertEqual(new, vault / "projects" / "paused" / "p")
            self.assertFalse((vault / "projects" / "_fridge" / "p").exists())

    def test_links_into_the_project_still_resolve_after_a_move(self):
        from _vault_walk import build_vault_index, resolve_wikilink
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pdir = _mk(vault, "p")
            (pdir / "notes").mkdir()
            (pdir / "notes" / "a.md").write_text("---\ntype: note\n---\n\n# A\n")
            self.assertTrue(resolve_wikilink("p/notes/a", build_vault_index(vault)))
            apply_move(vault, "p", "archive")
            self.assertTrue(resolve_wikilink("p/notes/a", build_vault_index(vault)))

    def test_unknown_zone_is_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p")
            with self.assertRaises(ValueError):
                apply_move(vault, "p", "_fridge")

    def test_missing_project_is_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                apply_move(Path(tmp), "nope", "paused")

    def test_occupied_destination_is_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "p", zone="active")
            _mk(vault, "p", zone="paused")
            with self.assertRaises(ValueError):
                apply_move(vault, "p", "paused")

    def test_a_move_to_the_current_zone_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pdir = _mk(vault, "p", zone="paused")
            self.assertEqual(apply_move(vault, "p", "paused"), pdir)


if __name__ == "__main__":
    unittest.main()
