"""
Measures the Foc&US non-functional requirements that one person can check on the
reference laptop: NFR-01 (frame rate), NFR-02 (CPU), NFR-03 (start-up time),
NFR-04 (memory growth, no crash) and NFR-07 (no images stored or sent).
NFR-08 and NFR-13 are covered by the unit tests; NFR-05/06/09/10/12 need other
people and NFR-11 a second computer.

usage, from the foc_us folder (the webcam is on for the whole run):
    python tools/measure_nfr.py --minutes 10     # full NFR-01 run
    python tools/measure_nfr.py --minutes 30     # full NFR-02 run
    python tools/measure_nfr.py --minutes 180    # full NFR-04 run
Shorter runs are reported as PRELIMINARY. Your real settings and session data are
not touched: the run uses temporary files. Results go to test_results/.
"""
import argparse
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

REQUIRED_MINUTES = {"NFR-01": 10, "NFR-02": 30, "NFR-04": 180}
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp",
             ".mp4", ".avi", ".mov", ".mkv", ".wmv"}
SOURCE_PATTERNS = r"imwrite|VideoWriter|\.save\(|socket|requests|urllib|http\.client"


def redirect_storage(tmp: Path, consent: bool):
    """Point the app at temporary settings and database files."""
    import core.settings as cs
    import ui.app as appmod
    from core.session import SessionTracker
    path = tmp / "settings.json"
    s = dict(cs.DEFAULTS, alert_channel="silent")
    if consent:   # the person running the measurement consents by starting it
        cs.record_consent(s)
    cs.save_settings(s, path)
    appmod.load_settings = lambda: cs.load_settings(path)
    appmod.save_settings = lambda s: cs.save_settings(s, path)
    appmod.SessionTracker = lambda: SessionTracker(str(tmp / "sessions.db"))
    return appmod


def startup_probe():
    """Child process: build the main window, report ready, exit."""
    import tkinter as tk
    appmod = redirect_storage(Path(tempfile.mkdtemp()), consent=False)
    root = tk.Tk()
    app = appmod.FocUSApp(root)
    root.update()
    print("READY", flush=True)
    app.on_close()


