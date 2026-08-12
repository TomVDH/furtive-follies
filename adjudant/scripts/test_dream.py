"""Tests for adjudant/scripts/dream.py."""

import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from dream import (
    cli_main as dream_cli,
    detect_contradiction_candidates,
    detect_dangling_scopes,
    detect_documentation_gaps,
    detect_orphan_questions,
    detect_orphan_threads,
    detect_redundancy_clusters,
    detect_stale_refs,
    detect_staleness,
    detect_supersession_signals,
    detect_unacted_decisions,
    run_dream,
)
from _vault_walk import build_vault_index, walk_project

TODAY = dt.date(2026, 6, 1)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


_CODING_BRIEF_SECTIONS = (
    "# Test Project\n\n"
    "## INTRO\nReal intro prose here.\n\n"
    "## TECHNICAL STACK\nPython.\n\n"
    "## CONSTRAINTS\nNone notable.\n\n"
    "## WORK NOTES\nOngoing.\n\n"
    "## MILESTONES\n- {first milestone}\n"   # template placeholder — skipped by dangling-scope
)


def _make_minimal_project(root: Path, slug: str = "test", project_type: str = "coding") -> None:
    body = _CODING_BRIEF_SECTIONS if project_type == "coding" else "# Test Project\n\n## INTRO\nx\n\n## WORK NOTES\ny\n"
    _write_file(root / "brief.md", (
        "---\n"
        "type: project\n"
        f"project_type: {project_type}\n"
        f"slug: {slug}\n"
        "tags:\n  - project\n"
        f"---\n\n{body}"
    ))
    _write_file(root / "_handoff.md", "---\ntype: handoff\nupdated: 2026-05-26\n---\n\nbody")
    (root / "sessions").mkdir()
    (root / "images").mkdir()


# ============================================================
# Staleness
# ============================================================


class TestDetectStaleness(unittest.TestCase):

    def test_old_note_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "old.md", "---\ntype: note\nupdated: 2024-01-01\n---\n\nold body line")
            files = list(walk_project(root))
            out = detect_staleness(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["file"], "old.md")
            self.assertGreater(out[0]["age_days"], 180)
            self.assertIn("old body line", out[0]["excerpt_head"])

    def test_recent_note_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "fresh.md", "---\ntype: note\nupdated: 2026-05-20\n---\n\nfresh")
            files = list(walk_project(root))
            self.assertEqual(detect_staleness(files, TODAY), [])

    def test_filename_date_used_when_no_frontmatter_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "sessions" / "2024-02-02.md", "---\ntype: session\n---\n\nlog")
            files = list(walk_project(root))
            out = detect_staleness(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["type"], "session")

    def test_undateable_file_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "n.md", "---\ntype: note\n---\n\nno date here")
            files = list(walk_project(root))
            self.assertEqual(detect_staleness(files, TODAY), [])


# ============================================================
# Supersession
# ============================================================


class TestDetectSupersession(unittest.TestCase):

    def test_same_topic_decisions_paired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-auth-strategy.md",
                        "---\ntype: decision\ndate: 2026-01-01\n---\n\nUse sessions for auth.")
            _write_file(root / "decisions" / "2026-05-01-auth-strategy.md",
                        "---\ntype: decision\ndate: 2026-05-01\n---\n\nUse JWT for auth.")
            files = list(walk_project(root))
            out = detect_supersession_signals(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["older"]["file"], "decisions/2026-01-01-auth-strategy.md")
            self.assertEqual(out[0]["newer"]["file"], "decisions/2026-05-01-auth-strategy.md")
            self.assertFalse(out[0]["older_has_superseded_marker"])

    def test_unrelated_decisions_not_paired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-database-choice.md",
                        "---\ntype: decision\ndate: 2026-01-01\n---\n\nPostgres.")
            _write_file(root / "decisions" / "2026-05-01-styling-approach.md",
                        "---\ntype: decision\ndate: 2026-05-01\n---\n\nTailwind.")
            files = list(walk_project(root))
            self.assertEqual(detect_supersession_signals(files, TODAY), [])

    def test_existing_superseded_marker_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-cache-layer.md",
                        "---\ntype: decision\ndate: 2026-01-01\nsuperseded: true\n---\n\nRedis cache layer.")
            _write_file(root / "decisions" / "2026-05-01-cache-layer.md",
                        "---\ntype: decision\ndate: 2026-05-01\n---\n\nIn-memory cache layer.")
            files = list(walk_project(root))
            out = detect_supersession_signals(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0]["older_has_superseded_marker"])


