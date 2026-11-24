"""Tests for Could, Should, NFR-08 and NFR-13."""
import importlib.metadata
import re
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.detector import backlight_ratio   # noqa: E402
from core.session import SessionTracker      # noqa: E402


class BacklightTests(unittest.TestCase):
    def test_should_bright_background_dark_face_is_flagged(self):
        gray = np.full((480, 640), 220, dtype=np.uint8)
        gray[100:300, 250:400] = 80                      # face in shadow
        self.assertLess(backlight_ratio(gray, (250, 100, 400, 300)), 0.6)

    def test_should_even_lighting_is_not_flagged(self):
        gray = np.full((480, 640), 130, dtype=np.uint8)
        gray[100:300, 250:400] = 140
        self.assertGreater(backlight_ratio(gray, (250, 100, 400, 300)), 0.9)

    def test_empty_or_full_box_gives_no_ratio(self):
        gray = np.full((10, 10), 100, dtype=np.uint8)
        self.assertIsNone(backlight_ratio(gray, (5, 5, 5, 5)))
        self.assertIsNone(backlight_ratio(gray, (0, 0, 10, 10)))


class DeletionLeavesNoTraceTests(unittest.TestCase):
    def test_nfr08_deleted_data_is_not_left_in_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "s.db"
            s = SessionTracker(str(db))
            s.start_session(now=0)
            s.log_alert("posture", "MARKER-7f3a-should-vanish", now=1)
            s.end_session(now=5)
            self.assertIn(b"MARKER-7f3a", db.read_bytes())
            s.delete_all()
            self.assertNotIn(b"MARKER-7f3a", db.read_bytes())


class PinnedVersionTests(unittest.TestCase):
    def test_nfr13_requirements_are_pinned_and_installed(self):
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        reqs = [l.split("#")[0].strip() for l in lines if l.split("#")[0].strip()]
        self.assertTrue(reqs)
        for req in reqs:
            m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][^\s]*)", req)
            self.assertIsNotNone(m, f"not pinned exactly: {req}")
            self.assertEqual(importlib.metadata.version(m[1]), m[2], req)
        for model in ("pose_landmarker_lite.task", "face_landmarker.task"):
            self.assertTrue((ROOT / "models" / model).exists(), model)


class WeeklyTrendTests(unittest.TestCase):
    def test_could_one_point_per_session_in_last_7_days(self):
        from ui.dashboard import DashboardWindow
        with tempfile.TemporaryDirectory() as d:
            s = SessionTracker(str(Path(d) / "s.db"))
            now = time.time()
            for days_ago in (0, 1, 1, 3, 6, 9):           # the 9-day-old one is outside
                start = now - days_ago * 86400 - 3600
                s.start_session(now=start)
                s.update("good", "focused", now=start + 1)
                s.end_session(now=start + 600)
            root = tk.Tk()
            root.withdraw()
            try:
                win = DashboardWindow(root, s)
                self.assertEqual(len(win._points), 5)
                xs = [p[0] for p in win._points]
                self.assertEqual(xs, sorted(xs))            # chronological
                self.assertEqual(len(win.tree.get_children()), 6)   # table keeps all
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
