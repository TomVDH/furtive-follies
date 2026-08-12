"""Tests for adjudant/scripts/connect.py."""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from connect import (
    VALID_PROJECT_TYPES,
    append_gitignore,
    build_contract,
    count_non_index_files,
    derive_project_name,
    derive_project_type,
    detect_state,
    infer_initial_status,
    infer_project_type,
    newest_session_date,
    provision_context_files,
    resolve_vault_for_connect,
    run_connect,
    scaffold_vault_project,
    slug_to_title,
    upsert_projects_index_row,
    validate_slug,
    write_breadcrumb,
    write_session_note,
)
from connect import cli_main as connect_cli


def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _make_vault(tmp: Path) -> Path:
    vault = Path(tmp) / "test-vault"
    vault.mkdir()
    (vault / "Home.md").write_text("---\ntype: vault-home\n---\n# Home\n")
    (vault / "projects").mkdir()
    return vault


# ============================================================
# Slug helpers
# ============================================================


class TestSlugHelpers(unittest.TestCase):

    def test_valid_slugs(self):
        for slug in ["my-project", "abc123", "one"]:
            self.assertIsNone(validate_slug(slug))

    def test_invalid_slugs(self):
        for slug in ["UpperCase", "with space", "-leading", "", "with.dot"]:
            self.assertIsNotNone(validate_slug(slug))

    def test_title_case(self):
        self.assertEqual(slug_to_title("my-cool-project"), "My Cool Project")
        self.assertEqual(slug_to_title("abc"), "Abc")


# ============================================================
# Vault resolution
# ============================================================


class TestResolveVault(unittest.TestCase):

    def test_explicit_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"; proj.mkdir()
            vault = Path(tmp) / "vault"; vault.mkdir()
            (vault / "Home.md").write_text("---\ntype: vault-home\n---\n")
            resolved = resolve_vault_for_connect(proj, str(vault), None)
            self.assertEqual(resolved, vault)

    def test_walk_up_for_home_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            (vault / "projects" / "x").mkdir()
            resolved = resolve_vault_for_connect(vault / "projects" / "x", None, None)
            self.assertEqual(resolved.resolve(), vault.resolve())

    def test_ob_vault_env_honored(self):
        """reference/connect.md lists OB_VAULT in the resolution order — the
        function ignored it entirely (regression)."""
        import os
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"; proj.mkdir()
            vault = Path(tmp) / "envvault"; vault.mkdir()
            os.environ["OB_VAULT"] = str(vault)
            try:
                resolved = resolve_vault_for_connect(proj, None, None)
            finally:
                del os.environ["OB_VAULT"]
            self.assertEqual(resolved, vault)

    def test_explicit_path_beats_ob_vault_env(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"; proj.mkdir()
            env_vault = Path(tmp) / "envvault"; env_vault.mkdir()
            arg_vault = Path(tmp) / "argvault"; arg_vault.mkdir()
            os.environ["OB_VAULT"] = str(env_vault)
            try:
                resolved = resolve_vault_for_connect(proj, str(arg_vault), None)
            finally:
                del os.environ["OB_VAULT"]
            self.assertEqual(resolved, arg_vault)


# ============================================================
# Step 1: breadcrumb
# ============================================================


class TestWriteBreadcrumb(unittest.TestCase):

    def test_writes_correct_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            mark = write_breadcrumb(proj, Path("/v"), "Vault", "my-slug")
            self.assertEqual(mark, "created")
            content = (proj / ".claude" / "adjudant").read_text()
            self.assertIn("vault_path: /v", content)
            self.assertIn("vault_name: Vault", content)
            self.assertIn("slug: my-slug", content)
            self.assertIn("mode: project", content)

    def test_identical_rewrite_is_already_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            write_breadcrumb(proj, Path("/v"), "Vault", "my-slug")
            mark = write_breadcrumb(proj, Path("/v"), "Vault", "my-slug")
            self.assertEqual(mark, "already-present")

    def test_changed_content_is_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            write_breadcrumb(proj, Path("/v"), "Vault", "my-slug")
            mark = write_breadcrumb(proj, Path("/other"), "Vault", "my-slug")
            self.assertEqual(mark, "updated")
            content = (proj / ".claude" / "adjudant").read_text()
            self.assertIn("vault_path: /other", content)

    def test_stamp_opt_in_preserved_on_rewrite(self):
        # v0.16.0: a hand-added stamp_source_session opt-in must survive
        # re-connect; connect itself never turns stamping on.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            write_breadcrumb(proj, Path("/v"), "Vault", "my-slug")
            bc = proj / ".claude" / "adjudant"
            bc.write_text(bc.read_text() + "stamp_source_session: true\n")
            mark = write_breadcrumb(proj, Path("/other"), "Vault", "my-slug")
            self.assertEqual(mark, "updated")
            self.assertIn("stamp_source_session: true", bc.read_text())

    def test_unknown_keys_survive_reconnect(self):
        # Audit 2026-07-27 finding 16: preservation was a hardcoded allowlist,
        # so any hand-added or future key was dropped on every re-connect.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            write_breadcrumb(proj, Path("/v"), "Vault", "my-slug")
            bc = proj / ".claude" / "adjudant"
            bc.write_text(bc.read_text()
                          + "handoff_style: terse\nmy_future_key: 42\n")
            write_breadcrumb(proj, Path("/other"), "Vault", "my-slug")
            text = bc.read_text()
            self.assertIn("handoff_style: terse", text)
            self.assertIn("my_future_key: 42", text)
            self.assertIn("vault_path: /other", text)

    def test_stamp_key_absent_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            write_breadcrumb(proj, Path("/v"), "Vault", "my-slug")
            self.assertNotIn("stamp_source_session",
                             (proj / ".claude" / "adjudant").read_text())


# ============================================================
# Step 2: context files
# ============================================================


class TestProvisionContextFiles(unittest.TestCase):

    def test_creates_if_missing(self):
        # Note: this test uses the real templates dir under adjudant/skills/.../templates
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            result = provision_context_files(proj)
            # If templates exist, both files should be created
            self.assertIn(result["AGENTS.md"], ("created", "template missing"))
            self.assertIn(result["CLAUDE.md"], ("created", "template missing"))

    def test_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "AGENTS.md").write_text("user content")
            (proj / "CLAUDE.md").write_text("user content")
            result = provision_context_files(proj)
            self.assertEqual(result["AGENTS.md"], "preserved")
            self.assertEqual(result["CLAUDE.md"], "preserved")
            self.assertEqual((proj / "AGENTS.md").read_text(), "user content")


