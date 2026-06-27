"""LED effect engine — pure-logic tests for services/led_effects.py.

The engine is stateless w.r.t. itself but mutates the caller-owned
``LedRuntimeState`` as a side effect.  Every test verifies BOTH the
returned color list AND the counter advancement, since the engine's
contract is "advance the phase between ticks".
"""
from __future__ import annotations

from trcc.core.led_models import (
    LedDeviceSettings,
    LEDMode,
    LedRuntimeState,
    LedZoneSettings,
)
from trcc.services.led_effects import (
    ColorEngine,
    LEDEffectEngine,
)

# ── ColorEngine — rainbow table + gradient ──────────────────────────


def test_rainbow_table_has_768_entries() -> None:
    table = ColorEngine.get_table()
    assert len(table) == 768
    # Cached across calls
    assert ColorEngine.get_table() is table


def test_rainbow_table_starts_red_and_wraps_back_to_red() -> None:
    table = ColorEngine.get_table()
    assert table[0] == (255, 0, 0)        # phase 0 start — pure red
    # Phase boundaries at multiples of 128
    assert table[128] == (255, 255, 0)    # red → yellow boundary
    assert table[256] == (0, 255, 0)      # yellow → green boundary
    assert table[640] == (255, 0, 255)    # blue → magenta boundary


def test_color_for_value_clamps_low_and_high() -> None:
    assert ColorEngine.color_for_value(0, ColorEngine.TEMP_GRADIENT) == (0, 255, 255)
    assert ColorEngine.color_for_value(500, ColorEngine.TEMP_GRADIENT) == (255, 0, 0)


def test_color_for_value_interpolates_between_stops() -> None:
    # Midpoint between 30 (cyan) and 50 (green) at value=40
    color = ColorEngine.color_for_value(40, ColorEngine.TEMP_GRADIENT)
    # cyan(0,255,255) → green(0,255,0); midpoint = (0, 255, 127)
    assert color == (0, 255, 127)


# ── LEDEffectEngine.tick — STATIC ───────────────────────────────────


def _settings(**overrides: object) -> LedDeviceSettings:
    """Build LedDeviceSettings with keyword overrides applied."""
    s = LedDeviceSettings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_static_returns_same_color_n_times() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState()
    settings = _settings(mode=LEDMode.STATIC, color=(255, 128, 0))

    colors = engine.tick(settings, runtime, sensors={}, led_count=10)

    assert colors == [(255, 128, 0)] * 10
    # Counters untouched on STATIC
    assert runtime.rgb_timer == 0


# ── BREATHING — pulse cycle ────────────────────────────────────────


def test_breathing_advances_timer_each_tick() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState()
    settings = _settings(mode=LEDMode.BREATHING, color=(255, 0, 0))

    engine.tick(settings, runtime, {}, led_count=5)
    assert runtime.rgb_timer == 1
    engine.tick(settings, runtime, {}, led_count=5)
    assert runtime.rgb_timer == 2


def test_breathing_pulses_toward_full_then_back() -> None:
    """At timer=0 brightness should be at the minimum floor (20% of color)."""
    engine = LEDEffectEngine()
    runtime = LedRuntimeState(rgb_timer=0)
    settings = _settings(mode=LEDMode.BREATHING, color=(255, 0, 0))

    colors = engine.tick(settings, runtime, {}, led_count=1)
    assert colors[0] == (int(255 * 0.2), 0, 0)


def test_breathing_period_wraps_to_zero() -> None:
    """After period-1 ticks, timer wraps back to 0."""
    engine = LEDEffectEngine()
    runtime = LedRuntimeState(rgb_timer=65)  # period=66, so 65→ next is 0
    settings = _settings(mode=LEDMode.BREATHING, color=(0, 255, 0))

    engine.tick(settings, runtime, {}, led_count=1)
    assert runtime.rgb_timer == 0


# ── RAINBOW — 768-entry table shift ─────────────────────────────────


def test_rainbow_uses_table_with_per_segment_offset() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState(rgb_timer=0)
    settings = _settings(mode=LEDMode.RAINBOW)
    table = ColorEngine.get_table()

    colors = engine.tick(settings, runtime, {}, led_count=4)

    stride = 768 // 4
    assert colors == [table[0], table[stride], table[stride * 2], table[stride * 3]]


