# Foc&US

AI-powered attention and posture tracking for students who study alone at a computer.
Prototype for the System Analysis and Design course project FOC-US-26 (Izmir Democracy University,
Management Information Systems). Requirement IDs in the code (FR-xx, NFR-xx, BR-xx, EX-xx) refer
to the cumulative project report.

Foc&US uses an ordinary webcam to estimate posture and gaze direction during a study session that
the user starts. When bad posture or off-screen gaze lasts longer than the user's alert delay, it
shows an alert; at the end it shows a summary. It is an indicator, not a medical or learning
assessment.

## Features

- Camera only during a session the user starts, with a CAMERA ON indicator (FR-01, FR-02)
- Privacy statement and consent, including an 18+ confirmation, before the first use (FR-04)
- Short calibration, then one posture and one gaze state per second (FR-05)
- Distraction events: looking away for 3 seconds or more (FR-06)
- Alerts after a user-set delay, as visual and sound, visual only or silent; until corrected,
  repeated every 2 minutes or once (FR-07, FR-08)
- One-click pause that releases the camera; paused time is not study time (FR-03)
- End-of-session summary: score and label, study time, longest focus streak, distractions and
  posture timeline (FR-09)
- History with per-session deletion, a 7-day trend and CSV export; a Data & Privacy screen that
  shows what is stored and deletes everything (FR-10)
- Note-taking mode, camera choice, lighting warnings and an alert banner above other windows
- Sessions cut off by a crash are recovered at the next start and marked "(recovered)" (EX-09)

## Privacy

- Webcam frames are processed in memory and never saved or sent by Foc&US.
- Only derived data is stored, in `data/sessions.db` on your own computer: one posture and one gaze
  state per second, alert times and session summaries. Settings and consent are in
  `data/settings.json`.
- The MediaPipe tracking library sends performance and usage statistics to Google. According to
  Google these contain no images or video. Foc&US cannot switch this off and says so in the
  consent text and on the Data & Privacy screen.
- Everything stored can be viewed and deleted in the app (Data & Privacy).

## Requirements

- Windows 10 or 11 (64-bit)
- Python 3.14 (the versions in `requirements.txt` were tested with 3.14 on Windows 11)
- A built-in or USB webcam

## Installation

1. Install Python 3.14 from python.org and tick "Add python.exe to PATH" during setup.
2. Download or clone this repository.
3. Double-click `setup.bat`, or run in the project folder:

   ```
   pip install -r requirements.txt
   ```

The pre-trained MediaPipe models are included in `models/`.

## Running

Double-click `foc_us.bat`, or run:

```
python main.py
```

1. Press **Start Session**. The first time, read the privacy statement, confirm that you are 18 or
   older and agree.
2. Sit upright and look at the screen for the short calibration.
3. Study. Use **Pause** for breaks and **End Session** to finish and see the summary.
4. **Settings** changes the alert delay, type and persistence; **History** lists past sessions;
   **Data & Privacy** shows and deletes stored data or withdraws consent.

If no camera is found, close other apps that use it (video calls, browser tabs) or choose another
camera in Settings.

## Tests

```
python -m unittest discover -s tests -v
```

The unit tests check the acceptance conditions of FR-03 to FR-10, EX-09, NFR-08 and NFR-13.

Optional measurements of NFR-01 to NFR-04 and NFR-07 (the webcam is on during the run; results go
to `test_results/`, which is not committed):

```
pip install -r requirements-dev.txt
python tools/measure_nfr.py --minutes 10
```

## Project structure

```
main.py              entry point
core/detector.py     posture and gaze estimation with MediaPipe
core/session.py      sessions, per-second states, summaries, recovery, deletion (SQLite)
core/alarm.py        when an alert is shown
core/settings.py     settings and consent
ui/                  Tkinter windows: main screen, summary, history, settings, data & privacy
tests/               unit tests
tools/measure_nfr.py performance and privacy measurements
models/              pre-trained MediaPipe models
data/                created at run time; not committed
```

## Licenses of third-party components

| Component | License |
|---|---|
| MediaPipe and its pose and face landmarker models | Apache-2.0 |
| OpenCV (opencv-python) | Apache-2.0 |
| NumPy | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| psutil (measurement tool only) | BSD-3-Clause |

No license has been chosen for the Foc&US code itself yet.
