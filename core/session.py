"""
Foc&US Session Tracker
Records each study session in a local SQLite file: one posture/gaze state per
second (derived data only, never images) plus a summary per session.
Every method takes an optional `now` so the logic can be tested with a fake clock.
"""
import csv
from contextlib import contextmanager
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

MIN_DISTRACTION = 3.0   # seconds off screen before an episode counts (FR-06)
RETURN_GRACE = 1.0      # seconds back on screen that end an episode
SAMPLE_BATCH = 30       # per-second rows written to disk at a time

POSTURE_WEIGHT = {"good": 1.0, "warning": 0.5, "bad": 0.0}
GAZE_WEIGHT = {"focused": 1.0, "distracted": 0.5, "away": 0.0}

STORED_FIELDS = [
    "Session start and end time",
    "Active study time and paused time",
    "One posture state per second (good / warning / bad / not detected)",
    "One gaze state per second (focused / drifting / away / not detected)",
    "Distraction count, longest focus streak, scores and score label",
    "Alert times and alert type",
]


def score_label(score: float) -> str:
    if score >= 75:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Needs attention"


@dataclass
class SessionSummary:
    session_id: int
    start_time: float
    duration_seconds: float
    paused_seconds: float
    longest_focus_seconds: float
    distraction_events: int
    good_posture_pct: float
    focus_pct: float
    posture_score: float
    attention_score: float
    overall_score: float
    label: str
    timeline: List[str] = field(default_factory=list)   # posture state per second


