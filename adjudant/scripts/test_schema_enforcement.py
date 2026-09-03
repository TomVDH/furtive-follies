"""Acceptance test for plan 2: the template is the schema, and it bites.

Before v3 nothing ever compared a real vault file to its template, which is
how the vault reached 45 type values, 110 frontmatter keys and 420 tags.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from _template_schema import FIELD_SCHEMA, TEMPLATES_DIR, load_schema
from _vault_walk import schema_drift_for_text


class TestSchemaBites(unittest.TestCase):
    """`schema_drift_for_text` returns Optional[dict] and omits a key entirely
    when that class of drift is absent, so every assertion below goes through
    `.get()` with a default rather than indexing."""

    def _drift(self, text: str, rel: str) -> dict:
        return schema_drift_for_text(text, rel) or {}

    def test_a_missing_required_field_is_drift(self):
        text = "---\ntype: decision\ncreated: 2026-09-01\nupdated: 2026-09-01\n---\n\n# X\n"
        drift = self._drift(text, "decisions/x.md")
        self.assertIn("status", drift.get("missing_required", []))

    def test_a_retired_field_is_unknown(self):
        text = ("---\ntype: note\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
                "tags:\n  - note\n---\n\n# X\n")
        drift = self._drift(text, "notes/x.md")
        self.assertIn("tags", drift.get("unknown_fields", []))

    def test_an_off_vocabulary_status_is_reported(self):
        text = ("---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
                "status: obsolete\n---\n\n# X\n")
        drift = self._drift(text, "tasks/x.md")
        invalid = drift.get("status_invalid") or {}
        self.assertEqual(invalid.get("value"), "obsolete",
                         "the value someone had to invent is still accepted")

    def test_dropped_is_now_a_real_status(self):
        text = ("---\ntype: task\ncreated: 2026-09-01\nupdated: 2026-09-01\n"
                "status: dropped\n---\n\n# X\n")
        drift = self._drift(text, "tasks/x.md")
        self.assertIsNone(drift.get("status_invalid"))

    def test_editing_a_template_changes_enforcement(self):
        """One declaration, proven end to end."""
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for p in TEMPLATES_DIR.glob("*.md"):
                shutil.copy2(p, tmp / p.name)
            self.assertIn("verified", load_schema(tmp)["doc"]["required"])
            doc = tmp / "doc.md"
            doc.write_text("\n".join(
                ln for ln in doc.read_text().splitlines()
                if not ln.startswith("verified:")) + "\n")
            self.assertNotIn("verified", load_schema(tmp)["doc"]["required"])


class TestTagsAreGone(unittest.TestCase):

    def test_no_kind_accepts_tags(self):
        for kind, spec in FIELD_SCHEMA.items():
            self.assertNotIn("tags", spec["required"] | spec["optional"],
                             f"{kind} still accepts tags")

    def test_the_bucket_constants_are_gone(self):
        import _vault_walk
        src = Path(_vault_walk.__file__).read_text()
        for gone in ("BUCKET_A_TYPES", "BUCKET_B_MIGRATIONS",
                     "BUCKET_D_TAG_EXACT", "BUCKET_D_TAG_PREFIXES",
                     "PROJECT_TYPE_TAGS", "CREW_NAMES",
                     "VAGUE_TOPICAL_TAGS", "PROJECT_STATUS_VALUES"):
            self.assertNotIn(f"{gone}:", src, f"{gone} survived")


if __name__ == "__main__":
    unittest.main()


class TestNoWriterEmitsTags(unittest.TestCase):
    """Retiring tags from the schema is not the same as retiring them from the
    writers. An adversarial prover ran the real connect CLI against a fresh
    vault and found five of the seven files it creates carrying a `tags:`
    block that the schema now rejects.
    """

    def test_no_source_file_writes_a_tags_block(self):
        import re as _re
        scripts = Path(__file__).resolve().parent
        hooks = scripts.parent / "hooks" / "scripts"
        offenders = []
        for d in (scripts, hooks):
            for py in sorted(d.glob("*.py")):
                if py.name.startswith("test_"):
                    continue
                for i, line in enumerate(py.read_text().splitlines(), 1):
                    # Any `tags:` inside a string literal, however it is
                    # spliced. The first version of this test required a quote
                    # immediately before it and missed a site written as
                    # "...\\ntags:\\n  - index...", which is exactly the blind
                    # spot the provers keep finding in fixtures.
                    if _re.search(r'(["\']|\\n)tags:', line):
                        offenders.append(f"{py.name}:{i}")
        self.assertEqual(offenders, [],
                         f"these still write a tags block: {offenders}")

    def test_connect_creates_no_file_carrying_tags(self):
        import subprocess, os
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            proj, vault = tmp / "proj", tmp / "vault"
            proj.mkdir(); vault.mkdir()
            env = dict(os.environ); env.pop("OB_VAULT", None)
            subprocess.run(
                ["python3", str(Path(__file__).resolve().parent / "connect.py"),
                 "--project-root", str(proj), "--vault-path", str(vault),
                 "--slug", "demo-proj", "--project-type", "coding",
                 "--project-name", "Demo"],
                env=env, capture_output=True, text=True, timeout=60)
            tagged = [str(p.relative_to(vault)) for p in vault.rglob("*.md")
                      if "\ntags:" in p.read_text() or p.read_text().startswith("tags:")]
            self.assertEqual(tagged, [], f"connect wrote tags into: {tagged}")


class TestNoWriterHandBuildsAnIndex(unittest.TestCase):
    """Plan 4 retired every index surface but two, both generated by
    `_index_gen`. An adversarial prover found a third still being hand-built.

    `posttooluse-commit-log.py:_upsert_index` wrote `releases/_index.md` on
    every release commit, and its row used a raw bare-stem wikilink. Plan 4
    also stopped resolution accepting bare stems, so that hook was emitting a
    link that adjudant's own resolver would then report as broken.
    """

    def _sources(self):
        scripts = Path(__file__).resolve().parent
        for d in (scripts, scripts.parent / "hooks" / "scripts"):
            for py in sorted(d.glob("*.py")):
                if not py.name.startswith("test_"):
                    yield py

    def test_only_the_generator_writes_an_index(self):
        import re as _re
        offenders = []
        for py in self._sources():
            if py.name == "_index_gen.py":
                continue
            for i, line in enumerate(py.read_text().splitlines(), 1):
                if _re.search(r'["\'](---\\n)?type: index', line):
                    offenders.append(f"{py.name}:{i}")
        self.assertEqual(offenders, [],
                         f"these hand-build index frontmatter: {offenders}")

    def test_no_writer_emits_a_bare_stem_wikilink(self):
        import re as _re
        offenders = []
        for py in self._sources():
            if py.name == "_place.py":
                continue
            for i, line in enumerate(py.read_text().splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                # Only lines that BUILD a link: an f-string containing [[.
                # Matching every [[ also caught regex character classes in
                # _vault_walk and _agents_reach. That is the same fixture
                # blind spot the provers keep finding in this programme, so it
                # is narrowed here rather than left to produce noise.
                if 'f"' not in line and "f'" not in line:
                    continue
                if _re.search(r"re\.(compile|search|match|finditer|sub)", line):
                    continue
                for m in _re.finditer(r"\[\[([A-Za-z0-9_{}.\- ]+)", line):
                    tgt = m.group(1)
                    if "/" not in tgt:
                        offenders.append(f"{py.name}:{i} -> [[{tgt}")
        self.assertEqual(offenders, [],
                         f"bare-stem links that will not resolve: {offenders}")
