"""
Foc&US User Settings
Persists alert, camera and consent settings to data/settings.json.
"""
import json
from datetime import datetime
from pathlib import Path

DELAY_STEPS = [1, 3, 5, 10, 15, 20, 30, 60, 120, 300]   # seconds (FR-08)
CHANNELS = {                                             # FR-08
    "visual_sound": "Visual and sound",
    "visual": "Visual only",
    "silent": "Silent (summary only)",
}
PERSISTENCE = {                                          # FR-08
    "until_corrected": "Until I correct it",
    "repeat": "Repeat every 2 minutes",
    "once": "Once per episode",
}
CAMERA_CHOICES = [0, 1, 2]                               # Should
# Raise when the consent text changes, so everyone is asked again.
# 2: discloses MediaPipe's usage metrics sent to Google.
CONSENT_VERSION = 2

# Tentative defaults from report Section 5.4
DEFAULTS = {
    "posture_delay": 30,
    "attention_delay": 30,
    "alert_channel": "visual_sound",
    "alert_persistence": "repeat",
    "camera_index": 0,
    "consent_accepted": False,
    "consent_date": "",
    "consent_version": 0,
}

_VALID = {
    "posture_delay": lambda v: v in DELAY_STEPS,
    "attention_delay": lambda v: v in DELAY_STEPS,
    "alert_channel": lambda v: v in CHANNELS,
    "alert_persistence": lambda v: v in PERSISTENCE,
    "camera_index": lambda v: v in CAMERA_CHOICES,
    "consent_accepted": lambda v: isinstance(v, bool),
    "consent_date": lambda v: isinstance(v, str),
    "consent_version": lambda v: isinstance(v, int),
}

_PATH = Path(__file__).parent.parent / "data" / "settings.json"


def load_settings(path: Path = _PATH) -> dict:
    settings = dict(DEFAULTS)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(saved, dict):
        for key, ok in _VALID.items():
            if key in saved and ok(saved[key]):
                settings[key] = saved[key]
    return settings


def save_settings(settings: dict, path: Path = _PATH):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def record_consent(settings: dict):
    settings["consent_accepted"] = True
    settings["consent_date"] = datetime.now().isoformat(timespec="seconds")
    settings["consent_version"] = CONSENT_VERSION


def has_consent(settings: dict) -> bool:
    return settings["consent_accepted"] and settings["consent_version"] == CONSENT_VERSION


def withdraw_consent(settings: dict):
    settings["consent_accepted"] = False
    settings["consent_date"] = ""
    settings["consent_version"] = 0
