"""Tests for adjudant/scripts/clean.py — the surface sweep.

The deep pass that was ramasse lives in test_clean_deep.py."""

import contextlib
import io
import json
import os
import shutil
import re
import tempfile
import unittest
from pathlib import Path

from clean import (
    apply_preview,
    apply_references_split,
    backup_root,
    build_preview,
    cli_main as clean_cli,
    detect_phase,
    fix_wikilink_form,
    plan_references_split,
    preview_dir,
    write_preview_to_disk,
    _bump_updated_field,
)
from _vault_walk import build_vault_index, extract_wikilinks, resolve_wikilink

_MODULE_TMP = None
_OLD_TMPDIR = None


def setUpModule():
    """Pin $TMPDIR for this module.

    Since v3 clean's preview and backup live under $TMPDIR rather than in
    the vault, so an un-pinned run would leave rotated backup dirs behind
    in the developer's real temp dir, run after run.
    """
    global _MODULE_TMP, _OLD_TMPDIR
    _OLD_TMPDIR = os.environ.get("TMPDIR")
    _MODULE_TMP = tempfile.mkdtemp(prefix="adjudant-test-clean-")
    os.environ["TMPDIR"] = _MODULE_TMP


def tearDownModule():
    if _OLD_TMPDIR is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = _OLD_TMPDIR
    if _MODULE_TMP:
        shutil.rmtree(_MODULE_TMP, ignore_errors=True)


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
            preview_dir(Path(tmp)).mkdir(parents=True)
            self.assertEqual(detect_phase(Path(tmp)), "preview")

    def test_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            (backup_root(Path(tmp)) / "20260526T120000Z").mkdir(parents=True)
            (backup_root(Path(tmp)) / "20260526T120000Z" / "x.legacy").write_text("old")
            self.assertEqual(detect_phase(Path(tmp)), "applied")


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


class TestRewrittenLinksAlwaysResolve(unittest.TestCase):
    """The invariant that makes the rewrite safe to run unattended.

    Every wikilink `fix_wikilink_form` produces must resolve in the index it
    was handed. Before v3 it did not: a href naming a sibling by filename
    became `[[sibling]]`, a bare stem the index matched against any file of
    that name anywhere in the vault. The old tests all passed, because every
    fixture put its files at the index root, where a filename IS the path.
    """

    def _vault(self, tmp: Path) -> Path:
        vault = tmp / "v"
        notes = vault / "projects" / "active" / "demo" / "notes"
        notes.mkdir(parents=True)
        (notes / "cold-cache.md").write_text("---\ntype: note\n---\n# c")
        return vault

    def test_every_link_it_writes_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            idx = build_vault_index(vault)
            body = (
                "sibling: [t](cold-cache.md)\n"
                "zone-less: [t](demo/notes/cold-cache.md)\n"
                "vault path: [t](projects/active/demo/notes/cold-cache.md)\n"
                "absent: [t](no-such-note.md)\n"
            )
            new, _ = fix_wikilink_form(body, idx)
            written = re.findall(r"\[\[([^\]|#]+)", new)
            self.assertTrue(written, "the rewriter produced no links at all")
            for target in written:
                self.assertTrue(resolve_wikilink(target, idx),
                                f"rewrote a link to [[{target}]], which "
                                "resolves to nothing")

    def test_a_sibling_filename_is_left_as_a_markdown_link(self):
        # It still works in Obsidian. Turning it into [[cold-cache]] would
        # have pointed at whichever project's cold-cache.md sorted first.
        with tempfile.TemporaryDirectory() as tmp:
            vault = self._vault(Path(tmp))
            idx = build_vault_index(vault)
            new, count = fix_wikilink_form("see [t](cold-cache.md)", idx)
            self.assertEqual(count, 0)
            self.assertEqual(new, "see [t](cold-cache.md)")


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
# build_preview end-to-end
# ============================================================


