"""Acceptance tests for adjudant v3 plan 4: structure and truth.

Three claims, asserted:

  The truth test. Seed a project where AGENTS.md names a missing script, a
  card sits open in an archive, a decision is superseded with no target, and a
  page is 100 days unverified. All four appear, ranked, in one status run.

  Link round-trip. One file of every kind, every link resolving by path with
  no full-vault scan, and every link still resolving after the project moves
  between lifecycle folders.

  Triage dry-run. One prompt per project across 27 projects, and nothing moves.

Deviation from the plan text: the plan calls the entry point
`run_status(project_dir, code_root=None, today=None)`. The tree as it stands
has no such name — plan 3's descendant of `check.run_check` is `status.run`,
whose real signature is `run(project_dir, vault_dir=None, *, code_root=None,
now=None, today: Optional[str] = None, sync=True)`. Note `today` is a STRING
here (unlike `truth_report`'s `today`, which is a real `date`) because `run`
derives its own `date` from it. Both are used correctly below.
"""

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _index_gen import regenerate
from _lifecycle import apply_move, triage_plan
from _place import KIND_FOLDER, link, place, project_rel
from _vault_walk import build_vault_index, extract_wikilinks, resolve_wikilink
from status import run


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class TestTheTruthTest(unittest.TestCase):
    """All four seeded failures must appear, ranked, in one status run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = root / "vault"
        self.code = root / "code"
        self.pdir = self.vault / "projects" / "active" / "demo"
        self._ob = os.environ.pop("OB_VAULT", None)

        # 1. AGENTS.md names a script that is not there. scripts/ itself
        # exists, holding a sibling, which is the realistic shape of this
        # drift and what makes the claim checkable at all.
        (self.code / "scripts").mkdir(parents=True, exist_ok=True)
        _w(self.code / "scripts" / "still-here.sh", "#\n")
        _w(self.code / "AGENTS.md",
           "Branch rules are enforced mechanically by "
           "`scripts/enforce-branch-rule.sh`.\n")

        _w(self.pdir / "brief.md",
           "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n"
           "---\n\n# Demo\n\nWhat this project is.\n\n"
           "## Where things are\n| | |\n|---|---|\n")
        _w(self.pdir / "sessions" / "2026-09-01.md",
           "---\ntype: session\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
           "---\n\n## Log\n")

        # 2. A card sits open inside the archive.
        _w(self.pdir / "tasks" / "_archive" / "still-open.md",
           "---\ntype: task\ncreated: 2026-01-01\nupdated: 2026-02-01\n"
           "status: doing\n---\n\n# Still open\n\n## Done when\nIt is done.\n")

        # 3. A decision is superseded with no target.
        _w(self.pdir / "decisions" / "2026-08-01-orphaned.md",
           "---\ntype: decision\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
           "status: superseded\n---\n\n# Orphaned\n")

        # 4. A page is 100 days unverified.
        _w(self.pdir / "docs" / "cache.md",
           "---\ntype: doc\nupdated: 2026-09-01\nverified: 2026-05-24\n"
           "---\n\n# Cache\n")

    def tearDown(self):
        if self._ob is not None:
            os.environ["OB_VAULT"] = self._ob
        self._tmp.cleanup()

    def test_all_four_appear_ranked_in_one_status_run(self):
        report = run(self.pdir, self.vault, code_root=self.code,
                     today="2026-09-01")
        findings = report["truth"]["findings"]
        kinds = [f["kind"] for f in findings]
        for expected in ("agents-missing-path", "open-card-in-archive",
                         "superseded-without-target", "verified-stale"):
            self.assertIn(expected, kinds, f"{expected} was not found")

        bands = [f["band"] for f in findings]
        rank = {"wrong-now": 0, "going-stale": 1, "worth-a-look": 2}
        self.assertEqual([rank[b] for b in bands],
                         sorted(rank[b] for b in bands),
                         "findings are not ordered by cost of being wrong")

        by_kind = {f["kind"]: f for f in findings}
        self.assertEqual(by_kind["agents-missing-path"]["band"], "wrong-now")
        self.assertEqual(by_kind["open-card-in-archive"]["band"], "wrong-now")
        self.assertEqual(by_kind["superseded-without-target"]["band"], "wrong-now")
        self.assertEqual(by_kind["verified-stale"]["band"], "going-stale")
        self.assertIn("100 days", by_kind["verified-stale"]["detail"])
        self.assertEqual(report["truth"]["counts"]["wrong-now"], 3)

    def test_the_truth_report_writes_nothing_at_all(self):
        # `run` makes derived state current before reporting (it may bump
        # brief.md's `updated:` and mirror a handoff), so the assertion that
        # nothing is written belongs to `truth_report` alone, called directly.
        from truth import truth_report
        before = sorted(str(p) for p in self.vault.rglob("*"))
        truth_report(self.pdir, vault=self.vault, code_root=self.code,
                     today=date(2026, 9, 1))
        self.assertEqual(sorted(str(p) for p in self.vault.rglob("*")), before)

    def test_agents_md_is_never_rewritten(self):
        original = (self.code / "AGENTS.md").read_text()
        run(self.pdir, self.vault, code_root=self.code, today="2026-09-01")
        self.assertEqual((self.code / "AGENTS.md").read_text(), original)


class TestLinkRoundTrip(unittest.TestCase):
    """One file of every kind, every link resolving by path, and every link
    still resolving after the project moves between lifecycle folders."""

    KIND_HINTS = {
        "session": {"date": "2026-09-01"},
        "decision": {"date": "2026-09-01", "slug": "drop-bucket-a-tags"},
        "dream": {"date": "2026-09-01"},
        "component": {"slug": "button", "group": "modules"},
    }

    def _one_of_every_kind(self, pdir: Path) -> list:
        made = []
        for kind in sorted(KIND_FOLDER):
            if kind == "release":
                # `place()` requires a kebab-case slug (no dots), so it can
                # never produce a release note's REAL shape: a version
                # number, written directly by posttooluse-commit-log.py as
                # `v{version}.md` rather than through place() for exactly
                # this reason. A kebab-safe stand-in slug here ("release.md")
                # would dodge the one shape most likely to break something —
                # the same "fixture avoids the one case that would break"
                # gap plan 1's test_no_crud.py had before it was fixed to
                # write into more than one folder. Placed by hand instead, at
                # its real name.
                path = pdir / KIND_FOLDER["release"] / "v2.1.0.md"
                path.parent.mkdir(parents=True, exist_ok=True)
            else:
                hints = dict(self.KIND_HINTS.get(
                    kind, {"slug": kind.replace("_", "-")}))
                path = place(kind, pdir, hints)
            _w(path, f"---\ntype: {kind}\nupdated: 2026-09-01\n---\n\n# {kind}\n")
            made.append(path)
        return made

    def test_every_link_resolves_before_and_after_a_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            pdir = vault / "projects" / "active" / "demo"
            pdir.mkdir(parents=True)
            made = self._one_of_every_kind(pdir)
            self.assertEqual(len(made), 15)

            # An index of links, one per file, written into a note.
            body = "\n".join(
                f"- {link(project_rel(p, pdir), p.stem)}" for p in made)
            note = pdir / "notes" / "link-index.md"
            _w(note, f"---\ntype: note\nupdated: 2026-09-01\n---\n\n{body}\n")

            targets = [wl.target for wl in extract_wikilinks(note.read_text())]
            self.assertEqual(len(targets), 15)
            for t in targets:
                self.assertFalse(t.startswith("projects/"), t)
                self.assertFalse(t.split("/", 1)[0] in
                                 ("active", "paused", "finished", "archive"), t)

            idx = build_vault_index(vault)
            for t in targets:
                self.assertTrue(resolve_wikilink(t, idx), f"before move: {t}")

            apply_move(vault, "demo", "finished")

            idx = build_vault_index(vault)
            for t in targets:
                self.assertTrue(resolve_wikilink(t, idx), f"after move: {t}")

    def test_a_link_that_names_the_lifecycle_folder_is_refused(self):
        # A BARE lifecycle folder stays refused: nothing there says whether
        # `active` is a zone or a project called active.
        with self.assertRaises(ValueError):
            link("active/demo/notes/a")

    def test_a_vault_root_path_yields_the_zone_less_link(self):
        # SUPERSEDED the second half of the test above, which required a
        # vault-root path to raise. build_vault_index resolves that form, so
        # refusing it made clean silently drop 270 of 543 conversions. The
        # outcome that matters is that the link carries no lifecycle folder,
        # and normalising delivers exactly that.
        self.assertEqual(link("projects/demo/notes/a"), "[[demo/notes/a]]")
        for zone in ("active", "paused", "finished", "archive"):
            self.assertEqual(link(f"projects/{zone}/demo/notes/a"),
                             "[[demo/notes/a]]")


class TestTriageDryRun(unittest.TestCase):
    """27 projects, 27 prompts, and nothing moves."""

    def test_twenty_seven_prompts_and_no_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            for i in range(27):
                zone = ("active", "paused", "finished", "archive")[i % 4]
                pdir = vault / "projects" / zone / f"p{i:02d}"
                _w(pdir / "brief.md",
                   "---\ntype: project\nupdated: 2026-09-01\n"
                   "verified: 2026-09-01\n---\n\n# P\n")
                _w(pdir / "sessions" / "2026-08-30.md",
                   "---\ntype: session\n---\n")
            before = sorted(str(p.relative_to(vault))
                            for p in vault.rglob("brief.md"))
            plan = triage_plan(vault, date(2026, 9, 1))
            self.assertEqual(len(plan), 27)
            after = sorted(str(p.relative_to(vault))
                           for p in vault.rglob("brief.md"))
            self.assertEqual(before, after)

    def test_regenerating_the_indexes_leaves_exactly_one_per_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            for i in range(27):
                pdir = vault / "projects" / "active" / f"p{i:02d}"
                _w(pdir / "brief.md",
                   "---\ntype: project\nupdated: 2026-09-01\n"
                   "verified: 2026-09-01\n---\n\n# P\n")
                _w(pdir / "notes" / "_index.md", "---\ntype: index\n---\n\n# N\n")
            out = regenerate(vault, date(2026, 9, 1))
            self.assertEqual(len(out["deleted"]), 27)
            self.assertEqual(len(out["projects"]), 27)
            survivors = list(vault.rglob("_index.md"))
            self.assertEqual(len(survivors), 27)
            self.assertTrue((vault / "Home.md").is_file())


if __name__ == "__main__":
    unittest.main()
