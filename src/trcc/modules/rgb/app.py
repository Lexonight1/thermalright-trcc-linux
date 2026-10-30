"""RGB Lighting's root: the lights TRCC drives itself, and what they follow.

Built when the module is switched on.  It holds the follower -- the thread
that sends a cooler's or an LCD's colours, or TRCC's own effects, to the
memory, the motherboard and OpenRGB's devices -- the motherboard finder, and
its settings, and reaches everything through its host: the hardware
(``host.hardware``), the memory's permission (``host.password``), the
panels' pictures (``host.frames``) and the sensor readings
(``host.readings``).  Its Commands (``core/commands.py``) run against this
class.

Switching it on registers only; the follower starts with the session -- an
App built to run one Command never takes the board or the memory's SMBus
from the running one (2026-10-21).  Switching it off stops following and
gives each light back its own effect, as quitting TRCC always did.
"""
from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from trcc.adapters.modules import BaseModule
from trcc.core.events import Event, LedColorsChanged
from trcc.core.logs import per_frame
from trcc.core.models import PanelPicture, RamAccessState
from trcc.core.ports import ModuleHost, RamAccess

from .adapters import make_board_driver, make_mirror
from .adapters.openrgb import OpenRgbMirror
from .core.events import RgbFollowSent
from .core.models import EFFECT_SOURCE, FollowColors, FollowMapping, RgbFollowMode
from .services.rgb_boards import MotherboardFinder
from .services.rgb_mirror import Columns, RgbMirrorService
from .services.settings import RgbSettings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .core.models import LightEffect
    from .core.ports import RgbMirror

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The memory's permission as TRCC granted it -- installed, whether or not
#: this session has picked it up yet.
_TRCC_GRANT = (RamAccessState.ON, RamAccessState.NOT_APPLIED)
#: Said on RGB's Modules row when switching it off could not take the
#: permission back (the password prompt was closed).
STILL_GRANTED = ("The RAM-lighting permission is still granted -- switch RGB "
                 "Lighting on and off again to remove it.")


