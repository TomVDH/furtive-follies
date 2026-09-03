"""Tests for adjudant/scripts/dream.py."""

import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

import dream
from dream import (
    cli_main as dream_cli,
    detect_dangling_scopes,
    detect_documentation_gaps,
    detect_orphan_questions,
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
                        "---\ntype: decision\ndate: 2026-01-01\n"
                        'superseded_by: "[[2026-05-01-cache-layer]]"\n---\n\nRedis cache layer.')
            _write_file(root / "decisions" / "2026-05-01-cache-layer.md",
                        "---\ntype: decision\ndate: 2026-05-01\n---\n\nIn-memory cache layer.")
            files = list(walk_project(root))
            out = detect_supersession_signals(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0]["older_has_superseded_marker"])

    def test_a_field_the_schema_does_not_define_is_not_a_marker(self):
        # The detector tested for a key named `superseded` for a year. No
        # template declares one, so the frontmatter half never fired.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-cache-layer.md",
                        "---\ntype: decision\ndate: 2026-01-01\nsuperseded: true\n---\n\nRedis cache layer.")
            _write_file(root / "decisions" / "2026-05-01-cache-layer.md",
                        "---\ntype: decision\ndate: 2026-05-01\n---\n\nIn-memory cache layer.")
            files = list(walk_project(root))
            out = detect_supersession_signals(files, TODAY)
            self.assertEqual(len(out), 1)
            self.assertFalse(out[0]["older_has_superseded_marker"])


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
            self.assertEqual(set(payload), {"scope", "cost"})  # scope null when unscoped
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


