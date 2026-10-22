"""The RGB page's Commands: the lights, finding them, and RAM effects.

Following (``SetRgbFollow`` / ``RgbFollow``) lives with the LED Commands it
grew out of; these are what the RGB page adds.  Every one is the same for
every UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from trcc.core.commands._base import ModuleCommand, ModuleQuery
from trcc.core.models import LinkedMetric, RamAccessState, RamAccessStatus, SensorLink
from trcc.modules.rgb.core.events import RamLightingChanged, RgbFollowChanged, RgbLightsChanged
from trcc.modules.rgb.core.models import (
    AURA_HEADER_REF,
    EFFECT_SOURCE,
    MAX_ARGB_LEDS,
    EffectDirection,
    EffectSpeed,
    FollowColors,
    FollowMapping,
    LightEffect,
    LightKind,
    LinkedEffect,
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbLight,
)
from trcc.modules.rgb.core.ram_effects import effect_problem
from trcc.modules.rgb.core.results import RamLightingResult, RgbFollowResult, RgbLightsResult

if TYPE_CHECKING:
    from trcc.modules.rgb.app import RgbModule
    from trcc.modules.rgb.services.rgb_boards import BoardScan

log = logging.getLogger(__name__)

#: The module these Commands belong to.
_KEY = "rgb"

#: The lights that take a built-in effect: a stick saves it, a motherboard
#: light runs it as TRCC draws it.
_EFFECT_KINDS = (LightKind.RAM, LightKind.BOARD)


def _lights(module: RgbModule, message: str = "", *, ok: bool = True) -> RgbLightsResult:
    """Every light from the last scans, as the page shows them -- no bus.

    A kind not looked for since the App started lists what the last Find
    saved, each light marked ``remembered``."""
    sticks = module.mirror.ram_sticks()
    openrgb, openrgb_error = module.mirror.openrgb_lights()
    boards = module.boards.last()
    lights: list[RgbLight] = []
    # A board Find could not read lists its last record, not nothing.
    driven = boards.driven if boards and not boards.error else None
    for kind, found in ((LightKind.RAM, sticks), (LightKind.OPENRGB, openrgb),
                        (LightKind.BOARD, driven),
                        (LightKind.FOUND, boards and boards.found)):
        remembered = found is None
        devices = module.settings.found_lights(kind) if found is None else found
        lights += [RgbLight(d.ref, d.name, d.led_count, kind,
                            module.settings.light_effect(d.ref)
                            if kind in _EFFECT_KINDS else None,
                            remembered=remembered)
                   for d in devices]
    log.debug("_lights: %d light(s), scanned=%s, %d remembered", len(lights),
              sticks is not None, sum(light.remembered for light in lights))
    return RgbLightsResult(
        ok=ok, message=message, lights=tuple(lights),
        scanned=sticks is not None, openrgb_error=openrgb_error,
        ram_access=module.memory_access().status().state)


def _board_summary(boards: BoardScan) -> str:
    """What the motherboard part of a Find found, in a few words."""
    log.debug("_board_summary: %s", boards)
    if boards.error:
        return boards.error
    parts = [f"{len(boards.driven)} light(s)"] if boards.driven else []
    if boards.found:
        parts.append(f"{len(boards.found)} controller(s) TRCC does not drive")
    return ", ".join(parts) or "none found"


def _find_boards(module: RgbModule) -> BoardScan:
    """Look for the motherboard's lights -- the ones TRCC drives taken from
    its driver -- and keep what was read; a board that could not be read
    keeps its record."""
    boards = module.boards.scan(held=module.mirror.board_lights())
    log.info("_find_boards: %d driven, %d found%s", len(boards.driven),
             len(boards.found), f" -- {boards.error}" if boards.error else "")
    if not boards.error:
        module.settings.set_found_lights(LightKind.BOARD, boards.driven)
        module.settings.set_found_lights(LightKind.FOUND, boards.found)
    return boards


def _refollow_board(module: RgbModule) -> None:
    """Start a motherboard follower again from the settings, so its driver
    reads the LED counts and effects as they are now -- a driver reads them
    once, when it is made.  Nothing when the board is not followed."""
    if module.settings.rgb_follow_mode() is not RgbFollowMode.BOARD:
        log.debug("_refollow_board: the board is not followed")
        return
    log.info("_refollow_board")
    module.resume_following()


@dataclass(frozen=True, slots=True)
class RgbLights(ModuleQuery[RgbLightsResult, "RgbModule"]):
    """The lights the RGB page lists, from the last scan -- touches no bus."""

    MODULE: ClassVar[str] = _KEY

    def run(self, module: RgbModule) -> RgbLightsResult:
        log.debug("RgbLights: query")
        return _lights(module)


@dataclass(frozen=True, slots=True)
class ScanRgbLights(ModuleCommand[RgbLightsResult, "RgbModule"]):
    """Look for the lights: RGB memory on the SMBus, and OpenRGB's devices.

    Only ever because a user asked ("Find lights") -- the RAM probe reads
    the chipset bus.  It is refused while the memory's SPD hubs are stuck
    (``LinuxOS.smbuses``), and without access to the bus.

    ``host``:``port`` is where OpenRGB is looked for; an empty host or a port
    of 0 keeps the saved address.  A new address is saved -- and handed to
    following, when it follows OpenRGB -- so the page, the setting and the
    follower never point at different servers.
    """

    MODULE: ClassVar[str] = _KEY
    host: str = ""
    port: int = 0

    def run(self, module: RgbModule) -> RgbLightsResult:
        log.info("ScanRgbLights: openrgb %s:%d", self.host or "(saved)",
                 self.port)
        prefs = module.settings.prefs
        host = self.host or prefs.openrgb_host
        port = self.port or prefs.openrgb_port
        if (host, port) != (prefs.openrgb_host, prefs.openrgb_port):
            try:
                self._move_openrgb(module, host, port)
            except ValueError as e:
                log.warning("ScanRgbLights: refused -- %s", e)
                return _lights(module, message=str(e), ok=False)
        ram_error = ""
        try:
            sticks = module.mirror.scan_ram()
        except OSError as e:
            sticks = ()
            ram_error = str(e)
            log.warning("ScanRgbLights: the RAM could not be searched -- %s", e)
        else:
            module.settings.set_found_lights(LightKind.RAM, sticks)
        module.mirror.scan_openrgb(host, port)
        openrgb, _error = module.mirror.openrgb_lights()
        if openrgb is not None:
            module.settings.set_found_lights(LightKind.OPENRGB, openrgb)
        boards = _find_boards(module)
        module.publish(RgbLightsChanged())
        message = (f"RAM: {ram_error}" if ram_error else
                   f"Found {len(sticks)} RGB memory stick(s)")
        message += (f"; OpenRGB: {len(openrgb)} device(s)" if openrgb is not None
                    else "; OpenRGB: not reachable")
        message += f"; motherboard: {_board_summary(boards)}"
        return _lights(module, message=message,
                       ok=not ram_error and not boards.error)

    @staticmethod
    def _move_openrgb(module: RgbModule, host: str, port: int) -> None:
        """Save OpenRGB's new address; a follower of it moves there too."""
        settings = module.settings
        prefs = settings.prefs
        mode = settings.rgb_follow_mode()
        log.info("ScanRgbLights: OpenRGB moves to %s:%d (following %s)", host,
                 port, mode.value)
        colors = settings.rgb_follow_colors()
        settings.set_rgb_follow(mode, host, port, prefs.rgb_follow_source,
                                settings.rgb_follow_mapping(),
                                settings.rgb_follow_targets(), colors=colors)
        if mode is RgbFollowMode.OPENRGB:
            module.configure(mode, host, port, prefs.rgb_follow_source,
                                     settings.rgb_follow_mapping(),
                                     settings.rgb_follow_targets(),
                                     colors=colors)


