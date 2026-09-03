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

# What the public build SHIPS, which is more than the plugin directory: the
# repo-root `scripts/` travels too, and in the twin it names `cabinet-of-imd`
# in two comments. `cabinet` was on the banned list all along; the scope was
# what let it through.
#
# NOT the whole repository root. In the full marketplace that root holds
# sibling plugins -- `cabinet-of-imd` is a crew-persona plugin and
# `tui-toolbox` mentions a nightly build -- which carry these names by right
# and never reach the twin. Widening to the root failed on exactly those, which
# is the check being wrong rather than the tree being dirty.
_SHIPPED_SIBLINGS = ("scripts",)


def scan_roots() -> tuple:
    """Derived on every call, never frozen at import.

    generate_twin repoints PLUGIN_ROOT to ask about a tree that is not its
    own. A tuple computed at import time ignored that and answered about the
    generator's own tree instead, which passed when the module was freshly
    imported and lied when it came from sys.modules. The check was right and
    the caching was wrong.
    """
    return (PLUGIN_ROOT,) + tuple(
        d for d in (PLUGIN_ROOT.parent / n for n in _SHIPPED_SIBLINGS)
        if d.is_dir())

# Identifiers that must not appear outside the allowlist. Kept as fragments so
# a compound (hubspot-nightly, ob/cabinet) is caught by its root.
FORBIDDEN = (
    "hubspot", "nightly", "cabinet",
    "bostrol", "kevijntje", "henske", "jonasty",
    # Both names of the full marketplace. `onnozelaer-claude-marketplace` was
    # renamed to `toolshed`, and the rename escaped this list: the gate ran
    # green while `TomVDH/toolshed` sat in three scanned files. Both repos are
    # public, so this is separation of the two products rather than secrecy --
    # but a banned term that survives a rename is a gate that does not work.
    # The author's handle is NOT banned: the twin IS `TomVDH/furtive-follies`
    # and says so in its README, manifest, field guide and onboarding script.
    "onnozelaer", "toolshed",
    "crew-roster",          # the crew personas by their directory name
)
# "Tom" as a word, not as a substring (custom, atom, bottom all contain it).
FORBIDDEN_WORDS = ("Tom",)

# Files where these names are the point, not a leak.
ALLOWLIST = {
    "scripts/test_no_personal_identifiers.py",
    # Repository identity. Each build states its OWN marketplace in its own
    # install line, and the generator rewrites that line from the target's
    # `repository` URL, so main saying `TomVDH/toolshed` here is correct and
    # the twin never inherits it.
    ".claude-plugin/plugin.json",
    "README.md",
    "GUIDE.md",
}

# A run of seven or more digits standing alone. An account, portal or object
# id looks like this; a semantic version, a date and a decimal do not.
LIVE_ID_RE = re.compile(r"(?<![\d.\-])\d{7,}(?![\d.])")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
# Nothing here is a suffix list. An eight-entry allowlist of extensions left
# the four `.base` dashboard templates unscanned, and they ship to the public
# repository like everything else. A maintained list of what to look at is a
# list of what you forgot: the same shape had the AGENTS.md path check 61%
# wrong until it stopped deciding by extension. A file is scanned when its
# bytes decode as text, which needs no list and cannot fall behind.
_SNIFF_BYTES = 8192


