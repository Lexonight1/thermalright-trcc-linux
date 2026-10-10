"""What a built-in RAM effect looks like, drawn by TRCC -- no toolkit.

The stick runs its effects itself, in its own firmware, and nothing tells
TRCC what each frame looks like.  So this is an APPROXIMATION from each
effect's name and settings -- its colours, speed, direction and brightness
-- for the preview strips on the RGB page, and the page says so.  Compared
with real sticks it can be corrected effect by effect; until then it shows
what kind of effect each is, not the exact frame on the glass.

``effect_frame(settings, t, stick, sticks)`` gives one stick's LEDs, top to
bottom, at *t* seconds.  Left / right effects travel across the sticks, so
each stick needs its place among them.
"""
from __future__ import annotations

import colorsys
import logging
import math
from collections.abc import Callable

from ...core.logs import per_frame
from ...core.models import (
    EFFECT_TRAITS,
    EffectDirection,
    EffectSpeed,
    RamEffect,
    RamEffectSettings,
)

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

Rgb = tuple[int, int, int]

#: LEDs a preview strip draws: a 10-LED stick.
LEDS = 10
#: Seconds one cycle of an effect takes at each speed.
PERIOD_S: dict[EffectSpeed, float] = {
    EffectSpeed.SLOW: 4.0, EffectSpeed.MEDIUM: 2.0, EffectSpeed.FAST: 1.0}
#: Shown with the strips, wherever they are.
APPROXIMATE = ("Drawn by TRCC from the effect's settings -- the stick runs it "
               "itself, so yours may differ in detail.")

_BLACK: Rgb = (0, 0, 0)
_WHITE: Rgb = (255, 255, 255)


def _hue(h: float) -> Rgb:
    """A fully saturated colour at hue *h* (0-1, wrapping)."""
    frame_log.debug("_hue")
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return round(r * 255), round(g * 255), round(b * 255)


def _mix(a: Rgb, b: Rgb, f: float) -> Rgb:
    """*a* to *b* by *f* (0-1)."""
    frame_log.debug("_mix")
    return (round(a[0] + (b[0] - a[0]) * f), round(a[1] + (b[1] - a[1]) * f),
            round(a[2] + (b[2] - a[2]) * f))


def _scale(c: Rgb, f: float) -> Rgb:
    frame_log.debug("_scale")
    return round(c[0] * f), round(c[1] * f), round(c[2] * f)


class _Frame:
    """One stick's place and time, with the effect's colours resolved."""

    def __init__(self, settings: RamEffectSettings, t: float, stick: int,
                 sticks: int) -> None:
        frame_log.debug("_Frame: %s t=%.2f stick %d/%d",
                        settings.effect.value, t, stick, sticks)
        traits = EFFECT_TRAITS[settings.effect]
        self.settings = settings
        self.phase = t / PERIOD_S[settings.speed]       # cycles so far
        self.stick, self.sticks = stick, max(1, sticks)
        self.direction = settings.direction or (
            traits.directions[0] if traits.directions else EffectDirection.DOWN)
        picked = list(settings.colors) or [_WHITE]
        if settings.random_colors and traits.random:
            # The stick picks its own: shown as colours walking the wheel.
            picked = [_hue(self.phase * 0.17), _hue(self.phase * 0.17 + 0.5)]
        self.a = picked[0]
        self.b = picked[1] if len(picked) > 1 else picked[0]

    def place(self, led: int) -> float:
        """Where *led* sits along the effect's travel, 0-1."""
        frame_log.debug("place")
        if self.direction in (EffectDirection.LEFT, EffectDirection.RIGHT,
                              EffectDirection.HORIZONTAL):
            across = (self.stick + 0.5) / self.sticks
            return 1 - across if self.direction is EffectDirection.LEFT else across
        down = (led + 0.5) / LEDS
        return 1 - down if self.direction is EffectDirection.UP else down


def _static(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_static")
    return f.a


def _color_shift(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_color_shift")
    # The whole stick eases A -> B -> A.
    return _mix(f.a, f.b, (1 - math.cos(f.phase * math.tau)) / 2)


def _color_pulse(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_color_pulse")
    # Breathes A, then B.
    level = (1 - math.cos(f.phase * math.tau)) / 2
    return _scale(f.a if int(f.phase) % 2 == 0 else f.b, level)


def _rainbow(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_rainbow")
    return _hue(f.phase * 0.25)


def _rainbow_wave(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_rainbow_wave")
    return _hue(f.place(led) - f.phase * 0.5)


def _color_wave(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_color_wave")
    return _mix(f.a, f.b, (1 + math.sin((f.place(led) - f.phase) * math.tau)) / 2)


def _visor(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_visor")
    # A bright band sweeping one way then back, A one way and B the other.
    sweep = abs((f.phase % 2.0) - 1.0)                  # 1 -> 0 -> 1
    near = max(0.0, 1 - abs(f.place(led) - sweep) * 4)
    return _scale(f.a if f.phase % 2.0 < 1.0 else f.b, near)


def _rain(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_rain")
    # Drops: each stick has its own, falling along the travel.
    seed = (f.stick * 7919) % 97 / 97
    drop = (f.phase + seed) % 1.0
    tail = max(0.0, 1 - ((drop - f.place(led)) % 1.0) * 5)
    return _scale(f.a if int(f.phase + seed) % 2 == 0 else f.b, tail)


def _marquee(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_marquee")
    # Every third LED lit, stepping along.
    return f.a if (led + int(f.phase * LEDS)) % 3 == 0 else _BLACK


def _sequential(f: _Frame, led: int) -> Rgb:
    frame_log.debug("_sequential")
    # Fills one LED at a time with A, then with B over it.
    filled = (f.phase % 1.0) >= f.place(led)
    second = int(f.phase) % 2 == 1
    if filled:
        return f.b if second else f.a
    return f.a if second else _BLACK


_DRAW: dict[RamEffect, Callable[[_Frame, int], Rgb]] = {
    RamEffect.STATIC: _static,
    RamEffect.COLOR_SHIFT: _color_shift,
    RamEffect.COLOR_PULSE: _color_pulse,
    RamEffect.RAINBOW: _rainbow,
    RamEffect.RAINBOW_WAVE: _rainbow_wave,
    RamEffect.COLOR_WAVE: _color_wave,
    RamEffect.VISOR: _visor,
    RamEffect.RAIN: _rain,
    RamEffect.MARQUEE: _marquee,
    RamEffect.SEQUENTIAL: _sequential,
}


def effect_frame(settings: RamEffectSettings, t: float, stick: int = 0,
                 sticks: int = 1) -> list[Rgb]:
    """Stick *stick* of *sticks* at *t* seconds: its LEDs, top to bottom."""
    frame_log.debug("effect_frame")
    frame = _Frame(settings, t, stick, sticks)
    draw = _DRAW[settings.effect]
    level = settings.brightness / 255 if EFFECT_TRAITS[
        settings.effect].brightness else 1.0
    return [_scale(draw(frame, led), level) for led in range(LEDS)]
