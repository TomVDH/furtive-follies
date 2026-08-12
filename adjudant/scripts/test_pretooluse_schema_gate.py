"""Tests for hooks/scripts/pretooluse-schema-gate.py.

The gate blocks a Write into the vault project when the proposed frontmatter
is missing required fields or carries a type/node_type conflict. Everything
else - unknown fields, writes outside the project, any infrastructural
problem - must let the write through.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
HOOK = SCRIPTS.parent / "hooks" / "scripts" / "pretooluse-schema-gate.py"
_spec = importlib.util.spec_from_file_location("pretooluse_schema_gate", HOOK)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

GOOD_NOTE = ("---\ntype: note\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
             "tags:\n  - note\n---\n\nBody.\n")


class _GateHarness(unittest.TestCase):

    def setUp(self):
        self._ob = os.environ.pop("OB_VAULT", None)

    def tearDown(self):
        if self._ob is not None:
            os.environ["OB_VAULT"] = self._ob

    def _fixture(self, tmp: Path, zone: str = "") -> tuple[Path, Path]:
        project = tmp / "code"
        vault = tmp / "vault"
        proot = vault / "projects" / zone / "demo" if zone else vault / "projects" / "demo"
        proot.mkdir(parents=True)
        (proot / "brief.md").write_text(
            "---\ntype: project\nslug: demo\n---\n\n# Demo\n")
        (project / ".claude").mkdir(parents=True)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {vault}\nvault_name: vault\nslug: demo\nmode: project\n")
        return project, proot

    def _run(self, project: Path, payload) -> int:
        return self._run_capturing(project, payload)[0]

    def _run_capturing(self, project: Path, payload) -> tuple[int, str]:
        """(exit code, stderr). stderr matters because it is the ONLY channel
        a PreToolUse hook has, and the harness reads it on a non-zero exit
        only: anything printed alongside an exit 0 is written to nobody."""
        os.environ["CLAUDE_PROJECT_DIR"] = str(project)
        before = sys.stdin
        sys.stdin = io.StringIO(payload if isinstance(payload, str)
                                else json.dumps(payload))
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = gate.main()
            return rc, err.getvalue()
        finally:
            sys.stdin = before
            del os.environ["CLAUDE_PROJECT_DIR"]

    def _payload(self, path: Path, content: str, tool: str = "Write") -> dict:
        return {"tool_name": tool,
                "tool_input": {"file_path": str(path), "content": content}}


class TestBlocks(_GateHarness):

    def test_missing_required_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(
                proot / "decisions" / "d.md", "---\ntype: decision\n---\n\nB\n"))
            self.assertEqual(rc, 2)

    def test_type_conflict_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            bad = GOOD_NOTE.replace("type: note\n", "type: note\nnode_type: note\n")
            rc = self._run(project, self._payload(proot / "notes" / "n.md", bad))
            self.assertEqual(rc, 2)

    def test_malformed_epistemic_declaration_blocks(self):
        # v0.22.0: epistemic fields have zero legacy values, so a malformed
        # declaration is pure model drift - block and name the field.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            bad = GOOD_NOTE.replace("type: note\n",
                                    "type: note\ncertainty: 9\n")
            rc, err = self._run_capturing(
                project, self._payload(proot / "notes" / "n.md", bad))
            self.assertEqual(rc, 2)
            self.assertIn("certainty", err)

    def test_valid_epistemic_declaration_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            good = GOOD_NOTE.replace(
                "type: note\n",
                "type: note\nfreshness: dated\nvalid_until: 2030-01-01\n")
            rc = self._run(project, self._payload(proot / "notes" / "n.md", good))
            self.assertEqual(rc, 0)


class TestVoiceGate(_GateHarness):
    """Surface 2 of the voice contract: prose landing in the vault.

    A note lives for years and nothing sweeps its prose afterwards - tidy is
    frontmatter and structure only. The gate is the one point where the text
    can still be corrected in the same turn that wrote it.

    The bar is narrower than a validator's on purpose. A false positive here
    wedges the model mid-write, so only conversational tics with no technical
    reading block; a merely banned word is commit-time business.
    """

    def test_a_glazing_phrase_blocks_the_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            note = GOOD_NOTE.replace("Body.", "Great question. The parser is fine.")
            rc, err = self._run_capturing(
                project, self._payload(proot / "notes" / "n.md", note))
            self.assertEqual(rc, 2)
            self.assertIn("Great question", err)

    def test_the_block_names_the_phrase_and_the_fix(self):
        # stderr is the gate's only channel, and only on a non-zero exit.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            note = GOOD_NOTE.replace("Body.", "Hope this helps with the parser.")
            _, err = self._run_capturing(
                project, self._payload(proot / "notes" / "n.md", note))
            self.assertIn("voice", err.lower())
            self.assertIn("Hope this helps", err)

    def test_a_merely_banned_word_does_not_block(self):
        # `robust` is worth fixing at commit time, not worth refusing a note.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            note = GOOD_NOTE.replace("Body.", "A robust parser, utilized daily.")
            self.assertEqual(self._run(project, self._payload(
                proot / "notes" / "n.md", note)), 0)

    def test_a_phrase_inside_a_code_fence_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            note = GOOD_NOTE.replace("Body.", "```\nGreat question\n```")
            self.assertEqual(self._run(project, self._payload(
                proot / "notes" / "n.md", note)), 0)

    def test_a_schema_failure_still_outranks_voice(self):
        # Frontmatter is objectively wrong; voice is a quality judgment. The
        # message the model gets back must be the one it can act on first.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            bad = "---\ntype: decision\n---\n\nGreat question. Body.\n"
            rc, err = self._run_capturing(
                project, self._payload(proot / "decisions" / "d.md", bad))
            self.assertEqual(rc, 2)
            self.assertIn("missing required", err)

    def test_a_write_outside_the_project_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._fixture(Path(tmp))
            outside = Path(tmp) / "elsewhere" / "n.md"
            note = GOOD_NOTE.replace("Body.", "Great question.")
            self.assertEqual(self._run(
                project, self._payload(outside, note)), 0)


class TestAllows(_GateHarness):

    def test_conformant_note_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(proot / "notes" / "n.md", GOOD_NOTE))
            self.assertEqual(rc, 0)

    def test_unknown_field_allowed_silently(self):
        # The gate used to print a warning here. On an exit 0 a PreToolUse
        # hook's stderr reaches nobody, so the "warns on unknown fields"
        # behaviour never happened; check reports them and tidy strips them.
        # Assert the silence, so the docs and the code cannot drift apart
        # again without a test noticing.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            bad = GOOD_NOTE.replace("type: note\n", "type: note\nbogus: x\n")
            rc, err = self._run_capturing(
                project, self._payload(proot / "notes" / "n.md", bad))
            self.assertEqual(rc, 0)
            self.assertEqual(err, "",
                             "an allowed write must print nothing at all")

    def test_block_still_explains_itself_on_stderr(self):
        # Control for the silence above: the one path the harness DOES read
        # still says what is wrong.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc, err = self._run_capturing(project, self._payload(
                proot / "decisions" / "d.md", "---\ntype: decision\n---\n\nB\n"))
            self.assertEqual(rc, 2)
            self.assertIn("missing required field(s)", err)

    def test_write_outside_project_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(
                Path(tmp) / "elsewhere.md", "---\ntype: decision\n---\n\nB\n"))
            self.assertEqual(rc, 0)

    def test_edit_tool_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(
                proot / "decisions" / "d.md", "---\ntype: decision\n---\n\nB\n",
                tool="Edit"))
            self.assertEqual(rc, 0)

    def test_shelved_project_is_gated_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp), zone="_fridge")
            rc = self._run(project, self._payload(
                proot / "decisions" / "d.md", "---\ntype: decision\n---\n\nB\n"))
            self.assertEqual(rc, 2)


class TestFailsOpen(_GateHarness):

    def test_no_breadcrumb_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "code"
            project.mkdir()
            rc = self._run(project, self._payload(
                Path(tmp) / "x.md", "---\ntype: decision\n---\n\nB\n"))
            self.assertEqual(rc, 0)

    def test_garbage_payload_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self._fixture(Path(tmp))
            self.assertEqual(self._run(project, "not json {{{"), 0)


BLOCKING = "---\ntype: decision\n---\n\nB\n"


class TestSlugGuard(_GateHarness):
    """The breadcrumb is repo-committed, so a cloned repo can carry a traversal
    slug. The gate must refuse it before joining it into a path.

    Every fixture here MATERIALIZES the directory the bad slug resolves to and
    fills it with a shape find_project_dir accepts (brief.md), so the zone
    lookup succeeds and everything downstream of the slug guard is live. The
    guard is then the only thing between the gate and judging a file that sits
    outside the vault entirely. An earlier version of this test pointed the
    traversal at a path that did not exist, so find_project_dir returned None
    and the gate bailed at the zone check: it passed with the guard deleted.
    """

    def _decoy(self, tmp: Path, slug: str) -> tuple[Path, Path]:
        """Breadcrumb carrying `slug`, plus a real project dir where it lands.

        The decoy path is built by the same `vault/projects/<slug>` join
        find_project_dir performs, so `..` segments resolve exactly where a
        neutered guard would send the gate. Returns (project, decoy_root).
        """
        project, _ = self._fixture(tmp)
        (project / ".claude" / "adjudant").write_text(
            f"vault_path: {tmp / 'vault'}\nslug: {slug}\nmode: project\n")
        decoy = tmp / "vault" / "projects" / slug
        decoy.mkdir(parents=True, exist_ok=True)
        (decoy / "brief.md").write_text(
            "---\ntype: project\nslug: decoy\n---\n\n# Decoy\n")
        return project, decoy

    def test_traversal_slug_is_refused(self):
        # `../../escaped` climbs out of projects/ AND out of the vault: the
        # decoy lands next to the vault, in the tmp root.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, decoy = self._decoy(root, "../../escaped")
            self.assertTrue((root / "escaped" / "brief.md").is_file(),
                            "fixture must place a live project OUTSIDE the vault")
            rc = self._run(project, self._payload(
                decoy / "decisions" / "d.md", BLOCKING))
            self.assertEqual(rc, 0,
                             "a traversal slug must never reach the schema check")

    def test_metachar_slug_is_refused(self):
        for bad in ("has space", "UPPER", "back`tick", "-leading"):
            with self.subTest(slug=bad):
                with tempfile.TemporaryDirectory() as tmp:
                    project, decoy = self._decoy(Path(tmp), bad)
                    rc = self._run(project, self._payload(
                        decoy / "decisions" / "d.md", BLOCKING))
                    self.assertEqual(rc, 0)

    def test_decoy_fixture_is_live_for_a_safe_slug(self):
        # Control: the same fixture with a kebab-case slug DOES get judged, and
        # the same payload blocks. Without it, a decoy that silently failed to
        # resolve would make the two tests above pass for the wrong reason.
        with tempfile.TemporaryDirectory() as tmp:
            project, decoy = self._decoy(Path(tmp), "decoy-project")
            rc = self._run(project, self._payload(
                decoy / "decisions" / "d.md", BLOCKING))
            self.assertEqual(rc, 2,
                             "the decoy project must be live enough to block")


class TestZoneGuard(_GateHarness):
    """`if project_root is None: return 0` — a slug that exists in no zone.

    The historical shape was a hardcoded `vault/projects/<slug>`, which judged
    writes against a project directory that does not exist anywhere. shelf
    moves projects between zones without touching the breadcrumb, so the gate
    must resolve the same way every other component does.

    Scope note: deleting the `is None` line alone changes nothing observable,
    because the AttributeError it prevents is swallowed by the blanket
    `except Exception: return 0` three lines below. What these two tests
    falsify is the resolution itself: swap find_project_dir back to
    `vault / "projects" / slug` and they fail.
    """

    def test_project_in_no_zone_is_not_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, proot = self._fixture(root)
            shutil.rmtree(proot)  # demo now exists in no zone at all
            rc = self._run(project, self._payload(
                proot / "decisions" / "d.md", BLOCKING))
            self.assertEqual(rc, 0,
                             "a project that exists nowhere must not be gated")
            self.assertFalse(proot.exists(),
                             "the gate must never materialize a project dir")

    def test_shelved_project_is_found_not_missed(self):
        # Control: the same payload against a project that DOES exist, in a
        # non-active zone. Resolve it wrong and this returns 0 instead of 2.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp), zone="_archive")
            rc = self._run(project, self._payload(
                proot / "decisions" / "d.md", BLOCKING))
            self.assertEqual(rc, 2)


class TestSkipList(_GateHarness):
    """The hook's own exemptions (_SKIP_NAMES and the sessions/ folder) must
    hold even when the content would otherwise be blocked. Each target here
    uses the same payload TestBlocks proves is blocking, so a passing test
    demonstrates the exemption is doing the work, not that the content
    happens to be clean.
    """

    BLOCKING = "---\ntype: decision\n---\n\nB\n"

    def test_handoff_file_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(proot / "_handoff.md", self.BLOCKING))
            self.assertEqual(rc, 0)

    def test_index_file_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(proot / "_index.md", self.BLOCKING))
            self.assertEqual(rc, 0)

    def test_iteration_file_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(proot / "_iteration.md", self.BLOCKING))
            self.assertEqual(rc, 0)

    def test_brief_file_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(proot / "brief.md", self.BLOCKING))
            self.assertEqual(rc, 0)

    def test_sessions_dir_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(
                proot / "sessions" / "2026-07-28.md", self.BLOCKING))
            self.assertEqual(rc, 0)

    def test_legacy_dir_exempt(self):
        # `_legacy/` holds files that are non-conformant BY DESIGN. Every other
        # component exempts it (walk_project drops it from the walk, _cost and
        # shelf add it to their skip sets); the gate used to be the one place
        # that blocked writes to it.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(
                proot / "_legacy" / "old.md", self.BLOCKING))
            self.assertEqual(rc, 0)

    def test_nested_legacy_dir_exempt(self):
        # walk_project matches `_legacy` against every part of the relative
        # path, not just the first, so the gate must too. Exempting only the
        # project root would leave notes/_legacy/ blocked by the gate and
        # invisible to check and tidy at the same time.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(
                proot / "notes" / "_legacy" / "old.md", self.BLOCKING))
            self.assertEqual(rc, 0)

    def test_legacy_lookalike_is_still_gated(self):
        # Control: the exemption is the exact folder name, not a prefix. A
        # folder called `_legacy-notes/` is an ordinary folder.
        with tempfile.TemporaryDirectory() as tmp:
            project, proot = self._fixture(Path(tmp))
            rc = self._run(project, self._payload(
                proot / "_legacy-notes" / "old.md", self.BLOCKING))
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
