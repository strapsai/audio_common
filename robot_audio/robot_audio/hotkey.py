"""Edge-triggered LT+X on routed, normalized Joy; no inspection-state dependency."""
import math


class AudioHotkey:
    def __init__(self, max_age=0.5):
        self.max_age = max_age
        self.ready = False
        self.last_x = False
        self.last_received = None
        self.last_stamp = None

    def observe(self, axes, buttons, stamp, now, received):
        valid = (len(axes) == 8 and len(buttons) == 11
                 and all(math.isfinite(v) and -1 <= v <= 1 for v in axes)
                 and all(v in (0, 1) for v in buttons)
                 and stamp > 0 and -0.1 <= now - stamp < self.max_age)
        if not valid:
            self.ready = False
            return False
        if self.last_received is not None and received - self.last_received >= self.max_age:
            self.ready = False
        self.last_received = received
        if self.last_stamp is not None and stamp <= self.last_stamp:
            self.ready = False
            self.last_stamp = stamp
            return False
        self.last_stamp = stamp
        x, lt, rt = bool(buttons[2]), axes[2] < 0, axes[5] < 0
        pressed = self.ready and x and not self.last_x and lt and not rt
        pressed = pressed and not any(b for i, b in enumerate(buttons) if i != 2)
        self.last_x = x
        # Require released buttons/triggers after startup, stale input or reconnect.
        if not any(buttons) and not lt and not rt:
            self.ready = True
        return pressed
