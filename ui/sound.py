"""Alert sounds (Windows). Each pattern is a list of (frequency Hz, duration ms, pause s)."""
import threading
import time
import winsound

PATTERNS = {
    "posture":   [(370, 350, 0.12), (370, 350, 0.50)],   # low double tone
    "attention": [(880, 280, 0.60)],                     # single higher tone
    "test":      [(660, 150, 0.08), (990, 200, 0.0)],
}


def play(pattern: str):
    for freq, ms, pause in PATTERNS[pattern]:
        winsound.Beep(freq, ms)
        time.sleep(pause)


def play_async(pattern: str):
    threading.Thread(target=lambda: _safe(play, pattern), daemon=True).start()


def _safe(fn, *args):
    try:
        fn(*args)
    except RuntimeError:   # no sound device
        pass
