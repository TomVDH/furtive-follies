"""Tests for adjudant/scripts/truth.py.

Check used to grade shape: 110 frontmatter keys against a schema, 99 failures,
69 of them in a folder adjudant does not own, and nobody acted on any of it.
Every finding here traces to a real failure in the audited vault, and every
one is settled by a file's existence or a date comparison.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from truth import BANDS, Finding, truth_report


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _project(tmp: Path, slug: str = "demo") -> Path:
    pdir = tmp / "vault" / "projects" / "active" / slug
    _w(pdir / "brief.md",
       "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n"
       "# Demo\n\nWhat this project is.\n\n"
       "## Where things are\n| | |\n|---|---|\n")
    _w(pdir / "sessions" / "2026-09-01.md", "---\ntype: session\n---\n\n## Log\n")
    return pdir


def _kinds(report) -> list:
    return [f["kind"] for f in report["findings"]]


class TestReportShape(unittest.TestCase):

    def test_bands_are_ordered_by_cost_of_being_wrong(self):
        self.assertEqual(BANDS, ("wrong-now", "going-stale", "worth-a-look"))

    def test_a_clean_project_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertEqual(report["findings"], [])
            self.assertEqual(report["counts"],
                             {"wrong-now": 0, "going-stale": 0, "worth-a-look": 0})
            self.assertGreater(report["checked"], 0)

    def test_findings_are_json_shaped_and_sorted_by_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "notes" / "a.md",
               "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
               "See [[demo/notes/ghost]].\n")
            _w(pdir / "docs" / "old.md",
               "---\ntype: doc\nupdated: 2026-01-01\nverified: 2026-01-01\n---\n\n"
               "# Old\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertTrue(report["findings"])
            for f in report["findings"]:
                self.assertEqual(set(f), {"band", "kind", "file", "detail"})
                self.assertIn(f["band"], BANDS)
            order = [BANDS.index(f["band"]) for f in report["findings"]]
            self.assertEqual(order, sorted(order))

    def test_the_memory_folder_is_never_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "memory" / "flat.md",
               "---\nname: x\ndescription: y\ntype: project\n---\n\n"
               "See [[nowhere-at-all]].\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertEqual(report["findings"], [])

    def test_a_generated_page_is_never_nagged_about(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "components" / "gen.md",
               "---\ntype: component\nupdated: 2026-01-01\n"
               "source: build-module-inventory.py\n---\n\n"
               "See [[demo/notes/ghost]].\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("broken-wikilink", _kinds(report))


class TestNamesSomethingThatIsNotThere(unittest.TestCase):

    def test_broken_wikilink(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "notes" / "a.md",
               "---\ntype: note\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n\n"
               "Real: [[demo/brief]]. Ghost: [[demo/notes/ghost]].\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            broken = [f for f in report["findings"] if f["kind"] == "broken-wikilink"]
            self.assertEqual(len(broken), 1)
            self.assertEqual(broken[0]["file"], "notes/a.md")
            self.assertEqual(broken[0]["band"], "wrong-now")
            self.assertIn("demo/notes/ghost", broken[0]["detail"])

    def test_an_embed_is_not_a_broken_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "notes" / "a.md",
               "---\ntype: note\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n\n"
               "![[diagram.png]]\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("broken-wikilink", _kinds(report))

    def test_superseded_by_pointing_at_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "decisions" / "2026-08-01-a.md",
               "---\ntype: decision\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: superseded\nsuperseded_by: \"[[demo/decisions/nope]]\"\n---\n\n"
               "# A\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "superseded-target-missing"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["file"], "decisions/2026-08-01-a.md")

    def test_a_card_citing_a_spec_that_was_never_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "specs" / "spec-018-page-spinup.md",
               "---\ntype: spec\nstatus: agreed\ncreated: 2026-08-01\n"
               "updated: 2026-08-30\nverified: 2026-08-30\n---\n\n# SPEC-018\n")
            _w(pdir / "tasks" / "real.md",
               "---\ntype: task\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: doing\nspec: \"[[demo/specs/spec-018-page-spinup|SPEC-018]]\"\n"
               "---\n\n# Real\n")
            _w(pdir / "tasks" / "phantom.md",
               "---\ntype: task\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: doing\nspec: \"[[demo/specs/spec-999-nope|SPEC-999]]\"\n"
               "---\n\n# Phantom\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"] if f["kind"] == "task-spec-missing"]
            self.assertEqual([h["file"] for h in hits], ["tasks/phantom.md"])

    def test_a_frontmatter_link_written_without_quotes_still_resolves(self):
        # `superseded_by: [[demo/decisions/a]]` is what Obsidian's Properties
        # editor and a hand-written YAML line both produce, and the
        # frontmatter parser reads the brackets as a list: ['[demo/…/a]'].
        # Reported as broken, that is a wrong-now finding on a working link,
        # in the band that costs the most to get wrong.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            _w(pdir / "decisions" / "2026-08-01-a.md",
               "---\ntype: decision\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: active\n---\n\n# A\n")
            _w(pdir / "decisions" / "2026-08-02-b.md",
               "---\ntype: decision\ncreated: 2026-08-02\nupdated: 2026-08-02\n"
               "status: superseded\n"
               "superseded_by: [[demo/decisions/2026-08-01-a]]\n---\n\n# B\n")
            _w(pdir / "specs" / "spec-018.md",
               "---\ntype: spec\nstatus: agreed\ncreated: 2026-08-01\n"
               "updated: 2026-08-01\nverified: 2026-08-01\n---\n\n# S\n")
            _w(pdir / "tasks" / "t.md",
               "---\ntype: task\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: doing\nspec: [[demo/specs/spec-018|SPEC-018]]\n---\n\n# T\n")
            report = truth_report(pdir, vault=root / "vault", today=date(2026, 9, 1))
            self.assertEqual(report["findings"], [])

    def test_an_unquoted_frontmatter_link_that_is_broken_is_still_reported(self):
        # The other direction, and the one that matters: reading the unquoted
        # form must not be a way to stop checking it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            _w(pdir / "decisions" / "2026-08-02-b.md",
               "---\ntype: decision\ncreated: 2026-08-02\nupdated: 2026-08-02\n"
               "status: superseded\nsuperseded_by: [[demo/decisions/nope]]\n---\n\n# B\n")
            _w(pdir / "tasks" / "t.md",
               "---\ntype: task\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: doing\nspec: [[demo/specs/spec-999-nope|SPEC-999]]\n---\n\n# T\n")
            report = truth_report(pdir, vault=root / "vault", today=date(2026, 9, 1))
            self.assertEqual(sorted(_kinds(report)),
                             ["superseded-target-missing", "task-spec-missing"])
            detail = [f["detail"] for f in report["findings"]
                      if f["kind"] == "superseded-target-missing"][0]
            self.assertIn("demo/decisions/nope", detail)

    def test_an_unfilled_optional_link_is_not_a_finding(self):
        # The decision, spec and task templates all ship the field present and
        # empty, with a `# optional` comment. Every shape that reaches the
        # parser from one of those has to read as "nothing to check".
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            for i, value in enumerate(("", " []", ' ""', " '  '")):
                # `created:` tracks each filename: a dated stem and a created
                # date that disagree are their own finding, and this fixture
                # is about the link field alone.
                day = f"2026-08-0{i + 1}"
                _w(pdir / "decisions" / f"{day}-d.md",
                   f"---\ntype: decision\ncreated: {day}\n"
                   f"updated: {day}\nstatus: active\n"
                   f"superseded_by:{value}\n---\n\n# D\n")
            report = truth_report(pdir, vault=root / "vault", today=date(2026, 9, 1))
            self.assertEqual(report["findings"], [])


    def test_a_brief_repo_path_that_no_longer_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            real = root / "code"
            real.mkdir()
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n"
               "# Demo\n\nWhat this project is.\n\n"
               "## Where things are\n| | |\n|---|---|\n"
               f"| Repo | {real} |\n| Deploy | https://example.test |\n")
            self.assertEqual(
                [f["kind"] for f in truth_report(
                    pdir, vault=root / "vault", today=date(2026, 9, 1))["findings"]],
                [])
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n"
               "# Demo\n\nWhat this project is.\n\n"
               "## Where things are\n| | |\n|---|---|\n"
               f"| Repo | {root / 'moved-away'} |\n")
            report = truth_report(pdir, vault=root / "vault", today=date(2026, 9, 1))
            hits = [f for f in report["findings"] if f["kind"] == "brief-repo-missing"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["band"], "wrong-now")

    def test_a_freshly_rendered_brief_is_not_a_finding(self):
        # `_render.render` leaves an unfilled placeholder as `{Its Name}` on
        # purpose, so a human can see what belongs there. The brief template
        # ships `| Repo | {path or url} |`, so before this guard every project
        # opened its first status run with a wrong-now finding saying the repo
        # had moved. Rendered from the shipped template, not a copy of it, so
        # this keeps holding when the template changes.
        from _render import render
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            _w(pdir / "brief.md",
               render("project",
                      {"created": "2026-09-01", "updated": "2026-09-01",
                       "verified": "2026-09-01"},
                      {"Project Name": "Demo"}))
            report = truth_report(pdir, vault=root / "vault", today=date(2026, 9, 1))
            self.assertEqual(report["findings"], [],
                             "a project reported a lie on the day it was created")

    def test_the_repo_verdict_does_not_depend_on_where_you_ran_it(self):
        # `acme/toolkit` is a plausible answer to "path or url" and is not
        # a claim about this disk. Statting it resolves against the shell's
        # cwd, so the same brief was clean from one directory and wrong-now
        # from another. Only an absolute path is settleable.
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            (root / "sibling").mkdir()
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n"
               "# Demo\n\nWhat this project is.\n\n"
               "## Where things are\n| | |\n|---|---|\n"
               "| Repo | sibling |\n")
            before = Path.cwd()
            try:
                for cwd in (before, root):
                    os.chdir(cwd)
                    report = truth_report(pdir, vault=root / "vault",
                                          today=date(2026, 9, 1))
                    self.assertNotIn("brief-repo-missing", _kinds(report),
                                     f"verdict changed with the cwd ({cwd})")
            finally:
                os.chdir(before)


    def test_an_elided_repo_path_is_not_a_finding(self):
        # The brief template's own example writes `~/…/Acme - Web`.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n"
               "# Demo\n\nWhat this project is.\n\n"
               "## Where things are\n| | |\n|---|---|\n"
               "| Repo | ~/…/Acme - Web |\n")
            report = truth_report(pdir, vault=root / "vault", today=date(2026, 9, 1))
            self.assertNotIn("brief-repo-missing", _kinds(report))


class TestNobodyHasCheckedItLately(unittest.TestCase):

    def test_the_verified_kinds_come_from_the_templates(self):
        from truth import verified_kinds
        kinds = verified_kinds()
        self.assertIn("doc", kinds)
        self.assertIn("spec", kinds)
        # verified: is the only thing dividing a doc from a note. A note is a
        # thought and cannot be wrong in that way.
        self.assertNotIn("note", kinds)
        self.assertNotIn("session", kinds)

    def test_verified_over_ninety_days_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "docs" / "fresh.md",
               "---\ntype: doc\nupdated: 2026-09-01\nverified: 2026-08-01\n---\n\n# F\n")
            _w(pdir / "docs" / "stale.md",
               "---\ntype: doc\nupdated: 2026-09-01\nverified: 2026-05-01\n---\n\n# S\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"] if f["kind"] == "verified-stale"]
            self.assertEqual([h["file"] for h in hits], ["docs/stale.md"])
            self.assertEqual(hits[0]["band"], "going-stale")
            self.assertIn("123 days", hits[0]["detail"])

    def test_exactly_ninety_days_is_the_edge_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "docs" / "edge.md",
               "---\ntype: doc\nupdated: 2026-09-01\nverified: 2026-06-03\n---\n\n# E\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertIn("verified-stale", _kinds(report))

    def test_a_page_with_no_verified_at_all(self):
        # 71 component sidecars carry none. The generated half of the pair
        # carries source: and is exempt; the hand-written half is not.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "docs" / "unchecked.md",
               "---\ntype: doc\nupdated: 2026-09-01\n---\n\n# U\n")
            _w(pdir / "notes" / "thought.md",
               "---\ntype: note\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n\n# T\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"] if f["kind"] == "verified-missing"]
            self.assertEqual([h["file"] for h in hits], ["docs/unchecked.md"])
            self.assertEqual(hits[0]["band"], "going-stale")

    def test_a_malformed_verified_date_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "docs" / "bad.md",
               "---\ntype: doc\nupdated: 2026-09-01\nverified: last tuesday\n---\n\n# B\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"] if f["kind"] == "verified-missing"]
            self.assertEqual(len(hits), 1)
            self.assertIn("last tuesday", hits[0]["detail"])

    def test_verified_by_docs_only(self):
        # A bare date throws away the difference between a live probe and a
        # skim of vendor documentation.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "api" / "contacts.md",
               "---\ntype: api\nupdated: 2026-09-01\nverified: 2026-08-30\n"
               "verified_by: docs\n---\n\n# Contacts\n")
            _w(pdir / "api" / "objects.md",
               "---\ntype: api\nupdated: 2026-09-01\nverified: 2026-08-30\n"
               "verified_by: tested\n---\n\n# Objects\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "verified-docs-only"]
            self.assertEqual([h["file"] for h in hits], ["api/contacts.md"])
            self.assertEqual(hits[0]["band"], "worth-a-look")


class TestWorkNobodyCanSee(unittest.TestCase):

    def test_an_open_card_in_the_archive(self):
        # The 17 August sweep moved 97 cards and closed zero. 44 of them still
        # read open from inside tasks/_archive/.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "tasks" / "_archive" / "closed.md",
               "---\ntype: task\ncreated: 2026-01-01\nupdated: 2026-02-01\n"
               "status: done\n---\n\n# Closed\n")
            _w(pdir / "tasks" / "_archive" / "dropped.md",
               "---\ntype: task\ncreated: 2026-01-01\nupdated: 2026-02-01\n"
               "status: dropped\n---\n\n# Dropped\n")
            _w(pdir / "tasks" / "_archive" / "alive.md",
               "---\ntype: task\ncreated: 2026-01-01\nupdated: 2026-02-01\n"
               "status: doing\n---\n\n# Alive\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "open-card-in-archive"]
            self.assertEqual([h["file"] for h in hits],
                             ["tasks/_archive/alive.md"])
            self.assertEqual(hits[0]["band"], "wrong-now")
            self.assertIn("doing", hits[0]["detail"])

    def test_a_bug_entry_with_no_card_citing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "docs" / "bug-log.md",
               "---\ntype: doc\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n"
               "# Bug log\n\n"
               "## BUG-001 cold cache\nstatus: closed\nfixed on 2026-08-01.\n\n"
               "## BUG-002 warm cache\nSomething is wrong.\n\n"
               "## BUG-003 hot cache\nSomething else is wrong.\n")
            _w(pdir / "tasks" / "fix-warm.md",
               "---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
               "status: doing\n---\n\n# Fix warm\n\nCloses BUG-002.\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "bug-entry-uncited"]
            self.assertEqual(len(hits), 1)
            self.assertIn("BUG-003", hits[0]["detail"])
            self.assertEqual(hits[0]["file"], "docs/bug-log.md")

    def test_a_spec_agreed_with_no_cards_and_no_verification(self):
        # SPEC-012 exactly.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "specs" / "spec-012-campaign-factory.md",
               "---\ntype: spec\nstatus: agreed\ncreated: 2026-06-01\n"
               "updated: 2026-06-01\n---\n\n# SPEC-012\n\n## Goal\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "spec-agreed-unbuilt"]
            self.assertEqual(len(hits), 1)
            self.assertIn("92 days", hits[0]["detail"])
            self.assertEqual(hits[0]["band"], "wrong-now")

    def test_a_cited_spec_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "specs" / "spec-012-campaign-factory.md",
               "---\ntype: spec\nstatus: agreed\ncreated: 2026-06-01\n"
               "updated: 2026-06-01\n---\n\n# SPEC-012\n\n## Goal\n")
            _w(pdir / "tasks" / "build-it.md",
               "---\ntype: task\ncreated: 2026-06-02\nupdated: 2026-06-02\n"
               "status: doing\n"
               "spec: \"[[demo/specs/spec-012-campaign-factory|SPEC-012]]\"\n"
               "---\n\n# Build it\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("spec-agreed-unbuilt", _kinds(report))

    def test_a_spec_cited_by_an_unquoted_card_is_not_flagged(self):
        # `spec: [[demo/specs/…|SPEC-012]]` is what Obsidian's Properties
        # editor writes, and the frontmatter parser reads the brackets as a
        # one-item list. Read literally that is not a string, the citation
        # goes unseen, and an actively worked spec is reported as intent that
        # never became work — a wrong-now finding on the good case.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "specs" / "spec-012-campaign-factory.md",
               "---\ntype: spec\nstatus: agreed\ncreated: 2026-06-01\n"
               "updated: 2026-06-01\n---\n\n# SPEC-012\n\n## Goal\n")
            _w(pdir / "tasks" / "build-it.md",
               "---\ntype: task\ncreated: 2026-06-02\nupdated: 2026-06-02\n"
               "status: doing\n"
               "spec: [[demo/specs/spec-012-campaign-factory|SPEC-012]]\n"
               "---\n\n# Build it\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("spec-agreed-unbuilt", _kinds(report))

    def test_a_verified_spec_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "specs" / "spec-012-campaign-factory.md",
               "---\ntype: spec\nstatus: agreed\ncreated: 2026-06-01\n"
               "updated: 2026-06-01\nverified: 2026-08-30\n---\n\n# SPEC-012\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("spec-agreed-unbuilt", _kinds(report))

    def test_a_draft_spec_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "specs" / "spec-013-idea.md",
               "---\ntype: spec\nstatus: draft\ncreated: 2026-01-01\n"
               "updated: 2026-01-01\n---\n\n# SPEC-013\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("spec-agreed-unbuilt", _kinds(report))

    def test_a_decision_whose_consequence_names_work_with_no_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "tasks" / "strip-bucket-a-tags.md",
               "---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
               "status: doing\n---\n\n# Strip\n")
            _w(pdir / "decisions" / "2026-09-01-carded.md",
               "---\ntype: decision\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
               "status: active\n---\n\n# Carded\n\n## Consequence\n"
               "Work: [[demo/tasks/strip-bucket-a-tags]]\n")
            _w(pdir / "decisions" / "2026-09-01-uncarded.md",
               "---\ntype: decision\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
               "status: active\n---\n\n# Uncarded\n\n## Consequence\n"
               "Work: someone has to rewrite the branch tracker.\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "decision-consequence-uncarded"]
            self.assertEqual([h["file"] for h in hits],
                             ["decisions/2026-09-01-uncarded.md"])


class TestRecordsThatDisagree(unittest.TestCase):

    def test_superseded_with_no_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "decisions" / "2026-08-01-a.md",
               "---\ntype: decision\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: superseded\n---\n\n# A\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "superseded-without-target"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["band"], "wrong-now")

    def test_an_empty_superseded_by_is_the_same_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "decisions" / "2026-08-01-a.md",
               "---\ntype: decision\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: superseded\nsuperseded_by: \"\"\n---\n\n# A\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertIn("superseded-without-target", _kinds(report))

    def test_an_empty_list_superseded_by_is_the_same_finding(self):
        # The template ships the field present and empty; an editor
        # round-trips that as `superseded_by: []`, which is still nothing
        # saying what replaced it.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "decisions" / "2026-08-01-a.md",
               "---\ntype: decision\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: superseded\nsuperseded_by: []\n---\n\n# A\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertIn("superseded-without-target", _kinds(report))

    def test_an_unquoted_superseded_by_is_a_target(self):
        # `superseded_by: [[demo/decisions/x]]` reaches the parser as a
        # one-item list. Read as "no target", a decision that says exactly
        # what replaced it gets a wrong-now finding for saying nothing.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "decisions" / "2026-08-01-a.md",
               "---\ntype: decision\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
               "status: active\n---\n\n# A\n")
            _w(pdir / "decisions" / "2026-08-02-b.md",
               "---\ntype: decision\ncreated: 2026-08-02\nupdated: 2026-08-02\n"
               "status: superseded\n"
               "superseded_by: [[demo/decisions/2026-08-01-a]]\n---\n\n# B\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("superseded-without-target", _kinds(report))

    def test_an_off_vocabulary_status_is_reported_never_coerced(self):
        # board.py silently refiled anything unrecognised as backlog, which is
        # how `obsolete` became invisible work.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "tasks" / "odd.md",
               "---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
               "status: obsolete\n---\n\n# Odd\n")
            _w(pdir / "tasks" / "blocked.md",
               "---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
               "status: blocked\n---\n\n# Blocked\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "status-off-vocabulary"]
            self.assertEqual([h["file"] for h in hits], ["tasks/odd.md"])
            self.assertIn("obsolete", hits[0]["detail"])

    def test_a_created_date_disagreeing_with_its_own_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "decisions" / "2026-08-01-a.md",
               "---\ntype: decision\ncreated: 2026-07-14\nupdated: 2026-08-01\n"
               "status: active\n---\n\n# A\n")
            _w(pdir / "decisions" / "2026-08-02-b.md",
               "---\ntype: decision\ncreated: 2026-08-02\nupdated: 2026-08-02\n"
               "status: active\n---\n\n# B\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "created-filename-mismatch"]
            self.assertEqual([h["file"] for h in hits], ["decisions/2026-08-01-a.md"])
            self.assertIn("2026-07-14", hits[0]["detail"])

    def test_a_release_version_disagreeing_with_its_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "releases" / "v2.1.0.md",
               "---\ntype: release\nversion: 2.0.9\ncreated: 2026-09-01\n"
               "updated: 2026-09-01\n---\n\n# v2.1.0\n")
            _w(pdir / "releases" / "v2.2.0.md",
               "---\ntype: release\nversion: v2.2.0\ncreated: 2026-09-01\n"
               "updated: 2026-09-01\n---\n\n# v2.2.0\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "version-filename-mismatch"]
            self.assertEqual([h["file"] for h in hits], ["releases/v2.1.0.md"])


class TestWentStaleQuietly(unittest.TestCase):

    def test_a_brief_untouched_while_sessions_kept_landing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-01-01\nverified: 2026-09-01\n"
               "---\n\n# Demo\n\nWhat this project is.\n\n"
               "## Where things are\n| | |\n|---|---|\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"] if f["kind"] == "brief-stale"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["band"], "going-stale")
            self.assertIn("2026-09-01", hits[0]["detail"])

    def test_a_quiet_project_with_an_old_brief_is_not_a_brief_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            (pdir / "sessions" / "2026-09-01.md").unlink()
            _w(pdir / "sessions" / "2025-01-01.md", "---\ntype: session\n---\n")
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-01-01\nverified: 2026-09-01\n"
               "---\n\n# Demo\n\nWhat this project is.\n\n"
               "## Where things are\n| | |\n|---|---|\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("brief-stale", _kinds(report))

    def test_a_handoff_older_than_the_newest_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "_handoff.md",
               "---\ntype: handoff\nupdated: 2026-08-01\n---\n\n# Handoff\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "handoff-behind-session"]
            self.assertEqual(len(hits), 1)
            self.assertIn("2026-09-01", hits[0]["detail"])

    def test_a_current_handoff_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            _w(pdir / "_handoff.md",
               "---\ntype: handoff\nupdated: 2026-09-01\n---\n\n# Handoff\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("handoff-behind-session", _kinds(report))

    def test_a_generated_page_older_than_its_own_script(self):
        import os
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            code = root / "code"
            script = _w(code / "build-module-inventory.py", "print('x')\n")
            page = _w(pdir / "components" / "gen.md",
                      "---\ntype: component\nupdated: 2026-09-01\n"
                      "source: build-module-inventory.py\n---\n\n# gen\n")
            old = time.time() - 86400
            os.utime(page, (old, old))
            report = truth_report(pdir, vault=root / "vault", code_root=code,
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "generated-page-stale"]
            self.assertEqual([h["file"] for h in hits], ["components/gen.md"])
            self.assertIn("build-module-inventory.py", hits[0]["detail"])
            self.assertEqual(script.name, "build-module-inventory.py")

    def test_a_regenerated_page_is_not_a_finding(self):
        # The other direction: once the script has been rerun the page is
        # newer than it, and saying otherwise would nag about the good case.
        import os
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            code = root / "code"
            script = _w(code / "build-module-inventory.py", "print('x')\n")
            old = time.time() - 86400
            os.utime(script, (old, old))
            _w(pdir / "components" / "gen.md",
               "---\ntype: component\nupdated: 2026-09-01\n"
               "source: build-module-inventory.py\n---\n\n# gen\n")
            report = truth_report(pdir, vault=root / "vault", code_root=code,
                                  today=date(2026, 9, 1))
            self.assertNotIn("generated-page-stale", _kinds(report))

    def test_a_source_naming_a_system_is_not_a_stale_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            _w(pdir / "sources" / "wiki.md",
               "---\ntype: source\nupdated: 2026-09-01\nverified: 2026-08-30\n"
               "source: confluence\n---\n\n# Wiki\n")
            report = truth_report(pdir, vault=root / "vault",
                                  code_root=root / "code",
                                  today=date(2026, 9, 1))
            self.assertNotIn("generated-page-stale", _kinds(report))


class TestWrongFolder(unittest.TestCase):

    def test_active_with_no_session_for_thirty_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            (pdir / "sessions" / "2026-09-01.md").unlink()
            _w(pdir / "sessions" / "2026-07-01.md", "---\ntype: session\n---\n")
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "project-zone-drift"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["band"], "worth-a-look")
            self.assertEqual(hits[0]["file"], "")
            self.assertIn("62 days", hits[0]["detail"])

    def test_a_paused_project_is_quiet_on_purpose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "vault" / "projects" / "paused" / "demo"
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n"
               "---\n\n# Demo\n\nWhat this project is.\n\n"
               "## Where things are\n| | |\n|---|---|\n")
            _w(pdir / "sessions" / "2025-01-01.md", "---\ntype: session\n---\n")
            report = truth_report(pdir, vault=root / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("project-zone-drift", _kinds(report))


class TestAgentsReachDetector(unittest.TestCase):

    def test_a_named_script_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            code = root / "code"
            code.mkdir()
            # scripts/ exists and the named file does not. That is what makes
            # the claim checkable: a token whose whole parent tree is gone is
            # indistinguishable from a hypothetical path in prose.
            (code / "scripts").mkdir()
            _w(code / "scripts" / "other.sh", "#\n")
            _w(code / "AGENTS.md",
               "Enforced mechanically by `scripts/enforce-branch-rule.sh`.\n")
            report = truth_report(pdir, vault=root / "vault", code_root=code,
                                  today=date(2026, 9, 1))
            hits = [f for f in report["findings"]
                    if f["kind"] == "agents-missing-path"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["band"], "wrong-now")
            self.assertEqual(hits[0]["file"], "",
                             "the repo file is named in the detail, so `file` "
                             "stays a project-relative path")
            self.assertIn("AGENTS.md", hits[0]["detail"])
            self.assertIn("scripts/enforce-branch-rule.sh", hits[0]["detail"])

    def test_no_code_root_means_no_reach(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _project(Path(tmp))
            report = truth_report(pdir, vault=Path(tmp) / "vault",
                                  today=date(2026, 9, 1))
            self.assertNotIn("agents-missing-path", _kinds(report))
            self.assertNotIn("agents-unchanged", _kinds(report))

    def test_an_agents_file_that_names_only_real_things_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = _project(root)
            code = root / "code"
            (code / "scripts").mkdir(parents=True)
            _w(code / "scripts" / "real.sh", "#!/bin/sh\n")
            _w(code / "AGENTS.md", "Run `scripts/real.sh`.\n")
            report = truth_report(pdir, vault=root / "vault", code_root=code,
                                  today=date(2026, 9, 1))
            self.assertNotIn("agents-missing-path", _kinds(report))


if __name__ == "__main__":
    unittest.main()
