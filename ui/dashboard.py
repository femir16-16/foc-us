"""
Foc&US Session History (FR-10), with per-session deletion (FR-10) and CSV export (Could).
"""
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, ttk
from typing import Callable, Optional

from core.session import SessionTracker
from ui.theme import C, header, button, fmt_duration

TREND_W, TREND_H = 836, 170
PAD_L, PAD_R, PAD_T, PAD_B = 40, 16, 16, 28
SERIES = "#e94560"   # single series; passes lightness and 3:1 contrast on the card surface
GRID = "#26314f"


class DashboardWindow:
    COLS = ("Date & time", "Study time", "Score", "Label", "Posture", "Attention",
            "Longest focus", "Distractions")
    WIDTHS = (130, 80, 60, 170, 65, 70, 95, 85)

    def __init__(self, parent: tk.Tk, session: SessionTracker,
                 on_changed: Optional[Callable[[], None]] = None):
        self.session, self.on_changed = session, on_changed
        self._armed_id: Optional[str] = None
        self.win = tk.Toplevel(parent)
        self.win.title("Foc&US — Session History")
        self.win.configure(bg=C["bg"])
        self.win.geometry("860x740")
        self.win.minsize(860, 600)
        header(self.win, "Session History", "Stored only on this computer")

        self.totals = tk.StringVar()
        tk.Label(self.win, textvariable=self.totals, font=("Helvetica", 10, "bold"),
                 fg=C["text"], bg=C["card"], anchor=tk.W, padx=16, pady=8
                 ).pack(fill=tk.X, padx=12, pady=(10, 0))

        # Could: weekly progress trend
        tk.Label(self.win, text="SESSION SCORES, LAST 7 DAYS", font=("Helvetica", 8, "bold"),
                 fg=C["muted"], bg=C["bg"]).pack(anchor=tk.W, padx=14, pady=(10, 0))
        self.trend = tk.Canvas(self.win, width=TREND_W, height=TREND_H, bg=C["card"],
                               highlightthickness=0)
        self.trend.pack(anchor=tk.W, padx=12, pady=(4, 0))
        self._points: list = []
        self.trend.bind("<Motion>", self._hover)
        self.trend.bind("<Leave>", lambda e: self.trend.delete("tip"))

        frame = tk.Frame(self.win, bg=C["bg"])
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        style = ttk.Style()
        style.theme_use("default")   # the native Windows theme ignores heading colours
        style.configure("History.Treeview", background=C["card"], foreground=C["text"],
                        rowheight=26, fieldbackground=C["card"], font=("Helvetica", 9))
        style.configure("History.Treeview.Heading", background=C["accent"],
                        foreground=C["text"], font=("Helvetica", 9, "bold"), relief="flat")
        style.map("History.Treeview", background=[("selected", C["accent"])],
                  foreground=[("selected", C["text"])])
        self.tree = ttk.Treeview(frame, columns=self.COLS, show="headings",
                                 style="History.Treeview", selectmode="browse")
        for col, w in zip(self.COLS, self.WIDTHS):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor=tk.CENTER, minwidth=50)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._disarm())

        self.note = tk.StringVar()
        tk.Label(self.win, textvariable=self.note, font=("Helvetica", 9), fg=C["warn"],
                 bg=C["bg"]).pack(anchor=tk.W, padx=14)

        bar = tk.Frame(self.win, bg=C["accent"], padx=12, pady=8)
        bar.pack(fill=tk.X)
        button(bar, "Close", self.win.destroy).pack(side=tk.RIGHT)
        self.delete_btn = button(bar, "Delete selected session", self._delete_selected,
                                 fg=C["bad"])
        self.delete_btn.pack(side=tk.LEFT)
        button(bar, "Export CSV…", self._export).pack(side=tk.LEFT, padx=8)
        self._load()

    def _load(self):
        self.tree.delete(*self.tree.get_children())
        sessions = self.session.get_past_sessions(limit=500)
        total, score_sum = 0.0, 0.0
        for s in sessions:
            dur = s["duration_seconds"] or 0.0
            total += dur
            score_sum += s["overall_score"] or 0.0
            self.tree.insert("", tk.END, iid=str(s["id"]), values=(
                datetime.fromtimestamp(s["start_time"]).strftime("%Y-%m-%d  %H:%M"),
                fmt_duration(dur),
                f"{s['overall_score'] or 0:.0f}",
                s["label"] or "—",
                f"{s['posture_score'] or 0:.0f}",
                f"{s['attention_score'] or 0:.0f}",
                fmt_duration(s["longest_focus_seconds"]) if s["longest_focus_seconds"] else "—",
                s["distraction_events"] or 0,
            ))
        n = len(sessions)
        self.totals.set(f"Sessions: {n}     Total study time: {fmt_duration(total)}     "
                        f"Average score: {score_sum / n if n else 0:.0f} / 100")
        self._draw_trend(sessions)

    def _draw_trend(self, sessions, now: Optional[datetime] = None):
        cv = self.trend
        cv.delete("all")
        self._points = []
        x0, x1, y0, y1 = PAD_L, TREND_W - PAD_R, PAD_T, TREND_H - PAD_B
        today = (now or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=6)
        t0, span = start.timestamp(), 7 * 86400
        X = lambda ts: x0 + (ts - t0) / span * (x1 - x0)
        Y = lambda score: y1 - score / 100 * (y1 - y0)

        for v in (0, 50, 100):
            cv.create_line(x0, Y(v), x1, Y(v), fill=GRID)
            cv.create_text(x0 - 8, Y(v), text=str(v), anchor=tk.E, fill=C["muted"],
                           font=("Helvetica", 8))
        for d in range(7):
            day = start + timedelta(days=d)
            if d:
                cv.create_line(X(day.timestamp()), y0, X(day.timestamp()), y1, fill=GRID,
                               dash=(2, 4))
            cv.create_text(X(day.timestamp() + 43200), y1 + 14, fill=C["muted"],
                           text="Today" if d == 6 else day.strftime("%a %d"),
                           font=("Helvetica", 8))

        pts = sorted((s for s in sessions if t0 <= s["start_time"] < t0 + span),
                     key=lambda s: s["start_time"])
        if not pts:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="No sessions in the last 7 days",
                           fill=C["muted"], font=("Helvetica", 10))
            return
        coords = [(X(s["start_time"]), Y(s["overall_score"] or 0)) for s in pts]
        if len(coords) > 1:
            cv.create_line(*[c for xy in coords for c in xy], fill=SERIES, width=2)
        for (x, y), s in zip(coords, pts):
            cv.create_oval(x - 5, y - 5, x + 5, y + 5, fill=SERIES, outline=C["card"], width=2)
            self._points.append((x, y, s))
        x, y, s = self._points[-1]   # direct label on the latest session only
        cv.create_text(x, y - 13, text=f"{s['overall_score'] or 0:.0f}", fill=C["text"],
                       font=("Helvetica", 9, "bold"))

    def _hover(self, event):
        self.trend.delete("tip")
        near = [(abs(x - event.x) + abs(y - event.y), x, y, s) for x, y, s in self._points]
        near = [n for n in near if n[0] <= 14]
        if not near:
            return
        _, x, y, s = min(near, key=lambda n: n[0])
        text = (f"{datetime.fromtimestamp(s['start_time']):%a %d %b, %H:%M}\n"
                f"Score {s['overall_score'] or 0:.0f} ({s['label'] or '—'})  ·  "
                f"{fmt_duration(s['duration_seconds'])}")
        tid = self.trend.create_text(0, 0, text=text, anchor=tk.NW, fill=C["text"],
                                     font=("Helvetica", 9), tags="tip")
        bx0, by0, bx1, by1 = self.trend.bbox(tid)
        w, h = bx1 - bx0 + 16, by1 - by0 + 10
        # beside the point, never on top of it: right/above first, else left/below
        tx = x + 12 if x + 12 + w <= TREND_W - 4 else x - 12 - w
        ty = y - h - 8 if y - h - 8 >= 4 else y + 12
        self.trend.coords(tid, tx + 8, ty + 5)
        box = self.trend.create_rectangle(tx, ty, tx + w, ty + h, fill=C["accent"],
                                          outline=C["border"], tags="tip")
        self.trend.tag_raise(tid, box)

    def _disarm(self):
        self._armed_id = None
        self.delete_btn.config(text="Delete selected session", bg=C["accent"], fg=C["bad"])

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            self.note.set("Select a session first.")
            return
        if self._armed_id != sel[0]:   # two-step confirmation inside the window
            self._armed_id = sel[0]
            self.delete_btn.config(text="Click again to delete it", bg=C["bad"], fg=C["white"])
            self.note.set("The selected session and its per-second records will be removed.")
            return
        self.session.delete_session(int(sel[0]))
        self._disarm()
        self.note.set("Session deleted.")
        self._load()
        if self.on_changed:
            self.on_changed()

    def _export(self):
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Export session summaries", defaultextension=".csv",
            initialfile=f"focus_sessions_{datetime.now():%Y%m%d}.csv",
            filetypes=[("CSV file", "*.csv")])
        if not path:
            return
        n = self.session.export_csv(path)
        self.note.set(f"Exported {n} sessions to {path}")
