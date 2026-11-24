"""Tests for the Foc&US logic against the acceptance conditions in report Section 5.

Run from the foc_us folder:  python -m unittest discover -s tests -v
"""
import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.alarm import AlarmController                      # noqa: E402
from core.session import SessionTracker                      # noqa: E402
from core.settings import DEFAULTS, load_settings, record_consent  # noqa: E402

FPS = 10


def settings(**kw):
    s = dict(DEFAULTS, posture_delay=5, attention_delay=5)
    s.update(kw)
    return s


class AlarmTests(unittest.TestCase):
    def run_alarm(self, alarm, s, start, end, posture_bad=False, gaze_away=False, **kw):
        # time from the step index, so 5.0 is exactly 5.0 (no float drift)
        times = [start + i / FPS for i in range(round((end - start) * FPS))]
        return [(round(t, 1), alarm.update(t, posture_bad, gaze_away, s, **kw)) for t in times]

    def test_fr07_alert_starts_after_delay(self):
        a, s = AlarmController(), settings(alert_persistence="until_corrected")
        states = dict(self.run_alarm(a, s, 0, 6, posture_bad=True))
        self.assertFalse(states[4.9])
        self.assertTrue(states[5.0])
        self.assertEqual(a.kind, "posture")

    def test_fr08_until_corrected_stays_on(self):
        a, s = AlarmController(), settings(alert_persistence="until_corrected")
        self.run_alarm(a, s, 0, 6, gaze_away=True)
        self.assertTrue(all(on for _, on in self.run_alarm(a, s, 6, 200, gaze_away=True)))

    def test_fr07_alert_ends_when_corrected(self):
        a, s = AlarmController(), settings(alert_persistence="until_corrected")
        self.run_alarm(a, s, 0, 10, gaze_away=True)
        self.assertFalse(a.update(10.1, False, False, s))

    def test_fr08_once_fires_one_burst_per_episode(self):
        a, s = AlarmController(), settings(alert_persistence="once")
        states = dict(self.run_alarm(a, s, 0, 300, posture_bad=True))
        on = [t for t, v in states.items() if v]
        self.assertEqual((min(on), max(on)), (5.0, 8.9))
        a.update(300, False, False, s)                       # episode ends
        states = dict(self.run_alarm(a, s, 301, 310, posture_bad=True))
        self.assertTrue(states[306.0])                       # new episode alerts again

    def test_fr08_repeat_every_two_minutes(self):
        a, s = AlarmController(), settings(alert_persistence="repeat")
        states = dict(self.run_alarm(a, s, 0, 260, posture_bad=True))
        self.assertTrue(states[5.0] and states[125.0] and states[245.0])
        self.assertFalse(states[10.0] or states[124.0] or states[130.0])

    def test_fr08_silent_raises_no_alert(self):
        a, s = AlarmController(), settings(alert_channel="silent")
        self.assertFalse(any(v for _, v in self.run_alarm(a, s, 0, 60, posture_bad=True)))

    def test_r01_suppressed_raises_no_alert(self):
        a, s = AlarmController(), settings()
        self.assertFalse(any(v for _, v in self.run_alarm(
            a, s, 0, 60, gaze_away=True, suppressed=True)))

    def test_fr08_delay_up_to_five_minutes(self):
        a, s = AlarmController(), settings(attention_delay=300, alert_persistence="until_corrected")
        states = dict(self.run_alarm(a, s, 0, 301, gaze_away=True))
        self.assertFalse(states[299.9])
        self.assertTrue(states[300.0])

    def test_posture_alert_has_priority(self):
        a, s = AlarmController(), settings(alert_persistence="until_corrected")
        self.run_alarm(a, s, 0, 6, posture_bad=True, gaze_away=True)
        self.assertEqual(a.kind, "posture")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "s.db")
        self.s = SessionTracker(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, start, end, posture="good", gaze="focused"):
        for i in range(round((end - start) * FPS)):
            self.s.update(posture, gaze, now=start + i / FPS)

    def test_fr06_five_look_aways_make_five_events(self):
        self.s.start_session(now=0)
        t = 0.0
        self.feed(t, t + 10)
        t += 10
        for _ in range(5):
            self.feed(t, t + 5, gaze="away")
            self.feed(t + 5, t + 15)
            t += 15
        self.assertEqual(self.s.end_session(now=t).distraction_events, 5)

    def test_fr06_short_glances_do_not_count(self):
        self.s.start_session(now=0)
        self.feed(0, 2.5, gaze="away")
        self.feed(2.5, 10)
        self.assertEqual(self.s.end_session(now=10).distraction_events, 0)

    def test_fr06_flicker_back_does_not_split_an_episode(self):
        self.s.start_session(now=0)
        self.feed(0, 2, gaze="away")
        self.feed(2, 2.5)                     # back on screen less than 1 s
        self.feed(2.5, 5, gaze="away")
        self.feed(5, 10)
        self.assertEqual(self.s.end_session(now=10).distraction_events, 1)

    def test_fr05_one_state_per_second(self):
        self.s.start_session(now=0)
        self.feed(0, 7)
        summary = self.s.end_session(now=7)
        self.assertEqual(len(summary.timeline), 7)
        with closing(sqlite3.connect(self.db)) as c:
            rows = c.execute("SELECT second, posture, gaze FROM samples ORDER BY second").fetchall()
            cols = {r[1] for r in c.execute("PRAGMA table_info(samples)")}
        self.assertEqual([r[0] for r in rows], list(range(7)))
        self.assertEqual(cols, {"session_id", "second", "posture", "gaze"})   # no image data

    def test_fr03_pause_is_not_study_time(self):
        self.s.start_session(now=0)
        self.feed(0, 10)
        self.s.pause(now=10)
        self.s.update("bad", "away", now=20)        # ignored while paused
        self.s.resume(now=40)
        self.feed(40, 50)
        summary = self.s.end_session(now=50)
        self.assertAlmostEqual(summary.duration_seconds, 20)
        self.assertAlmostEqual(summary.paused_seconds, 30)
        self.assertEqual(summary.distraction_events, 0)

    def test_fr09_summary_contents(self):
        self.s.start_session(now=0)
        self.feed(0, 20)
        self.feed(20, 25, gaze="away")
        self.feed(25, 60, posture="bad")
        s = self.s.end_session(now=60)
        self.assertEqual(s.distraction_events, 1)
        self.assertAlmostEqual(s.longest_focus_seconds, 35, delta=0.2)
        self.assertEqual(len(s.timeline), 60)
        self.assertIn(s.label, ("Good", "Fair", "Needs attention"))
        self.assertAlmostEqual(s.good_posture_pct, 25 / 60 * 100, delta=2)

    def test_fr10_overview_and_deletion(self):
        for start in (0, 100):
            self.s.start_session(now=start)
            self.feed(start, start + 5)
            self.s.log_alert("posture", "Sit up straight", now=start + 1)
            self.s.end_session(now=start + 5)
        o = self.s.data_overview()
        self.assertEqual((o["sessions"], o["samples"], o["alerts"]), (2, 10, 2))
        first = self.s.get_past_sessions()[-1]["id"]
        self.s.delete_session(first)
        self.assertEqual(self.s.data_overview()["sessions"], 1)
        self.s.delete_all()
        o = self.s.data_overview()
        self.assertEqual((o["sessions"], o["samples"], o["alerts"]), (0, 0, 0))

    def test_could_export_csv(self):
        self.s.start_session(now=0)
        self.feed(0, 30)
        self.s.end_session(now=30)
        path = Path(self.tmp.name) / "out.csv"
        self.assertEqual(self.s.export_csv(str(path)), 1)
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["study_minutes"], "0.5")

    def test_old_database_is_migrated(self):
        old = str(Path(self.tmp.name) / "old.db")
        with closing(sqlite3.connect(old)) as c:
            c.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, start_time REAL, "
                      "end_time REAL, duration_seconds REAL, good_posture_pct REAL, "
                      "bad_posture_pct REAL, focus_pct REAL, distraction_events INTEGER, "
                      "posture_score REAL, attention_score REAL, overall_score REAL)")
            c.commit()
        SessionTracker(old)
        with closing(sqlite3.connect(old)) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        self.assertTrue({"longest_focus_seconds", "paused_seconds", "label"} <= cols)

    def test_ex09_crashed_session_is_recovered_from_saved_states(self):
        self.s.start_session(now=0)
        self.feed(0, 20)
        self.feed(20, 25, gaze="away")
        self.feed(25, 65, posture="bad")      # rows are saved in batches of 30 seconds
        # crash: no end_session(); the next start of the app opens a new tracker
        later = SessionTracker(self.db)
        self.assertEqual(later.recover_incomplete(), 1)
        past = later.get_past_sessions()
        self.assertEqual(len(past), 1)
        r = past[0]
        self.assertEqual(r["duration_seconds"], 60)          # last saved batch ends at 59 s
        self.assertEqual(r["distraction_events"], 1)
        self.assertEqual(r["longest_focus_seconds"], 35)
        self.assertTrue(r["label"].endswith("(recovered)"))
        self.assertEqual(later.recover_incomplete(), 0)       # nothing left to recover

    def test_ex09_empty_crashed_session_and_running_session(self):
        self.s.start_session(now=0)                          # crashed before any state was saved
        self.assertEqual(self.s.recover_incomplete(), 0)      # never touches a running session
        later = SessionTracker(self.db)
        self.assertEqual(later.recover_incomplete(), 1)
        r = later.get_past_sessions()[0]
        self.assertEqual((r["duration_seconds"], r["label"]), (0, "No data (recovered)"))