# ============================================================
# Contradiction
# ============================================================


class TestDetectContradiction(unittest.TestCase):

    def test_change_verb_with_overlap_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-build-tooling.md",
                        "---\ntype: decision\ndate: 2026-01-01\n---\n\nWe use webpack for build tooling.")
            _write_file(root / "decisions" / "2026-05-01-build-tooling.md",
                        "---\ntype: decision\ndate: 2026-05-01\n---\n\nWe no longer use webpack; switched to vite.")
            files = list(walk_project(root))
            out = detect_contradiction_candidates(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0]["a"]["line"] or out[0]["b"]["line"])

    def test_overlap_without_change_verb_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "a-build-tooling.md", "---\ntype: note\n---\n\nNotes about build tooling setup.")
            _write_file(root / "b-build-tooling.md", "---\ntype: note\n---\n\nMore on build tooling config.")
            files = list(walk_project(root))
            self.assertEqual(detect_contradiction_candidates(files, TODAY), [])


# ============================================================
# Redundancy
# ============================================================


class TestDetectRedundancy(unittest.TestCase):

    def test_near_duplicate_notes_clustered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = "deployment pipeline runs migrations then restarts workers gracefully always"
            _write_file(root / "notes" / "deploy-a.md", f"---\ntype: note\n---\n\n{shared} alpha")
            _write_file(root / "notes" / "deploy-b.md", f"---\ntype: note\n---\n\n{shared} beta")
            files = list(walk_project(root))
            out = detect_redundancy_clusters(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertEqual(len(out[0]["files"]), 2)

    def test_distinct_notes_not_clustered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "notes" / "alpha.md", "---\ntype: note\n---\n\nquantum entanglement physics lecture")
            _write_file(root / "notes" / "beta.md", "---\ntype: note\n---\n\ngardening tomatoes compost watering")
            files = list(walk_project(root))
            self.assertEqual(detect_redundancy_clusters(files, TODAY), [])


# ============================================================
# Stale refs
# ============================================================


class TestDetectStaleRefs(unittest.TestCase):

    def test_archive_ref_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "n.md", "---\ntype: note\n---\n\nSee [[_archive/old-plan]] for history.")
            files = list(walk_project(root))
            out = detect_stale_refs(files, TODAY, vault_index=None)
            self.assertEqual(len(out), 1)
            self.assertIn("archived", out[0]["reason"])

    def test_old_dated_ref_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "n.md", "---\ntype: note\n---\n\nBack in [[2024-01-01]] we decided X.")
            files = list(walk_project(root))
            out = detect_stale_refs(files, TODAY, vault_index=None)
            self.assertEqual(len(out), 1)
            self.assertIn("dated target", out[0]["reason"])

    def test_fresh_ref_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "n.md", "---\ntype: note\n---\n\nSee [[notes/current-plan]].")
            files = list(walk_project(root))
            self.assertEqual(detect_stale_refs(files, TODAY, vault_index=None), [])

    def test_unresolved_skipped_when_index_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "n.md", "---\ntype: note\n---\n\nSee [[_archive/ghost]].")
            files = list(walk_project(root))
            idx = build_vault_index(root)  # ghost doesn't exist → unresolved
            self.assertEqual(detect_stale_refs(files, TODAY, vault_index=idx), [])


# ============================================================
# Orphan questions
# ============================================================


