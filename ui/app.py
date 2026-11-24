"""
Foc&US Main Application Window

The camera is open only while a session is calibrating or running (FR-01, FR-03):
idle → calibrating → running ⇄ paused → idle (summary shown).
"""
import ctypes
import statistics
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Optional

import cv2
from PIL import Image, ImageTk

from core.alarm import AlarmController
from core.detector import AttentionLevel, FocUSDetector, PostureLevel
from core.session import SessionTracker
from core.settings import (has_consent, load_settings, record_consent, save_settings,
                           withdraw_consent)
from ui import sound
from ui.dashboard import DashboardWindow
from ui.dialogs import DataWindow, SummaryWindow, ask_consent
from ui.settings_dialog import SettingsWindow
from ui.theme import C, button, fmt_duration

POSTURE_LABELS = {
    PostureLevel.GOOD:         ("Good",         C["good"]),
    PostureLevel.WARNING:      ("Warning",      C["warn"]),
    PostureLevel.BAD:          ("Bad",          C["bad"]),
    PostureLevel.NO_DETECTION: ("Not detected", C["muted"]),
}
GAZE_LABELS = {
    AttentionLevel.FOCUSED:      ("On screen",    C["good"]),
    AttentionLevel.DISTRACTED:   ("Drifting",     C["warn"]),
    AttentionLevel.AWAY:         ("Away",         C["bad"]),
    AttentionLevel.NO_DETECTION: ("Not detected", C["muted"]),
}
ALARM_TEXT = {
    "posture":   ("SIT UP STRAIGHT",
                  "Poor posture detected: lift your head and straighten your back"),
    "attention": ("LOOK BACK AT THE SCREEN",
                  "You have been looking away from your study material"),
}
ALARM_DARK = "#7f1d1d"
FLASH_MS = 450

CALIBRATION_SECONDS = 4.0       # FR-05: at most 5 s
COVERED_BRIGHTNESS = 15         # mean grey level of a covered lens
DIM_BRIGHTNESS = 45
COVERED_AFTER = 2.0             # FR-01: message within 5 s
NOBODY_AFTER = 3.0
BACKLIGHT_RATIO = 0.6           # Should: face much darker than the background
BACKLIGHT_AFTER = 2.0
READ_FAIL_AFTER = 3.0
SMOOTH_N, SMOOTH_THRESH = 20, 0.65

IDLE_TEXT = "The camera is off.\nPress Start Session to begin."
PAUSED_TEXT = "Paused: the camera is off.\nPress Resume to continue."
NO_CAMERA_TEXT = ("Camera not available.\nCheck that a webcam is connected and not used by "
                  "another app,\nor choose another camera in Settings.")


def open_capture(index: int):
    # DirectShow delivers the first frame ~4x faster than the default MSMF backend (FR-01)
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(index)
    return cap


