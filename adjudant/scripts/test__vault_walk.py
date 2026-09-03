"""Tests for adjudant/scripts/_vault_walk.py."""

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from _vault_walk import (
    Frontmatter,
    atomic_write_text,
    file_lock,
    lock_path_for,
    Wikilink,
    ProjectContext,
    parse_frontmatter,
    extract_wikilinks,
    extract_inline_tags,
    extract_markdown_md_links,
    walk_project,
    build_vault_index,
    resolve_wikilink,
    parse_breadcrumb,
    resolve_vault,
    resolve_project_from_cwd,
    smart_project_dir,
    VaultUnresolvableError,
)


# ============================================================
# parse_frontmatter
# ============================================================


class TestParseFrontmatter(unittest.TestCase):

    def test_simple_frontmatter(self):
        text = "---\ntype: note\ntitle: Hello\n---\n\nBody here."
        fm, body = parse_frontmatter(text)
        self.assertTrue(fm.has_block)
        self.assertEqual(fm.fields["type"], "note")
        self.assertEqual(fm.fields["title"], "Hello")
        self.assertEqual(body, "\nBody here.")
        self.assertIsNone(fm.parse_error)

    def test_no_frontmatter(self):
        text = "Just body, no frontmatter."
        fm, body = parse_frontmatter(text)
        self.assertFalse(fm.has_block)
        self.assertEqual(fm.fields, {})
        self.assertEqual(body, text)

    def test_missing_closing_delimiter(self):
        text = "---\ntype: note\nno closing delim follows"
        fm, body = parse_frontmatter(text)
        self.assertFalse(fm.has_block)
        self.assertIsNotNone(fm.parse_error)

    def test_quoted_value(self):
        text = '---\ntitle: "Hello: with colon"\nproject: \'simple-quoted\'\n---\n'
        fm, _ = parse_frontmatter(text)
        self.assertEqual(fm.fields["title"], "Hello: with colon")
        self.assertEqual(fm.fields["project"], "simple-quoted")

    def test_null_value_preserved_as_string(self):
        # Per vault-standards §1: `null` is drift (should omit the key).
        # The parser preserves the literal so drift detection can flag it.
        text = "---\ncodename: null\nstatus: active\n---\n"
        fm, _ = parse_frontmatter(text)
        self.assertEqual(fm.fields["codename"], "null")
        self.assertEqual(fm.fields["status"], "active")

    def test_list_value(self):
        text = "---\ntags:\n  - note\n  - decision\n  - ob/doc\n---\n"
        fm, _ = parse_frontmatter(text)
        self.assertEqual(fm.fields["tags"], ["note", "decision", "ob/doc"])

    def test_empty_value_becomes_None(self):
        text = "---\ncodename:\nstatus: active\n---\n"
        fm, _ = parse_frontmatter(text)
        self.assertIsNone(fm.fields["codename"])

    def test_comment_skipped(self):
        text = "---\n# this is a comment\ntype: note\n---\n"
        fm, _ = parse_frontmatter(text)
        self.assertEqual(fm.fields, {"type": "note"})

    def test_piped_wikilink_value_kept_raw(self):
        text = '---\nproject: "[[projects/acme-web/brief|acme-web]]"\n---\n'
        fm, _ = parse_frontmatter(text)
        self.assertEqual(fm.fields["project"], "[[projects/acme-web/brief|acme-web]]")


# ============================================================
# extract_wikilinks
# ============================================================


class TestExtractWikilinks(unittest.TestCase):

    def test_simple(self):
        body = "Refer to [[my-note]] for details."
        links = extract_wikilinks(body)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target, "my-note")
        self.assertIsNone(links[0].alias)

    def test_with_alias(self):
        body = "See [[my-note|the note]]."
        links = extract_wikilinks(body)
        self.assertEqual(links[0].target, "my-note")
        self.assertEqual(links[0].alias, "the note")

    def test_escaped_pipe_in_table(self):
        # `[[README\|README]]` is an Obsidian-table escape: target=README, alias=README
        body = "| [[README\\|README]] | description |"
        links = extract_wikilinks(body)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target, "README")
        self.assertEqual(links[0].alias, "README")

    def test_heading_anchor(self):
        body = "Jump to [[note#Section Two]]."
        links = extract_wikilinks(body)
        self.assertEqual(links[0].target, "note")
        self.assertEqual(links[0].heading, "Section Two")

    def test_inside_fenced_code_block_skipped(self):
        body = (
            "Real [[link-one]]\n"
            "```python\n"
            "x = [[fake-link]]\n"
            "```\n"
            "Real [[link-two]]"
        )
        links = extract_wikilinks(body)
        targets = [l.target for l in links]
        self.assertEqual(targets, ["link-one", "link-two"])

    def test_inside_indented_code_block_skipped(self):
        body = "Real [[a]]\n    x = [[fake]]\n[[b]]"
        links = extract_wikilinks(body)
        targets = [l.target for l in links]
        self.assertEqual(targets, ["a", "b"])

    def test_multiple_on_line(self):
        body = "Compare [[one]] and [[two|the second]]."
        links = extract_wikilinks(body)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0].target, "one")
        self.assertEqual(links[1].target, "two")
        self.assertEqual(links[1].alias, "the second")

    def test_line_numbers(self):
        body = "first\n[[a]]\nthird\n[[b]]\n"
        links = extract_wikilinks(body)
        self.assertEqual(links[0].line, 2)
        self.assertEqual(links[1].line, 4)


# ============================================================
# extract_inline_tags
# ============================================================


class TestExtractInlineTags(unittest.TestCase):

    def test_simple_tag(self):
        body = "Some text with #cool-tag in it."
        self.assertEqual(extract_inline_tags(body), ["cool-tag"])

    def test_namespaced_tag(self):
        body = "Use #content/seafood-companies for that."
        self.assertEqual(extract_inline_tags(body), ["content/seafood-companies"])

    def test_url_not_a_tag(self):
        body = "Visit https://example.com/page#anchor for more."
        self.assertEqual(extract_inline_tags(body), [])

    def test_inside_code_block_skipped(self):
        body = "Tag #real here.\n```\n#fake\n```\n#also-real"
        tags = extract_inline_tags(body)
        self.assertEqual(set(tags), {"real", "also-real"})

    def test_heading_anchor_not_a_tag(self):
        # `#Section` mid-prose IS sometimes a tag-looking heading anchor.
        # We treat it as a tag — there's no reliable disambiguation; vault
        # convention is heading anchors only appear inside wikilinks.
        body = "See section #Section above."
        self.assertEqual(extract_inline_tags(body), ["Section"])


# ============================================================
# extract_markdown_md_links
# ============================================================


class TestExtractMarkdownMdLinks(unittest.TestCase):

    def test_finds_md_link(self):
        body = "See [the note](path/to/note.md) for context."
        out = extract_markdown_md_links(body)
        self.assertEqual(len(out), 1)
        text, path, line = out[0]
        self.assertEqual(text, "the note")
        self.assertEqual(path, "path/to/note.md")

    def test_ignores_non_md_links(self):
        body = "See [docs](https://example.com) and [image](pic.png)."
        out = extract_markdown_md_links(body)
        self.assertEqual(out, [])

    def test_skips_code_blocks(self):
        body = "Real [a](a.md)\n```\n[fake](fake.md)\n```\n[b](b.md)"
        out = extract_markdown_md_links(body)
        paths = [p for _, p, _ in out]
        self.assertEqual(paths, ["a.md", "b.md"])


# ============================================================
# walk_project
# ============================================================


class TestWalkProject(unittest.TestCase):

    def _make_project(self, tmp: Path) -> None:
        (tmp / "brief.md").write_text(
            "---\ntype: project\nslug: test\n---\n\n# Test\n\nBody."
        )
        (tmp / "decisions").mkdir()
        (tmp / "decisions" / "2026-05-26-decide.md").write_text(
            "---\ntype: decision\n---\n\n## Decision\n\nDo X."
        )
        (tmp / "sessions").mkdir()
        (tmp / "sessions" / "2026-05-26.md").write_text(
            "---\ntype: session\n---\n\n## Log\n\n- 10:00 start"
        )
        # _legacy should be skipped by default
        (tmp / "_legacy").mkdir()
        (tmp / "_legacy" / "old.md").write_text(
            "---\ntype: doc\n---\n\nLegacy."
        )
        # .git should be skipped
        (tmp / ".git").mkdir()
        (tmp / ".git" / "ignored.md").write_text("# ignored")

    def test_skips_legacy_and_git_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_project(root)
            files = list(walk_project(root))
            rels = sorted(str(f.rel_path) for f in files)
            self.assertEqual(rels, [
                "brief.md",
                "decisions/2026-05-26-decide.md",
                "sessions/2026-05-26.md",
            ])

    def test_include_legacy_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_project(root)
            files = list(walk_project(root, include_legacy=True))
            rels = sorted(str(f.rel_path) for f in files)
            self.assertIn("_legacy/old.md", rels)
            self.assertNotIn(".git/ignored.md", rels)

    def test_frontmatter_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_project(root)
            files = list(walk_project(root))
            briefs = [f for f in files if f.rel_path.name == "brief.md"]
            self.assertEqual(len(briefs), 1)
            self.assertEqual(briefs[0].frontmatter.fields["type"], "project")
            self.assertEqual(briefs[0].file_type, "project")

    def test_tags_collected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.md").write_text(
                "---\ntype: note\ntags:\n  - alpha\n  - beta\n---\n\n"
                "Body with #inline-tag."
            )
            files = list(walk_project(root))
            self.assertEqual(set(files[0].tags), {"alpha", "beta", "inline-tag"})