class TestDetectOrphanQuestions(unittest.TestCase):

    def test_old_todo_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "sessions" / "2024-01-01.md",
                        "---\ntype: session\n---\n\n- TODO: decide on the cache eviction policy")
            files = list(walk_project(root))
            out = detect_orphan_questions(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertIn("cache eviction", out[0]["text"])

    def test_recent_todo_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "sessions" / "2026-05-28.md",
                        "---\ntype: session\n---\n\n- TODO: still fresh")
            files = list(walk_project(root))
            self.assertEqual(detect_orphan_questions(files, TODAY), [])

    def test_code_fence_todo_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "old.md",
                        "---\ntype: note\nupdated: 2024-01-01\n---\n\n```\n# TODO: in code\n```\nclean prose")
            files = list(walk_project(root))
            self.assertEqual(detect_orphan_questions(files, TODAY), [])


# ============================================================
# Orphan threads
# ============================================================


class TestDetectOrphanThreads(unittest.TestCase):

    def test_unlinked_old_note_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "notes" / "lonely.md", "---\ntype: note\nupdated: 2024-01-01\n---\n\nnobody links here")
            files = list(walk_project(root))
            out = detect_orphan_threads(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["file"], "notes/lonely.md")

    def test_linked_note_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "notes" / "popular.md", "---\ntype: note\nupdated: 2024-01-01\n---\n\nlinked")
            _write_file(root / "hub.md", "---\ntype: doc\n---\n\nSee [[popular]].")
            files = list(walk_project(root))
            self.assertEqual(detect_orphan_threads(files, TODAY), [])

    def test_recent_unlinked_note_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "notes" / "newish.md", "---\ntype: note\nupdated: 2026-05-20\n---\n\nfresh orphan")
            files = list(walk_project(root))
            self.assertEqual(detect_orphan_threads(files, TODAY), [])


# ============================================================
# Unacted decisions
# ============================================================


class TestDetectUnactedDecisions(unittest.TestCase):

    def test_aged_active_decision_with_consequence_unreferenced_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-migrate.md",
                        "---\ntype: decision\nstatus: active\ndate: 2026-01-01\n---\n\n"
                        "## Decision\nMigrate to vite.\n\n## Consequence\nRewrite the build config.\n")
            files = list(walk_project(root))
            out = detect_unacted_decisions(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertIn("Rewrite", out[0]["consequence_excerpt"])

    def test_referenced_by_session_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-migrate.md",
                        "---\ntype: decision\nstatus: active\ndate: 2026-01-01\n---\n\n"
                        "## Decision\nMigrate.\n\n## Consequence\nRewrite config.\n")
            _write_file(root / "sessions" / "2026-02-01.md",
                        "---\ntype: session\n---\n\nDid the [[2026-01-01-migrate]] rewrite today.")
            files = list(walk_project(root))
            self.assertEqual(detect_unacted_decisions(files, TODAY), [])

    def test_recent_decision_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-05-25-x.md",
                        "---\ntype: decision\nstatus: active\ndate: 2026-05-25\n---\n\n"
                        "## Consequence\nDo the thing.\n")
            files = list(walk_project(root))
            self.assertEqual(detect_unacted_decisions(files, TODAY), [])

    def test_superseded_decision_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-x.md",
                        "---\ntype: decision\nstatus: superseded\ndate: 2026-01-01\n---\n\n"
                        "## Consequence\nWould have done the thing.\n")
            files = list(walk_project(root))
            self.assertEqual(detect_unacted_decisions(files, TODAY), [])


# ============================================================
# Documentation gaps
# ============================================================


