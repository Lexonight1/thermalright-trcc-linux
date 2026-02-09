"""Tests for HR10 7-segment display renderer.

Validates digit rendering, indicator LEDs, alignment, edge cases,
and the metric rendering convenience functions.
"""

import pytest

from trcc.hr10_display import (
    CHAR_SEGMENTS,
    DIGIT_LEDS,
    IND_DEG,
    IND_MBS,
    IND_PCT,
    LED_COUNT,
    WIRE_ORDER,
    apply_animation_colors,
    get_digit_mask,
    render_display,
    render_metric,
)

OFF = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)


# =========================================================================
# Basic LED count and structure
# =========================================================================

class TestConstants:
    def test_led_count(self):
        assert LED_COUNT == 31

    def test_digit_leds_count(self):
        """Each digit has exactly 7 segment LEDs."""
        for leds in DIGIT_LEDS:
            assert len(leds) == 7

    def test_digit_leds_unique(self):
        """All LED indices across digits are unique (no overlap)."""
        all_indices = []
        for leds in DIGIT_LEDS:
            all_indices.extend(leds)
        # Add indicators
        all_indices.extend([IND_MBS, IND_PCT, IND_DEG])
        assert len(all_indices) == len(set(all_indices))

    def test_all_indices_in_range(self):
        """All LED indices fit within 0..30."""
        for leds in DIGIT_LEDS:
            for idx in leds:
                assert 0 <= idx < LED_COUNT
        assert 0 <= IND_MBS < LED_COUNT
        assert 0 <= IND_PCT < LED_COUNT
        assert 0 <= IND_DEG < LED_COUNT

    def test_wire_order_has_7_segments(self):
        assert len(WIRE_ORDER) == 7

    def test_char_segments_all_valid(self):
        """All segment names in CHAR_SEGMENTS are valid (a-g)."""
        valid = {'a', 'b', 'c', 'd', 'e', 'f', 'g'}
        for ch, segs in CHAR_SEGMENTS.items():
            assert segs.issubset(valid), f"Char '{ch}' has invalid segments: {segs - valid}"


# =========================================================================
# render_display
# =========================================================================

class TestRenderDisplay:
    def test_returns_31_tuples(self):
        result = render_display("0")
        assert len(result) == 31
        assert all(isinstance(c, tuple) and len(c) == 3 for c in result)

    def test_empty_string_all_off(self):
        result = render_display("")
        assert all(c == OFF for c in result)

    def test_space_all_off(self):
        result = render_display("    ")
        assert all(c == OFF for c in result)

    def test_single_digit_right_aligned(self):
        """Single digit '5' should appear in digit 1 (rightmost)."""
        result = render_display("5", WHITE)
        # Digit 1 LEDs should have some lit
        digit1_leds = DIGIT_LEDS[0]
        lit_count = sum(1 for idx in digit1_leds if result[idx] != OFF)
        assert lit_count > 0
        # Digits 2-4 should be all off (padded spaces)
        for d in range(1, 4):
            for idx in DIGIT_LEDS[d]:
                assert result[idx] == OFF, f"Digit {d+1} LED {idx} should be off"

    def test_four_digits_all_lit(self):
        """'8888' should light segments in all 4 digits."""
        result = render_display("8888", WHITE)
        for d in range(4):
            for idx in DIGIT_LEDS[d]:
                assert result[idx] == WHITE, f"Digit {d+1} LED {idx} should be on for '8'"

    def test_dash_lights_only_middle_segment(self):
        """'-' should only light segment 'g' (middle)."""
        result = render_display("-", RED)
        digit1_leds = DIGIT_LEDS[0]
        for wire_idx, seg_name in enumerate(WIRE_ORDER):
            led_idx = digit1_leds[wire_idx]
            if seg_name == 'g':
                assert result[led_idx] == RED
            else:
                assert result[led_idx] == OFF

    def test_custom_color(self):
        result = render_display("1", RED)
        # '1' lights segments b and c
        digit1_leds = DIGIT_LEDS[0]
        for wire_idx, seg_name in enumerate(WIRE_ORDER):
            led_idx = digit1_leds[wire_idx]
            if seg_name in {'b', 'c'}:
                assert result[led_idx] == RED
            else:
                assert result[led_idx] == OFF

    def test_truncation_at_4_chars(self):
        """Text longer than 4 chars should be truncated."""
        result = render_display("12345", WHITE)
        assert len(result) == 31

    def test_unknown_char_no_segments(self):
        """Unknown characters should not light any segments."""
        result = render_display("@", WHITE)
        # '@' not in CHAR_SEGMENTS → digit 1 all off
        for idx in DIGIT_LEDS[0]:
            assert result[idx] == OFF


# =========================================================================
# Indicator LEDs
# =========================================================================