class SessionTracker:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            data_dir = Path(__file__).parent.parent / "data"
            data_dir.mkdir(exist_ok=True)
            db_path = str(data_dir / "sessions.db")
        self.db_path = db_path
        self._init_db()
        self.session_id: Optional[int] = None
        self.is_active = False
        self.is_paused = False

    # ── Database ────────────────────────────────────────────────────────────

    def _connect(self):
        return sqlite3.connect(self.db_path)

    @contextmanager
    def _db(self):
        conn = self._connect()
        try:
            with conn:          # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()        # an open handle would keep the file locked on Windows

    def _init_db(self):
        with self._db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time         REAL NOT NULL,
                    end_time           REAL,
                    duration_seconds   REAL,
                    good_posture_pct   REAL,
                    bad_posture_pct    REAL,
                    focus_pct          REAL,
                    distraction_events INTEGER,
                    posture_score      REAL,
                    attention_score    REAL,
                    overall_score      REAL
                )""")
            existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            for column, kind in (("longest_focus_seconds", "REAL"), ("paused_seconds", "REAL"),
                                 ("label", "TEXT")):
                if column not in existing:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {kind}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS samples (
                    session_id INTEGER NOT NULL,
                    second     INTEGER NOT NULL,
                    posture    TEXT NOT NULL,
                    gaze       TEXT NOT NULL,
                    PRIMARY KEY (session_id, second)
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    ts         REAL NOT NULL,
                    kind       TEXT NOT NULL,
                    message    TEXT
                )""")

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start_session(self, now: Optional[float] = None):
        now = time.time() if now is None else now
        self.start_time = now
        self.is_active, self.is_paused = True, False
        self._paused_total = 0.0
        self._pause_started: Optional[float] = None
        self._samples: List[tuple] = []        # (second, posture, gaze)
        self._unsaved = 0
        self._bucket_sec: Optional[int] = None
        self._bucket_p, self._bucket_g = Counter(), Counter()
        self.distraction_events = 0
        self._ep_start: Optional[float] = None
        self._ep_counted = False
        self._return_since: Optional[float] = None
        self._streak_start = 0.0
        self._longest = 0.0
        with self._db() as conn:
            self.session_id = conn.execute(
                "INSERT INTO sessions (start_time) VALUES (?)", (now,)).lastrowid

    def pause(self, now: Optional[float] = None):
        if self.is_active and not self.is_paused:
            self.is_paused = True
            self._pause_started = time.time() if now is None else now

    def resume(self, now: Optional[float] = None):
        if self.is_active and self.is_paused:
            now = time.time() if now is None else now
            self._paused_total += now - self._pause_started
            self.is_paused, self._pause_started = False, None

    def active_seconds(self, now: Optional[float] = None) -> float:
        if not self.is_active:
            return 0.0
        now = time.time() if now is None else now
        paused = self._paused_total + ((now - self._pause_started) if self.is_paused else 0.0)
        return max(0.0, now - self.start_time - paused)

    # ── Per-frame update ────────────────────────────────────────────────────

    def update(self, posture: str, gaze: str, now: Optional[float] = None):
        if not self.is_active or self.is_paused:
            return
        t = self.active_seconds(now)
        sec = int(t)
        if self._bucket_sec is not None and sec != self._bucket_sec:
            self._flush_bucket()
        self._bucket_sec = sec
        self._bucket_p[posture] += 1
        self._bucket_g[gaze] += 1
        self._track_distraction(gaze, t)

    def _track_distraction(self, gaze: str, t: float):
        if gaze == "away":
            self._return_since = None
            if self._ep_start is None:
                self._ep_start = t
            if not self._ep_counted and t - self._ep_start >= MIN_DISTRACTION:
                self._ep_counted = True
                self.distraction_events += 1
                self._longest = max(self._longest, self._ep_start - self._streak_start)
        elif gaze in ("focused", "distracted") and self._ep_start is not None:
            if self._return_since is None:
                self._return_since = t
            if t - self._return_since >= RETURN_GRACE:
                if self._ep_counted:
                    self._streak_start = self._return_since
                self._ep_start, self._ep_counted, self._return_since = None, False, None

    def _flush_bucket(self):
        if self._bucket_sec is None or not self._bucket_p:
            return
        row = (self._bucket_sec, self._bucket_p.most_common(1)[0][0],
               self._bucket_g.most_common(1)[0][0])
        self._samples.append(row)
        self._bucket_p, self._bucket_g = Counter(), Counter()
        self._unsaved += 1
        if self._unsaved >= SAMPLE_BATCH:
            self._save_samples()

    def _save_samples(self):
        rows = self._samples[len(self._samples) - self._unsaved:]
        if rows:
            with self._db() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO samples (session_id, second, posture, gaze) "
                    "VALUES (?,?,?,?)", [(self.session_id, *r) for r in rows])
        self._unsaved = 0

    def log_alert(self, kind: str, message: str, now: Optional[float] = None):
        if not self.is_active:
            return
        with self._db() as conn:
            conn.execute("INSERT INTO alerts (session_id, ts, kind, message) VALUES (?,?,?,?)",
                         (self.session_id, time.time() if now is None else now, kind, message))

    # ── Statistics ──────────────────────────────────────────────────────────

    def _longest_so_far(self, t: float) -> float:
        if self._ep_counted:
            return self._longest
        return max(self._longest, t - self._streak_start)

    def _scores(self, rows):
        posture = [POSTURE_WEIGHT[p] for _, p, _ in rows if p in POSTURE_WEIGHT]
        gaze = [GAZE_WEIGHT[g] for _, _, g in rows if g in GAZE_WEIGHT]
        good = sum(1 for _, p, _ in rows if p == "good")
        on_screen = sum(1 for _, _, g in rows if g in ("focused", "distracted"))
        p_score = 100 * sum(posture) / len(posture) if posture else 0.0
        a_score = 100 * sum(gaze) / len(gaze) if gaze else 0.0
        return dict(
            good_posture_pct=100 * good / len(posture) if posture else 0.0,
            focus_pct=100 * on_screen / len(gaze) if gaze else 0.0,
            posture_score=p_score,
            attention_score=a_score,
            overall_score=0.4 * p_score + 0.6 * a_score,
        )

    def live_stats(self, now: Optional[float] = None) -> dict:
        t = self.active_seconds(now)
        s = self._scores(self._samples)
        return dict(duration=t, distraction_events=self.distraction_events,
                    longest_focus=self._longest_so_far(t),
                    score=s["overall_score"], label=score_label(s["overall_score"]),
                    has_data=bool(self._samples))

    def end_session(self, now: Optional[float] = None) -> SessionSummary:
        now = time.time() if now is None else now
        if self.is_paused:
            self.resume(now)
        t = self.active_seconds(now)
        self._flush_bucket()
        self._save_samples()
        s = self._scores(self._samples)
        summary = SessionSummary(
            session_id=self.session_id, start_time=self.start_time, duration_seconds=t,
            paused_seconds=self._paused_total, longest_focus_seconds=self._longest_so_far(t),
            distraction_events=self.distraction_events, label=score_label(s["overall_score"]),
            timeline=[p for _, p, _ in self._samples], **s)
        bad = sum(1 for _, p, _ in self._samples if p == "bad")
        detected = sum(1 for _, p, _ in self._samples if p in POSTURE_WEIGHT)
        with self._db() as conn:
            conn.execute("""
                UPDATE sessions SET end_time=?, duration_seconds=?, good_posture_pct=?,
                    bad_posture_pct=?, focus_pct=?, distraction_events=?, posture_score=?,
                    attention_score=?, overall_score=?, longest_focus_seconds=?,
                    paused_seconds=?, label=?
                WHERE id=?""", (
                now, t, summary.good_posture_pct, 100 * bad / detected if detected else 0.0,
                summary.focus_pct, summary.distraction_events, summary.posture_score,
                summary.attention_score, summary.overall_score, summary.longest_focus_seconds,
                summary.paused_seconds, summary.label, self.session_id))
        self.is_active = self.is_paused = False
        return summary

    # ── Recovery of interrupted sessions (EX-09, report issue U6) ───────────

    @staticmethod
    def _episodes(rows):
        """Distraction events and longest focus streak rebuilt from saved per-second
        states, with the live rules: MIN_DISTRACTION s away counts once, and coming
        back on screen ends the episode."""
        events, longest, streak_start, ep_start = 0, 0.0, 0.0, None
        for sec, _, gaze in rows:
            if gaze == "away":
                if ep_start is None:
                    ep_start = sec
            elif gaze in ("focused", "distracted") and ep_start is not None:
                if sec - ep_start >= MIN_DISTRACTION:
                    events += 1
                    longest = max(longest, ep_start - streak_start)
                    streak_start = sec
                ep_start = None
        end = rows[-1][0] + 1 if rows else 0
        if ep_start is not None and end - ep_start >= MIN_DISTRACTION:
            events += 1
            longest = max(longest, ep_start - streak_start)
            streak_start = end
        return events, float(max(longest, end - streak_start))

    def recover_incomplete(self) -> int:
        """Close sessions that a crash or power loss left without an end time.
        The summary is rebuilt from the states saved before the crash (at most the
        last 30 s are lost), the duration ends at the last saved second, paused time
        is unknown (0) and the label is marked "(recovered)". Returns the count."""
        if self.is_active:
            return 0
        with self._db() as conn:
            open_rows = conn.execute(
                "SELECT id, start_time FROM sessions WHERE end_time IS NULL").fetchall()
            for sid, start in open_rows:
                rows = conn.execute("SELECT second, posture, gaze FROM samples "
                                    "WHERE session_id=? ORDER BY second", (sid,)).fetchall()
                s = self._scores(rows)
                duration = float(rows[-1][0] + 1) if rows else 0.0
                events, longest = self._episodes(rows)
                bad = sum(1 for _, p, _ in rows if p == "bad")
                detected = sum(1 for _, p, _ in rows if p in POSTURE_WEIGHT)
                label = (score_label(s["overall_score"]) if rows else "No data") + " (recovered)"
                conn.execute("""
                    UPDATE sessions SET end_time=?, duration_seconds=?, good_posture_pct=?,
                        bad_posture_pct=?, focus_pct=?, distraction_events=?, posture_score=?,
                        attention_score=?, overall_score=?, longest_focus_seconds=?,
                        paused_seconds=?, label=?
                    WHERE id=?""", (
                    start + duration, duration, s["good_posture_pct"],
                    100 * bad / detected if detected else 0.0, s["focus_pct"], events,
                    s["posture_score"], s["attention_score"], s["overall_score"], longest,
                    0.0, label, sid))
        return len(open_rows)

    # ── History and user data (FR-10; CSV export is a Could feature) ────────

    def get_past_sessions(self, limit: int = 100) -> List[dict]:
        with self._db() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM sessions WHERE end_time IS NOT NULL "
                                "ORDER BY start_time DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def data_overview(self) -> dict:
        with self._db() as conn:
            count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return dict(sessions=count("sessions"), samples=count("samples"),
                        alerts=count("alerts"), db_path=self.db_path, fields=STORED_FIELDS)

    def delete_session(self, session_id: int):
        with self._db() as conn:
            for table, key in (("samples", "session_id"), ("alerts", "session_id"),
                               ("sessions", "id")):
                conn.execute(f"DELETE FROM {table} WHERE {key}=?", (session_id,))
        self._vacuum()

    def delete_all(self):
        with self._db() as conn:
            for table in ("samples", "alerts", "sessions"):
                conn.execute(f"DELETE FROM {table}")
        self._vacuum()

    def _vacuum(self):
        # Rewrites the file so deleted rows are not left in free pages (NFR-08)
        conn = self._connect()
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    def export_csv(self, path: str) -> int:
        sessions = self.get_past_sessions(limit=100000)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "study_minutes", "paused_minutes", "overall_score", "label",
                        "posture_score", "attention_score", "focus_pct", "distraction_events",
                        "longest_focus_minutes"])
            for s in sessions:
                w.writerow([
                    datetime.fromtimestamp(s["start_time"]).strftime("%Y-%m-%d %H:%M"),
                    round((s["duration_seconds"] or 0) / 60, 1),
                    round((s["paused_seconds"] or 0) / 60, 1),
                    round(s["overall_score"] or 0), s["label"] or "",
                    round(s["posture_score"] or 0), round(s["attention_score"] or 0),
                    round(s["focus_pct"] or 0), s["distraction_events"] or 0,
                    round((s["longest_focus_seconds"] or 0) / 60, 1)])
        return len(sessions)
