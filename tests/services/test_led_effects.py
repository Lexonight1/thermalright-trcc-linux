"""Tests for services/led_effects.py — LED animation effect engine.

Covers:
- _tick_single_mode() dispatch to correct algorithm
- Static mode — solid color fill
- Breathing — pulse cycle, timer wraparound
- Colorful — 6-phase gradient, timer wraparound
- Rainbow — per-segment offset, table lookup
- Temp-linked — color from temperature
- Load-linked — color from CPU/GPU load
- Test mode — 4-color cycle every 10 ticks
- Multi-zone — per-zone color with brightness scaling
- _next_sync_zone() — rotation with wrapping
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from trcc.legacy.core.models import HardwareMetrics, LEDMode, LEDState, LEDZoneState
from trcc.legacy.services.led_effects import LEDEffectEngine


def _make_engine(state: LEDState | None = None,
                 metrics: HardwareMetrics | None = None) -> LEDEffectEngine:
    return LEDEffectEngine(
        state or LEDState(),
        metrics or HardwareMetrics(),
    )


# =========================================================================
# Static mode
# =========================================================================


class TestStaticMode:
    def test_returns_solid_color(self):
        engine = _make_engine()
        colors = engine._tick_single_mode(LEDMode.STATIC, (255, 0, 0), 5)
        assert colors == [(255, 0, 0)] * 5

    def test_single_segment(self):
        engine = _make_engine()
        colors = engine._tick_single_mode(LEDMode.STATIC, (0, 255, 128), 1)
        assert colors == [(0, 255, 128)]

    def test_zero_segments(self):
        engine = _make_engine()
        colors = engine._tick_single_mode(LEDMode.STATIC, (255, 0, 0), 0)
        assert colors == []


# =========================================================================
# Breathing mode
# =========================================================================


class TestBreathingMode:
    def test_period_is_66(self):
        state = LEDState(rgb_timer=0)
        engine = _make_engine(state)
        # Tick 66 times — timer should wrap back to 0
        for _ in range(66):
            engine._tick_breathing_for((255, 255, 255), 1)
        assert state.rgb_timer == 0

    def test_timer_advances(self):
        state = LEDState(rgb_timer=0)
        engine = _make_engine(state)
        engine._tick_breathing_for((255, 0, 0), 3)
        assert state.rgb_timer == 1

    def test_midpoint_brightness(self):
        """At timer=0 (start), factor=0 → colors at 20% base."""
        state = LEDState(rgb_timer=0)
        engine = _make_engine(state)
        colors = engine._tick_breathing_for((100, 100, 100), 1)
        r, g, b = colors[0]
        # factor=0: r = int(100 * 0 * 0.8 + 100 * 0.2) = 20
        assert r == 20
        assert g == 20
        assert b == 20

    def test_peak_brightness(self):
        """At half period (timer=32), factor approaches 1."""
        state = LEDState(rgb_timer=32)
        engine = _make_engine(state)
        colors = engine._tick_breathing_for((100, 100, 100), 1)
        r, _, _ = colors[0]
        # factor = 32/33 ≈ 0.97 → r ≈ int(100*0.97*0.8 + 100*0.2) = 97
        assert r > 90

    def test_returns_uniform_colors(self):
        engine = _make_engine()
        colors = engine._tick_breathing_for((255, 0, 0), 4)
        assert len(colors) == 4
        assert all(c == colors[0] for c in colors)


# =========================================================================
# Colorful mode
# =========================================================================


class TestColorfulMode:
    def test_period_is_168(self):
        state = LEDState(rgb_timer=0)
        engine = _make_engine(state)
        for _ in range(168):
            engine._tick_colorful_for(1)
        assert state.rgb_timer == 0

    def test_phase_0_starts_red(self):
        """Phase 0, offset 0 → (255, 0, 0)."""
        state = LEDState(rgb_timer=0)
        engine = _make_engine(state)
        colors = engine._tick_colorful_for(1)
        assert colors[0] == (255, 0, 0)

    def test_phase_1_starts_yellow(self):
        """Phase 1, offset 0 → (255, 255, 0)."""
        state = LEDState(rgb_timer=28)
        engine = _make_engine(state)
        colors = engine._tick_colorful_for(1)
        assert colors[0] == (255, 255, 0)

    def test_segments_have_phase_offset(self):
        """Each segment gets a different phase offset — colorful is not uniform."""
        engine = _make_engine()
        colors = engine._tick_colorful_for(6)
        assert len(colors) == 6
        assert len(set(colors)) > 1


# =========================================================================
# Rainbow mode
# =========================================================================


class TestRainbowMode:
    @patch('trcc.legacy.core.color.ColorEngine')
    def test_uses_color_table(self, mock_ce):
        table = [(i, 0, 0) for i in range(768)]
        mock_ce.get_table.return_value = table
        engine = _make_engine()
        colors = engine._tick_rainbow_for(3)
        assert len(colors) == 3
        mock_ce.get_table.assert_called_once()

    @patch('trcc.legacy.core.color.ColorEngine')
    def test_timer_advances_by_4(self, mock_ce):
        table = [(0, 0, 0)] * 768
        mock_ce.get_table.return_value = table
        state = LEDState(rgb_timer=0)
        engine = _make_engine(state)
        engine._tick_rainbow_for(1)
        assert state.rgb_timer == 4

    @patch('trcc.legacy.core.color.ColorEngine')
    def test_per_segment_offset(self, mock_ce):
        """Each segment gets a different color from the table."""
        table = [(i, 0, 0) for i in range(768)]
        mock_ce.get_table.return_value = table
        state = LEDState(rgb_timer=0)
        engine = _make_engine(state)
        colors = engine._tick_rainbow_for(3)
        # Segments should have different colors (offset by table_len/seg_count)
        assert colors[0] != colors[1]


# =========================================================================
# Temp-linked mode
# =========================================================================


class TestTempLinkedMode:
    """Tick path gated on ``HardwareMetrics._populated`` (metrics-unification)."""

    @patch('trcc.legacy.core.color.ColorEngine')
    def test_cpu_temp_source(self, mock_ce):
        mock_ce.color_for_value.return_value = (255, 0, 0)
        metrics = HardwareMetrics(cpu_temp=75.0, _populated={'cpu_temp'})
        state = LEDState(temp_source='cpu')
        engine = _make_engine(state, metrics)
        colors = engine._tick_temp_linked_for(3)
        assert colors == [(255, 0, 0)] * 3
        mock_ce.color_for_value.assert_called_once_with(
            75.0, mock_ce.TEMP_GRADIENT)

    @patch('trcc.legacy.core.color.ColorEngine')
    def test_gpu_temp_source(self, mock_ce):
        mock_ce.color_for_value.return_value = (0, 0, 255)
        metrics = HardwareMetrics(gpu_temp=80.0, _populated={'gpu_temp'})
        state = LEDState(temp_source='gpu')
        engine = _make_engine(state, metrics)
        engine._tick_temp_linked_for(2)
        mock_ce.color_for_value.assert_called_once_with(
            80.0, mock_ce.TEMP_GRADIENT)


# =========================================================================
# Load-linked mode
# =========================================================================


class TestLoadLinkedMode:
    """Same ``_populated`` gating as temp-linked mode."""

    @patch('trcc.legacy.core.color.ColorEngine')
    def test_cpu_load(self, mock_ce):
        mock_ce.color_for_value.return_value = (0, 255, 0)
        metrics = HardwareMetrics(cpu_percent=45.0, _populated={'cpu_percent'})
        state = LEDState(load_source='cpu')
        engine = _make_engine(state, metrics)
        engine._tick_load_linked_for(2)
        mock_ce.color_for_value.assert_called_once_with(
            45.0, mock_ce.LOAD_GRADIENT)

    @patch('trcc.legacy.core.color.ColorEngine')
    def test_gpu_load(self, mock_ce):
        mock_ce.color_for_value.return_value = (128, 128, 0)
        metrics = HardwareMetrics(gpu_usage=90.0, _populated={'gpu_usage'})
        state = LEDState(load_source='gpu')
        engine = _make_engine(state, metrics)
        engine._tick_load_linked_for(1)
        mock_ce.color_for_value.assert_called_once_with(
            90.0, mock_ce.LOAD_GRADIENT)


# =========================================================================
# Test mode
# =========================================================================


class TestTestMode:
    def test_cycles_4_colors(self):
        state = LEDState(led_count=3, test_timer=0, test_color=0)
        engine = _make_engine(state)
        # Color 0 for first 10 ticks
        colors = engine._tick_test_mode()
        assert colors == [(1, 1, 1)] * 3

    def test_color_advances_every_10_ticks(self):
        state = LEDState(led_count=2, test_timer=9, test_color=0)
        engine = _make_engine(state)
        colors = engine._tick_test_mode()
        # Timer was 9, incremented to 10 → reset, color advances to 1
        assert state.test_color == 1
        assert state.test_timer == 0
        assert colors == [(1, 0, 0)] * 2

    def test_wraps_around_after_4_colors(self):
        state = LEDState(led_count=1, test_timer=9, test_color=3)
        engine = _make_engine(state)
        engine._tick_test_mode()
        assert state.test_color == 0  # Wraps back to white


# =========================================================================
# Multi-zone
# =========================================================================


class TestMultiZone:
    def test_places_colors_at_mapped_indices(self):
        state = LEDState(
            led_count=6,
            zones=[
                LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0),
                             brightness=100, on=True),
                LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0),
                             brightness=100, on=True),
            ],
        )
        engine = _make_engine(state)
        zone_map = ((0, 1, 2), (3, 4, 5))
        colors = engine._tick_multi_zone(zone_map)
        assert colors[:3] == [(255, 0, 0)] * 3
        assert colors[3:] == [(0, 255, 0)] * 3

    def test_disabled_zone_stays_black(self):
        state = LEDState(
            led_count=4,
            zones=[
                LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0),
                             brightness=100, on=False),
                LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0),
                             brightness=100, on=True),
            ],
        )
        engine = _make_engine(state)
        zone_map = ((0, 1), (2, 3))
        colors = engine._tick_multi_zone(zone_map)
        assert colors[:2] == [(0, 0, 0)] * 2  # Zone 0 off
        assert colors[2:] == [(0, 255, 0)] * 2  # Zone 1 on

    def test_brightness_scaling(self):
        state = LEDState(
            led_count=2,
            zones=[
                LEDZoneState(mode=LEDMode.STATIC, color=(200, 100, 50),
                             brightness=50, on=True),
            ],
        )
        engine = _make_engine(state)
        zone_map = ((0, 1),)
        colors = engine._tick_multi_zone(zone_map)
        assert colors[0] == (100, 50, 25)

    def test_zone_map_exceeds_zone_count(self):
        """Zone map has more zones than state — stops at state.zones length."""
        state = LEDState(
            led_count=4,
            zones=[
                LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0),
                             brightness=100, on=True),
            ],
        )
        engine = _make_engine(state)
        zone_map = ((0, 1), (2, 3))  # 2 zones in map, 1 in state
        colors = engine._tick_multi_zone(zone_map)
        assert colors[:2] == [(255, 0, 0)] * 2
        assert colors[2:] == [(0, 0, 0)] * 2

    def test_out_of_bounds_index_skipped(self):
        """LED index >= total is silently skipped."""
        state = LEDState(
            led_count=2,
            zones=[
                LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0),
                             brightness=100, on=True),
            ],
        )
        engine = _make_engine(state)
        zone_map = ((0, 1, 99),)  # Index 99 is out of bounds
        colors = engine._tick_multi_zone(zone_map)
        assert len(colors) == 2
        assert colors == [(255, 0, 0)] * 2

    @patch('trcc.legacy.core.color.ColorEngine.color_for_value')
    def test_per_zone_temp_linked_uses_zone_metric_source(self, mock_cfv):
        """PA120-style: zone 0 uses CPU temp, zone 2 uses GPU temp."""
        # Return different colors for different temp values
        mock_cfv.side_effect = lambda val, _: (val, 0, 0)
        state = LEDState(
            led_count=4,
            zones=[
                LEDZoneState(mode=LEDMode.TEMP_LINKED, brightness=100, on=True),
                LEDZoneState(mode=LEDMode.TEMP_LINKED, brightness=100, on=True),
            ],
        )
        metrics = HardwareMetrics(
            cpu_temp=50.0, gpu_temp=80.0,
            _populated={'cpu_temp', 'gpu_temp'},
        )
        engine = _make_engine(state, metrics)
        zone_map = ((0, 1), (2, 3))
        metric_sources = (("cpu", "temp"), ("gpu", "temp"))
        colors = engine._tick_multi_zone(zone_map, metric_sources)
        # Zone 0 reads cpu_temp=50, zone 1 reads gpu_temp=80
        assert colors[0] == (50, 0, 0)
        assert colors[2] == (80, 0, 0)

    @patch('trcc.legacy.core.color.ColorEngine.color_for_value')
    def test_per_zone_temp_linked_fallback_to_global(self, mock_cfv):
        """Without metric_sources, all zones use global temp_source."""
        mock_cfv.side_effect = lambda val, _: (val, 0, 0)
        state = LEDState(
            led_count=4, temp_source="gpu",
            zones=[
                LEDZoneState(mode=LEDMode.TEMP_LINKED, brightness=100, on=True),
                LEDZoneState(mode=LEDMode.TEMP_LINKED, brightness=100, on=True),
            ],
        )
        metrics = HardwareMetrics(
            cpu_temp=50.0, gpu_temp=80.0,
            _populated={'cpu_temp', 'gpu_temp'},
        )
        engine = _make_engine(state, metrics)
        zone_map = ((0, 1), (2, 3))
        colors = engine._tick_multi_zone(zone_map)  # No metric_sources
        # Both zones use global gpu temp=80
        assert colors[0] == (80, 0, 0)
        assert colors[2] == (80, 0, 0)


# =========================================================================
# _next_sync_zone()
# =========================================================================


class TestNextSyncZone:
    def test_finds_next_enabled(self):
        state = LEDState(zone_count=4)
        state.zone_sync_zones = [True, False, True, False]
        engine = _make_engine(state)
        assert engine._next_sync_zone(0) == 2

    def test_wraps_around(self):
        state = LEDState(zone_count=3)
        state.zone_sync_zones = [True, False, False]
        engine = _make_engine(state)
        assert engine._next_sync_zone(0) == 0  # Only zone 0 enabled

    def test_skips_disabled_zones(self):
        state = LEDState(zone_count=4)
        state.zone_sync_zones = [False, False, False, True]
        engine = _make_engine(state)
        assert engine._next_sync_zone(0) == 3

    def test_all_disabled_returns_zero(self):
        state = LEDState(zone_count=3)
        state.zone_sync_zones = [False, False, False]
        engine = _make_engine(state)
        assert engine._next_sync_zone(0) == 0


# =========================================================================
# Dispatch
# =========================================================================


class TestDispatch:
    def test_unknown_mode_returns_black(self):
        engine = _make_engine()
        # Use an invalid mode value via a mock
        colors = engine._tick_single_mode(MagicMock(value=99), (255, 0, 0), 3)
        assert colors == [(0, 0, 0)] * 3

    def test_metrics_property(self):
        m1 = HardwareMetrics(cpu_temp=50)
        m2 = HardwareMetrics(cpu_temp=80)
        engine = _make_engine(metrics=m1)
        assert engine.metrics.cpu_temp == 50
        engine.metrics = m2
        assert engine.metrics.cpu_temp == 80


# ── Decoration ring (LF25 sub=1) ─────────────────────────────────

class TestDecorationRing:
    """Ring LED computation for sub-variant devices (e.g. LF25)."""

    def test_static_ring_appended(self):
        """Static mode appends ring_count LEDs with same color."""
        state = LEDState(ring_count=77)
        engine = _make_engine(state)
        colors = engine._tick_single_mode(LEDMode.STATIC, (255, 0, 0), 23)
        assert len(colors) == 23 + 77
        # Segments = static color
        assert all(c == (255, 0, 0) for c in colors[:23])
        # Ring = same color
        assert all(c == (255, 0, 0) for c in colors[23:])

    def test_breathing_ring_matches_segments(self):
        """Breathing mode: ring gets same pulsed color as segments."""
        state = LEDState(ring_count=77)
        engine = _make_engine(state)
        colors = engine._tick_single_mode(LEDMode.BREATHING, (0, 255, 0), 23)
        assert len(colors) == 100
        # Ring color should match segment color (same pulse phase)
        assert colors[23] == colors[0]
        assert colors[99] == colors[0]

    def test_colorful_ring_matches_segments(self):
        """Colorful mode: ring gets same gradient color as segments."""
        state = LEDState(ring_count=77)
        engine = _make_engine(state)
        colors = engine._tick_single_mode(LEDMode.COLORFUL, (0, 0, 0), 23)
        assert len(colors) == 100
        assert colors[23] == colors[0]

    def test_rainbow_ring_per_led_phase(self):
        """Rainbow mode: each ring LED gets a unique phase offset."""
        state = LEDState(ring_count=77)
        engine = _make_engine(state)
        colors = engine._tick_single_mode(LEDMode.RAINBOW, (0, 0, 0), 23)
        assert len(colors) == 100
        ring = colors[23:]
        # Not all the same — per-LED phase offsets
        assert len(set(ring)) > 1

    def test_rainbow_ring_reversed(self):
        """Rainbow ring is filled in reverse order (C# 77-j-1)."""
        state = LEDState(ring_count=10, rgb_timer=0)
        engine = _make_engine(state)
        colors = engine._tick_single_mode(LEDMode.RAINBOW, (0, 0, 0), 5)
        ring = colors[5:]
        # First and last should differ (reversed gradient)
        assert ring[0] != ring[-1]

    def test_no_ring_when_zero(self):
        """ring_count=0 (default) returns only segment colors."""
        state = LEDState(ring_count=0)
        engine = _make_engine(state)
        colors = engine._tick_single_mode(LEDMode.STATIC, (255, 0, 0), 23)
        assert len(colors) == 23

    def test_temp_linked_ring_uniform(self):
        """Temp-linked mode: ring gets same temp color as segments."""
        state = LEDState(ring_count=77)
        m = HardwareMetrics(cpu_temp=60)
        engine = _make_engine(state, m)
        colors = engine._tick_single_mode(LEDMode.TEMP_LINKED, (0, 0, 0), 23)
        assert len(colors) == 100
        assert colors[23] == colors[0]

    def test_load_linked_ring_uniform(self):
        """Load-linked mode: ring gets same load color as segments."""
        state = LEDState(ring_count=77)
        m = HardwareMetrics(cpu_percent=75)
        engine = _make_engine(state, m)
        colors = engine._tick_single_mode(LEDMode.LOAD_LINKED, (0, 0, 0), 23)
        assert len(colors) == 100
        assert colors[23] == colors[0]
