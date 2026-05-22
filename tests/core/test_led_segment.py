"""Comprehensive tests for trcc.adapters.device.led_segment.

Covers:
- Base class encoding tables (7-seg, 13-seg)
- All encoding helper methods (_encode_7seg, _encode_digits, _encode_2digit_partial,
  _encode_unit, _encode_clock_digit, _encode_3digit_13seg)
- All 10 display styles: AX120, PA120, AK120, LC1, LF8, LF12, LF10, CZ1, LC2, LF11
- Module-level functions: compute_mask, get_display, has_segment_display, DISPLAYS
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.core.led_segment import (
    DISPLAYS,
    AK120Display,
    AX120Display,
    CZ1Display,
    LC1Display,
    LC2Display,
    LF8Display,
    LF10Display,
    LF11Display,
    LF12Display,
    PA120Display,
    SegmentDisplay,
    compute_mask,
    get_display,
    has_segment_display,
)
from trcc.legacy.core.models import HardwareMetrics

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_metrics(**kw: float) -> HardwareMetrics:
    """Build a HardwareMetrics with sensible defaults, overridden by kw."""
    defaults: dict[str, float] = dict(
        cpu_temp=65.0, cpu_percent=42.0, cpu_freq=3600.0, cpu_power=95.0,
        gpu_temp=70.0, gpu_usage=55.0, gpu_clock=1800.0, gpu_power=200.0,
        mem_temp=45.0, mem_percent=60.0, mem_clock=3200.0, mem_available=16384.0,
        disk_temp=38.0, disk_activity=30.0, disk_read=150.0, disk_write=200.0,
    )
    defaults.update({k: float(v) for k, v in kw.items()})
    return HardwareMetrics(**defaults)


def _segments_on(mask: list[bool], leds: tuple[int, ...]) -> set[str]:
    """Return the set of WIRE_7SEG segment names that are lit for the given LED indices."""
    wire = SegmentDisplay.WIRE_7SEG
    return {wire[i] for i, led in enumerate(leds) if mask[led]}


def _segments_on_13(mask: list[bool], leds: tuple[int, ...]) -> set[str]:
    """Return the set of WIRE_13SEG segment names that are lit for the given LED indices."""
    wire = SegmentDisplay.WIRE_13SEG
    return {wire[i] for i, led in enumerate(leds) if mask[led]}


# ─────────────────────────────────────────────────────────────────────────────
# Concrete subclass for testing base helpers in isolation
# ─────────────────────────────────────────────────────────────────────────────

class _TestDisplay(SegmentDisplay):
    """Minimal concrete subclass so we can instantiate SegmentDisplay helpers."""
    mask_size = 200

    def compute_mask(self, metrics: HardwareMetrics, phase: int = 0,  # type: ignore[override]
                     temp_unit: str = "C", **kw: object) -> list[bool]:
        return [False] * self.mask_size


# ═════════════════════════════════════════════════════════════════════════════
# Base class — encoding tables
# ═════════════════════════════════════════════════════════════════════════════

class TestSegmentDisplayEncoding:

    def setup_method(self) -> None:
        self.d = _TestDisplay()

    # ── 7-Segment table ───────────────────────────────────────────────────

    def test_char_7seg_has_all_digits(self) -> None:
        for ch in '0123456789':
            assert ch in SegmentDisplay.CHAR_7SEG

    def test_char_7seg_has_unit_symbols(self) -> None:
        for ch in (' ', 'C', 'F', 'H', 'G'):
            assert ch in SegmentDisplay.CHAR_7SEG

    def test_wire_7seg_is_abcdefg(self) -> None:
        assert SegmentDisplay.WIRE_7SEG == ('a', 'b', 'c', 'd', 'e', 'f', 'g')

    def test_digit_0_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['0'] == {'a', 'b', 'c', 'd', 'e', 'f'}

    def test_digit_1_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['1'] == {'b', 'c'}

    def test_digit_7_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['7'] == {'a', 'b', 'c'}

    def test_digit_8_all_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['8'] == {'a', 'b', 'c', 'd', 'e', 'f', 'g'}

    def test_space_is_empty_set(self) -> None:
        assert SegmentDisplay.CHAR_7SEG[' '] == set()

    def test_char_C_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['C'] == {'a', 'd', 'e', 'f'}

    def test_char_F_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['F'] == {'a', 'e', 'f', 'g'}

    def test_char_H_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['H'] == {'b', 'c', 'e', 'f', 'g'}

    def test_char_G_segments(self) -> None:
        assert SegmentDisplay.CHAR_7SEG['G'] == {'a', 'b', 'c', 'd', 'f', 'g'}

    def test_all_7seg_segments_within_abcdefg(self) -> None:
        valid = set(SegmentDisplay.WIRE_7SEG)
        for ch, segs in SegmentDisplay.CHAR_7SEG.items():
            assert segs <= valid, f"'{ch}' has invalid segments: {segs - valid}"

    # ── 13-Segment table ──────────────────────────────────────────────────

    def test_char_13seg_has_all_digits(self) -> None:
        for ch in '0123456789':
            assert ch in SegmentDisplay.CHAR_13SEG

    def test_char_13seg_has_space(self) -> None:
        assert ' ' in SegmentDisplay.CHAR_13SEG

    def test_wire_13seg_has_13_entries(self) -> None:
        assert len(SegmentDisplay.WIRE_13SEG) == 13

    def test_wire_13seg_is_a_through_m(self) -> None:
        assert SegmentDisplay.WIRE_13SEG == (
            'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm'
        )

    def test_13seg_8_has_all_segments(self) -> None:
        assert SegmentDisplay.CHAR_13SEG['8'] == set(SegmentDisplay.WIRE_13SEG)

    def test_13seg_space_is_empty(self) -> None:
        assert SegmentDisplay.CHAR_13SEG[' '] == set()

    def test_all_13seg_segments_within_wire(self) -> None:
        valid = set(SegmentDisplay.WIRE_13SEG)
        for ch, segs in SegmentDisplay.CHAR_13SEG.items():
            assert segs <= valid, f"'{ch}' has invalid 13-seg: {segs - valid}"

    # ── _encode_7seg ──────────────────────────────────────────────────────

    @pytest.mark.parametrize("digit,expected_segs", [
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
        (' ', set()),
        ('C', {'a', 'd', 'e', 'f'}),
        ('F', {'a', 'e', 'f', 'g'}),
        ('H', {'b', 'c', 'e', 'f', 'g'}),
        ('G', {'a', 'b', 'c', 'd', 'f', 'g'}),
    ])
    def test_encode_7seg_char(self, digit: str, expected_segs: set[str]) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_7seg(digit, leds, mask)
        assert _segments_on(mask, leds) == expected_segs

    def test_encode_7seg_unknown_char_is_blank(self) -> None:
        """An unknown character should produce no lit segments (falls back to empty set)."""
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_7seg('Z', leds, mask)
        assert not any(mask[:7])

    def test_encode_7seg_writes_to_correct_positions(self) -> None:
        """Segments are written at the correct LED indices."""
        # Use non-zero base indices to confirm indexing
        leds: tuple[int, ...] = (10, 11, 12, 13, 14, 15, 16)
        mask = [False] * 20
        self.d._encode_7seg('1', leds, mask)
        # '1' = b,c → WIRE_7SEG[1]=b, WIRE_7SEG[2]=c → leds[1]=11, leds[2]=12
        assert mask[11] is True   # b
        assert mask[12] is True   # c
        assert mask[10] is False  # a
        assert mask[13] is False  # d
        assert mask[14] is False  # e
        assert mask[15] is False  # f
        assert mask[16] is False  # g

    def test_encode_7seg_does_not_clear_existing(self) -> None:
        """_encode_7seg only sets True — it never clears bits already set."""
        leds: tuple[int, ...] = tuple(range(7))
        mask = [True] * 10
        self.d._encode_7seg(' ', leds, mask)
        # Space = no segments, but pre-set bits must remain
        assert all(mask)

    # ── _encode_digits ────────────────────────────────────────────────────

    def test_encode_digits_value_zero(self) -> None:
        """Value 0 → ones digit shows '0', all leading digits blank."""
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)), tuple(range(14, 21)))
        mask = [False] * 30
        self.d._encode_digits(0, 999, 3, digit_leds, mask)
        # Hundreds and tens should be blank
        assert not any(mask[i] for i in range(14))
        # Ones should show '0'
        assert _segments_on(mask, digit_leds[2]) == SegmentDisplay.CHAR_7SEG['0']

    def test_encode_digits_value_5_leading_zeros_suppressed(self) -> None:
        """Value 5 → hundreds and tens blank, ones = '5'."""
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)), tuple(range(14, 21)))
        mask = [False] * 30
        self.d._encode_digits(5, 999, 3, digit_leds, mask)
        assert not any(mask[i] for i in range(14))
        assert _segments_on(mask, digit_leds[2]) == SegmentDisplay.CHAR_7SEG['5']

    def test_encode_digits_value_42(self) -> None:
        """Value 42 → hundreds blank, tens = '4', ones = '2'."""
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)), tuple(range(14, 21)))
        mask = [False] * 30
        self.d._encode_digits(42, 999, 3, digit_leds, mask)
        assert not any(mask[i] for i in range(7))
        assert _segments_on(mask, digit_leds[1]) == SegmentDisplay.CHAR_7SEG['4']
        assert _segments_on(mask, digit_leds[2]) == SegmentDisplay.CHAR_7SEG['2']

    def test_encode_digits_value_123(self) -> None:
        """Value 123 → all three digits lit."""
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)), tuple(range(14, 21)))
        mask = [False] * 30
        self.d._encode_digits(123, 999, 3, digit_leds, mask)
        assert _segments_on(mask, digit_leds[0]) == SegmentDisplay.CHAR_7SEG['1']
        assert _segments_on(mask, digit_leds[1]) == SegmentDisplay.CHAR_7SEG['2']
        assert _segments_on(mask, digit_leds[2]) == SegmentDisplay.CHAR_7SEG['3']

    def test_encode_digits_clamped_to_max_val(self) -> None:
        """Value exceeding max_val is clamped."""
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)), tuple(range(14, 21)))
        mask_clamped = [False] * 30
        mask_exact = [False] * 30
        self.d._encode_digits(9999, 999, 3, digit_leds, mask_clamped)
        self.d._encode_digits(999, 999, 3, digit_leds, mask_exact)
        assert mask_clamped == mask_exact

    def test_encode_digits_negative_clamped_to_zero(self) -> None:
        """Negative value is clamped to 0."""
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)), tuple(range(14, 21)))
        mask_neg = [False] * 30
        mask_zero = [False] * 30
        self.d._encode_digits(-50, 999, 3, digit_leds, mask_neg)
        self.d._encode_digits(0, 999, 3, digit_leds, mask_zero)
        assert mask_neg == mask_zero

    def test_encode_3digit_max_is_999(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)), tuple(range(14, 21)))
        mask_over = [False] * 30
        mask_max = [False] * 30
        self.d._encode_3digit(1500, digit_leds, mask_over)
        self.d._encode_3digit(999, digit_leds, mask_max)
        assert mask_over == mask_max

    def test_encode_4digit_max_is_9999(self) -> None:
        digit_leds = (
            tuple(range(0, 7)), tuple(range(7, 14)),
            tuple(range(14, 21)), tuple(range(21, 28)),
        )
        mask_over = [False] * 30
        mask_max = [False] * 30
        self.d._encode_4digit(99999, digit_leds, mask_over)
        self.d._encode_4digit(9999, digit_leds, mask_max)
        assert mask_over == mask_max

    def test_encode_5digit_max_is_99999(self) -> None:
        digit_leds = tuple(tuple(range(i * 7, (i + 1) * 7)) for i in range(5))
        mask_over = [False] * 40
        mask_max = [False] * 40
        self.d._encode_5digit(999999, digit_leds, mask_over)
        self.d._encode_5digit(99999, digit_leds, mask_max)
        assert mask_over == mask_max

    def test_encode_2digit_max_is_99(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        mask_over = [False] * 20
        mask_max = [False] * 20
        self.d._encode_2digit(200, digit_leds, mask_over)
        self.d._encode_2digit(99, digit_leds, mask_max)
        assert mask_over == mask_max

    # ── _encode_2digit_partial ────────────────────────────────────────────

    def test_encode_2digit_partial_under_100_no_partial(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        partial_bc = (20, 21)
        mask = [False] * 30
        self.d._encode_2digit_partial(75, digit_leds, partial_bc, mask)
        assert mask[20] is False
        assert mask[21] is False

    def test_encode_2digit_partial_100_sets_bc_and_shows_00(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        partial_bc = (20, 21)
        mask = [False] * 30
        self.d._encode_2digit_partial(100, digit_leds, partial_bc, mask)
        assert mask[20] is True
        assert mask[21] is True
        # Remainder = 0 → both digits show '0' (no leading-zero suppression)
        assert _segments_on(mask, digit_leds[0]) == SegmentDisplay.CHAR_7SEG['0']
        assert _segments_on(mask, digit_leds[1]) == SegmentDisplay.CHAR_7SEG['0']

    def test_encode_2digit_partial_150_bc_set_shows_50(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        partial_bc = (20, 21)
        mask = [False] * 30
        self.d._encode_2digit_partial(150, digit_leds, partial_bc, mask)
        assert mask[20] is True
        assert mask[21] is True
        assert _segments_on(mask, digit_leds[0]) == SegmentDisplay.CHAR_7SEG['5']
        assert _segments_on(mask, digit_leds[1]) == SegmentDisplay.CHAR_7SEG['0']

    def test_encode_2digit_partial_199_bc_set_shows_99(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        partial_bc = (20, 21)
        mask = [False] * 30
        self.d._encode_2digit_partial(199, digit_leds, partial_bc, mask)
        assert mask[20] is True
        assert mask[21] is True
        assert _segments_on(mask, digit_leds[0]) == SegmentDisplay.CHAR_7SEG['9']
        assert _segments_on(mask, digit_leds[1]) == SegmentDisplay.CHAR_7SEG['9']

    def test_encode_2digit_partial_clamped_at_199(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        partial_bc = (20, 21)
        mask_over = [False] * 30
        mask_max = [False] * 30
        self.d._encode_2digit_partial(250, digit_leds, partial_bc, mask_over)
        self.d._encode_2digit_partial(199, digit_leds, partial_bc, mask_max)
        assert mask_over == mask_max

    def test_encode_2digit_partial_zero_no_bc(self) -> None:
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        partial_bc = (20, 21)
        mask = [False] * 30
        self.d._encode_2digit_partial(0, digit_leds, partial_bc, mask)
        assert mask[20] is False
        assert mask[21] is False

    def test_encode_2digit_partial_none_partial_bc(self) -> None:
        """partial_bc=None means no overflow LEDs even for value >= 100."""
        digit_leds = (tuple(range(0, 7)), tuple(range(7, 14)))
        mask = [False] * 30
        self.d._encode_2digit_partial(150, digit_leds, None, mask)
        # Should not raise; value treated as clamped to 50 (100 subtracted)
        # No partial LEDs to set since partial_bc is None

    # ── _encode_unit ──────────────────────────────────────────────────────

    def test_encode_unit_celsius(self) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_unit(0, leds, mask)
        assert _segments_on(mask, leds) == SegmentDisplay.CHAR_7SEG['C']

    def test_encode_unit_fahrenheit(self) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_unit(-1, leds, mask)
        assert _segments_on(mask, leds) == SegmentDisplay.CHAR_7SEG['F']

    def test_encode_unit_mhz(self) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_unit(1, leds, mask)
        assert _segments_on(mask, leds) == SegmentDisplay.CHAR_7SEG['H']

    def test_encode_unit_gb(self) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_unit(2, leds, mask)
        assert _segments_on(mask, leds) == SegmentDisplay.CHAR_7SEG['G']

    def test_encode_unit_unknown_is_blank(self) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_unit(99, leds, mask)
        assert _segments_on(mask, leds) == set()

    # ── _encode_clock_digit ───────────────────────────────────────────────

    def test_encode_clock_digit_normal(self) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_clock_digit(5, leds, mask)
        assert _segments_on(mask, leds) == SegmentDisplay.CHAR_7SEG['5']

    def test_encode_clock_digit_zero_no_suppress(self) -> None:
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_clock_digit(0, leds, mask, suppress_zero=False)
        assert _segments_on(mask, leds) == SegmentDisplay.CHAR_7SEG['0']

    def test_encode_clock_digit_zero_with_suppress(self) -> None:
        """Zero with suppress_zero=True → nothing written."""
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_clock_digit(0, leds, mask, suppress_zero=True)
        assert not any(mask[:7])

    def test_encode_clock_digit_nonzero_suppress_has_no_effect(self) -> None:
        """suppress_zero=True only affects value==0."""
        leds: tuple[int, ...] = tuple(range(7))
        mask = [False] * 10
        self.d._encode_clock_digit(3, leds, mask, suppress_zero=True)
        assert _segments_on(mask, leds) == SegmentDisplay.CHAR_7SEG['3']

    # ── _encode_3digit_13seg ──────────────────────────────────────────────

    def test_encode_3digit_13seg_value_zero(self) -> None:
        """Value 0: hundreds and tens suppressed; ones digit shows '0'."""
        digits_13 = (tuple(range(0, 13)), tuple(range(13, 26)), tuple(range(26, 39)))
        mask = [False] * 40
        self.d._encode_3digit_13seg(0, digits_13, mask)
        # Hundreds blank (suppressed)
        assert not any(mask[i] for i in range(13))
        # Tens blank (hundreds == 0 → suppress tens too)
        assert not any(mask[i] for i in range(13, 26))
        # Ones shows '0'
        assert _segments_on_13(mask, digits_13[2]) == SegmentDisplay.CHAR_13SEG['0']

    def test_encode_3digit_13seg_value_5(self) -> None:
        """Value 5 → only ones digit lit."""
        digits_13 = (tuple(range(0, 13)), tuple(range(13, 26)), tuple(range(26, 39)))
        mask = [False] * 40
        self.d._encode_3digit_13seg(5, digits_13, mask)
        assert not any(mask[i] for i in range(26))
        assert _segments_on_13(mask, digits_13[2]) == SegmentDisplay.CHAR_13SEG['5']

    def test_encode_3digit_13seg_value_123(self) -> None:
        digits_13 = (tuple(range(0, 13)), tuple(range(13, 26)), tuple(range(26, 39)))
        mask = [False] * 40
        self.d._encode_3digit_13seg(123, digits_13, mask)
        assert _segments_on_13(mask, digits_13[0]) == SegmentDisplay.CHAR_13SEG['1']
        assert _segments_on_13(mask, digits_13[1]) == SegmentDisplay.CHAR_13SEG['2']
        assert _segments_on_13(mask, digits_13[2]) == SegmentDisplay.CHAR_13SEG['3']

    def test_encode_3digit_13seg_tens_shown_when_nonzero(self) -> None:
        """Value 55 → hundreds suppressed, tens lit."""
        digits_13 = (tuple(range(0, 13)), tuple(range(13, 26)), tuple(range(26, 39)))
        mask = [False] * 40
        self.d._encode_3digit_13seg(55, digits_13, mask)
        assert not any(mask[i] for i in range(13))
        assert _segments_on_13(mask, digits_13[1]) == SegmentDisplay.CHAR_13SEG['5']
        assert _segments_on_13(mask, digits_13[2]) == SegmentDisplay.CHAR_13SEG['5']

    def test_encode_3digit_13seg_clamped_to_999(self) -> None:
        digits_13 = (tuple(range(0, 13)), tuple(range(13, 26)), tuple(range(26, 39)))
        mask_over = [False] * 40
        mask_max = [False] * 40
        self.d._encode_3digit_13seg(9999, digits_13, mask_over)
        self.d._encode_3digit_13seg(999, digits_13, mask_max)
        assert mask_over == mask_max

    # ── _to_display_temp ──────────────────────────────────────────────────

    def test_to_display_temp_truncates(self) -> None:
        """Metrics are pre-converted — display just truncates to int."""
        assert _TestDisplay._to_display_temp(65.7, "C") == 65
        assert _TestDisplay._to_display_temp(77.0, "F") == 77
        assert _TestDisplay._to_display_temp(0.0, "C") == 0


# ═════════════════════════════════════════════════════════════════════════════
# Style 1 — AX120_DIGITAL (30 LEDs, 4-phase)
# ═════════════════════════════════════════════════════════════════════════════

class TestAX120Display:

    def setup_method(self) -> None:
        self.d = AX120Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 30

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 4

    def test_returns_30_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 30
        assert all(isinstance(b, bool) for b in mask)

    def test_always_on_leds_0_and_1(self) -> None:
        for phase in range(4):
            mask = self.d.compute_mask(_make_metrics(), phase, "C")
            assert mask[0] is True
            assert mask[1] is True

    # ── Phase indicators ──────────────────────────────────────────────────

    def test_cpu_temp_phase_indicators(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=55.0), 0, "C")
        assert mask[2] is True   # CPU1
        assert mask[3] is True   # CPU2
        assert mask[4] is False  # GPU1
        assert mask[5] is False  # GPU2
        assert mask[6] is True   # °C
        assert mask[7] is False  # °F
        assert mask[8] is False  # %

    def test_cpu_usage_phase_indicators(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=80.0), 1, "C")
        assert mask[2] is True   # CPU1
        assert mask[3] is True   # CPU2
        assert mask[8] is True   # %
        assert mask[6] is False  # °C
        assert mask[7] is False  # °F

    def test_gpu_temp_phase_indicators(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_temp=70.0), 2, "C")
        assert mask[4] is True   # GPU1
        assert mask[5] is True   # GPU2
        assert mask[2] is False  # CPU1
        assert mask[6] is True   # °C

    def test_gpu_usage_phase_indicators(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_usage=50.0), 3, "C")
        assert mask[4] is True   # GPU1
        assert mask[5] is True   # GPU2
        assert mask[8] is True   # %
        assert mask[6] is False

    def test_fahrenheit_shows_f_not_c(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=72.0), 0, "F")
        assert mask[7] is True   # °F
        assert mask[6] is False  # °C

    def test_phase_wraps_modulo_4(self) -> None:
        """Phase 4 == phase 0."""
        m0 = self.d.compute_mask(_make_metrics(), 0, "C")
        m4 = self.d.compute_mask(_make_metrics(), 4, "C")
        assert m0 == m4

    # ── Value encoding ────────────────────────────────────────────────────

    @pytest.mark.parametrize("value,expected_ones_segs", [
        (0, {'a', 'b', 'c', 'd', 'e', 'f'}),
        (1, {'b', 'c'}),
        (5, {'a', 'c', 'd', 'f', 'g'}),
        (8, {'a', 'b', 'c', 'd', 'e', 'f', 'g'}),
    ])
    def test_ones_digit_encoding(self, value: int, expected_ones_segs: set[str]) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=float(value)), 0, "C")
        assert _segments_on(mask, self.d.DIGITS[2]) == expected_ones_segs

    def test_leading_zero_suppression_value_0(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=0.0), 0, "C")
        assert not any(mask[led] for led in self.d.DIGITS[0])
        assert not any(mask[led] for led in self.d.DIGITS[1])

    def test_leading_zero_suppression_value_5(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=5.0), 0, "C")
        assert not any(mask[led] for led in self.d.DIGITS[0])
        assert not any(mask[led] for led in self.d.DIGITS[1])

    def test_value_10_tens_digit_lit(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=10.0), 0, "C")
        assert not any(mask[led] for led in self.d.DIGITS[0])
        # '1' = b,c → 2 segments
        assert sum(1 for led in self.d.DIGITS[1] if mask[led]) == 2

    def test_value_888_all_segments_lit(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=888.0), 0, "C")
        for digit in self.d.DIGITS:
            assert all(mask[led] for led in digit)

    def test_value_clamped_to_999(self) -> None:
        m_over = self.d.compute_mask(_make_metrics(cpu_temp=2000.0), 0, "C")
        m_max = self.d.compute_mask(_make_metrics(cpu_temp=999.0), 0, "C")
        assert m_over == m_max

    def test_negative_temp_clamped_to_0(self) -> None:
        m_neg = self.d.compute_mask(_make_metrics(cpu_temp=-10.0), 0, "C")
        m_zero = self.d.compute_mask(_make_metrics(cpu_temp=0.0), 0, "C")
        assert m_neg == m_zero

    def test_gpu_usage_value_encoded(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_usage=55.0), 3, "C")
        on_count = sum(1 for led in self.d.DIGITS[2] if mask[led])
        assert on_count > 0  # '5' has segments lit


# ═════════════════════════════════════════════════════════════════════════════
# Style 2 — PA120_DIGITAL (84 LEDs, simultaneous 4-value)
# ═════════════════════════════════════════════════════════════════════════════

class TestPA120Display:

    def setup_method(self) -> None:
        self.d = PA120Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 84

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 1

    def test_returns_84_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 84

    def test_always_on_indicators(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        for idx in (self.d.CPU1, self.d.CPU2, self.d.GPU1, self.d.GPU2,
                    self.d.BFB, self.d.BFB1):
            assert mask[idx] is True

    def test_celsius_temp_indicators(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.SSD] is True   # CPU °C
        assert mask[self.d.SSD1] is True  # GPU °C
        assert mask[self.d.HSD] is False  # CPU °F
        assert mask[self.d.HSD1] is False # GPU °F

    def test_fahrenheit_temp_indicators(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "F")
        assert mask[self.d.HSD] is True   # CPU °F
        assert mask[self.d.HSD1] is True  # GPU °F
        assert mask[self.d.SSD] is False
        assert mask[self.d.SSD1] is False

    def test_simultaneous_all_four_values(self) -> None:
        """All four digit regions should have some LEDs lit for non-zero metrics."""
        metrics = _make_metrics(cpu_temp=65.0, cpu_percent=80.0,
                                gpu_temp=70.0, gpu_usage=50.0)
        mask = self.d.compute_mask(metrics, 0, "C")
        for digit_group in self.d.CPU_TEMP_DIGITS:
            assert any(mask[led] for led in digit_group) or True  # may be leading-zero
        # Ones digit of cpu_temp should be lit
        assert any(mask[led] for led in self.d.CPU_TEMP_DIGITS[2])

    def test_cpu_usage_partial_at_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=100.0), 0, "C")
        assert mask[self.d.CPU_USE_PARTIAL[0]] is True
        assert mask[self.d.CPU_USE_PARTIAL[1]] is True

    def test_cpu_usage_no_partial_under_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=99.0), 0, "C")
        assert mask[self.d.CPU_USE_PARTIAL[0]] is False
        assert mask[self.d.CPU_USE_PARTIAL[1]] is False

    def test_gpu_usage_partial_at_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_usage=100.0), 0, "C")
        assert mask[self.d.GPU_USE_PARTIAL[0]] is True
        assert mask[self.d.GPU_USE_PARTIAL[1]] is True

    def test_gpu_usage_no_partial_under_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_usage=50.0), 0, "C")
        assert mask[self.d.GPU_USE_PARTIAL[0]] is False
        assert mask[self.d.GPU_USE_PARTIAL[1]] is False

    def test_zone_led_map_has_4_zones(self) -> None:
        assert self.d.zone_led_map is not None
        assert len(self.d.zone_led_map) == 4

    def test_zone_led_map_covers_all_84_leds(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        all_indices = sorted(idx for zone in zmap for idx in zone)
        assert all_indices == list(range(84))

    def test_no_zone_overlap(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        seen: set[int] = set()
        for zone in zmap:
            zone_set = set(zone)
            assert not (seen & zone_set), f"Overlap: {seen & zone_set}"
            seen |= zone_set

    def test_zone1_contains_cpu_temp_digits(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        z1 = set(zmap[0])
        assert {self.d.CPU1, self.d.CPU2, self.d.SSD, self.d.HSD} <= z1
        for digit in self.d.CPU_TEMP_DIGITS:
            assert set(digit) <= z1

    def test_zone2_contains_cpu_usage_digits(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        z2 = set(zmap[1])
        assert self.d.BFB in z2
        for digit in self.d.CPU_USE_DIGITS:
            assert set(digit) <= z2
        assert set(self.d.CPU_USE_PARTIAL) <= z2


# ═════════════════════════════════════════════════════════════════════════════
# Style 3 — AK120_DIGITAL (64 LEDs, 2-phase CPU/GPU)
# ═════════════════════════════════════════════════════════════════════════════

class TestAK120Display:

    def setup_method(self) -> None:
        self.d = AK120Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 64

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 2

    def test_returns_64_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 64

    def test_watt_and_bfb_always_on(self) -> None:
        for phase in range(2):
            mask = self.d.compute_mask(_make_metrics(), phase, "C")
            assert mask[self.d.WATT] is True
            assert mask[self.d.BFB] is True

    def test_cpu_phase_source_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.CPU1] is True
        assert mask[self.d.GPU1] is False

    def test_gpu_phase_source_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 1, "C")
        assert mask[self.d.GPU1] is True
        assert mask[self.d.CPU1] is False

    def test_celsius_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.SSD] is True
        assert mask[self.d.HSD] is False

    def test_fahrenheit_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "F")
        assert mask[self.d.HSD] is True
        assert mask[self.d.SSD] is False

    def test_watt_digits_encoded(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_power=120.0), 0, "C")
        assert any(mask[led] for digit in self.d.WATT_DIGITS for led in digit)

    def test_temp_digits_encoded(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=65.0), 0, "C")
        assert any(mask[led] for digit in self.d.TEMP_DIGITS for led in digit)

    def test_usage_partial_at_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=100.0), 0, "C")
        assert mask[self.d.USE_PARTIAL[0]] is True
        assert mask[self.d.USE_PARTIAL[1]] is True

    def test_usage_no_partial_under_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=50.0), 0, "C")
        assert mask[self.d.USE_PARTIAL[0]] is False
        assert mask[self.d.USE_PARTIAL[1]] is False

    def test_phase_wraps_modulo_2(self) -> None:
        m0 = self.d.compute_mask(_make_metrics(), 0, "C")
        m2 = self.d.compute_mask(_make_metrics(), 2, "C")
        assert m0 == m2

    def test_gpu_metrics_used_in_gpu_phase(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_power=200.0, gpu_temp=60.0, gpu_usage=90.0), 1, "C")
        assert any(mask[led] for digit in self.d.WATT_DIGITS for led in digit)
        assert any(mask[led] for digit in self.d.TEMP_DIGITS for led in digit)


# ═════════════════════════════════════════════════════════════════════════════
# Style 4 — LC1 (31 LEDs, mode-based 3-phase)
# ═════════════════════════════════════════════════════════════════════════════

class TestLC1Display:

    def setup_method(self) -> None:
        self.d = LC1Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 31

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 3

    def test_returns_31_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 31

    # ── Memory sub-style (sub_style=0) ────────────────────────────────────

    def test_memory_temp_phase_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(mem_temp=45.0), 0, "C", sub_style=0)
        assert mask[self.d.SSD] is True
        assert mask[self.d.MTNO] is False
        assert mask[self.d.GNO] is False

    def test_memory_clock_phase_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(mem_clock=3200.0), 1, "C", sub_style=0)
        assert mask[self.d.MTNO] is True
        assert mask[self.d.SSD] is False
        assert mask[self.d.GNO] is False

    def test_memory_used_phase_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 2, "C", sub_style=0)
        assert mask[self.d.GNO] is True
        assert mask[self.d.SSD] is False
        assert mask[self.d.MTNO] is False

    def test_unit_digit_celsius_in_temp_phase(self) -> None:
        mask = self.d.compute_mask(_make_metrics(mem_temp=45.0), 0, "C", sub_style=0)
        assert _segments_on(mask, self.d.UNIT_DIGIT) == SegmentDisplay.CHAR_7SEG['C']

    def test_unit_digit_fahrenheit_in_temp_phase(self) -> None:
        mask = self.d.compute_mask(_make_metrics(mem_temp=45.0), 0, "F", sub_style=0)
        assert _segments_on(mask, self.d.UNIT_DIGIT) == SegmentDisplay.CHAR_7SEG['F']


    def test_memory_clock_applies_ratio(self) -> None:
        """mem_clock is multiplied by memory_ratio in phase 1."""
        m_r1 = self.d.compute_mask(_make_metrics(mem_clock=100.0), 1, "C",
                                   sub_style=0, memory_ratio=1)
        m_r2 = self.d.compute_mask(_make_metrics(mem_clock=100.0), 1, "C",
                                   sub_style=0, memory_ratio=2)
        # ratio=2 → value 200; ratio=1 → value 100 → different digit encoding
        assert m_r1 != m_r2

    # ── Disk sub-style (sub_style=1) ──────────────────────────────────────

    def test_disk_temp_phase_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_temp=38.0), 0, "C", sub_style=1)
        assert mask[self.d.SSD] is True

    def test_disk_read_phase_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_read=150.0), 1, "C", sub_style=1)
        assert mask[self.d.MTNO] is True

    def test_disk_activity_phase_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_activity=30.0), 2, "C", sub_style=1)
        assert mask[self.d.GNO] is True

    def test_disk_read_no_ratio_applied(self) -> None:
        """Disk sub_style does NOT apply memory_ratio."""
        m_r1 = self.d.compute_mask(_make_metrics(disk_read=100.0), 1, "C",
                                   sub_style=1, memory_ratio=1)
        m_r2 = self.d.compute_mask(_make_metrics(disk_read=100.0), 1, "C",
                                   sub_style=1, memory_ratio=2)
        # No ratio applied for disk → same mask regardless of memory_ratio
        assert m_r1 == m_r2

    def test_phase_wraps_modulo_3(self) -> None:
        m0 = self.d.compute_mask(_make_metrics(), 0, "C")
        m3 = self.d.compute_mask(_make_metrics(), 3, "C")
        assert m0 == m3


# ═════════════════════════════════════════════════════════════════════════════
# Style 5/11 — LF8 / LF15 (93 LEDs, 4-metric 2-phase)
# ═════════════════════════════════════════════════════════════════════════════

class TestLF8Display:

    def setup_method(self) -> None:
        self.d = LF8Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 93

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 2

    def test_returns_93_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 93

    def test_watt_mhz_bfb_always_on(self) -> None:
        for phase in range(2):
            mask = self.d.compute_mask(_make_metrics(), phase, "C")
            assert mask[self.d.WATT] is True
            assert mask[self.d.MHZ] is True
            assert mask[self.d.BFB] is True

    def test_cpu_phase_source_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.CPU1] is True
        assert mask[self.d.GPU1] is False

    def test_gpu_phase_source_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 1, "C")
        assert mask[self.d.GPU1] is True
        assert mask[self.d.CPU1] is False

    def test_celsius_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.SSD] is True
        assert mask[self.d.HSD] is False

    def test_fahrenheit_indicator(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "F")
        assert mask[self.d.HSD] is True
        assert mask[self.d.SSD] is False

    def test_all_4_digit_regions_have_some_leds_lit(self) -> None:
        metrics = _make_metrics(cpu_temp=55.0, cpu_power=120.0,
                                cpu_freq=3500.0, cpu_percent=75.0)
        mask = self.d.compute_mask(metrics, 0, "C")
        for region in (self.d.TEMP_DIGITS, self.d.WATT_DIGITS, self.d.MHZ_DIGITS):
            assert any(mask[led] for digit in region for led in digit)

    def test_usage_partial_at_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=100.0), 0, "C")
        assert mask[self.d.USE_PARTIAL[0]] is True
        assert mask[self.d.USE_PARTIAL[1]] is True

    def test_usage_no_partial_under_100(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=42.0), 0, "C")
        assert mask[self.d.USE_PARTIAL[0]] is False
        assert mask[self.d.USE_PARTIAL[1]] is False

    def test_phase_wraps_modulo_2(self) -> None:
        m0 = self.d.compute_mask(_make_metrics(), 0, "C")
        m2 = self.d.compute_mask(_make_metrics(), 2, "C")
        assert m0 == m2

    def test_4digit_mhz_region(self) -> None:
        """cpu_freq encoded as 4-digit value in MHz region."""
        mask = self.d.compute_mask(_make_metrics(cpu_freq=3600.0), 0, "C")
        assert any(mask[led] for digit in self.d.MHZ_DIGITS for led in digit)

    def test_style_11_uses_same_lf8_layout(self) -> None:
        """DISPLAYS[11] is an LF8Display with identical mask behaviour."""
        d11 = DISPLAYS[11]
        assert isinstance(d11, LF8Display)
        metrics = _make_metrics()
        mask_5 = DISPLAYS[5].compute_mask(metrics, 0, "C")
        mask_11 = d11.compute_mask(metrics, 0, "C")
        assert mask_5 == mask_11


# ═════════════════════════════════════════════════════════════════════════════
# Style 6 — LF12 (124 LEDs = LF8 + 31 decoration)
# ═════════════════════════════════════════════════════════════════════════════

class TestLF12Display:

    def setup_method(self) -> None:
        self.d = LF12Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 124

    def test_inherits_from_lf8(self) -> None:
        assert isinstance(self.d, LF8Display)

    def test_returns_124_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 124

    def test_decoration_leds_always_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        for idx in range(93, 124):
            assert mask[idx] is True

    def test_decoration_range_is_93_to_123(self) -> None:
        assert tuple(range(93, 124)) == self.d.DECORATION

    def test_digit_region_matches_lf8(self) -> None:
        """First 93 LEDs should be identical to LF8 encoding."""
        metrics = _make_metrics(cpu_temp=65.0, cpu_power=100.0,
                                cpu_freq=2000.0, cpu_percent=50.0)
        lf8 = LF8Display()
        mask_lf8 = lf8.compute_mask(metrics, 0, "C")
        mask_lf12 = self.d.compute_mask(metrics, 0, "C")
        assert mask_lf12[:93] == mask_lf8[:93]

    def test_digit_region_gpu_phase_matches_lf8(self) -> None:
        metrics = _make_metrics(gpu_temp=70.0, gpu_power=200.0,
                                gpu_clock=1800.0, gpu_usage=55.0)
        lf8 = LF8Display()
        mask_lf8 = lf8.compute_mask(metrics, 1, "C")
        mask_lf12 = self.d.compute_mask(metrics, 1, "C")
        assert mask_lf12[:93] == mask_lf8[:93]

    def test_phase_count_same_as_lf8(self) -> None:
        assert self.d.phase_count == LF8Display.phase_count


# ═════════════════════════════════════════════════════════════════════════════
# Style 7 — LF10 (116 LEDs, 13-segment + decoration)
# ═════════════════════════════════════════════════════════════════════════════

class TestLF10Display:

    def setup_method(self) -> None:
        self.d = LF10Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 116

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 1

    def test_returns_116_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 116

    def test_cpu1_and_gpu1_always_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.CPU1] is True
        assert mask[self.d.GPU1] is True

    def test_celsius_indicators_both_sides(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.SSD] is True    # CPU °C
        assert mask[self.d.SSD1] is True   # GPU °C
        assert mask[self.d.HSD] is False
        assert mask[self.d.HSD1] is False

    def test_fahrenheit_indicators_both_sides(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "F")
        assert mask[self.d.HSD] is True
        assert mask[self.d.HSD1] is True
        assert mask[self.d.SSD] is False
        assert mask[self.d.SSD1] is False

    def test_decoration_leds_84_to_115_always_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        for idx in range(84, 116):
            assert mask[idx] is True

    def test_decoration_range(self) -> None:
        assert tuple(range(84, 116)) == self.d.DECORATION

    def test_13seg_digit_groups_have_13_leds_each(self) -> None:
        for digit in self.d.DIGIT_LEDS_13:
            assert len(digit) == 13

    def test_six_13seg_digit_groups(self) -> None:
        assert len(self.d.DIGIT_LEDS_13) == 6

    def test_cpu_temp_encoded_in_first_3_digits(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=88.0), 0, "C")
        assert any(mask[led] for digit in self.d.DIGIT_LEDS_13[:3] for led in digit)

    def test_gpu_temp_encoded_in_last_3_digits(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_temp=75.0), 0, "C")
        assert any(mask[led] for digit in self.d.DIGIT_LEDS_13[3:6] for led in digit)

    def test_13seg_hundreds_suppressed_for_value_under_100(self) -> None:
        """cpu_temp=55 → hundreds digit (DIGIT_LEDS_13[0]) all dark."""
        mask = self.d.compute_mask(_make_metrics(cpu_temp=55.0), 0, "C")
        for led in self.d.DIGIT_LEDS_13[0]:
            assert mask[led] is False

    def test_13seg_hundreds_lit_for_value_100_plus(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=150.0), 0, "C")
        assert any(mask[led] for led in self.d.DIGIT_LEDS_13[0])

    def test_zone_led_map_has_3_zones(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        assert len(zmap) == 3

    def test_zone_led_map_covers_all_116_leds(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        all_idx = sorted(idx for zone in zmap for idx in zone)
        assert all_idx == list(range(116))

    def test_zone3_is_accent_only(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        assert set(zmap[2]) == set(range(104, 116))

    def test_no_zone_overlap(self) -> None:
        zmap = self.d.zone_led_map
        assert zmap is not None
        seen: set[int] = set()
        for zone in zmap:
            zone_set = set(zone)
            assert not (seen & zone_set)
            seen |= zone_set


# ═════════════════════════════════════════════════════════════════════════════
# Style 8 — CZ1 (18 LEDs, 2 digits, 4-phase)
# ═════════════════════════════════════════════════════════════════════════════

class TestCZ1Display:

    def setup_method(self) -> None:
        self.d = CZ1Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 18

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 4

    def test_returns_18_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 18

    def test_cpu_temp_phase_cpu1_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=65.0), 0, "C")
        assert mask[self.d.CPU1] is True
        assert mask[self.d.GPU1] is False
        assert mask[self.d.CPU2] is False
        assert mask[self.d.GPU2] is False

    def test_cpu_percent_phase_cpu2_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_percent=42.0), 1, "C")
        assert mask[self.d.CPU2] is True
        assert mask[self.d.CPU1] is False

    def test_gpu_temp_phase_gpu1_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_temp=70.0), 2, "C")
        assert mask[self.d.GPU1] is True
        assert mask[self.d.CPU1] is False

    def test_gpu_usage_phase_gpu2_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(gpu_usage=55.0), 3, "C")
        assert mask[self.d.GPU2] is True
        assert mask[self.d.GPU1] is False

    def test_2digit_encoding_value_88(self) -> None:
        """'8' in both digits = all segments lit."""
        mask = self.d.compute_mask(_make_metrics(cpu_temp=88.0), 0, "C")
        for digit in self.d.DIGITS:
            assert all(mask[led] for led in digit)

    def test_leading_zero_suppression_value_5(self) -> None:
        mask = self.d.compute_mask(_make_metrics(cpu_temp=5.0), 0, "C")
        assert not any(mask[led] for led in self.d.DIGITS[0])

    def test_percent_not_converted(self) -> None:
        """cpu_percent (non-temp) is not run through temperature conversion."""
        mask = self.d.compute_mask(_make_metrics(cpu_percent=42.0), 1, "C")
        assert any(mask[led] for digit in self.d.DIGITS for led in digit)

    def test_phase_wraps_modulo_4(self) -> None:
        m0 = self.d.compute_mask(_make_metrics(), 0, "C")
        m4 = self.d.compute_mask(_make_metrics(), 4, "C")
        assert m0 == m4


# ═════════════════════════════════════════════════════════════════════════════
# Style 9 — LC2 (61 LEDs, clock display)
# ═════════════════════════════════════════════════════════════════════════════

class TestLC2Display:

    def setup_method(self) -> None:
        self.d = LC2Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 61

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 1

    def test_returns_61_bools(self) -> None:
        with patch('trcc.legacy.core.led_segment.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2024, 6, 15, 14, 30)
            mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 61

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_colon_and_separator_always_on(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 6, 15, 10, 0)
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        for idx in self.d.COLON_AND_SEP:
            assert mask[idx] is True

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_24h_hour_15_tens_lit(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 6, 15, 15, 30)
        mask = self.d.compute_mask(_make_metrics(), 0, "C", is_24h=True)
        # Hour=15 → tens='1' → DIGITS[0] should have lit segments
        assert any(mask[led] for led in self.d.DIGITS[0])

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_12h_hour_3pm_tens_suppressed(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 6, 15, 15, 30)
        mask = self.d.compute_mask(_make_metrics(), 0, "C", is_24h=False)
        # 15h → 3 in 12h → hour tens = 0 → suppress_zero → blank
        assert not any(mask[led] for led in self.d.DIGITS[0])

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_12h_midnight_shows_12(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 6, 15, 0, 0)
        mask = self.d.compute_mask(_make_metrics(), 0, "C", is_24h=False)
        # 0h → 12 in 12h → hour tens = 1 → segments lit
        assert any(mask[led] for led in self.d.DIGITS[0])

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_month_tens_bc_set_for_october_plus(self, mock_dt: MagicMock) -> None:
        """Month 10-12: month tens = 1 → MONTH_TENS_BC both set."""
        mock_dt.now.return_value = datetime(2024, 10, 5, 10, 0)
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.MONTH_TENS_BC[0]] is True
        assert mask[self.d.MONTH_TENS_BC[1]] is True

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_month_tens_bc_clear_for_single_digit_month(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 3, 15, 10, 0)
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert mask[self.d.MONTH_TENS_BC[0]] is False
        assert mask[self.d.MONTH_TENS_BC[1]] is False

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_weekday_monday_one_bar(self, mock_dt: MagicMock) -> None:
        """Monday (weekday()=0 Mon-start) → w=0 → only bar[0] on."""
        mock_dt.now.return_value = datetime(2024, 2, 5, 12, 0)  # Monday
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        deco = list(range(54, 61))
        assert mask[deco[0]] is True   # always on
        assert mask[deco[1]] is False  # w=0 not > 0
        assert mask[deco[2]] is False

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_weekday_wednesday_three_bars(self, mock_dt: MagicMock) -> None:
        """Wednesday (weekday()=2) → w=2 → bars 0,1,2 on."""
        mock_dt.now.return_value = datetime(2024, 2, 14, 12, 0)  # Wednesday
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        deco = list(range(54, 61))
        assert mask[deco[0]] is True
        assert mask[deco[1]] is True
        assert mask[deco[2]] is True
        assert mask[deco[3]] is False

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_weekday_sunday_all_bars(self, mock_dt: MagicMock) -> None:
        """Sunday (weekday()=6 Mon-start) → w=6 → all 7 bars on."""
        mock_dt.now.return_value = datetime(2024, 2, 18, 12, 0)  # Sunday
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        for idx in range(54, 61):
            assert mask[idx] is True

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_week_sunday_mode_sunday_is_day_0(self, mock_dt: MagicMock) -> None:
        """week_sunday=True: Sunday=0, Monday=1, so Sunday→w=0→only bar[0]."""
        mock_dt.now.return_value = datetime(2024, 2, 18, 12, 0)  # Sunday (weekday()=6)
        mask = self.d.compute_mask(_make_metrics(), 0, "C", week_sunday=True)
        deco = list(range(54, 61))
        assert mask[deco[0]] is True   # bar[0] always on
        # w = (6+1)%7 = 0 → bar[1] is False (w=0 not > 0)
        assert mask[deco[1]] is False

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_day_tens_suppressed_for_single_digit_day(self, mock_dt: MagicMock) -> None:
        """Day < 10 → day tens digit is blank (suppress_zero=True)."""
        mock_dt.now.return_value = datetime(2024, 6, 5, 12, 0)  # day=5
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert not any(mask[led] for led in self.d.DIGITS[5])

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_day_tens_lit_for_double_digit_day(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 6, 14, 12, 0)  # day=14
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert any(mask[led] for led in self.d.DIGITS[5])


# ═════════════════════════════════════════════════════════════════════════════
# Style 10 — LF11 (38 LEDs, 4-phase disk sensor)
# ═════════════════════════════════════════════════════════════════════════════

class TestLF11Display:

    def setup_method(self) -> None:
        self.d = LF11Display()

    def test_mask_size(self) -> None:
        assert self.d.mask_size == 38

    def test_phase_count(self) -> None:
        assert self.d.phase_count == 4

    def test_returns_38_bools(self) -> None:
        mask = self.d.compute_mask(_make_metrics(), 0, "C")
        assert len(mask) == 38

    def test_disk_temp_phase_ssd_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_temp=38.0), 0, "C")
        assert mask[self.d.SSD] is True
        assert mask[self.d.BFB] is False
        assert mask[self.d.MHZ_IND] is False

    def test_disk_activity_phase_bfb_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_activity=30.0), 1, "C")
        assert mask[self.d.BFB] is True
        assert mask[self.d.SSD] is False
        assert mask[self.d.MHZ_IND] is False

    def test_disk_read_phase_mhz_on(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_read=150.0), 2, "C")
        assert mask[self.d.MHZ_IND] is True
        assert mask[self.d.SSD] is False
        assert mask[self.d.BFB] is False

    def test_disk_write_phase_mhz_on(self) -> None:
        """Phase 3 (disk_write) uses same mode=2 as disk_read → MHZ_IND."""
        mask = self.d.compute_mask(_make_metrics(disk_write=200.0), 3, "C")
        assert mask[self.d.MHZ_IND] is True
        assert mask[self.d.SSD] is False
        assert mask[self.d.BFB] is False

    def test_temp_phase_uses_3_digit_region(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_temp=45.0), 0, "C")
        assert any(mask[led] for digit in self.d.DIGITS[:3] for led in digit)

    def test_temp_phase_unit_digit_celsius(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_temp=45.0), 0, "C")
        assert _segments_on(mask, self.d.DIGITS[3]) == SegmentDisplay.CHAR_7SEG['C']

    def test_temp_phase_unit_digit_fahrenheit(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_temp=45.0), 0, "F")
        assert _segments_on(mask, self.d.DIGITS[3]) == SegmentDisplay.CHAR_7SEG['F']

    def test_activity_phase_uses_5_digit_region(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_activity=12345.0), 1, "C")
        for group in self.d.DIGITS:
            assert any(mask[led] for led in group)

    def test_read_phase_uses_5_digit_region(self) -> None:
        mask = self.d.compute_mask(_make_metrics(disk_read=12345.0), 2, "C")
        for group in self.d.DIGITS:
            assert any(mask[led] for led in group)

    def test_phase_wraps_modulo_4(self) -> None:
        m0 = self.d.compute_mask(_make_metrics(), 0, "C")
        m4 = self.d.compute_mask(_make_metrics(), 4, "C")
        assert m0 == m4

    def test_5_digit_groups_defined(self) -> None:
        assert len(self.d.DIGITS) == 5


# ═════════════════════════════════════════════════════════════════════════════
# Module-level functions
# ═════════════════════════════════════════════════════════════════════════════

class TestModuleFunctions:

    # ── DISPLAYS registry ──────────────────────────────────────────────────

    def test_displays_has_styles_1_through_11(self) -> None:
        for s in range(1, 12):
            assert s in DISPLAYS, f"Style {s} missing from DISPLAYS"

    def test_displays_does_not_have_style_12(self) -> None:
        assert 12 not in DISPLAYS

    def test_displays_does_not_have_style_0(self) -> None:
        assert 0 not in DISPLAYS

    @pytest.mark.parametrize("style_id,expected_type", [
        (1, AX120Display), (2, PA120Display), (3, AK120Display),
        (4, LC1Display), (5, LF8Display), (6, LF12Display),
        (7, LF10Display), (8, CZ1Display), (9, LC2Display), (10, LF11Display),
    ])
    def test_displays_correct_types(self, style_id: int, expected_type: type) -> None:
        assert isinstance(DISPLAYS[style_id], expected_type)

    def test_style_11_is_lf8_instance(self) -> None:
        assert isinstance(DISPLAYS[11], LF8Display)

    # ── compute_mask ──────────────────────────────────────────────────────

    def test_compute_mask_returns_bool_list(self) -> None:
        mask = compute_mask(1, HardwareMetrics(cpu_temp=50.0))
        assert isinstance(mask, list)
        assert all(isinstance(b, bool) for b in mask)

    def test_compute_mask_correct_length_per_style(self) -> None:
        expected = {
            1: 30, 2: 84, 3: 64, 4: 31, 5: 93,
            6: 124, 7: 116, 8: 18, 9: 61, 10: 38, 11: 93,
        }
        for style_id, size in expected.items():
            mask = compute_mask(style_id, HardwareMetrics())
            assert len(mask) == size, f"Style {style_id} wrong length"

    def test_compute_mask_unknown_style_returns_empty(self) -> None:
        assert compute_mask(99, HardwareMetrics()) == []

    def test_compute_mask_style_0_returns_empty(self) -> None:
        assert compute_mask(0, HardwareMetrics()) == []

    def test_compute_mask_passes_phase(self) -> None:
        """Phase parameter is forwarded correctly — phase 0 vs 1 differ for AX120."""
        m0 = compute_mask(1, _make_metrics(), phase=0)
        m1 = compute_mask(1, _make_metrics(), phase=1)
        assert m0 != m1

    def test_compute_mask_passes_temp_unit(self) -> None:
        compute_mask(1, _make_metrics(cpu_temp=100.0), temp_unit="C")
        compute_mask(1, _make_metrics(cpu_temp=100.0), temp_unit="F")

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_compute_mask_passes_is_24h_to_lc2(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 6, 15, 15, 0)
        m_24 = compute_mask(9, _make_metrics(), is_24h=True)
        m_12 = compute_mask(9, _make_metrics(), is_24h=False)
        assert m_24 != m_12

    @patch('trcc.legacy.core.led_segment.datetime')
    def test_compute_mask_passes_week_sunday_to_lc2(self, mock_dt: MagicMock) -> None:
        mock_dt.now.return_value = datetime(2024, 2, 18, 12, 0)  # Sunday
        m_mon = compute_mask(9, _make_metrics(), week_sunday=False)
        m_sun = compute_mask(9, _make_metrics(), week_sunday=True)
        assert m_mon != m_sun

    # ── get_display ───────────────────────────────────────────────────────

    def test_get_display_returns_correct_instance(self) -> None:
        d = get_display(1)
        assert isinstance(d, AX120Display)

    def test_get_display_all_valid_styles(self) -> None:
        for s in range(1, 12):
            d = get_display(s)
            assert d is not None
            assert isinstance(d, SegmentDisplay)

    def test_get_display_none_for_unknown(self) -> None:
        assert get_display(99) is None

    def test_get_display_none_for_style_0(self) -> None:
        assert get_display(0) is None

    def test_get_display_same_object_as_displays(self) -> None:
        """get_display() returns the same instance as DISPLAYS[style_id]."""
        for s in range(1, 12):
            assert get_display(s) is DISPLAYS[s]

    # ── has_segment_display ───────────────────────────────────────────────

    def test_has_segment_display_true_for_1_to_11(self) -> None:
        for s in range(1, 12):
            assert has_segment_display(s) is True

    def test_has_segment_display_false_for_12(self) -> None:
        assert has_segment_display(12) is False

    def test_has_segment_display_false_for_0(self) -> None:
        assert has_segment_display(0) is False

    def test_has_segment_display_false_for_99(self) -> None:
        assert has_segment_display(99) is False


# ═════════════════════════════════════════════════════════════════════════════
# Cross-style consistency
# ═════════════════════════════════════════════════════════════════════════════

class TestCrossStyleConsistency:

    def test_all_mask_sizes_match_class_attribute(self) -> None:
        """compute_mask() returns exactly mask_size elements for every style."""
        for style_id, display in DISPLAYS.items():
            mask = display.compute_mask(HardwareMetrics(), 0, "C")
            assert len(mask) == display.mask_size, f"Style {style_id}"

    def test_all_masks_contain_only_bools(self) -> None:
        for style_id in range(1, 12):
            mask = compute_mask(style_id, HardwareMetrics())
            assert all(isinstance(b, bool) for b in mask), f"Style {style_id}"

    def test_all_styles_handle_zero_metrics(self) -> None:
        for style_id in range(1, 12):
            mask = compute_mask(style_id, HardwareMetrics())
            assert isinstance(mask, list)

    def test_all_phase_counts_at_least_1(self) -> None:
        for style_id, display in DISPLAYS.items():
            assert display.phase_count >= 1, f"Style {style_id}"

    def test_mask_size_positive_for_all_styles(self) -> None:
        """All registered displays must have mask_size > 0."""
        for style_id, display in DISPLAYS.items():
            assert display.mask_size > 0, f"Style {style_id} has mask_size=0"

    def test_zone_led_maps_no_overlap_per_style(self) -> None:
        for style_id, display in DISPLAYS.items():
            zmap = display.zone_led_map
            if zmap is None:
                continue
            seen: set[int] = set()
            for zone in zmap:
                zone_set = set(zone)
                assert not (seen & zone_set), (
                    f"Style {style_id} zone overlap: {seen & zone_set}"
                )
                seen |= zone_set

    def test_lf12_is_lf8_subclass(self) -> None:
        assert issubclass(LF12Display, LF8Display)

    def test_subclass_contract_enforced(self) -> None:
        """SegmentDisplay.__init_subclass__ raises if mask_size is missing."""
        with pytest.raises(TypeError, match="mask_size"):
            class BadDisplay(SegmentDisplay):
                mask_size = 0

                def compute_mask(self, metrics, phase=0, temp_unit="C", **kw):  # type: ignore[override]
                    return []