def _is_text(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(_SNIFF_BYTES)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        # A multi-byte character may straddle the cut; retry without the tail.
        try:
            chunk[:-4].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def _rel(path: Path) -> str:
    """Path as written in the ALLOWLIST: plugin-relative where it can be."""
    try:
        return path.relative_to(PLUGIN_ROOT).as_posix()
    except ValueError:
        return path.relative_to(scan_roots()[-1]).as_posix()


def _files() -> list[Path]:
    out: list[Path] = []
    seen: set = set()
    for root in scan_roots():
        for path in sorted(root.rglob("*")):
            if path in seen:
                continue
            if not path.is_file() or path.is_symlink():
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            seen.add(path)
            if _rel(path) in ALLOWLIST:
                continue
            if not _is_text(path):
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
        rel = _rel(path)
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
        # Resolved against every scan root, because the allowlist spans both:
        # the plugin directory and the shipped repo-root scripts/. An entry
        # that stops matching a real file is an exemption nobody is using and
        # a name nobody is checking.
        for rel in sorted(ALLOWLIST):
            roots = scan_roots()
            self.assertTrue(any((root / rel).is_file() for root in roots),
                            f"{rel} matches no file under {roots}")

    def test_the_marketplace_slug_is_banned_under_both_its_names(self):
        # The gate ran green while `TomVDH/toolshed` sat in three scanned
        # files, because only the pre-rename name was listed.
        for name in ("onnozelaer", "toolshed"):
            self.assertIn(name, FORBIDDEN)

    def test_the_author_handle_is_not_banned(self):
        # The public twin IS TomVDH/furtive-follies and says so in its README,
        # manifest, field guide and onboarding script. Banning the handle would
        # fail the gate on the repository's own identity.
        self.assertNotIn("tomvdh", [f.lower() for f in FORBIDDEN])

    def test_every_text_file_is_scanned_whatever_its_extension(self):
        # `.base` was not on the old eight-entry suffix list, so the four
        # dashboard templates shipped to a public repository unscanned.
        scanned = {p for p in _files()}
        bases = [p for p in PLUGIN_ROOT.rglob("*.base") if p.is_file()]
        self.assertTrue(bases, "the fixture assumes .base templates exist")
        for b in bases:
            self.assertIn(b, scanned, "a shipped text file went unscanned")

    def test_a_planted_leak_in_an_odd_extension_is_caught(self):
        # The gate must FAIL when it should. Written and removed inside the
        # test so the tree never carries the name.
        planted = PLUGIN_ROOT / "skills" / "adjudant" / "templates" / "_leakcheck.base"
        planted.write_text("filters:\n  - 'file.path.contains(\"hubspot\")'\n")
        try:
            self.assertTrue(any("_leakcheck.base" in f for f in leaks()),
                            "the gate did not catch a planted identifier")
        finally:
            planted.unlink()

    def test_a_binary_file_is_skipped_without_crashing(self):
        planted = PLUGIN_ROOT / "skills" / "adjudant" / "templates" / "_leakcheck.bin"
        planted.write_bytes(b"\x00\x01\x02hubspot\xff")
        try:
            self.assertFalse(any("_leakcheck.bin" in f for f in leaks()))
        finally:
            planted.unlink()


class TestNoLiveIdentifiersInShippedProse(unittest.TestCase):
    """A denylist of names cannot catch a number.

    Writing the technical-authoring reference, I copied a real example out of
    a project vault and shipped a live account id, an object id and its
    fully-qualified name into a public plugin. Every name-based check passed:
    the terms were not on the list, because a list of names never contains the
    digits nobody thought to add.

    So this is a SHAPE rule, and it is deliberately narrow. Code and tests
    carry long digit runs for good reasons -- a digit alphabet, a timestamp
    stem, milliseconds in a day -- so it looks only at shipped prose, outside
    fenced code. Measured over the whole plugin: 9 legitimate long runs in
    code, 0 in prose.
    """

    def _prose_lines(self, path):
        fence = False
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fence = not fence
                continue
            if not fence:
                yield n, line

    def test_no_long_numeric_id_in_shipped_prose(self):
        offenders = []
        for path in sorted((PLUGIN_ROOT / "skills").rglob("*.md")):
            for n, line in self._prose_lines(path):
                for m in LIVE_ID_RE.findall(line):
                    offenders.append(f"{path.relative_to(PLUGIN_ROOT)}:{n}: {m}")
        self.assertEqual(offenders, [],
                         "a live account or object id reached shipped prose:\n  "
                         + "\n  ".join(offenders))

    def test_the_rule_catches_a_planted_id(self):
        planted = PLUGIN_ROOT / "skills" / "adjudant" / "reference" / "_idcheck.md"
        planted.write_text("Portal: 50629780 and object 2-62057387.\n")
        try:
            found = [m for _, line in self._prose_lines(planted)
                     for m in LIVE_ID_RE.findall(line)]
            self.assertIn("50629780", found)
        finally:
            planted.unlink()

    def test_a_date_stem_and_a_constant_are_not_ids(self):
        self.assertEqual(LIVE_ID_RE.findall("built 2026-09-01 in 1.2.3"), [])
        self.assertEqual(LIVE_ID_RE.findall("version 10.20.30"), [])


if __name__ == "__main__":
    unittest.main()
