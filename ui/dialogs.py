"""
Foc&US dialogs: camera consent (FR-04), end-of-session summary (FR-09) and
data & privacy (FR-10).
"""
import tkinter as tk
from typing import Callable

from core.session import SessionSummary, STORED_FIELDS
from ui.theme import C, STATE_COLOR, header, button, center_on, fmt_duration

CONSENT_TEXT = (
    "Foc&US uses your webcam during the study sessions you start, to estimate your posture "
    "and where you are looking.\n\n"
    "•  Video is processed only on this computer, in memory. No images or video are saved "
    "or sent anywhere.\n"
    "•  Saved locally: one posture and gaze state per second, session summaries and alert "
    "times. You can view and delete them at any time under Data & Privacy.\n"
    "•  The camera is on only during a session, and a red CAMERA ON sign shows when it is.\n"
    "•  The MediaPipe library that Foc&US uses for tracking sends performance and usage "
    "statistics to Google. According to Google these contain no images or video. Foc&US "
    "cannot switch this off.\n"
    "•  Results are indicators, not a medical or learning assessment.\n\n"
    "You can withdraw this consent at any time under Data & Privacy."
)


def ask_consent(parent: tk.Tk) -> bool:
    """Modal consent dialog; True only if the user confirms age and agrees."""
    win = tk.Toplevel(parent)
    win.title("Foc&US — Before you start")
    win.configure(bg=C["bg"])
    win.resizable(False, False)
    win.transient(parent)
    header(win, "Before you start", "Camera use and privacy")

    body = tk.Frame(win, bg=C["bg"], padx=18, pady=12)
    body.pack(fill=tk.BOTH)
    tk.Label(body, text=CONSENT_TEXT, font=("Helvetica", 10), fg=C["text"], bg=C["bg"],
             justify=tk.LEFT, wraplength=470).pack(anchor=tk.W)

    adult, agree = tk.BooleanVar(), tk.BooleanVar()
    result = {"ok": False}
    for var, text in ((adult, "I am 18 years old or older."),
                      (agree, "I agree to Foc&US using my webcam as described above.")):
        tk.Checkbutton(body, text=text, variable=var, font=("Helvetica", 10),
                       fg=C["text"], bg=C["bg"], selectcolor=C["card"],
                       activebackground=C["bg"], activeforeground=C["text"],
                       command=lambda: ok_btn.config(
                           state=tk.NORMAL if adult.get() and agree.get() else tk.DISABLED)
                       ).pack(anchor=tk.W, pady=(8, 0))

    bar = tk.Frame(win, bg=C["accent"], padx=12, pady=8)
    bar.pack(fill=tk.X)

    def finish(ok):
        result["ok"] = ok
        win.destroy()

    ok_btn = button(bar, "Agree and continue", lambda: finish(True), bg=C["good"],
                    fg=C["white"], bold=True, state=tk.DISABLED)
    ok_btn.pack(side=tk.RIGHT)
    button(bar, "Decline", lambda: finish(False)).pack(side=tk.RIGHT, padx=8)
    win.protocol("WM_DELETE_WINDOW", lambda: finish(False))

    center_on(win, parent)
    win.grab_set()
    parent.wait_window(win)
    return result["ok"]