class FocUSApp:
    CAM_W, CAM_H = 640, 480

    def __init__(self, root: tk.Tk, camera_factory=open_capture):
        self.root = root
        self.root.title("Foc&US — AI Attention & Posture Tracker")
        self.root.configure(bg=C["bg"])
        self.root.resizable(False, False)

        self.camera_factory = camera_factory
        self.detector = FocUSDetector()
        self.session = SessionTracker()
        recovered = self.session.recover_incomplete()   # EX-09: sessions cut off by a crash
        self.alarm = AlarmController()
        self.settings = load_settings()

        self.state = "idle"
        self.cap = None
        self.notes_mode = False
        self._job: Optional[str] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._calib: dict = {}
        self._issue_since: dict[str, float] = {}
        self._posture_buf: list[bool] = []
        self._gaze_buf: list[bool] = []
        self._sound_gen = 0
        self._flash_on = False
        self._settings_win: Optional[SettingsWindow] = None

        self._build_ui()
        self._build_overlay()
        self._show_idle()
        if recovered:
            self._status(f"Recovered {recovered} interrupted session{'s' if recovered > 1 else ''}; "
                         "see History.")
        self._visual_tick()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        content = tk.Frame(self.root, bg=C["bg"])
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        self._build_camera_pane(content)
        self._build_status_pane(content)
        self._build_footer()
        s = ttk.Style()
        s.theme_use("default")
        s.configure("Posture.Horizontal.TProgressbar", troughcolor=C["bg"],
                    background=C["good"], thickness=10)

    def _build_header(self):
        h = tk.Frame(self.root, bg=C["accent"], height=52)
        h.pack(fill=tk.X)
        h.pack_propagate(False)
        tk.Label(h, text="Foc&US", font=("Helvetica", 22, "bold"),
                 fg=C["highlight"], bg=C["accent"]).pack(side=tk.LEFT, padx=16)
        tk.Label(h, text="AI-Powered Attention & Posture Tracker",
                 font=("Helvetica", 10), fg=C["muted"], bg=C["accent"]).pack(side=tk.LEFT)
        # FR-02: visible whenever frames are being processed
        self._cam_indicator = tk.Label(h, text="  ●  CAMERA ON  ", font=("Helvetica", 10, "bold"),
                                       fg=C["white"], bg=C["bad"])

    def _build_camera_pane(self, parent):
        left = tk.Frame(parent, bg=C["bg"])
        left.pack(side=tk.LEFT, fill=tk.BOTH)
        self.canvas = tk.Canvas(left, width=self.CAM_W, height=self.CAM_H, bg="#050510",
                                highlightthickness=4, highlightbackground=C["border"])
        self.canvas.pack()
        self._img_id = self.canvas.create_image(0, 0, anchor=tk.NW)
        self._placeholder = self.canvas.create_text(
            self.CAM_W // 2, self.CAM_H // 2, text="", fill=C["muted"],
            font=("Helvetica", 14), justify=tk.CENTER)
        self._calib_bg = self.canvas.create_rectangle(
            0, self.CAM_H - 96, self.CAM_W, self.CAM_H - 30, fill=C["accent"], outline="",
            state="hidden")
        self._calib_txt = self.canvas.create_text(
            self.CAM_W // 2, self.CAM_H - 63, text="", fill=C["white"],
            font=("Helvetica", 15, "bold"), state="hidden")
        self._banner_bg = self.canvas.create_rectangle(
            0, self.CAM_H - 96, self.CAM_W, self.CAM_H - 30, fill=C["bad"], outline="",
            state="hidden")
        self._banner_txt = self.canvas.create_text(
            self.CAM_W // 2, self.CAM_H - 63, text="", fill=C["white"],
            font=("Helvetica", 22, "bold"), state="hidden")
        self._status_var = tk.StringVar()
        self._status_lbl = tk.Label(left, textvariable=self._status_var, font=("Helvetica", 9),
                                    fg=C["muted"], bg=C["bg"])
        self._status_lbl.pack(pady=4)

    def _card(self, parent, title, pady=(0, 8)):
        f = tk.Frame(parent, bg=C["card"], padx=12, pady=8)
        f.pack(fill=tk.X, pady=pady)
        tk.Label(f, text=title, font=("Helvetica", 8, "bold"), fg=C["muted"],
                 bg=C["card"]).pack(anchor=tk.W)
        return f

    def _state_row(self, card):
        row = tk.Frame(card, bg=C["card"])
        row.pack(fill=tk.X, pady=(4, 2))
        dot = tk.Canvas(row, width=14, height=14, bg=C["card"], highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 6))
        oval = dot.create_oval(2, 2, 12, 12, fill=C["muted"])
        var = tk.StringVar(value="—")
        tk.Label(row, textvariable=var, font=("Helvetica", 12, "bold"), fg=C["text"],
                 bg=C["card"]).pack(side=tk.LEFT)
        msg = tk.StringVar()
        tk.Label(card, textvariable=msg, font=("Helvetica", 9), fg=C["muted"], bg=C["card"],
                 wraplength=240, anchor=tk.W, justify=tk.LEFT).pack(fill=tk.X)
        return dot, oval, var, msg

    def _build_status_pane(self, parent):
        right = tk.Frame(parent, bg=C["bg"], width=268)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        right.pack_propagate(False)

        tc = self._card(right, "STUDY TIME")
        self._timer_var = tk.StringVar(value="00:00:00")
        tk.Label(tc, textvariable=self._timer_var, font=("Helvetica", 36, "bold"),
                 fg=C["white"], bg=C["card"]).pack()
        self._score_var = tk.StringVar(value="Score: —")
        self._score_lbl = tk.Label(tc, textvariable=self._score_var,
                                   font=("Helvetica", 12, "bold"), fg=C["muted"], bg=C["card"])
        self._score_lbl.pack()

        pc = self._card(right, "POSTURE (OBJ-01)")
        self._p_dot, self._p_oval, self._p_var, self._p_msg = self._state_row(pc)
        self._p_bar = ttk.Progressbar(pc, length=240, mode="determinate", maximum=100,
                                      style="Posture.Horizontal.TProgressbar")
        self._p_bar.pack(pady=(4, 0))

        gc = self._card(right, "ATTENTION (OBJ-02)")
        self._g_dot, self._g_oval, self._g_var, self._g_msg = self._state_row(gc)
        self._dist_var = tk.StringVar(value="Distractions: 0     Longest focus: —")
        tk.Label(gc, textvariable=self._dist_var, font=("Helvetica", 9, "bold"),
                 fg=C["warn"], bg=C["card"]).pack(anchor=tk.W, pady=(6, 0))

        ac = self._card(right, "ALERTS", pady=(0, 0))
        ac.pack_configure(fill=tk.BOTH, expand=True)
        frame = tk.Frame(ac, bg=C["card"])
        frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._alert_lb = tk.Listbox(frame, font=("Helvetica", 8), bg=C["bg"], fg=C["text"],
                                    selectbackground=C["accent"], relief=tk.FLAT,
                                    borderwidth=0, highlightthickness=0, activestyle="none")
        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._alert_lb.yview)
        self._alert_lb.config(yscrollcommand=sb.set)
        self._alert_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_footer(self):
        bar = tk.Frame(self.root, bg=C["accent"], height=56)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        self._start_btn = button(bar, "Start Session", self._start_or_end, bg=C["good"],
                                 fg=C["white"], bold=True)
        self._start_btn.pack(side=tk.LEFT, padx=(16, 6), pady=9)
        self._pause_btn = button(bar, "Pause", self._toggle_pause, state=tk.DISABLED)
        self._pause_btn.pack(side=tk.LEFT, padx=6, pady=9)
        self._notes_btn = button(bar, "Notes mode: Off", self._toggle_notes)
        self._notes_btn.pack(side=tk.LEFT, padx=6, pady=9)
        button(bar, "Data & Privacy", self._show_data).pack(side=tk.RIGHT, padx=(6, 16), pady=9)
        button(bar, "Settings", self._show_settings).pack(side=tk.RIGHT, padx=6, pady=9)
        button(bar, "History", self._show_history).pack(side=tk.RIGHT, padx=6, pady=9)

    # ── Always-on-top alert banner (Should) ────────────────────────────────────

    def _build_overlay(self):
        """Stays mapped but fully transparent while idle; showing it only changes its
        alpha, so it never steals keyboard focus."""
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.attributes("-alpha", 0.0)
        w, h = 620, 96
        ov.geometry(f"{w}x{h}+{(ov.winfo_screenwidth() - w) // 2}+24")
        ov.configure(bg=C["bad"])
        self._ov_title = tk.Label(ov, font=("Helvetica", 24, "bold"), fg=C["white"], bg=C["bad"])
        self._ov_title.pack(pady=(14, 0))
        self._ov_sub = tk.Label(ov, font=("Helvetica", 11), fg=C["white"], bg=C["bad"])
        self._ov_sub.pack()
        ov.update_idletasks()
        ov.update()
        # Windows: no focus stealing, no taskbar entry, clicks pass through
        hwnd = ctypes.windll.user32.GetParent(ov.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x80 | 0x20 | 0x80000)
        self._overlay = ov
        self._overlay_visible = False

    def _visual_tick(self):
        if self.alarm.active:
            self._flash_on = not self._flash_on
            color = C["bad"] if self._flash_on else ALARM_DARK
            title, sub = ALARM_TEXT[self.alarm.kind]
            self.canvas.config(highlightbackground=color)
            self.canvas.itemconfig(self._banner_bg, fill=color, state="normal")
            self.canvas.itemconfig(self._banner_txt, text=title, state="normal")
            self._overlay.configure(bg=color)
            self._ov_title.config(text=title, bg=color)
            self._ov_sub.config(text=sub, bg=color)
            if not self._overlay_visible:
                self._overlay.attributes("-alpha", 0.97)
                self._overlay.lift()
                self._overlay_visible = True
        elif self._overlay_visible or self._flash_on:
            self._flash_on = False
            self.canvas.config(highlightbackground=C["border"])
            self.canvas.itemconfig(self._banner_bg, state="hidden")
            self.canvas.itemconfig(self._banner_txt, state="hidden")
            self._overlay.attributes("-alpha", 0.0)
            self._overlay_visible = False
        self.root.after(FLASH_MS, self._visual_tick)

    # ── Camera ────────────────────────────────────────────────────────────────

    def _open_camera(self) -> bool:
        cap = self.camera_factory(self.settings["camera_index"])
        if not cap.isOpened():
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.CAM_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.CAM_H)
        self.cap = cap
        self._issue_since.clear()
        self._cam_indicator.pack(side=tk.RIGHT, padx=16)
        return True

    def _release_camera(self):
        if self._job:
            self.root.after_cancel(self._job)
            self._job = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self._cam_indicator.pack_forget()
        self.canvas.itemconfig(self._img_id, image="")
        self._photo = None

    def _schedule(self):
        self._job = self.root.after(15, self._tick)

    # ── Session control ───────────────────────────────────────────────────────

    def _start_or_end(self):
        if self.state == "idle":
            self._start_session()
        else:
            self._end_session()

    def _start_session(self):
        if not has_consent(self.settings):
            if not ask_consent(self.root):
                self._status("Consent was declined. The camera stays off.")
                return
            record_consent(self.settings)
            save_settings(self.settings)
        if not self._open_camera():
            self._placeholder_text(NO_CAMERA_TEXT)
            self._status("Camera not available.", C["bad"])
            return
        self.detector.clear_baseline()
        self.detector.notes_mode = self.notes_mode
        self._calib = dict(until=None, lean=[], yaw=[], pitch=[])   # timer starts at 1st frame
        self.state = "calibrating"
        self._alert_lb.delete(0, tk.END)
        self._placeholder_text("")
        self.canvas.itemconfig(self._calib_bg, state="normal")
        self.canvas.itemconfig(self._calib_txt, state="normal",
                               text="Calibrating: sit up straight and look at the screen")
        self._start_btn.config(text="End Session", bg=C["bad"])
        self._schedule()

    def _finish_calibration(self):
        c = self._calib
        if len(c["lean"]) >= 5 and len(c["yaw"]) >= 5:
            self.detector.set_baseline(statistics.median(c["lean"]), statistics.median(c["yaw"]),
                                       statistics.median(c["pitch"]))
            note = "Calibrated to your position."
        else:
            note = "Calibration incomplete: using default thresholds."
        self.canvas.itemconfig(self._calib_bg, state="hidden")
        self.canvas.itemconfig(self._calib_txt, state="hidden")
        self.session.start_session()
        self.alarm.reset()
        self._posture_buf.clear()
        self._gaze_buf.clear()
        self.state = "running"
        self._pause_btn.config(state=tk.NORMAL, text="Pause")
        self._status(note)

    def _toggle_pause(self):
        if self.state == "running":
            self.session.pause()
            self.alarm.reset()
            self._release_camera()
            self.state = "paused"
            self._placeholder_text(PAUSED_TEXT)
            self._pause_btn.config(text="Resume")
            self._status("Paused. Paused time is not counted as study time.")
        elif self.state == "paused":
            if not self._open_camera():
                self._placeholder_text(NO_CAMERA_TEXT)
                self._status("Camera not available.", C["bad"])
                return
            self.session.resume()
            self._posture_buf.clear()
            self._gaze_buf.clear()
            self.state = "running"
            self._placeholder_text("")
            self._pause_btn.config(text="Pause")
            self._schedule()

    def _end_session(self):
        was = self.state
        self._release_camera()
        self.alarm.reset()
        self.canvas.itemconfig(self._calib_bg, state="hidden")
        self.canvas.itemconfig(self._calib_txt, state="hidden")
        self.state = "idle"
        if was in ("running", "paused"):
            summary = self.session.end_session()
            SummaryWindow(self.root, summary)
        self._show_idle()

    def _show_idle(self):
        self._placeholder_text(IDLE_TEXT)
        self._start_btn.config(text="Start Session", bg=C["good"])
        self._pause_btn.config(state=tk.DISABLED, text="Pause")
        self._timer_var.set("00:00:00")
        self._score_var.set("Score: —")
        self._score_lbl.config(fg=C["muted"])
        self._dist_var.set("Distractions: 0     Longest focus: —")
        self._update_posture_card(None)
        self._update_attention_card(None)
        self._status("Ready. The camera turns on only when you start a session.")

    # ── Per-frame loop ────────────────────────────────────────────────────────

    def _tick(self):
        self._job = None
        if self.cap is None or self.state not in ("calibrating", "running"):
            return
        now = time.time()
        ok, frame = self.cap.read()
        if not ok:
            since = self._issue_since.setdefault("read", now)
            if now - since >= READ_FAIL_AFTER:
                self._placeholder_text("The camera stopped sending images.\n"
                                       "Check the connection or end the session.")
                self._status("Camera stopped sending images.", C["bad"])
            self._update_alarm(now, False, False, suppressed=True)
            self._schedule()
            return
        self._issue_since.pop("read", None)

        frame = cv2.flip(frame, 1)
        r = self.detector.process_frame(frame)
        p = r.posture.level if r.posture else PostureLevel.NO_DETECTION
        g = r.gaze.level if r.gaze else AttentionLevel.NO_DETECTION
        self._update_posture_card(r.posture)
        self._update_attention_card(r.gaze)

        # FR-01 / R-01: no alerts while the camera view cannot be trusted
        issue = ("covered" if r.brightness < COVERED_BRIGHTNESS
                 else "nobody" if not (r.pose_detected or r.face_detected) else None)
        for k in ("covered", "nobody"):
            if k == issue:
                self._issue_since.setdefault(k, now)
            else:
                self._issue_since.pop(k, None)
        suppressed = issue is not None
        if r.backlight_ratio is not None and r.backlight_ratio < BACKLIGHT_RATIO:
            self._issue_since.setdefault("backlight", now)
        else:
            self._issue_since.pop("backlight", None)
        self._report_quality(now, issue, r.brightness)

        if self.state == "calibrating":
            if self._calib["until"] is None:
                self._calib["until"] = now + CALIBRATION_SECONDS
            if r.posture and p != PostureLevel.NO_DETECTION:
                self._calib["lean"].append(r.posture.neck_lean)
            if r.gaze and r.face_detected:
                self._calib["yaw"].append(r.gaze.yaw)
                self._calib["pitch"].append(r.gaze.pitch)
            if now >= self._calib["until"]:
                self._finish_calibration()
        else:
            p_str, g_str = p.name.lower(), g.name.lower()
            self.session.update(p_str, g_str, now)
            self._update_alarm(now, self._smooth(self._posture_buf, p_str == "bad"),
                               self._smooth(self._gaze_buf, g_str == "away"), suppressed)
            self._update_live(now)

        rgb = cv2.cvtColor(r.annotated_frame, cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(image=Image.fromarray(rgb))
        self.canvas.itemconfig(self._img_id, image=self._photo)
        self._schedule()

    def _report_quality(self, now, issue, brightness):
        if issue == "covered" and now - self._issue_since["covered"] >= COVERED_AFTER:
            self._status("The camera view is blocked or completely dark. Uncover the camera "
                         "or turn on a light.", C["bad"])
        elif issue == "nobody" and now - self._issue_since["nobody"] >= NOBODY_AFTER:
            self._status("No one detected. Alerts are paused until you are back in view.",
                         C["warn"])
        elif now - self._issue_since.get("backlight", now) >= BACKLIGHT_AFTER:   # Should
            self._status("Strong light behind you. Face the light or add light in front "
                         "of you for better tracking.", C["warn"])
        elif brightness < DIM_BRIGHTNESS:   # Should
            self._status("The lighting is dim. Tracking may be unreliable.", C["warn"])
        elif self.state == "calibrating":
            left = max(0, (self._calib["until"] or now + CALIBRATION_SECONDS) - now)
            self._status(f"Calibrating… {left:.0f} s")
        elif self.state == "running" and not issue:
            self._status("Tracking" + ("  ·  Notes mode on" if self.notes_mode else ""))

    def _smooth(self, buf: list, value: bool) -> bool:
        buf.append(value)
        if len(buf) > SMOOTH_N:
            buf.pop(0)
        return sum(buf) / len(buf) >= SMOOTH_THRESH

    # ── Alerts ────────────────────────────────────────────────────────────────

    def _update_alarm(self, now, posture_bad, gaze_away, suppressed):
        was_active, was_kind = self.alarm.active, self.alarm.kind
        if not self.alarm.update(now, posture_bad, gaze_away, self.settings, suppressed):
            return
        if not was_active or was_kind != self.alarm.kind:
            title = ALARM_TEXT[self.alarm.kind][0].capitalize()
            ts = datetime.now().strftime("%H:%M")
            self._alert_lb.insert(0, f"{ts}  {title}")
            self.session.log_alert(self.alarm.kind, title, now)
        if not was_active and self.settings["alert_channel"] == "visual_sound":
            self._sound_gen += 1
            threading.Thread(target=self._sound_loop, args=(self._sound_gen,),
                             daemon=True).start()

    def _sound_loop(self, gen: int):
        while (self.alarm.active and gen == self._sound_gen
               and self.settings["alert_channel"] == "visual_sound"):
            try:
                sound.play(self.alarm.kind or "attention")
            except RuntimeError:
                return

    # ── Live display ──────────────────────────────────────────────────────────

    def _status(self, text, color=None):
        self._status_var.set(text)
        self._status_lbl.config(fg=color or C["muted"])

    def _placeholder_text(self, text):
        self.canvas.itemconfig(self._placeholder, text=text)
        self.canvas.tag_raise(self._placeholder)

    def _update_live(self, now):
        t = self.session.active_seconds(now)
        self._timer_var.set(f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}")
        st = self.session.live_stats(now)
        if st["has_data"]:
            color = C["good"] if st["label"] == "Good" else C["warn"] if st["label"] == "Fair" \
                else C["bad"]
            self._score_var.set(f"Score: {st['score']:.0f} / 100  ·  {st['label']}")
            self._score_lbl.config(fg=color)
        self._dist_var.set(f"Distractions: {st['distraction_events']}     "
                           f"Longest focus: {fmt_duration(st['longest_focus'])}")

    def _update_posture_card(self, posture):
        level = posture.level if posture else PostureLevel.NO_DETECTION
        label, color = POSTURE_LABELS[level]
        self._p_dot.itemconfig(self._p_oval, fill=color)
        self._p_var.set(label if self.state != "idle" else "—")
        self._p_msg.set(posture.message if posture else "")
        lean = posture.neck_lean if posture else 0.0
        base = self.detector.baseline_lean or 0.20
        self._p_bar["value"] = max(0, min(100, lean / base * 100))
        ttk.Style().configure("Posture.Horizontal.TProgressbar", background=color)

    def _update_attention_card(self, gaze):
        level = gaze.level if gaze else AttentionLevel.NO_DETECTION
        label, color = GAZE_LABELS[level]
        self._g_dot.itemconfig(self._g_oval, fill=color)
        self._g_var.set(label if self.state != "idle" else "—")
        self._g_msg.set(gaze.message if gaze else "")

    # ── Other windows ─────────────────────────────────────────────────────────

    def _toggle_notes(self):
        self.notes_mode = not self.notes_mode
        self.detector.notes_mode = self.notes_mode
        self._notes_btn.config(text=f"Notes mode: {'On' if self.notes_mode else 'Off'}",
                               fg=C["good"] if self.notes_mode else C["text"])

    def _show_history(self):
        DashboardWindow(self.root, self.session)

    def _show_settings(self):
        if self._settings_win and self._settings_win.win.winfo_exists():
            self._settings_win.win.lift()
            return
        self._settings_win = SettingsWindow(self.root, self.settings,
                                            lambda: save_settings(self.settings))

    def _show_data(self):
        DataWindow(self.root, self.session, self._withdraw_consent, lambda: None)

    def _withdraw_consent(self):
        if self.state != "idle":
            self._end_session()
        withdraw_consent(self.settings)
        save_settings(self.settings)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def on_close(self):
        if self.state in ("running", "paused"):
            self.session.end_session()
        self._release_camera()
        self.alarm.reset()
        self.detector.release()
        self.root.destroy()
