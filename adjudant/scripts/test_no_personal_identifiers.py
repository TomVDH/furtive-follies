"""The full build and the public build ship the same files, so the shared
files must name nobody.

The twin used to rewrite fixtures on the way out: hubspot-nightly to acme-web,
ob/cabinet to ob/legacy, "Tom's vault" to "a real vault". That rewrite is why
several test files could not be shared, and skipping it would publish a client
name and (were they ever reintroduced) four crew nicknames to a public
repository. FORBIDDEN bans the crew names defensively even though nothing in
this tree currently carries them: the tag-bucket data they used to live in
(scripts/build-profile.json's old `tags` block) is gone, not merely moved.

The two-entry ALLOWLIST is deliberately narrow: this file names its own
examples, and .claude-plugin/plugin.json is where the author's and the
repository's public identity is meant to live. Everywhere else is neutral.
"""

import re
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Identifiers that must not appear outside the allowlist. Kept as fragments so
# a compound (hubspot-nightly, ob/cabinet) is caught by its root.
FORBIDDEN = (
    "hubspot", "nightly", "cabinet",
    "bostrol", "kevijntje", "henske", "jonasty",
    "onnozelaer",
)
# "Tom" as a word, not as a substring (custom, atom, bottom all contain it).
FORBIDDEN_WORDS = ("Tom",)

# Files where these names are the point, not a leak.
ALLOWLIST = {
    "scripts/test_no_personal_identifiers.py",
    ".claude-plugin/plugin.json",       # author and repository identity
}

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
TEXT_SUFFIXES = {".py", ".sh", ".md", ".json", ".html", ".yaml", ".yml", ".txt"}


def _files() -> list[Path]:
    out = []
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        if path.relative_to(PLUGIN_ROOT).as_posix() in ALLOWLIST:
            continue
        out.append(path)
    return out


def leaks() -> list[str]:
    """Every forbidden identifier outside the allowlist, as path:line: term."""
    found: list[str] = []
    word_res = [(w, re.compile(rf"\b{re.escape(w)}\b")) for w in FORBIDDEN_WORDS]
    for path in _files():
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(PLUGIN_ROOT).as_posix()
        for n, line in enumerate(lines, 1):
            low = line.lower()
            for term in FORBIDDEN:
                if term in low:
                    found.append(f"{rel}:{n}: {term}")
            for word, rx in word_res:
                if rx.search(line):
                    found.append(f"{rel}:{n}: {word}")
    return found


class TestNoPersonalIdentifiers(unittest.TestCase):

    def test_shared_files_name_nobody(self):
        found = leaks()
        self.assertEqual(found, [], "personal identifiers in shared files:\n  "
                                    + "\n  ".join(found))

    def test_the_allowlist_still_points_at_real_files(self):
        for rel in sorted(ALLOWLIST):
            self.assertTrue((PLUGIN_ROOT / rel).is_file(), rel)


if __name__ == "__main__":
    unittest.main()
