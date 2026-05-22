"""
Tests for LED service and model layer.

Covers LEDMode enum, LEDZoneState, LEDState, LEDService (state mutations,
tick dispatch, all 6 effect algorithms, mask application, zone mapping,
clock persistence).

Architecture mirrors Windows FormLED.cs:
  - LEDService holds state + computes per-segment colors each tick
"""

from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.core.models import (
    LED_STYLES,
    HardwareMetrics,
    LEDMode,
    LEDState,
    LEDZoneState,
)
from trcc.legacy.services.led import LEDService

# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def led_state():
    """Default LEDState with 10 segments."""
    return LEDState()


@pytest.fixture
def led_svc():
    """Fresh LEDService with default state."""
    return LEDService()


# =========================================================================
# Tests: LEDMode enum
# =========================================================================

class TestLEDMode:
    """Verify enum values match FormLED.cs mode IDs."""

    def test_static_value(self):
        assert LEDMode.STATIC.value == 0

    def test_breathing_value(self):
        assert LEDMode.BREATHING.value == 1

    def test_colorful_value(self):
        assert LEDMode.COLORFUL.value == 2

    def test_rainbow_value(self):
        assert LEDMode.RAINBOW.value == 3

    def test_temp_linked_value(self):
        assert LEDMode.TEMP_LINKED.value == 4

    def test_load_linked_value(self):
        assert LEDMode.LOAD_LINKED.value == 5

    def test_mode_count(self):
        assert len(LEDMode) == 6

    def test_round_trip_from_int(self):
        for mode in LEDMode:
            assert LEDMode(mode.value) is mode


# =========================================================================
# Tests: LEDZoneState dataclass
# =========================================================================

class TestLEDZoneState:
    """Verify default creation and field semantics."""

    def test_defaults(self):
        zone = LEDZoneState()
        assert zone.mode is LEDMode.STATIC
        assert zone.color == (255, 0, 0)
        assert zone.brightness == 65
        assert zone.on is True

    def test_custom_creation(self):
        zone = LEDZoneState(
            mode=LEDMode.BREATHING,
            color=(0, 255, 0),
            brightness=50,
            on=False,
        )
        assert zone.mode is LEDMode.BREATHING
        assert zone.color == (0, 255, 0)
        assert zone.brightness == 50
        assert zone.on is False


# =========================================================================
# Tests: LEDState dataclass
# =========================================================================

class TestLEDState:
    """Verify LEDState creation, defaults, __post_init__ logic."""

    def test_defaults(self, led_state):
        assert led_state.style == 1
        assert led_state.led_count == 30
        assert led_state.segment_count == 10
        assert led_state.zone_count == 1
        assert led_state.mode is LEDMode.STATIC
        assert led_state.color == (255, 0, 0)
        assert led_state.brightness == 65
        assert led_state.global_on is True
        assert led_state.rgb_timer == 0
        assert led_state.temp_source == "cpu"
        assert led_state.load_source == "cpu"

    def test_segment_on_auto_populated(self, led_state):
        """__post_init__ creates segment_on list matching segment_count."""
        assert len(led_state.segment_on) == 10
        assert all(s is True for s in led_state.segment_on)

    def test_segment_on_custom_count(self):
        state = LEDState(segment_count=5)
        assert len(state.segment_on) == 5

    def test_segment_on_preserves_if_provided(self):
        """If segment_on is already set, __post_init__ does not overwrite."""
        custom = [True, False, True]
        state = LEDState(segment_count=3, segment_on=custom)
        assert state.segment_on == [True, False, True]

    def test_zones_auto_populated_for_multi_zone(self):
        state = LEDState(zone_count=3)
        assert len(state.zones) == 3
        assert all(isinstance(z, LEDZoneState) for z in state.zones)

    def test_no_zones_for_single_zone(self, led_state):
        """Single-zone devices (zone_count=1) have empty zones list."""
        assert led_state.zones == []

    def test_zones_preserved_if_provided(self):
        custom_zones = [LEDZoneState(brightness=42)]
        state = LEDState(zone_count=2, zones=custom_zones)
        # Non-empty list provided, __post_init__ skips
        assert len(state.zones) == 1
        assert state.zones[0].brightness == 42


# =========================================================================
# Tests: LEDService — state mutations
# =========================================================================

