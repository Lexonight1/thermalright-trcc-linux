"""Game mode's hysteresis -- when a busy CPU takes the panel, and when it gives
it back.

A line-for-line port of the C#'s once-a-second check (2.1.8
``FormCZTV.GetSystemInfo``, FormCZTV.cs:2853-2913), kept pure so the counting
is tested without a device, a clock or Qt.  The task that reads the sensor and
draws the frame is separate; it feeds :meth:`GameModeGate.step` one reading a
second and acts on the verdict.

**The counts.**  Engaging takes 11 readings above the threshold in a row: the
first ten raise the counter to 10, the eleventh engages.  Releasing takes 11 at
or below it: the counter falls from 10 to 0, and the next low reading releases.
One reading the other way resets the count -- to 0 while arming, to 10 while
engaged.  "Above" is strict, as the C#'s ``>`` is.

**What each verdict draws.**  The C# draws nothing on the reading that engages
or releases; the game frame first appears one reading after engaging.  So only
:attr:`GameVerdict.HOLD` asks for a game frame.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from ..core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The C#'s literal 10: readings counted before a switch either way.
HOLD_COUNT = 10


class GameVerdict(Enum):
    """What one reading decided."""
    IDLE = "idle"          # not engaged; the panel's own sources run
    ENGAGE = "engage"      # engaged on this reading; nothing drawn yet
    HOLD = "hold"          # engaged; draw the game frame
    RELEASE = "release"    # released on this reading; the panel's own again


@dataclass
class GameModeGate:
    """One panel's game-mode state: the C#'s ``myCpuHigh`` / ``myCpuCount``."""
    engaged: bool = False
    count: int = 0

    def step(self, enabled: bool, value: int, threshold: int) -> GameVerdict:
        """Take one reading of the row the check watches, and decide."""
        frame_log.debug("GameModeGate.step: enabled=%s value=%d threshold=%d "
                        "engaged=%s count=%d", enabled, value, threshold,
                        self.engaged, self.count)
        if not enabled:
            was_engaged, self.engaged, self.count = self.engaged, False, 0
            if was_engaged:
                log.info("GameModeGate: switched off while engaged -- released")
                return GameVerdict.RELEASE
            return GameVerdict.IDLE
        if self.engaged:
            if value > threshold:
                self.count = HOLD_COUNT
            elif self.count <= 0:
                self.engaged = False
                log.info("GameModeGate: %d <= %d%% for %d readings -- released",
                         value, threshold, HOLD_COUNT + 1)
                return GameVerdict.RELEASE
            else:
                self.count -= 1
            return GameVerdict.HOLD
        if value <= threshold:
            self.count = 0
        elif self.count < HOLD_COUNT:
            self.count += 1
        else:
            self.engaged = True
            log.info("GameModeGate: %d > %d%% for %d readings -- engaged",
                     value, threshold, HOLD_COUNT + 1)
            return GameVerdict.ENGAGE
        return GameVerdict.IDLE
