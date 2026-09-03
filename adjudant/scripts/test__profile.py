"""Tests for adjudant/scripts/_profile.py — the one file a build may differ in.

The profile exists because the twin used to fork source files to carry its
differences: a token threshold, a PATH probe, and a verb list. Every shared
edit then had to be made twice. The tests that matter here are the ones that
prove there is no second declaration to drift against: a missing profile must
raise, never fall back, and no shipped file may restate a number the profile
already holds.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _profile

SCRIPTS = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS.parent


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


MINIMAL = {
    "audience": "public",
    "description_suffix": "",
    "cost_warn_tokens": 10000,
    "capabilities": [],
}


class TestLoad(unittest.TestCase):

    def setUp(self):
        _profile.load.cache_clear()

    def tearDown(self):
        _profile.load.cache_clear()

    def test_missing_profile_raises_rather_than_defaulting(self):
        # The whole point: an inline default is a second declaration.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(_profile.ProfileError):
                _profile.load(Path(tmp) / "nope.json")

    def test_malformed_profile_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "build-profile.json"
            bad.write_text("{ not json")
            with self.assertRaises(_profile.ProfileError):
                _profile.load(bad)

    def test_unknown_audience_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = dict(MINIMAL, audience="staging")
            with self.assertRaises(_profile.ProfileError):
                _profile.load(_write(Path(tmp) / "p.json", payload))

    def test_missing_required_key_raises_and_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {k: v for k, v in MINIMAL.items() if k != "cost_warn_tokens"}
            with self.assertRaises(_profile.ProfileError) as ctx:
                _profile.load(_write(Path(tmp) / "p.json", payload))
            self.assertIn("cost_warn_tokens", str(ctx.exception))

    def test_load_is_cached_per_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp) / "p.json", MINIMAL)
            first = _profile.load(p)
            self.assertIs(_profile.load(p), first)


class TestShippedProfile(unittest.TestCase):
    """The real file in this tree, whichever build it is."""

    def test_audience_is_one_of_two(self):
        self.assertIn(_profile.audience(), ("full", "public"))

    def test_threshold_is_a_positive_int(self):
        self.assertIsInstance(_profile.cost_warn_tokens(), int)
        self.assertGreater(_profile.cost_warn_tokens(), 0)

    def test_capabilities_carry_every_field_the_consumers_read(self):
        for cap in _profile.capabilities():
            for field in ("id", "probe", "reference", "check_line",
                          "sitrep_line", "session_banner"):
                self.assertIn(field, cap)
                self.assertTrue(cap[field], f"{cap.get('id')}.{field} is empty")


class TestTheThresholdIsStatedOnce(unittest.TestCase):
    """The outcome, not the mechanism: change the number in one file and every
    consumer changes with it, and no shipped file keeps a copy to drift.

    It used to be four declarations of one fact: an int in `_cost.py`, the
    string "30000" in `connect.py`'s breadcrumb writer, and the number again in
    two docs. They agreed by luck, and only the twin's fork ever noticed.
    """

    def test_no_shipped_file_outside_the_profile_states_the_number(self):
        default = str(_profile.cost_warn_tokens())
        offenders = []
        for f in sorted(PLUGIN_ROOT.rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            if f.name == "build-profile.json" or f.name.startswith("test_"):
                continue
            if f.suffix not in (".py", ".md", ".sh", ".json"):
                continue
            if default in f.read_text(errors="replace"):
                offenders.append(str(f.relative_to(PLUGIN_ROOT)))
        self.assertEqual(
            offenders, [],
            f"{default} is restated outside build-profile.json: {offenders}")

    def test_a_changed_profile_moves_the_gate_and_the_breadcrumb(self):
        # A fresh interpreter, so the module-level constants are computed
        # against the temp profile rather than the shipped one.
        script = r"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _profile
tmp = Path(tempfile.mkdtemp())
(tmp / "p.json").write_text(json.dumps({
    "audience": "public", "description_suffix": "",
    "cost_warn_tokens": 12345, "capabilities": []}))
_profile.PROFILE_PATH = tmp / "p.json"
import _cost, connect
project = tmp / "proj"
project.mkdir()
connect.write_breadcrumb(project, tmp / "vault", "vault", "demo")
print(json.dumps({
    "gate": _cost.read_threshold(project),
    "breadcrumb": (project / ".claude" / "adjudant").read_text(),
}))
"""
        out = subprocess.run([sys.executable, "-c", script, str(SCRIPTS)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["gate"], 12345)
        self.assertIn("cost_warn_tokens: 12345", got["breadcrumb"])


class TestCapabilityProbing(unittest.TestCase):

    def setUp(self):
        _profile.load.cache_clear()
        self._path = os.environ.get("PATH", "")

    def tearDown(self):
        os.environ["PATH"] = self._path
        _profile.load.cache_clear()

    def _profile_with(self, tmp: Path, caps: list) -> Path:
        return _write(tmp / "p.json", dict(MINIMAL, capabilities=caps))

    CAP = {
        "id": "widget",
        "probe": "widget-brief",
        "reference": "reference/widget.md",
        "check_line": "Widget: present (widget-brief for orientation)",
        "sitrep_line": "Widget environment on this machine: run widget-brief",
        "session_banner": "- Widget detected: run widget-brief for orientation",
    }

    def test_absent_probe_yields_no_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.environ["PATH"] = str(root / "empty")
            orig = _profile.PROFILE_PATH
            _profile.PROFILE_PATH = self._profile_with(root, [self.CAP])
            try:
                self.assertEqual(_profile.present_capabilities(), [])
            finally:
                _profile.PROFILE_PATH = orig

    def test_present_probe_yields_the_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binpath = root / "bin"
            binpath.mkdir()
            fake = binpath / "widget-brief"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            os.environ["PATH"] = str(binpath)
            orig = _profile.PROFILE_PATH
            _profile.PROFILE_PATH = self._profile_with(root, [self.CAP])
            try:
                got = _profile.present_capabilities()
                self.assertEqual([c["id"] for c in got], ["widget"])
            finally:
                _profile.PROFILE_PATH = orig

    def test_empty_registry_never_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = _profile.PROFILE_PATH
            _profile.PROFILE_PATH = self._profile_with(Path(tmp), [])
            try:
                self.assertEqual(_profile.present_capabilities(), [])
            finally:
                _profile.PROFILE_PATH = orig

    def test_the_session_banner_prints_one_line_per_present_capability(self):
        # The hook's whole contract: what a fresh session actually sees.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binpath = root / "bin"
            binpath.mkdir()
            fake = binpath / "widget-brief"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            prof = self._profile_with(root, [self.CAP])
            script = (
                "import sys; sys.path.insert(0, sys.argv[1]);"
                "import _profile; from pathlib import Path;"
                "_profile.PROFILE_PATH = Path(sys.argv[2]);"
                "sys.exit(_profile.main(['--session-banner']))")
            env = dict(os.environ, PATH=str(binpath))
            with_it = subprocess.run(
                [sys.executable, "-c", script, str(SCRIPTS), str(prof)],
                capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(with_it.returncode, 0, with_it.stderr)
            self.assertEqual(with_it.stdout.splitlines(),
                             [self.CAP["session_banner"]])

            env["PATH"] = str(root / "empty")
            without = subprocess.run(
                [sys.executable, "-c", script, str(SCRIPTS), str(prof)],
                capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(without.returncode, 0, without.stderr)
            self.assertEqual(without.stdout, "")


if __name__ == "__main__":
    unittest.main()