@dataclass(frozen=True, slots=True)
class SetRamEffect(ModuleCommand[RgbLightsResult, "RgbModule"]):
    """Save one of the memory's own effects on the sticks named by ``refs``
    (every stick when empty).

    The stick keeps it after TRCC closes and through a reboot; it is written
    once, here, never on a timer.  Settings the effect cannot take are
    refused before anything changes.  RAM following stops first -- its
    colours would cover the effect at once.
    """

    MODULE: ClassVar[str] = _KEY
    effect: RamEffect
    refs: tuple[str, ...] = ()
    speed: EffectSpeed = EffectSpeed.MEDIUM
    direction: EffectDirection | None = None
    colors: tuple[tuple[int, int, int], ...] = ()
    random_colors: bool = False
    brightness: int = 255

    def run(self, module: RgbModule) -> RgbLightsResult:
        settings = RamEffectSettings(
            effect=self.effect, speed=self.speed, direction=self.direction,
            colors=self.colors, random_colors=self.random_colors,
            brightness=self.brightness)
        log.info("SetRamEffect: %s on %s", settings, self.refs or "every stick")
        if (problem := effect_problem(settings)) is not None:
            log.warning("SetRamEffect: refused -- %s", problem)
            return _lights(module, message=problem, ok=False)
        follow_stopped = (module.settings.rgb_follow_mode() is RgbFollowMode.RAM
                          and self._release_from_follow(module))
        try:
            applied = module.mirror.apply_effect(self.refs, settings)
        except (ValueError, OSError) as e:
            log.warning("SetRamEffect: not saved -- %s", e)
            return _lights(module, message=str(e), ok=False)
        for stick in applied:
            module.settings.set_light_effect(stick.ref, settings)
        module.publish(RgbLightsChanged())
        message = (f"{self.effect.value} saved on {len(applied)} stick(s)"
                   + (" -- RAM following turned off" if follow_stopped else ""))
        return _lights(module, message=message)

    def _release_from_follow(self, module: RgbModule) -> bool:
        """Take the sticks this saves on out of RAM following first -- its
        colours would cover the effect -- and leave the others following:
        one stick on a linked colour, the other on Rain (his ask,
        2026-10-21).  Following stops only when no stick is left in it.
        True when it stopped."""
        prefs, settings = module.settings.prefs, module.settings
        sticks = [light.ref for light in _lights(module).lights
                  if light.kind is LightKind.RAM]
        following = settings.rgb_follow_targets() or tuple(sticks)
        written = set(self.refs or sticks)
        left = tuple(ref for ref in following if ref not in written)
        log.info("SetRamEffect: %s leave following; %s keep it",
                 sorted(written), list(left))
        settings.set_rgb_follow(
            RgbFollowMode.RAM if left else RgbFollowMode.OFF,
            prefs.openrgb_host, prefs.openrgb_port, prefs.rgb_follow_source,
            settings.rgb_follow_mapping(), left or settings.rgb_follow_targets(),
            colors=settings.rgb_follow_colors())
        module.resume_following()
        module.publish(RgbFollowChanged(mode=settings.rgb_follow_mode()))
        return not left


