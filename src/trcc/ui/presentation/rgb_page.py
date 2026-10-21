"""The RGB page -- what it lists, what the user has picked, and the Command
Apply sends.  No toolkit: both windows draw this one model, so they offer the
same choices, refuse the same mistakes and send the same Commands.

The App's answers (``RgbLights``, ``RgbFollow``, ``ListDevices``) fill it;
``load`` always starts again from them, so a change another UI made shows
here the next time the page hears of it.  What the user picks lives here
until Apply -- view state, never a copy of the App's.

Apply and Find hand back a PLAN -- plain values -- and each window builds the
Command from it, as every UI turns its own input into Commands.  The choices
and the refusals are made once, here.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ...core.models import (
    EFFECT_SOURCE,
    EFFECT_TRAITS,
    EffectDirection,
    EffectSpeed,
    EffectTraits,
    FollowColors,
    FollowMapping,
    Kind,
    LightKind,
    RamAccessState,
    RamEffect,
    RamEffectSettings,
    RgbFollowMode,
    RgbLight,
    aura_header,
)
from ...core.ram_effects import effect_problem
from ...core.results import DeviceEntry, Result, RgbFollowResult, RgbLightsResult
from ...services.rgb_mirror import FRAME_ROWS
from .openrgb_address import format_openrgb_address, parse_openrgb_address

log = logging.getLogger(__name__)

Rgb = tuple[int, int, int]


class RgbSource(str, Enum):
    """What the page sends to the lights."""
    LEAVE = "leave"
    EFFECT = "effect"
    FOLLOW = "follow"


SOURCE_LABELS: dict[RgbSource, str] = {
    RgbSource.LEAVE: "Leave alone",
    RgbSource.EFFECT: "Built-in effect",
    RgbSource.FOLLOW: "Follow a device",
}
EFFECT_LABELS: dict[RamEffect, str] = {
    RamEffect.STATIC: "Static",
    RamEffect.COLOR_SHIFT: "Colour Shift",
    RamEffect.COLOR_PULSE: "Colour Pulse",
    RamEffect.RAINBOW: "Rainbow",
    RamEffect.RAINBOW_WAVE: "Rainbow Wave",
    RamEffect.COLOR_WAVE: "Colour Wave",
    RamEffect.VISOR: "Visor",
    RamEffect.RAIN: "Rain",
    RamEffect.MARQUEE: "Marquee",
    RamEffect.SEQUENTIAL: "Sequential",
}
SPEED_LABELS: dict[EffectSpeed, str] = {
    EffectSpeed.SLOW: "Slow", EffectSpeed.MEDIUM: "Medium",
    EffectSpeed.FAST: "Fast",
}
DIRECTION_LABELS: dict[EffectDirection, str] = {
    d: d.value.capitalize() for d in EffectDirection}
MAPPING_LABELS: dict[FollowMapping, str] = {
    FollowMapping.HALVES: "Split left to right",
    FollowMapping.SINGLE: "Same on every light",
}
COLORS_LABELS: dict[FollowColors, str] = {
    FollowColors.VIVID: "Vivid",
    FollowColors.SMOOTH: "Smooth",
}
#: The colours an effect starts with before the user picks any.
DEFAULT_COLORS: tuple[Rgb, Rgb] = ((255, 32, 64), (32, 128, 255))
#: The follow choice for "whichever LED cooler sends colours first".
FIRST_COOLER = ""
BAD_ADDRESS = "OpenRGB's address is host:port, e.g. 127.0.0.1:6742"
#: Under the motherboard's lights: what the list means, for anyone.
BOARD_NOTE = ("Tick a header and give it the number of LEDs on its fan or "
              "strip.  A board listed as found has its own software; TRCC "
              "leaves it alone.")
#: Beside an ARGB header's LED count: what to enter, for anyone.
LEDS_TIP = ("How many LEDs are on the fan or strip plugged into this header "
            "-- it is on the box or spec sheet.  The board cannot tell.")
#: Beside an ARGB header's Blink button: what it does, for anyone.
BLINK_TIP = ("Flashes this header dim white for two seconds, so you can see "
             "which fan or strip is on it.  Nothing is saved.")
#: Under sticks listed from the settings, before this App looked for them.
REMEMBERED_NOTE = ("Found at the last search -- press Find lights to check "
                   "again.")

class RgbDevice(str, Enum):
    """A device the page lists on the left -- the panel shows only its
    lights, and Apply changes only it."""
    MEMORY = "memory"
    MOTHERBOARD = "motherboard"
    OPENRGB = "openrgb"


DEVICE_LABELS: dict[RgbDevice, str] = {
    RgbDevice.MEMORY: "Memory", RgbDevice.MOTHERBOARD: "Motherboard",
    RgbDevice.OPENRGB: "OpenRGB"}
#: The kinds of light each device lists; the first is the one it drives.
DEVICE_KINDS: dict[RgbDevice, tuple[LightKind, ...]] = {
    RgbDevice.MEMORY: (LightKind.RAM,),
    RgbDevice.MOTHERBOARD: (LightKind.BOARD, LightKind.FOUND),
    RgbDevice.OPENRGB: (LightKind.OPENRGB,)}
#: The follower that drives each device's lights.
DEVICE_FOLLOWER: dict[RgbDevice, RgbFollowMode] = {
    RgbDevice.MEMORY: RgbFollowMode.RAM,
    RgbDevice.MOTHERBOARD: RgbFollowMode.BOARD,
    RgbDevice.OPENRGB: RgbFollowMode.OPENRGB}
#: The devices a built-in effect runs on: a stick saves it, a motherboard
#: light runs it as TRCC draws it.  OpenRGB's lights only follow.
EFFECT_DEVICES = frozenset({RgbDevice.MEMORY, RgbDevice.MOTHERBOARD})

HINTS: dict[tuple[RgbDevice, RgbSource], str] = {
    (RgbDevice.MEMORY, RgbSource.LEAVE): (
        "TRCC sends nothing to the sticks.  Each keeps the effect saved on it."),
    (RgbDevice.MEMORY, RgbSource.EFFECT): (
        "Saved on the stick itself -- it keeps running after TRCC closes and "
        "after a reboot.  Written once when you press Apply."),
    # Seen on Corsair Vengeance DDR5, 2026-10-10: a stick stopped following
    # keeps its last colours -- it does not go back to its saved effect.
    (RgbDevice.MEMORY, RgbSource.FOLLOW): (
        "Live only -- nothing is saved on the stick.  When following stops, "
        "each stick keeps the last colours it was sent."),
    (RgbDevice.MOTHERBOARD, RgbSource.LEAVE): (
        "TRCC sends nothing to the board.  Each header shows the board's own "
        "effect."),
    (RgbDevice.MOTHERBOARD, RgbSource.EFFECT): (
        "TRCC runs the effect while it runs -- nothing is saved on the board, "
        "which shows its own effect before TRCC starts."),
    (RgbDevice.MOTHERBOARD, RgbSource.FOLLOW): (
        "Live only.  When following stops, each header goes back to the "
        "board's own effect."),
    (RgbDevice.OPENRGB, RgbSource.LEAVE): (
        "TRCC sends nothing to OpenRGB's devices.  OpenRGB keeps what it "
        "shows."),
    (RgbDevice.OPENRGB, RgbSource.EFFECT): "",
    (RgbDevice.OPENRGB, RgbSource.FOLLOW): (
        "TRCC sends the colours to OpenRGB, which shows them in its direct "
        "mode."),
}


@dataclass(frozen=True, slots=True)
class FindLights:
    """Plan: look for the lights, OpenRGB at *host*:*port* (``ScanRgbLights``)."""
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class LeaveLights:
    """Plan: TRCC stops sending to the lights (``SetRgbFollow`` off)."""


@dataclass(frozen=True, slots=True)
class SaveEffect:
    """Plan: save *settings* on the sticks *refs*, every one when empty
    (``SetRamEffect``)."""
    refs: tuple[str, ...]
    settings: RamEffectSettings


@dataclass(frozen=True, slots=True)
class FollowDevice:
    """Plan: the lights *targets* (every one when empty) of *mode* follow
    *source* (``SetRgbFollow``)."""
    mode: RgbFollowMode
    host: str
    port: int
    source: str
    mapping: FollowMapping
    colors: FollowColors
    targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunBoardEffect:
    """Plan: the motherboard lights *refs* (every one when empty) run
    *settings*, drawn by TRCC (``SetAuraEffect``)."""
    refs: tuple[str, ...]
    settings: RamEffectSettings


ApplyPlan = LeaveLights | SaveEffect | RunBoardEffect | FollowDevice



#: What a light TRCC never set shows, on Linux -- his list, 2026-10-21.
NOT_KNOWN_NOTE = ("Not known: its factory default, or what OpenRGB or the "
                  "motherboard's own lighting control last set.")


def strip_legend(strips: Sequence[tuple[str, RamEffectSettings | None]]
                 ) -> str:
    """The effect preview's legend: one line per lettered strip, the effect
    it runs.  Letters only: the lights' names are on their cards, in the same
    order (his call, 2026-10-21)."""
    log.debug("strip_legend: %d", len(strips))
    legend = "\n".join(
        f"{chr(ord('A') + i)}: "
        f"{EFFECT_LABELS[settings.effect] if settings else 'not known'}"
        for i, (_name, settings) in enumerate(strips))
    # Said once, under the letters -- on every line it was noise (his call).
    if any(settings is None for _name, settings in strips):
        legend += "\n\n" + NOT_KNOWN_NOTE
    return legend


@dataclass(frozen=True, slots=True)
class LightRow:
    """One light as the list shows it; ``checkable`` -- whether it can take
    part in following or effects yet.  ``header`` is set for an ASUS Aura
    ARGB header, whose LED count (``leds``) the user gives."""
    ref: str
    title: str
    detail: str
    kind: LightKind
    checked: bool
    checkable: bool = True
    header: int | None = None
    leds: int = 0


@dataclass(frozen=True, slots=True)
class HeaderLeds:
    """Plan: ARGB *header* drives *leds* LEDs (``SetAuraLeds``)."""
    header: int
    leds: int


#: Which follower drives each kind of light that can follow -- one kind at
#: a time.  These are the kinds a light can be ticked for.
FOLLOWS: dict[LightKind, RgbFollowMode] = {
    LightKind.RAM: RgbFollowMode.RAM,
    LightKind.OPENRGB: RgbFollowMode.OPENRGB,
    LightKind.BOARD: RgbFollowMode.BOARD,
}
TICKABLE = frozenset(FOLLOWS)
#: Ticked when nothing says otherwise.  A motherboard's headers start
#: unticked: which one has a fan on it is the user's to say.
TICKED_AT_FIRST = frozenset({LightKind.RAM, LightKind.OPENRGB})
#: Who is following, in the status line.
_FOLLOWER_NAMES = {RgbFollowMode.OPENRGB: "OpenRGB", RgbFollowMode.RAM: "The RAM",
                   RgbFollowMode.BOARD: "The motherboard"}


@dataclass(frozen=True, slots=True)
class FollowChoice:
    """One entry of the "Follow" picker: a device key and its label."""
    key: str
    label: str
    is_lcd: bool = False


def _remembered(lights: Sequence[RgbLight], kind: LightKind) -> bool:
    """Whether *kind*'s lights come from the last Find's record."""
    remembered = any(light.remembered for light in lights if light.kind is kind)
    log.debug("_remembered: %s -> %s", kind.value, remembered)
    return remembered


def _detail(light: RgbLight) -> str:
    """The grey line under a light's name."""
    log.debug("_detail: %s %s", light.kind.value, light.ref)
    if light.kind is LightKind.OPENRGB:
        return f"via OpenRGB · {light.led_count} LEDs"
    if light.kind is LightKind.FOUND:
        return "found -- TRCC does not drive it; another app may"
    if light.kind is LightKind.BOARD:
        count = light.led_count
        effect = f" · {EFFECT_LABELS[light.effect.effect]}" if light.effect else ""
        return (f"direct · {count} LED{'' if count == 1 else 's'}{effect}" if count
                else "direct · LED count not set")
    # The name already says where the stick sits (``named_by_ref``).
    saved = (f" · {EFFECT_LABELS[light.effect.effect]} saved"
             if light.effect else "")
    return f"direct · {light.led_count} LEDs{saved}"