def test_rainbow_advances_timer_by_step() -> None:
    """RAINBOW uses a 4-step advance to keep visible motion at low LED counts."""
    engine = LEDEffectEngine()
    runtime = LedRuntimeState(rgb_timer=0)
    settings = _settings(mode=LEDMode.RAINBOW)

    engine.tick(settings, runtime, {}, led_count=10)
    assert runtime.rgb_timer == 4
    engine.tick(settings, runtime, {}, led_count=10)
    assert runtime.rgb_timer == 8


# ── COLORFUL — 6-phase cycle ────────────────────────────────────────


def test_colorful_advances_timer_each_tick() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState()
    settings = _settings(mode=LEDMode.COLORFUL)

    engine.tick(settings, runtime, {}, led_count=5)
    assert runtime.rgb_timer == 1


def test_colorful_period_wraps_to_zero() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState(rgb_timer=167)   # period=168
    settings = _settings(mode=LEDMode.COLORFUL)

    engine.tick(settings, runtime, {}, led_count=4)
    assert runtime.rgb_timer == 0


# ── TEMP_LINKED ─────────────────────────────────────────────────────


def test_temp_linked_uses_cpu_temp_sensor() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState()
    settings = _settings(mode=LEDMode.TEMP_LINKED, temp_source="cpu")

    # 40°C → cyan/green midpoint
    colors = engine.tick(
        settings, runtime, sensors={"cpu:temp": 40.0}, led_count=3,
    )

    assert colors == [(0, 255, 127)] * 3
    assert runtime.last_temp_color == (0, 255, 127)


def test_temp_linked_holds_last_color_when_sensor_missing() -> None:
    """No sensor reading → keep the previous color instead of dropping to (0,0,0)."""
    engine = LEDEffectEngine()
    runtime = LedRuntimeState(last_temp_color=(255, 110, 0))
    settings = _settings(mode=LEDMode.TEMP_LINKED, temp_source="cpu")

    colors = engine.tick(settings, runtime, sensors={}, led_count=2)

    assert colors == [(255, 110, 0)] * 2
    assert runtime.last_temp_color == (255, 110, 0)


def test_temp_linked_switches_to_gpu_when_configured() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState()
    settings = _settings(mode=LEDMode.TEMP_LINKED, temp_source="gpu")

    colors = engine.tick(
        settings, runtime,
        sensors={"cpu:temp": 100.0,           # red
                 "gpu:primary:temp": 30.0},   # cyan
        led_count=1,
    )

    assert colors == [(0, 255, 255)]   # GPU temp wins


# ── LOAD_LINKED ─────────────────────────────────────────────────────


def test_load_linked_uses_cpu_usage() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState()
    settings = _settings(mode=LEDMode.LOAD_LINKED, load_source="cpu")

    colors = engine.tick(
        settings, runtime, sensors={"cpu:usage": 100.0}, led_count=1,
    )

    assert colors == [(255, 0, 0)]   # 100% → red


# ── Test mode — 4-color diagnostic cycle ───────────────────────────


def test_test_mode_overrides_mode_and_cycles_colors() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState()
    settings = _settings(mode=LEDMode.RAINBOW, test_mode=True)

    # First tick: white (1,1,1), test_timer goes from 0 → 1
    colors = engine.tick(settings, runtime, sensors={}, led_count=2)
    assert colors == [(1, 1, 1), (1, 1, 1)]
    assert runtime.test_timer == 1


def test_test_mode_rotates_color_every_10_ticks() -> None:
    engine = LEDEffectEngine()
    runtime = LedRuntimeState(test_timer=9)   # next tick triggers rotation
    settings = _settings(mode=LEDMode.STATIC, test_mode=True)

    colors = engine.tick(settings, runtime, sensors={}, led_count=1)

    assert runtime.test_timer == 0
    assert runtime.test_color == 1
    assert colors == [(1, 0, 0)]   # red — index 1 in _TEST_COLORS


# ── Multi-zone fill (PA120 / LF10) ──────────────────────────────────


def test_tick_multi_zone_places_each_zone_color_at_its_mapped_indices() -> None:
    """Each zone's own color lands on its physical LED indices — the gap
    the audit found (every zone was showing one global color)."""
    engine = LEDEffectEngine()
    zones = [
        LedZoneSettings(mode=LEDMode.STATIC, color=(255, 0, 0),
                        brightness=100, on=True),
        LedZoneSettings(mode=LEDMode.STATIC, color=(0, 255, 0),
                        brightness=100, on=True),
    ]
    settings = _settings(zones=zones)
    # zone 0 → LEDs 0,1 ; zone 1 → LEDs 2,3
    colors = engine.tick_multi_zone(
        settings, LedRuntimeState(), sensors={},
        zone_map=((0, 1), (2, 3)), metric_sources=None, led_count=4,
    )
    assert colors == [(255, 0, 0), (255, 0, 0), (0, 255, 0), (0, 255, 0)]


