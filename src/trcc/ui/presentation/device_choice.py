"""DeviceChoice — which device a graphical window shows, for EVERY skin.

The gui is the blueprint for how a graphical UI behaves, and a skin is only
its look (his rule, 2026-11-10).  These are the gui's rules for the device on
screen, written once and toolkit-free, so the gui, hudgui and qtgui each bind
their own widgets to the same decisions instead of re-deriving them:

1. A window opens on the device last shown in any window (``ui_state.json``),
   else the first LCD attached, else the first device -- an LED controller is
   the default only when it is all there is.
2. Every device shown is remembered as last used.
3. The device on screen going away puts the first remaining one on screen,
   and the lost one is remembered: when it comes back (a VM, a hub reset) it
   takes the screen again, so a blink never leaves the user on another panel.
4. A user's pick ends that: a returning device does not take the screen back
   from their choice.
5. Any other device arriving moves nothing -- unless nothing is on screen,
   when it is the default again (a window opened before any cooler was in).

Rule 1 had never held in the gui: its sidebar selected the first device while
the window was still being built, which recorded it as last used before the
restore read it, so every start opened on the first device.  The sidebar now
only shows what this decides.

*attached* is always every device attached now, key -> kind (``"lcd"`` /
``"led"``), in the order the window lists them.  Every method answers the key
to show -- ``""`` for none -- or ``None`` for "leave the screen as it is".  No
toolkit here: plain ``pytest`` tests it.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

log = logging.getLogger(__name__)


class DeviceChoice:
    """The device a window shows, and the one to bring back."""

    def __init__(self, last_used: str, remember: Callable[[str], None]) -> None:
        log.info("DeviceChoice: last used %r", last_used)
        self._last_used = last_used
        self._remember = remember
        #: The device on screen, or "" for none.
        self.shown = ""
        #: The device that went from the screen, until it is back or the user picks.
        self._lost_from = ""

    def opened(self, attached: Mapping[str, str]) -> str:
        """The window opened over *attached*: the last used, else the first."""
        log.info("DeviceChoice.opened: %s (last used %r)", dict(attached), self._last_used)
        return self._show(self._default(attached))

    def picked(self, key: str) -> str:
        """The user picked *key* -- a lost device no longer comes back over it."""
        log.info("DeviceChoice.picked: %r (lost %r forgotten)", key, self._lost_from)
        self._lost_from = ""
        return self._show(key)

    def connected(self, key: str, attached: Mapping[str, str]) -> str | None:
        """*key* arrived; *attached* is everything attached now."""
        if key and key == self._lost_from:
            log.info("DeviceChoice.connected: %r is back -- on screen again", key)
            self._lost_from = ""
            return self._show(key)
        if not self.shown:
            log.info("DeviceChoice.connected: %r, nothing on screen -- the default", key)
            return self._show(self._default(attached))
        log.debug("DeviceChoice.connected: %r -- %r stays on screen", key, self.shown)
        return None

    def disconnected(self, key: str, attached: Mapping[str, str]) -> str | None:
        """*key* went; *attached* is what is left."""
        if key != self.shown:
            log.debug("DeviceChoice.disconnected: %r was not on screen", key)
            return None
        log.info("DeviceChoice.disconnected: %r left the screen -- remembered", key)
        self._lost_from = key
        return self._show(self._default(attached))

    def _default(self, attached: Mapping[str, str]) -> str:
        """The last used while attached, else the first LCD, else the first."""
        lcds = [key for key, kind in attached.items() if kind == "lcd"]
        chosen = (self._last_used if self._last_used in attached
                  else next(iter(lcds or attached), ""))
        log.debug("DeviceChoice._default: %s -> %r", dict(attached), chosen)
        return chosen

    def _show(self, key: str) -> str:
        """*key* is on screen now -- and, being shown, the last used."""
        log.debug("DeviceChoice._show: %r -> %r", self.shown, key)
        self.shown = key
        if key and key != self._last_used:
            self._last_used = key
            self._remember(key)
        return key