@dataclass(frozen=True, slots=True)
class SetAuraEffect(ModuleCommand[RgbLightsResult, "RgbModule"]):
    """Run one of the built-in effects on the motherboard's lights named by
    ``refs`` -- ARGB headers (``aura/argb1``) or the board's own LEDs
    (``aura/onboard``); every one the last Find listed when empty.

    TRCC draws it and sends it in direct mode for as long as its App runs:
    nothing is saved on the board, which shows its own effect before TRCC
    starts and after ``trcc kill``.  Each light keeps its own effect, so a
    light already running one keeps it.  Whatever followed before stops --
    the lights show one source at a time.  Settings the effect cannot take,
    an unknown light, or a header with no LED count are refused before
    anything changes.
    """

    MODULE: ClassVar[str] = _KEY
    effect: RamEffect
    refs: tuple[str, ...] = ()
    speed: EffectSpeed = EffectSpeed.MEDIUM
    direction: EffectDirection | None = None
    colors: tuple[tuple[int, int, int], ...] = ()
    random_colors: bool = False
    brightness: int = 255

    def run(self, module: RgbModule) -> RgbLightsResult:
        settings = RamEffectSettings(
            effect=self.effect, speed=self.speed, direction=self.direction,
            colors=self.colors, random_colors=self.random_colors,
            brightness=self.brightness)
        log.info("SetAuraEffect: %s on %s", settings, self.refs or "every light")
        if (problem := effect_problem(settings)) is not None:
            log.warning("SetAuraEffect: refused -- %s", problem)
            return _lights(module, message=problem, ok=False)
        return _run_on_board(module, self.refs, settings, self.effect.value)