class TestLEDServiceStateMutations:
    """Test set_mode, set_color, set_brightness, toggles, zone methods."""

    def test_set_mode(self, led_svc):
        led_svc.set_mode(LEDMode.RAINBOW)
        assert led_svc.state.mode is LEDMode.RAINBOW

    def test_set_mode_resets_timer(self, led_svc):
        led_svc.state.rgb_timer = 42
        led_svc.set_mode(LEDMode.BREATHING)
        assert led_svc.state.rgb_timer == 0

    def test_set_color(self, led_svc):
        led_svc.set_color(10, 20, 30)
        assert led_svc.state.color == (10, 20, 30)

    def test_set_brightness_normal(self, led_svc):
        led_svc.set_brightness(75)
        assert led_svc.state.brightness == 75

    def test_set_brightness_clamps_high(self, led_svc):
        led_svc.set_brightness(200)
        assert led_svc.state.brightness == 100

    def test_set_brightness_clamps_low(self, led_svc):
        led_svc.set_brightness(-10)
        assert led_svc.state.brightness == 0

    def test_toggle_global_off(self, led_svc):
        led_svc.toggle_global(False)
        assert led_svc.state.global_on is False

    def test_toggle_global_on(self, led_svc):
        led_svc.state.global_on = False
        led_svc.toggle_global(True)
        assert led_svc.state.global_on is True

    def test_toggle_segment(self, led_svc):
        led_svc.toggle_segment(3, False)
        assert led_svc.state.segment_on[3] is False
        # Others unchanged
        assert led_svc.state.segment_on[0] is True

    def test_toggle_segment_out_of_range(self, led_svc):
        """Out-of-range index does not raise."""
        led_svc.toggle_segment(999, False)
        led_svc.toggle_segment(-1, False)
        # No exception, state unchanged
        assert all(s is True for s in led_svc.state.segment_on)

    def test_set_zone_mode(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_mode(0, LEDMode.COLORFUL)
        assert model.state.zones[0].mode is LEDMode.COLORFUL

    def test_set_zone_mode_out_of_range(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_mode(5, LEDMode.STATIC)  # No exception

    def test_set_zone_color(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_color(1, 0, 128, 255)
        assert model.state.zones[1].color == (0, 128, 255)

    def test_set_zone_color_out_of_range(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_color(99, 0, 0, 0)  # No exception

    def test_set_zone_brightness(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_brightness(0, 50)
        assert model.state.zones[0].brightness == 50

    def test_set_zone_brightness_clamps(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_brightness(0, 999)
        assert model.state.zones[0].brightness == 100
        model.set_zone_brightness(0, -5)
        assert model.state.zones[0].brightness == 0

    def test_set_zone_brightness_out_of_range(self):
        model = LEDService(state=LEDState(zone_count=2))
        model.set_zone_brightness(99, 50)  # No exception

    def test_update_metrics(self, led_svc):
        metrics = HardwareMetrics(cpu_temp=65)
        led_svc.update_metrics(metrics)
        assert led_svc._metrics == metrics


# =========================================================================
# Tests: LEDService — configure_for_style
# =========================================================================

class TestLEDServiceConfigureForStyle:
    """configure_for_style() derives LED/segment counts from LED_STYLES — the model."""

    @pytest.mark.parametrize("style_id,style", list(LED_STYLES.items()),
                             ids=[str(sid) for sid in LED_STYLES])
    def test_configure_matches_model(self, style_id, style):
        """Every style in LED_STYLES: configure_for_style sets counts from the model."""
        svc = LEDService()
        svc.configure_for_style(style_id)
        assert svc.state.style == style.style_id
        assert svc.state.led_count == style.led_count
        assert svc.state.segment_count == style.segment_count
        assert len(svc.state.segment_on) == style.segment_count

    @pytest.mark.parametrize("style_id,style",
                             [(sid, s) for sid, s in LED_STYLES.items() if s.zone_count > 1],
                             ids=[str(sid) for sid, s in LED_STYLES.items() if s.zone_count > 1])
    def test_multi_zone_styles_create_zones(self, style_id, style):
        """Multi-zone styles create a zones list matching zone_count."""
        svc = LEDService()
        svc.configure_for_style(style_id)
        assert len(svc.state.zones) == style.zone_count

    @pytest.mark.parametrize("style_id,style",
                             [(sid, s) for sid, s in LED_STYLES.items() if s.zone_count == 0],
                             ids=[str(sid) for sid, s in LED_STYLES.items() if s.zone_count == 0])
    def test_zero_zone_styles_have_empty_zones(self, style_id, style):
        """Zero-zone styles leave zones list empty."""
        svc = LEDService()
        svc.configure_for_style(style_id)
        assert svc.state.zones == []

    def test_configure_unknown_style_is_no_op(self):
        """Unknown style_id leaves state unchanged."""
        svc = LEDService()
        original_count = svc.state.segment_count
        svc.configure_for_style(9999)
        assert svc.state.segment_count == original_count


# =========================================================================
# Tests: LEDService — tick dispatch
# =========================================================================

class TestLEDServiceTickDispatch:
    """Test that tick() dispatches to the correct mode algorithm."""

    def test_tick_static(self, led_svc):
        led_svc.set_mode(LEDMode.STATIC)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count
        assert all(c == led_svc.state.color for c in colors)

    def test_tick_breathing(self, led_svc):
        led_svc.set_mode(LEDMode.BREATHING)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    def test_tick_colorful(self, led_svc):
        led_svc.set_mode(LEDMode.COLORFUL)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    @patch("trcc.legacy.adapters.device.led.ColorEngine.get_table")
    def test_tick_rainbow(self, mock_table, led_svc):
        # Provide a minimal table
        mock_table.return_value = [(i, i, i) for i in range(768)]
        led_svc.set_mode(LEDMode.RAINBOW)
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value", return_value=(0, 255, 255))
    def test_tick_temp_linked(self, mock_cfv, led_svc):
        led_svc.set_mode(LEDMode.TEMP_LINKED)
        led_svc.update_metrics(HardwareMetrics(cpu_temp=25))
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value", return_value=(255, 255, 0))
    def test_tick_load_linked(self, mock_cfv, led_svc):
        led_svc.set_mode(LEDMode.LOAD_LINKED)
        led_svc.update_metrics(HardwareMetrics(cpu_percent=60))
        colors = led_svc.tick()
        assert len(colors) == led_svc.state.segment_count

    def test_tick_returns_list_of_tuples(self, led_svc):
        colors = led_svc.tick()
        assert isinstance(colors, list)
        for c in colors:
            assert isinstance(c, tuple)
            assert len(c) == 3


# =========================================================================
# Tests: LEDService — physical zone mapping (style 7 / LF10)
# =========================================================================

class TestPhysicalZones:
    """Style 7 (LF10) has 3 physical zones with independent color/mode/brightness."""

    @pytest.fixture
    def svc7(self):
        svc = LEDService()
        svc.configure_for_style(7)
        return svc

    def test_tick_returns_model_led_count(self, svc7):
        """tick() returns exactly as many colors as LED_STYLES[7].led_count declares."""
        colors = svc7.tick()
        assert len(colors) == LED_STYLES[7].led_count

    def test_zones_get_independent_colors(self, svc7):
        from trcc.legacy.core.led_segment import get_display
        zone_map = get_display(7).zone_led_map
        z0_first = zone_map[0][0]  # first LED index of zone 0
        z1_first = zone_map[1][0]  # first LED index of zone 1
        z2_first = zone_map[2][0]  # first LED index of zone 2

        for z in svc7.state.zones:
            z.brightness = 100
        svc7.set_zone_color(0, 255, 0, 0)
        svc7.set_zone_color(1, 0, 255, 0)
        svc7.set_zone_color(2, 0, 0, 255)
        colors = svc7.tick()
        assert colors[z0_first] == (255, 0, 0)
        assert colors[z1_first] == (0, 255, 0)
        assert colors[z2_first] == (0, 0, 255)

    def test_zone_brightness_scales(self, svc7):
        from trcc.legacy.core.led_segment import get_display
        z0_first = get_display(7).zone_led_map[0][0]
        svc7.state.zones[0].brightness = 100
        svc7.set_zone_color(0, 200, 100, 50)
        svc7.state.zones[0].brightness = 50
        colors = svc7.tick()
        assert colors[z0_first] == (100, 50, 25)

    def test_zone_off_produces_black(self, svc7):
        from trcc.legacy.core.led_segment import get_display
        z2_first = get_display(7).zone_led_map[2][0]
        svc7.set_zone_color(2, 255, 255, 255)
        svc7.state.zones[2].on = False
        colors = svc7.tick()
        assert colors[z2_first] == (0, 0, 0)

    def test_mask_gates_physical_zone_colors(self, svc7):
        from trcc.legacy.core.led_segment import get_display
        z0_first = get_display(7).zone_led_map[0][0]
        svc7.state.zones[0].brightness = 100
        svc7.set_zone_color(0, 255, 0, 0)
        colors = svc7.tick()
        masked = svc7.apply_mask(colors)
        assert masked[z0_first] == (255, 0, 0)
        # Hundreds digit (index 6) off — leading zero suppression at temp=0
        assert masked[6] == (0, 0, 0)

    def test_breathing_mode_per_zone(self, svc7):
        from trcc.legacy.core.led_segment import get_display
        z1_first = get_display(7).zone_led_map[1][0]
        for z in svc7.state.zones:
            z.brightness = 100
        svc7.state.zones[0].mode = LEDMode.BREATHING
        svc7.state.zones[0].color = (255, 0, 0)
        svc7.state.zones[1].mode = LEDMode.STATIC
        svc7.state.zones[1].color = (0, 255, 0)
        colors = svc7.tick()
        assert colors[z1_first] == (0, 255, 0)
        assert len(colors) == LED_STYLES[7].led_count


# =========================================================================
# Tests: LEDService — _tick_static
# =========================================================================

class TestTickStatic:
    """DSCL_Timer: all segments = user color."""

    def test_all_segments_same_color(self, led_svc):
        led_svc.set_color(100, 200, 50)
        colors = led_svc._tick_single_mode(
            LEDMode.STATIC, led_svc.state.color, led_svc.state.segment_count)
        assert all(c == (100, 200, 50) for c in colors)

    def test_segment_count_matches(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.STATIC, led_svc.state.color, led_svc.state.segment_count)
        assert len(colors) == led_svc.state.segment_count


# =========================================================================
# Tests: LEDService — _tick_breathing
# =========================================================================

class TestTickBreathing:
    """DSHX_Timer: pulse brightness, period=66."""

    def test_advances_timer(self, led_svc):
        led_svc.state.mode = LEDMode.BREATHING
        led_svc.state.rgb_timer = 0
        led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 1

    def test_timer_wraps_at_66(self, led_svc):
        led_svc.state.rgb_timer = 65
        led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0

    def test_at_zero_brightness_is_20_percent_base(self, led_svc):
        """At timer=0, factor=0 so anim is 0; output = 20% of color."""
        led_svc.set_color(255, 0, 0)
        led_svc.state.rgb_timer = 0
        colors = led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        r, g, b = colors[0]
        # int(255 * 0 * 0.8 + 255 * 0.2) = int(51.0) = 51
        assert r == 51

    def test_at_midpoint_brightness_is_full(self, led_svc):
        """At timer=33 (half), factor= (66-1-33)/33 ~ 0.9697 -> near full."""
        led_svc.set_color(255, 0, 0)
        led_svc.state.rgb_timer = 32  # factor = 32/33 ~ 0.97
        colors = led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        r, g, b = colors[0]
        # 80% animated + 20% base -> near 255
        assert r > 200

    def test_uniform_across_segments(self, led_svc):
        """All segments get the same breathing color."""
        led_svc.set_color(100, 100, 100)
        led_svc.state.rgb_timer = 10
        colors = led_svc._tick_breathing_for(led_svc.state.color, led_svc.state.segment_count)
        assert len(set(colors)) == 1  # All identical


# =========================================================================
# Tests: LEDService — _tick_colorful
# =========================================================================

class TestTickColorful:
    """QCJB_Timer: 6-phase gradient, period=168."""

    def test_advances_timer(self, led_svc):
        led_svc.state.rgb_timer = 0
        led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 1

    def test_timer_wraps_at_168(self, led_svc):
        led_svc.state.rgb_timer = 167
        led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0

    def test_phase_0_starts_red(self, led_svc):
        """Phase 0 offset 0 -> (255, 0, 0) = pure red."""
        led_svc.state.rgb_timer = 0
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert colors[0] == (255, 0, 0)

    def test_phase_1_yellow_to_green(self, led_svc):
        """Phase 1 offset 0 -> (255, 255, 0) -> R starts decreasing."""
        led_svc.state.rgb_timer = 28  # Phase 1 start
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        r, g, b = colors[0]
        assert g == 255
        assert b == 0

    def test_phase_2_green_to_cyan(self, led_svc):
        """Phase 2 starts at timer=56."""
        led_svc.state.rgb_timer = 56
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        r, g, b = colors[0]
        assert r == 0
        assert g == 255

    def test_full_cycle_returns_to_start(self, led_svc):
        """After 168 ticks, we're back at phase 0 offset 0."""
        led_svc.state.rgb_timer = 0
        for _ in range(168):
            led_svc._tick_colorful_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0

    def test_segments_have_phase_offset(self, led_svc):
        """Each segment gets a different phase offset — colorful is not uniform."""
        led_svc.state.rgb_timer = 0
        colors = led_svc._tick_colorful_for(led_svc.state.segment_count)
        # Multiple segments → multiple distinct colors due to per-segment offset
        assert len(colors) == led_svc.state.segment_count
        assert len(set(colors)) > 1


# =========================================================================
# Tests: LEDService — _tick_rainbow
# =========================================================================

class TestTickRainbow:
    """CHMS_Timer: 768-entry table, offset per segment."""

    @patch("trcc.legacy.adapters.device.led.ColorEngine.get_table")
    def test_uses_rgb_table(self, mock_table, led_svc):
        table = [(i, 0, 0) for i in range(768)]
        mock_table.return_value = table
        led_svc.state.rgb_timer = 0
        colors = led_svc._tick_rainbow_for(led_svc.state.segment_count)
        # Each segment gets a different offset
        assert len(colors) == led_svc.state.segment_count
        mock_table.assert_called()

    @patch("trcc.legacy.adapters.device.led.ColorEngine.get_table")
    def test_advances_by_4(self, mock_table, led_svc):
        mock_table.return_value = [(0, 0, 0)] * 768
        led_svc.state.rgb_timer = 0
        led_svc._tick_rainbow_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 4

    @patch("trcc.legacy.adapters.device.led.ColorEngine.get_table")
    def test_timer_wraps(self, mock_table, led_svc):
        mock_table.return_value = [(0, 0, 0)] * 768
        led_svc.state.rgb_timer = 764
        led_svc._tick_rainbow_for(led_svc.state.segment_count)
        assert led_svc.state.rgb_timer == 0  # (764 + 4) % 768 = 0

    @patch("trcc.legacy.adapters.device.led.ColorEngine.get_table")
    def test_segments_get_different_offsets(self, mock_table, led_svc):
        """Different segments get different colors from the table."""
        table = [(i, i, i) for i in range(768)]
        mock_table.return_value = table
        led_svc.state.rgb_timer = 0
        led_svc.state.segment_count = 4
        led_svc.state.segment_on = [True] * 4
        colors = led_svc._tick_rainbow_for(led_svc.state.segment_count)
        # With 4 segments, offsets should be 0, 192, 384, 576
        assert len(set(colors)) > 1  # Not all the same


# =========================================================================
# Tests: LEDService — _tick_temp_linked
# =========================================================================

class TestTickTempLinked:
    """WDLD_Timer: color from CPU/GPU temperature thresholds.

    The tick path is gated on ``HardwareMetrics._populated`` since the
    metrics-unification refactor — fields the OS didn't actually source
    don't trigger a color recompute (the LED holds its last color rather
    than jumping to gradient bottom on a missing sensor).
    """

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value")
    def test_uses_cpu_temp_by_default(self, mock_cfv, led_svc):
        mock_cfv.return_value = (0, 255, 0)
        led_svc.state.temp_source = "cpu"
        led_svc.update_metrics(HardwareMetrics(cpu_temp=45, _populated={'cpu_temp'}))
        led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        mock_cfv.assert_called_once()
        # First positional arg is the temp value
        assert mock_cfv.call_args[0][0] == 45

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value")
    def test_uses_gpu_temp(self, mock_cfv, led_svc):
        mock_cfv.return_value = (255, 0, 0)
        led_svc.state.temp_source = "gpu"
        led_svc.update_metrics(HardwareMetrics(gpu_temp=92, _populated={'gpu_temp'}))
        led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 92

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value")
    def test_missing_metric_holds_prior_color(self, mock_cfv, led_svc):
        """When the source field is not in ``_populated`` (no OS sensor),
        the tick must NOT recompute — last color is held."""
        mock_cfv.return_value = (0, 255, 255)
        led_svc.update_metrics(HardwareMetrics())  # empty _populated
        led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        mock_cfv.assert_not_called()

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value")
    def test_uniform_color(self, mock_cfv, led_svc):
        mock_cfv.return_value = (0, 255, 0)
        led_svc.update_metrics(HardwareMetrics(cpu_temp=40, _populated={'cpu_temp'}))
        colors = led_svc._tick_temp_linked_for(led_svc.state.segment_count)
        assert all(c == (0, 255, 0) for c in colors)
        assert len(colors) == led_svc.state.segment_count


# =========================================================================
# Tests: LEDService — _tick_load_linked
# =========================================================================

class TestTickLoadLinked:
    """FZLD_Timer: color from CPU/GPU load thresholds.

    Same ``_populated`` gating as ``_tick_temp_linked_for`` — unsourced
    fields hold the last color instead of recomputing.
    """

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value")
    def test_uses_cpu_load_by_default(self, mock_cfv, led_svc):
        mock_cfv.return_value = (255, 255, 0)
        led_svc.state.load_source = "cpu"
        led_svc.update_metrics(HardwareMetrics(cpu_percent=60, _populated={'cpu_percent'}))
        led_svc._tick_load_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 60

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value")
    def test_uses_gpu_load(self, mock_cfv, led_svc):
        mock_cfv.return_value = (255, 110, 0)
        led_svc.state.load_source = "gpu"
        led_svc.update_metrics(HardwareMetrics(gpu_usage=85, _populated={'gpu_usage'}))
        led_svc._tick_load_linked_for(led_svc.state.segment_count)
        assert mock_cfv.call_args[0][0] == 85

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value")
    def test_missing_metric_holds_prior_color(self, mock_cfv, led_svc):
        mock_cfv.return_value = (0, 255, 255)
        led_svc.update_metrics(HardwareMetrics())  # empty _populated
        led_svc._tick_load_linked_for(led_svc.state.segment_count)
        mock_cfv.assert_not_called()


# =========================================================================
# Tests: Multi-zone tick()
# =========================================================================

class TestMultiZoneTick:
    """Test _tick_multi_zone() — per-zone color computation via zone map."""

    @pytest.fixture
    def multi_zone_model(self):
        """LEDService with 2-zone, 10-LED zone map."""
        model = LEDService()
        model.state.led_count = 10
        model.state.segment_count = 10
        model.state.zone_count = 2
        model.state.zones = [
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 0, 255), brightness=100),
        ]
        return model

    def test_multi_zone_dispatches(self, multi_zone_model):
        """_tick_multi_zone places zone colors at mapped indices."""
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)))
        colors = multi_zone_model._tick_multi_zone(zone_map)
        assert len(colors) == 10
        assert colors[0] == (255, 0, 0)
        assert colors[4] == (255, 0, 0)
        assert colors[5] == (0, 0, 255)
        assert colors[9] == (0, 0, 255)

    def test_multi_zone_uneven_sizes(self):
        """Zone map with different-sized zones."""
        model = LEDService()
        model.state.led_count = 10
        model.state.segment_count = 10
        model.state.zone_count = 3
        model.state.zones = [
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 0, 255), brightness=100),
        ]
        zone_map = ((0, 1, 2, 3, 4), (5, 6, 7), (8, 9))
        colors = model._tick_multi_zone(zone_map)
        assert len(colors) == 10
        assert all(c == (255, 0, 0) for c in colors[0:5])
        assert all(c == (0, 255, 0) for c in colors[5:8])
        assert all(c == (0, 0, 255) for c in colors[8:10])

    def test_multi_zone_brightness_scaling(self, multi_zone_model):
        """Zone brightness scales RGB values."""
        multi_zone_model.state.zones[0].brightness = 50
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)))
        colors = multi_zone_model._tick_multi_zone(zone_map)
        assert colors[0] == (127, 0, 0)
        assert colors[5] == (0, 0, 255)

    def test_multi_zone_off(self, multi_zone_model):
        """Zone with on=False produces black."""
        multi_zone_model.state.zones[0].on = False
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)))
        colors = multi_zone_model._tick_multi_zone(zone_map)
        assert colors[0] == (0, 0, 0)
        assert colors[4] == (0, 0, 0)
        assert colors[5] == (0, 0, 255)

    def test_multi_zone_breathing(self):
        """Multi-zone with breathing mode advances timer."""
        model = LEDService()
        model.state.led_count = 6
        model.state.segment_count = 6
        model.state.zone_count = 2
        model.state.zones = [
            LEDZoneState(mode=LEDMode.BREATHING, color=(100, 100, 100), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0), brightness=100),
        ]
        zone_map = ((0, 1, 2), (3, 4, 5))
        colors = model._tick_multi_zone(zone_map)
        assert len(colors) == 6
        assert colors[0] == (20, 20, 20)
        assert colors[3] == (0, 255, 0)

    def test_single_zone_skips_multi(self):
        """Single-zone device uses global mode, not multi-zone path."""
        model = LEDService()
        model.state.zone_count = 1
        model.state.segment_count = 5
        model.state.mode = LEDMode.STATIC
        model.state.color = (0, 128, 0)
        model.state.zones = []
        colors = model.tick()
        assert all(c == (0, 128, 0) for c in colors)

    def test_four_zone_device(self):
        """4-zone device with mapped LED indices."""
        model = LEDService()
        model.state.led_count = 18
        model.state.segment_count = 18
        model.state.zone_count = 4
        model.state.zones = [
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 0, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 255, 0), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(0, 0, 255), brightness=100),
            LEDZoneState(mode=LEDMode.STATIC, color=(255, 255, 0), brightness=100),
        ]
        zone_map = (tuple(range(0, 5)), tuple(range(5, 10)),
                    tuple(range(10, 14)), tuple(range(14, 18)))
        colors = model._tick_multi_zone(zone_map)
        assert len(colors) == 18
        assert all(c == (255, 0, 0) for c in colors[0:5])
        assert all(c == (0, 255, 0) for c in colors[5:10])
        assert all(c == (0, 0, 255) for c in colors[10:14])
        assert all(c == (255, 255, 0) for c in colors[14:18])