# ============================================================
# build_vault_index + resolve_wikilink
# ============================================================


class TestVaultIndex(unittest.TestCase):

    def _vault(self, tmp: Path) -> Path:
        vault = tmp / "v"
        p = vault / "projects" / "active" / "demo" / "decisions"
        p.mkdir(parents=True)
        (p / "2026-08-12-branch-track.md").write_text("# d")
        (vault / "projects" / "active" / "demo" / "brief.md").write_text("# b")
        return vault

    def test_the_zone_less_form_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = build_vault_index(self._vault(Path(tmp)))
            self.assertTrue(resolve_wikilink(
                "demo/decisions/2026-08-12-branch-track", idx))
            self.assertTrue(resolve_wikilink(
                "demo/decisions/2026-08-12-branch-track.md", idx))
            self.assertTrue(resolve_wikilink("demo/brief", idx))

    def test_the_full_vault_path_still_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = build_vault_index(self._vault(Path(tmp)))
            self.assertTrue(resolve_wikilink(
                "projects/active/demo/brief", idx))
            self.assertTrue(resolve_wikilink(
                "projects/active/demo/brief.md", idx))

    def test_a_bare_stem_no_longer_resolves(self):
        # Obsidian's default resolution matches any `brief.md` anywhere. In a
        # vault with 27 projects that is 27 files answering to one name, and
        # adjudant reported such a link as healthy.
        with tempfile.TemporaryDirectory() as tmp:
            idx = build_vault_index(self._vault(Path(tmp)))
            self.assertFalse(resolve_wikilink("brief", idx))
            self.assertFalse(resolve_wikilink("brief.md", idx))
            self.assertFalse(resolve_wikilink("2026-08-12-branch-track", idx))

    def test_a_wrong_project_does_not_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = build_vault_index(self._vault(Path(tmp)))
            self.assertFalse(resolve_wikilink("other/brief", idx))

    def test_non_existent(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = build_vault_index(self._vault(Path(tmp)))
            self.assertFalse(resolve_wikilink("does/not/exist", idx))

    def test_canvas_and_base_indexed_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "v"
            (vault / "projects" / "active" / "demo" / "canvases").mkdir(parents=True)
            (vault / "projects" / "active" / "demo" / "canvases" / "art.canvas").write_text("{}")
            idx = build_vault_index(vault)
            self.assertTrue(resolve_wikilink("demo/canvases/art.canvas", idx))
            self.assertTrue(resolve_wikilink("demo/canvases/art", idx))
            self.assertFalse(resolve_wikilink("art", idx))

    def test_a_vault_root_file_resolves_by_its_own_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "v"
            vault.mkdir()
            (vault / "Home.md").write_text("# h")
            idx = build_vault_index(vault)
            self.assertTrue(resolve_wikilink("Home", idx))
            self.assertTrue(resolve_wikilink("Home.md", idx))


# ============================================================
# breadcrumb + vault resolution
# ============================================================


class TestBreadcrumb(unittest.TestCase):

    def test_parse_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".claude" / "adjudant").write_text(
                "vault_path: /v\nvault_name: v\nslug: x\nmode: project\n"
            )
            bc = parse_breadcrumb(root)
            self.assertEqual(bc["slug"], "x")
            self.assertEqual(bc["vault_path"], "/v")

    def test_resolve_vault_via_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as vault:
            root = Path(tmp)
            (root / ".claude").mkdir()
            (root / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: v\nslug: x\nmode: project\n"
            )
            resolved = resolve_vault(root)
            self.assertEqual(resolved, Path(vault))

    def test_resolve_vault_via_walk_up_to_home_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (vault / "Home.md").write_text("---\ntype: vault-home\n---\n# Home\n")
            (vault / "projects").mkdir()
            (vault / "projects" / "x").mkdir()
            self.assertEqual(resolve_vault(vault / "projects" / "x"), vault.resolve())

    def test_resolve_vault_env_override(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as vault:
            self.assertEqual(resolve_vault(Path(tmp), env_vault=vault), Path(vault))


# ============================================================
# Inline-code wikilink skip (regression: false positive in release notes)
# ============================================================


class TestInlineCodeSkip(unittest.TestCase):

    def test_wikilink_inside_backticks_skipped(self):
        body = "Rewrite `[[stem|text]]` to the canonical form."
        links = extract_wikilinks(body)
        self.assertEqual(links, [])

    def test_wikilink_outside_backticks_kept(self):
        body = "Real [[link]] here."
        links = extract_wikilinks(body)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].target, "link")

    def test_mixed_inline_code_and_real_link(self):
        body = "Use `[[code-example]]` but see [[real-link]]."
        links = extract_wikilinks(body)
        self.assertEqual([l.target for l in links], ["real-link"])

    def test_tag_inside_backticks_skipped(self):
        body = "Use `#sample-tag` as literal; real tag is #real-tag."
        tags = extract_inline_tags(body)
        self.assertEqual(tags, ["real-tag"])

    def test_md_link_inside_backticks_skipped(self):
        body = "Don't link `[a](b.md)` from inside code; do link [c](c.md) outside."
        out = extract_markdown_md_links(body)
        paths = [p for _, p, _ in out]
        self.assertEqual(paths, ["c.md"])


# ============================================================
# Breadcrumb auto-follow (smart_project_dir + resolve_project_from_cwd)
# ============================================================


class TestSmartProjectDir(unittest.TestCase):

    def test_passes_through_when_no_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No .claude/adjudant — should treat arg as the vault project itself
            scan_dir, vault_hint = smart_project_dir(tmp)
            self.assertEqual(scan_dir, Path(tmp).resolve())
            self.assertIsNone(vault_hint)

    def test_follows_breadcrumb_to_vault_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = Path(tmp) / "code"; code.mkdir()
            vault = Path(tmp) / "vault"; vault.mkdir()
            (vault / "projects").mkdir()
            (vault / "projects" / "p").mkdir()
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\nslug: p\nmode: project\n"
            )
            scan_dir, vault_hint = smart_project_dir(str(code))
            self.assertEqual(scan_dir.resolve(), (vault / "projects" / "p").resolve())
            self.assertEqual(vault_hint.resolve(), vault.resolve())

    def test_raises_when_breadcrumb_present_but_vault_unresolvable(self):
        # Regression: this used to fall through and return the CODE REPO as the
        # scan dir, letting write-path verbs (clean apply) rewrite the repository.
        with tempfile.TemporaryDirectory() as tmp:
            code = Path(tmp) / "code"; code.mkdir()
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {tmp}/does-not-exist\nvault_name: no-such-vault\nslug: p\nmode: project\n"
            )
            with self.assertRaises(VaultUnresolvableError):
                smart_project_dir(str(code))

    def test_subdirectory_of_connected_repo_follows_the_breadcrumb_above(self):
        # Audit 2026-07-27 finding 7: running a helper from a SUBDIR of a
        # connected repo found no breadcrumb there, fell through to
        # "treat the arg as the vault project", and wrote into the CODE REPO
        # (board.py created board/board-data.json inside repo/backend/svc).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = root / "code"
            sub = code / "backend" / "svc"
            sub.mkdir(parents=True)
            vault = root / "vault"
            (vault / "projects" / "p").mkdir(parents=True)
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\nslug: p\nmode: project\n")
            scan_dir, vault_hint = smart_project_dir(str(sub))
            self.assertEqual(scan_dir.resolve(), (vault / "projects" / "p").resolve())
            self.assertEqual(vault_hint.resolve(), vault.resolve())

    def test_code_repo_without_any_breadcrumb_is_refused(self):
        # A git repo is never a vault project: accepting it silently let
        # write verbs rewrite source files.
        with tempfile.TemporaryDirectory() as tmp:
            code = Path(tmp) / "repo"
            (code / ".git").mkdir(parents=True)
            (code / "AGENTS.md").write_text("# repo\n")
            with self.assertRaises(VaultUnresolvableError):
                smart_project_dir(str(code))

    def test_real_vault_project_still_accepted(self):
        # Backward compatibility: a genuine vault project path keeps working.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "vault" / "projects" / "p"
            proj.mkdir(parents=True)
            (proj / "brief.md").write_text("---\ntype: project\nslug: p\n---\n")
            scan_dir, _ = smart_project_dir(str(proj))
            self.assertEqual(scan_dir, proj.resolve())


