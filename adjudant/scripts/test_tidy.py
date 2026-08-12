"""Tests for adjudant/scripts/tidy.py."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tidy import (
    PREVIEW_DIR_NAME,
    BACKUP_DIR_NAME,
    apply_preview,
    build_preview,
    cli_main as tidy_cli,
    detect_phase,
    fix_wikilink_form,
    generate_index_content,
    normalize_tags,
    upsert_index_content,
    write_preview_to_disk,
    _migrate_ob_to_bucket_a,
    _rewrite_tags_block,
    _bump_updated_field,
)
from _vault_walk import build_vault_index


def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ============================================================
# Detection
# ============================================================


class TestDetectPhase(unittest.TestCase):

    def test_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(detect_phase(Path(tmp)), "fresh")

    def test_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / PREVIEW_DIR_NAME).mkdir()
            self.assertEqual(detect_phase(Path(tmp)), "preview")

    def test_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / BACKUP_DIR_NAME / "20260526T120000Z").mkdir(parents=True)
            (Path(tmp) / BACKUP_DIR_NAME / "20260526T120000Z" / "x.legacy").write_text("old")
            self.assertEqual(detect_phase(Path(tmp)), "applied")


# ============================================================
# Tag normalisation
# ============================================================


class TestNormalizeTags(unittest.TestCase):

    def test_ob_bucket_a_migrates(self):
        # ob/{bucket-A-type} → {bucket-A-type} preserves §2A file-type tag
        new, dropped = normalize_tags(["ob/doc"], project_slug="x")
        self.assertEqual(new, ["doc"])
        self.assertEqual(dropped, ["ob/doc → doc"])

    def test_ob_non_bucket_a_drops(self):
        # ob/api-ref is not Bucket A → drop
        new, dropped = normalize_tags(["ob/api-ref"], project_slug="x")
        self.assertEqual(new, [])
        self.assertEqual(dropped, ["ob/api-ref"])

    def test_ob_dedup_with_existing_bare(self):
        new, _ = normalize_tags(["project", "ob/project"], project_slug="x")
        # ob/project migrates to project, dedup keeps just 'project'
        self.assertEqual(new, ["project"])

    def test_drops_project_slug_self_tag(self):
        new, _ = normalize_tags(["acme-web", "decision"], project_slug="acme-web")
        self.assertEqual(new, ["decision"])

    def test_dedup(self):
        new, _ = normalize_tags(["a", "a", "b"], project_slug=None)
        self.assertEqual(new, ["a", "b"])

    def test_preserves_unknown(self):
        new, _ = normalize_tags(["content/blog", "auth"], project_slug=None)
        # content/blog is canonical (§2C); 'auth' is uncategorised but not Bucket D
        self.assertIn("content/blog", new)
        self.assertIn("auth", new)


# ============================================================
# Wikilink form fix
# ============================================================


class TestFixWikilinkForm(unittest.TestCase):

    def test_external_url_ending_in_md_untouched(self):
        idx = {"README.md", "README"}
        body = "see [x](https://github.com/a/b/blob/main/README.md) ok"
        out, n = fix_wikilink_form(body, idx)
        self.assertEqual(out, body)
        self.assertEqual(n, 0)

    def test_heading_anchor_preserved(self):
        idx = {"notes/n.md", "notes/n", "n", "n.md"}
        out, n = fix_wikilink_form("[Foo](n.md#Section)", idx)
        self.assertEqual(out, "[[n#Section|Foo]]")
        self.assertEqual(n, 1)

    def test_relative_paths_untouched(self):
        idx = {"bar.md", "bar"}
        body = "[t](../foo/bar.md) and [u](./bar.md)"
        out, n = fix_wikilink_form(body, idx)
        self.assertEqual(out, body)
        self.assertEqual(n, 0)

    def test_inline_code_span_untouched(self):
        idx = {"n.md", "n"}
        body = "real [t](n.md) and code `[t](n.md)` here"
        out, n = fix_wikilink_form(body, idx)
        self.assertEqual(out, "real [[n|t]] and code `[t](n.md)` here")
        self.assertEqual(n, 1)

    def test_indented_code_block_untouched(self):
        idx = {"n.md", "n"}
        # Same heuristic as the detectors: 4-space indent skipped unless the
        # first char is a list/table marker (hanging-indent continuation).
        body = "para\n\n    x = [t](n.md) in code block\n\n[t](n.md) in prose"
        out, n = fix_wikilink_form(body, idx)
        self.assertIn("    x = [t](n.md) in code block", out)
        self.assertIn("[[n|t]] in prose", out)
        self.assertEqual(n, 1)

    def test_rewrites_resolvable(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _w(vault / "target.md", "x")
            idx = build_vault_index(vault)
            body = "See [target](target.md)."
            new, count = fix_wikilink_form(body, idx)
            self.assertEqual(count, 1)
            self.assertIn("[[target]]", new)

    def test_preserves_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _w(vault / "target.md", "x")
            idx = build_vault_index(vault)
            body = "See [the target](target.md)."
            new, _ = fix_wikilink_form(body, idx)
            self.assertIn("[[target|the target]]", new)

    def test_unresolvable_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = build_vault_index(Path(tmp))  # empty vault
            body = "See [target](target.md)."
            new, count = fix_wikilink_form(body, idx)
            self.assertEqual(count, 0)
            self.assertEqual(new, body)

    def test_skips_code_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _w(vault / "target.md", "x")
            idx = build_vault_index(vault)
            body = "Real [target](target.md)\n```\n[fake](target.md)\n```"
            new, count = fix_wikilink_form(body, idx)
            self.assertEqual(count, 1)
            # fenced block content unchanged
            self.assertIn("[fake](target.md)", new)


# ============================================================
# Tags block surgical rewrite
# ============================================================


class TestRewriteTagsBlock(unittest.TestCase):

    def test_replace_existing(self):
        text = "---\ntype: note\ntags:\n  - ob/note\n  - keep\n---\n\nbody"
        new = _rewrite_tags_block(text, ["keep"])
        self.assertIn("tags:\n  - keep", new)
        self.assertNotIn("ob/note", new)
        self.assertIn("body", new)

    def test_empty_tags_removes_block(self):
        text = "---\ntype: note\ntags:\n  - ob/note\n---\n\nbody"
        new = _rewrite_tags_block(text, [])
        self.assertNotIn("tags:", new)

    def test_no_existing_block_adds_when_tags(self):
        text = "---\ntype: note\n---\n\nbody"
        new = _rewrite_tags_block(text, ["new"])
        self.assertIn("tags:\n  - new", new)


class TestBumpUpdatedField(unittest.TestCase):

    def test_bumps_existing(self):
        text = "---\ntype: note\nupdated: 2026-05-01\n---\n\nbody"
        new = _bump_updated_field(text, "2026-05-26")
        self.assertIn("updated: 2026-05-26", new)
        self.assertNotIn("updated: 2026-05-01", new)

    def test_does_not_add(self):
        text = "---\ntype: note\n---\n\nbody"
        new = _bump_updated_field(text, "2026-05-26")
        self.assertNotIn("updated:", new)


# ============================================================
# Index generation
# ============================================================


class TestMigrateObToBucketA(unittest.TestCase):

    def test_bucket_a_migrates(self):
        self.assertEqual(_migrate_ob_to_bucket_a("ob/decision"), "decision")
        self.assertEqual(_migrate_ob_to_bucket_a("ob/doc"), "doc")
        self.assertEqual(_migrate_ob_to_bucket_a("ob/dream-report"), "dream-report")

    def test_non_bucket_a_returns_none(self):
        self.assertIsNone(_migrate_ob_to_bucket_a("ob/api-ref"))
        self.assertIsNone(_migrate_ob_to_bucket_a("ob/gemini"))

    def test_non_ob_prefix_returns_none(self):
        self.assertIsNone(_migrate_ob_to_bucket_a("decision"))
        self.assertIsNone(_migrate_ob_to_bucket_a("custom/decision"))


class TestUpsertIndexContent(unittest.TestCase):

    def test_upsert_bullet_list_preserves_intro(self):
        existing = (
            "---\n"
            "type: index\n"
            "tags:\n  - ob/index\n  - architecture\n"
            "updated: 2026-05-01\n"
            "---\n\n"
            "# Decisions\n\n"
            "Intro paragraph that the human wrote. Preserve me.\n\n"
            "## Entries\n\n"
            "- [[old-entry|old]]\n\n"
            "## Notes\n\n"
            "Trailing section. Preserve me too.\n"
        )
        entries = [Path("a.md"), Path("b.md")]
        new, mode = upsert_index_content(existing, "decisions", entries, "x")
        self.assertEqual(mode, "upserted")
        # Intro preserved
        self.assertIn("Intro paragraph that the human wrote", new)
        # Trailing section preserved
        self.assertIn("Trailing section. Preserve me too.", new)
        # New entries
        self.assertIn("[[a|a]]", new)
        self.assertIn("[[b|b]]", new)
        # Old entry gone
        self.assertNotIn("old-entry", new)
        # Tags normalized
        self.assertNotIn("ob/index", new)
        self.assertIn("- index", new)

    def test_curated_alias_survives_the_rebuild(self):
        # A hand-written alias is the one line of an index a human actually
        # authored, and it is unrecoverable from the filename. Replacing it
        # with a slug-title also contradicts the one-line-per-entry
        # convention the rebuild exists to serve.
        existing = (
            "---\ntype: index\ntags:\n  - index\nupdated: 2026-05-01\n---\n\n"
            "# Memory\n\n## Entries\n\n"
            "- [[prefers-agents-md|Canonical repo context lives in AGENTS.md]]\n"
        )
        new, mode = upsert_index_content(
            existing, "memory", [Path("prefers-agents-md.md")], "x")
        self.assertEqual(mode, "upserted")
        self.assertIn("[[prefers-agents-md|Canonical repo context lives in AGENTS.md]]",
                      new)

    def test_a_new_entry_still_gets_the_generated_alias(self):
        # Preserving curated text must not stop the rebuild doing its job for
        # entries nobody has annotated yet.
        existing = (
            "---\ntype: index\ntags:\n  - index\nupdated: 2026-05-01\n---\n\n"
            "# Memory\n\n## Entries\n\n"
            "- [[prefers-agents-md|Canonical repo context lives in AGENTS.md]]\n"
        )
        new, _ = upsert_index_content(
            existing, "memory",
            [Path("prefers-agents-md.md"), Path("in-repo-over-patch.md")], "x")
        self.assertIn("[[in-repo-over-patch|in repo over patch]]", new)
        self.assertIn("Canonical repo context lives in AGENTS.md", new)

    def test_a_stale_alias_for_a_deleted_entry_does_not_come_back(self):
        # Preservation is keyed on the entry still existing; a curated alias
        # is not a reason to resurrect a file that is gone.
        existing = (
            "---\ntype: index\ntags:\n  - index\nupdated: 2026-05-01\n---\n\n"
            "# Memory\n\n## Entries\n\n"
            "- [[deleted-one|Something I wrote about a file that is gone]]\n"
        )
        new, _ = upsert_index_content(existing, "memory", [Path("kept.md")], "x")
        self.assertNotIn("deleted-one", new)
        self.assertIn("[[kept|kept]]", new)

    def test_upsert_table_format_leaves_body_alone(self):
        existing = (
            "---\n"
            "type: index\n"
            "tags:\n  - ob/index\n"
            "updated: 2026-05-01\n"
            "---\n\n"
            "# Nightly\n\n"
            "## Entries\n\n"
            "| Doc | Purpose |\n"
            "|---|---|\n"
            "| [[architecture]] | system overview |\n"
        )
        new, mode = upsert_index_content(existing, "acme", [Path("a.md"), Path("b.md")], "x")
        self.assertEqual(mode, "frontmatter_only")
        # Table preserved verbatim
        self.assertIn("| Doc | Purpose |", new)
        self.assertIn("[[architecture]]", new)
        # But tags normalized
        self.assertNotIn("ob/index", new)

    def test_upsert_no_entries_heading_leaves_body_alone(self):
        existing = (
            "---\n"
            "type: index\n"
            "tags:\n  - index\n"
            "updated: 2026-05-01\n"
            "---\n\n"
            "# Some Index\n\n"
            "Free-form content with no entries heading.\n"
        )
        new, mode = upsert_index_content(existing, "x", [Path("a.md"), Path("b.md")], "x")
        self.assertEqual(mode, "frontmatter_only")
        self.assertIn("Free-form content with no entries heading.", new)


class TestGenerateIndexContent(unittest.TestCase):

    def test_chronological_for_dated(self):
        out = generate_index_content("decisions", [
            Path("2026-05-26-a.md"),
            Path("2026-05-27-b.md"),
            Path("2026-05-25-c.md"),
        ], project_slug="x")
        # 2026-05-27 should come first
        lines = out.split("\n")
        entry_lines = [l for l in lines if l.startswith("- [[")]
        self.assertEqual(entry_lines[0], "- [[2026-05-27-b|2026-05-27 b]]")
        self.assertEqual(entry_lines[1], "- [[2026-05-26-a|2026-05-26 a]]")
        self.assertEqual(entry_lines[2], "- [[2026-05-25-c|2026-05-25 c]]")

    def test_alphabetical_for_plain(self):
        out = generate_index_content("notes", [
            Path("zebra.md"),
            Path("alpha.md"),
            Path("mango.md"),
        ], project_slug="x")
        entry_lines = [l for l in out.split("\n") if l.startswith("- [[")]
        self.assertEqual(entry_lines[0], "- [[alpha|alpha]]")
        self.assertEqual(entry_lines[2], "- [[zebra|zebra]]")

    def test_has_frontmatter_and_heading(self):
        out = generate_index_content("decisions", [Path("2026-05-26-a.md")], project_slug="x")
        self.assertTrue(out.startswith("---\n"))
        self.assertIn("type: index", out)
        self.assertIn("# Decisions", out)


# ============================================================
# build_preview end-to-end
# ============================================================


class TestBuildPreview(unittest.TestCase):

    def test_dirty_project_produces_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
                "---\ntype: project\nslug: t\nproject_type: coding\nupdated: 2026-05-01\n"
                "tags:\n  - project\n  - ob/project\n---\n\n# T\n")
            # Two decisions, no index
            _w(root / "decisions" / "2026-05-26-a.md", "---\ntype: decision\n---\n")
            _w(root / "decisions" / "2026-05-25-b.md", "---\ntype: decision\n---\n")
            # File with a markdown-style link to a vault file
            _w(root / "target.md", "---\ntype: note\n---\n")
            _w(root / "src.md",
                "---\ntype: note\ntags:\n  - ob/note\n---\n\nSee [target](target.md).")
            vault_index = build_vault_index(root)
            cs = build_preview(root, vault_index, project_slug="t")
            # Should rebuild decisions/_index.md
            self.assertIn("decisions/_index.md", cs["index_proposals"])
            # Should propose changes to src.md (tags + wikilink)
            self.assertIn("src.md", cs["file_proposals"])
            # Should propose changes to brief.md (tags)
            self.assertIn("brief.md", cs["file_proposals"])

    def test_clean_project_no_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
                "---\ntype: project\nslug: t\nproject_type: coding\n"
                "tags:\n  - project\n---\n\n# T\n")
            _w(root / "_handoff.md", "---\ntype: handoff\n---\nbody")
            cs = build_preview(root, set(), project_slug="t")
            self.assertEqual(cs["summary"]["total_changes"], 0)


# ============================================================
# write_preview + apply_preview round-trip
# ============================================================


class TestApplySafety(unittest.TestCase):
    """Audit 2026-07-27 findings 8, 12, 17 — the apply path could destroy work.

    The vault is multi-machine synced and the preview window exists precisely
    so a human or agent can review (and edit) between the two phases.
    """

    def _dirty(self, root: Path) -> Path:
        """A project with one file tidy will want to change."""
        p = root / "decisions" / "2026-01-01-d.md"
        _w(p, "---\ntype: decision\nstatus: accepted\ndate: 2026-01-01\n"
              "tags:\n  - decision\n  - ob/legacy\n---\n\nBody.\n")
        return p

    def test_stale_preview_does_not_clobber_a_fresh_edit(self):
        # Finding 8: original_hash is recorded in changes.json and never
        # checked, so an edit made between preview and apply vanished.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = self._dirty(root)
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            live.write_text(live.read_text() + "\nA paragraph I added after preview.\n")
            apply_preview(root)
            self.assertIn("A paragraph I added after preview.", live.read_text(),
                          "apply must not overwrite a file edited since preview")

    def test_untouched_files_still_apply_when_a_sibling_is_stale(self):
        # One stale file must not block the rest of the sweep.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = self._dirty(root)
            other = root / "decisions" / "2026-01-02-e.md"
            _w(other, "---\ntype: decision\nstatus: locked\ndate: 2026-01-02\n"
                      "tags:\n  - decision\n---\n\nBody.\n")
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            stale.write_text(stale.read_text() + "\nfresh edit\n")
            apply_preview(root)
            self.assertIn("fresh edit", stale.read_text())
            self.assertIn("status: active", other.read_text())

    def test_backup_dir_collision_preserves_the_first_backup(self):
        # Finding 12: second-granularity dirs + exist_ok=True meant a retry
        # inside the same second overwrote the ONLY pre-change backup with
        # already-tidied content.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._dirty(root)
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            first = apply_preview(root)
            legacy = list(first.rglob("*.legacy"))
            self.assertTrue(legacy, "first apply must back up the original")
            self.assertIn("status: accepted", legacy[0].read_text(),
                          "backup must hold the PRE-tidy content")
            # Immediately run a second cycle (same wall-clock second).
            cs2 = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs2)
            second = apply_preview(root)
            self.assertNotEqual(first, second, "each apply needs its own backup dir")
            self.assertIn("status: accepted", legacy[0].read_text(),
                          "the first backup must survive a same-second retry")

    def test_apply_refuses_paths_outside_the_project(self):
        # Finding 17: a tampered changes.json wrote outside project_dir,
        # bypassing both the backup and the skip set.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            root.mkdir()
            self._dirty(root)
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            preview = root / PREVIEW_DIR_NAME
            changes = json.loads((preview / "changes.json").read_text())
            escaped = "../escaped-outside-project.md"
            changes["file_proposals"][escaped] = {
                "original_hash": "x", "proposed_hash": "y",
                "proposed_content": "pwned\n"}
            (preview / "changes.json").write_text(json.dumps(changes))
            target = preview / "files" / escaped
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("pwned\n")
            apply_preview(root)
            self.assertFalse((Path(tmp) / "escaped-outside-project.md").exists(),
                             "apply must never write outside the project dir")


class TestStalePreviewGuardHoles(unittest.TestCase):
    """Fix wave 1 finding 5: two holes in finding 8's stale-preview guard.

    (a) The guard covered `file_proposals` only. `index_proposals` carried no
        `original_hash`, so an `_index.md` edited between preview and apply was
        still silently overwritten. The proposal is computed FROM that file's
        content, so the edit is genuinely lost, not merely regenerated.
    (b) `if original_hash and live.is_file()` skipped the guard entirely when
        the live file was GONE, so a proposal for a file deleted or renamed
        between preview and apply recreated it at the old path, silently
        undoing an intentional deletion.
    """

    def _indexed_folder(self, root: Path) -> Path:
        """A folder tidy will want to rebuild an index for, with one present."""
        for n, d in (("a", "2026-01-01"), ("b", "2026-01-02")):
            _w(root / "decisions" / f"{d}-{n}.md",
               f"---\ntype: decision\nstatus: accepted\ndate: {d}\n"
               f"tags:\n  - decision\n---\n\nBody {n}.\n")
        live = root / "decisions" / "_index.md"
        _w(live, "---\ntype: index\n---\n\n# Decisions\n")
        return live

    def test_edited_index_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = self._indexed_folder(root)
            cs = build_preview(root, build_vault_index(root), "t")
            self.assertIn("decisions/_index.md", cs["index_proposals"],
                          "fixture must actually produce an index proposal")
            write_preview_to_disk(root, cs)
            live.write_text(live.read_text() + "\nMy hand-written summary.\n")
            backup = apply_preview(root)
            self.assertIn("My hand-written summary.", live.read_text(),
                          "an index edited since preview must not be clobbered")
            note = backup / "SKIPPED-STALE.txt"
            self.assertTrue(note.is_file(), "the skip must be recorded")
            self.assertIn("decisions/_index.md", note.read_text())

    def test_unedited_index_still_rebuilds(self):
        # Guard against over-correction: the ordinary path must still apply.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = self._indexed_folder(root)
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            backup = apply_preview(root)
            self.assertEqual(live.read_text(),
                             cs["index_proposals"]["decisions/_index.md"]["proposed_content"],
                             "an untouched index must still be rebuilt")
            self.assertFalse((backup / "SKIPPED-STALE.txt").exists())

    def test_brand_new_index_is_still_created(self):
        # An index proposal for a folder that had none records no hash, and
        # must still be created rather than treated as stale.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n, d in (("a", "2026-01-01"), ("b", "2026-01-02")):
                _w(root / "decisions" / f"{d}-{n}.md",
                   f"---\ntype: decision\nstatus: accepted\ndate: {d}\n"
                   f"tags:\n  - decision\n---\n\nBody {n}.\n")
            cs = build_preview(root, build_vault_index(root), "t")
            self.assertFalse(
                cs["index_proposals"]["decisions/_index.md"]["had_existing"])
            write_preview_to_disk(root, cs)
            apply_preview(root)
            self.assertTrue((root / "decisions" / "_index.md").is_file(),
                            "a missing index must still be generated")

    def test_index_created_between_preview_and_apply_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n, d in (("a", "2026-01-01"), ("b", "2026-01-02")):
                _w(root / "decisions" / f"{d}-{n}.md",
                   f"---\ntype: decision\nstatus: accepted\ndate: {d}\n"
                   f"tags:\n  - decision\n---\n\nBody {n}.\n")
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            live = root / "decisions" / "_index.md"
            live.write_text("---\ntype: index\n---\n\n# Mine, written just now.\n")
            backup = apply_preview(root)
            self.assertIn("Mine, written just now.", live.read_text(),
                          "a file that appeared since preview is not ours to overwrite")
            self.assertIn("decisions/_index.md",
                          (backup / "SKIPPED-STALE.txt").read_text())

    def test_file_deleted_after_preview_is_not_resurrected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "decisions" / "2026-01-01-d.md"
            _w(live, "---\ntype: decision\nstatus: accepted\ndate: 2026-01-01\n"
                     "tags:\n  - decision\n  - ob/legacy\n---\n\nBody.\n")
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            live.unlink()
            backup = apply_preview(root)
            self.assertFalse(
                live.exists(),
                "a deletion between preview and apply is an intentional act")
            self.assertIn("decisions/2026-01-01-d.md",
                          (backup / "SKIPPED-STALE.txt").read_text())

    def test_file_renamed_after_preview_is_not_resurrected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "decisions" / "2026-01-01-d.md"
            _w(live, "---\ntype: decision\nstatus: accepted\ndate: 2026-01-01\n"
                     "tags:\n  - decision\n  - ob/legacy\n---\n\nBody.\n")
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            renamed = root / "decisions" / "2026-01-01-better-name.md"
            live.rename(renamed)
            apply_preview(root)
            self.assertFalse(live.exists(),
                             "the old path must not reappear beside the rename")
            self.assertTrue(renamed.is_file())

    def test_an_index_in_both_proposal_dicts_is_applied_once(self):
        """An `_index.md` that also needs a tag fix lands in file_proposals AND
        index_proposals, but `write_preview_to_disk` collapses both into one
        `files/<rel>`. Applying it twice reports the second pass as stale (a
        lie: nothing changed under us) and re-backs-up the already-tidied file
        over the only pre-change copy."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n, d in (("a", "2026-01-01"), ("b", "2026-01-02")):
                _w(root / "decisions" / f"{d}-{n}.md",
                   f"---\ntype: decision\nstatus: accepted\ndate: {d}\n"
                   f"tags:\n  - decision\n---\n\nBody {n}.\n")
            live = root / "decisions" / "_index.md"
            _w(live, "---\ntype: index\nupdated: 2020-01-01\n"
                     "tags:\n  - index\n  - ob/legacy\n---\n\n"
                     "# Decisions\n\n## Entries\n\n- [[stale-entry]]\n")
            cs = build_preview(root, build_vault_index(root), "t")
            self.assertIn("decisions/_index.md", cs["file_proposals"])
            self.assertIn("decisions/_index.md", cs["index_proposals"])
            write_preview_to_disk(root, cs)
            backup = apply_preview(root)
            self.assertFalse((backup / "SKIPPED-STALE.txt").exists(),
                             "nothing changed under us, so nothing is stale")
            legacy = backup / "decisions" / "_index.md.legacy"
            self.assertIn("ob/legacy", legacy.read_text(),
                          "the backup must hold the PRE-tidy index")
            self.assertIn("2026-01-02-b", live.read_text(),
                          "the rebuild must still land")