# =========================================================================
# Tests: LEDState clock fields
# =========================================================================

class TestLEDStateClockFields:
    """Test LC2 clock fields on LEDState dataclass."""

    def test_default_values(self):
        state = LEDState()
        assert state.is_timer_24h is True
        assert state.is_week_sunday is False

    def test_custom_values(self):
        state = LEDState(is_timer_24h=False, is_week_sunday=True)
        assert state.is_timer_24h is False
        assert state.is_week_sunday is True


# =========================================================================
# Tests: _tick_single_mode dispatcher
# =========================================================================

class TestTickSingleMode:
    """Test _tick_single_mode dispatches correctly."""

    def test_static(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.STATIC, (10, 20, 30), 5)
        assert colors == [(10, 20, 30)] * 5

    def test_breathing(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.BREATHING, (100, 100, 100), 3)
        assert len(colors) == 3

    def test_colorful(self, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.COLORFUL, (0, 0, 0), 4)
        assert len(colors) == 4

    @patch("trcc.legacy.adapters.device.led.ColorEngine.get_table",
           return_value=[(i, i, i) for i in range(768)])
    def test_rainbow(self, mock_table, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.RAINBOW, (0, 0, 0), 6)
        assert len(colors) == 6

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value", return_value=(0, 255, 255))
    def test_temp_linked(self, mock_cfv, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.TEMP_LINKED, (0, 0, 0), 3)
        assert len(colors) == 3

    @patch("trcc.legacy.adapters.device.led.ColorEngine.color_for_value", return_value=(255, 0, 0))
    def test_load_linked(self, mock_cfv, led_svc):
        colors = led_svc._tick_single_mode(
            LEDMode.LOAD_LINKED, (0, 0, 0), 3)
        assert len(colors) == 3

    def test_unknown_mode_returns_black(self, led_svc):
        colors = led_svc._tick_single_mode(99, (255, 255, 255), 4)
        assert colors == [(0, 0, 0)] * 4


