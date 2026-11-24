"""Shared Foc&US colours and small widget helpers."""
import tkinter as tk

C = {
    "bg":        "#1a1a2e",
    "card":      "#16213e",
    "accent":    "#0f3460",
    "border":    "#1a3a6e",
    "text":      "#e8e8e8",
    "muted":     "#8090a0",
    "good":      "#4caf50",
    "warn":      "#ff9800",
    "bad":       "#ef5350",
    "highlight": "#e94560",
    "white":     "#ffffff",
    "none":      "#3a4560",
}

STATE_COLOR = {"good": C["good"], "warning": C["warn"], "bad": C["bad"]}


def header(parent, title, subtitle=""):
    h = tk.Frame(parent, bg=C["accent"], height=46)
    h.pack(fill=tk.X)
    h.pack_propagate(False)
    tk.Label(h, text=title, font=("Helvetica", 15, "bold"),
             fg=C["highlight"], bg=C["accent"]).pack(side=tk.LEFT, padx=16)
    if subtitle:
        tk.Label(h, text=subtitle, font=("Helvetica", 9),
                 fg=C["muted"], bg=C["accent"]).pack(side=tk.LEFT)
    return h


def button(parent, text, command, bg=None, fg=None, bold=False, **kw):
    return tk.Button(parent, text=text, command=command,
                     font=("Helvetica", 10, "bold" if bold else "normal"),
                     bg=bg or C["accent"], fg=fg or C["text"],
                     activebackground=C["border"], activeforeground=C["text"],
                     disabledforeground=C["muted"], relief=tk.FLAT,
                     padx=14, pady=8, cursor="hand2", **kw)


def center_on(win, parent):
    parent.update_idletasks()
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds or 0))
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