def test_tick_multi_zone_skips_an_off_zone() -> None:
    """An off zone leaves its LEDs dark (0,0,0); other zones still render."""
    engine = LEDEffectEngine()
    zones = [
        LedZoneSettings(mode=LEDMode.STATIC, color=(255, 0, 0),
                        brightness=100, on=True),
        LedZoneSettings(mode=LEDMode.STATIC, color=(0, 255, 0),
                        brightness=100, on=False),
    ]
    settings = _settings(zones=zones)
    colors = engine.tick_multi_zone(
        settings, LedRuntimeState(), sensors={},
        zone_map=((0, 1), (2, 3)), metric_sources=None, led_count=4,
    )
    assert colors == [(255, 0, 0), (255, 0, 0), (0, 0, 0), (0, 0, 0)]


def test_tick_multi_zone_scales_by_zone_brightness() -> None:
    """Per-zone brightness scales that zone's color (legacy int truncation)."""
    engine = LEDEffectEngine()
    zones = [LedZoneSettings(mode=LEDMode.STATIC, color=(200, 100, 40),
                             brightness=50, on=True)]
    settings = _settings(zones=zones)
    colors = engine.tick_multi_zone(
        settings, LedRuntimeState(), sensors={},
        zone_map=((0,),), metric_sources=None, led_count=1,
    )
    assert colors == [(100, 50, 20)]


def test_tick_multi_zone_ignores_indices_past_led_count() -> None:
    """A zone_map index >= led_count is skipped, not an IndexError."""
    engine = LEDEffectEngine()
    zones = [LedZoneSettings(mode=LEDMode.STATIC, color=(255, 0, 0),
                             brightness=100, on=True)]
    settings = _settings(zones=zones)
    colors = engine.tick_multi_zone(
        settings, LedRuntimeState(), sensors={},
        zone_map=((0, 9),), metric_sources=None, led_count=2,
    )
    assert colors == [(255, 0, 0), (0, 0, 0)]   # index 9 dropped


# ── Zone-sync carousel rotation ─────────────────────────────────────


def test_next_sync_zone_rotates_skipping_disabled_zones() -> None:
    engine = LEDEffectEngine()
    zones = [True, False, True, True]
    assert engine.next_sync_zone(zones, 0) == 2    # skip disabled zone 1
    assert engine.next_sync_zone(zones, 2) == 3
    assert engine.next_sync_zone(zones, 3) == 0    # wrap around


def test_next_sync_zone_no_enabled_zone_returns_zero() -> None:
    engine = LEDEffectEngine()
    assert engine.next_sync_zone([False, False, False], 0) == 0


def test_next_sync_zone_empty_returns_zero() -> None:
    engine = LEDEffectEngine()
    assert engine.next_sync_zone([], 0) == 0


# ── Cohesive per-digit colouring for segment displays (#193) ─────────


def _all_segment_displays():
    from trcc.services.led_segment import SegmentDisplay
    subs = SegmentDisplay.__subclasses__()
    return subs + [c for s in subs for c in s.__subclasses__()]


def test_grouped_styles_cover_every_led() -> None:
    """A style's colour groups MUST cover all its LEDs (single-zone) / its
    zone_led_map row (multi-zone) — else grouped LEDs render black (#193)."""
    for cls in _all_segment_displays():
        d = cls()
        if d.color_groups is not None:
            union: set[int] = set()
            for g in d.color_groups:
                union |= set(g)
            assert union == set(range(d.mask_size)), cls.__name__
        if d.zone_color_groups is not None:
            assert d.zone_led_map is not None
            for zleds, zgroups in zip(d.zone_led_map, d.zone_color_groups,
                                      strict=True):
                gu: set[int] = set()
                for g in zgroups:
                    gu |= set(g)
                assert gu == set(zleds), cls.__name__