class TestDetectDocumentationGaps(unittest.TestCase):

    def test_stub_note_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "notes" / "thin.md", "---\ntype: note\n---\n\none line only")
            files = list(walk_project(root))
            out = detect_documentation_gaps(files, TODAY)
            self.assertTrue(any(g["kind"] == "stub" for g in out))

    def test_session_with_work_no_decision_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "sessions" / "2026-05-01.md",
                        "---\ntype: session\n---\n\n## Log\n"
                        "- 09:00 a\n- 09:10 b\n- 09:20 c\n- 09:30 d\n- 09:40 e\n- 09:50 f\n")
            files = list(walk_project(root))
            out = detect_documentation_gaps(files, TODAY)
            self.assertTrue(any(g["kind"] == "session-without-decision" for g in out))

    def test_session_with_decision_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "sessions" / "2026-05-01.md",
                        "---\ntype: session\n---\n\n## Log\n"
                        "- 09:00 a\n- 09:10 b\n- 09:20 c\n- 09:30 d\n- 09:40 e\n")
            _write_file(root / "decisions" / "2026-05-01-x.md",
                        "---\ntype: decision\nstatus: active\ndate: 2026-05-01\n---\n\n## Decision\nx\n## Context\ny\n## Consequence\nz\n")
            files = list(walk_project(root))
            out = detect_documentation_gaps(files, TODAY)
            self.assertFalse(any(g["kind"] == "session-without-decision" for g in out))

    def test_template_scaffold_not_flagged_as_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # canonical skeletal scaffold under templates/ — must NOT be a stub
            _write_file(root / "templates" / "decision.md", "---\ntype: decision\n---\n\n## Decision\n")
            files = list(walk_project(root))
            out = detect_documentation_gaps(files, TODAY)
            self.assertFalse(any(g["kind"] == "stub" for g in out))

    def test_brief_missing_sections_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "brief.md",
                        "---\ntype: project\nproject_type: coding\nslug: t\n---\n\n# T\n\n## INTRO\nhi\n")
            files = list(walk_project(root))
            out = detect_documentation_gaps(files, TODAY)
            gap = next(g for g in out if g["kind"] == "brief-missing-sections")
            self.assertIn("MILESTONES", gap["detail"])


# ============================================================
# Dangling scopes
# ============================================================


class TestDetectDanglingScopes(unittest.TestCase):

    def test_untouched_milestone_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "brief.md",
                        "---\ntype: project\nproject_type: coding\nslug: t\n---\n\n# T\n\n"
                        "## MILESTONES\n- build the scheduler dashboard\n")
            _write_file(root / "sessions" / "2026-05-01.md", "---\ntype: session\n---\n\nworked on auth")
            files = list(walk_project(root))
            out = detect_dangling_scopes(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertIn("scheduler", out[0]["item"])

    def test_milestone_mentioned_in_session_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "brief.md",
                        "---\ntype: project\nproject_type: coding\nslug: t\n---\n\n# T\n\n"
                        "## MILESTONES\n- build the scheduler dashboard\n")
            _write_file(root / "sessions" / "2026-05-01.md",
                        "---\ntype: session\n---\n\nstarted the scheduler dashboard work")
            files = list(walk_project(root))
            self.assertEqual(detect_dangling_scopes(files, TODAY), [])

    def test_template_placeholder_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "brief.md",
                        "---\ntype: project\nproject_type: coding\nslug: t\n---\n\n# T\n\n"
                        "## MILESTONES\n- {first milestone}\n")
            files = list(walk_project(root))
            self.assertEqual(detect_dangling_scopes(files, TODAY), [])


# ============================================================
# End-to-end run_dream
# ============================================================


class TestRunDream(unittest.TestCase):

    def test_clean_project_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minimal_project(root)
            (root / "decisions").mkdir()
            _write_file(root / "decisions" / "2026-05-20-fresh.md",
                        "---\ntype: decision\nstatus: active\ndate: 2026-05-20\n---\n\n"
                        "## Decision\nChose X.\n\n## Context\nBecause Y.\n\n## Consequence\nDo Z.\n")
            report = run_dream(root, root, today=TODAY)
            self.assertEqual(report["meta"]["project_slug"], "test")
            self.assertEqual(report["summary"]["candidates"], 0)

    def test_dirty_project_candidates_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minimal_project(root)
            _write_file(root / "stale.md", "---\ntype: note\nupdated: 2023-01-01\n---\n\nold thinking")
            _write_file(root / "sessions" / "2024-01-01.md",
                        "---\ntype: session\n---\n\n- TODO: resolve the open thread")
            report = run_dream(root, root, today=TODAY)
            self.assertGreater(report["summary"]["candidates"], 0)
            self.assertGreater(report["summary"]["staleness"], 0)
            self.assertGreater(report["summary"]["orphan_questions"], 0)

    def test_emits_serializable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minimal_project(root)
            _write_file(root / "stale.md", "---\ntype: note\nupdated: 2023-01-01\n---\n\nold")
            report = run_dream(root, root, today=TODAY)
            payload = json.dumps(report, default=str)
            roundtrip = json.loads(payload)
            self.assertEqual(roundtrip["meta"]["project_slug"], "test")

    def test_today_override_changes_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_minimal_project(root)
            _write_file(root / "n.md", "---\ntype: note\nupdated: 2025-12-01\n---\n\nbody")
            # Far-future today → stale; near today → fresh
            self.assertGreater(run_dream(root, root, today=dt.date(2026, 12, 1))["summary"]["staleness"], 0)
            self.assertEqual(run_dream(root, root, today=dt.date(2025, 12, 15))["summary"]["staleness"], 0)