@dataclass(frozen=True, slots=True)
class SetLinkedColor(ModuleCommand[RgbLightsResult, "RgbModule"]):
    """Colour the lights named by ``refs`` -- RAM sticks, or the motherboard's
    lights, not both -- by a reading: ``metric``, the temperature or the
    load, of ``source``, the CPU or the GPU, on a green-yellow-orange-red
    scale, red when hot or busy.  One colour per
    light, changed when the reading moves it.

    The LED cooler's Temperature-linked and Load-linked modes.  Live, not
    saved: a stick cannot read a sensor, so TRCC sends the colour, and only
    when it changes; a stick keeps its last colour when TRCC stops, a header
    goes back to the board's own effect.  Whatever followed before stops --
    TRCC sends to one device at a time.
    """

    MODULE: ClassVar[str] = _KEY
    metric: LinkedMetric
    refs: tuple[str, ...] = ()
    source: SensorLink = "cpu"

    def run(self, module: RgbModule) -> RgbLightsResult:
        log.info("SetLinkedColor: %s of %s on %s", self.metric, self.source,
                 self.refs)
        kinds = {light.ref: light.kind for light in _lights(module).lights}
        named = {kinds.get(ref) for ref in self.refs}
        effect = LinkedEffect(metric=self.metric, source=self.source)
        name = f"{self.metric}-linked ({self.source})"
        if not self.refs:
            problem = ("name the lights -- RAM sticks (i2c-...) or motherboard "
                       "lights (aura/...), see `trcc system rgb`")
        elif None in named:
            problem = (f"no light {', '.join(r for r in self.refs if r not in kinds)}"
                       " -- press Find lights")
        elif named == {LightKind.BOARD}:
            return _run_on_board(module, self.refs, effect, name)
        elif named == {LightKind.RAM}:
            return _follow_with_effect(module, RgbFollowMode.RAM, self.refs, effect,
                                       name)
        else:
            problem = ("a linked colour goes to RAM sticks or to motherboard "
                       "lights -- not both, and not OpenRGB's")
        log.warning("SetLinkedColor: refused -- %s", problem)
        return _lights(module, message=problem, ok=False)


def _run_on_board(module: RgbModule, refs: tuple[str, ...], effect: LightEffect,
                  name: str) -> RgbLightsResult:
    """*effect* on the board lights *refs* (every one listed when empty) --
    what ``SetAuraEffect`` and ``SetLinkedColor`` share.  Refused before
    anything changes for an unknown light or a header with no LED count;
    lights already running TRCC's effect keep theirs."""
    board = {light.ref: light for light in _lights(module).lights
             if light.kind is LightKind.BOARD}
    named = refs or tuple(board)
    if problem := _board_refusal(board, named):
        log.warning("_run_on_board: refused -- %s", problem)
        return _lights(module, message=problem, ok=False)
    return _follow_with_effect(module, RgbFollowMode.BOARD, named, effect, name)


