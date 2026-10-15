"""The RGB page's Commands: the lights, finding them, and RAM effects.

Following (``SetRgbFollow`` / ``RgbFollow``) lives with the LED Commands it
grew out of; these are what the RGB page adds.  Every one is the same for
every UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..events import RgbFollowChanged, RgbLightsChanged
from ..models import (
    AURA_HEADER_REF,
    MAX_ARGB_LEDS,
    EffectDirection,
    EffectSpeed,
    LightKind,
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbLight,
)
from ..ram_effects import effect_problem
from ..results import RgbLightsResult
from ._base import Command, Query

if TYPE_CHECKING:
    from ...app import App
    from ...services.rgb_boards import BoardScan

log = logging.getLogger(__name__)


def _lights(app: App, message: str = "", *, ok: bool = True) -> RgbLightsResult:
    """Every light from the last scans, as the page shows them -- no bus.

    A kind not looked for since the App started lists what the last Find
    saved, each light marked ``remembered``."""
    sticks = app.rgb_mirror.ram_sticks()
    openrgb, openrgb_error = app.rgb_mirror.openrgb_lights()
    boards = app.rgb_boards.last()
    lights: list[RgbLight] = []
    # A board Find could not read lists its last record, not nothing.
    driven = boards.driven if boards and not boards.error else None
    for kind, found in ((LightKind.RAM, sticks), (LightKind.OPENRGB, openrgb),
                        (LightKind.BOARD, driven),
                        (LightKind.FOUND, boards and boards.found)):
        remembered = found is None
        devices = app.settings.found_lights(kind) if found is None else found
        lights += [RgbLight(d.ref, d.name, d.led_count, kind,
                            app.settings.ram_effect(d.ref)
                            if kind is LightKind.RAM else None,
                            remembered=remembered)
                   for d in devices]
    log.debug("_lights: %d light(s), scanned=%s, %d remembered", len(lights),
              sticks is not None, sum(light.remembered for light in lights))
    return RgbLightsResult(
        ok=ok, message=message, lights=tuple(lights),
        scanned=sticks is not None, openrgb_error=openrgb_error,
        ram_access=app.platform.ram_access().status().state)


def _board_summary(boards: BoardScan) -> str:
    """What the motherboard part of a Find found, in a few words."""
    log.debug("_board_summary: %s", boards)
    if boards.error:
        return boards.error
    parts = [f"{len(boards.driven)} light(s)"] if boards.driven else []
    if boards.found:
        parts.append(f"{len(boards.found)} controller(s) TRCC does not drive")
    return ", ".join(parts) or "none found"


@dataclass(frozen=True, slots=True)
class RgbLights(Query[RgbLightsResult]):
    """The lights the RGB page lists, from the last scan -- touches no bus."""

    def execute(self, app: App) -> RgbLightsResult:
        log.debug("RgbLights: query")
        return _lights(app)


@dataclass(frozen=True, slots=True)
class ScanRgbLights(Command[RgbLightsResult]):
    """Look for the lights: RGB memory on the SMBus, and OpenRGB's devices.

    Only ever because a user asked ("Find lights") -- the RAM probe reads
    the chipset bus.  It is refused while the memory's SPD hubs are stuck
    (``LinuxOS.smbuses``), and without access to the bus.

    ``host``:``port`` is where OpenRGB is looked for; an empty host or a port
    of 0 keeps the saved address.  A new address is saved -- and handed to
    following, when it follows OpenRGB -- so the page, the setting and the
    follower never point at different servers.
    """
    host: str = ""
    port: int = 0

    def execute(self, app: App) -> RgbLightsResult:
        log.info("ScanRgbLights: openrgb %s:%d", self.host or "(saved)",
                 self.port)
        prefs = app.settings.app
        host = self.host or prefs.openrgb_host
        port = self.port or prefs.openrgb_port
        if (host, port) != (prefs.openrgb_host, prefs.openrgb_port):
            try:
                self._move_openrgb(app, host, port)
            except ValueError as e:
                log.warning("ScanRgbLights: refused -- %s", e)
                return _lights(app, message=str(e), ok=False)
        ram_error = ""
        try:
            sticks = app.rgb_mirror.scan_ram()
        except OSError as e:
            sticks = ()
            ram_error = str(e)
            log.warning("ScanRgbLights: the RAM could not be searched -- %s", e)
        else:
            app.settings.set_found_lights(LightKind.RAM, sticks)
        app.rgb_mirror.scan_openrgb(host, port)
        openrgb, _error = app.rgb_mirror.openrgb_lights()
        if openrgb is not None:
            app.settings.set_found_lights(LightKind.OPENRGB, openrgb)
        boards = app.rgb_boards.scan()
        if not boards.error:              # a board not read keeps its record
            app.settings.set_found_lights(LightKind.BOARD, boards.driven)
            app.settings.set_found_lights(LightKind.FOUND, boards.found)
        app.events.publish(RgbLightsChanged())
        message = (f"RAM: {ram_error}" if ram_error else
                   f"Found {len(sticks)} RGB memory stick(s)")
        message += (f"; OpenRGB: {len(openrgb)} device(s)" if openrgb is not None
                    else "; OpenRGB: not reachable")
        message += f"; motherboard: {_board_summary(boards)}"
        return _lights(app, message=message,
                       ok=not ram_error and not boards.error)

    @staticmethod
    def _move_openrgb(app: App, host: str, port: int) -> None:
        """Save OpenRGB's new address; a follower of it moves there too."""
        settings = app.settings
        prefs = settings.app
        mode = settings.rgb_follow_mode()
        log.info("ScanRgbLights: OpenRGB moves to %s:%d (following %s)", host,
                 port, mode.value)
        colors = settings.rgb_follow_colors()
        settings.set_rgb_follow(mode, host, port, prefs.rgb_follow_source,
                                settings.rgb_follow_mapping(),
                                settings.rgb_follow_targets(), colors=colors)
        if mode is RgbFollowMode.OPENRGB:
            app.rgb_mirror.configure(mode, host, port, prefs.rgb_follow_source,
                                     settings.rgb_follow_mapping(),
                                     settings.rgb_follow_targets(),
                                     colors=colors)