class TestFolderScope(unittest.TestCase):
    """--folder narrows the heavy walk to one subtree. The parked-work ruling
    blessed exactly this and nothing more: deliberate operator scoping, no
    inferred relevance. A scoped run must say so in the report, and its cost
    estimate must be the subtree's, or the flag would let a partial run
    masquerade as a full one."""

    def _project(self, root: Path) -> None:
        _write_file(root / "brief.md",
                    "---\ntype: project\nslug: t\nproject_type: coding\n"
                    "status: active\n---\n\n# T\n")
        # An aged open loop in notes/ and a big file in sessions/: scope to
        # notes/ must see the first and pay for neither of the second.
        _write_file(root / "notes" / "old.md",
                    "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
                    "tags:\n  - note\n---\n\nTODO: still open\n")
        _write_file(root / "sessions" / "2026-01-02.md",
                    "---\ntype: session\ndate: 2026-01-02\nstarted: 09:00\n"
                    "session_id: []\ntags:\n  - session\n---\n\n" + "x" * 9000)

    def _run(self, root: Path, *extra: str) -> tuple[int, dict]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dream_cli(["--project-dir", str(root),
                            "--today", "2026-06-01", *extra])
        out = buf.getvalue()
        return rc, (json.loads(out) if rc == 0 and out.strip() else {})

    def test_scoped_run_sees_only_the_subtree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            rc, report = self._run(root, "--folder", "notes")
            self.assertEqual(rc, 0)
            flagged = [c["file"] for c in report["orphan_questions"]]
            self.assertTrue(any("old.md" in f for f in flagged))
            everything = json.dumps(report["staleness_candidates"]
                                    + report["orphan_questions"])
            self.assertNotIn("sessions/", everything)

    def test_report_names_its_scope(self):
        # A scoped report that does not say so reads as a full one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            _, scoped = self._run(root, "--folder", "notes")
            _, full = self._run(root)
            self.assertEqual(scoped["scope"], "notes")
            self.assertIsNone(full["scope"])

    def test_cost_estimate_is_the_subtrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            _, scoped = self._run(root, "--folder", "notes")
            _, full = self._run(root)
            self.assertLess(scoped["cost"]["est_read_tokens"],
                            full["cost"]["est_read_tokens"])

    def test_estimate_only_respects_the_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            rc_s, scoped = self._run(root, "--folder", "notes", "--estimate-only")
            rc_f, full = self._run(root, "--estimate-only")
            self.assertEqual((rc_s, rc_f), (0, 0))
            self.assertLess(scoped["cost"]["est_read_tokens"],
                            full["cost"]["est_read_tokens"])

    def test_escape_is_contained(self):
        # The flag takes a path; a path can climb. Same containment bar as
        # every other operator-supplied path in the plugin.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            self._project(root)
            _write_file(Path(tmp) / "outside" / "leak.md", "# leak\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc, _ = self._run(root, "--folder", "../outside")
            self.assertEqual(rc, 1)
            self.assertIn("--folder", err.getvalue())

    def test_missing_folder_fails_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc, _ = self._run(root, "--folder", "nope")
            self.assertEqual(rc, 1)
            self.assertIn("nope", err.getvalue())


class TestPrecisionRebuild(unittest.TestCase):
    """The rebuild for precision.

    The 2026-08-13 run turned 602 files into 602 candidates, 463 of them
    contradictions, none real, at 918k read tokens. These tests pin the four
    causes shut: the detector that fired on shared vocabulary, the field name
    that never matched, the exclusion that ate 47 of 55 active decisions, and
    the census that nobody could read.
    """

    DAY = dt.date(2026, 9, 1)

    def _project(self, root: Path) -> Path:
        """A project with one aged, unlinked, single-line note at notes/a.md."""
        _make_minimal_project(root)
        _write_file(root / "notes" / "a.md",
                    "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
                    "An aged note nothing links to.\n")
        return root

    def _noisy_project(self, root: Path, notes: int = 200) -> Path:
        """The shape of the real run: hundreds of files, each mildly flaggable."""
        _make_minimal_project(root)
        for i in range(notes):
            _write_file(root / "notes" / f"n{i:03d}.md",
                        "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
                        f"Aged prose number {i}, linked from nowhere.\n")
        return root

    def _dismissal_report(self, project: Path, date: str, finding: str) -> None:
        dreams = project / "dreams"
        dreams.mkdir(parents=True, exist_ok=True)
        (dreams / f"{date}-dream.md").write_text(
            f"---\ntype: dream\ncreated: {date}\nupdated: {date}\n---\n\n"
            f"# Dream {date}\n\n1 findings, 0 acted on, 1 dismissed.\n\n"
            "## Dismissed\n\n| Finding | Why | Suppress until |\n|---|---|---|\n"
            f"| {finding} | intentional | the file changes |\n")

    def test_the_contradiction_detector_is_gone(self):
        # 463 candidates, 23 sampled, zero real. It fired on any two files
        # sharing vocabulary where one contained a negation cue, which in a
        # vault of decisions that say "we switched from X to Y" is every pair.
        self.assertFalse(hasattr(dream, "detect_contradiction_candidates"))
        self.assertNotIn("contradiction", dream.__doc__ or "")

    def test_supersession_reads_the_real_field_name(self):
        # The check tested for a key named `superseded`. The schema field is
        # `superseded_by`, so the frontmatter half could never pass and only
        # the prose regex ever fired.
        #
        # This asserted the SOURCE TEXT, which is the mistake this whole
        # programme keeps finding: a test that greps for a substring passes
        # when the behaviour breaks and fails when the code is merely
        # reworded. It exercises the behaviour now.
        def marker(field):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_file(root / "decisions" / "2026-01-01-cache-warm-order.md",
                            f"---\ntype: decision\nstatus: active\n{field}"
                            "---\n\n# cache warm order\n")
                _write_file(root / "decisions" / "2026-02-01-cache-warm-revision.md",
                            "---\ntype: decision\nstatus: active\n---\n\n"
                            "# cache warm revision\n")
                pairs = detect_supersession_signals(list(walk_project(root)), TODAY)
                self.assertEqual(len(pairs), 1, "the fixture pair must be found")
                return pairs[0]["older_has_superseded_marker"]

        self.assertTrue(marker('superseded_by: "[[2026-02-01-cache-warm-revision]]"\n'),
                        "the real schema field must be seen")
        self.assertFalse(marker('superseded: "[[2026-02-01-cache-warm-revision]]"\n'),
                         "a key no template declares is not the marker")

    def test_a_session_link_no_longer_proves_closure(self):
        # The check skipped any decision a session linked to. Adjudant tells
        # you to link decisions from sessions, so this excluded 47 of 55 active
        # decisions: the only verb auditing them, defeated by its own
        # convention. This too asserted source text, and worse, it asserted a
        # COMMENT, so deleting the comment passed it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-migrate.md",
                        "---\ntype: decision\nstatus: active\ndate: 2026-01-01\n---\n\n"
                        "## Decision\nMigrate.\n\n## Consequence\nRewrite config.\n")
            _write_file(root / "sessions" / "2026-02-01.md",
                        "---\ntype: session\n---\n\n"
                        "Did the [[2026-01-01-migrate]] rewrite today.")
            out = detect_unacted_decisions(list(walk_project(root)), TODAY)
            self.assertEqual([e["file"] for e in out],
                             ["decisions/2026-01-01-migrate.md"],
                             "a session link must not remove the decision")

    def test_a_session_link_lowers_the_score_instead(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "decisions" / "2026-01-01-migrate.md",
                        "---\ntype: decision\nstatus: active\ndate: 2026-01-01\n---\n\n"
                        "## Decision\nMigrate.\n\n## Consequence\nRewrite config.\n")
            _write_file(root / "sessions" / "2026-02-01.md",
                        "---\ntype: session\n---\n\nDid the [[2026-01-01-migrate]] rewrite today.")
            linked = detect_unacted_decisions(list(walk_project(root)), TODAY)
            self.assertEqual(len(linked), 1)
            self.assertGreater(linked[0]["inbound_session_refs"], 0)
            self.assertLess(dream._score("unacted_decisions", linked[0]),
                            dream._BASE_CONFIDENCE["unacted_decisions"])

    def test_every_candidate_carries_a_confidence(self):
        with tempfile.TemporaryDirectory() as t:
            project = self._project(Path(t))
            report = dream.run_dream(project, project, today=self.DAY)
            seen = 0
            for key, entries in report.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    seen += 1
                    self.assertIn("confidence", entry, f"{key} entry has no score")
                    self.assertGreaterEqual(entry["confidence"], 0.0)
                    self.assertLessEqual(entry["confidence"], 1.0)
            self.assertGreater(seen, 0, "fixture produced no candidates to score")

    def test_the_catalog_is_capped(self):
        with tempfile.TemporaryDirectory() as t:
            project = self._noisy_project(Path(t), notes=200)
            report = dream.run_dream(project, project, today=self.DAY)
            total = sum(len(v) for v in report.values() if isinstance(v, list))
            self.assertLessEqual(total, 20,
                                 "the catalog is a shortlist, not a census")
            self.assertGreater(report["summary"]["candidates_found"], 20,
                               "the fixture did not exceed the cap")

    def test_the_cap_keeps_the_report_readable_by_its_consumers(self):
        # cli_main prints meta and summary; a cap that dropped them would take
        # the CLI down with it.
        with tempfile.TemporaryDirectory() as t:
            project = self._noisy_project(Path(t), notes=40)
            report = dream.run_dream(project, project, today=self.DAY)
            for key in ("scope", "meta", "summary"):
                self.assertIn(key, report)
            self.assertEqual(report["meta"]["project_slug"], "test")

    def test_the_catalog_is_ordered_by_confidence(self):
        with tempfile.TemporaryDirectory() as t:
            project = self._noisy_project(Path(t), notes=60)
            report = dream.run_dream(project, project, today=self.DAY)
            kept = [e["confidence"] for v in report.values()
                    if isinstance(v, list) for e in v]
            self.assertEqual(kept, sorted(kept, reverse=True))

    def test_dismissals_suppress_a_repeat(self):
        # Two consecutive real reports dismissed the _archive/ naming finding
        # in identical words.
        with tempfile.TemporaryDirectory() as t:
            project = self._project(Path(t))
            self._dismissal_report(project, "2026-08-01", "notes/a.md orphaned")
            report = dream.run_dream(project, project, today=self.DAY)
            flagged = [e["file"] for v in report.values() if isinstance(v, list)
                       for e in v if "file" in e]
            self.assertNotIn("notes/a.md", flagged)

    def test_a_dismissal_expires_when_the_file_changes(self):
        # "Suppress until: the file changes" is the template's own wording. A
        # dismissal that outlived the file it judged would hide new drift.
        with tempfile.TemporaryDirectory() as t:
            project = self._project(Path(t))
            _write_file(project / "notes" / "a.md",
                        "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-08-20\n---\n\n"
                        "Rewritten after the dismissal.\n")
            self._dismissal_report(project, "2026-08-01", "notes/a.md orphaned")
            report = dream.run_dream(project, project, today=self.DAY)
            flagged = [e["file"] for v in report.values() if isinstance(v, list)
                       for e in v if "file" in e]
            self.assertIn("notes/a.md", flagged)

    def test_dismissals_are_read_from_the_filename_dream_actually_writes(self):
        # reference/dream.md mandates `{YYYY-MM-DD}-dream.md`; the state
        # contract records the statusline reading either spelling.
        with tempfile.TemporaryDirectory() as t:
            project = self._project(Path(t))
            self._dismissal_report(project, "2026-08-01", "notes/a.md orphaned")
            (project / "dreams" / "2026-07-01.md").write_text(
                "---\ntype: dream\ncreated: 2026-07-01\nupdated: 2026-07-01\n---\n\n"
                "# Dream 2026-07-01\n\n## Dismissed\n\n"
                "| Finding | Why | Suppress until |\n|---|---|---|\n"
                "| notes/b.md stale | intentional | the file changes |\n")
            found = dream.read_dismissals(project)
            self.assertEqual(sorted(found), ["notes/a.md", "notes/b.md"])