def _follow_with_effect(module: RgbModule, mode: RgbFollowMode, named: tuple[str, ...],
                        effect: LightEffect, name: str) -> RgbLightsResult:
    """*named* lights of *mode*'s kind run *effect* on TRCC's effect clock;
    lights already on it keep theirs.  The follower restarts from the
    settings, as a session starts it."""
    prefs, previous = module.settings.prefs, module.settings.rgb_follow_mode()
    running = (previous is mode and prefs.rgb_follow_source == EFFECT_SOURCE)
    targets = tuple(sorted(set(named) | (set(module.settings.rgb_follow_targets())
                                         if running else set())))
    log.info("_follow_with_effect: %s %s on %s, targets %s", mode.value, name,
             named, targets)
    for ref in named:
        module.settings.set_light_effect(ref, effect)
    module.settings.set_rgb_follow(
        mode, prefs.openrgb_host, prefs.openrgb_port,
        EFFECT_SOURCE, module.settings.rgb_follow_mapping(), targets,
        colors=module.settings.rgb_follow_colors())
    module.resume_following()
    module.publish(RgbFollowChanged(mode=mode))
    module.publish(RgbLightsChanged())
    what = "motherboard light(s)" if mode is RgbFollowMode.BOARD else "stick(s)"
    message = f"{name} on {len(named)} {what}"
    if previous is not RgbFollowMode.OFF and not running:
        message += f" -- {previous.value} following turned off"
    return _lights(module, message=message)


def _board_refusal(board: dict[str, RgbLight], named: tuple[str, ...]) -> str:
    """Why *named* cannot run an effect; "" when they can."""
    log.debug("_board_refusal: %s of %s", named, sorted(board))
    if not board:
        return "no motherboard lights listed -- press Find lights first"
    if unknown := sorted(set(named) - set(board)):
        return (f"no motherboard light {', '.join(unknown)} -- found "
                f"{', '.join(sorted(board))}")
    if dark := [board[ref].name for ref in named if not board[ref].led_count]:
        return f"give {dark[0]} its number of LEDs first"
    return ""


@dataclass(frozen=True, slots=True)
class SetAuraLeds(ModuleCommand[RgbLightsResult, "RgbModule"]):
    """How many LEDs an ASUS Aura ARGB header drives -- the board cannot
    say, its header's data line runs one way.

    Saved, then the board is read again -- its two read requests, no
    colours -- so every UI lists the new count.  A header the last Find did
    not list is refused before anything changes.
    """

    MODULE: ClassVar[str] = _KEY
    header: int
    leds: int

    def run(self, module: RgbModule) -> RgbLightsResult:
        log.info("SetAuraLeds: header %d -> %d LED(s)", self.header, self.leds)
        if not 0 <= self.leds <= MAX_ARGB_LEDS:
            log.warning("SetAuraLeds: refused -- %d LEDs", self.leds)
            return _lights(module, message=f"an ARGB header drives 0-"
                           f"{MAX_ARGB_LEDS} LEDs, not {self.leds}", ok=False)
        ref = AURA_HEADER_REF.format(self.header)
        if ref not in {light.ref for light in _lights(module).lights
                       if light.kind is LightKind.BOARD}:
            log.warning("SetAuraLeds: refused -- no %s listed", ref)
            return _lights(module, ok=False, message=(
                f"no ARGB header {self.header} -- press Find lights to list "
                "the board's headers"))
        module.settings.set_aura_leds(self.header, self.leds)
        _refollow_board(module)
        boards = _find_boards(module)
        module.publish(RgbLightsChanged())
        message = f"ARGB header {self.header}: {self.leds} LED(s)"
        if boards.error:
            message += f" -- saved, but {boards.error}"
        return _lights(module, message=message, ok=not boards.error)