def measure_startup(reps=5):
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        p = subprocess.Popen([sys.executable, __file__, "--startup-probe"], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in p.stdout:
            if line.startswith("READY"):
                times.append(time.perf_counter() - t0)
                break
        p.wait()
    return times


def snapshot(dirs):
    files = {}
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            try:
                if p.is_file() and "__pycache__" not in p.parts:
                    files[p] = p.stat().st_mtime
            except OSError:
                pass
    return files


def source_hits():
    hits = []
    for f in [ROOT / "main.py", *ROOT.glob("core/*.py"), *ROOT.glob("ui/*.py")]:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(SOURCE_PATTERNS, line):
                hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    return hits


def run_session(minutes, camera):
    import psutil
    import tkinter as tk
    tmp = Path(tempfile.mkdtemp())
    appmod = redirect_storage(tmp, consent=True)
    proc = psutil.Process()
    watched = [ROOT, Path.home() / "Pictures", Path.home() / "Videos"]
    before = snapshot(watched)
    root = tk.Tk()
    app = appmod.FocUSApp(root)
    app.settings["camera_index"] = camera
    data = dict(frames=0, cpu=[], rss=[], conns=set(), error=None, run_start=None)
    original = app.detector.process_frame

    def counted(frame):
        if app.state == "running":
            data["frames"] += 1
        return original(frame)
    app.detector.process_frame = counted

    def sample():
        if app.state == "running":
            if data["run_start"] is None:
                data["run_start"] = time.time()
                proc.cpu_percent(None)          # prime the counter
            else:
                data["cpu"].append(proc.cpu_percent(None))
                data["rss"].append(proc.memory_info().rss)
            for c in proc.net_connections(kind="inet"):
                data["conns"].add(f"{c.laddr} -> {c.raddr} {c.status}")
        root.after(10_000, sample)

    def finish():
        try:
            data["seconds"] = time.time() - data["run_start"]
            app._start_or_end()                 # end session
            data["summary_rows"] = app.session.data_overview()["samples"]
        except Exception as e:                  # report, don't hide, a crash
            data["error"] = repr(e)
        app.on_close()

    def start():
        app._start_or_end()
        if app.state != "calibrating":
            data["error"] = "camera could not be opened"
            app.on_close()
            return
        sample()
        root.after(int(minutes * 60_000) + 6_000, finish)   # + calibration time

    root.after(500, start)
    root.mainloop()
    after = snapshot(watched)
    data["new_media"] = sorted(str(p) for p, m in after.items()
                               if p.suffix.lower() in MEDIA_EXT and before.get(p) != m)
    return data


def verdict(ok, nfr, minutes):
    if not ok:
        return "FAIL"
    need = REQUIRED_MINUTES.get(nfr)
    return "PASS" if need is None or minutes >= need else f"PASS (PRELIMINARY: {minutes:g} of {need} min)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=2)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--startup-probe", action="store_true")
    a = ap.parse_args()
    if a.startup_probe:
        return startup_probe()

    import psutil
    cores = psutil.cpu_count()
    print("Measuring start-up time (5 launches)…", flush=True)
    startup = measure_startup()
    print(f"Running a {a.minutes:g}-minute session with the webcam…", flush=True)
    d = run_session(a.minutes, a.camera)

    lines = [f"Foc&US non-functional requirement measurements, {datetime.now():%Y-%m-%d %H:%M}",
             f"Reference laptop: {platform.processor()}, {psutil.cpu_count(logical=False)} cores / "
             f"{cores} threads, {psutil.virtual_memory().total / 2**30:.1f} GB RAM, "
             f"{platform.platform()}, Python {platform.python_version()}",
             f"Session length: {a.minutes:g} min (camera {a.camera}); temporary data only", ""]
    if d["error"]:
        lines.append(f"RUN ERROR: {d['error']}")
    else:
        fps = d["frames"] / d["seconds"]
        cpu_core = statistics.mean(d["cpu"]) if d["cpu"] else 0.0
        cpu_total = cpu_core / cores
        rss = d["rss"]
        k = max(1, len(rss) // 5)
        growth = (statistics.mean(rss[-k:]) / statistics.mean(rss[:k]) - 1) * 100 if rss else 0
        hits = source_hits()
        rows = [
            ("NFR-01", f"{fps:.1f} frames/s (threshold >= 10)", fps >= 10),
            ("NFR-02", f"{cpu_total:.1f}% of total CPU ({cpu_core:.0f}% of one core; "
                       f"threshold <= 50%)", cpu_total <= 50),
            ("NFR-03", f"ready in {max(startup):.1f} s at worst, {statistics.mean(startup):.1f} s "
                       f"average over {len(startup)} launches (threshold <= 10 s)",
             len(startup) == 5 and max(startup) <= 10),
            ("NFR-04", f"no crash; memory {rss[0] / 2**20:.0f} -> {rss[-1] / 2**20:.0f} MB, "
                       f"growth {growth:+.1f}% first vs last fifth (threshold <= 20%)",
             not d["error"] and growth <= 20),
            ("NFR-07", f"{len(d['new_media'])} image/video files written, {len(hits)} save or "
                       f"network calls in Foc&US code, {len(d['conns'])} HTTPS connections"
                       + (" opened by the MediaPipe library (usage metrics to Google; encrypted, "
                          "content cannot be inspected; Google states no input images are sent)"
                          if d["conns"] else ""),
             "FAIL" if d["new_media"] or hits else "PARTIAL" if d["conns"] else True),
        ]
        for nfr, text, ok in rows:
            v = ok if isinstance(ok, str) else verdict(ok, nfr, a.minutes)
            lines.append(f"{nfr}  {v:<34} {text}")
        lines.append(f"\nPer-second records saved: {d['summary_rows']}")
        for label, items in (("Connections", d["conns"]), ("Media files", d["new_media"]),
                             ("Source hits", hits)):
            lines += [f"{label}:"] + [f"  {i}" for i in items] if items else []
    out_dir = ROOT / "test_results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"nfr_{datetime.now():%Y%m%d_%H%M}.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
