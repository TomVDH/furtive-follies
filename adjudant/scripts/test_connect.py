"""Tests for adjudant/scripts/connect.py."""

import contextlib
import io
import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from _cost import DEFAULT_WARN_TOKENS
from connect import (
    VALID_PROJECT_TYPES,
    append_gitignore,
    build_contract,
    derive_project_name,
    derive_project_type,
    detect_state,
    infer_initial_status,
    infer_project_type,
    provision_context_files,
    resolve_vault_for_connect,
    run_connect,
    scaffold_vault_project,
    slug_to_title,
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
    """v3: a folder exists when something is in it. connect used to create
    four to seven folders up front and drop an empty _index.md into each,
    which produced fifteen index files with a body under 25 bytes."""

    def test_creates_the_project_dir_and_the_brief_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "my-slug", "coding", "My Slug", "2026-05-27")
            proj_dir = vault / "projects" / "active" / "my-slug"
            self.assertTrue((proj_dir / "brief.md").is_file())
            self.assertEqual([p.name for p in proj_dir.iterdir()], ["brief.md"])

    def test_no_index_file_is_written_anywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "p", "plugin", "P", "2026-05-27")
            self.assertEqual(list(vault.rglob("_index.md")), [])

    def test_new_projects_land_in_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "p", "coding", "P", "2026-05-27")
            self.assertTrue((vault / "projects" / "active" / "p" / "brief.md").is_file())
            self.assertFalse((vault / "projects" / "p").exists())

    def test_brief_has_slug_and_date_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "abc", "coding", "Abc Project", "2026-05-27")
            brief = (vault / "projects" / "active" / "abc" / "brief.md").read_text()
            self.assertIn("2026-05-27", brief)
            self.assertIn("# Abc Project", brief)
            # v3 dropped slug: and aliases: from the brief; the folder is the slug.
            self.assertNotIn("slug:", brief)
            self.assertNotIn("{kebab-slug}", brief)
            self.assertNotIn("{YYYY-MM-DD}", brief)

    def test_when_markers_pick_sections_by_project_type(self):
        # One brief replaced four variants: the project type now decides which
        # sections get written, and the marker never survives into the file.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "code", "coding", "Code", "2026-05-27")
            scaffold_vault_project(vault, "know", "knowledge", "Know", "2026-05-27")
            active = vault / "projects" / "active"
            coding = (active / "code" / "brief.md").read_text()
            knowledge = (active / "know" / "brief.md").read_text()
            self.assertIn("## Stack", coding)
            self.assertIn("## Constraints", coding)
            self.assertNotIn("## Stack", knowledge)
            self.assertNotIn("## Constraints", knowledge)
            for text in (coding, knowledge):
                self.assertNotIn("<!-- when:", text)
                self.assertIn("## Where things are", text)

    def test_idempotent_preserves_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            scaffold_vault_project(vault, "abc", "coding", "Abc", "2026-05-27")
            brief_path = vault / "projects" / "active" / "abc" / "brief.md"
            brief_path.write_text("USER EDITED")
            scaffold_vault_project(vault, "abc", "coding", "Abc 2", "2026-05-28")
            self.assertEqual(brief_path.read_text(), "USER EDITED")

    def test_an_unknown_project_type_is_refused(self):
        # The per-type folder table was the only thing that rejected a typo.
        # Deleting it must not turn a bad type into a brief with every gated
        # section silently missing.
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            with self.assertRaises(RuntimeError):
                scaffold_vault_project(vault, "p", "codin", "P", "2026-05-27")
            self.assertFalse((vault / "projects" / "active" / "p").exists())

    def test_reconnect_fills_no_folders_into_a_paused_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            proj_dir = vault / "projects" / "paused" / "p"
            scaffold_vault_project(vault, "p", "coding", "P", "2026-05-27",
                                   proj_dir=proj_dir)
            self.assertEqual([x.name for x in proj_dir.iterdir()], ["brief.md"])


# ============================================================
# Step 4: session note
# ============================================================