class EffectPicks:
    """One built-in effect as the user sets it, before Apply."""

    def __init__(self) -> None:
        log.debug("EffectPicks.__init__")
        self.effect = RamEffect.STATIC
        self.speed = EffectSpeed.MEDIUM
        self.direction: EffectDirection | None = None
        self.colors: list[Rgb] = list(DEFAULT_COLORS)
        self.random_colors = False
        self.brightness = 255

    def take(self, saved: RamEffectSettings) -> None:
        """Start from an effect saved on a stick."""
        log.debug("EffectPicks.take: %s", saved)
        self.effect, self.speed = saved.effect, saved.speed
        self.direction = saved.direction
        self.random_colors, self.brightness = (saved.random_colors,
                                               saved.brightness)
        # A slot the saved effect did not use keeps its own default.
        self.colors = [*saved.colors[:len(DEFAULT_COLORS)],
                       *DEFAULT_COLORS[len(saved.colors):]]

    @property
    def traits(self) -> EffectTraits:
        log.debug("EffectPicks.traits: %s", self.effect.value)
        return EFFECT_TRAITS[self.effect]

    def set_effect(self, effect: RamEffect) -> None:
        """Pick an effect; a direction it cannot take falls back to its own."""
        log.info("EffectPicks.set_effect: %s -> %s", self.effect.value,
                 effect.value)
        self.effect = effect
        if self.direction not in EFFECT_TRAITS[effect].directions:
            self.direction = None

    @property
    def shown_direction(self) -> EffectDirection | None:
        """The direction the buttons show: the picked one, else the default."""
        log.debug("EffectPicks.shown_direction: picked %s", self.direction)
        directions = self.traits.directions
        return self.direction or (directions[0] if directions else None)

    def set_color(self, index: int, color: Rgb) -> None:
        log.info("EffectPicks.set_color: %d %s", index, color)
        self.colors[index] = color

    def settings(self) -> RamEffectSettings:
        """The picks as the effect takes them: only its own colours, direction
        and random choice."""
        log.debug("EffectPicks.settings: %s", self.effect.value)
        traits = self.traits
        return RamEffectSettings(
            effect=self.effect, speed=self.speed,
            direction=self.direction if traits.directions else None,
            colors=tuple(self.colors[:traits.colors]),
            random_colors=traits.random and self.random_colors,
            brightness=self.brightness)


