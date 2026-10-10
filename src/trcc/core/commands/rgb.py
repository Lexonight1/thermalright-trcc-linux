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

log = logging.getLogger(__name__)


def _lights(app: App, message: str = "", *, ok: bool = True) -> RgbLightsResult:
    """Every light from the last scans, as the page shows them -- no bus."""
    sticks = app.rgb_mirror.ram_sticks()
    openrgb, openrgb_error = app.rgb_mirror.openrgb_lights()
    lights = [RgbLight(s.ref, s.name, s.led_count, LightKind.RAM,
                       app.settings.ram_effect(s.ref)) for s in sticks or ()]
    lights += [RgbLight(d.ref, d.name, d.led_count, LightKind.OPENRGB)
               for d in openrgb or ()]
    log.debug("_lights: %d light(s), scanned=%s", len(lights),
              sticks is not None)
    return RgbLightsResult(
        ok=ok, message=message, lights=tuple(lights),
        scanned=sticks is not None, openrgb_error=openrgb_error,
        ram_access=app.platform.ram_access().status().state)


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
        app.rgb_mirror.scan_openrgb(host, port)
        app.events.publish(RgbLightsChanged())
        openrgb, _error = app.rgb_mirror.openrgb_lights()
        message = (f"RAM: {ram_error}" if ram_error else
                   f"Found {len(sticks)} RGB memory stick(s)")
        message += (f"; OpenRGB: {len(openrgb)} device(s)" if openrgb is not None
                    else "; OpenRGB: not reachable")
        return _lights(app, message=message, ok=not ram_error)

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