@dataclass(frozen=True, slots=True)
class SetRamEffect(Command[RgbLightsResult]):
    """Save one of the memory's own effects on the sticks named by ``refs``
    (every stick when empty).

    The stick keeps it after TRCC closes and through a reboot; it is written
    once, here, never on a timer.  Settings the effect cannot take are
    refused before anything changes.  RAM following stops first -- its
    colours would cover the effect at once.
    """
    effect: RamEffect
    refs: tuple[str, ...] = ()
    speed: EffectSpeed = EffectSpeed.MEDIUM
    direction: EffectDirection | None = None
    colors: tuple[tuple[int, int, int], ...] = ()
    random_colors: bool = False
    brightness: int = 255

    def execute(self, app: App) -> RgbLightsResult:
        settings = RamEffectSettings(
            effect=self.effect, speed=self.speed, direction=self.direction,
            colors=self.colors, random_colors=self.random_colors,
            brightness=self.brightness)
        log.info("SetRamEffect: %s on %s", settings, self.refs or "every stick")
        if (problem := effect_problem(settings)) is not None:
            log.warning("SetRamEffect: refused -- %s", problem)
            return _lights(app, message=problem, ok=False)
        follow_stopped = app.settings.rgb_follow_mode() is RgbFollowMode.RAM
        if follow_stopped:
            self._stop_ram_follow(app)
        try:
            applied = app.rgb_mirror.apply_effect(self.refs, settings)
        except (ValueError, OSError) as e:
            log.warning("SetRamEffect: not saved -- %s", e)
            return _lights(app, message=str(e), ok=False)
        for stick in applied:
            app.settings.set_ram_effect(stick.ref, settings)
        app.events.publish(RgbLightsChanged())
        message = (f"{self.effect.value} saved on {len(applied)} stick(s)"
                   + (" -- RAM following turned off" if follow_stopped else ""))
        return _lights(app, message=message)

    @staticmethod
    def _stop_ram_follow(app: App) -> None:
        """Following off, everything else about it kept -- in every UI."""
        prefs, settings = app.settings.app, app.settings
        log.info("SetRamEffect: RAM following off first")
        settings.set_rgb_follow(
            RgbFollowMode.OFF, prefs.openrgb_host, prefs.openrgb_port,
            prefs.rgb_follow_source, settings.rgb_follow_mapping(),
            settings.rgb_follow_targets(),
            colors=settings.rgb_follow_colors())
        app.rgb_mirror.configure(RgbFollowMode.OFF, prefs.openrgb_host,
                                 prefs.openrgb_port)
        app.events.publish(RgbFollowChanged(mode=RgbFollowMode.OFF))