class FollowPicks:
    """What a device's lights follow, as the user sets it, before Apply --
    the device that leads, how its picture is spread, and how a region
    becomes one colour."""

    def __init__(self) -> None:
        log.debug("FollowPicks.__init__")
        self.source = FIRST_COOLER
        self.mapping = FollowMapping.HALVES
        self.colors = FollowColors.VIVID
        self.devices: tuple[DeviceEntry, ...] = ()

    def take(self, follow: RgbFollowResult, devices: Sequence[DeviceEntry]) -> None:
        """Start from what the App follows -- TRCC's own effect clock is no
        device to follow, so it starts on the first cooler."""
        log.debug("FollowPicks.take: %r %s", follow.source, follow.mapping.value)
        self.source = FIRST_COOLER if follow.source == EFFECT_SOURCE else follow.source
        self.mapping, self.colors = follow.mapping, follow.colors
        self.devices = tuple(devices)

    def choices(self) -> tuple[FollowChoice, ...]:
        """The first LED cooler, every attached device, and a saved one that
        is not attached now -- so the picker can show what is set."""
        choices = [FollowChoice(FIRST_COOLER, "The first LED cooler")]
        choices += [FollowChoice(d.key, f"{d.product or d.key} "
                                 f"({'LCD' if d.kind == Kind.LCD.value else 'LED cooler'})",
                                 d.kind == Kind.LCD.value)
                    for d in self.devices]
        if self.source not in {c.key for c in choices}:
            choices.append(FollowChoice(self.source,
                                        f"{self.source} (not connected)", True))
        log.debug("FollowPicks.choices: %d", len(choices))
        return tuple(choices)

    @property
    def mapping_applies(self) -> bool:
        """Mapping is how a PICTURE is spread -- an LCD's, not a cooler's."""
        log.debug("FollowPicks.mapping_applies: %r", self.source)
        return any(c.key == self.source and c.is_lcd for c in self.choices())

    def columns(self, lights: int) -> int:
        """Columns the picture is cut into: one per light (light *i* takes
        column *i*), or one for all -- as ``RgbMirrorService.on_frame``."""
        columns = 1 if self.mapping is FollowMapping.SINGLE else max(1, lights)
        log.debug("FollowPicks.columns: %d light(s) -> %d", lights, columns)
        return columns


