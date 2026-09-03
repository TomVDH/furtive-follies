"""Tests for adjudant/scripts/_index_gen.py — the two generated surfaces.

Home carries 39 project links against 27 projects. projects/_index.md has 28
rows, two of them duplicated, with malformed table pipes. Both were
hand-maintained, and a hand-maintained list of a directory is stale the moment
anything changes.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from _index_gen import (
    prune_index_files,
    regenerate,
    render_home,
    render_project_index,
    write_home,
    write_project_index,
)
from _vault_walk import build_vault_index, parse_frontmatter, resolve_wikilink


def _mk(vault: Path, slug: str, zone: str = "active", sessions=()) -> Path:
    pdir = vault / "projects" / zone / slug
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "brief.md").write_text(
        "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-08-13\n---\n\n"
        f"# {slug.title()}\n\nWhat this project is.\n")
    if sessions:
        (pdir / "sessions").mkdir(exist_ok=True)
        for d in sessions:
            (pdir / "sessions" / f"{d}.md").write_text("---\ntype: session\n---\n")
    return pdir


class TestHome(unittest.TestCase):

    def test_grouped_by_lifecycle_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "alpha", "active", ["2026-08-30"])
            _mk(vault, "beta", "paused", ["2026-01-02"])
            _mk(vault, "gamma", "finished")
            text = render_home(vault, date(2026, 9, 1))
            self.assertIn("## Active", text)
            self.assertIn("## Paused", text)
            self.assertIn("## Finished", text)
            self.assertNotIn("## Archive", text,
                             "an empty lifecycle folder gets no heading")
            self.assertLess(text.index("## Active"), text.index("## Paused"))

    def test_one_row_per_project_with_its_last_active_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "alpha", "active", ["2026-08-30"])
            text = render_home(vault, date(2026, 9, 1))
            self.assertEqual(text.count("[[alpha/brief"), 1)
            self.assertIn("2026-08-30", text)

    def test_a_project_with_no_sessions_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "alpha", "active")
            self.assertIn("never", render_home(vault, date(2026, 9, 1)))

    def test_links_omit_the_lifecycle_folder_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "alpha", "paused", ["2026-08-30"])
            text = render_home(vault, date(2026, 9, 1))
            self.assertNotIn("projects/", text)
            self.assertNotIn("[[paused/", text)
            self.assertTrue(resolve_wikilink("alpha/brief", build_vault_index(vault)))

    def test_home_keeps_the_type_the_resolver_looks_for(self):
        # _vault_walk resolves a vault by finding Home.md with a type in
        # VAULT_HOME_TYPES = {"vault-home", "index"}. "vault-home" is not one
        # of the fifteen schema-backed kinds any more (plan 2 retired it), so
        # Home shares "index" with the project contents page rather than
        # reviving a sixteenth kind for one file.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "alpha", "active")
            fm, _ = parse_frontmatter(render_home(vault, date(2026, 9, 1)))
            self.assertEqual(fm.fields["type"], "index")
            self.assertEqual(fm.fields["updated"], "2026-09-01")

    def test_write_home_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk(vault, "alpha", "active", ["2026-08-30"])
            p1 = write_home(vault, date(2026, 9, 1))
            first = p1.read_text()
            p2 = write_home(vault, date(2026, 9, 1))
            self.assertEqual(p1, p2)
            self.assertEqual(p1, vault / "Home.md")
            self.assertEqual(first, p2.read_text())

    def test_an_empty_vault_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "projects").mkdir()
            text = render_home(vault, date(2026, 9, 1))
            self.assertIn("No projects yet", text)


class TestProjectIndex(unittest.TestCase):

    def _full(self, tmp: Path) -> Path:
        pdir = _mk(tmp, "demo", "active", ["2026-08-30", "2026-08-31"])
        (pdir / "_handoff.md").write_text("---\ntype: handoff\n---\n\n# Handoff\n")
        for folder, names in (
            ("decisions", ["2026-08-12-branch-track.md"]),
            ("specs", ["spec-018-page-spinup.md"]),
            ("notes", ["cold-cache.md", "warm-cache.md"]),
        ):
            (pdir / folder).mkdir()
            for n in names:
                (pdir / folder / n).write_text(
                    f"---\ntype: {folder[:-1]}\nupdated: 2026-08-12\n---\n\n# {n}\n")
        return pdir

    def test_start_here_names_the_brief_the_handoff_and_the_newest_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._full(Path(tmp))
            text = render_project_index(pdir, date(2026, 9, 1))
            self.assertIn("## Start here", text)
            self.assertIn("[[demo/brief|", text)
            self.assertIn("[[demo/_handoff|", text)
            self.assertIn("[[demo/sessions/2026-08-31|", text)

    def test_specs_are_surfaced_near_the_top(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._full(Path(tmp))
            text = render_project_index(pdir, date(2026, 9, 1))
            self.assertIn("## Specs", text)
            self.assertIn("[[demo/specs/spec-018-page-spinup|", text)
            self.assertLess(text.index("## Specs"), text.index("## Contents"))

    def test_contents_carries_counts_and_the_newest_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._full(Path(tmp))
            text = render_project_index(pdir, date(2026, 9, 1))
            self.assertIn("| notes | 2 |", text)
            self.assertIn("| decisions | 1 |", text)
            # Escaped: this cell sits inside a markdown table, where a bare
            # `|` would be read as the next column (_place.link's in_table
            # contract, test__place.TestLink.test_table_cells_escape_the_separator).
            self.assertIn("[[demo/notes/warm-cache\\|", text)

    def test_an_empty_folder_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._full(Path(tmp))
            (pdir / "tasks").mkdir()
            self.assertNotIn("| tasks |",
                             render_project_index(pdir, date(2026, 9, 1)))

    def test_the_index_never_lists_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._full(Path(tmp))
            write_project_index(pdir, date(2026, 9, 1))
            text = render_project_index(pdir, date(2026, 9, 1))
            self.assertNotIn("[[demo/_index", text)

    def test_a_generated_page_is_never_listed(self):
        # "Adjudant stays out of generated files": a page carrying source: is
        # rewritten by its own script every run.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._full(Path(tmp))
            (pdir / "components").mkdir()
            (pdir / "components" / "gen.md").write_text(
                "---\ntype: component\nupdated: 2026-09-01\n"
                "source: build-module-inventory.py\n---\n\n# gen\n")
            (pdir / "components" / "hand.md").write_text(
                "---\ntype: component\nupdated: 2026-09-01\n---\n\n# hand\n")
            text = render_project_index(pdir, date(2026, 9, 1))
            self.assertIn("| components | 1 |", text)
            # Escaped: this cell sits inside a markdown table (see the note
            # in test_contents_carries_counts_and_the_newest_entry above).
            self.assertIn("[[demo/components/hand\\|", text)
            self.assertNotIn("[[demo/components/gen", text)

    def test_write_lands_at_the_project_root_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._full(Path(tmp))
            p1 = write_project_index(pdir, date(2026, 9, 1))
            self.assertEqual(p1, pdir / "_index.md")
            first = p1.read_text()
            self.assertEqual(write_project_index(pdir, date(2026, 9, 1)).read_text(),
                             first)

    def test_every_link_it_writes_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pdir = self._full(vault)
            write_project_index(pdir, date(2026, 9, 1))
            idx = build_vault_index(vault)
            from _vault_walk import extract_wikilinks
            body = (pdir / "_index.md").read_text()
            links = extract_wikilinks(body)
            self.assertTrue(links)
            for wl in links:
                self.assertTrue(resolve_wikilink(wl.target, idx), wl.target)


class TestPruneAndRegenerate(unittest.TestCase):

    def _vault(self, tmp: Path) -> Path:
        vault = tmp / "v"
        pdir = _mk(vault, "demo", "active", ["2026-08-30"])
        for folder in ("decisions", "notes", "tasks"):
            (pdir / folder).mkdir()
            (pdir / folder / "_index.md").write_text(
                "---\ntype: index\n---\n\n# X\n\n## Entries\n")
            (pdir / folder / "real.md").write_text(
                "---\ntype: note\nupdated: 2026-08-01\n---\n\n# real\n")
        (pdir / "_index.md").write_text("hand written, will be overwritten")
        (vault / "projects" / "_index.md").write_text(
            "---\ntype: index\n---\n\n# All Projects\n")
        return vault

    def test_prune_removes_folder_indexes_and_the_projects_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            deleted = prune_index_files(vault)
            names = sorted(str(p.relative_to(vault)) for p in deleted)
            self.assertEqual(len(names), 4)
            self.assertIn("projects/_index.md", names)
            for folder in ("decisions", "notes", "tasks"):
                self.assertIn(f"projects/active/demo/{folder}/_index.md", names)

    def test_prune_keeps_the_project_contents_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            prune_index_files(vault)
            self.assertTrue(
                (vault / "projects" / "active" / "demo" / "_index.md").is_file())

    def test_prune_keeps_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            (vault / "Home.md").write_text("---\ntype: index\n---\n# Vault\n")
            prune_index_files(vault)
            self.assertTrue((vault / "Home.md").is_file())

    def test_prune_touches_no_content_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            prune_index_files(vault)
            self.assertEqual(
                len(list((vault / "projects").rglob("real.md"))), 3)

    def test_prune_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            prune_index_files(vault)
            self.assertEqual(prune_index_files(vault), [])

    def test_regenerate_writes_both_surfaces_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            out = regenerate(vault, date(2026, 9, 1))
            self.assertEqual(out["home"], str(vault / "Home.md"))
            self.assertEqual(
                out["projects"],
                [str(vault / "projects" / "active" / "demo" / "_index.md")])
            self.assertEqual(len(out["deleted"]), 4)
            survivors = sorted(
                str(p.relative_to(vault)) for p in vault.rglob("_index.md"))
            self.assertEqual(survivors, ["projects/active/demo/_index.md"])


if __name__ == "__main__":
    unittest.main()