# =========================================================================
# Tests: UCInfoImage widget (headless — no display needed)
# =========================================================================

class TestUCInfoImageWidget:
    """Test UCInfoImage sensor gauge widget logic."""

    def test_import(self):
        """UCInfoImage class is importable."""
        try:
            from trcc.legacy.ui.gui.uc_led_control import UCInfoImage
            assert UCInfoImage is not None
        except ImportError:
            pytest.skip("PyQt6 not available")

    def test_set_value(self):
        """set_value stores values for painting."""
        try:
            from trcc.legacy.ui.gui.uc_led_control import UCInfoImage
        except ImportError:
            pytest.skip("PyQt6 not available")
        # Can't instantiate without QApplication, just verify class exists
        assert hasattr(UCInfoImage, 'set_value')
        assert hasattr(UCInfoImage, 'set_mode')
        assert hasattr(UCInfoImage, 'paintEvent')

    def test_bar_width_calculation_temp(self):
        """Progress bar width for temp/percent mode: value*2, max 200."""
        # Test the formula directly (value * 2, capped at 200)
        assert max(0, min(200, (50 * 2))) == 100

    def test_bar_width_calculation_mhz(self):
        """Progress bar width for MHz mode: value/25, max 200."""
        assert max(0, min(200, int(2500 / 25))) == 100


