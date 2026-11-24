"""
Foc&US Settings: alert delay, type and persistence (FR-08) and camera choice (Should).
Changes apply immediately and are saved.
"""
import tkinter as tk
from typing import Callable

from core.settings import CAMERA_CHOICES, CHANNELS, DELAY_STEPS, PERSISTENCE
from ui import sound
from ui.theme import C, header, button, center_on

SLIDER_LEN = 380
KNOB_LEN = 22


def _step_label(seconds: int) -> str:
    return f"{seconds // 60}m" if seconds >= 60 else f"{seconds}s"


class SettingsWindow:
    def __init__(self, parent: tk.Tk, settings: dict, on_change: Callable[[], None]):
        self.settings, self.on_change = settings, on_change
        self._vars: list = []
        self.win = tk.Toplevel(parent)
        self.win.title("Foc&US — Settings")
        self.win.configure(bg=C["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)
        header(self.win, "Settings")

        self._delay_row("POSTURE ALERT DELAY", "Bad posture lasts", "posture_delay")
        self._delay_row("ATTENTION ALERT DELAY", "Looking away lasts", "attention_delay")
        self._choice_row("ALERT TYPE", "alert_channel", CHANNELS)
        self._choice_row("WHILE THE PROBLEM CONTINUES", "alert_persistence", PERSISTENCE)
        self._choice_row("CAMERA (applies at the next session start)", "camera_index",
                         {i: f"Camera {i + 1}" for i in CAMERA_CHOICES}, horizontal=True)

        bar = tk.Frame(self.win, bg=C["accent"], padx=12, pady=8)
        bar.pack(fill=tk.X, pady=(10, 0))
        button(bar, "Close", self.win.destroy).pack(side=tk.RIGHT)
        button(bar, "Test sound", lambda: sound.play_async("test")).pack(side=tk.LEFT)
        center_on(self.win, parent)

    def _card(self, title):
        card = tk.Frame(self.win, bg=C["card"], padx=14, pady=8)
        card.pack(fill=tk.X, padx=16, pady=(10, 0))
        tk.Label(card, text=title, font=("Helvetica", 8, "bold"), fg=C["muted"],
                 bg=C["card"]).pack(anchor=tk.W)
        return card

    def _set(self, key, value):
        if self.settings[key] != value:
            self.settings[key] = value
            self.on_change()

    def _delay_row(self, title, caption, key):
        card = self._card(title)
        value_var = tk.StringVar()
        tk.Label(card, textvariable=value_var, font=("Helvetica", 12, "bold"),
                 fg=C["white"], bg=C["card"]).pack(anchor=tk.W, pady=(2, 2))

        def on_move(raw):
            seconds = DELAY_STEPS[int(float(raw))]
            value_var.set(f"{caption} {_step_label(seconds)}  →  alert")
            self._set(key, seconds)

        scale = tk.Scale(card, from_=0, to=len(DELAY_STEPS) - 1, resolution=1,
                         orient=tk.HORIZONTAL, showvalue=False, length=SLIDER_LEN,
                         sliderlength=KNOB_LEN, width=14, bg=C["highlight"],
                         troughcolor="#0b1020", activebackground="#ff6b81",
                         highlightthickness=0, bd=0, sliderrelief=tk.FLAT, command=on_move)
        scale.set(DELAY_STEPS.index(self.settings[key]))
        scale.pack(anchor=tk.W)
        on_move(str(scale.get()))
        ticks = tk.Frame(card, bg=C["card"], width=SLIDER_LEN, height=16)
        ticks.pack(anchor=tk.W)
        span = (SLIDER_LEN - KNOB_LEN) / (len(DELAY_STEPS) - 1)
        for i, sec in enumerate(DELAY_STEPS):
            tk.Label(ticks, text=_step_label(sec), font=("Helvetica", 8), fg=C["muted"],
                     bg=C["card"]).place(x=KNOB_LEN / 2 + i * span, y=0, anchor=tk.N)

    def _choice_row(self, title, key, options, horizontal=False):
        card = self._card(title)
        var = tk.StringVar(value=str(self.settings[key]))
        self._vars.append(var)   # a garbage-collected StringVar clears the radio buttons
        row = tk.Frame(card, bg=C["card"])
        row.pack(anchor=tk.W)
        for value, label in options.items():
            tk.Radiobutton(row, text=label, value=str(value), variable=var,
                           font=("Helvetica", 10), fg=C["text"], bg=C["card"],
                           selectcolor=C["bg"], activebackground=C["card"],
                           activeforeground=C["text"],
                           command=lambda v=value: self._set(key, v)
                           ).pack(side=tk.LEFT if horizontal else tk.TOP, anchor=tk.W,
                                  padx=(0, 12) if horizontal else 0)