# ============================================================
# Step 3: vault scaffold
# ============================================================


class TestScaffoldVaultProject(unittest.TestCase):

    def test_coding_project_creates_default_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            result = scaffold_vault_project(vault, "my-slug", "coding", "My Slug", "2026-05-27")
            proj_dir = vault / "projects" / "my-slug"
            self.assertTrue((proj_dir / "brief.md").is_file())
            for sub in ["decisions", "notes", "tasks", "references", "sessions", "images"]:
                self.assertTrue((proj_dir / sub).is_dir(), f"{sub} missing")
            # Index-required folders have _index.md, exempt ones don't
            self.assertTrue((proj_dir / "decisions" / "_index.md").is_file())
            self.assertFalse((proj_dir / "sessions" / "_index.md").is_file())
            self.assertFalse((proj_dir / "images" / "_index.md").is_file())

    def test_plugin_project_includes_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "p", "plugin", "P", "2026-05-27")
            self.assertTrue((vault / "projects" / "p" / "releases").is_dir())
            self.assertTrue((vault / "projects" / "p" / "releases" / "_index.md").is_file())

    def test_brief_has_slug_and_date_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "abc", "coding", "Abc Project", "2026-05-27")
            brief = (vault / "projects" / "abc" / "brief.md").read_text()
            self.assertIn("slug: abc", brief)
            self.assertIn("2026-05-27", brief)
            self.assertIn("# Abc Project", brief)
            # No placeholder leftovers
            self.assertNotIn("{kebab-slug}", brief)
            self.assertNotIn("{YYYY-MM-DD}", brief)

    def test_idempotent_preserves_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "abc", "coding", "Abc", "2026-05-27")
            brief_path = vault / "projects" / "abc" / "brief.md"
            brief_path.write_text("USER EDITED")
            scaffold_vault_project(vault, "abc", "coding", "Abc 2", "2026-05-28")
            self.assertEqual(brief_path.read_text(), "USER EDITED")