@dataclass(frozen=True, slots=True)
class SetAuraLeds(Command[RgbLightsResult]):
    """How many LEDs an ASUS Aura ARGB header drives -- the board cannot
    say, its header's data line runs one way.

    Saved, then the board is read again -- its two read requests, no
    colours -- so every UI lists the new count.  A header the last Find did
    not list is refused before anything changes.
    """
    header: int
    leds: int

    def execute(self, app: App) -> RgbLightsResult:
        log.info("SetAuraLeds: header %d -> %d LED(s)", self.header, self.leds)
        if not 0 <= self.leds <= MAX_ARGB_LEDS:
            log.warning("SetAuraLeds: refused -- %d LEDs", self.leds)
            return _lights(app, message=f"an ARGB header drives 0-"
                           f"{MAX_ARGB_LEDS} LEDs, not {self.leds}", ok=False)
        ref = AURA_HEADER_REF.format(self.header)
        if ref not in {light.ref for light in _lights(app).lights
                       if light.kind is LightKind.BOARD}:
            log.warning("SetAuraLeds: refused -- no %s listed", ref)
            return _lights(app, ok=False, message=(
                f"no ARGB header {self.header} -- press Find lights to list "
                "the board's headers"))
        app.settings.set_aura_leds(self.header, self.leds)
        boards = app.rgb_boards.scan()
        if not boards.error:
            app.settings.set_found_lights(LightKind.BOARD, boards.driven)
            app.settings.set_found_lights(LightKind.FOUND, boards.found)
        app.events.publish(RgbLightsChanged())
        message = f"ARGB header {self.header}: {self.leds} LED(s)"
        if boards.error:
            message += f" -- saved, but {boards.error}"
        return _lights(app, message=message, ok=not boards.error)


@dataclass(frozen=True, slots=True)
class BlinkAuraHeader(Command[RgbLightsResult]):
    """Blink an ASUS Aura ARGB header for two seconds, so the user can see
    which header the fan or strip is on -- before its LED count is known.

    Nothing is saved: the board goes back to its own effect afterwards, or,
    while it is followed, to what the follower sends next.
    """
    header: int

    def execute(self, app: App) -> RgbLightsResult:
        ref = AURA_HEADER_REF.format(self.header)
        log.info("BlinkAuraHeader: %s", ref)
        try:
            device = app.rgb_mirror.blink(ref)
        except ValueError:
            log.warning("BlinkAuraHeader: refused -- no %s on the board", ref)
            return _lights(app, ok=False,
                           message=f"the motherboard has no ARGB header "
                                   f"{self.header}")
        except OSError as e:
            log.warning("BlinkAuraHeader: %s could not be blinked -- %s", ref, e)
            return _lights(app, ok=False, message=f"cannot blink: {e}")
        return _lights(app, message=f"{device.name} blinked")
