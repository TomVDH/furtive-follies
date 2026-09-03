"""Tests for hooks/scripts/precompact.py — the PreCompact/SessionEnd hook.

Since v3 the hook has one lane, and it runs only under `--sync-only`: mirror
the remember source into `_handoff.md`. A bare PreCompact invocation drains
stdin and returns, so the handoff is written once per session rather than once
per compaction. The `paused (compaction)` tombstone the hook also appended is
gone, so a compaction leaves the session note exactly as the work left it.

Every test that exercises the write path therefore passes `--sync-only`; a
bare run would sail past the resolver and prove nothing about the guards.

Regression focus: the hook must fail closed on a stale/cross-machine breadcrumb
instead of materializing a phantom vault directory chain via mkdir(parents=True);
resolution must use the same resolve_vault chain as the verbs; and a broken or
mid-sync scripts/ module must only degrade its own capability (no import
shadowing, no crash).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks" / "scripts"))
import precompact
import _vault_walk

SCRIPTS = Path(__file__).resolve().parent
HOOK = SCRIPTS.parent / "hooks" / "scripts" / "precompact.py"


class _EnvHygiene(unittest.TestCase):
    """OB_VAULT from the developer's shell must never leak into these tests —
    resolve_vault consults it as step 1."""

    def setUp(self):
        self._ob_vault = os.environ.pop("OB_VAULT", None)

    def tearDown(self):
        if self._ob_vault is not None:
            os.environ["OB_VAULT"] = self._ob_vault


class TestFailClosedOnStaleVault(_EnvHygiene):

    def _breadcrumb(self, project: Path, vault_path: str, slug: str = "demo") -> None:
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault_path}\nvault_name: vault\nslug: {slug}\nmode: project\n"
        )

    def test_stale_vault_path_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            phantom = Path(tmp) / "gone" / "vault"  # does not exist
            self._breadcrumb(project, str(phantom))
            (project / ".remember").mkdir()
            (project / ".remember" / "remember.md").write_text("NEXT: something\n")

            os.environ["CLAUDE_PROJECT_DIR"] = str(project)
            argv_before = sys.argv
            sys.argv = ["precompact.py", "--sync-only"]
            try:
                rc = precompact.main()
            finally:
                sys.argv = argv_before
                del os.environ["CLAUDE_PROJECT_DIR"]

            self.assertEqual(rc, 0)  # hook never blocks
            self.assertFalse(phantom.exists(),
                             "stale vault path must NOT be materialized by the hook")

    def test_vault_name_fallback_resolves_on_second_machine(self):
        # Cross-machine: absolute vault_path is from the other Mac, but the
        # vault exists under a standard location on THIS machine. The hook now
        # delegates to _vault_walk.resolve_vault, so the candidate scan is
        # patched at its source.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            vault = Path(tmp) / "cands" / "MyVault"
            (vault / "projects" / "demo").mkdir(parents=True)
            self._breadcrumb(project, "/other-machine/vault")
            (project / ".claude" / "adjudant").write_text(
                f"vault_path: /other-machine/vault\nvault_name: MyVault\nslug: demo\nmode: project\n")
            (project / ".remember").mkdir()
            (project / ".remember" / "remember.md").write_text("NEXT: x\n")

            orig = _vault_walk._candidate_vault_paths
            _vault_walk._candidate_vault_paths = lambda name: [Path(tmp) / "cands" / name]
            os.environ["CLAUDE_PROJECT_DIR"] = str(project)
            argv_before = sys.argv
            sys.argv = ["precompact.py", "--sync-only"]
            try:
                rc = precompact.main()
            finally:
                sys.argv = argv_before
                del os.environ["CLAUDE_PROJECT_DIR"]
                _vault_walk._candidate_vault_paths = orig

            self.assertEqual(rc, 0)
            self.assertTrue((vault / "projects" / "demo" / "_handoff.md").is_file(),
                            "vault_name fallback must find the local vault")

    def test_vault_path_absent_still_resolves_via_vault_name(self):
        # A breadcrumb with only vault_name + slug (hand-ported, no absolute
        # path) used to make the python hooks silently no-op while the shell
        # hooks resolved it.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            vault = Path(tmp) / "cands" / "MyVault"
            (vault / "projects" / "demo").mkdir(parents=True)
            (project / ".claude").mkdir(parents=True)
            (project / ".claude" / "adjudant").write_text(
                "vault_name: MyVault\nslug: demo\nmode: project\n")
            (project / ".remember").mkdir()
            (project / ".remember" / "remember.md").write_text("NEXT: x\n")

            orig = _vault_walk._candidate_vault_paths
            _vault_walk._candidate_vault_paths = lambda name: [Path(tmp) / "cands" / name]
            os.environ["CLAUDE_PROJECT_DIR"] = str(project)
            argv_before = sys.argv
            sys.argv = ["precompact.py", "--sync-only"]
            try:
                rc = precompact.main()
            finally:
                sys.argv = argv_before
                del os.environ["CLAUDE_PROJECT_DIR"]
                _vault_walk._candidate_vault_paths = orig

            self.assertEqual(rc, 0)
            self.assertTrue((vault / "projects" / "demo" / "_handoff.md").is_file())

    def test_real_vault_still_gets_handoff_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            vault = Path(tmp) / "vault"
            (vault / "projects" / "demo").mkdir(parents=True)
            self._breadcrumb(project, str(vault))
            (project / ".remember").mkdir()
            (project / ".remember" / "remember.md").write_text("body\n\nNEXT: keep going\n")

            os.environ["CLAUDE_PROJECT_DIR"] = str(project)
            argv_before = sys.argv
            sys.argv = ["precompact.py", "--sync-only"]
            try:
                rc = precompact.main()
            finally:
                sys.argv = argv_before
                del os.environ["CLAUDE_PROJECT_DIR"]

            self.assertEqual(rc, 0)
            handoff = vault / "projects" / "demo" / "_handoff.md"
            self.assertTrue(handoff.is_file(), "handoff mirror must be written for a real vault")
            self.assertIn("NEXT: keep going", handoff.read_text())

class TestEmptySourceGuard(_EnvHygiene):
    """A blank .remember source must never wipe a populated handoff. The
    remember plugin rotates now.md to empty at session start; every quick
    SessionEnd then mirrored nothing over the last surviving summary."""

    def _fixture(self, tmp: Path, now_content: str) -> tuple[Path, Path, Path]:
        project = tmp / "code"
        vault = tmp / "vault"
        (vault / "projects" / "demo").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        (project / ".remember").mkdir()
        (project / ".remember" / "now.md").write_text(now_content)
        handoff = vault / "projects" / "demo" / "_handoff.md"
        handoff.write_text(
            "---\ntype: handoff\nupdated: 2026-05-01\n---\n\n"
            "# Handoff: demo\n\nprecious context\nNEXT: keep this\n")
        return project, vault, handoff

    def _run_sync_only(self, project: Path) -> int:
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        argv_before = sys.argv
        sys.argv = ["precompact.py", "--sync-only"]
        try:
            return precompact.main()
        finally:
            sys.argv = argv_before
            del os.environ["CLAUDE_PROJECT_DIR"]

    def test_empty_source_preserves_existing_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, vault, handoff = self._fixture(Path(tmp), "")
            before = handoff.read_text()
            rc = self._run_sync_only(project)
            self.assertEqual(rc, 0)
            self.assertEqual(handoff.read_text(), before)

    def test_whitespace_source_preserves_existing_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, vault, handoff = self._fixture(Path(tmp), "\n  \n")
            before = handoff.read_text()
            rc = self._run_sync_only(project)
            self.assertEqual(rc, 0)
            self.assertEqual(handoff.read_text(), before)

    def test_populated_source_still_mirrors(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, vault, handoff = self._fixture(Path(tmp), "fresh state\n")
            rc = self._run_sync_only(project)
            self.assertEqual(rc, 0)
            self.assertIn("fresh state", handoff.read_text())


class TestWrittenOnce(_EnvHygiene):
    """The handoff is written by SessionEnd (`--sync-only`), and nowhere else.

    A session that compacted three times rewrote `_handoff.md` three times and
    once more at session end, each pass clobbering the last. Compaction now
    drains stdin and returns; the flag SessionEnd already passed is what asks
    for the write.
    """

    def _fixture(self, tmp: Path) -> tuple[Path, Path]:
        project = tmp / "code"
        vault = tmp / "vault"
        (vault / "projects" / "demo").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        (project / ".remember").mkdir()
        (project / ".remember" / "remember.md").write_text("body\n\nNEXT: carry on\n")
        return project, vault / "projects" / "demo" / "_handoff.md"

    def _run(self, project: Path, *args: str) -> int:
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        argv_before = sys.argv
        sys.argv = ["precompact.py", *args]
        try:
            return precompact.main()
        finally:
            sys.argv = argv_before
            del os.environ["CLAUDE_PROJECT_DIR"]

    def test_compaction_writes_no_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, handoff = self._fixture(Path(tmp))
            self.assertEqual(self._run(project), 0)
            self.assertFalse(handoff.exists(),
                             "compaction must not write the handoff")

    def test_compaction_leaves_an_existing_handoff_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, handoff = self._fixture(Path(tmp))
            handoff.write_text("---\ntype: handoff\nupdated: 2026-05-01\n---\n\n"
                               "# Handoff: demo\n\nearlier state\nNEXT: keep this\n")
            before = handoff.read_text()
            self.assertEqual(self._run(project), 0)
            self.assertEqual(handoff.read_text(), before)

    def test_sync_only_still_writes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, handoff = self._fixture(Path(tmp))
            self.assertEqual(self._run(project, "--sync-only"), 0)
            self.assertIn("NEXT: carry on", handoff.read_text())


class TestZoneAwareness(_EnvHygiene):
    """Audit 2026-07-27: the hook hardcoded projects/<slug> while shelf moves
    projects to _fridge/ and _archive/ without touching the breadcrumb.

    This one clobbers: `_handoff.md` is written with write_text, so a shelved
    project grew a phantom active-zone twin that was rewritten on EVERY
    compaction while the real handoff went stale in the fridge.
    """

    def _shelved(self, tmp: Path, zone: str):
        project = tmp / "code"
        vault = tmp / "vault"
        proot = vault / "projects" / zone / "demo"
        (proot / "sessions").mkdir(parents=True)
        (proot / "brief.md").write_text(
            "---\ntype: project\nslug: demo\nstatus: fridge\n---\n\n# Demo\n")
        note = proot / "sessions" / f"{datetime.now():%Y-%m-%d}.md"
        note.write_text("## Log\n")
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        (project / ".remember").mkdir()
        (project / ".remember" / "remember.md").write_text("body\n\nNEXT: thaw it\n")
        return project, proot, note

    def _run(self, project: Path, *args: str) -> int:
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        argv_before = sys.argv
        sys.argv = ["precompact.py", *args]
        try:
            return precompact.main()
        finally:
            sys.argv = argv_before
            del os.environ["CLAUDE_PROJECT_DIR"]

    def test_handoff_lands_in_the_shelved_project(self):
        for zone in ("_fridge", "_archive"):
            with self.subTest(zone=zone):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    project, proot, note = self._shelved(root, zone)
                    self.assertEqual(self._run(project, "--sync-only"), 0)
                    self.assertEqual(note.read_text(), "## Log\n",
                                     "v3: the hook appends no marker")
                    handoff = proot / "_handoff.md"
                    self.assertTrue(handoff.is_file(),
                                    "the handoff must mirror into the shelved project")
                    self.assertIn("NEXT: thaw it", handoff.read_text())
                    self.assertFalse((root / "vault" / "projects" / "demo").exists(),
                                     "no phantom active-zone twin may be created")

    def test_sync_only_also_follows_the_zone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, proot, note = self._shelved(root, "_fridge")
            self.assertEqual(self._run(project, "--sync-only"), 0)
            self.assertTrue((proot / "_handoff.md").is_file())
            self.assertNotIn("paused", note.read_text())  # v3: no marker, either mode
            self.assertFalse((root / "vault" / "projects" / "demo").exists())

    def test_unknown_project_is_noop(self):
        # No project in any zone: never materialize one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, proot, note = self._shelved(root, "_fridge")
            shutil.rmtree(proot)
            self.assertEqual(self._run(project, "--sync-only"), 0)
            self.assertFalse((root / "vault" / "projects" / "demo").exists())
            self.assertFalse((root / "vault" / "projects" / "_fridge" / "demo").exists())


class TestSlugGuard(_EnvHygiene):
    """The breadcrumb is repo-committed, so a cloned repo can carry a traversal
    slug. This hook is the dangerous one: `_handoff.md` is written with
    write_text, which CLOBBERS, so a slug that escapes the vault overwrites
    whatever file already sits at that path.

    Each fixture MATERIALIZES the directory the bad slug resolves to, with the
    shape find_project_dir accepts (brief.md plus a dated session note), and
    plants a hand-written `_handoff.md` there. Everything downstream of the
    slug guard is therefore live, and the guard is the last thing standing
    between the hook and destroying that file.
    """

    PRECIOUS = ("---\ntype: handoff\nupdated: 2026-05-01\nsource: elsewhere\n"
                "tags:\n  - handoff\n---\n\n# Not adjudant's file\n\n"
                "someone else's work\n")

    def _decoy(self, tmp: Path, slug: str) -> tuple[Path, Path]:
        """Project breadcrumbed to `slug`, plus a live project where it lands.

        The join is the same `vault/projects/<slug>` find_project_dir performs,
        so `..` segments resolve exactly where a neutered guard would send the
        write. Returns (project, decoy_root).
        """
        project = tmp / "code"
        vault = tmp / "vault"
        (vault / "projects").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: precompact-slug-test-vault-3c7e\n"
            f"slug: {slug}\nmode: project\n")
        (project / ".remember").mkdir()
        (project / ".remember" / "remember.md").write_text(
            "fresh body\n\nNEXT: overwrite something\n")
        decoy = vault / "projects" / slug
        (decoy / "sessions").mkdir(parents=True, exist_ok=True)
        (decoy / "brief.md").write_text(
            "---\ntype: project\nslug: decoy\n---\n\n# Decoy\n")
        (decoy / "sessions" / f"{datetime.now():%Y-%m-%d}.md").write_text("## Log\n")
        (decoy / "_handoff.md").write_text(self.PRECIOUS)
        return project, decoy

    def _run(self, project: Path, *args: str) -> int:
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        argv_before = sys.argv
        sys.argv = ["precompact.py", *args]
        try:
            return precompact.main()
        finally:
            sys.argv = argv_before
            del os.environ["CLAUDE_PROJECT_DIR"]

    def test_traversal_slug_never_clobbers_an_outside_file(self):
        # `../../escaped` climbs out of projects/ AND out of the vault: the
        # decoy lands next to the vault, in the tmp root.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, decoy = self._decoy(root, "../../escaped")
            outside = root / "escaped"
            self.assertTrue((outside / "_handoff.md").is_file(),
                            "fixture must plant a file OUTSIDE the vault")
            self.assertEqual(self._run(project, "--sync-only"), 0)
            self.assertEqual((outside / "_handoff.md").read_text(), self.PRECIOUS,
                             "a traversal slug must never overwrite a file "
                             "outside the vault")
            self.assertEqual(
                (outside / "sessions" / f"{datetime.now():%Y-%m-%d}.md").read_text(),
                "## Log\n", "and must never append a pause marker there either")

    def test_metachar_slug_never_writes(self):
        for bad in ("has space", "UPPER", "back`tick", "-leading"):
            with self.subTest(slug=bad):
                with tempfile.TemporaryDirectory() as tmp:
                    project, decoy = self._decoy(Path(tmp), bad)
                    self.assertEqual(self._run(project, "--sync-only"), 0)
                    self.assertEqual((decoy / "_handoff.md").read_text(),
                                     self.PRECIOUS)

    def test_decoy_fixture_is_live_for_a_safe_slug(self):
        # Control: the same fixture with a kebab-case slug DOES get clobbered.
        # Without it, a decoy that silently failed to resolve would make the
        # two tests above pass for the wrong reason all over again.
        with tempfile.TemporaryDirectory() as tmp:
            project, decoy = self._decoy(Path(tmp), "decoy-project")
            self.assertEqual(self._run(project, "--sync-only"), 0)
            text = (decoy / "_handoff.md").read_text()
            self.assertIn("NEXT: overwrite something", text)
            self.assertNotIn("someone else's work", text)


class TestImportDegradation(_EnvHygiene):
    """A broken or mid-sync scripts/ module must only degrade its own
    capability. Runs the hook as a subprocess inside a fake plugin tree so the
    import-time behavior is exercised for real."""

    def _fake_plugin(self, tmp: Path, *, break_freshness: bool, break_walk: bool) -> Path:
        plugin = tmp / "plugin"
        (plugin / "hooks" / "scripts").mkdir(parents=True)
        (plugin / "scripts").mkdir(parents=True)
        shutil.copy2(HOOK, plugin / "hooks" / "scripts" / "precompact.py")
        # The real plugin layout, because _handoff_freshness derives the
        # handoff frontmatter from templates/handoff.md through _render since
        # v3. A tree without the templates is not a plugin, and testing
        # degradation against one would only prove the fixture is incomplete.
        for mod in ("_render.py", "_template_schema.py"):
            shutil.copy2(SCRIPTS / mod, plugin / "scripts")
        shutil.copytree(SCRIPTS.parent / "skills" / "adjudant" / "templates",
                        plugin / "skills" / "adjudant" / "templates")
        if break_freshness:
            (plugin / "scripts" / "_handoff_freshness.py").write_text("def (broken syntax\n")
        else:
            shutil.copy2(SCRIPTS / "_handoff_freshness.py", plugin / "scripts")
        if break_walk:
            (plugin / "scripts" / "_vault_walk.py").write_text("def (broken syntax\n")
        else:
            shutil.copy2(SCRIPTS / "_vault_walk.py", plugin / "scripts")
        return plugin

    def _project_and_vault(self, tmp: Path) -> tuple[Path, Path]:
        project = tmp / "code"
        vault = tmp / "vault"
        (vault / "projects" / "demo").mkdir(parents=True)
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        (project / ".remember").mkdir()
        (project / ".remember" / "remember.md").write_text("body\n\nNEXT: carry on\n")
        return project, vault

    def _run(self, plugin: Path, project: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env.pop("OB_VAULT", None)
        return subprocess.run(
            [sys.executable, str(plugin / "hooks" / "scripts" / "precompact.py"), "--sync-only"],
            env=env, capture_output=True, text=True, timeout=15,
        )

    def test_broken_freshness_still_does_mechanical_work(self):
        # _handoff_freshness broken, _vault_walk fine: exit 0, handoff written,
        # freshness header simply absent. (Used to NameError-crash and write
        # nothing.)
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._fake_plugin(Path(tmp), break_freshness=True, break_walk=False)
            project, vault = self._project_and_vault(Path(tmp))
            r = self._run(plugin, project)
            self.assertEqual(r.returncode, 0, r.stderr)
            handoff = vault / "projects" / "demo" / "_handoff.md"
            self.assertTrue(handoff.is_file())
            text = handoff.read_text()
            self.assertIn("Mirrored from", text)
            self.assertNotIn("handoff age", text)  # degraded: no freshness header

    def test_broken_walk_keeps_freshness_header(self):
        # _vault_walk broken, _handoff_freshness fine: exit 0, handoff written
        # WITH the freshness header. (The old shims clobbered the working
        # freshness functions.)
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._fake_plugin(Path(tmp), break_freshness=False, break_walk=True)
            project, vault = self._project_and_vault(Path(tmp))
            r = self._run(plugin, project)
            self.assertEqual(r.returncode, 0, r.stderr)
            handoff = vault / "projects" / "demo" / "_handoff.md"
            self.assertTrue(handoff.is_file())
            text = handoff.read_text()
            self.assertIn("handoff age", text)      # freshness survived
            self.assertIn("NEXT: carry on", text)

    def test_degraded_mode_honors_ob_vault_first(self):
        # _vault_walk broken + OB_VAULT set + locally-valid breadcrumb path:
        # the Python hook must prefer OB_VAULT, matching the shell hooks'
        # degraded branch (same-vault invariant).
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._fake_plugin(Path(tmp), break_freshness=False, break_walk=True)
            project, vault = self._project_and_vault(Path(tmp))
            override = Path(tmp) / "ovault"
            (override / "projects" / "demo").mkdir(parents=True)
            env = dict(os.environ)
            env["CLAUDE_PROJECT_DIR"] = str(project)
            env["OB_VAULT"] = str(override)
            r = subprocess.run(
                [sys.executable, str(plugin / "hooks" / "scripts" / "precompact.py"), "--sync-only"],
                env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((override / "projects" / "demo" / "_handoff.md").is_file(),
                            "degraded mode must write to the OB_VAULT vault")
            self.assertFalse((vault / "projects" / "demo" / "_handoff.md").exists(),
                             "breadcrumb vault must NOT receive the handoff when OB_VAULT overrides")

    def test_both_broken_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._fake_plugin(Path(tmp), break_freshness=True, break_walk=True)
            project, vault = self._project_and_vault(Path(tmp))
            r = self._run(plugin, project)
            self.assertEqual(r.returncode, 0, r.stderr)
            # vault_path is locally valid → degraded mode still mirrors
            self.assertTrue((vault / "projects" / "demo" / "_handoff.md").is_file())


class TestStdinDiscipline(_EnvHygiene):
    """Finding 22: precompact never read stdin, so a large PreCompact payload
    EPIPEs the harness writer the moment the hook exits. Drain first."""

    def test_stdin_fully_consumed_before_exit(self):
        hook = Path(__file__).resolve().parent.parent / "hooks" / "scripts" / "precompact.py"
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.pop("OB_VAULT", None)
        proc = subprocess.Popen(
            [sys.executable, str(hook)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        wrote_all = True
        try:
            proc.stdin.write(b"x" * 8_000_000)
            proc.stdin.close()
        except BrokenPipeError:
            wrote_all = False
        rc = proc.wait(timeout=30)
        self.assertTrue(wrote_all, "hook exited before draining stdin (EPIPE)")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