# ============================================================
# Step 4: session note
# ============================================================


class TestWriteSessionNote(unittest.TestCase):

    def test_creates(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            (vault / "projects" / "x").mkdir()
            r = write_session_note(vault, "x", "2026-05-27", "09:30")
            self.assertEqual(r, "created")
            self.assertTrue((vault / "projects" / "x" / "sessions" / "2026-05-27.md").is_file())

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            write_session_note(vault, "x", "2026-05-27", "09:30")
            r = write_session_note(vault, "x", "2026-05-27", "10:30")
            self.assertEqual(r, "preserved")


# ============================================================
# Step 5: gitignore
# ============================================================


class TestAppendGitignore(unittest.TestCase):

    def test_creates_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = append_gitignore(Path(tmp))
            self.assertEqual(r, "created")
            self.assertIn(".claude/adjudant", (Path(tmp) / ".gitignore").read_text())

    def test_appends_if_missing_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".gitignore").write_text("existing\n")
            r = append_gitignore(Path(tmp))
            self.assertEqual(r, "added")
            content = (Path(tmp) / ".gitignore").read_text()
            self.assertIn("existing", content)
            self.assertIn(".claude/adjudant", content)

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".gitignore").write_text(".claude/adjudant\n")
            r = append_gitignore(Path(tmp))
            self.assertEqual(r, "preserved")


# ============================================================
# Step 6: projects/_index.md row upsert
# ============================================================


class TestUpsertProjectsIndexRow(unittest.TestCase):

    def test_creates_index_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            r = upsert_projects_index_row(vault, "x", "coding", "active", 0, 0, "—")
            self.assertEqual(r, "created-index")
            text = (vault / "projects" / "_index.md").read_text()
            self.assertIn("x/brief", text)

    def test_inserts_if_index_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            upsert_projects_index_row(vault, "a", "coding", "active", 0, 0, "—")
            r = upsert_projects_index_row(vault, "b", "plugin", "active", 1, 2, "2026-05-27")
            self.assertEqual(r, "inserted")
            text = (vault / "projects" / "_index.md").read_text()
            self.assertIn("a/brief", text)
            self.assertIn("b/brief", text)

    def test_hand_maintained_index_is_never_corrupted(self):
        # Audit 2026-07-27 finding 13: the row was inserted after the FIRST
        # `|---|` anywhere in the file, so it landed inside an unrelated table
        # in a hand-curated index. port.py already guarded against this;
        # connect, sync and shelf all used this unguarded copy.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            idx = vault / "projects" / "_index.md"
            custom = (
                "---\ntype: index\ntags:\n  - index\n---\n\n"
                "# Projects by quarter\n\n"
                "| Quarter | Theme |\n"
                "|---------|-------|\n"
                "| Q1 | Platform |\n"
            )
            idx.write_text(custom)
            r = upsert_projects_index_row(vault, "x", "coding", "active", 0, 0, "—")
            self.assertEqual(r, "skipped-unknown-format")
            self.assertEqual(idx.read_text(), custom,
                             "a custom index must be left byte-identical")

    def test_canonical_index_still_takes_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            idx = vault / "projects" / "_index.md"
            idx.write_text(
                "---\ntype: index\ntags:\n  - index\n---\n\n"
                "# All Projects\n\n"
                "| Project | Type | Status | Decisions | Sessions | Last Session |\n"
                "|---------|------|--------|-----------|----------|--------------|\n")
            r = upsert_projects_index_row(vault, "x", "coding", "active", 0, 0, "—")
            self.assertEqual(r, "inserted")
            self.assertIn("x/brief", idx.read_text())

    def test_updates_existing_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            upsert_projects_index_row(vault, "a", "coding", "active", 0, 0, "—")
            r = upsert_projects_index_row(vault, "a", "coding", "active", 5, 10, "2026-05-27")
            self.assertEqual(r, "updated")
            text = (vault / "projects" / "_index.md").read_text()
            # Row contains updated counts + last_session, with wikilink form preserved
            self.assertIn("coding | active | 5 | 10 | 2026-05-27", text)
            # And the old "0 | 0" row is gone
            self.assertNotIn("active | 0 | 0 | —", text)