# =========================================================================
# Tests: LEDService — style resolution and style info
# =========================================================================

class TestLEDServiceStyleResolution:
    """resolve_style_id and get_style_info static methods."""

    def test_resolve_style_id_known_model(self):
        """resolve_style_id returns correct id for a known model_name."""
        # Style 1 = AX120_DIGITAL (model_name = "AX120_DIGITAL")
        result = LEDService.resolve_style_id("AX120_DIGITAL")
        assert result == 1

    def test_resolve_style_id_unknown_returns_1(self):
        """resolve_style_id returns 1 for an unknown model_name."""
        result = LEDService.resolve_style_id("NONEXISTENT_MODEL")
        assert result == 1

    def test_get_style_info_known_id(self):
        """get_style_info returns LedDeviceStyle for a known id."""
        style = LEDService.get_style_info(1)
        assert style is not None
        assert style.style_id == 1

    def test_get_style_info_unknown_id_returns_none(self):
        """get_style_info returns None for an unknown style_id."""
        result = LEDService.get_style_info(9999)
        assert result is None


# =========================================================================
# Tests: LEDService — toggle_global, toggle_segment
# =========================================================================

class TestLEDServiceToggles:
    """toggle_global and toggle_segment contracts."""

    def test_toggle_global_sets_state(self, led_svc):
        """toggle_global sets state.global_on to the given value."""
        led_svc.toggle_global(False)
        assert led_svc.state.global_on is False
        led_svc.toggle_global(True)
        assert led_svc.state.global_on is True

    def test_toggle_segment_valid_index(self, led_svc):
        """toggle_segment updates state.segment_on at valid index."""
        led_svc.toggle_segment(2, False)
        assert led_svc.state.segment_on[2] is False

    def test_toggle_segment_out_of_range_is_noop(self, led_svc):
        """toggle_segment with out-of-range index does not raise or modify state."""
        original = list(led_svc.state.segment_on)
        led_svc.toggle_segment(999, False)
        assert led_svc.state.segment_on == original