class TestBuildPreview(unittest.TestCase):

    def test_dirty_project_produces_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
                "---\ntype: project\ncreated: 2026-05-01\nupdated: 2026-05-01\n"
                "verified: 2026-05-01\nverified_by: read\n"
                "slug: t\nproject_type: coding\n"
                "tags:\n  - project\n  - ob/project\n---\n\n# T\n")
            # Two decisions, no index
            _w(root / "decisions" / "2026-05-26-a.md", "---\ntype: decision\n---\n")
            _w(root / "decisions" / "2026-05-25-b.md", "---\ntype: decision\n---\n")
            # File with a markdown-style link to a vault file
            _w(root / "target.md", "---\ntype: note\n---\n")
            _w(root / "src.md",
                "---\ntype: note\ncreated: 2026-05-01\nupdated: 2026-05-01\n"
                "tags:\n  - ob/note\n---\n\nSee [target](target.md).")
            vault_index = build_vault_index(root)
            cs = build_preview(root, vault_index, project_slug="t")
            # decisions/ has two entries and no index. clean says nothing
            # about that: folder indexes are retired, and prune_index_files
            # deletes any that appear. Both the old rebuild feature and the
            # gap report that replaced it are gone.
            self.assertNotIn("index_proposals", cs)
            self.assertNotIn("index_gaps", cs)
            # Should propose changes to src.md (unknown tags: + wikilink)
            self.assertIn("src.md", cs["file_proposals"])
            # Should propose changes to brief.md (unknown fields stripped)
            self.assertIn("brief.md", cs["file_proposals"])
            # tags: is no longer a rule of its own — it strips because no
            # template declares it, the same way slug: and project_type: do.
            self.assertNotIn("tags:", cs["file_proposals"]["brief.md"]["proposed_content"])

    def test_clean_project_no_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
                "---\ntype: project\ncreated: 2026-05-01\nupdated: 2026-05-01\n"
                "verified: 2026-05-01\nverified_by: read\n---\n\n# T\n")
            _w(root / "_handoff.md",
               "---\ntype: handoff\ncreated: 2026-05-01\nupdated: 2026-05-01\n---\nbody")
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
        """A project with one file clean will want to change."""
        p = root / "decisions" / "2026-01-01-d.md"
        _w(p, "---\ntype: decision\nstatus: accepted\n"
              "created: 2026-01-01\nupdated: 2026-01-01\ndate: 2026-01-01\n"
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
            _w(other, "---\ntype: decision\nstatus: locked\n"
                      "created: 2026-01-02\nupdated: 2026-01-02\n---\n\nBody.\n")
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
                          "backup must hold the PRE-clean content")
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
            preview = preview_dir(root)
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

    (a) The guard covered `file_proposals` only; `index_proposals` carried no
        `original_hash`, so a folder-level `_index.md` rebuild computed FROM
        stale content could silently overwrite a live edit. Task 8 deleted
        `index_proposals` and the whole in-place rebuild it guarded — the
        hole and the code path it lived in went together, so its two tests
        (`test_edited_index_is_not_silently_overwritten`,
        `test_unedited_index_still_rebuilds`) went with them, along with
        `test_an_index_in_both_proposal_dicts_is_applied_once`: with only one
        proposal dict left, a path landing in two of them is not a case that
        exists any more.
    (b) `if original_hash and live.is_file()` skipped the guard entirely when
        the live file was GONE, so a proposal for a file deleted or renamed
        between preview and apply recreated it at the old path, silently
        undoing an intentional deletion. This one still applies to any
        `file_proposals` entry, `_index.md` included.
    """

    def test_file_deleted_after_preview_is_not_resurrected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "decisions" / "2026-01-01-d.md"
            _w(live, "---\ntype: decision\nstatus: accepted\n"
                     "created: 2026-01-01\nupdated: 2026-01-01\ndate: 2026-01-01\n"
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
            _w(live, "---\ntype: decision\nstatus: accepted\n"
                     "created: 2026-01-01\nupdated: 2026-01-01\ndate: 2026-01-01\n"
                     "tags:\n  - decision\n  - ob/legacy\n---\n\nBody.\n")
            cs = build_preview(root, build_vault_index(root), "t")
            write_preview_to_disk(root, cs)
            renamed = root / "decisions" / "2026-01-01-better-name.md"
            live.rename(renamed)
            apply_preview(root)
            self.assertFalse(live.exists(),
                             "the old path must not reappear beside the rename")
            self.assertTrue(renamed.is_file())

    def test_a_retired_index_is_removed_rather_than_repaired(self):
        # SUPERSEDED test_an_index_file_with_drift_is_schema_repaired_like_any_other.
        # A folder index is retired now, so repairing its schema and then
        # deleting it in the same run would back up a version that never
        # existed before the run. It is skipped for repair and removed, with
        # the pre-clean file in the backup.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "decisions" / "_index.md"
            _w(live, "---\ntype: index\ncreated: 2020-01-01\nupdated: 2020-01-01\n"
                     "tags:\n  - index\n  - ob/legacy\n---\n\n"
                     "# Decisions\n\n## Entries\n\n- [[stale-entry]]\n")
            cs = build_preview(root, build_vault_index(root), "t")
            self.assertNotIn("decisions/_index.md", cs["file_proposals"],
                             "a file being removed is not repaired first")
            self.assertIn("decisions/_index.md", cs["retired_indexes"])
            write_preview_to_disk(root, cs)
            backup = apply_preview(root)
            self.assertFalse(live.exists(), "the retired index is gone")
            legacy = backup / "decisions" / "_index.md.legacy"
            self.assertIn("ob/legacy", legacy.read_text(),
                          "the backup must hold the file exactly as it was")

    def test_full_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "brief.md",
                "---\ntype: project\ncreated: 2026-05-01\nupdated: 2026-05-01\n"
                "verified: 2026-05-01\nverified_by: read\n"
                "slug: t\nproject_type: coding\n"
                "tags:\n  - project\n  - ob/project\n---\n\n# T\n")
            _w(root / "_handoff.md",
               "---\ntype: handoff\ncreated: 2026-05-01\nupdated: 2026-05-01\n---\nbody")

            # Phase 1: preview
            self.assertEqual(detect_phase(root), "fresh")
            vault_idx = build_vault_index(root)
            cs = build_preview(root, vault_idx, project_slug="t")
            self.assertGreater(cs["summary"]["total_changes"], 0)
            write_preview_to_disk(root, cs)
            self.assertEqual(detect_phase(root), "preview")
            preview = preview_dir(root)
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
            self.assertFalse(preview_dir(root).exists())
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


class TestCleanCost(unittest.TestCase):

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
                rc = clean_cli(["detect", "--project-dir", str(root), "--estimate-only"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            # scope rides along since the merge: null when unscoped, so a
            # reader of the estimate always knows what it covered.
            self.assertEqual(set(payload), {"scope", "cost"})
            self.assertGreaterEqual(payload["cost"]["est_read_tokens"], 2000)

    def test_normal_detect_includes_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = clean_cli(["detect", "--project-dir", str(root)])
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
                rc = clean_cli(["preview", "--project-dir", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("cost", payload)
            self.assertGreaterEqual(payload["cost"]["est_read_tokens"], 2000)


from clean import (
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
    "created: 2026-01-01\nupdated: 2026-01-01\n---\nN\n")


class TestSchemaPhase(unittest.TestCase):

    def _preview(self, root: Path):
        return build_preview(root, build_vault_index(root), "t")

    def test_strip_project_and_drop_origin_session(self):
        # v3: source_session is not a field on any kind, so the legacy key has
        # nowhere to migrate to and drops with every other unknown field.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED)
            cs = self._preview(root)
            prop = cs["file_proposals"]["notes/n.md"]["proposed_content"]
            self.assertNotIn("project:", prop)
            self.assertNotIn("originSessionId", prop)
            self.assertNotIn("source_session", prop)
            self.assertEqual(cs["schema_actions"]["notes/n.md"]["dropped"],
                             ["originSessionId", "project"])
            self.assertNotIn("renamed", cs["schema_actions"]["notes/n.md"])

    def test_uncorroborated_type_is_reported_not_stripped(self):
        # A Claude Code auto-memory file flattened by an external editor lands
        # with `type: project` and nothing else a project brief has. Treating
        # that declaration as true made clean strip `name:`/`description:` -
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
               "---\ntype: project\ncreated: 2026-01-01\nupdated: 2026-01-02\n"
               "verified: 2026-01-02\nverified_by: read\nbogus: nope\n---\n\nB\n")
            cs = self._preview(root)
            self.assertEqual(cs["schema_actions"]["brief.md"]["dropped"], ["bogus"])

    def test_both_session_stamps_drop_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "notes" / "n.md", _SCHEMA_NOTE_DRIFTED.replace(
                "originSessionId: abc-123\n",
                "originSessionId: abc-123\nsource_session: def-456\n"))
            cs = self._preview(root)
            prop = cs["file_proposals"]["notes/n.md"]["proposed_content"]
            self.assertNotIn("originSessionId", prop)
            self.assertNotIn("source_session", prop)

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
               "---\ntype: decision\nstatus: accepted\ncreated: 2026-01-01\n"
               "updated: 2026-01-01\n---\nD\n")
            cs = self._preview(root)
            prop = cs["file_proposals"]["decisions/2026-01-01-d.md"]["proposed_content"]
            self.assertIn("status: active", prop)

    def test_task_alias_status_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "tasks" / "t.md",
               "---\ntype: task\nstatus: wip\ncreated: 2026-01-01\n"
               "updated: 2026-01-01\n---\nT\n")
            cs = self._preview(root)
            self.assertNotIn("tasks/t.md", cs["file_proposals"])

    def test_required_keys_never_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _w(root / "sessions" / "2026-01-01.md",
               "---\ntype: session\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
               "foo: bar\n---\nS\n")
            cs = self._preview(root)
            prop = cs["file_proposals"]["sessions/2026-01-01.md"]["proposed_content"]
            self.assertNotIn("foo: bar", prop)
            self.assertIn("created: 2026-01-01", prop)
            self.assertIn("updated: 2026-01-01", prop)

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
            # A second file so this exercises multi-file idempotency. The v3
            # handoff is type/created/updated and nothing else, so the block
            # key is drift like any other.
            _w(root / "_handoff.md",
               "---\ntype: handoff\nbogus_key:\n  - x\n"
               "created: 2026-01-01\nupdated: 2026-01-01\n---\nH\n")
            cs = self._preview(root)
            self.assertIn("_handoff.md", cs["file_proposals"])
            write_preview_to_disk(root, cs)
            apply_preview(root)
            handoff_after = (root / "_handoff.md").read_text()
            self.assertNotIn("bogus_key", handoff_after)
            cs2 = self._preview(root)
            self.assertEqual(cs2["schema_actions"], {})
            self.assertNotIn("notes/n.md", cs2["file_proposals"])
            self.assertNotIn("_handoff.md", cs2["file_proposals"])


class TestScratchIsOutsideTheVault(unittest.TestCase):
    """The defect this whole plan exists for: the cleanup verb wrote its
    working copies into the vault it was cleaning, and nothing ever reaped
    them."""

    def _isolate_scratch(self, tmp: Path) -> None:
        """Point $TMPDIR at this test's own temp dir.

        _scratch keys on the project *name*, so every test whose project is
        called "demo" shares one scratch subtree, and nothing reaps it between
        runs: a backup left by an earlier test would make `detect_phase` report
        "applied" on a freshly built project. Pinning $TMPDIR here makes the
        scratch per-test and lets the temp dir take it away.
        """
        old = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = str(tmp)

        def _restore():
            if old is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = old

        self.addCleanup(_restore)

    def _project(self, tmp: Path) -> Path:
        self._isolate_scratch(tmp)
        project = tmp / "vault" / "projects" / "demo"
        (project / "notes").mkdir(parents=True)
        _w(project / "notes" / "a.md",
           "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\ntags:\n  - ob/note\n---\n\n# A\n")
        _w(project / "notes" / "b.md",
           "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\ntags:\n  - ob/note\n---\n\n# B\n")
        return project

    def _preview(self, project: Path) -> dict:
        return build_preview(project, build_vault_index(project), "demo")

    def test_preview_writes_nothing_into_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            before = {p for p in project.rglob("*")}
            change_set = self._preview(project)
            write_preview_to_disk(project, change_set)
            after = {p for p in project.rglob("*")}
            self.assertEqual(before, after,
                             "clean preview created files inside the vault project")

    def test_apply_writes_no_backup_into_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            change_set = self._preview(project)
            write_preview_to_disk(project, change_set)
            apply_preview(project)
            stray = [p for p in project.rglob(".adjudant-*")]
            self.assertEqual(stray, [],
                             f"clean apply left scratch in the vault: {stray}")

    def test_detect_phase_reads_the_scratch_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            self.assertEqual(detect_phase(project), "fresh")
            change_set = self._preview(project)
            write_preview_to_disk(project, change_set)
            self.assertEqual(detect_phase(project), "preview")
            apply_preview(project)
            self.assertEqual(detect_phase(project), "applied")

    def test_backups_rotate(self):
        from _scratch import BACKUP_KEEP, scratch_dir
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            for i in range(BACKUP_KEEP + 3):
                _w(project / "notes" / f"n{i}.md",
                   "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\ntags:\n  - ob/note\n---\n\n# N\n")
                change_set = self._preview(project)
                write_preview_to_disk(project, change_set)
                apply_preview(project)
            root = scratch_dir(project, "clean-backup")
            kept = [d for d in root.iterdir() if d.is_dir()]
            self.assertLessEqual(len(kept), BACKUP_KEEP)


class TestReferencesSplit(unittest.TestCase):
    """references/ held api pages, schemas, specs, sections, component
    inventories and imported wiki pages at once. Each now has a folder that
    names it, and clean offers the move rather than making it."""

    def _project(self, tmp: Path) -> Path:
        pdir = tmp / "vault" / "projects" / "active" / "demo"
        (pdir / "references").mkdir(parents=True)
        _w(pdir / "brief.md",
           "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n# D\n")
        for name, kind in (("contacts.md", "api"), ("ep-object.md", "schema"),
                           ("spec-018-page-spinup.md", "spec"),
                           ("button.md", "component"),
                           ("wiki-runbook.md", "source")):
            _w(pdir / "references" / name,
               f"---\ntype: {kind}\nupdated: 2026-09-01\n---\n\n# {name}\n")
        return pdir

    def test_the_plan_routes_each_file_by_its_own_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._project(Path(tmp))
            plan = {m["from"]: m["to"] for m in plan_references_split(pdir)}
            self.assertEqual(plan["references/contacts.md"], "api/contacts.md")
            self.assertEqual(plan["references/ep-object.md"],
                             "schemas/ep-object.md")
            self.assertEqual(plan["references/spec-018-page-spinup.md"],
                             "specs/spec-018-page-spinup.md")
            self.assertEqual(plan["references/button.md"],
                             "components/button.md")
            self.assertEqual(plan["references/wiki-runbook.md"],
                             "sources/wiki-runbook.md")

    def test_the_plan_moves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._project(Path(tmp))
            before = sorted(str(p) for p in pdir.rglob("*"))
            plan_references_split(pdir)
            self.assertEqual(sorted(str(p) for p in pdir.rglob("*")), before)

    def test_a_file_with_no_home_is_left_where_it_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._project(Path(tmp))
            # `note` has a folder of its own (KIND_FOLDER["note"] == "notes"),
            # so it is not the "no home" case. `project`, `handoff` and
            # `index` are the three kinds with no folder (KIND_FOLDER maps
            # them to ""), and `project`/`index` already collide with a file
            # `place()` itself would refuse to put in references/. `handoff`
            # is the one that plausibly turns up there by hand and has to be
            # left alone rather than routed to a bare project-root file.
            _w(pdir / "references" / "loose.md",
               "---\ntype: handoff\nupdated: 2026-09-01\n---\n\n# Loose\n")
            froms = [m["from"] for m in plan_references_split(pdir)]
            self.assertNotIn("references/loose.md", froms)

    def test_apply_moves_the_files_and_repoints_the_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = self._project(root)
            _w(pdir / "notes" / "uses.md",
               "---\ntype: note\nupdated: 2026-09-01\n---\n\n"
               "See [[demo/references/contacts|contacts]] and "
               "[[demo/references/ep-object]].\n")
            moves = plan_references_split(pdir)
            receipts = apply_references_split(pdir, moves)
            self.assertTrue((pdir / "api" / "contacts.md").is_file())
            self.assertFalse((pdir / "references" / "contacts.md").exists())
            body = (pdir / "notes" / "uses.md").read_text()
            self.assertIn("[[demo/api/contacts|contacts]]", body)
            self.assertIn("[[demo/schemas/ep-object]]", body)
            self.assertNotIn("demo/references/", body)
            repointed = {r["from"]: r["links_repointed"] for r in receipts}
            self.assertEqual(repointed["references/contacts.md"], 1)

    def test_every_link_still_resolves_after_the_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            pdir = self._project(root)
            _w(pdir / "notes" / "uses.md",
               "---\ntype: note\nupdated: 2026-09-01\n---\n\n"
               "See [[demo/references/contacts|contacts]].\n")
            apply_references_split(pdir, plan_references_split(pdir))
            idx = build_vault_index(vault)
            for wl in extract_wikilinks((pdir / "notes" / "uses.md").read_text()):
                self.assertTrue(resolve_wikilink(wl.target, idx), wl.target)

    def test_the_split_creates_no_new_vault_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = self._project(Path(tmp))
            before = len([p for p in pdir.rglob("*") if p.is_file()])
            apply_references_split(pdir, plan_references_split(pdir))
            after = len([p for p in pdir.rglob("*") if p.is_file()])
            self.assertEqual(after, before, "clean must never add a vault file")

    def test_apply_backs_up_every_file_it_touches_before_writing(self):
        # apply_preview's own contract is five gates, one of them "the
        # pre-change copy must land in a backup dir" - every other write path
        # in clean honours it. A move-and-rewrite with no backup would be the
        # one exception, and the one time recovering from a bad rewrite would
        # matter most (a moved file plus every note that linked to it).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = self._project(root)
            _w(pdir / "notes" / "uses.md",
               "---\ntype: note\nupdated: 2026-09-01\n---\n\n"
               "See [[demo/references/contacts|contacts]].\n")
            original_contact = (pdir / "references" / "contacts.md").read_text()
            original_uses = (pdir / "notes" / "uses.md").read_text()
            apply_references_split(pdir, plan_references_split(pdir))
            legacies = list(backup_root(pdir).rglob("*.legacy"))
            by_name = {p.name: p for p in legacies}
            self.assertIn("contacts.md.legacy", by_name,
                         "the moved file has no recoverable pre-image")
            self.assertIn("uses.md.legacy", by_name,
                         "the repointed note has no recoverable pre-image")
            self.assertEqual(by_name["contacts.md.legacy"].read_text(),
                             original_contact)
            self.assertEqual(by_name["uses.md.legacy"].read_text(),
                             original_uses)

    def test_build_preview_offers_it_and_apply_preview_applies_it(self):
        # Step 4 wiring: no test in the plan exercises build_preview/
        # apply_preview at all for this feature, and it is the one write path
        # in the file with no dedicated coverage of its own.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = self._project(root)
            cs = build_preview(pdir, build_vault_index(root / "vault"), "demo")
            self.assertEqual(
                {m["from"] for m in cs["references_split"]},
                {"references/contacts.md", "references/ep-object.md",
                 "references/spec-018-page-spinup.md", "references/button.md",
                 "references/wiki-runbook.md"})
            write_preview_to_disk(pdir, cs)
            apply_preview(pdir)
            self.assertTrue((pdir / "api" / "contacts.md").is_file())
            self.assertFalse((pdir / "references").exists())

    def test_an_empty_split_is_absent_from_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "vault" / "projects" / "active" / "demo"
            _w(pdir / "brief.md",
               "---\ntype: project\nupdated: 2026-09-01\nverified: 2026-09-01\n---\n\n# D\n")
            cs = build_preview(pdir, build_vault_index(root / "vault"), "demo")
            self.assertEqual(cs["references_split"], [])
            write_preview_to_disk(pdir, cs)
            summary = (preview_dir(pdir) / "summary.md").read_text()
            self.assertNotIn("references", summary.lower())

    def test_a_file_touched_by_both_passes_still_recovers_to_its_true_original(self):
        # A note that BOTH file_proposals rewrites (a schema repair, here)
        # AND the split repoints (it links to a moved reference) shares one
        # backup path between the two passes. The backup must hold the note's
        # state before EITHER write, never the state after only the first.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = self._project(root)
            _w(pdir / "notes" / "uses.md",
               "---\nnode_type: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
               "---\n\nSee [[demo/references/contacts|contacts]].\n")
            original_uses = (pdir / "notes" / "uses.md").read_text()

            cs = build_preview(pdir, build_vault_index(root / "vault"), "demo")
            write_preview_to_disk(pdir, cs)
            apply_preview(pdir)

            live = (pdir / "notes" / "uses.md").read_text()
            self.assertIn("type: note", live)
            self.assertNotIn("node_type:", live)
            self.assertIn("[[demo/api/contacts|contacts]]", live)

            legacies = list(backup_root(pdir).rglob("uses.md.legacy"))
            self.assertEqual(len(legacies), 1)
            self.assertEqual(
                legacies[0].read_text(), original_uses,
                "the backup must be the note's state before EITHER write, "
                "not the state after the first one")


if __name__ == "__main__":
    unittest.main()