class TestUpsertReplacementIsConfinedToTheCanonicalTable(unittest.TestCase):
    """Fix wave 1 finding 4: finding 13's remediation guarded only the INSERT
    path. The row-REPLACEMENT path still matched `| [[slug/brief\\|` on ANY
    line, so a row for that slug living inside the user's own hand-maintained
    table was overwritten with the canonical 6-column row.
    """

    _HAND_ROW = "| [[demo/brief\\|demo]] | my own annotation |"

    def test_hand_maintained_row_is_not_overwritten(self):
        # No canonical table anywhere: nothing may be written at all.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            idx = vault / "projects" / "_index.md"
            custom = (
                "---\ntype: index\ntags:\n  - index\n---\n\n"
                "# Reading notes\n\n"
                "| Source | Note |\n"
                "|--------|------|\n"
                + self._HAND_ROW + "\n"
            )
            idx.write_text(custom)
            r = upsert_projects_index_row(vault, "demo", "coding", "done", 3, 4, "2026-07-01")
            self.assertEqual(r, "skipped-unknown-format")
            self.assertEqual(idx.read_text(), custom,
                             "a hand-maintained table must be left byte-identical")

    def test_canonical_row_updates_while_hand_row_survives(self):
        # Both tables in one file: the canonical row must still be refreshed,
        # and only that one.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            idx = vault / "projects" / "_index.md"
            idx.write_text(
                "---\ntype: index\ntags:\n  - index\n---\n\n"
                "# Reading notes\n\n"
                "| Source | Note |\n"
                "|--------|------|\n"
                + self._HAND_ROW + "\n\n"
                "# All Projects\n\n"
                "| Project | Type | Status | Decisions | Sessions | Last Session |\n"
                "|---------|------|--------|-----------|----------|--------------|\n"
                "| [[demo/brief\\|demo]] | coding | active | 0 | 0 | — |\n")
            r = upsert_projects_index_row(vault, "demo", "coding", "done", 3, 4, "2026-07-01")
            self.assertEqual(r, "updated")
            text = idx.read_text()
            self.assertIn(self._HAND_ROW, text,
                          "the user's own row must survive verbatim")
            self.assertIn("coding | done | 3 | 4 | 2026-07-01", text,
                          "the canonical row must still be refreshed")
            self.assertNotIn("active | 0 | 0 | —", text)
            self.assertEqual(text.count("my own annotation"), 1)

    def test_row_below_the_canonical_table_is_not_touched(self):
        # A second table AFTER the canonical one: the span must end at the
        # canonical table's last row, not run to end of file.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            idx = vault / "projects" / "_index.md"
            idx.write_text(
                "---\ntype: index\ntags:\n  - index\n---\n\n"
                "# All Projects\n\n"
                "| Project | Type | Status | Decisions | Sessions | Last Session |\n"
                "|---------|------|--------|-----------|----------|--------------|\n"
                "| [[other/brief\\|other]] | coding | active | 0 | 0 | — |\n\n"
                "# Reading notes\n\n"
                "| Source | Note |\n"
                "|--------|------|\n"
                + self._HAND_ROW + "\n")
            r = upsert_projects_index_row(vault, "demo", "coding", "done", 3, 4, "2026-07-01")
            self.assertEqual(r, "inserted",
                             "the slug is absent from the canonical table, so insert")
            text = idx.read_text()
            self.assertIn(self._HAND_ROW, text,
                          "the trailing hand-maintained row must survive verbatim")
            self.assertIn("| [[demo/brief\\|demo]] | coding | done | 3 | 4 | 2026-07-01 |", text)


# ============================================================
# End-to-end run_connect
# ============================================================


