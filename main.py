"""
Foc&US — AI-Powered Attention and Posture Tracking System
Project: FOC-US-26 | Izmir Democracy University | MIS Dept.
"""
import sys
import os

# Allow imports from project root when running main.py directly
sys.path.insert(0, os.path.dirname(__file__))

import tkinter as tk
from ui.app import FocUSApp


def main():
    root = tk.Tk()
    root.minsize(920, 580)

    app = FocUSApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)

    # Center window on screen
    root.update_idletasks()
    w = root.winfo_reqwidth()
    h = root.winfo_reqheight()
    x = (root.winfo_screenwidth()  - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"+{x}+{y}")

    root.mainloop()


if __name__ == "__main__":
    main()