class TestOpenLoopMarkers(unittest.TestCase):

    def test_double_question_mark_detected(self):
        from dream import OPEN_LOOP_RE
        # Regression: the trailing \b made `??` dead for these common forms
        self.assertTrue(OPEN_LOOP_RE.search("does this even work??"))
        self.assertTrue(OPEN_LOOP_RE.search("??"))
        self.assertTrue(OPEN_LOOP_RE.search("- ?? unresolved thing"))

    def test_word_markers_still_bounded(self):
        from dream import OPEN_LOOP_RE
        self.assertTrue(OPEN_LOOP_RE.search("there is a TODO here"))
        self.assertFalse(OPEN_LOOP_RE.search("TODOS are plural"))  # \b intact


class TestDreamCost(unittest.TestCase):

    def test_estimate_only_is_cost_only_and_stat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brief.md").write_text(
                "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
            (root / "notes").mkdir()
            (root / "notes" / "big.md").write_text("x" * 8000)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dream_cli(["--project-dir", str(root), "--estimate-only"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(set(payload), {"cost"})
            self.assertGreaterEqual(payload["cost"]["est_read_tokens"], 2000)

    def test_normal_run_includes_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brief.md").write_text(
                "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dream_cli(["--project-dir", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("cost", payload)
            self.assertIn("summary", payload)


class TestDeclaredFreshnessPrecedence(unittest.TestCase):
    """v0.22.0: declared epistemic signals outrank mtime heuristics.
    Timeless notes never age out; a declared expiry is stale no matter how
    recently the file was touched; a dangling superseded_by joins the
    supersession catalog."""

    def test_timeless_note_exempt_from_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "law.md",
                        "---\ntype: note\nupdated: 2024-01-01\n"
                        "freshness: timeless\n---\n\nnever ages")
            _write_file(root / "old.md",
                        "---\ntype: note\nupdated: 2024-01-01\n---\n\nages")
            out = detect_staleness(list(walk_project(root)), TODAY)
            names = [e["file"] for e in out]
            self.assertIn("old.md", names)
            self.assertNotIn("law.md", names)

    def test_declared_expiry_beats_fresh_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "expired.md",
                        "---\ntype: note\nupdated: 2026-05-30\n"
                        "valid_until: 2026-05-01\n---\n\nrecently touched, expired")
            out = detect_staleness(list(walk_project(root)), TODAY)
            self.assertEqual(len(out), 1)
            e = out[0]
            self.assertEqual(e["file"], "expired.md")
            self.assertEqual(e.get("reason"), "declared validity expired")

    def test_dangling_superseded_by_joins_supersession_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "old-decision.md",
                        "---\ntype: decision\nstatus: superseded\ndate: 2026-01-01\n"
                        'superseded_by: "[[decisions/nonexistent]]"\n---\n\nbody')
            out = detect_supersession_signals(list(walk_project(root)), TODAY)
            dangling = [e for e in out if e.get("kind") == "dangling-pointer"]
            self.assertEqual(len(dangling), 1)
            self.assertEqual(dangling[0]["target"], "nonexistent")


if __name__ == "__main__":
    unittest.main()