class SettingsTests(unittest.TestCase):
    def test_invalid_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text(json.dumps({"posture_delay": 7, "alert_channel": "loud",
                                     "attention_delay": 300, "consent_accepted": True}))
            s = load_settings(p)
        self.assertEqual(s["posture_delay"], DEFAULTS["posture_delay"])
        self.assertEqual(s["alert_channel"], DEFAULTS["alert_channel"])
        self.assertEqual(s["attention_delay"], 300)
        self.assertTrue(s["consent_accepted"])

    def test_fr04_consent_recorded_with_date(self):
        s = dict(DEFAULTS)
        record_consent(s)
        self.assertTrue(s["consent_accepted"])
        self.assertRegex(s["consent_date"], r"^\d{4}-\d\d-\d\dT")

    def test_fr04_changed_consent_text_asks_again(self):
        from core.settings import has_consent, withdraw_consent
        old = dict(DEFAULTS, consent_accepted=True, consent_version=1)   # older wording
        self.assertFalse(has_consent(old))
        record_consent(old)
        self.assertTrue(has_consent(old))
        withdraw_consent(old)
        self.assertFalse(has_consent(old))


class DetectorCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.detector import FocUSDetector, PostureLevel
        cls.d, cls.Level = FocUSDetector(), PostureLevel

    @classmethod
    def tearDownClass(cls):
        cls.d.release()

    def posture(self, lean):
        lm = [SimpleNamespace(x=0.5, y=0.0, visibility=1.0) for _ in range(13)]
        lm[0].y = 0.5 - lean                       # nose above shoulders by `lean`
        lm[11].y = lm[12].y = 0.5
        return self.d._analyze_posture(lm).level

    def test_fr05_posture_judged_against_calibrated_baseline(self):
        self.d.set_baseline(0.30, 0.0, 0.0)
        self.assertEqual(self.posture(0.29), self.Level.GOOD)
        self.assertEqual(self.posture(0.24), self.Level.WARNING)
        self.assertEqual(self.posture(0.20), self.Level.BAD)
        self.d.clear_baseline()
        self.assertEqual(self.posture(0.20), self.Level.GOOD)   # absolute fallback


if __name__ == "__main__":
    unittest.main()
