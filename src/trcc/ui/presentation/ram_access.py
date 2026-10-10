"""What a window says about RAM lighting -- no toolkit.

One ``RamLighting`` answer becomes a line of status and one button.  Both
skins show this, so they say the same thing in the same words, and the
warning before enabling is written once.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ...core.models import RamAccessState
from ...core.results import RamLightingResult

log = logging.getLogger(__name__)

CONFIRM_TITLE = "Enable RAM lighting?"
CONFIRM_TEXT = (
    "Your memory's lighting chips sit on the motherboard's SMBus, beside the "
    "chips that hold the memory's own settings.  Linux can only open the whole "
    "bus, so this lets programs you run reach all of it -- not just TRCC.\n\n"
    "- TRCC only ever talks to the lighting chips.\n"
    "- Only the person logged in at this computer gets access, and only this "
    "one bus (not the graphics card's or any other).\n"
    "- Turn it off here at any time; it is removed completely.\n\n"
    "Your password is asked for to install the permission.")
WAITING = "Waiting for the password..."


@dataclass(frozen=True, slots=True)
class RamAccessView:
    """The row: its status line and its one button.

    ``enables`` is what the button asks for -- True to enable, False to turn
    off, None when there is no button (nothing to switch here, or busy).
    """
    status: str
    button: str = ""
    enables: bool | None = None


def ram_access_view(result: RamLightingResult, *, busy: bool = False
                    ) -> RamAccessView:
    """*result* as the row shows it; *busy* while a switch waits on the user."""
    log.debug("ram_access_view: %s busy=%s", result.state.value, busy)
    if busy:
        return RamAccessView(WAITING)
    state = result.state
    if state in (RamAccessState.UNSUPPORTED, RamAccessState.NO_BUS):
        return RamAccessView(result.message)
    if state in (RamAccessState.ON, RamAccessState.NOT_APPLIED):
        return RamAccessView(result.message, "Turn off RAM lighting", False)
    if result.command:
        # No password prompt from here (a pip install): say what will do it.
        return RamAccessView(f"{result.message} -- to enable, run: "
                             f"{result.command}")
    return RamAccessView(result.message, "Enable RAM lighting...", True)