class TestWalkUpDoesNotOverreach(unittest.TestCase):
    """Fix wave 1 finding 2: the breadcrumb walk-up ran even when the argument
    was ALREADY a valid vault project dir, and it climbed every ancestor
    without bound. A breadcrumb at or above the vault root therefore
    retargeted an explicitly passed vault-project path at some OTHER project,
    so every verb silently operated on the wrong one.
    """

    def _vault_with_breadcrumb_at_root(self, tmp: str) -> tuple[Path, Path]:
        """Vault whose own root carries a breadcrumb pointing at `other`."""
        vault = Path(tmp) / "vault"
        for slug in ("p", "other"):
            (vault / "projects" / slug).mkdir(parents=True)
            (vault / "projects" / slug / "brief.md").write_text(
                f"---\ntype: project\nslug: {slug}\n---\n")
        (vault / ".claude").mkdir()
        (vault / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: other\nmode: project\n")
        return vault, vault / "projects" / "p"

    def test_explicit_vault_project_ignores_an_ancestor_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, proj = self._vault_with_breadcrumb_at_root(tmp)
            scan_dir, _ = smart_project_dir(str(proj))
            self.assertEqual(
                scan_dir, proj.resolve(),
                "an explicit vault-project path must resolve to ITSELF, not to "
                "whatever some ancestor breadcrumb happens to name")
            self.assertNotEqual(scan_dir, (vault / "projects" / "other").resolve())

    def test_zoned_vault_project_ignores_an_ancestor_breadcrumb(self):
        # A shelved project has no brief.md requirement to lean on here: the
        # positive marker is the projects/<zone>/ parent.
        with tempfile.TemporaryDirectory() as tmp:
            vault, _ = self._vault_with_breadcrumb_at_root(tmp)
            shelved = vault / "projects" / "_fridge" / "cold"
            shelved.mkdir(parents=True)
            scan_dir, _ = smart_project_dir(str(shelved))
            self.assertEqual(scan_dir, shelved.resolve())

    def test_walk_up_stops_at_the_vault_boundary(self):
        # A subdir INSIDE a vault project must not climb out of the vault to a
        # breadcrumb sitting above it and land on a different project.
        with tempfile.TemporaryDirectory() as tmp:
            vault, proj = self._vault_with_breadcrumb_at_root(tmp)
            sub = proj / "sessions"
            sub.mkdir()
            scan_dir, _ = smart_project_dir(str(sub))
            self.assertNotEqual(
                scan_dir.resolve(), (vault / "projects" / "other").resolve(),
                "the walk must not cross the vault boundary to a foreign breadcrumb")

    def test_connected_repo_subdir_still_follows_its_breadcrumb(self):
        # Guard against over-correction: finding 7's fix must not regress even
        # when the repo itself contains a `projects/` directory.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = root / "code"
            sub = code / "backend" / "svc"
            sub.mkdir(parents=True)
            (code / "projects").mkdir()
            vault = root / "vault"
            (vault / "projects" / "p").mkdir(parents=True)
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\nslug: p\nmode: project\n")
            scan_dir, vault_hint = smart_project_dir(str(sub))
            self.assertEqual(scan_dir.resolve(), (vault / "projects" / "p").resolve())
            self.assertEqual(vault_hint.resolve(), vault.resolve())


class TestRoundThreeRegressions(unittest.TestCase):
    """v0.11.0 primitives fixes."""

    def test_md_link_re_ignores_external_urls(self):
        from _vault_walk import MD_LINK_RE
        line = "see [readme](https://github.com/x/y/blob/main/README.md) and [n](notes/n.md)"
        paths = [m.group(2) for m in MD_LINK_RE.finditer(line)]
        self.assertEqual(paths, ["notes/n.md"])

    def test_embed_flag_set(self):
        links = extract_wikilinks("An embed ![[diagram.png]] and a link [[note]].")
        by_target = {l.target: l for l in links}
        self.assertTrue(by_target["diagram.png"].is_embed)
        self.assertFalse(by_target["note"].is_embed)

    def test_is_checkable_wikilink(self):
        from _vault_walk import is_checkable_wikilink
        links = extract_wikilinks(
            "![[img.png]] [[#Heading]] [[attach.pdf]] [[real-note]] [[art.canvas]]")
        checkable = [l.target for l in links if is_checkable_wikilink(l)]
        self.assertEqual(checkable, ["real-note", "art.canvas"])

    def test_flow_style_tags_parse_as_list(self):
        fm, _ = parse_frontmatter("---\ntags: [project, adjudant]\n---\n")
        self.assertEqual(fm.fields["tags"], ["project", "adjudant"])

    def test_flow_style_empty_list(self):
        fm, _ = parse_frontmatter("---\nstack: []\n---\n")
        self.assertEqual(fm.fields["stack"], [])

    def test_zero_indent_block_list(self):
        fm, _ = parse_frontmatter("---\ntags:\n- a\n- b\n---\n")
        self.assertEqual(fm.fields["tags"], ["a", "b"])

    def test_ob_vault_env_override(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OB_VAULT"] = tmp
            try:
                self.assertEqual(resolve_vault(Path("/nonexistent-project")), Path(tmp))
            finally:
                del os.environ["OB_VAULT"]

    def test_walk_project_skips_scratch_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes").mkdir()
            (root / "notes" / "real.md").write_text("# real")
            scratch = root / ".adjudant-remise-preview" / "files" / "notes"
            scratch.mkdir(parents=True)
            (scratch / "pending.md").write_text("# pending")
            names = [f.rel_path.name for f in walk_project(root)]
            self.assertEqual(names, ["real.md"])


class TestResolveProjectFromCwd(unittest.TestCase):

    def test_returns_context_when_breadcrumb_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = Path(tmp) / "code"; code.mkdir()
            vault = Path(tmp) / "vault"; vault.mkdir()
            (vault / "projects" / "p").mkdir(parents=True)
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\nslug: p\nmode: project\n"
            )
            ctx = resolve_project_from_cwd(code)
            self.assertIsNotNone(ctx)
            self.assertEqual(ctx.slug, "p")
            self.assertTrue(ctx.is_connected)

    def test_returns_none_without_breadcrumb(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_project_from_cwd(Path(tmp)))


class TestBreadcrumbSlugIsGatedOnTheVerbPath(unittest.TestCase):
    """`.claude/adjudant` is a REPO-COMMITTED file, so a cloned repo carries
    whatever slug its author wrote. v0.18.0 gated the slug in every HOOK and
    left the VERB path open: resolve_project_from_cwd fed bc["slug"] straight
    into `{vault}/projects/{slug}`, so `slug: ../../escaped` handed every verb
    behind smart_project_dir a project dir outside the vault.
    """

    HOSTILE = (
        "../../escaped", "..", "../sibling", "sub/../../../out",
        "a/b", "/etc", "./.", "%2e%2e/x",
    )

    @staticmethod
    def _setup(tmp: Path, slug: str) -> tuple[Path, Path]:
        vault = tmp / "vault"
        (vault / "projects").mkdir(parents=True)
        (vault / "Home.md").write_text(
            "---\ntype: vault-home\nupdated: 2026-01-01\n---\n")
        code = tmp / "code"
        (code / ".claude").mkdir(parents=True)
        (code / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: {slug}\nmode: project\n")
        return code, vault

    def _assert_under_projects(self, vault: Path, path: Path) -> None:
        """A real project dir always sits UNDER `{vault}/projects`. Asserting
        that, rather than 'inside the vault', also rejects the `..` slug that
        resolves to the vault root itself (which clean apply would rewrite
        wholesale)."""
        projects = (vault / "projects").resolve()
        p = Path(path).resolve()
        self.assertIn(projects, p.parents,
                      f"{p} is not under {projects}")

    def test_no_hostile_slug_yields_a_path_outside_the_vault(self):
        # Asserts the OUTCOME (containment), not the mechanism: returning None,
        # raising, or clamping the path all pass here. Only an absent guard
        # cannot.
        for slug in self.HOSTILE:
            with self.subTest(slug=slug), tempfile.TemporaryDirectory() as tmp:
                code, vault = self._setup(Path(tmp), slug)
                try:
                    ctx = resolve_project_from_cwd(code)
                except VaultUnresolvableError:
                    continue
                if ctx is not None:
                    self._assert_under_projects(vault, ctx.vault_project_dir)

    def test_a_traversal_target_that_exists_is_still_refused(self):
        # find_project_dir returns the FIRST candidate that exists, so a
        # traversal slug pointing at a real directory took the zone-aware
        # branch rather than the `or (vault / "projects" / slug)` fallback.
        # Both branches have to be gated.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, vault = self._setup(root, "../../escaped")
            escaped = root / "escaped"
            escaped.mkdir()
            (escaped / "brief.md").write_text(
                "---\ntype: project\nslug: escaped\n---\n")
            try:
                ctx = resolve_project_from_cwd(code)
            except VaultUnresolvableError:
                return
            self.assertIsNotNone(ctx)
            self._assert_under_projects(vault, ctx.vault_project_dir)

    def test_the_refusal_names_the_slug_and_points_at_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _vault = self._setup(Path(tmp), "../../escaped")
            with self.assertRaises(VaultUnresolvableError) as cm:
                resolve_project_from_cwd(code)
            msg = str(cm.exception)
            self.assertIn("../../escaped", msg)
            self.assertIn("connect", msg)

    def test_smart_project_dir_never_hands_a_verb_a_path_outside_the_vault(self):
        # The shared resolver behind check/clean/dream/sitrep/board.
        for slug in self.HOSTILE:
            with self.subTest(slug=slug), tempfile.TemporaryDirectory() as tmp:
                code, vault = self._setup(Path(tmp), slug)
                try:
                    scan_dir, _hint = smart_project_dir(str(code))
                except VaultUnresolvableError:
                    continue
                self._assert_under_projects(vault, scan_dir)

    def test_a_safe_slug_is_unaffected(self):
        # The guard must not be over-broad: ordinary kebab slugs still resolve,
        # connected or not.
        with tempfile.TemporaryDirectory() as tmp:
            code, vault = self._setup(Path(tmp), "good-slug-1")
            (vault / "projects" / "good-slug-1").mkdir()
            ctx = resolve_project_from_cwd(code)
            self.assertIsNotNone(ctx)
            self.assertTrue(ctx.is_connected)
            self._assert_under_projects(vault, ctx.vault_project_dir)

    def test_a_safe_slug_with_no_vault_dir_yet_still_reports_the_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, vault = self._setup(Path(tmp), "not-created-yet")
            ctx = resolve_project_from_cwd(code)
            self.assertIsNotNone(ctx)
            self.assertFalse(ctx.is_connected)
            self._assert_under_projects(vault, ctx.vault_project_dir)

    def test_a_symlinked_projects_dir_cannot_place_a_new_project_outside(self):
        """The slug rule alone is lexical: `{vault}/projects/good-slug` is
        inside the vault by spelling but outside it on disk when `projects`
        is a symlink. safe_project_root is the containment half of the guard,
        and the fallback branch is the one that SCAFFOLDS a project that does
        not exist yet, so it must refuse to scaffold outside the vault.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            vault = root / "vault"
            vault.mkdir()
            (vault / "Home.md").write_text(
                "---\ntype: vault-home\nupdated: 2026-01-01\n---\n")
            (vault / "projects").symlink_to(outside, target_is_directory=True)
            code = root / "code"
            (code / ".claude").mkdir(parents=True)
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\n"
                f"slug: not-created-yet\nmode: project\n")
            try:
                ctx = resolve_project_from_cwd(code)
            except VaultUnresolvableError:
                return
            self.assertIsNotNone(ctx)
            self.assertIn(vault.resolve(),
                          Path(ctx.vault_project_dir).resolve().parents,
                          f"{ctx.vault_project_dir} is outside {vault}")


# ============================================================
# Vault-name cross-machine resolution
# ============================================================


class TestVaultNameResolution(unittest.TestCase):

    def test_vault_name_resolves_when_abs_path_fails(self):
        """If breadcrumb's vault_path is missing/wrong but vault_name matches a
        standard-location vault, resolve_vault should still find it."""
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch

            home = Path(tmp)
            (home / "Documents").mkdir()
            vault = home / "Documents" / "MyVault"
            vault.mkdir()
            # F26: a candidate must carry a vault marker to qualify
            (vault / ".obsidian").mkdir()

            code = home / "code"; code.mkdir()
            (code / ".claude").mkdir()
            # Absolute path is bogus, vault_name should rescue
            (code / ".claude" / "adjudant").write_text(
                "vault_path: /nope/missing\nvault_name: MyVault\nslug: p\nmode: project\n"
            )

            with patch("pathlib.Path.home", return_value=home):
                resolved = resolve_vault(code)
            self.assertEqual(resolved.resolve(), vault.resolve())


from datetime import date

from _vault_walk import (
    DEFAULT_STALE_DAYS,
    LEGACY_ZONES,
    LEGACY_ZONE_ALIAS,
    PROJECT_ZONES,
    ZONE_FOR_STATUS,
    enumerate_projects_all_zones,
    find_project_dir,
    newest_dated_stem,
    resolve_project_from_cwd,
    suggest_status,
    zone_dir,
    zone_of,
)


def _mk_project(vault: Path, slug: str, zone: str = "", status: str = "active",
                sessions: list = ()) -> Path:
    pdir = vault / "projects" / zone / slug if zone else vault / "projects" / slug
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "brief.md").write_text(
        f"---\ntype: project\nslug: {slug}\nproject_type: coding\nstatus: {status}\n---\n\n# {slug}\n")
    if sessions:
        (pdir / "sessions").mkdir(exist_ok=True)
        for d in sessions:
            (pdir / "sessions" / f"{d}.md").write_text("---\ntype: session\n---\n")
    return pdir


class TestLifecycleFolders(unittest.TestCase):

    def test_four_named_folders(self):
        self.assertEqual(PROJECT_ZONES,
                         ("active", "paused", "finished", "archive"))
        self.assertNotIn("", PROJECT_ZONES,
                         "the live zone is a named folder now, not the absence of one")

    def test_legacy_shapes_map_onto_the_four(self):
        self.assertEqual(LEGACY_ZONES, ("", "_fridge", "_archive"))
        self.assertEqual(set(LEGACY_ZONE_ALIAS.values()) - set(PROJECT_ZONES), set())
        self.assertEqual(LEGACY_ZONE_ALIAS[""], "active")
        self.assertEqual(LEGACY_ZONE_ALIAS["_fridge"], "paused")
        self.assertEqual(LEGACY_ZONE_ALIAS["_archive"], "archive")

    def test_status_migration_map_lands_in_the_four(self):
        # The retired project status vocabulary still sits in briefs written
        # before v3. It is read to SUGGEST a folder during triage, never to
        # grade one.
        self.assertEqual(set(ZONE_FOR_STATUS.values()) - set(PROJECT_ZONES), set())
        self.assertEqual(ZONE_FOR_STATUS["active"], "active")
        self.assertEqual(ZONE_FOR_STATUS["stale"], "active")
        self.assertEqual(ZONE_FOR_STATUS["seed"], "active")
        self.assertEqual(ZONE_FOR_STATUS["fridge"], "paused")
        self.assertEqual(ZONE_FOR_STATUS["done"], "finished")
        self.assertEqual(ZONE_FOR_STATUS["dead"], "archive")

    def test_zone_dir(self):
        self.assertEqual(zone_dir(Path("/v"), "paused"),
                         Path("/v/projects/paused"))


class TestSuggestStatus(unittest.TestCase):

    def test_active_goes_stale_after_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", sessions=["2026-05-01"])
            out = suggest_status("active", pdir, date(2026, 7, 16))
            self.assertEqual(out["suggested"], "stale")
            self.assertEqual(out["days_quiet"], 76)
            self.assertIn("76 days", out["reason"])

    def test_active_stays_when_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", sessions=["2026-07-10"])
            out = suggest_status("active", pdir, date(2026, 7, 16))
            self.assertIsNone(out["suggested"])

    def test_stale_suggests_active_on_new_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", status="stale", sessions=["2026-07-15"])
            out = suggest_status("stale", pdir, date(2026, 7, 16))
            self.assertEqual(out["suggested"], "active")

    def test_deliberate_states_never_suggested_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            for status in ("seed", "done", "dead"):
                pdir = _mk_project(Path(tmp), f"p-{status}", status=status,
                                   sessions=["2020-01-01"])
                out = suggest_status(status, pdir, date(2026, 7, 16))
                self.assertIsNone(out["suggested"], status)

    def test_fridge_nudges_after_180_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", status="fridge", sessions=["2025-06-01"])
            out = suggest_status("fridge", pdir, date(2026, 7, 16))
            self.assertIsNone(out["suggested"])
            self.assertIn("still intentional", out["nudge"])

    def test_invalid_declared_flagged_and_treated_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", status="paused", sessions=["2026-01-01"])
            out = suggest_status("paused", pdir, date(2026, 7, 16))
            self.assertFalse(out["declared_valid"])
            self.assertEqual(out["suggested"], "stale")

    def test_no_sessions_no_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p")
            out = suggest_status("active", pdir, date(2026, 7, 16))
            self.assertIsNone(out["days_quiet"])
            self.assertIsNone(out["suggested"])

    def test_custom_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", sessions=["2026-07-06"])
            out = suggest_status("active", pdir, date(2026, 7, 16), stale_after_days=7)
            self.assertEqual(out["suggested"], "stale")

    def test_malformed_date_cannot_mask_staleness(self):
        # Regression: a lexicographically-larger malformed stem (month 99) must
        # not beat a valid older date and collapse days_quiet to None.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p",
                               sessions=["2026-06-01", "2026-99-01-broken"])
            out = suggest_status("active", pdir, date(2026, 7, 16))
            self.assertEqual(out["last_session"], "2026-06-01")
            self.assertEqual(out["days_quiet"], 45)
            self.assertEqual(out["suggested"], "stale")

    def test_boundary_exactly_at_threshold_goes_stale(self):
        # days_quiet == stale_after_days is the >= edge: suggest stale.
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", sessions=["2026-06-16"])
            out = suggest_status("active", pdir, date(2026, 7, 16))
            self.assertEqual(out["days_quiet"], 30)
            self.assertEqual(out["suggested"], "stale")

    def test_declared_none_flagged_and_treated_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = _mk_project(Path(tmp), "p", sessions=["2026-01-01"])
            out = suggest_status(None, pdir, date(2026, 7, 16))
            self.assertFalse(out["declared_valid"])
            self.assertEqual(out["suggested"], "stale")


class TestZones(unittest.TestCase):

    def test_find_project_dir_across_the_four(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            for zone in ("active", "paused", "finished", "archive"):
                _mk_project(vault, f"p-{zone}", zone=zone)
            for zone in ("active", "paused", "finished", "archive"):
                found = find_project_dir(vault, f"p-{zone}")
                self.assertEqual(zone_of(found), zone)
            self.assertIsNone(find_project_dir(vault, "nope"))

    def test_find_project_dir_still_finds_an_unmigrated_project(self):
        # A vault that has not been triaged yet must keep working: every hook
        # and every verb resolves through this one function.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk_project(vault, "bare", zone="")
            _mk_project(vault, "cold", zone="_fridge")
            _mk_project(vault, "gone", zone="_archive")
            self.assertEqual(find_project_dir(vault, "bare"),
                             vault / "projects" / "bare")
            self.assertEqual(zone_of(find_project_dir(vault, "bare")), "active")
            self.assertEqual(zone_of(find_project_dir(vault, "cold")), "paused")
            self.assertEqual(zone_of(find_project_dir(vault, "gone")), "archive")

    def test_named_folder_beats_a_legacy_twin(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk_project(vault, "p", zone="")
            _mk_project(vault, "p", zone="active")
            self.assertEqual(find_project_dir(vault, "p"),
                             vault / "projects" / "active" / "p")

    def test_enumerate_normalises_the_zone(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            _mk_project(vault, "a", zone="active")
            _mk_project(vault, "b", zone="_fridge")
            (vault / "projects" / "_index.md").write_text("idx")
            rows = enumerate_projects_all_zones(vault)
            self.assertEqual([(s, z) for s, _p, z in rows],
                             [("a", "active"), ("b", "paused")])

    def test_zone_matches_status_is_gone(self):
        import _vault_walk
        self.assertFalse(hasattr(_vault_walk, "zone_matches_status"),
                         "the folder IS the lifecycle state; nothing grades it "
                         "against a field the brief no longer carries")

    def test_resolve_project_from_cwd_finds_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            _mk_project(vault, "proj", zone="_archive", status="done")
            code = root / "code"
            (code / ".claude").mkdir(parents=True)
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\nslug: proj\nmode: project\n")
            ctx = resolve_project_from_cwd(code)
            self.assertTrue(ctx.is_connected)
            self.assertEqual(ctx.vault_project_dir,
                             vault / "projects" / "_archive" / "proj")
            self.assertEqual(zone_of(ctx.vault_project_dir), "archive")


from _vault_walk import (
    FIELD_SCHEMA,
    STATUS_VALUES_FOR_TYPE,
)


class TestFieldSchema(unittest.TestCase):

    def test_every_board_lane_has_a_canonical_status(self):
        from board import CANONICAL_STATUS_FOR_COLUMN, DEFAULT_COLUMNS
        for col in DEFAULT_COLUMNS:
            status = CANONICAL_STATUS_FOR_COLUMN.get(col["id"])
            self.assertIsNotNone(status, f"lane {col['id']} has no status")
            self.assertIn(status, STATUS_VALUES_FOR_TYPE["task"])

    def test_status_values_for_type_keys(self):
        # Derived: a kind has a status vocabulary exactly when its template
        # writes one as a trailing `# a | b | c` comment. project lost its
        # status with the brief; spec gained one.
        self.assertEqual(set(STATUS_VALUES_FOR_TYPE),
                         {"decision", "task", "spec"})

    def test_every_entry_has_required_and_optional_frozensets(self):
        for ftype, spec in FIELD_SCHEMA.items():
            self.assertEqual(set(spec), {"required", "optional"}, ftype)
            self.assertIsInstance(spec["required"], frozenset, ftype)
            self.assertIsInstance(spec["optional"], frozenset, ftype)
            self.assertIn("type", spec["required"], ftype)

    def test_required_and_optional_disjoint(self):
        for ftype, spec in FIELD_SCHEMA.items():
            self.assertFalse(spec["required"] & spec["optional"], ftype)

    def test_project_field_absent_everywhere(self):
        for ftype, spec in FIELD_SCHEMA.items():
            self.assertNotIn("project", spec["required"], ftype)
            self.assertNotIn("project", spec["optional"], ftype)

    def test_is_safe_slug_accepts_kebab(self):
        from _vault_walk import is_safe_slug
        for good in ("demo", "a", "acme-web", "proj-2026", "0x"):
            self.assertTrue(is_safe_slug(good), good)

    def test_is_safe_slug_rejects_traversal_and_metachars(self):
        # Audit 2026-07-27: the breadcrumb is repo-committed, so these are
        # attacker-reachable. Each must be refused before any path build.
        from _vault_walk import is_safe_slug
        bad = [
            "../../../escaped", "..", ".", "/abs", "a/b", "a\\b",
            "-leading", "with space", "UPPER", "back`tick", "semi;colon",
            "dollar$sign", "new\nline", "tab\t", "emoji-\N{SNOWMAN}",
            "", "   ", "a" * 65, None, 42, ["demo"],
        ]
        for value in bad:
            self.assertFalse(is_safe_slug(value), repr(value))

    def test_safe_project_root_contains(self):
        from _vault_walk import safe_project_root
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "projects").mkdir(parents=True)
            got = safe_project_root(vault, "demo")
            self.assertEqual(got, (vault / "projects" / "demo").resolve())
            for bad in ("../../../escaped", "/etc", "a/b", "-x", ""):
                self.assertIsNone(safe_project_root(vault, bad), bad)

    def test_connect_shares_the_slug_rule(self):
        # connect.validate_slug is the single source of the kebab-case
        # rule; the hooks and resolve_project_from_cwd gate on
        # is_safe_slug. Asserted as behavioural agreement rather than shared
        # identity: validate_slug used to match SLUG_RE without the length
        # bound, so connect accepted a 100-char slug that every hook then
        # silently refused.
        import connect
        from _vault_walk import SLUG_MAX_LEN, is_safe_slug
        cases = ("demo", "a", "a-b-1", "", "-x", "A", "a_b", "a b", "a/b",
                 "../../escaped", "/etc", "..", "x" * SLUG_MAX_LEN,
                 "x" * (SLUG_MAX_LEN + 1))
        for value in cases:
            self.assertEqual(connect.validate_slug(value) is None,
                             is_safe_slug(value), repr(value))

from _vault_walk import (
    DECISION_STATUS_ALIASES,
    VaultFile,
    schema_drift,
    schema_drift_for_file,
)


def _vf(text: str, rel: str = "notes/x.md") -> VaultFile:
    fm, body = parse_frontmatter(text)
    return VaultFile(path=Path("/tmp") / rel, rel_path=Path(rel), frontmatter=fm,
                     body=body, tags_frontmatter=[], tags_inline=[],
                     wikilinks=[], markdown_md_links=[])


_CLEAN_DECISION = (
    "---\ntype: decision\nstatus: active\ncreated: 2026-07-27\n"
    "updated: 2026-07-27\n---\n\nBody\n")


class TestSchemaDrift(unittest.TestCase):

    def test_clean_decision_returns_none(self):
        self.assertIsNone(schema_drift_for_file(_vf(_CLEAN_DECISION)))

    def test_missing_required_flagged(self):
        d = schema_drift_for_file(_vf(_CLEAN_DECISION.replace("created: 2026-07-27\n", "")))
        self.assertEqual(d["missing_required"], ["created"])

    def test_project_field_is_unknown(self):
        d = schema_drift_for_file(_vf(_CLEAN_DECISION.replace(
            "type: decision\n", 'type: decision\nproject: "[[projects/x/brief|x]]"\n')))
        self.assertEqual(d["unknown_fields"], ["project"])

    def test_node_type_beside_type_is_conflict(self):
        d = schema_drift_for_file(_vf(_CLEAN_DECISION.replace(
            "type: decision\n", "type: decision\nnode_type: decision\n")))
        self.assertTrue(d["type_conflict"])
        self.assertIn("node_type", d["unknown_fields"])

    def test_metadata_nest_surfaces_as_single_unknown(self):
        d = schema_drift_for_file(_vf(_CLEAN_DECISION.replace(
            "type: decision\n", "type: decision\nmetadata:\n  node_type: memory\n  foo: bar\n")))
        self.assertEqual(d["unknown_fields"], ["metadata"])

    def test_decision_alias_status_normalizable(self):
        d = schema_drift_for_file(_vf(_CLEAN_DECISION.replace("status: active", "status: accepted")))
        self.assertEqual(d["status_invalid"]["value"], "accepted")
        self.assertTrue(d["status_invalid"]["normalizable"])

    def test_decision_bogus_status_not_normalizable(self):
        d = schema_drift_for_file(_vf(_CLEAN_DECISION.replace("status: active", "status: banana")))
        self.assertFalse(d["status_invalid"]["normalizable"])

    def test_task_alias_status_accepted_with_alias_set(self):
        # Aliases are accepted input (vault-standards section 4): with the
        # alias set supplied a wip task is clean; without it, flagged but
        # never normalizable (clean must not rewrite lane information).
        task = ("---\ntype: task\nstatus: wip\ncreated: 2026-07-27\n"
                "updated: 2026-07-27\n---\n")
        self.assertIsNone(schema_drift_for_file(_vf(task), aliases={"wip", "parked"}))
        d = schema_drift_for_file(_vf(task))
        self.assertFalse(d["status_invalid"]["normalizable"])

    def test_session_is_three_fields_and_nothing_else(self):
        # v3 dropped date, started, session_id and the bare tag: one note had
        # stacked eighteen conversation UUIDs into session_id.
        s = "---\ntype: session\ncreated: 2026-07-27\nupdated: 2026-07-27\n---\n"
        self.assertIsNone(schema_drift_for_file(_vf(s)))
        d = schema_drift_for_file(_vf(s.replace(
            "updated: 2026-07-27\n", "updated: 2026-07-27\nsession_id: []\n")))
        self.assertEqual(d["unknown_fields"], ["session_id"])

    def test_decision_alias_map_locked(self):
        self.assertEqual(DECISION_STATUS_ALIASES,
                         {"accepted": "active", "locked": "active", "current": "active"})

    def test_aggregate_counts_and_skips(self):
        files = [
            _vf(_CLEAN_DECISION),                                     # clean
            _vf(_CLEAN_DECISION.replace("created: 2026-07-27\n", "")),  # flagged
            _vf("no frontmatter at all\n"),                           # unchecked
            _vf("---\ntype: tasks\n---\n"),                           # non-canonical type
            _vf("---\ntype: decision\nbroken"),                       # parse error
        ]
        agg = schema_drift(files)
        self.assertEqual(agg["checked"], 2)
        self.assertEqual(agg["unchecked"], 3)
        self.assertEqual(agg["flagged"], 1)
        self.assertEqual(agg["counts"]["missing_required"], 1)
        self.assertEqual(len(agg["samples"]), 1)
        self.assertEqual(agg["samples"][0]["file"], "notes/x.md")

    def test_schema_drift_for_text_matches_file_variant(self):
        from _vault_walk import schema_drift_for_text
        text = ("---\ntype: decision\nstatus: accepted\ncreated: 2026-01-01\n"
                "updated: 2026-01-01\n---\n\nBody.\n")
        by_text = schema_drift_for_text(text, "decisions/d.md")
        by_file = schema_drift_for_file(_vf(text, rel="decisions/d.md"))
        self.assertEqual(by_text, by_file)

    def test_schema_drift_for_text_flags_missing_required(self):
        from _vault_walk import schema_drift_for_text
        d = schema_drift_for_text("---\ntype: decision\n---\n\nB\n", "decisions/d.md")
        self.assertEqual(d["file"], "decisions/d.md")
        self.assertEqual(d["type"], "decision")
        self.assertIn("status", d["missing_required"])

    def test_schema_drift_for_text_clean_returns_none(self):
        from _vault_walk import schema_drift_for_text
        text = "---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\nB\n"
        self.assertIsNone(schema_drift_for_text(text, "notes/n.md"))

    def test_schema_drift_for_text_ignores_unjudgeable(self):
        from _vault_walk import schema_drift_for_text
        # no frontmatter, unknown type, and a parse error are all deep-pass
        # territory, not schema territory
        self.assertIsNone(schema_drift_for_text("no frontmatter\n", "notes/n.md"))
        self.assertIsNone(schema_drift_for_text(
            "---\ntype: unknowntype\n---\n\nB\n", "notes/n.md"))
        self.assertIsNone(schema_drift_for_text("---\ntype: note\nno close\n", "notes/n.md"))


class TestAtomicWriteText(unittest.TestCase):
    """Audit 2026-07-30 finding 5. There was no atomic-write helper anywhere in
    the codebase; every writer truncated in place, which is how a concurrent
    reader lands on a zero-byte file."""

    def test_writes_the_content_and_leaves_no_temp_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "deck.json"
            atomic_write_text(p, '{"a": 1}\n')
            self.assertEqual(p.read_text(), '{"a": 1}\n')
            self.assertEqual([q.name for q in Path(tmp).iterdir()], ["deck.json"])

    def test_the_temp_file_is_a_sibling_so_os_replace_stays_atomic(self):
        # A temp in $TMPDIR is usually another filesystem, where os.replace
        # degrades to copy-then-delete and stops being atomic.
        seen = []
        real = tempfile.mkstemp

        def spy(*a, **kw):
            seen.append(kw.get("dir"))
            return real(*a, **kw)

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sub" / "deck.json"
            p.parent.mkdir()
            with unittest.mock.patch.object(tempfile, "mkstemp", spy):
                atomic_write_text(p, "x")
            self.assertEqual(seen, [str(p.parent)])

    def test_a_failed_write_leaves_the_original_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "deck.json"
            p.write_text("original\n")
            with self.assertRaises(UnicodeEncodeError):
                atomic_write_text(p, "caf\ud800")     # lone surrogate
            self.assertEqual(p.read_text(), "original\n")
            self.assertEqual([q.name for q in Path(tmp).iterdir()], ["deck.json"])

    def test_preserves_the_destination_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "deck.json"
            p.write_text("old")
            p.chmod(0o640)
            atomic_write_text(p, "new")
            self.assertEqual(p.stat().st_mode & 0o777, 0o640)

    def test_a_reader_never_sees_a_partial_file(self):
        # Real processes: a writer looping over a large payload while this
        # process reads. Every read must be one whole version.
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            target = tmpp / "big.txt"
            target.write_text("A" * 400_000 + "\n")
            runner = tmpp / "w.py"
            runner.write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                "from pathlib import Path\n"
                "from _vault_walk import atomic_write_text\n"
                "t = Path(sys.argv[1])\n"
                "for i in range(150):\n"
                "    c = 'AB'[i % 2]\n"
                "    atomic_write_text(t, c * 400_000 + '\\n')\n")
            procs = [subprocess.Popen([sys.executable, str(runner), str(target)])
                     for _ in range(2)]
            bad, ok = [], 0
            while any(p.poll() is None for p in procs):
                raw = target.read_text()
                body = raw.strip()
                if len(body) != 400_000 or len(set(body)) != 1:
                    bad.append(f"{len(body)} bytes, {sorted(set(body))[:3]}")
                else:
                    ok += 1
            for p in procs:
                p.wait()
            self.assertEqual(bad, [], f"{len(bad)} partial reads: {bad[:3]}")
            # Anti-vacuity floor, not a throughput bar: it only proves the
            # reader genuinely raced the writers. The old `> 20` was
            # calibrated to one machine's disk speed and flaked on faster
            # hardware where 300 writes finish before 21 reads land.
            self.assertGreaterEqual(ok, 5)


class TestFileLock(unittest.TestCase):
    """The lock exists for LOST UPDATES, which atomicity does not touch. It
    must also degrade rather than hang or crash: adjudant vaults live on
    OneDrive, iCloud Drive and SMB shares, where flock may be refused."""

    def test_serialises_concurrent_read_modify_write_cycles(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            target = tmpp / "counter.json"
            target.write_text(json.dumps({"ids": []}))
            runner = tmpp / "rmw.py"
            runner.write_text(
                "import json, os, sys, time\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                "from pathlib import Path\n"
                "from _vault_walk import atomic_write_text, file_lock\n"
                "t, tag, go = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n"
                "while not os.path.exists(go):\n"
                "    time.sleep(0.001)\n"
                "for i in range(12):\n"
                "    with file_lock(t) as locked:\n"
                "        d = json.loads(t.read_text())\n"
                "        d['ids'].append(f'{tag}-{i}')\n"
                "        atomic_write_text(t, json.dumps(d))\n")
            go = tmpp / "go"
            procs = [subprocess.Popen([sys.executable, str(runner), str(target), f"p{n}", str(go)])
                     for n in range(4)]
            go.write_text("")
            for p in procs:
                self.assertEqual(p.wait(), 0)
            ids = json.loads(target.read_text())["ids"]
            self.assertEqual(len(ids), 48, f"{48 - len(ids)} updates lost")
            self.assertEqual(len(set(ids)), 48)

    def test_a_contended_lock_times_out_instead_of_hanging(self):
        import subprocess
        import time as _time
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            target = tmpp / "held.json"
            target.write_text("{}")
            holder = tmpp / "hold.py"
            holder.write_text(
                "import sys, time\n"
                f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
                "from pathlib import Path\n"
                "from _vault_walk import file_lock\n"
                "t = Path(sys.argv[1])\n"
                "with file_lock(t) as locked:\n"
                "    assert locked\n"
                "    Path(sys.argv[2]).write_text('held')\n"
                "    time.sleep(5)\n")
            ready = tmpp / "ready"
            proc = subprocess.Popen([sys.executable, str(holder), str(target), str(ready)])
            try:
                # Bounded: if the holder never takes the lock this test must
                # fail, not hang (that is the failure mode it exists to catch).
                waited = _time.monotonic() + 10.0
                while not ready.exists():
                    if proc.poll() is not None or _time.monotonic() > waited:
                        self.fail("the holder process never acquired the lock")
                    _time.sleep(0.005)
                start = _time.monotonic()
                with file_lock(target, timeout=0.2) as locked:
                    elapsed = _time.monotonic() - start
                    self.assertFalse(locked, "the lock is held elsewhere")
                self.assertLess(elapsed, 2.0, "a contended lock must never hang")
            finally:
                proc.kill()
                proc.wait()

    def test_falls_back_to_unlocked_when_the_platform_refuses(self):
        import errno
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "deck.json"
            target.write_text("{}")

            def refuse(*_a, **_kw):
                raise OSError(errno.ENOLCK, "no locks available")

            with unittest.mock.patch.object(fcntl, "flock", refuse):
                with file_lock(target) as locked:
                    self.assertFalse(locked)   # no crash, no hang, caller proceeds

    def test_an_unwritable_directory_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "ro"
            d.mkdir()
            target = d / "deck.json"
            target.write_text("{}")
            d.chmod(0o500)
            try:
                with file_lock(target) as locked:
                    self.assertFalse(locked)
            finally:
                d.chmod(0o700)

    def test_lock_sidecar_is_hidden_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "board-data.json"
            self.assertEqual(lock_path_for(target).name, ".board-data.json.lock")
            target.write_text("{}")
            with file_lock(target) as locked:
                self.assertTrue(locked)
            # Never unlinked: deleting it races another holder onto a new inode.
            self.assertTrue(lock_path_for(target).exists())


class TestWalkSkipsWorkingDirs(unittest.TestCase):
    """archived-context/ and the remise working dirs are never walked.

    Was TestMemoryType, whose other three tests asserted the `memory` kind's
    schema shape; the kind went with its template in v3.
    """

    def test_archived_context_and_remise_dirs_skipped(self):
        from _vault_walk import walk_project
        with tempfile.TemporaryDirectory() as tmp:
            proot = Path(tmp)
            (proot / "notes").mkdir()
            (proot / "notes" / "live.md").write_text("---\ntype: note\n---\nx\n")
            for d in ("archived-context/sessions", ".adjudant-remise-preview",
                      ".adjudant-remise-backup"):
                p = proot / d
                p.mkdir(parents=True)
                (p / "buried.md").write_text("---\ntype: note\n---\nx\n")
            rels = [str(vf.rel_path) for vf in walk_project(proot)]
            self.assertTrue(any("live.md" in r for r in rels))
            self.assertFalse(any("buried.md" in r for r in rels),
                             "archived and remise working dirs must never be walked")


class TestUnownedFolders(unittest.TestCase):
    """`memory/` is walked, never graded.

    The plan called for these on `TestMemoryType`, which no longer exists:
    the memory KIND went with its template in v3 and the class was renamed
    TestWalkSkipsWorkingDirs. The memory FOLDER is a separate thing and gets
    its own class.
    """

    def test_memory_folder_is_never_schema_graded(self):
        # 69 of check's 99 failures came from grading memory/ against a schema
        # adjudant does not own. A Claude Code auto-memory note is name /
        # description / metadata.type; Obsidian's Properties editor flattens
        # metadata.type to a top-level type:, and adjudant then read the file
        # as whatever type: claimed and proposed stripping the rest.
        from _vault_walk import schema_drift, walk_project
        flattened = ("---\nname: prefers-agents-md\ndescription: a preference\n"
                     "type: project\n---\n\nbody\n")
        with tempfile.TemporaryDirectory() as tmp:
            proot = Path(tmp)
            (proot / "memory").mkdir()
            (proot / "memory" / "flattened.md").write_text(flattened)
            (proot / "notes").mkdir()
            (proot / "notes" / "ours.md").write_text(
                "---\ntype: note\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
                "---\n\nbody\n")
            report = schema_drift(list(walk_project(proot)))
            self.assertEqual(report["flagged"], 0,
                             "memory/ was graded against a schema we do not own")
            self.assertEqual(report["exempt"], 1)
            self.assertEqual(report["checked"] + report["unchecked"], 1)
            self.assertEqual([s["file"] for s in report["samples"]], [])

            # The same bytes outside memory/ are graded exactly as before: the
            # exemption is the folder, not the content, and it does not fail open.
            (proot / "notes" / "flattened.md").write_text(flattened)
            report = schema_drift(list(walk_project(proot)))
            self.assertEqual(report["flagged"], 1)
            self.assertEqual(report["exempt"], 1)
            self.assertEqual([s["file"] for s in report["samples"]],
                             ["notes/flattened.md"])

    def test_the_unowned_set_is_named_and_narrow(self):
        from _vault_walk import UNOWNED_FOLDERS, is_unowned
        self.assertEqual(UNOWNED_FOLDERS, frozenset({"memory"}))
        self.assertTrue(is_unowned(Path("memory/a.md")))
        self.assertTrue(is_unowned("memory/deep/a.md"))
        self.assertFalse(is_unowned(Path("notes/memory.md")))
        self.assertFalse(is_unowned(Path("MEMORY.md")))

class TestObsidianCliProbe(unittest.TestCase):
    """Tranche 2C: capability probe only - never a wrapper."""

    def test_probe_finds_binary_on_path(self):
        import os
        from _vault_walk import obsidian_cli_path
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "obsidian"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            before = os.environ.get("PATH", "")
            os.environ["PATH"] = tmp
            try:
                self.assertEqual(obsidian_cli_path(), str(fake))
            finally:
                os.environ["PATH"] = before

    def test_probe_absent_is_none(self):
        import os
        from _vault_walk import obsidian_cli_path
        with tempfile.TemporaryDirectory() as tmp:
            before = os.environ.get("PATH", "")
            os.environ["PATH"] = tmp
            try:
                self.assertIsNone(obsidian_cli_path())
            finally:
                os.environ["PATH"] = before


class TestScratchSkip(unittest.TestCase):
    """Finding 31: a project `scratch/` dir was walked like content, so junk
    working files skewed counts and the cost estimator."""

    def test_scratch_dir_is_never_walked(self):
        from _vault_walk import walk_project
        with tempfile.TemporaryDirectory() as tmp:
            proot = Path(tmp)
            (proot / "notes").mkdir()
            (proot / "notes" / "real.md").write_text("---\ntype: note\n---\nx\n")
            (proot / "scratch").mkdir()
            (proot / "scratch" / "junk.md").write_text("---\ntype: note\n---\nx\n")
            rels = [str(vf.rel_path) for vf in walk_project(proot)]
            self.assertTrue(any("real.md" in r for r in rels))
            self.assertFalse(any("junk.md" in r for r in rels),
                             "scratch/ must be in the walker skip set")


class TestSchemaDriftStatusShape(unittest.TestCase):
    """Finding 27: only a non-empty string status was checked, so a blank
    `status:` (parsed None), a list value, or an empty string was invisible
    drift while the literal string "null" was flagged - an inconsistent trio."""

    def _drift(self, status_block: str):
        from _vault_walk import schema_drift_for_text
        text = ("---\ntype: decision\n" + status_block +
                "created: 2026-01-01\nupdated: 2026-01-01\n---\nbody\n")
        return schema_drift_for_text(text, "decisions/x.md")

    def test_blank_status_is_flagged(self):
        d = self._drift("status:\n")
        self.assertIsNotNone(d)
        self.assertIn("status_invalid", d)

    def test_list_status_is_flagged(self):
        d = self._drift("status:\n  - active\n  - deferred\n")
        self.assertIsNotNone(d)
        self.assertIn("status_invalid", d)

    def test_empty_quoted_status_is_flagged(self):
        d = self._drift('status: ""\n')
        self.assertIsNotNone(d)
        self.assertIn("status_invalid", d)

    def test_null_string_status_stays_flagged(self):
        d = self._drift("status: null\n")
        self.assertIsNotNone(d)
        self.assertIn("status_invalid", d)

    def test_valid_status_stays_clean(self):
        self.assertIsNone(self._drift("status: active\n"))


class TestDatedStemFutureBound(unittest.TestCase):
    """Finding 19: newest_dated_stem calendar-validates but had no upper
    bound, so a future-dated session skewed days_quiet negative."""

    def test_newest_dated_stem_bounds_to_not_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "2026-01-01.md").write_text("x")
            (folder / "2029-12-31.md").write_text("x")
            self.assertEqual(
                newest_dated_stem(folder, not_after="2026-07-30"),
                "2026-01-01")

    def test_newest_dated_stem_unbounded_without_not_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "2029-12-31.md").write_text("x")
            self.assertEqual(newest_dated_stem(folder), "2029-12-31")

    def test_suggest_status_ignores_future_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pdir = _mk_project(vault, "p", sessions=["2029-12-31"])
            got = suggest_status("active", pdir, date(2026, 7, 30))
            self.assertIsNone(got["days_quiet"],
                              "a future-dated session must not count as activity")


class TestResolverHardening(unittest.TestCase):
    """Findings 26/28/29/30: the resolver accepted anything directory-shaped.
    A bare same-named dir captured every write on the fallback machine, a
    prose Home.md up-tree became "the vault", a relative OB_VAULT broke the
    same-vault invariant across cwds, and a BOM hid frontmatter entirely."""

    def test_vault_name_fallback_rejects_markerless_directory(self):
        # F26: an empty directory in a standard location must not capture.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "Documents").mkdir()
            (home / "Documents" / "MyVault").mkdir()   # bare: no marker
            code = home / "code"; code.mkdir()
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                "vault_path: /nope/missing\nvault_name: MyVault\nslug: p\nmode: project\n")
            with unittest.mock.patch("pathlib.Path.home", return_value=home):
                self.assertIsNone(resolve_vault(code))

    def test_vault_name_fallback_accepts_obsidian_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            vault = home / "Documents" / "MyVault"
            (vault / ".obsidian").mkdir(parents=True)
            code = home / "code"; code.mkdir()
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                "vault_path: /nope/missing\nvault_name: MyVault\nslug: p\nmode: project\n")
            with unittest.mock.patch("pathlib.Path.home", return_value=home):
                resolved = resolve_vault(code)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.resolve(), vault.resolve())

    def test_ob_vault_relative_path_is_rejected(self):
        # F29: a relative override returned unresolved captures a different
        # directory per cwd. It must fall through to the breadcrumb instead.
        import os
        with tempfile.TemporaryDirectory() as tmp:
            tmpp = Path(tmp)
            (tmpp / "relvault").mkdir()                 # exists relative to cwd
            real_vault = tmpp / "RealVault"
            (real_vault / ".obsidian").mkdir(parents=True)
            code = tmpp / "code"; code.mkdir()
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {real_vault}\nslug: p\nmode: project\n")
            before = os.getcwd()
            os.chdir(tmp)
            try:
                resolved = resolve_vault(code, env_vault="relvault")
            finally:
                os.chdir(before)
            self.assertEqual(resolved.resolve(), real_vault.resolve())

    def test_home_md_walk_up_requires_frontmatter_type(self):
        # F28: `type: vault-home` in prose must not make a directory the vault.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "docs"
            proj = root / "nested" / "proj"
            proj.mkdir(parents=True)
            (root / "Home.md").write_text(
                "# My notes about adjudant\n\n"
                "A vault home is marked with\ntype: vault-home\nin frontmatter.\n")
            self.assertIsNone(resolve_vault(proj))

    def test_home_md_of_type_index_is_a_vault_marker(self):
        # v3 retired the `vault-home` kind, so templates/home.md declares
        # `type: index`. A vault built from the shipped template has to stay
        # findable, and every pre-v3 vault keeps working.
        for marker in ("vault-home", "index"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "vault"
                proj = root / "nested" / "proj"
                proj.mkdir(parents=True)
                (root / "Home.md").write_text(f"---\ntype: {marker}\n---\n\n# Vault\n")
                self.assertEqual(resolve_vault(proj).resolve(), root.resolve(),
                                 f"type: {marker} did not mark the vault")

    def test_home_md_of_an_unrelated_type_is_not_a_vault_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "notes"
            proj = root / "nested" / "proj"
            proj.mkdir(parents=True)
            (root / "Home.md").write_text("---\ntype: note\n---\n\n# Home\n")
            self.assertIsNone(resolve_vault(proj))

    def test_bom_prefixed_frontmatter_parses(self):
        # F30: a BOM silently dropped the note out of every schema check
        # while Obsidian rendered it fine.
        fm, body = parse_frontmatter("﻿---\ntype: note\n---\nbody\n")
        self.assertTrue(fm.has_block)
        self.assertEqual(fm.fields.get("type"), "note")
        self.assertEqual(body, "body\n")


class TestVaultSearchRoots(unittest.TestCase):
    """OS-aware vault-root taxonomy: the same breadcrumb must find a vault on
    macOS, native Windows, and Linux/WSL. Each OS branch is exercised with a
    mocked platform, home, and filesystem probe, so the test is deterministic on
    any host."""

    def _roots(self, kind, home, subdirs=None):
        # Mock _os_kind (not sys.platform/os.name): patching os.name globally
        # would flip pathlib's flavour and make it try to build WindowsPath on
        # this host. A Windows home is passed as a plain string, so its path
        # ops and str() stay POSIX-shaped and comparable on any test host.
        import _vault_walk
        table = {str(k): [Path(p) for p in v] for k, v in (subdirs or {}).items()}

        def fake_safe_subdirs(parent):
            return table.get(str(parent), [])

        with unittest.mock.patch.object(_vault_walk, "_os_kind", lambda: kind), \
             unittest.mock.patch.object(_vault_walk, "_safe_subdirs", fake_safe_subdirs):
            return [str(p) for p in _vault_walk._vault_search_roots(home=Path(home))]

    def test_macos_offers_icloud_and_cloudstorage(self):
        roots = self._roots(
            "macos", "/Users/x",
            {"/Users/x/Library/CloudStorage": ["/Users/x/Library/CloudStorage/OneDrive-Acme"]})
        self.assertIn("/Users/x/Library/Mobile Documents/iCloud~md~obsidian/Documents", roots)
        self.assertIn("/Users/x/Library/Mobile Documents/com~apple~CloudDocs", roots)
        self.assertIn("/Users/x/Library/CloudStorage/OneDrive-Acme", roots)
        self.assertIn("/Users/x/Dropbox", roots)
        self.assertIn("/Users/x/Documents", roots)
        self.assertEqual(roots[-1], "/Users/x")   # bare home is the last resort

    def test_windows_offers_onedrive_variants(self):
        home = "C:/Users/x"
        roots = self._roots(
            "windows", home,
            {home: [f"{home}/OneDrive - Acme Corp", f"{home}/Desktop"]})
        self.assertIn(f"{home}/OneDrive", roots)
        self.assertIn(f"{home}/OneDrive - Acme Corp", roots)   # per-org business OneDrive
        self.assertNotIn(f"{home}/Desktop", roots)             # only OneDrive - * is lifted
        self.assertIn(f"{home}/iCloudDrive", roots)
        self.assertIn(f"{home}/Dropbox", roots)
        self.assertEqual(roots[-1], home)

    def test_wsl_sees_windows_side_mounts(self):
        roots = self._roots(
            "linux", "/home/x",
            {"/mnt/c/Users": ["/mnt/c/Users/tom"],
             "/mnt/c/Users/tom": ["/mnt/c/Users/tom/OneDrive - Acme"]})
        self.assertIn("/home/x/Dropbox", roots)                # native Linux root
        self.assertIn("/mnt/c/Users/tom/OneDrive", roots)      # Windows drive under WSL
        self.assertIn("/mnt/c/Users/tom/OneDrive - Acme", roots)
        self.assertIn("/mnt/c/Users/tom/Dropbox", roots)
        self.assertEqual(roots[-1], "/home/x")

    def test_candidate_paths_pair_root_and_obsidian_subfolder(self):
        import _vault_walk
        with unittest.mock.patch.object(
                _vault_walk, "_vault_search_roots", lambda: [Path("/root")]):
            cands = [str(p) for p in _vault_walk._candidate_vault_paths("MyVault")]
        self.assertEqual(cands, ["/root/MyVault", "/root/Obsidian/MyVault"])

    def test_safe_subdirs_never_raises_on_missing(self):
        import _vault_walk
        self.assertEqual(_vault_walk._safe_subdirs(Path("/no/such/path/ever")), [])


class TestSuggestVaultRoots(unittest.TestCase):

    def test_returns_only_existing_dirs_with_labels(self):
        import _vault_walk
        roots = _vault_walk.suggest_vault_roots()
        for entry in roots:
            self.assertTrue(Path(entry["path"]).is_dir(), entry["path"])
            self.assertTrue(entry["label"])
            self.assertIn(entry["kind"], ("local", "cloud"))
            self.assertIsInstance(entry["recommended"], bool)

    def test_no_duplicate_paths(self):
        import _vault_walk
        paths = [e["path"] for e in _vault_walk.suggest_vault_roots()]
        self.assertEqual(len(paths), len(set(paths)))

    def test_cloud_roots_are_recommended_and_home_is_not(self):
        import _vault_walk
        home = str(Path.home())
        for entry in _vault_walk.suggest_vault_roots():
            if entry["path"] == home:
                self.assertFalse(entry["recommended"])
                self.assertEqual(entry["kind"], "local")


class TestSchemaIsDerived(unittest.TestCase):
    """The templates are the schema; this module only re-exports it.

    Before v3 a kind's shape was declared twice, as a Python constant here and
    as the template file a writer copies, and validators existed to check the
    two agreed. One declaration cannot disagree with itself.
    """

    def test_vault_walk_reexports_the_template_schema(self):
        import _template_schema
        import _vault_walk
        self.assertIs(_vault_walk.FIELD_SCHEMA, _template_schema.FIELD_SCHEMA)
        self.assertIs(_vault_walk.STATUS_VALUES_FOR_TYPE,
                      _template_schema.STATUS_VALUES_FOR_TYPE)

    def test_no_hand_written_field_schema_remains(self):
        # A literal FIELD_SCHEMA dict in this file would be the second
        # declaration this plan exists to remove.
        import _vault_walk
        src = Path(_vault_walk.__file__).read_text()
        self.assertNotIn("FIELD_SCHEMA: dict[str, dict[str, frozenset[str]]] = {", src)
        for gone in ("DECISION_STATUS_VALUES", "TASK_STATUS_VALUES",
                     "ITERATION_STATUS_VALUES", "_EPISTEMIC_OPTIONAL",
                     "FRESHNESS_VALUES", "MEMORY_HEADINGS",
                     "PROJECT_STATUS_VALUES"):
            self.assertNotIn(f"{gone}:", src, f"{gone} survived")

    def test_the_retired_kinds_are_absent(self):
        import _vault_walk
        for gone in ("memory", "iteration", "dream-report", "vault-home"):
            self.assertNotIn(gone, _vault_walk.FIELD_SCHEMA)

    def test_headings_are_re_exported_too(self):
        import _template_schema
        import _vault_walk
        self.assertIs(_vault_walk.HEADINGS_FOR_TYPE,
                      _template_schema.HEADINGS_FOR_TYPE)


class TestLegacyBreadcrumbIsNotResolved(unittest.TestCase):
    """The retired obsidian-bridge breadcrumb stops being a resolution step.

    Its only migration partner was port.py, deleted in v3, so a resolved legacy
    path led nowhere: adjudant would quietly work from a stale vault the user
    was never told about. Reporting it is strictly more useful than silently
    honouring it, and it is one of the differences keeping this module forked
    between the two builds.
    """

    def _legacy_project(self, tmp: Path):
        vault = tmp / "OldVault"
        (vault / "projects").mkdir(parents=True)
        (vault / "Home.md").write_text("---\ntype: vault-home\n---\n\n# Home\n")
        project = tmp / "code"
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "obsidian-bridge").write_text(
            f"vault: {vault}\nslug: legacy-proj\n")
        return project, vault

    def test_legacy_breadcrumb_alone_resolves_to_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _vault = self._legacy_project(Path(tmp))
            self.assertIsNone(resolve_vault(project))

    def test_an_adjudant_breadcrumb_still_wins_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _old = self._legacy_project(root)
            new_vault = root / "NewVault"
            (new_vault / "projects").mkdir(parents=True)
            (new_vault / "Home.md").write_text("---\ntype: vault-home\n---\n\n# Home\n")
            (project / ".claude" / "adjudant").write_text(
                f"vault_path: {new_vault}\nvault_name: NewVault\nslug: demo\n")
            self.assertEqual(resolve_vault(project), new_vault)

    def test_the_docstring_offers_four_steps_and_disclaims_the_fifth(self):
        # The docstring is the contract readers trust; a five-step docstring
        # over a four-step function is the drift this whole plan removes.
        # The numbered list is the contract: the prose under it is allowed to
        # name the retired breadcrumb, because saying it is not a step is the
        # whole point of keeping the paragraph.
        doc = resolve_vault.__doc__
        self.assertIn("4-step resolution:", doc)
        steps = doc.split("\n\n")[0]
        self.assertNotIn("obsidian-bridge", steps)
        self.assertIn("is NOT a resolution step", doc)


if __name__ == "__main__":
    unittest.main()