class RgbModule(BaseModule, key="rgb"):
    """The follower, the motherboard finder and the settings of RGB
    Lighting -- while it is on."""

    title = "RGB Lighting"
    summary = ("Lights your memory, your motherboard's RGB and OpenRGB's "
               "devices -- with effects, or following a cooler or an LCD.")

    def __init__(self) -> None:
        log.debug("RgbModule.__init__")
        self._watching = False

    # ── switching ──────────────────────────────────────────────────────

    def _enable(self, host: ModuleHost) -> None:
        """Build the follower and the finder, and hear the coolers -- no
        hardware is touched until the session starts."""
        self.settings = RgbSettings(host.settings)
        self.boards = MotherboardFinder(
            host.hardware.ids,
            partial(make_board_driver, open_hid=host.hardware.open_hid,
                    argb_leds=self.settings.aura_argb_leds))
        self.mirror = RgbMirrorService(
            partial(make_mirror, smbuses=host.hardware.smbuses,
                    board=self.boards.driver, openrgb=self._openrgb),
            on_sent=self._sent, readings=host.readings.latest)
        host.subscribe(LedColorsChanged, self.mirror.on_colors)
        log.info("rgb: on")

    def start(self, host: ModuleHost) -> None:
        """The session started: follow again as the settings say."""
        log.info("rgb: session started")
        self.resume_following()

    def stop(self, host: ModuleHost) -> None:
        """The session ended, or the module is off: stop following and
        release the lights to their own effects."""
        log.info("rgb: stopping")
        self._watch_frames(False)
        self.mirror.close()

    def _disable(self, host: ModuleHost) -> None:
        """Off: a follower a one-off Command started is let go too."""
        log.info("rgb: off")
        self._watch_frames(False)
        self.mirror.close()

    def turning_off(self, host: ModuleHost) -> str:
        """The user switches RGB off: TRCC's permission to reach the memory's
        bus goes with it -- no password at the seat where TRCC's polkit files
        are installed (its own "off" action), a prompt otherwise -- since with
        RGB off nothing in TRCC could take it back.  Access another rule gives (OpenRGB's)
        is not TRCC's and stays.  A closed prompt leaves the permission, and
        the note says how to remove it -- in the status's own words when TRCC
        cannot remove it from here at all (no polkit, or NixOS, where the
        system configuration holds it)."""
        access = host.password.memory_access()
        state = access.status().state
        log.info("rgb.turning_off: memory access %s", state.value)
        if state not in _TRCC_GRANT:
            return ""
        status = access.disable()
        if status.state in _TRCC_GRANT:
            log.warning("rgb.turning_off: the permission stays -- %s", status.message)
            return status.message if status.command else STILL_GRANTED
        return ""

    # ── what the Commands reach ────────────────────────────────────────

    def configure(self, mode: RgbFollowMode, host: str, port: int,
                  source: str = "", mapping: FollowMapping = FollowMapping.HALVES,
                  targets: tuple[str, ...] = (), *,
                  colors: FollowColors = FollowColors.VIVID,
                  effects: Mapping[str, LightEffect] | None = None) -> None:
        """Follow as asked (``RgbMirrorService.configure``) -- and watch the
        panels' pictures only while a device leads, so no frame costs RGB
        anything otherwise."""
        log.info("rgb.configure: %s source=%r", mode.value, source)
        self.mirror.configure(mode, host, port, source, mapping, targets,
                              colors=colors, effects=effects)
        self._watch_frames(mode is not RgbFollowMode.OFF
                           and source not in ("", EFFECT_SOURCE))

    def resume_following(self) -> None:
        """Follow again as the settings say -- what a session starts with."""
        prefs, settings = self.settings.prefs, self.settings
        log.info("resume_following: %s source=%r", prefs.rgb_follow,
                 prefs.rgb_follow_source)
        self.configure(settings.rgb_follow_mode(), prefs.openrgb_host,
                       prefs.openrgb_port, prefs.rgb_follow_source,
                       settings.rgb_follow_mapping(), settings.rgb_follow_targets(),
                       colors=settings.rgb_follow_colors(),
                       effects=settings.follow_effects())

    def publish(self, event: Event) -> None:
        """Tell every UI."""
        log.debug("rgb.publish: %s", type(event).__name__)
        self.host.publish(event)

    def memory_access(self) -> RamAccess:
        """The memory's lighting permission (it asks for the password)."""
        log.debug("rgb.memory_access")
        return self.host.password.memory_access()

    # ── its own wiring ─────────────────────────────────────────────────

    def _openrgb(self, host: str, port: int) -> RgbMirror:
        """OpenRGB's client for *host*:*port*, as this machine runs it -- a
        stand-in platform's refuses, so no test reaches a real OpenRGB."""
        log.debug("rgb._openrgb: %s:%d", host, port)
        return self.host.hardware.substitute(OpenRgbMirror)(host, port)

    def _sent(self, source: str, columns: Columns) -> None:
        """What following just sent, for every window's preview."""
        frame_log.debug("rgb._sent: %s %d column(s)", source, len(columns))
        self.host.publish(RgbFollowSent(source=source, columns=columns))

    def _picture(self, picture: PanelPicture) -> None:
        """A panel's picture, to the follower (it reads the pixels only for
        the panel it follows, at its own pace)."""
        frame_log.debug("rgb._picture: %s", picture.key)
        self.mirror.on_frame(picture)

    def _watch_frames(self, on: bool) -> None:
        log.debug("rgb._watch_frames: %s (was %s)", on, self._watching)
        if on == self._watching:
            return
        log.info("rgb: %s the panels' pictures", "watching" if on else "not watching")
        if on:
            self.host.frames.watch(self._picture)
        else:
            self.host.frames.unwatch(self._picture)
        self._watching = on