def test_rainbow_cohesive_within_each_digit_single_zone() -> None:
    """Single-zone rainbow: every LED of a digit shares ONE colour, but the
    old per-LED path spread a different colour across each segment (#193)."""
    from trcc.services.led_segment import AK120Display

    d = AK120Display()
    eng = LEDEffectEngine()
    st = LedDeviceSettings(mode=LEDMode.RAINBOW, color=(255, 0, 0))

    grouped = eng.tick(st, LedRuntimeState(), {},
                       led_count=d.mask_size, color_groups=d.color_groups)
    flat = eng.tick(st, LedRuntimeState(), {},
                    led_count=d.mask_size, color_groups=None)

    digit = d.color_groups[0]
    assert len({grouped[i] for i in digit}) == 1          # cohesive (fixed)
    assert len({flat[i] for i in digit}) > 1              # spread (the bug)


def test_rainbow_cohesive_within_each_digit_multi_zone() -> None:
    """Multi-zone (PA120) rainbow: each digit cohesive, digits gently differ."""
    from trcc.services.led_segment import PA120Display

    d = PA120Display()
    eng = LEDEffectEngine()
    zones = [LedZoneSettings(on=True, mode=LEDMode.RAINBOW, brightness=100)
             for _ in range(len(d.zone_led_map))]
    st = LedDeviceSettings(zones=zones)

    colors = eng.tick_multi_zone(
        st, LedRuntimeState(), {}, zone_map=d.zone_led_map,
        metric_sources=d.zone_metric_sources, led_count=d.mask_size,
        zone_color_groups=d.zone_color_groups,
    )
    d1, d2, d3 = d.CPU_TEMP_DIGITS
    assert len({colors[i] for i in d1}) == 1              # digit 1 cohesive
    assert len({colors[i] for i in d2}) == 1              # digit 2 cohesive
    assert colors[d1[0]] != colors[d2[0]]                 # gentle gradient


def test_rainbow_strip_still_spreads_without_groups() -> None:
    """No groups (RGB strip) keeps the full per-LED spread — unchanged."""
    eng = LEDEffectEngine()
    st = LedDeviceSettings(mode=LEDMode.RAINBOW, color=(255, 0, 0))
    colors = eng.tick(st, LedRuntimeState(), {}, led_count=12)
    assert len(set(colors)) > 1


# ── Decoration-strip styles: digits cohesive, strip stays spatial (#193) ──


def test_cohere_digit_groups_collapses_only_groups() -> None:
    """cohere_digit_groups flattens each group to its first LED's color and
    leaves every other LED untouched (the decoration strip)."""
    colors = [(i, 0, 0) for i in range(10)]
    out = LEDEffectEngine.cohere_digit_groups(colors, ((1, 2, 3), (5, 6)))
    assert out[1] == out[2] == out[3] == (1, 0, 0)   # group 0 → first LED
    assert out[5] == out[6] == (5, 0, 0)             # group 1 → first LED
    assert out[0] == (0, 0, 0) and out[4] == (4, 0, 0) and out[7] == (7, 0, 0)


def test_decoration_styles_cohere_digits_keep_strip_spatial() -> None:
    """LF10/LF12 rainbow: each metric digit is one color, but the decoration
    strip retains its per-LED spatial rainbow (the C# spreads it) (#193)."""
    from trcc.services.led_segment import LF10Display, LF12Display

    eng = LEDEffectEngine()

    d12 = LF12Display()
    st = LedDeviceSettings(mode=LEDMode.RAINBOW, color=(255, 0, 0))
    flat = eng.tick(st, LedRuntimeState(), {},
                    led_count=d12.mask_size, color_groups=d12.color_groups)
    fixed = eng.cohere_digit_groups(flat, d12.digit_groups)
    assert len({fixed[i] for i in d12.digit_groups[0]}) == 1
    assert len({fixed[i] for i in d12.DECORATION}) > 1

    d10 = LF10Display()
    zones = [LedZoneSettings(on=True, mode=LEDMode.RAINBOW, brightness=100)
             for _ in range(len(d10.zone_led_map))]
    st2 = LedDeviceSettings(zones=zones)
    flat2 = eng.tick_multi_zone(
        st2, LedRuntimeState(), {}, zone_map=d10.zone_led_map,
        metric_sources=d10.zone_metric_sources, led_count=d10.mask_size,
        zone_color_groups=d10.zone_color_groups,
    )
    fixed2 = eng.cohere_digit_groups(flat2, d10.digit_groups)
    assert len({fixed2[i] for i in d10.digit_groups[0]}) == 1
    assert len({fixed2[i] for i in d10.DECORATION}) > 1
