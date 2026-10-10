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

HINTS: dict[RgbSource, str] = {
    RgbSource.LEAVE: ("TRCC sends nothing to any light.  Each stick keeps the "
                      "effect saved on it."),
    RgbSource.EFFECT: ("Saved on the stick itself -- it keeps running after "
                       "TRCC closes and after a reboot.  Written once when you "
                       "press Apply, never on a timer."),
    # Seen on Corsair Vengeance DDR5, 2026-10-10: a stick stopped following
    # keeps its last colours -- it does not go back to its saved effect.
    RgbSource.FOLLOW: ("Live only -- nothing is saved on the stick.  When "
                       "following stops, each stick keeps the last colours "
                       "it was sent; pick an effect to change them."),
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


ApplyPlan = LeaveLights | SaveEffect | FollowDevice


def strip_legend(strips: Sequence[tuple[str, RamEffectSettings | None]]
                 ) -> str:
    """The effect preview's legend: what each lettered strip is and runs."""
    log.debug("strip_legend: %d", len(strips))
    unknown = "not known -- its factory default, or set by another program"
    return "\n".join(
        f"{chr(ord('A') + i)}: {name} -- "
        f"{EFFECT_LABELS[settings.effect] if settings else unknown}"
        for i, (name, settings) in enumerate(strips))


@dataclass(frozen=True, slots=True)
class LightRow:
    """One light as the list shows it."""
    ref: str
    title: str
    detail: str
    kind: LightKind
    checked: bool


@dataclass(frozen=True, slots=True)
class FollowChoice:
    """One entry of the "Follow" picker: a device key and its label."""
    key: str
    label: str
    is_lcd: bool = False


def _detail(light: RgbLight) -> str:
    """The grey line under a light's name."""
    log.debug("_detail: %s %s", light.kind.value, light.ref)
    if light.kind is LightKind.OPENRGB:
        return f"via OpenRGB · {light.led_count} LEDs"
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


class RgbPage:
    """The page's state between the App's answers and the user's Apply."""

    def __init__(self) -> None:
        log.debug("RgbPage.__init__")
        self._lights: tuple[RgbLight, ...] = ()
        self.scanned = False
        self.openrgb_error = ""
        self.ram_access = RamAccessState.UNSUPPORTED
        self._follow = RgbFollowResult()
        self._devices: tuple[DeviceEntry, ...] = ()
        self._checked: set[str] = set()
        self.source = RgbSource.LEAVE
        self.effect_picks = EffectPicks()
        self.follow_source = FIRST_COOLER
        self.mapping = FollowMapping.HALVES
        self.colors = FollowColors.VIVID
        self.address = format_openrgb_address("127.0.0.1", 6742)
        self.message = ""

    # ── From the App ─────────────────────────────────────────────────

    def load(self, lights: RgbLightsResult, follow: RgbFollowResult,
             devices: Sequence[DeviceEntry]) -> None:
        """Start again from the App's answers; the user's picks are dropped."""
        log.info("RgbPage.load: %d light(s) scanned=%s access=%s follow=%s "
                 "source=%r targets=%s", len(lights.lights), lights.scanned,
                 lights.ram_access.value, follow.mode.value, follow.source,
                 list(follow.targets) or "all")
        self._lights = lights.lights
        self.scanned = lights.scanned
        self.openrgb_error = lights.openrgb_error
        self.ram_access = lights.ram_access
        self._follow = follow
        self._devices = tuple(devices)
        refs = {light.ref for light in self._lights}
        self._checked = (set(follow.targets) & refs if follow.targets
                         else refs)
        if follow.host:
            self.address = format_openrgb_address(follow.host, follow.port)
        self.follow_source = follow.source
        self.mapping = follow.mapping
        self.colors = follow.colors
        saved = next((light.effect for light in self._ram_lights()
                      if light.effect is not None), None)
        if saved is not None:
            self.effect_picks.take(saved)
        self.source = (RgbSource.FOLLOW if follow.mode is not RgbFollowMode.OFF
                       else RgbSource.EFFECT if saved is not None
                       else RgbSource.LEAVE)

    def follow_now(self, follow: RgbFollowResult) -> None:
        """What following is doing now -- the status alone; picks are kept."""
        log.debug("RgbPage.follow_now: connected=%s", follow.connected)
        self._follow = follow

    def answered(self, result: Result) -> None:
        """Show what the last Command said."""
        log.info("RgbPage.answered: ok=%s %s", result.ok, result.message)
        self.message = result.message

    # ── The lights ───────────────────────────────────────────────────

    def _ram_lights(self) -> tuple[RgbLight, ...]:
        log.debug("RgbPage._ram_lights")
        return tuple(light for light in self._lights
                     if light.kind is LightKind.RAM)

    def rows(self, kind: LightKind) -> tuple[LightRow, ...]:
        """The lights of *kind*, with whether each is ticked."""
        rows = tuple(LightRow(light.ref, light.name, _detail(light),
                              light.kind, light.ref in self._checked)
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
    def ram_note(self) -> str:
        """The line in place of the sticks when there are none to list."""
        if self.rows(LightKind.RAM):
            return ""
        note = ("Press Find lights to look for RGB memory." if not self.scanned
                else "No RGB memory found.")
        log.debug("RgbPage.ram_note: %s", note)
        return note

    @property
    def openrgb_note(self) -> str:
        """The OpenRGB box's status line."""
        if self.openrgb_error:
            note = f"not reachable -- {self.openrgb_error}"
        elif not self.scanned:
            note = "not searched yet -- press Find lights"
        else:
            note = f"{len(self.rows(LightKind.OPENRGB))} device(s) found"
        log.debug("RgbPage.openrgb_note: %s", note)
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
        """An effect needs a stick; following needs a light, or to be on."""
        enabled = {
            RgbSource.LEAVE: True,
            RgbSource.EFFECT: bool(self.rows(LightKind.RAM)),
            RgbSource.FOLLOW: (bool(self._lights)
                               or self._follow.mode is not RgbFollowMode.OFF),
        }[source]
        log.debug("RgbPage.source_enabled: %s -> %s", source.value, enabled)
        return enabled

    def set_source(self, source: RgbSource) -> None:
        log.info("RgbPage.set_source: %s -> %s", self.source.value, source.value)
        self.source = source

    @property
    def hint(self) -> str:
        log.debug("RgbPage.hint: %s", self.source.value)
        return HINTS[self.source]

    # ── Following ────────────────────────────────────────────────────

    def follow_choices(self) -> tuple[FollowChoice, ...]:
        """The first LED cooler, every attached device, and a saved one that
        is not attached now -- so the picker can show what is set."""
        choices = [FollowChoice(FIRST_COOLER, "The first LED cooler")]
        choices += [FollowChoice(d.key, f"{d.product or d.key} "
                                 f"({'LCD' if d.kind == Kind.LCD.value else 'LED cooler'})",
                                 d.kind == Kind.LCD.value)
                    for d in self._devices]
        if self.follow_source not in {c.key for c in choices}:
            choices.append(FollowChoice(self.follow_source,
                                        f"{self.follow_source} (not connected)",
                                        True))
        log.debug("RgbPage.follow_choices: %d", len(choices))
        return tuple(choices)

    @property
    def mapping_applies(self) -> bool:
        """Mapping is how a PICTURE is spread -- an LCD's, not a cooler's."""
        log.debug("RgbPage.mapping_applies: %r", self.follow_source)
        return any(c.key == self.follow_source and c.is_lcd
                   for c in self.follow_choices())

    @property
    def follow_status(self) -> str:
        """What following is doing now, as the App last said."""
        follow = self._follow
        who = "OpenRGB" if follow.mode is RgbFollowMode.OPENRGB else "The RAM"
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
        """The effect preview's strips: each stick's name and what it runs.

        Built-in effect: the effect being set, on the ticked sticks.  Leave
        alone: what TRCC last saved on each stick -- None where it never saved
        one, so the stick runs its factory default or another program's.
        """
        if self.source is RgbSource.EFFECT:
            settings = self.effect_picks.settings()
            strips = tuple((row.title, settings)
                           for row in self.rows(LightKind.RAM) if row.checked)
        else:
            strips = tuple((light.name, light.effect)
                           for light in self._ram_lights())
        log.debug("RgbPage.effect_strips: %d", len(strips))
        return strips

    #: LEDs per strip in the follow preview: the rows the App samples a
    #: frame into, one per LED of a 10-LED stick.
    PREVIEW_ROWS = FRAME_ROWS

    @property
    def preview_lights(self) -> tuple[str, ...]:
        """The lights the follow preview draws a strip for, in the order the
        App sends them -- the ticked ones of the kind that would follow."""
        kind = {RgbFollowMode.RAM: LightKind.RAM,
                RgbFollowMode.OPENRGB: LightKind.OPENRGB,
                }.get(self._follow_mode() or RgbFollowMode.OFF)
        names = (tuple(row.title for row in self.rows(kind) if row.checked)
                 if kind is not None else ())
        log.debug("RgbPage.preview_lights: %s", names)
        return names

    @property
    def preview_columns(self) -> int:
        """Columns the picture is cut into: one per light (light *i* takes
        column *i*), or one for all -- as ``RgbMirrorService.on_frame``."""
        columns = (1 if self.mapping is FollowMapping.SINGLE
                   else max(1, len(self.preview_lights)))
        log.debug("RgbPage.preview_columns: %d", columns)
        return columns

    def _follow_mode(self) -> RgbFollowMode | None:
        """RAM or OpenRGB, from the ticked lights; None for none or both."""
        log.debug("RgbPage._follow_mode: %d ticked", len(self._checked))
        kinds = {light.kind for light in self._lights
                 if light.ref in self._checked}
        if not self._lights and self._follow.mode is not RgbFollowMode.OFF:
            return self._follow.mode     # nothing listed yet: keep what runs
        return {frozenset({LightKind.RAM}): RgbFollowMode.RAM,
                frozenset({LightKind.OPENRGB}): RgbFollowMode.OPENRGB,
                }.get(frozenset(kinds))

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
        problem: str | None = None
        if self.source is RgbSource.EFFECT:
            if not any(row.checked for row in self.rows(LightKind.RAM)):
                problem = "Tick a RAM stick -- effects run on the memory itself."
            else:
                problem = effect_problem(self.effect_picks.settings())
        elif self.source is RgbSource.FOLLOW:
            kinds = {light.kind for light in self._lights
                     if light.ref in self._checked}
            if len(kinds) > 1:
                problem = ("Following drives RAM or OpenRGB lights, one at a "
                           "time -- untick one kind.")
            elif self._follow_mode() is None:
                problem = "Tick the lights that should follow."
            elif parse_openrgb_address(self.address) is None:
                problem = BAD_ADDRESS
        log.debug("RgbPage.problem: %s -> %s", self.source.value, problem)
        return problem

    def apply(self) -> ApplyPlan | None:
        """What Apply does, or None while ``problem`` says why not."""
        if (problem := self.problem) is not None:
            log.info("RgbPage.apply: nothing -- %s", problem)
            return None
        plan: ApplyPlan | None = None
        mode = self._follow_mode()
        address = parse_openrgb_address(self.address)
        if self.source is RgbSource.LEAVE:
            plan = LeaveLights()
        elif self.source is RgbSource.EFFECT:
            plan = SaveEffect(self._targets(LightKind.RAM),
                              self.effect_picks.settings())
        elif mode is not None and address is not None:
            kind = (LightKind.RAM if mode is RgbFollowMode.RAM
                    else LightKind.OPENRGB)
            plan = FollowDevice(mode, *address, self.follow_source,
                                self.mapping, self.colors, self._targets(kind))
        log.info("RgbPage.apply: %s", plan)
        return plan