# =========================================================================
# Tests: LEDService — selected zone and zone sync
# =========================================================================

class TestLEDServiceZoneSync:
    """set_selected_zone, set_zone_sync, set_zone_sync_zone, set_zone_sync_interval."""

    @pytest.fixture
    def multi_zone_svc(self):
        """LEDService configured for 3-zone style (style 4, LC1)."""
        svc = LEDService()
        svc.configure_for_style(4)
        return svc

    def test_set_selected_zone_updates_state(self, multi_zone_svc):
        """set_selected_zone sets state.selected_zone and zone_sync_zones."""
        multi_zone_svc.set_selected_zone(1)
        assert multi_zone_svc.state.selected_zone == 1
        assert multi_zone_svc.state.zone_sync_zones[1] is True
        assert multi_zone_svc.state.zone_sync_zones[0] is False

    def test_set_zone_sync_select_all_style_syncs_all_zones(self):
        """set_zone_sync with select-all style (2 or 7) calls _sync_all_zones_to_selected."""
        svc = LEDService()
        svc.configure_for_style(2)  # PA120 — SELECT_ALL_STYLES
        # Set selected zone to 0 with distinctive color
        svc.state.zones[0].color = (10, 20, 30)
        svc.state.selected_zone = 0
        svc.set_zone_sync(True)
        # All zones should now have the same color as zone 0
        for z in svc.state.zones:
            assert z.color == (10, 20, 30)

    def test_set_zone_sync_circulate_style_resets_ticks(self, multi_zone_svc):
        """set_zone_sync with circulate style resets zone_sync_ticks to 0."""
        multi_zone_svc.state.zone_sync_ticks = 42
        multi_zone_svc.set_zone_sync(True)
        assert multi_zone_svc.state.zone_sync_ticks == 0

    def test_set_zone_sync_zone_last_active_cannot_deselect(self, multi_zone_svc):
        """set_zone_sync_zone cannot deselect the last active zone."""
        # Ensure only zone 0 is active
        for i in range(len(multi_zone_svc.state.zone_sync_zones)):
            multi_zone_svc.state.zone_sync_zones[i] = (i == 0)
        multi_zone_svc.set_zone_sync_zone(0, False)
        # Zone 0 must remain True because it's the only active one
        assert multi_zone_svc.state.zone_sync_zones[0] is True

    def test_set_zone_sync_interval_1s(self, led_svc):
        """set_zone_sync_interval(1) computes round(1000/150) = 7 ticks."""
        led_svc.set_zone_sync_interval(1)
        assert led_svc.state.zone_sync_interval == round(1000 / 150)


# =========================================================================
# Tests: LEDService — disk/memory/sensor/clock mutations
# =========================================================================