class TestRunConnectEndToEnd(unittest.TestCase):

    def test_fresh_connect_produces_all_artefacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "my-project"; proj.mkdir()
            vault = _make_vault(tmp)
            summary = run_connect(
                project_root=proj,
                vault_path=vault,
                vault_name="test-vault",
                slug="my-project",
                project_type="coding",
                project_name="My Project",
                today="2026-05-27",
                now_hhmm="10:00",
            )
            # Breadcrumb
            self.assertTrue((proj / ".claude" / "adjudant").is_file())
            # Vault scaffold
            self.assertTrue((vault / "projects" / "my-project" / "brief.md").is_file())
            self.assertTrue((vault / "projects" / "my-project" / "decisions" / "_index.md").is_file())
            # Session note
            self.assertTrue((vault / "projects" / "my-project" / "sessions" / "2026-05-27.md").is_file())
            # .gitignore
            self.assertIn(".claude/adjudant", (proj / ".gitignore").read_text())
            # Projects index row
            self.assertIn("my-project", (vault / "projects" / "_index.md").read_text())

    def test_reconnect_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "p"; proj.mkdir()
            vault = _make_vault(tmp)
            for _ in range(2):
                run_connect(proj, vault, "v", "p", "coding", "P", "2026-05-27", "10:00")
            self.assertEqual(detect_state(proj, vault, "p"), "connected")


# ============================================================
# Counts + dates
# ============================================================


class TestCounts(unittest.TestCase):

    def test_count_non_index_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "_index.md").write_text("idx")
            (d / "a.md").write_text("a")
            (d / "b.md").write_text("b")
            (d / "c.txt").write_text("c")
            self.assertEqual(count_non_index_files(d), 2)

    def test_newest_session_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for date in ["2026-05-25", "2026-05-27", "2026-05-26"]:
                (d / f"{date}.md").write_text("x")
            self.assertEqual(newest_session_date(d), "2026-05-27")


# ============================================================
# Contract inference
# ============================================================


class TestInference(unittest.TestCase):

    def test_plugin_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text("{}")
            ptype, signal = infer_project_type(root)
            self.assertEqual(ptype, "plugin")
            self.assertIn("plugin.json", signal)

    def test_coding_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('x')")
            self.assertEqual(infer_project_type(root)[0], "coding")

    def test_knowledge_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(4):
                (root / f"n{i}.md").write_text("# note")
            self.assertEqual(infer_project_type(root)[0], "knowledge")

    def test_tinkerage_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(infer_project_type(Path(tmp))[0], "tinkerage")

    def test_initial_status_seed_when_nearly_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("x")
            self.assertEqual(infer_initial_status(root)[0], "seed")

    def test_initial_status_active_otherwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.py", "b.py", "c.md", "d.md"):
                (root / name).write_text("x")
            self.assertEqual(infer_initial_status(root)[0], "active")


