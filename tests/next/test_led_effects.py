"""LED effect engine — pure-logic tests for services/led_effects.py.

The engine is stateless w.r.t. itself but mutates the caller-owned
``LedRuntimeState`` as a side effect.  Every test verifies BOTH the
returned color list AND the counter advancement, since the engine's
contract is "advance the phase between ticks".
"""
from __future__ import annotations

from trcc.next.core.led_models import (
    LedDeviceSettings,
    LEDMode,
    LedRuntimeState,
)
from trcc.next.services.led_effects import (
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
