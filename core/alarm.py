"""
Foc&US Alarm Controller
Decides when an alert is shown from smoothed posture/gaze flags and the user's
alert settings (FR-07, FR-08). Pure logic: the caller passes the clock.
"""
from typing import Optional


class AlarmController:
    BURST_SECONDS = 4.0       # how long a "repeat" or "once" alert stays on
    REPEAT_INTERVAL = 120.0   # gap between repeated alerts (report Section 5.4)
    KINDS = ("posture", "attention")   # posture wins when both are due

    def __init__(self):
        self.reset()

    def reset(self):
        self._since: dict[str, float] = {}      # when the current episode started
        self._fired_at: dict[str, float] = {}   # last alert start in this episode
        self.active = False
        self.kind: Optional[str] = None

    def update(self, now: float, posture_bad: bool, gaze_away: bool,
               settings: dict, suppressed: bool = False) -> bool:
        """Advance the alarm state; returns whether an alert is showing."""
        flags = {"posture": posture_bad, "attention": gaze_away}
        for kind in self.KINDS:
            if flags[kind]:
                self._since.setdefault(kind, now)
            else:   # episode over: the next one starts fresh (FR-07)
                self._since.pop(kind, None)
                self._fired_at.pop(kind, None)

        self.active, self.kind = False, None
        if suppressed or settings["alert_channel"] == "silent":
            return False

        mode = settings["alert_persistence"]
        for kind in self.KINDS:
            since = self._since.get(kind)
            if since is None or now - since < settings[f"{kind}_delay"]:
                continue
            if mode == "until_corrected":
                showing = True
            else:
                last = self._fired_at.get(kind)
                if last is None or (mode == "repeat" and now - last >= self.REPEAT_INTERVAL):
                    self._fired_at[kind] = last = now
                showing = now - last < self.BURST_SECONDS
            if showing:
                self.active, self.kind = True, kind
                break
        return self.active
