"""The twin's guided vault setup must exist in main before any regeneration.

Before v3 the twin held the only copy of suggest_vault_roots(), --create-vault,
and the runbook that drives them. Plan 1 back-ported the Python. This module is
the standing proof that all four pieces are here, because a regeneration that
runs without them deletes the twin's whole onboarding story and reports success.

BACKPORT_MARKERS is the machine-readable form: scripts/generate_twin.py refuses
to run when any entry is missing.
"""

import inspect
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# path relative to the plugin root -> substring that proves the back-port
BACKPORT_MARKERS = {
    "scripts/_vault_walk.py": "def suggest_vault_roots(",
    "scripts/connect.py": "--suggest-vaults",
    "skills/adjudant/reference/connect.md": "No vault yet? Guided location setup",
}


def missing_markers(plugin_root: Path = PLUGIN_ROOT) -> list[str]:
    """Which back-port markers are absent. Empty means the back-port is whole."""
    gone = []
    for rel, marker in sorted(BACKPORT_MARKERS.items()):
        path = plugin_root / rel
        try:
            text = path.read_text()
        except OSError:
            gone.append(f"{rel}: unreadable")
            continue
        if marker not in text:
            gone.append(f"{rel}: missing {marker!r}")
    return gone


class TestBackportIsWhole(unittest.TestCase):

    def test_every_marker_present(self):
        self.assertEqual(missing_markers(), [],
                         "plan 1's back-port is incomplete; do not regenerate the twin")

    def test_suggest_vault_roots_returns_the_documented_shape(self):
        from _vault_walk import suggest_vault_roots
        for entry in suggest_vault_roots():
            self.assertTrue(Path(entry["path"]).is_dir(), entry["path"])
            self.assertTrue(entry["label"])
            self.assertIn(entry["kind"], ("local", "cloud"))
            self.assertIsInstance(entry["recommended"], bool)

    def test_describe_vault_root_takes_three_arguments(self):
        from _vault_walk import _describe_vault_root
        params = list(inspect.signature(_describe_vault_root).parameters)
        self.assertEqual(params, ["root", "home", "is_local"])

    def test_connect_accepts_both_guided_flags(self):
        import connect
        source = Path(inspect.getsourcefile(connect)).read_text()
        self.assertIn("--suggest-vaults", source)
        self.assertIn("--create-vault", source)

    def test_the_runbook_names_the_flags_it_drives(self):
        # A doc that says "guided setup" without naming the flags cannot be
        # followed. This is the half plan 1 did not back-port.
        doc = (PLUGIN_ROOT / "skills" / "adjudant" / "reference" / "connect.md").read_text()
        self.assertIn("--suggest-vaults", doc)
        self.assertIn("--create-vault", doc)
        self.assertIn("cloud-sync", doc)


if __name__ == "__main__":
    unittest.main()