class TestPreviewApplyRoundTrip(unittest.TestCase):

    def test_full_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
                "---\ntype: project\nslug: t\nproject_type: coding\n"
                "tags:\n  - project\n  - ob/project\n---\n\n# T\n")
            _w(root / "_handoff.md", "---\ntype: handoff\n---\nbody")

            # Phase 1: preview
            self.assertEqual(detect_phase(root), "fresh")
            vault_idx = build_vault_index(root)
            cs = build_preview(root, vault_idx, project_slug="t")
            self.assertGreater(cs["summary"]["total_changes"], 0)
            write_preview_to_disk(root, cs)
            self.assertEqual(detect_phase(root), "preview")
            preview = root / PREVIEW_DIR_NAME
            self.assertTrue((preview / "summary.md").is_file())
            self.assertTrue((preview / "changes.json").is_file())
            self.assertTrue((preview / "files" / "brief.md").is_file())

            # Verify the proposed brief no longer has ob/project
            proposed_brief = (preview / "files" / "brief.md").read_text()
            self.assertNotIn("ob/project", proposed_brief)

            # Phase 2: apply
            backup = apply_preview(root)
            self.assertTrue(backup.is_dir())
            backup_brief = backup / "brief.md.legacy"
            self.assertTrue(backup_brief.is_file())
            # Original brief had ob/project — backup retains it
            self.assertIn("ob/project", backup_brief.read_text())
            # Live brief no longer has it
            live_brief = (root / "brief.md").read_text()
            self.assertNotIn("ob/project", live_brief)
            # Preview gone
            self.assertFalse((root / PREVIEW_DIR_NAME).exists())
            self.assertEqual(detect_phase(root), "applied")

    def test_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
                "---\ntype: project\nslug: t\nproject_type: coding\n"
                "tags:\n  - project\n---\n\n# T\n")
            _w(root / "_handoff.md", "---\ntype: handoff\n---\nbody")
            # First pass — clean already
            cs = build_preview(root, set(), project_slug="t")
            self.assertEqual(cs["summary"]["total_changes"], 0)