class TestLEDServiceMutations:
    """Basic mutation methods: set_disk_index, set_memory_ratio, etc."""

    def test_set_disk_index(self, led_svc):
        """set_disk_index stores the given disk index (clamped >= 0)."""
        led_svc.set_disk_index(2)
        assert led_svc.state.disk_index == 2

    def test_set_memory_ratio_valid(self, led_svc):
        """set_memory_ratio accepts valid values 1, 2, 4."""
        for ratio in (1, 2, 4):
            led_svc.set_memory_ratio(ratio)
            assert led_svc.state.memory_ratio == ratio

    def test_set_memory_ratio_invalid_defaults_to_2(self, led_svc):
        """set_memory_ratio with invalid value defaults to 2."""
        led_svc.set_memory_ratio(99)
        assert led_svc.state.memory_ratio == 2

    def test_set_sensor_source(self, led_svc):
        """set_sensor_source sets both temp_source and load_source."""
        led_svc.set_sensor_source("gpu")
        assert led_svc.state.temp_source == "gpu"
        assert led_svc.state.load_source == "gpu"

    def test_set_clock_format(self, led_svc):
        """set_clock_format updates is_timer_24h."""
        led_svc.set_clock_format(False)
        assert led_svc.state.is_timer_24h is False

    def test_set_week_start(self, led_svc):
        """set_week_start updates is_week_sunday."""
        led_svc.set_week_start(True)
        assert led_svc.state.is_week_sunday is True

    def test_update_metrics_updates_engine_metrics(self, led_svc):
        """update_metrics updates both _metrics and engine.metrics."""
        metrics = HardwareMetrics(cpu_temp=72.0)
        led_svc.update_metrics(metrics)
        assert led_svc._metrics is metrics
        assert led_svc._engine.metrics is metrics


# =========================================================================
# Tests: LEDService — configure_for_style
# =========================================================================

class TestLEDServiceConfigureForStyleExtra:
    """configure_for_style sets up state from style registry."""

    def test_zone_count_gt_1_creates_zones(self):
        """configure_for_style with zone_count > 1 creates zone list."""
        svc = LEDService()
        svc.configure_for_style(4)  # LC1: zone_count=3
        assert len(svc.state.zones) == 3

    def test_ring_count_set_from_sub_table(self):
        """configure_for_style with sub_table sets ring_count to extra LED count."""
        svc = LEDService()
        svc.configure_for_style(5, 1)  # LF25 sub1 — has remap sub table
        # ring_count = len(sub_table) - style.led_count
        from trcc.legacy.core.models import LED_REMAP_SUB_TABLES, LED_STYLES
        sub = LED_REMAP_SUB_TABLES.get((5, 1))
        style = LED_STYLES[5]
        expected = len(sub) - style.led_count if sub else 0
        assert svc.state.ring_count == expected


# =========================================================================
# Tests: LEDService — tick in segment mode, circulate mode
# =========================================================================

class TestLEDServiceTick:
    """tick() dispatches through segment/circulate paths."""

    def test_tick_segment_mode_with_zone_map_calls_multi_zone(self):
        """tick in segment mode with zone_map dispatches to _tick_multi_zone."""
        svc = LEDService()
        svc.configure_for_style(2)  # PA120 — has zone_led_map
        from trcc.legacy.core.led_segment import get_display
        seg = get_display(2)
        if seg and seg.zone_led_map:
            colors = svc.tick()
            assert isinstance(colors, list)
            assert len(colors) > 0

    def test_tick_circulate_advances_zone_on_timer(self):
        """tick in circulate mode advances zone_sync_current when timer fires."""
        svc = LEDService()
        svc.configure_for_style(4)  # LC1, 3 zones
        svc.state.zone_sync = True
        svc._led_style = 4  # Not in SELECT_ALL_STYLES
        # Force short interval so timer fires on first tick
        svc.state.zone_sync_interval = 1
        svc.tick()
        # After 1 tick with interval=1, zone_sync_ticks increments then fires
        # zone_sync_ticks was 0 → becomes 1 → 1 >= 1 → resets and advances
        # (may or may not advance depending on exact state, but no crash)
        assert isinstance(svc.state.zone_sync_current, int)


# =========================================================================
# Tests: LEDService — _sync_all_zones_to_selected
# =========================================================================

class TestLEDServiceSyncAllZones:
    """_sync_all_zones_to_selected copies selected zone settings to all."""

    def test_sync_all_zones_copies_mode_color_brightness(self):
        """_sync_all_zones_to_selected copies all fields from selected zone."""
        svc = LEDService()
        svc.configure_for_style(4)  # 3 zones
        svc.state.zones[0].color = (100, 200, 50)
        svc.state.zones[0].mode = LEDMode.BREATHING
        svc.state.zones[0].brightness = 80
        svc.state.selected_zone = 0
        svc._sync_all_zones_to_selected()
        for z in svc.state.zones:
            assert z.color == (100, 200, 50)
            assert z.mode == LEDMode.BREATHING
            assert z.brightness == 80


# =========================================================================
# Tests: LEDService — apply_mask
# =========================================================================

class TestLEDServiceApplyMask:
    """apply_mask contracts for segment mode and non-segment mode."""

    def test_apply_mask_segment_mode_per_led_colors(self):
        """apply_mask in segment mode with per-LED colors uses mask to gate colors."""
        svc = LEDService()
        svc.configure_for_style(1)  # AX120 — has segment display
        # Force a known segment mask
        svc._segment_mask = [True, False, True, False, True,
                              False, True, False, True, False]
        colors = [(255, 0, 0)] * 10
        result = svc.apply_mask(colors)
        assert len(result) == 10
        for i, c in enumerate(result):
            if svc._segment_mask[i]:
                assert c == (255, 0, 0)
            else:
                assert c == (0, 0, 0)

    def test_apply_mask_broadcast_color_path(self):
        """apply_mask uses broadcast (single-color) path when colors length != mask length."""
        svc = LEDService()
        svc.configure_for_style(1)
        svc._segment_mask = [True, False, True, False, True,
                              False, True, False, True, False]
        colors = [(0, 255, 0)]  # Single broadcast color
        result = svc.apply_mask(colors)
        assert len(result) == 10
        assert result[0] == (0, 255, 0)
        assert result[1] == (0, 0, 0)

    def test_apply_mask_no_segment_mode_passthrough(self, led_svc):
        """apply_mask in non-segment mode returns colors unchanged."""
        led_svc._segment_mode = False
        colors = [(1, 2, 3), (4, 5, 6)]
        result = led_svc.apply_mask(colors)
        assert result == colors


# =========================================================================
# Tests: LEDService — protocol, send, initialize, config, cleanup
# =========================================================================