@dataclass(frozen=True, slots=True)
class BlinkAuraHeader(ModuleCommand[RgbLightsResult, "RgbModule"]):
    """Blink an ASUS Aura ARGB header for two seconds, so the user can see
    which header the fan or strip is on -- before its LED count is known.

    Nothing is saved: the board goes back to its own effect afterwards, or,
    while it is followed, to what the follower sends next.
    """

    MODULE: ClassVar[str] = _KEY
    header: int

    def run(self, module: RgbModule) -> RgbLightsResult:
        ref = AURA_HEADER_REF.format(self.header)
        log.info("BlinkAuraHeader: %s", ref)
        try:
            device = module.mirror.blink(ref)
        except ValueError:
            log.warning("BlinkAuraHeader: refused -- no %s on the board", ref)
            return _lights(module, ok=False,
                           message=f"the motherboard has no ARGB header "
                                   f"{self.header}")
        except OSError as e:
            log.warning("BlinkAuraHeader: %s could not be blinked -- %s", ref, e)
            return _lights(module, ok=False, message=f"cannot blink: {e}")
        return _lights(module, message=f"{device.name} blinked")


def _rgb_follow_result(module: RgbModule, message: str) -> RgbFollowResult:
    """The follow setting and the follower's live status, as one Result."""
    settings, status = module.settings.prefs, module.mirror.status
    mode = module.settings.rgb_follow_mode()
    log.debug("_rgb_follow_result: %s connected=%s", mode.value,
              status.connected)
    return RgbFollowResult(
        ok=True, mode=mode,
        host=settings.openrgb_host, port=settings.openrgb_port,
        source=settings.rgb_follow_source,
        mapping=module.settings.rgb_follow_mapping(),
        targets=module.settings.rgb_follow_targets(),
        colors=module.settings.rgb_follow_colors(),
        connected=status.connected, devices=status.devices,
        lead=status.lead, error=status.error, message=message,
    )


def _rgb_follow_label(mode: RgbFollowMode, host: str, port: int,
                      source: str = "") -> str:
    """What follows which device, in words -- *source* empty is the cooler."""
    log.debug("_rgb_follow_label: %s source=%r", mode.value, source)
    lead = f"device {source}" if source else "the cooler"
    match mode:
        case RgbFollowMode.OPENRGB:
            return f"OpenRGB at {host}:{port} follows {lead}"
        case RgbFollowMode.RAM:
            return f"Corsair RAM follows {lead}"
        case RgbFollowMode.BOARD:
            return f"The motherboard's RGB follows {lead}"
    return "Nothing follows -- TRCC sends no colours to other lights"


@dataclass(frozen=True, slots=True)
class SetRgbFollow(ModuleCommand[RgbFollowResult, "RgbModule"]):
    """Pick what follows the LED cooler's colours (#160): one at a time.

    TRCC leads.  ``OPENRGB``: TRCC connects to the OpenRGB SDK server at
    host:port as a client and sends the cooler's colours to every OpenRGB
    device, in OpenRGB's direct mode.  ``RAM``: TRCC sends them to Corsair RGB
    memory over the SMBus itself, with no OpenRGB running.  ``BOARD``: to the
    motherboard's own RGB -- its LEDs and ARGB headers -- directly; stopping
    hands each header back to the effect it ran, unsaved.  ``OFF`` stops.
    An empty host or a port of 0 keeps the one saved.

    ``source`` is the device that leads: empty for the first LED cooler, an
    LCD's key to follow its picture, mapped by ``mapping``, each region
    becoming one colour by ``colors``.  ``targets`` are the lights that
    follow (refs; empty for every one).  None keeps each saved.
    """

    MODULE: ClassVar[str] = _KEY
    mode: RgbFollowMode
    host: str = ""
    port: int = 0
    source: str | None = None
    mapping: FollowMapping | None = None
    targets: tuple[str, ...] | None = None
    colors: FollowColors | None = None

    def run(self, module: RgbModule) -> RgbFollowResult:
        current = module.settings.prefs
        host = self.host or current.openrgb_host
        port = self.port or current.openrgb_port
        source = (current.rgb_follow_source if self.source is None
                  else self.source)
        mapping = (module.settings.rgb_follow_mapping() if self.mapping is None
                   else self.mapping)
        targets = (module.settings.rgb_follow_targets() if self.targets is None
                   else self.targets)
        colors = (module.settings.rgb_follow_colors() if self.colors is None
                  else self.colors)
        log.info("SetRgbFollow: %s %s:%d source=%r %s %s targets=%s",
                 self.mode.value, host, port, source, mapping.value,
                 colors.value, list(targets) or "all")
        try:
            module.settings.set_rgb_follow(self.mode, host, port, source,
                                        mapping, targets, colors=colors)
        except ValueError as e:
            log.warning("SetRgbFollow: refused — %s", e)
            return RgbFollowResult(ok=False, message=str(e))
        module.configure(self.mode, host, port, source, mapping,
                                 targets, colors=colors,
                                 effects=module.settings.follow_effects())
        module.publish(RgbFollowChanged(mode=self.mode))
        return _rgb_follow_result(
            module, _rgb_follow_label(self.mode, host, port, source))