class TestTidyCost(unittest.TestCase):

    def _project(self, root: Path) -> None:
        _w(root / "brief.md",
            "---\ntype: project\nslug: t\nproject_type: coding\nstatus: active\n---\n\n# T\n")
        _w(root / "notes" / "big.md", "x" * 8000)

    def test_estimate_only_is_cost_only_and_stat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = tidy_cli(["detect", "--project-dir", str(root), "--estimate-only"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(set(payload), {"cost"})
            self.assertGreaterEqual(payload["cost"]["est_read_tokens"], 2000)

    def test_normal_detect_includes_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = tidy_cli(["detect", "--project-dir", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("cost", payload)
            self.assertEqual(payload["state"], "fresh")

    def test_preview_includes_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = tidy_cli(["preview", "--project-dir", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("cost", payload)
            self.assertGreaterEqual(payload["cost"]["est_read_tokens"], 2000)


from tidy import (
    _drop_frontmatter_keys,
    _rename_frontmatter_key,
    _set_frontmatter_scalar,
)


class TestSchemaPrimitives(unittest.TestCase):

    def test_drop_single_line_key(self):
        text = '---\ntype: note\nproject: "[[projects/x/brief|x]]"\ncreated: 2026-01-01\n---\nB\n'
        out = _drop_frontmatter_keys(text, {"project"})
        self.assertNotIn("project:", out)
        self.assertIn("type: note", out)
        self.assertIn("created: 2026-01-01", out)

    def test_drop_block_list_key_consumes_items(self):
        text = "---\ntype: handoff\nsession_id:\n  - aaa\n  - bbb\nupdated: 2026-01-01\n---\nB\n"
        out = _drop_frontmatter_keys(text, {"session_id"})
        self.assertNotIn("session_id", out)
        self.assertNotIn("aaa", out)
        self.assertIn("updated: 2026-01-01", out)

    def test_drop_nested_map_key_consumes_children(self):
        text = "---\ntype: note\nmetadata:\n  node_type: memory\n  foo: bar\ntags:\n  - note\n---\nB\n"
        out = _drop_frontmatter_keys(text, {"metadata"})
        self.assertNotIn("metadata", out)
        self.assertNotIn("node_type", out)
        self.assertIn("tags:", out)
        self.assertIn("  - note", out)

    def test_drop_never_touches_body(self):
        text = "---\ntype: note\nfoo: bar\n---\nbody keeps foo: bar mention\n"
        out = _drop_frontmatter_keys(text, {"foo"})
        self.assertIn("body keeps foo: bar mention", out)

    def test_drop_quoted_colon_sibling_untouched(self):
        text = '---\ntype: doc\ntitle: "A: B"\nfoo: bar\n---\nB\n'
        out = _drop_frontmatter_keys(text, {"foo"})
        self.assertIn('title: "A: B"', out)
        self.assertNotIn("foo: bar", out)

    def test_rename_preserves_value(self):
        text = "---\nnode_type: memory\ntags:\n  - note\n---\nB\n"
        out = _rename_frontmatter_key(text, "node_type", "type")
        self.assertIn("type: memory", out)
        self.assertNotIn("node_type", out)

    def test_set_scalar_preserves_trailing_comment(self):
        text = "---\ntype: decision\nstatus: accepted   # wild\ndate: 2026-01-01\n---\nB\n"
        out = _set_frontmatter_scalar(text, "status", "active")
        self.assertIn("status: active", out)
        self.assertIn("# wild", out)


_SCHEMA_NOTE_DRIFTED = (
    '---\ntype: note\nproject: "[[projects/t/brief|t]]"\noriginSessionId: abc-123\n'
    "created: 2026-01-01\nupdated: 2026-01-01\ntags:\n  - note\n---\nN\n")


class TestSchemaPhase(unittest.TestCase):

    def _preview(self, root: Path):
        return build_preview(root, build_vault_index(root), "t")

    def test_strip_project_and_migrate_origin_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED)
            cs = self._preview(root)
            prop = cs["file_proposals"]["notes/n.md"]["proposed_content"]
            self.assertNotIn("project:", prop)
            self.assertNotIn("originSessionId", prop)
            self.assertIn("source_session: abc-123", prop)
            self.assertEqual(cs["schema_actions"]["notes/n.md"]["dropped"], ["project"])
            self.assertIn("originSessionId -> source_session",
                          cs["schema_actions"]["notes/n.md"]["renamed"])

    def test_uncorroborated_type_is_reported_not_stripped(self):
        # A Claude Code auto-memory file flattened by an external editor lands
        # with `type: project` and nothing else a project brief has. Treating
        # that declaration as true made tidy strip `name:`/`description:` -
        # the two fields the memory system reads for relevance - off a file
        # that was never a project brief. Misclassified is not drifted.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "memory" / "prefers-agents-md.md",
               "---\nname: prefers-agents-md\n"
               "description: Canonical repo context lives in AGENTS.md\n"
               "type: project\n---\n\nBody.\n")
            cs = self._preview(root)
            self.assertNotIn("memory/prefers-agents-md.md", cs["file_proposals"])
            act = cs["schema_actions"]["memory/prefers-agents-md.md"]
            self.assertNotIn("dropped", act)
            self.assertIn("project", act["unverified_type"])

    def test_a_real_brief_with_drift_is_still_stripped(self):
        # The guard must cost nothing on a file that genuinely IS its declared
        # type: corroborated by the required fields, so the strip proceeds.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
               "---\ntype: project\nproject_type: coding\nslug: t\n"
               "aliases:\n  - T\nstatus: active\ncreated: 2026-01-01\n"
               "updated: 2026-01-02\nbogus: nope\ntags:\n  - project\n---\n\nB\n")
            cs = self._preview(root)
            self.assertEqual(cs["schema_actions"]["brief.md"]["dropped"], ["bogus"])

    def test_origin_session_dropped_when_source_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED.replace(
                "originSessionId: abc-123\n",
                "originSessionId: abc-123\nsource_session: def-456\n"))
            cs = self._preview(root)
            prop = cs["file_proposals"]["notes/n.md"]["proposed_content"]
            self.assertNotIn("originSessionId", prop)
            self.assertIn("source_session: def-456", prop)

    def test_node_type_renamed_when_type_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "m.md", "---\nnode_type: memory\ntags:\n  - note\n---\nM\n")
            cs = self._preview(root)
            prop = cs["file_proposals"]["notes/m.md"]["proposed_content"]
            self.assertIn("type: memory", prop)
            self.assertNotIn("node_type", prop)

    def test_node_type_dropped_when_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED.replace(
                "type: note\n", "type: note\nnode_type: note\n"))
            cs = self._preview(root)
            prop = cs["file_proposals"]["notes/n.md"]["proposed_content"]
            self.assertIn("type: note", prop)
            self.assertNotIn("node_type", prop)

    def test_decision_alias_status_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "decisions" / "2026-01-01-d.md",
               "---\ntype: decision\nstatus: accepted\ndate: 2026-01-01\ntags:\n  - decision\n---\nD\n")
            cs = self._preview(root)
            prop = cs["file_proposals"]["decisions/2026-01-01-d.md"]["proposed_content"]
            self.assertIn("status: active", prop)

    def test_task_alias_status_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "tasks" / "t.md", "---\ntype: task\nstatus: wip\ntags:\n  - task\n---\nT\n")
            cs = self._preview(root)
            self.assertNotIn("tasks/t.md", cs["file_proposals"])

    def test_required_keys_never_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "sessions" / "2026-01-01.md",
               "---\ntype: session\ndate: 2026-01-01\nstarted: \"09:00\"\n"
               "session_id: []\nfoo: bar\ntags:\n  - session\n---\nS\n")
            cs = self._preview(root)
            prop = cs["file_proposals"]["sessions/2026-01-01.md"]["proposed_content"]
            self.assertNotIn("foo: bar", prop)
            self.assertIn("session_id: []", prop)
            self.assertIn("date: 2026-01-01", prop)

    def test_parse_error_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "broken.md", "---\ntype: note\nno closing fence\n")
            cs = self._preview(root)
            self.assertNotIn("notes/broken.md", cs["file_proposals"])

    def test_updated_bumped_on_schema_strip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED)
            cs = self._preview(root)
            prop = cs["file_proposals"]["notes/n.md"]["proposed_content"]
            self.assertNotIn("updated: 2026-01-01", prop)

    def test_summary_has_schema_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED)
            cs = self._preview(root)
            preview = write_preview_to_disk(root, cs)
            summary = (preview / "summary.md").read_text()
            self.assertIn("## Schema", summary)
            self.assertIn("notes/n.md", summary)

    def test_schema_apply_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED)
            # session_id is LEGAL on handoffs (the sync mirror preserves it);
            # use a genuinely unknown block-list key so this still exercises
            # multi-file idempotency, and assert session_id survives.
            _w(root / "_handoff.md",
               "---\ntype: handoff\nsession_id:\n  - aaa\nbogus_key:\n  - x\n"
               "updated: 2026-01-01\nsource: sync\ntags:\n  - handoff\n---\nH\n")
            cs = self._preview(root)
            self.assertIn("_handoff.md", cs["file_proposals"])
            write_preview_to_disk(root, cs)
            apply_preview(root)
            handoff_after = (root / "_handoff.md").read_text()
            self.assertIn("session_id:", handoff_after)
            self.assertNotIn("bogus_key", handoff_after)
            cs2 = self._preview(root)
            self.assertEqual(cs2["schema_actions"], {})
            self.assertNotIn("notes/n.md", cs2["file_proposals"])
            self.assertNotIn("_handoff.md", cs2["file_proposals"])


if __name__ == "__main__":
    unittest.main()