class TestSupersessionNeedsASharedSubject(unittest.TestCase):
    """Supersession means replacing a decision on the same SUBJECT.

    Measured on a copy of a real 93-decision project: 46 of 61 supersession
    pairs had ZERO shared title tokens, tied only by a link like `../brief`
    that most decisions carry. Twelve of the twenty capped slots were
    supersession, ten of those with no shared vocabulary at all, at confidence
    0.80, which is the top band and wins the cap.

    That is the same failure as the contradiction detector this redesign
    deleted, which "fired on any two files sharing vocabulary". This one fired
    on any two decisions sharing one link.
    """

    def _project(self, tmp, n_common=12, n_related=2):
        root = Path(tmp)
        d = root / "decisions"
        d.mkdir(parents=True)
        (root / "brief.md").write_text("---\ntype: project\n---\n# P\n")
        # Many unrelated decisions, every one linking the project brief.
        topics = ["form-informs-schema", "zoo-office-waitlist", "relative-gitfile",
                  "cache-warm-order", "token-budget-split", "vendor-pin-policy",
                  "queue-drain-rule", "colour-token-scale", "retry-backoff",
                  "index-shard-size", "log-rotation-window", "tls-cert-source"]
        for i in range(n_common):
            (d / f"2026-05-{i+1:02d}-{topics[i % len(topics)]}.md").write_text(
                "---\ntype: decision\nstatus: active\n---\n"
                f"# {topics[i % len(topics)]}\n\nSee [[../brief|brief]].\n")
        # A genuinely related pair: shared vocabulary AND a shared narrow link.
        for k, day in enumerate(("20", "21")):
            (d / f"2026-06-{day}-cache-warm-order-revision.md").write_text(
                "---\ntype: decision\nstatus: active\n---\n"
                "# cache warm order revision\n\n"
                "See [[../brief|brief]] and [[../notes/cache-warm|warm]].\n")
        (root / "notes").mkdir()
        (root / "notes" / "cache-warm.md").write_text(
            "---\ntype: note\n---\n# W\n")
        return root

    def _pairs(self, root):
        files = list(walk_project(root))
        return detect_supersession_signals(files, dt.date(2026, 9, 1))

    def test_a_link_most_decisions_carry_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            pairs = self._pairs(self._project(tmp))
            tied_only_by_brief = [
                x for x in pairs
                if not x["shared_terms"] and x["shared_links"] == ["../brief"]]
            self.assertEqual(
                tied_only_by_brief, [],
                "a link carried by most decisions ties nothing")

    def test_every_reported_pair_carries_real_evidence(self):
        # The outcome: no pair survives on nothing.
        with tempfile.TemporaryDirectory() as tmp:
            for x in self._pairs(self._project(tmp)):
                self.assertTrue(
                    len(x["shared_terms"]) >= 2 or x["shared_links"],
                    f"pair with no evidence: {x}")

    def test_a_genuinely_related_pair_still_fires(self):
        # Precision must not be bought by going silent.
        with tempfile.TemporaryDirectory() as tmp:
            pairs = self._pairs(self._project(tmp))
            hits = [x for x in pairs
                    if "cache" in x["older"]["file"] and "cache" in x["newer"]["file"]]
            self.assertTrue(hits, "the real supersession pair vanished")

    def test_a_shared_link_alone_never_creates_a_pair(self):
        # SUPERSEDED test_a_narrow_shared_link_alone_is_still_evidence, which
        # I wrote before measuring. On the real 92-decision project five
        # decisions cited one hub, and that alone produced ten pairs with no
        # shared vocabulary. Citing the same document is not sharing a subject.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "decisions"
            d.mkdir(parents=True)
            (root / "notes").mkdir()
            (root / "notes" / "narrow.md").write_text("---\ntype: note\n---\n# N\n")
            for i, name in enumerate(("alpha-thing", "beta-other")):
                (d / f"2026-05-0{i+1}-{name}.md").write_text(
                    "---\ntype: decision\nstatus: active\n---\n"
                    f"# {name}\n\nSee [[../notes/narrow|n]].\n")
            self.assertEqual(self._pairs(root), [],
                             "a shared citation is not an overlapping subject")

    def test_a_hub_document_does_not_make_a_clique(self):
        # The measured shape. Five decisions cited one hub decision, and that
        # alone produced ten pairs (5 choose 2), every one with no shared
        # vocabulary. On the real project this was 46 of 61 pairs.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "decisions"
            d.mkdir(parents=True)
            (d / "2026-05-11-the-hub.md").write_text(
                "---\ntype: decision\nstatus: active\n---\n# hub\n")
            names = ["auth-cascade", "consolidation-design", "canonical-store",
                     "review-remediation", "hardening-sweep"]
            for k, name in enumerate(names):
                (d / f"2026-06-{k+1:02d}-{name}.md").write_text(
                    "---\ntype: decision\nstatus: active\n---\n"
                    f"# {name}\n\nPer [[2026-05-11-the-hub|hub]].\n")
            pairs = self._pairs(root)
            self.assertEqual(
                pairs, [],
                "five decisions citing one hub must not yield ten pairs")


