"""Tests for adjudant/scripts/_handoff_freshness.py.

The shared freshness primitives used by both the PreCompact hook and the
`/adjudant sync` verb.
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import _handoff_freshness as pc

NOW = datetime(2026, 6, 1, 14, 0)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ============================================================
# parse_next_line
# ============================================================


class TestParseNextLine(unittest.TestCase):

    def test_inline_plain(self):
        self.assertEqual(pc.parse_next_line("NEXT: wire the UI steps"), "wire the UI steps")

    def test_inline_bold(self):
        self.assertEqual(pc.parse_next_line("**NEXT:** ship the thing"), "ship the thing")

    def test_inline_bulleted(self):
        self.assertEqual(pc.parse_next_line("- NEXT — review the PR"), "review the PR")

    def test_heading_form(self):
        text = "## NEXT\n\n- finish the migration\n- later thing\n"
        self.assertEqual(pc.parse_next_line(text), "finish the migration")

    def test_first_inline_wins_over_body(self):
        text = "intro\nNEXT: do A\nmore\n## NEXT\n- do B\n"
        self.assertEqual(pc.parse_next_line(text), "do A")

    def test_none_when_absent(self):
        self.assertIsNone(pc.parse_next_line("just some notes\nno next here"))


# ============================================================
# traffic light + age formatting
# ============================================================


    def test_compound_next_word_not_a_directive(self):
        # Regression: "Next-day ..." prose matched via the bare-hyphen separator
        self.assertIsNone(pc.parse_next_line("Next-day retry logic is planned.\n"))

    def test_space_hyphen_separator_still_works(self):
        self.assertEqual(pc.parse_next_line("NEXT - ship it\n"), "ship it")

class TestTrafficLightAndAge(unittest.TestCase):

    def test_green_under_2h(self):
        self.assertEqual(pc.traffic_light(1.5), "\U0001f7e2")

    def test_yellow_2_to_8h(self):
        self.assertEqual(pc.traffic_light(6.0), "\U0001f7e1")

    def test_red_over_8h(self):
        self.assertEqual(pc.traffic_light(11.0), "\U0001f534")

    def test_white_when_unknown(self):
        self.assertEqual(pc.traffic_light(None), "⚪")

    def test_age_minutes_hours_days(self):
        self.assertEqual(pc.fmt_age(0.5), "30m")
        self.assertEqual(pc.fmt_age(6.0), "6h")
        self.assertEqual(pc.fmt_age(72.0), "3d")
        self.assertEqual(pc.fmt_age(None), "unknown")

    def test_age_hours_never_negative(self):
        future = datetime(2026, 6, 1, 15, 0)
        self.assertEqual(pc.age_hours(future, NOW), 0.0)


# ============================================================
# latest_today_activity
# ============================================================


class TestLatestTodayActivity(unittest.TestCase):

    def test_reads_last_time_with_date_from_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp) / ".remember"
            _write(rd / "today-2026-06-01.md", "- 09:00 started\n- 13:30 did a thing\n")
            dt = pc.latest_today_activity(rd)
            self.assertEqual(dt, datetime(2026, 6, 1, 13, 30))

    def test_picks_newest_across_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp) / ".remember"
            _write(rd / "today-2026-05-30.md", "- 18:00 old\n")
            _write(rd / "today-2026-06-01.md", "- 08:15 newer\n")
            dt = pc.latest_today_activity(rd)
            self.assertEqual(dt, datetime(2026, 6, 1, 8, 15))

    def test_none_when_no_today_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp) / ".remember"
            rd.mkdir(parents=True)
            self.assertIsNone(pc.latest_today_activity(rd))


# ============================================================
# latest_session_activity (markers ignored)
# ============================================================


class TestLatestSessionActivity(unittest.TestCase):

    def test_ignores_hook_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "2026-06-01.md"
            _write(sf, "- 10:00 · Added: [[x]]\n- 23:59 · paused (compaction)\n")
            dt = pc.latest_session_activity(sf, NOW)
            self.assertEqual(dt, datetime(2026, 6, 1, 10, 0))  # 23:59 marker ignored

    def test_none_when_only_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "2026-06-01.md"
            _write(sf, "- 09:00 · session ended\n")
            self.assertIsNone(pc.latest_session_activity(sf, NOW))

    def test_started_and_resumed_markers_are_noise_too(self):
        # Regression: a note holding ONLY hook churn (started/resumed pairs)
        # counted as real activity and raised a false STALE banner.
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "2026-06-01.md"
            _write(sf, "- 10:00 · session started\n\n--- 10:05 session resumed ---\n")
            self.assertIsNone(pc.latest_session_activity(sf, NOW))

    def test_activity_dated_from_filename_not_from_now(self):
        # Regression: a midnight-straddling sync read yesterday's note and
        # stamped its 23:55 with TODAY's date, inventing future activity.
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "2026-05-31.md"
            _write(sf, "- 23:55 · wrote the fix\n")
            dt = pc.latest_session_activity(sf, NOW)
            self.assertEqual(dt, datetime(2026, 5, 31, 23, 55))


# ============================================================
# latest_session_file (shared midnight fallback)
# ============================================================


class TestLatestSessionFile(unittest.TestCase):

    def test_prefers_todays_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            _write(sessions / "2026-06-01.md", "## Log\n")
            _write(sessions / "2026-05-31.md", "## Log\n")
            got = pc.latest_session_file(sessions, "2026-06-01")
            self.assertEqual(got.name, "2026-06-01.md")

    def test_falls_back_to_latest_dated_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            _write(sessions / "2026-05-30.md", "## Log\n")
            _write(sessions / "2026-05-31.md", "## Log\n")
            _write(sessions / "abcd-ef-gh.md", "decoy\n")  # 4-2-2 shape, not a date
            got = pc.latest_session_file(sessions, "2026-06-01")
            self.assertEqual(got.name, "2026-05-31.md")

    def test_returns_today_path_when_dir_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = pc.latest_session_file(Path(tmp), "2026-06-01")
            self.assertEqual(got.name, "2026-06-01.md")
            self.assertFalse(got.exists())


# ============================================================
# shared handoff renderer (single source of truth for both writers)
# ============================================================


class TestRenderHandoff(unittest.TestCase):

    def test_render_shape_and_body_normalization(self):
        fm = pc.HANDOFF_FRONTMATTER_TEMPLATE.format(
            slug="p", today="2026-06-01", source_stem="now")
        out = pc.render_handoff("p", "2026-06-01", "09:30", "now.md",
                                "", "body line\n\n\n", fm)
        self.assertIn("# Handoff: p\n", out)
        self.assertIn("*Mirrored from `.remember/now.md` on 2026-06-01 09:30.*", out)
        self.assertTrue(out.endswith("---\n\nbody line\n"))

    def test_template_carries_source_stem(self):
        fm = pc.HANDOFF_FRONTMATTER_TEMPLATE.format(
            slug="p", today="2026-06-01", source_stem="remember")
        self.assertIn("source: remember", fm)

    def test_rendered_handoff_has_no_em_dash(self):
        # voice.md: no em dashes in vault writes. The old hook heading had one.
        fm = pc.HANDOFF_FRONTMATTER_TEMPLATE.format(
            slug="p", today="2026-06-01", source_stem="now")
        out = pc.render_handoff("p", "2026-06-01", "09:30", "now.md",
                                pc.freshness_header("\U0001f7e2", "1h", None, False) + "\n\n",
                                "plain body\n", fm)
        self.assertNotIn("—", out)


class TestPreservedFrontmatter(unittest.TestCase):

    def test_keeps_custom_fields_and_bumps_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            handoff = Path(tmp) / "_handoff.md"
            handoff.write_text(
                "---\ntype: handoff\ncodename: falcon\nupdated: 2026-05-01\n---\n\nbody\n")
            block = pc.preserved_frontmatter(handoff, "2026-06-01")
            self.assertIn("codename: falcon", block)
            self.assertIn("updated: 2026-06-01", block)

    def test_none_when_missing_or_fenceless(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pc.preserved_frontmatter(Path(tmp) / "nope.md", "2026-06-01"))
            plain = Path(tmp) / "plain.md"
            plain.write_text("no frontmatter here\n")
            self.assertIsNone(pc.preserved_frontmatter(plain, "2026-06-01"))


# ============================================================
# compute_freshness (stale logic + end-to-end header)
# ============================================================


class TestComputeFreshness(unittest.TestCase):

    def _project(self, tmp, today_lines, session_lines):
        root = Path(tmp)
        _write(root / ".remember" / "today-2026-06-01.md", today_lines)
        _write(root / ".remember" / "remember.md", "NEXT: do the next thing\n\nbody")
        sf = root / "sessions" / "2026-06-01.md"
        _write(sf, session_lines)
        return root, sf

    def test_fresh_not_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, sf = self._project(tmp, "- 13:30 worked\n", "- 12:00 · Added: [[x]]\n")
            light, age, nxt, stale = pc.compute_freshness(
                root, (root / ".remember" / "remember.md").read_text(),
                root / ".remember" / "remember.md", sf, NOW)
            self.assertEqual(nxt, "do the next thing")
            self.assertFalse(stale)            # session 12:00 < activity 13:30
            self.assertEqual(light, "\U0001f7e2")  # 30m old → green

    def test_stale_when_session_newer_than_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, sf = self._project(tmp, "- 09:00 worked\n", "- 13:45 · Added: [[y]]\n")
            light, age, nxt, stale = pc.compute_freshness(
                root, (root / ".remember" / "remember.md").read_text(),
                root / ".remember" / "remember.md", sf, NOW)
            self.assertTrue(stale)             # session 13:45 > activity 09:00

    def test_header_renders_light_age_next_and_stale(self):
        block = pc.freshness_header("\U0001f534", "11h", "ship it", True)
        self.assertIn("handoff age: 11h", block)
        self.assertIn("NEXT: ship it", block)
        self.assertIn("STALE", block)

    def test_header_next_not_set(self):
        block = pc.freshness_header("\U0001f7e2", "1h", None, False)
        self.assertIn("NEXT: (not set)", block)
        self.assertNotIn("STALE", block)


class TestActivityTimeParsing(unittest.TestCase):
    """Finding 31: the activity regex matched any d:dd token, so prose like
    "ratio 3:45" skewed the stale banner. Activity is a line that LEADS with
    markup + a time: remember headers and session bullets."""

    def test_prose_time_does_not_register_as_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            (rd / "today-2026-07-30.md").write_text(
                "## 01:00 | main\nDiscussed the golden ratio 3:45 idea.\n")
            got = pc.latest_today_activity(rd)
            self.assertEqual((got.hour, got.minute), (1, 0))

    def test_range_header_takes_the_end_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = Path(tmp)
            (rd / "today-2026-07-30.md").write_text(
                "## 18:34-23:00 | main\nwork happened\n")
            got = pc.latest_today_activity(rd)
            self.assertEqual((got.hour, got.minute), (23, 0))

    def test_session_bullet_takes_leading_time_not_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "2026-07-30.md"
            note.write_text("## Log\n- 14:02 · discussed the 3:45 ratio\n")
            got = pc.latest_session_activity(
                note, datetime(2026, 7, 30))
            self.assertEqual((got.hour, got.minute), (14, 2))


class TestLatestSessionFutureBound(unittest.TestCase):
    """Finding 19: `sorted(glob)[-1]` with no upper bound let a future-dated
    note (clock skew, restored backup) permanently absorb midnight-straddle
    appends. The glob's digit classes also admit impossible dates."""

    def test_future_and_impossible_dates_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            past = sessions / "2026-01-01.md"
            past.write_text("## Log\n")
            (sessions / "2029-12-31.md").write_text("## Log\n")   # future
            (sessions / "2026-99-99.md").write_text("## Log\n")   # impossible
            got = pc.latest_session_file(sessions, "2026-07-30")
            self.assertEqual(got, past)

    def test_only_future_notes_fall_back_to_todays_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            (sessions / "2029-12-31.md").write_text("## Log\n")
            got = pc.latest_session_file(sessions, "2026-07-30")
            self.assertEqual(got, sessions / "2026-07-30.md")


if __name__ == "__main__":
    unittest.main()