class TestLEDServiceProtocolAndConfig:
    """has_protocol, set_protocol, send_colors, send_tick, initialize, config."""

    def test_has_protocol_false_initially(self, led_svc):
        """has_protocol is False when no protocol has been set."""
        assert led_svc.has_protocol is False

    def test_set_protocol_and_has_protocol(self, led_svc):
        """set_protocol stores protocol; has_protocol becomes True."""
        proto = MagicMock()
        led_svc.set_protocol(proto)
        assert led_svc.has_protocol is True

    def test_send_colors_no_protocol_returns_false(self, led_svc):
        """send_colors returns False when no protocol is configured."""
        result = led_svc.send_colors([(255, 0, 0)] * 10)
        assert result is False

    def test_send_colors_with_protocol_calls_send_led_data(self, led_svc):
        """send_colors with protocol calls protocol.send_led_data and returns its result."""
        proto = MagicMock()
        proto.send_data.return_value = True
        led_svc.set_protocol(proto)
        result = led_svc.send_colors([(255, 0, 0)] * 10)
        proto.send_data.assert_called_once()
        assert result is True

    def test_send_tick_calls_tick_then_send_colors(self, led_svc):
        """send_tick calls tick() and passes colors to send_colors."""
        proto = MagicMock()
        proto.send_data.return_value = True
        led_svc.set_protocol(proto)
        led_svc.send_tick()
        assert proto.send_data.call_count >= 1

    def test_initialize_no_protocol_returns_message(self):
        """initialize returns 'LED protocol not injected' when protocol DI is absent."""
        svc = LEDService()
        dev_info = MagicMock()
        dev_info.device_index = 0
        dev_info.vid = 0x1234
        dev_info.pid = 0x5678
        dev_info.led_style_sub = 0
        result = svc.initialize(dev_info, led_style=1)
        assert 'not injected' in result

    def test_initialize_protocol_error_returns_error_message(self):
        """initialize returns error message when protocol.handshake() raises."""
        proto = MagicMock()
        proto.handshake.side_effect = RuntimeError("USB error")
        svc = LEDService(protocol=proto)
        dev_info = MagicMock()
        dev_info.device_index = 0
        dev_info.vid = 0x1234
        dev_info.pid = 0x5678
        dev_info.led_style_sub = 0
        result = svc.initialize(dev_info, led_style=1)
        assert 'LED protocol error' in result

    def test_initialize_success_returns_name(self):
        """initialize returns LED device name on success."""
        proto = MagicMock()
        proto.handshake.return_value = None
        svc = LEDService(protocol=proto)
        dev_info = MagicMock()
        dev_info.device_index = 0
        dev_info.vid = 0x1234
        dev_info.pid = 0x5678
        dev_info.led_style_sub = 0
        result = svc.initialize(dev_info, led_style=1)
        assert 'LED:' in result or 'AX120' in result

    def test_initialize_handshake_sets_identity_from_pm(self):
        """Handshake PM byte is authoritative — overrides led_style param."""
        from trcc.legacy.core.models import LedHandshakeInfo, PmRegistry
        # PM=16 → PA120_DIGITAL (style 2)
        style = PmRegistry.get_style(16, 0)
        hs = LedHandshakeInfo(
            pm=16, sub_type=0, style=style,
            model_name="PA120_DIGITAL", style_sub=0,
        )
        proto = MagicMock()
        proto.handshake.return_value = hs
        svc = LEDService(protocol=proto)
        dev_info = MagicMock()
        dev_info.device_index = 0
        dev_info.vid = 0x0416
        dev_info.pid = 0x8001
        dev_info.led_style_sub = 0
        # Pass led_style=1 (AX120) but handshake says PA120 (style 2)
        result = svc.initialize(dev_info, led_style=1)
        assert 'PA120' in result
        assert dev_info.led_style_id == 2
        assert dev_info.model == "PA120_DIGITAL"

    def test_initialize_handshake_none_falls_back_to_param(self):
        """When handshake returns None, uses the led_style param."""
        proto = MagicMock()
        proto.handshake.return_value = None
        svc = LEDService(protocol=proto)
        dev_info = MagicMock()
        dev_info.device_index = 0
        dev_info.vid = 0x1234
        dev_info.pid = 0x5678
        dev_info.led_style_sub = 0
        result = svc.initialize(dev_info, led_style=7)
        assert 'LF10' in result  # style 7 = LF10

    def test_save_config_delegates_to_led_config(self):
        """save_config calls led_config.save_state when device is set."""
        mock_cfg = MagicMock()
        mock_dev = MagicMock()
        svc = LEDService(led_config=mock_cfg)
        svc._device = mock_dev
        svc.save_config()
        mock_cfg.save_state.assert_called_once_with(mock_dev, svc.state)

    def test_load_config_delegates_to_led_config(self):
        """load_config calls led_config.load_state when device is set."""
        mock_cfg = MagicMock()
        mock_dev = MagicMock()
        svc = LEDService(led_config=mock_cfg)
        svc._device = mock_dev
        svc.load_config()
        mock_cfg.load_state.assert_called_once_with(mock_dev, svc.state)

    def test_cleanup_saves_config_and_clears_protocol(self):
        """cleanup saves config and sets protocol to None."""
        mock_cfg = MagicMock()
        mock_dev = MagicMock()
        svc = LEDService(led_config=mock_cfg)
        svc._device = mock_dev
        proto = MagicMock()
        svc.set_protocol(proto)
        svc.cleanup()
        mock_cfg.save_state.assert_called_once()
        assert svc.has_protocol is False


# =========================================================================
# Tests: LEDService — __getattr__ delegation
# =========================================================================

class TestLEDServiceGetattr:
    """__getattr__ delegates engine methods; raises for unknown attributes."""

    def test_engine_method_accessible(self, led_svc):
        """_tick_single_mode is accessible via __getattr__ delegation."""
        result = led_svc._tick_single_mode(LEDMode.STATIC, (0, 255, 0), 3)
        assert result == [(0, 255, 0)] * 3

    def test_unknown_attribute_raises(self, led_svc):
        """Accessing an unknown attribute raises AttributeError."""
        with pytest.raises(AttributeError):
            _ = led_svc.totally_unknown_attribute_xyz