class TestContract(unittest.TestCase):

    def test_contract_shape_and_artifact_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            code = root / "my-proj"
            code.mkdir()
            (code / "AGENTS.md").write_text("# existing")
            contract = build_contract(
                project_root=code, vault_path=vault, vault_name="vault",
                slug="my-proj", project_type="coding", type_signal="test",
                initial_status="active", status_signal="test", purpose=None)
            self.assertEqual(
                set(contract["required"]),
                {"vault", "vault_name", "slug", "project_type",
                 "initial_status", "purpose"})
            states = {a["artifact"]: a["state"] for a in contract["artifacts"]}
            self.assertEqual(states["AGENTS.md"], "already-present")
            self.assertEqual(states["GEMINI.md"], "will-create")
            self.assertEqual(states["vault scaffold"], "will-create")
            self.assertEqual(contract["state"], "fresh")

    def test_contract_cli_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            code = root / "proj"
            code.mkdir()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = connect_cli([
                    "--project-root", str(code), "--vault-path", str(vault),
                    "--contract"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("contract", payload)
            self.assertFalse((code / ".claude" / "adjudant").exists())
            self.assertFalse((vault / "projects" / "proj").exists())


from connect import build_receipt


class TestApplyContract(unittest.TestCase):

    def _connect(self, root: Path, vault: Path, extra: list = ()) -> dict:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = connect_cli([
                "--project-root", str(root), "--vault-path", str(vault),
                "--slug", "proj", "--project-type", "coding",
                *extra])
        assert rc == 0, buf.getvalue()
        return json.loads(buf.getvalue())

    def test_gemini_md_created_and_breadcrumb_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            code = root / "proj"; code.mkdir()
            summary = self._connect(code, vault)
            self.assertTrue((code / "GEMINI.md").is_file())
            bc = (code / ".claude" / "adjudant").read_text()
            self.assertIn("cost_warn_tokens: 10000", bc)
            self.assertIn("stale_after_days: 30", bc)
            self.assertIn("receipt", summary)
            states = {r["artifact"]: r["state"] for r in summary["receipt"]}
            self.assertEqual(states["GEMINI.md"], "created")

    def test_breadcrumb_overrides_preserved_on_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            code = root / "proj"; code.mkdir()
            self._connect(code, vault)
            bc_path = code / ".claude" / "adjudant"
            bc_path.write_text(bc_path.read_text().replace(
                "cost_warn_tokens: 10000", "cost_warn_tokens: 99000"))
            self._connect(code, vault)
            self.assertIn("cost_warn_tokens: 99000", bc_path.read_text())

    def test_purpose_and_initial_status_land(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            code = root / "proj"; code.mkdir()
            self._connect(code, vault,
                          extra=["--purpose", "Track the garden irrigation build.",
                                 "--initial-status", "seed"])
            agents = (code / "AGENTS.md").read_text()
            self.assertIn("> Track the garden irrigation build.", agents)
            self.assertNotIn("{Project Name}", agents)
            self.assertNotIn("{slug}", agents)
            brief = (vault / "projects" / "proj" / "brief.md").read_text()
            self.assertIn("status: seed", brief)
            self.assertIn("Track the garden irrigation build.", brief)

    def test_receipt_names_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            code = root / "proj"; code.mkdir()
            summary = self._connect(code, vault)  # coding type
            self.assertIn("/adjudant board", json.dumps(summary["receipt"]))
            # knowledge projects have no tasks/ folder, so no board pointer
            know = root / "know"; know.mkdir()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = connect_cli([
                    "--project-root", str(know), "--vault-path", str(vault),
                    "--slug", "know", "--project-type", "knowledge"])
            assert rc == 0, buf.getvalue()
            summary2 = json.loads(buf.getvalue())
            self.assertNotIn("/adjudant board", json.dumps(summary2["receipt"]))

    def test_reconnect_receipt_all_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            code = root / "proj"; code.mkdir()
            self._connect(code, vault)
            summary = self._connect(code, vault)
            states = {r["artifact"]: r["state"] for r in summary["receipt"]}
            self.assertEqual(states["AGENTS.md"], "already-present")
            self.assertEqual(states["GEMINI.md"], "already-present")
            self.assertEqual(states["session note"], "already-present")
            self.assertEqual(states[".claude/adjudant"], "already-present")


# ============================================================
# Zone-awareness (v0.14.0): re-connecting a shelved project
# ============================================================


class TestZoneAwareReconnect(unittest.TestCase):

    def _fridge_project(self, vault: Path, slug: str = "p") -> Path:
        """A fully-scaffolded project already living in the _fridge zone
        (mirrors what a prior connect + shelf would have produced)."""
        proj_dir = vault / "projects" / "_fridge" / slug
        scaffold_vault_project(
            vault, slug, "coding", slug.title(), "2026-05-27",
            initial_status="fridge", proj_dir=proj_dir)
        write_session_note(vault, slug, "2026-05-27", "09:00", proj_dir=proj_dir)
        return proj_dir

    def test_contract_on_fridged_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            self._fridge_project(vault, "p")
            code = root / "p"; code.mkdir()
            (code / ".claude").mkdir()
            (code / ".claude" / "adjudant").write_text(
                f"vault_path: {vault}\nvault_name: vault\nslug: p\nmode: project\n"
            )
            contract = build_contract(
                project_root=code, vault_path=vault, vault_name="vault",
                slug="p", project_type="coding", type_signal="test",
                initial_status="active", status_signal="test", purpose=None)
            self.assertEqual(contract["state"], "connected")
            self.assertEqual(contract["zone"], "_fridge")
            states = {a["artifact"]: a["state"] for a in contract["artifacts"]}
            self.assertEqual(states["vault scaffold"], "already-present")

    def test_reconnect_does_not_fork_a_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            self._fridge_project(vault, "p")
            code = root / "p"; code.mkdir()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = connect_cli([
                    "--project-root", str(code), "--vault-path", str(vault),
                    "--slug", "p", "--project-type", "coding"])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            # No duplicate forked into the live zone
            self.assertFalse((vault / "projects" / "p").exists())
            # The fridged project is still exactly one dir
            self.assertTrue((vault / "projects" / "_fridge" / "p" / "brief.md").is_file())
            states = {r["artifact"]: r["state"] for r in summary["receipt"]}
            self.assertEqual(states["vault scaffold"], "already-present")

    def test_session_note_lands_in_zoned_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            self._fridge_project(vault, "p")
            code = root / "p"; code.mkdir()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = connect_cli([
                    "--project-root", str(code), "--vault-path", str(vault),
                    "--slug", "p", "--project-type", "coding"])
            self.assertEqual(rc, 0)
            today = datetime.now().strftime("%Y-%m-%d")
            sess_dir = vault / "projects" / "_fridge" / "p" / "sessions"
            self.assertTrue((sess_dir / f"{today}.md").is_file())
            self.assertFalse((vault / "projects" / "p" / "sessions").exists())

    def test_zoned_brief_drives_project_type_on_reconnect(self):
        """A fridged brief declaring project_type plugin must win over
        re-inference when --project-type is omitted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            proj_dir = vault / "projects" / "_fridge" / "p"
            scaffold_vault_project(
                vault, "p", "plugin", "P", "2026-05-27",
                initial_status="fridge", proj_dir=proj_dir)
            code = root / "p"; code.mkdir()
            # A code file would make infer_project_type() say "coding"
            (code / "main.py").write_text("print('x')")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = connect_cli([
                    "--project-root", str(code), "--vault-path", str(vault),
                    "--slug", "p"])
            self.assertEqual(rc, 0)
            summary = json.loads(buf.getvalue())
            self.assertEqual(summary["project_type"], "plugin")


class TestProvisionDashboards(unittest.TestCase):
    """Tranche 2B: connect provisions the four .base dashboards into
    {project}/bases/, write-if-absent, slug templated, edits never clobbered."""

    def _connect(self, root: Path, vault: Path) -> dict:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = connect_cli([
                "--project-root", str(root), "--vault-path", str(vault),
                "--slug", "proj", "--project-type", "coding"])
        assert rc == 0, buf.getvalue()
        return json.loads(buf.getvalue())

    def test_dashboards_written_with_slug_templated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"; vault.mkdir()
            self._connect(root, vault)
            bases = vault / "projects" / "proj" / "bases"
            names = sorted(p.name for p in bases.glob("dashboard-*.base"))
            self.assertEqual(names, ["dashboard-decisions.base",
                                     "dashboard-freshness.base",
                                     "dashboard-sessions.base",
                                     "dashboard-tasks.base"])
            text = (bases / "dashboard-sessions.base").read_text()
            self.assertIn('file.inFolder("projects/proj/sessions")', text)
            self.assertNotIn("{slug}", text)

    def test_edited_dashboard_never_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"; vault.mkdir()
            self._connect(root, vault)
            target = vault / "projects" / "proj" / "bases" / "dashboard-tasks.base"
            target.write_text("filters: 'status == \"done\"'\n# my edit\n")
            self._connect(root, vault)   # idempotent re-run
            self.assertIn("# my edit", target.read_text())


class TestCreateVaultFlag(unittest.TestCase):
    """--create-vault makes a new vault; --suggest-vaults lists locations."""

    def test_create_vault_makes_and_scaffolds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = root / "proj"; code.mkdir()
            new_vault = root / "Fresh Vault"
            rc = connect_cli([
                "--project-root", str(code),
                "--vault-path", str(new_vault),
                "--create-vault",
                "--slug", "demo",
                "--project-type", "coding",
                "--purpose", "test create vault",
                "--initial-status", "seed",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(new_vault.is_dir())
            self.assertTrue((new_vault / "projects" / "demo" / "brief.md").is_file())

    def test_suggest_vaults_prints_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = connect_cli(["--suggest-vaults"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("vault_roots", data)
        self.assertIsInstance(data["vault_roots"], list)


if __name__ == "__main__":
    unittest.main()