class SummaryWindow:
    TIMELINE_W, TIMELINE_H = 560, 28

    def __init__(self, parent: tk.Tk, s: SessionSummary):
        self.win = tk.Toplevel(parent)
        self.win.title("Foc&US — Session summary")
        self.win.configure(bg=C["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)
        header(self.win, "Session summary")

        body = tk.Frame(self.win, bg=C["bg"], padx=20, pady=14)
        body.pack(fill=tk.BOTH)
        color = C["good"] if s.label == "Good" else C["warn"] if s.label == "Fair" else C["bad"]
        score_row = tk.Frame(body, bg=C["bg"])
        score_row.pack(anchor=tk.W)
        tk.Label(score_row, text=f"{s.overall_score:.0f}", font=("Helvetica", 40, "bold"),
                 fg=color, bg=C["bg"]).pack(side=tk.LEFT)
        tk.Label(score_row, text=f"/ 100   {s.label}", font=("Helvetica", 16, "bold"),
                 fg=color, bg=C["bg"]).pack(side=tk.LEFT, padx=8, pady=(16, 0))

        rows = [
            ("Study time", fmt_duration(s.duration_seconds)
             + (f"   (paused {fmt_duration(s.paused_seconds)})" if s.paused_seconds >= 1 else "")),
            ("Longest focus streak", fmt_duration(s.longest_focus_seconds)),
            ("Distractions", f"{s.distraction_events} "
             + ("event" if s.distraction_events == 1 else "events")),
            ("Good posture", f"{s.good_posture_pct:.0f}% of the time"),
            ("Gaze on screen", f"{s.focus_pct:.0f}% of the time"),
        ]
        grid = tk.Frame(body, bg=C["bg"])
        grid.pack(anchor=tk.W, pady=(8, 10))
        for r, (k, v) in enumerate(rows):
            tk.Label(grid, text=k, font=("Helvetica", 10), fg=C["muted"], bg=C["bg"]
                     ).grid(row=r, column=0, sticky=tk.W, pady=2)
            tk.Label(grid, text=v, font=("Helvetica", 11, "bold"), fg=C["text"], bg=C["bg"]
                     ).grid(row=r, column=1, sticky=tk.W, padx=(18, 0), pady=2)

        tk.Label(body, text="POSTURE TIMELINE", font=("Helvetica", 8, "bold"),
                 fg=C["muted"], bg=C["bg"]).pack(anchor=tk.W)
        self._timeline(body, s.timeline, s.duration_seconds)
        legend = tk.Frame(body, bg=C["bg"])
        legend.pack(anchor=tk.W, pady=(4, 10))
        for name, key in (("Good", "good"), ("Warning", "warning"), ("Bad", "bad"),
                          ("Not detected", None)):
            tk.Label(legend, text="■", fg=STATE_COLOR.get(key, C["none"]), bg=C["bg"],
                     font=("Helvetica", 11)).pack(side=tk.LEFT)
            tk.Label(legend, text=name, fg=C["muted"], bg=C["bg"],
                     font=("Helvetica", 9)).pack(side=tk.LEFT, padx=(2, 12))

        tk.Label(body, text="These are indicators of posture and gaze direction, not a medical "
                            "or learning assessment.",
                 font=("Helvetica", 9, "italic"), fg=C["muted"], bg=C["bg"],
                 wraplength=560, justify=tk.LEFT).pack(anchor=tk.W)

        bar = tk.Frame(self.win, bg=C["accent"], padx=12, pady=8)
        bar.pack(fill=tk.X)
        button(bar, "Close", self.win.destroy).pack(side=tk.RIGHT)
        center_on(self.win, parent)

    def _timeline(self, parent, timeline, seconds):
        cv = tk.Canvas(parent, width=self.TIMELINE_W, height=self.TIMELINE_H,
                       bg=C["card"], highlightthickness=0)
        cv.pack(anchor=tk.W, pady=(4, 0))
        n = len(timeline)
        if not n:
            cv.create_text(self.TIMELINE_W // 2, self.TIMELINE_H // 2, text="No data recorded",
                           fill=C["muted"], font=("Helvetica", 9))
            return
        buckets = min(n, self.TIMELINE_W)   # long sessions: majority state per pixel
        width = self.TIMELINE_W / buckets
        for b in range(buckets):
            chunk = timeline[b * n // buckets:(b + 1) * n // buckets]
            state = max(set(chunk), key=chunk.count)
            x0 = b * width
            cv.create_rectangle(x0, 0, x0 + width + 0.5, self.TIMELINE_H, outline="",
                                fill=STATE_COLOR.get(state, C["none"]))
        ticks = tk.Frame(parent, bg=C["bg"], width=self.TIMELINE_W)
        ticks.pack(anchor=tk.W, fill=tk.X)
        tk.Label(ticks, text="0:00", fg=C["muted"], bg=C["bg"], font=("Helvetica", 8)
                 ).pack(side=tk.LEFT)
        tk.Label(ticks, text=fmt_duration(seconds), fg=C["muted"], bg=C["bg"],
                 font=("Helvetica", 8)).pack(side=tk.RIGHT, padx=(0, 20))


class DataWindow:
    def __init__(self, parent: tk.Tk, session, on_withdraw: Callable[[], None],
                 on_deleted: Callable[[], None]):
        self.session, self.on_withdraw, self.on_deleted = session, on_withdraw, on_deleted
        self._armed = False
        self.win = tk.Toplevel(parent)
        self.win.title("Foc&US — Data & Privacy")
        self.win.configure(bg=C["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)
        header(self.win, "Data & Privacy", "What Foc&US keeps on this computer")

        body = tk.Frame(self.win, bg=C["bg"], padx=20, pady=12)
        body.pack(fill=tk.BOTH)
        tk.Label(body, text="STORED", font=("Helvetica", 8, "bold"), fg=C["muted"],
                 bg=C["bg"]).pack(anchor=tk.W)
        for f in STORED_FIELDS:
            tk.Label(body, text="•  " + f, font=("Helvetica", 10), fg=C["text"], bg=C["bg"],
                     justify=tk.LEFT, wraplength=520).pack(anchor=tk.W)
        tk.Label(body, text="NEVER STORED", font=("Helvetica", 8, "bold"), fg=C["muted"],
                 bg=C["bg"]).pack(anchor=tk.W, pady=(10, 0))
        tk.Label(body, text="•  Images, video or audio from the camera\n•  Your name or "
                            "any account details",
                 font=("Helvetica", 10), fg=C["text"], bg=C["bg"], justify=tk.LEFT
                 ).pack(anchor=tk.W)
        tk.Label(body, text="SENT TO GOOGLE BY THE MEDIAPIPE LIBRARY", font=("Helvetica", 8, "bold"),
                 fg=C["muted"], bg=C["bg"]).pack(anchor=tk.W, pady=(10, 0))
        tk.Label(body, text="•  Performance and usage statistics of the tracking library. "
                            "According to Google they contain no images or video. Foc&US "
                            "cannot switch this off. Details: "
                            "developers.google.com/edge/mediapipe/solutions/tasks",
                 font=("Helvetica", 10), fg=C["text"], bg=C["bg"], justify=tk.LEFT,
                 wraplength=520).pack(anchor=tk.W)

        self.counts = tk.StringVar()
        tk.Label(body, textvariable=self.counts, font=("Helvetica", 10, "bold"),
                 fg=C["white"], bg=C["bg"]).pack(anchor=tk.W, pady=(12, 2))
        tk.Label(body, text="File location:", font=("Helvetica", 9), fg=C["muted"],
                 bg=C["bg"]).pack(anchor=tk.W)
        path = tk.Entry(body, font=("Consolas", 9), width=70, relief=tk.FLAT,
                        readonlybackground=C["card"], fg=C["text"])
        path.insert(0, session.db_path)
        path.config(state="readonly")
        path.pack(anchor=tk.W, pady=(0, 6))
        self.note = tk.StringVar()
        tk.Label(body, textvariable=self.note, font=("Helvetica", 10), fg=C["warn"],
                 bg=C["bg"], wraplength=520, justify=tk.LEFT).pack(anchor=tk.W)

        bar = tk.Frame(self.win, bg=C["accent"], padx=12, pady=8)
        bar.pack(fill=tk.X)
        button(bar, "Close", self.win.destroy).pack(side=tk.RIGHT)
        self.delete_btn = button(bar, "Delete all data", self._delete_all, fg=C["bad"])
        self.delete_btn.pack(side=tk.LEFT)
        button(bar, "Withdraw camera consent", self._withdraw).pack(side=tk.LEFT, padx=8)
        self._refresh()
        center_on(self.win, parent)

    def _refresh(self):
        o = self.session.data_overview()
        self.counts.set(f"{o['sessions']} sessions, {o['samples']} per-second records, "
                        f"{o['alerts']} alerts stored")

    def _delete_all(self):
        if not self._armed:   # two-step confirmation inside the window
            self._armed = True
            self.delete_btn.config(text="Click again to delete permanently", bg=C["bad"],
                                   fg=C["white"])
            self.note.set("This removes every stored session and cannot be undone.")
            return
        self.session.delete_all()
        self._armed = False
        self.delete_btn.config(text="Delete all data", bg=C["accent"], fg=C["bad"])
        self.note.set("All stored data was deleted.")
        self._refresh()
        self.on_deleted()

    def _withdraw(self):
        self.on_withdraw()
        self.note.set("Consent withdrawn. Foc&US will ask again before the next session.")
