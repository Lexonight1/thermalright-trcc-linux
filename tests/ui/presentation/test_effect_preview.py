"""TRCC's drawing of each built-in RAM effect -- no Qt."""
from __future__ import annotations

import pytest

from trcc.core.models import (
    EFFECT_TRAITS,
    EffectDirection,
    EffectSpeed,
    RamEffect,
    RamEffectSettings,
)
from trcc.ui.presentation.effect_preview import LEDS, PERIOD_S, effect_frame

RED, BLUE = (255, 0, 0), (0, 0, 255)
TIMES = [i * 0.13 for i in range(40)]


def _settings(effect: RamEffect, speed: EffectSpeed = EffectSpeed.MEDIUM,
              direction: EffectDirection | None = None,
              brightness: int = 255) -> RamEffectSettings:
    """*effect* in red then blue, as many colours as it takes."""
    colors = (RED, BLUE)[:EFFECT_TRAITS[effect].colors]
    return RamEffectSettings(effect, speed, direction, colors,
                             brightness=brightness)


def _same(a: list[tuple[int, int, int]], b: list[tuple[int, int, int]]) -> bool:
    """Equal to a colour step: float maths rounds a hue a step either way."""
    return all(abs(x - y) <= 1
               for p, q in zip(a, b, strict=True) for x, y in zip(p, q, strict=True))


@pytest.mark.parametrize("effect", list(RamEffect))
def test_every_effect_draws_a_stick_of_real_colours(effect: RamEffect) -> None:
    for t in TIMES:
        leds = effect_frame(_settings(effect), t, 1, 2)
        assert len(leds) == LEDS
        assert all(0 <= c <= 255 for led in leds for c in led)


#: Steady on the maintainer's Vengeance DDR5 (0x0701), seen 2026-10-10.
STEADY = (RamEffect.STATIC, RamEffect.MARQUEE, RamEffect.SEQUENTIAL)


@pytest.mark.parametrize("effect", [e for e in RamEffect if e not in STEADY])
def test_every_moving_effect_moves(effect: RamEffect) -> None:
    frames = {tuple(effect_frame(_settings(effect), t)) for t in TIMES}
    assert len(frames) > 1


@pytest.mark.parametrize("effect", STEADY)
def test_a_steady_effect_is_its_colour_on_every_led_at_every_moment(
        effect: RamEffect) -> None:
    for t in TIMES:
        assert effect_frame(_settings(effect), t) == [RED] * LEDS


def test_colour_wave_is_one_colour_dark_the_other_dark_travelling() -> None:
    """As the DDR5 sticks show it: bands, not a blend."""
    wave = _settings(RamEffect.COLOR_WAVE, direction=EffectDirection.DOWN)
    seen = {led for t in TIMES for led in effect_frame(wave, t)}
    assert seen == {RED, BLUE, (0, 0, 0)}          # never a mix of the two
    # The pattern spans two sticks, so one LED is 1/20 of a cycle -- 0.1 s
    # at Medium.  Start off a band edge so rounding cannot decide it.
    start, one_led = 0.013, PERIOD_S[EffectSpeed.MEDIUM] / 20
    first = effect_frame(wave, start)
    later = effect_frame(wave, start + one_led)
    assert later[1:] == first[:-1]                 # moved one LED down


def test_up_draws_down_upside_down() -> None:
    up = _settings(RamEffect.RAINBOW_WAVE, direction=EffectDirection.UP)
    down = _settings(RamEffect.RAINBOW_WAVE, direction=EffectDirection.DOWN)
    for t in TIMES:
        assert _same(effect_frame(up, t), effect_frame(down, t)[::-1])


def test_left_and_right_travel_across_the_sticks() -> None:
    wave = _settings(RamEffect.COLOR_WAVE, direction=EffectDirection.RIGHT)
    t = PERIOD_S[EffectSpeed.MEDIUM] / 8
    first, second = effect_frame(wave, t, 0, 2), effect_frame(wave, t, 1, 2)
    assert first != second
    # Each stick is one place along the travel: one colour top to bottom.
    assert len(set(first)) == 1 and len(set(second)) == 1


def test_faster_runs_more_cycles_in_the_same_time() -> None:
    t = PERIOD_S[EffectSpeed.FAST] / 2        # half a fast cycle
    slow = _settings(RamEffect.COLOR_SHIFT, speed=EffectSpeed.SLOW)
    fast = _settings(RamEffect.COLOR_SHIFT, speed=EffectSpeed.FAST)
    assert effect_frame(fast, t) == [BLUE] * LEDS     # all the way to B
    assert effect_frame(slow, t) != [BLUE] * LEDS


def test_brightness_dims_only_effects_that_take_it() -> None:
    full = effect_frame(_settings(RamEffect.COLOR_WAVE), 0.3)
    half = effect_frame(_settings(RamEffect.COLOR_WAVE, brightness=128), 0.3)
    assert half == [tuple(round(c * 128 / 255) for c in led) for led in full]
    # Static takes no brightness: it is drawn at full whatever is saved.
    assert effect_frame(_settings(RamEffect.STATIC, brightness=10),
                        0.3) == [RED] * LEDS


def test_rainbow_wave_at_medium_comes_round_in_six_seconds() -> None:
    """As the maintainer's sticks run it: ~1 s per colour, six colours."""
    wave = _settings(RamEffect.RAINBOW_WAVE)
    assert _same(effect_frame(wave, 6.0), effect_frame(wave, 0.0))
    assert not _same(effect_frame(wave, 3.0), effect_frame(wave, 0.0))