class TestStubsCoverTheDevKinds(unittest.TestCase):
    """The stub detector covered note, doc and decision, so a three-line api
    page was invisible to every tier. The dev kinds are exactly the pages
    someone starts and does not finish.
    """

    def test_a_three_line_api_page_is_a_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "api" / "contacts.md",
                        "---\ntype: api\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
                        "verified: 2026-09-01\nverified_by: read\n---\n\n# Contacts\n")
            out = detect_documentation_gaps(list(walk_project(root)), TODAY)
            self.assertTrue(any(g["kind"] == "stub" and "contacts" in g["file"]
                                for g in out), out)

    def test_a_generated_page_is_not_a_stub(self):
        # Its script rewrites it every run; a short one is the script's choice.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_file(root / "components" / "modules" / "button-generated.md",
                        "---\ntype: component\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
                        "verified: 2026-09-01\nverified_by: read\nsource: build.py\n"
                        "---\n\n# button\n\n![[button]]\n")
            out = detect_documentation_gaps(list(walk_project(root)), TODAY)
            self.assertFalse(any(g["kind"] == "stub" for g in out), out)


if __name__ == "__main__":
    unittest.main()


class TestTheCapDoesNotUndoTheFix(unittest.TestCase):
    """An adversarial prover refuted plan 3's dream claim.

    The detector fix was real: `detect_unacted_decisions` stopped excluding a
    decision just because a session linked to it. But `_score` damped a linked
    entry from 0.5 to 0.35, which ranks it BELOW staleness_candidates at 0.4,
    and `_cap` then cut at twenty before reaching any of them. On a fixture of
    55 aged decisions with 47 linked, the delivered report contained 0 of the
    47. The user-visible outcome was identical to the bug the fix removed.

    The repo's own test missed it by asserting the MECHANISM (that the score
    drops) rather than the OUTCOME (that the entry still reaches the report).
    These assert the outcome.
    """

    DAY = dt.date(2026, 9, 1)

    def _fixture(self, root: Path, *, decisions: int, linked: int,
                 stale_notes: int) -> Path:
        proj = root / "vault" / "projects" / "demo"
        for sub in ("decisions", "sessions", "notes"):
            (proj / sub).mkdir(parents=True, exist_ok=True)
        names = []
        for i in range(decisions):
            n = f"2026-01-{i % 28 + 1:02d}-policy-{i}"
            names.append(n)
            _write_file(proj / "decisions" / f"{n}.md",
                        "---\ntype: decision\nstatus: active\n"
                        "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
                        f"# Policy {i}\n\n## Why\nReason.\n\n## Consequence\nDo work {i}.\n")
        # One session linking the first `linked` decisions.
        body = "\n".join(f"- did [[{n}]]" for n in names[:linked])
        _write_file(proj / "sessions" / "2026-02-01.md",
                    "---\ntype: session\ncreated: 2026-02-01\nupdated: 2026-02-01\n---\n\n"
                    f"Session.\n\n## Log\n{body}\n")
        # Enough staleness candidates to swamp the cap on score alone.
        for i in range(stale_notes):
            _write_file(proj / "notes" / f"old-{i}.md",
                        "---\ntype: note\ncreated: 2024-01-01\nupdated: 2024-01-01\n---\n\n"
                        f"# Old {i}\n\nAging prose.\n")
        return proj

    def _unacted(self, report: dict) -> list:
        return report.get("unacted_decisions") or []

    def test_a_link_no_longer_excludes_unconditionally(self):
        # The real regression. The old bug dropped a decision the moment any
        # session linked it, whatever else was in the vault. With EVERY
        # decision linked there is nothing else competing for the slots, so if
        # the report is still empty the link is still an exclusion rather than
        # a ranking signal.
        with tempfile.TemporaryDirectory() as t:
            proj = self._fixture(Path(t), decisions=12, linked=12, stale_notes=0)
            report = dream.run_dream(proj, proj.parent.parent, today=self.DAY)
            linked = [e for e in self._unacted(report) if e.get("inbound_session_refs")]
            self.assertTrue(
                linked,
                "every session-linked decision is still absent from the report, "
                "which is the same outcome as the bug the detector fix removed")

    def test_a_link_ranks_a_decision_lower_without_removing_it(self):
        # Damping ORDERS within a category; it must not remove. Asserted on the
        # detector plus the score, because a BINDING cap legitimately cuts the
        # lowest-scoring entries and that is the ranking doing its job, not the
        # old bug. The bug was unconditional exclusion, covered by the test
        # above.
        with tempfile.TemporaryDirectory() as t:
            proj = self._fixture(Path(t), decisions=10, linked=5, stale_notes=0)
            entries = dream.detect_unacted_decisions(
                list(walk_project(proj)), self.DAY)
            linked = [e for e in entries if e.get("inbound_session_refs")]
            plain = [e for e in entries if not e.get("inbound_session_refs")]
            self.assertEqual(len(linked), 5, "the detector dropped linked ones")
            self.assertEqual(len(plain), 5)
            self.assertLess(dream._score("unacted_decisions", linked[0]),
                            dream._score("unacted_decisions", plain[0]))

    def test_the_cap_says_what_it_dropped(self):
        # A silent cap reads as "nothing more was found". This one is not
        # silent, and that must stay true: candidates_found is the pre-cap
        # total and the CLI prints "N of M candidates" from it.
        with tempfile.TemporaryDirectory() as t:
            proj = self._fixture(Path(t), decisions=55, linked=47, stale_notes=40)
            report = dream.run_dream(proj, proj.parent.parent, today=self.DAY)
            summary = report.get("summary") or {}
            shown = sum(len(v) for v in report.values() if isinstance(v, list))
            self.assertLessEqual(shown, dream.CATALOG_CAP)
            self.assertEqual(summary.get("candidates"), shown)
            self.assertGreater(summary.get("candidates_found", 0), shown,
                               "the pre-cap total is not recorded, so the cap "
                               "would read as a census")

    def test_no_non_empty_category_is_starved_by_the_cap(self):
        with tempfile.TemporaryDirectory() as t:
            proj = self._fixture(Path(t), decisions=55, linked=47, stale_notes=40)
            report = dream.run_dream(proj, proj.parent.parent, today=self.DAY)
            # unacted_decisions has 55 candidates before the cap; it must not
            # come back empty just because another category scores higher.
            self.assertTrue(self._unacted(report),
                            "unacted_decisions was starved to zero by the cap")