@dataclass(frozen=True, slots=True)
class RgbFollow(ModuleQuery[RgbFollowResult, "RgbModule"]):
    """What follows the cooler, and what it is doing (#160)."""

    MODULE: ClassVar[str] = _KEY

    def run(self, module: RgbModule) -> RgbFollowResult:
        log.debug("RgbFollow: query")
        current = module.settings.prefs
        return _rgb_follow_result(module, _rgb_follow_label(
            module.settings.rgb_follow_mode(), current.openrgb_host,
            current.openrgb_port, current.rgb_follow_source))


def _ram_lighting_result(status: RamAccessStatus, *,
                         ok: bool = True) -> RamLightingResult:
    """A ``RamAccessStatus`` as the Result every UI reads."""
    log.debug("_ram_lighting_result: %s ok=%s", status, ok)
    return RamLightingResult(ok=ok, state=status.state, message=status.message,
                             command=status.command)


@dataclass(frozen=True, slots=True)
class RamLighting(ModuleQuery[RamLightingResult, "RgbModule"]):
    """Whether TRCC may reach RGB memory's bus -- read from files, no bus."""

    MODULE: ClassVar[str] = _KEY

    def run(self, module: RgbModule) -> RamLightingResult:
        log.debug("RamLighting: query")
        return _ram_lighting_result(module.memory_access().status())


@dataclass(frozen=True, slots=True)
class SetRamLighting(ModuleCommand[RamLightingResult, "RgbModule"]):
    """Switch the opt-in RAM-lighting grant on or off.

    The grant lets the user at this seat open the chipset SMBus, where RGB
    memory's lighting controller answers -- beside the memory's own settings
    chips, which is why it is never on by default and asks for a password
    every time.  ``ok`` says whether the asked-for state was reached: a
    closed password prompt changes nothing and answers ``ok=False``.
    """

    MODULE: ClassVar[str] = _KEY
    #: Answers when the person types the password or closes the prompt.
    WAITS_ON_USER: ClassVar[bool] = True
    enabled: bool

    def run(self, module: RgbModule) -> RamLightingResult:
        log.info("SetRamLighting: %s", "on" if self.enabled else "off")
        access = module.memory_access()
        status = access.enable() if self.enabled else access.disable()
        reached = (status.state in (RamAccessState.ON, RamAccessState.ELSEWHERE)
                   if self.enabled else
                   status.state not in (RamAccessState.ON,
                                        RamAccessState.NOT_APPLIED))
        if not reached:
            log.warning("SetRamLighting: still %s -- %s", status.state.value,
                        status.message)
        module.publish(RamLightingChanged(state=status.state))
        return _ram_lighting_result(status, ok=reached)