class RgbPage:
    """The page's state between the App's answers and the user's Apply."""

    def __init__(self) -> None:
        log.debug("RgbPage.__init__")
        self._lights: tuple[RgbLight, ...] = ()
        self.scanned = False
        self.openrgb_error = ""
        self.ram_access = RamAccessState.UNSUPPORTED
        self._follow = RgbFollowResult()
        self._checked: set[str] = set()
        self.device = RgbDevice.MEMORY
        self._picked = False            # the user chose the device on screen
        self.source = RgbSource.LEAVE
        self.effect_picks = EffectPicks()
        self.follow_picks = FollowPicks()
        self.address = format_openrgb_address("127.0.0.1", 6742)
        self.message = ""

    # ── From the App ─────────────────────────────────────────────────

    def load(self, lights: RgbLightsResult, follow: RgbFollowResult,
             devices: Sequence[DeviceEntry]) -> None:
        """Start again from the App's answers; the user's picks are dropped,
        the device on screen is kept."""
        log.info("RgbPage.load: %d light(s) scanned=%s access=%s follow=%s "
                 "source=%r targets=%s device=%s", len(lights.lights),
                 lights.scanned, lights.ram_access.value, follow.mode.value,
                 follow.source, list(follow.targets) or "all", self.device.value)
        self._lights = lights.lights
        self.scanned = lights.scanned
        self.openrgb_error = lights.openrgb_error
        self.ram_access = lights.ram_access
        self._follow = follow
        following = next((k for k, m in FOLLOWS.items() if m is follow.mode),
                         None)
        refs = {light.ref for light in self._lights if light.kind in TICKABLE}
        # The lights that follow are ticked as the App has them; every other
        # kind starts on its own default.
        self._checked = {light.ref for light in self._lights
                         if light.kind in TICKED_AT_FIRST and light.kind is not following}
        if following is not None:
            self._checked |= (set(follow.targets) & refs if follow.targets else
                              {light.ref for light in self._lights
                               if light.kind is following})
        if follow.host:
            self.address = format_openrgb_address(follow.host, follow.port)
        self.follow_picks.take(follow, devices)
        # First the device TRCC drives, else the first with lights; once the
        # user picks one, that one.
        self._show_device(self.device if self._picked else (
            self.follower or next((d for d in RgbDevice
                                   if self.rows(DEVICE_KINDS[d][0])), RgbDevice.MEMORY)))

    def follow_now(self, follow: RgbFollowResult) -> None:
        """What following is doing now -- the status alone; picks are kept."""
        log.debug("RgbPage.follow_now: connected=%s", follow.connected)
        self._follow = follow

    def answered(self, result: Result) -> None:
        """Show what the last Command said."""
        log.info("RgbPage.answered: ok=%s %s", result.ok, result.message)
        self.message = result.message

    # ── The devices ──────────────────────────────────────────────────

    @property
    def follower(self) -> RgbDevice | None:
        """The device whose lights TRCC sends to now -- following, or the
        motherboard running TRCC's effect -- None while nothing does."""
        device = next((d for d, m in DEVICE_FOLLOWER.items()
                       if m is self._follow.mode), None)
        log.debug("RgbPage.follower: %s", device)
        return device

    def set_device(self, device: RgbDevice) -> None:
        """The user picked *device*: shown from now on, reloads included."""
        log.info("RgbPage.set_device: %s -> %s", self.device.value, device.value)
        self._picked = True
        self._show_device(device)

    def _show_device(self, device: RgbDevice) -> None:
        """Show *device*: its source as the App runs it, its effect picks
        from what its lights run."""
        log.debug("RgbPage._show_device: %s", device.value)
        self.device = device
        lights = [light for light in self._lights if light.kind in DEVICE_KINDS[device]]
        driven = self.follower is device
        if driven and device is RgbDevice.MOTHERBOARD:
            lights = [light for light in lights if light.ref in self._checked]
        saved = next((light.effect for light in lights if light.effect is not None), None)
        if saved is not None:
            self.effect_picks.take(saved)
        board_effect = self._follow.source == EFFECT_SOURCE
        self.source = (
            (RgbSource.EFFECT if board_effect else RgbSource.FOLLOW) if driven
            else RgbSource.EFFECT if saved is not None and device is RgbDevice.MEMORY
            else RgbSource.LEAVE)

    # ── The lights ───────────────────────────────────────────────────

    def rows(self, kind: LightKind) -> tuple[LightRow, ...]:
        """The lights of *kind*, with whether each is ticked."""
        rows = tuple(LightRow(light.ref, light.name, _detail(light),
                              light.kind, light.ref in self._checked,
                              light.kind in TICKABLE,
                              aura_header(light.ref)
                              if light.kind is LightKind.BOARD else None,
                              light.led_count)
                     for light in self._lights if light.kind is kind)
        log.debug("RgbPage.rows: %s -> %d", kind.value, len(rows))
        return rows

    def set_checked(self, ref: str, checked: bool) -> None:
        log.info("RgbPage.set_checked: %s %s", ref, checked)
        if checked:
            self._checked.add(ref)
        else:
            self._checked.discard(ref)

    @property
    def ram_reachable(self) -> bool:
        """Whether the RAM can be searched -- else the access card shows."""
        log.debug("RgbPage.ram_reachable: %s", self.ram_access.value)
        return self.ram_access in (RamAccessState.ON, RamAccessState.ELSEWHERE)

    @property
    def note(self) -> str:
        """The line under the device's lights: where the list came from, or
        why it is empty."""
        kind = DEVICE_KINDS[self.device][0]
        if self.device is RgbDevice.OPENRGB and self.openrgb_error:
            note = f"not reachable -- {self.openrgb_error}"
        elif self.rows(kind):
            note = REMEMBERED_NOTE if _remembered(self._lights, kind) else ""
        elif not self.scanned:
            note = "Not searched yet -- press Find lights."
        else:
            note = f"No {DEVICE_LABELS[self.device].lower()} lights found."
        log.debug("RgbPage.note: %s %s", self.device.value, note)
        return note

    def find(self) -> FindLights | None:
        """"Find lights": at the address in the box, None if it is not one."""
        address = parse_openrgb_address(self.address)
        log.info("RgbPage.find: %r -> %s", self.address, address)
        if address is None:
            self.message = BAD_ADDRESS
            return None
        return FindLights(*address)

    # ── The source ───────────────────────────────────────────────────

    def source_enabled(self, source: RgbSource) -> bool:
        """An effect runs on memory and the board; following needs a light
        of the device, or that it already follows."""
        has_lights = bool(self.rows(DEVICE_KINDS[self.device][0]))
        enabled = {
            RgbSource.LEAVE: True,
            RgbSource.EFFECT: self.device in EFFECT_DEVICES and has_lights,
            RgbSource.FOLLOW: has_lights or self.follower is self.device,
        }[source]
        log.debug("RgbPage.source_enabled: %s %s -> %s", self.device.value,
                  source.value, enabled)
        return enabled

    def set_source(self, source: RgbSource) -> None:
        log.info("RgbPage.set_source: %s -> %s", self.source.value, source.value)
        self.source = source

    @property
    def hint(self) -> str:
        """What the source means for this device -- and, when sending to it
        takes the one follower from another device, which."""
        hint = HINTS[self.device, self.source]
        other = self.follower
        takes_follower = (self.source is RgbSource.FOLLOW
                          or (self.source is RgbSource.EFFECT
                              and self.device is RgbDevice.MOTHERBOARD))
        if takes_follower and other is not None and other is not self.device:
            hint += (f"  TRCC sends to one device at a time: "
                     f"{DEVICE_LABELS[other]} stops when you apply this.")
        log.debug("RgbPage.hint: %s %s", self.device.value, self.source.value)
        return hint

    @property
    def follow_status(self) -> str:
        """What following is doing now, as the App last said."""
        follow = self._follow
        who = _FOLLOWER_NAMES.get(follow.mode, "")
        if follow.mode is RgbFollowMode.OFF:
            status = "Not following."
        elif follow.error:
            status = f"{who} is not reachable -- {follow.error}"
        elif follow.connected:
            status = f"Following on: {', '.join(follow.devices) or 'no lights'}"
        else:
            status = f"{who} is waiting for colours."
        log.debug("RgbPage.follow_status: %s", status)
        return status

    def effect_strips(self) -> tuple[tuple[str, RamEffectSettings | None], ...]:
        """The effect preview's strips: each light's name and what it runs.

        Built-in effect: the effect being set, on the device's ticked lights.
        Otherwise what TRCC last gave each of them -- None where it never
        gave one, so the light runs its own or another program's.
        """
        kind = DEVICE_KINDS[self.device][0]
        if self.source is RgbSource.EFFECT:
            settings = self.effect_picks.settings()
            strips = tuple((row.title, settings) for row in self.rows(kind)
                           if row.checked)
        else:
            strips = tuple((light.name, light.effect) for light in self._lights
                           if light.kind is kind)
        log.debug("RgbPage.effect_strips: %d", len(strips))
        return strips

    #: LEDs per strip in the follow preview: the rows the App samples a
    #: frame into, one per LED of a 10-LED stick.
    PREVIEW_ROWS = FRAME_ROWS

    @property
    def preview_lights(self) -> tuple[str, ...]:
        """The lights the follow preview draws a strip for, in the order the
        App sends them -- the device's ticked ones."""
        names = tuple(row.title for row in self.rows(DEVICE_KINDS[self.device][0])
                      if row.checked)
        log.debug("RgbPage.preview_lights: %s", names)
        return names

    def _targets(self, kind: LightKind) -> tuple[str, ...]:
        """The ticked lights of *kind*; empty when every one is ticked, so a
        light found later follows too."""
        log.debug("RgbPage._targets: %s", kind.value)
        refs = tuple(row.ref for row in self.rows(kind) if row.checked)
        return () if len(refs) == len(self.rows(kind)) else refs

    # ── Apply ────────────────────────────────────────────────────────

    @property
    def problem(self) -> str | None:
        """Why Apply would be refused -- shown, and Apply stays off."""
        kind = DEVICE_KINDS[self.device][0]
        ticked = [row for row in self.rows(kind) if row.checked]
        silent = [row.title for row in ticked
                  if kind is LightKind.BOARD and not row.leds]
        problem: str | None = None
        if self.source is RgbSource.LEAVE:
            if self.follower is not self.device:
                problem = "TRCC is not sending to these lights."
        elif not ticked:
            problem = ("Tick the lights that should follow."
                       if self.source is RgbSource.FOLLOW
                       else "Tick the lights the effect runs on.")
        elif silent:
            problem = f"Give {silent[0]} its number of LEDs first."
        elif self.source is RgbSource.EFFECT:
            problem = effect_problem(self.effect_picks.settings())
        elif (self.device is RgbDevice.OPENRGB
              and parse_openrgb_address(self.address) is None):
            problem = BAD_ADDRESS
        log.debug("RgbPage.problem: %s %s -> %s", self.device.value,
                  self.source.value, problem)
        return problem

    def apply(self) -> ApplyPlan | None:
        """What Apply does to this device, or None while ``problem`` says
        why not."""
        if (problem := self.problem) is not None:
            log.info("RgbPage.apply: nothing -- %s", problem)
            return None
        kind = DEVICE_KINDS[self.device][0]
        settings = self.effect_picks.settings()
        address = parse_openrgb_address(self.address) or ("127.0.0.1", 6742)
        picks = self.follow_picks
        plan: ApplyPlan
        if self.source is RgbSource.LEAVE:
            plan = LeaveLights()
        elif self.source is RgbSource.EFFECT:
            plan = (SaveEffect(self._targets(kind), settings)
                    if self.device is RgbDevice.MEMORY
                    else RunBoardEffect(self._targets(kind), settings))
        else:
            plan = FollowDevice(DEVICE_FOLLOWER[self.device], *address, picks.source,
                                picks.mapping, picks.colors, self._targets(kind))
        log.info("RgbPage.apply: %s", plan)
        return plan