class TestIndicators:
    def test_mbs_indicator(self):
        result = render_display("", WHITE, {'mbs'})
        assert result[IND_MBS] == WHITE
        assert result[IND_PCT] == OFF
        assert result[IND_DEG] == OFF

    def test_pct_indicator(self):
        result = render_display("", WHITE, {'%'})
        assert result[IND_PCT] == WHITE
        assert result[IND_MBS] == OFF
        assert result[IND_DEG] == OFF

    def test_deg_indicator(self):
        result = render_display("", WHITE, {'deg'})
        assert result[IND_DEG] == WHITE
        assert result[IND_MBS] == OFF
        assert result[IND_PCT] == OFF

    def test_multiple_indicators(self):
        result = render_display("", WHITE, {'mbs', '%', 'deg'})
        assert result[IND_MBS] == WHITE
        assert result[IND_PCT] == WHITE
        assert result[IND_DEG] == WHITE

    def test_no_indicators_default(self):
        result = render_display("0")
        assert result[IND_MBS] == OFF
        assert result[IND_PCT] == OFF
        assert result[IND_DEG] == OFF


# =========================================================================
# get_digit_mask
# =========================================================================

class TestGetDigitMask:
    def test_returns_31_bools(self):
        mask = get_digit_mask("0")
        assert len(mask) == 31
        assert all(isinstance(b, bool) for b in mask)

    def test_empty_all_false(self):
        mask = get_digit_mask("")
        assert not any(mask)

    def test_8888_all_digits_lit(self):
        mask = get_digit_mask("8888")
        for d in range(4):
            for idx in DIGIT_LEDS[d]:
                assert mask[idx] is True

    def test_with_indicators(self):
        mask = get_digit_mask("", {'deg', '%'})
        assert mask[IND_DEG] is True
        assert mask[IND_PCT] is True
        assert mask[IND_MBS] is False


# =========================================================================
# apply_animation_colors
# =========================================================================

class TestApplyAnimationColors:
    def test_applies_only_to_on_segments(self):
        mask = [True, False] * 15 + [True]
        anim_colors = [(i, i, i) for i in range(31)]
        result = apply_animation_colors(mask, anim_colors)
        assert len(result) == 31
        for i in range(31):
            if mask[i]:
                assert result[i] == anim_colors[i]
            else:
                assert result[i] == OFF

    def test_all_false_mask(self):
        mask = [False] * 31
        anim = [(255, 0, 0)] * 31
        result = apply_animation_colors(mask, anim)
        assert all(c == OFF for c in result)

    def test_all_true_mask(self):
        mask = [True] * 31
        anim = [(r, r, r) for r in range(31)]
        result = apply_animation_colors(mask, anim)
        assert result == anim


# =========================================================================
# render_metric
# =========================================================================

class TestRenderMetric:
    def test_temp_celsius(self):
        result = render_metric(42.0, "temp", WHITE, "C")
        assert len(result) == 31
        # Should have degree indicator lit
        assert result[IND_DEG] == WHITE

    def test_temp_fahrenheit(self):
        result = render_metric(100.0, "temp", WHITE, "F")
        assert result[IND_DEG] == WHITE
        # 100°C → 212°F, renders "212F"

    def test_temp_none_shows_dashes(self):
        result = render_metric(None, "temp", WHITE)
        assert result[IND_DEG] == WHITE
        # '---' should light middle segments

    def test_activity(self):
        result = render_metric(85.0, "activity", WHITE)
        assert result[IND_PCT] == WHITE
        assert result[IND_MBS] == OFF

    def test_read_rate(self):
        result = render_metric(1250.0, "read", WHITE)
        assert result[IND_MBS] == WHITE

    def test_write_rate(self):
        result = render_metric(500.0, "write", RED)
        assert result[IND_MBS] == RED

    def test_unknown_metric(self):
        result = render_metric(42.0, "unknown_metric", WHITE)
        # Should fall through to "---"
        assert len(result) == 31

    def test_none_activity(self):
        result = render_metric(None, "activity", WHITE)
        assert result[IND_PCT] == OFF  # No indicator for None non-temp


# =========================================================================
# Digit correctness — verify specific characters
# =========================================================================

class TestDigitCorrectness:
    """Verify that specific characters light the correct segments."""

    @pytest.mark.parametrize("char,expected_segs", [
        ('0', {'a', 'b', 'c', 'd', 'e', 'f'}),
        ('1', {'b', 'c'}),
        ('2', {'a', 'b', 'd', 'e', 'g'}),
        ('3', {'a', 'b', 'c', 'd', 'g'}),
        ('4', {'b', 'c', 'f', 'g'}),
        ('5', {'a', 'c', 'd', 'f', 'g'}),
        ('6', {'a', 'c', 'd', 'e', 'f', 'g'}),
        ('7', {'a', 'b', 'c'}),
        ('8', {'a', 'b', 'c', 'd', 'e', 'f', 'g'}),
        ('9', {'a', 'b', 'c', 'd', 'f', 'g'}),
    ])
    def test_digit_segments(self, char, expected_segs):
        """Each digit lights exactly the right segments."""
        result = render_display(char, WHITE)
        digit1_leds = DIGIT_LEDS[0]
        for wire_idx, seg_name in enumerate(WIRE_ORDER):
            led_idx = digit1_leds[wire_idx]
            if seg_name in expected_segs:
                assert result[led_idx] == WHITE, f"Segment '{seg_name}' should be on for '{char}'"
            else:
                assert result[led_idx] == OFF, f"Segment '{seg_name}' should be off for '{char}'"

    @pytest.mark.parametrize("char", list(CHAR_SEGMENTS.keys()))
    def test_all_chars_render_without_error(self, char):
        """Every character in CHAR_SEGMENTS should render without errors."""
        result = render_display(char, WHITE)
        assert len(result) == 31