class TestWriteSessionNote(unittest.TestCase):

    def test_creates(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = _make_vault(tmp)
            (vault / "projects" / "active" / "x").mkdir(parents=True)
            r = write_session_note(vault, "x", "2026-05-27", "09:30")
            self.assertEqual(r, "created")
            self.assertTrue((vault / "projects" / "active" / "x" / "sessions"
                             / "2026-05-27.md").is_file())

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
            # Vault scaffold: a real connect lands the project in active/, not
            # in the un-zoned projects/{slug} it used before v3.
            pdir = vault / "projects" / "active" / "my-project"
            self.assertTrue((pdir / "brief.md").is_file())
            self.assertFalse((vault / "projects" / "my-project").exists())
            # No folder exists that nothing was written into. decisions/ used
            # to arrive here holding one empty _index.md.
            self.assertFalse((pdir / "decisions").exists())
            # Session note
            self.assertTrue((pdir / "sessions" / "2026-05-27.md").is_file())
            # .gitignore
            self.assertIn(".claude/adjudant", (proj / ".gitignore").read_text())
            # projects/_index.md is retired: Home groups every project by
            # lifecycle folder instead, and connect writes no row anywhere.
            self.assertFalse((vault / "projects" / "_index.md").exists())

    def test_reconnect_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "p"; proj.mkdir()
            vault = _make_vault(tmp)
            for _ in range(2):
                run_connect(proj, vault, "v", "p", "coding", "P", "2026-05-27", "10:00")
            self.assertEqual(detect_state(proj, vault, "p"), "connected")


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
            self.assertFalse((vault / "projects" / "active" / "proj").exists())


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
            self.assertIn(f"cost_warn_tokens: {DEFAULT_WARN_TOKENS}", bc)
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
                f"cost_warn_tokens: {DEFAULT_WARN_TOKENS}",
                "cost_warn_tokens: 99000"))
            self._connect(code, vault)
            self.assertIn("cost_warn_tokens: 99000", bc_path.read_text())

    def test_purpose_lands_in_the_brief_and_agents_md(self):
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
            brief = (vault / "projects" / "active" / "proj" / "brief.md").read_text()
            # v3 dropped status: from the brief; the zone folder is the status.
            self.assertNotIn("status:", brief)
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
            # zone_of() normalises since Task 1: the legacy _fridge/ folder
            # reads as "paused", one of the four named lifecycle folders.
            self.assertEqual(contract["zone"], "paused")
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

    def _reconnect_project_type(self, root: Path, vault: Path, brief_text: str) -> str:
        proj_dir = vault / "projects" / "_fridge" / "p"
        scaffold_vault_project(
            vault, "p", "plugin", "P", "2026-05-27",
            initial_status="fridge", proj_dir=proj_dir)
        (proj_dir / "brief.md").write_text(brief_text)
        code = root / "p"; code.mkdir()
        # A code file makes infer_project_type() say "coding"
        (code / "main.py").write_text("print('x')")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = connect_cli([
                "--project-root", str(code), "--vault-path", str(vault),
                "--slug", "p"])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())["project_type"]

    def test_zoned_brief_drives_project_type_on_reconnect(self):
        """A fridged pre-v3 brief declaring project_type plugin must win over
        re-inference when --project-type is omitted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            legacy = ("---\ntype: project\nproject_type: plugin\nslug: p\n"
                      "status: fridge\ncreated: 2026-05-27\nupdated: 2026-05-27\n"
                      "---\n\n# P\n")
            self.assertEqual(self._reconnect_project_type(root, vault, legacy), "plugin")

    def test_a_v3_brief_declares_no_project_type_so_inference_wins(self):
        """v3 dropped project_type from the brief, so a brief carrying none
        cannot override re-inference. Pinned rather than discovered later."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            (vault / "projects").mkdir(parents=True)
            v3 = ("---\ntype: project\ncreated: 2026-05-27\nupdated: 2026-05-27\n"
                  "verified: 2026-05-27\nverified_by: read\n---\n\n# P\n")
            self.assertEqual(self._reconnect_project_type(root, vault, v3), "coding")


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
            pdir = vault / "projects" / "active" / "proj"
            bases = pdir / "bases"
            names = sorted(p.name for p in bases.glob("dashboard-*.base"))
            self.assertEqual(names, ["dashboard-decisions.base",
                                     "dashboard-freshness.base",
                                     "dashboard-sessions.base",
                                     "dashboard-tasks.base"])
            # The filter must name the folder the project is actually in. A
            # dashboard scoped to a path the project left returns nothing at
            # all, with no error to notice.
            rel = pdir.relative_to(vault).as_posix()
            for tpl in bases.glob("dashboard-*.base"):
                text = tpl.read_text()
                self.assertNotIn("{slug}", text)
                for folder in re.findall(r'file\.inFolder\("([^"]+)"\)', text):
                    self.assertTrue(folder.startswith(rel + "/"),
                                    f"{tpl.name} filters on {folder}, but the "
                                    f"project lives at {rel}")

    def test_edited_dashboard_never_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "vault"; vault.mkdir()
            self._connect(root, vault)
            target = (vault / "projects" / "active" / "proj" / "bases"
                      / "dashboard-tasks.base")
            target.write_text("filters: 'status == \"done\"'\n# my edit\n")
            self._connect(root, vault)   # idempotent re-run
            self.assertIn("# my edit", target.read_text())


class TestGuidedVaultSetup(unittest.TestCase):

    def test_suggest_vaults_prints_json_and_exits_zero(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = connect_cli(["--suggest-vaults"])
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertIn("vault_roots", payload)
        self.assertIsInstance(payload["vault_roots"], list)

    def test_create_vault_makes_the_dir_and_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            (project / ".claude").mkdir(parents=True)
            new_vault = Path(tmp) / "fresh-vault"
            connect_cli([
                "--project-root", str(project),
                "--vault-path", str(new_vault),
                "--create-vault",
                "--detect-only",
            ])
            self.assertTrue(new_vault.is_dir())
            self.assertTrue((new_vault / "projects").is_dir())


if __name__ == "__main__":
    unittest.main()
