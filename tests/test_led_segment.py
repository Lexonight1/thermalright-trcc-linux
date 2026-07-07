"""Magic Qube segment-display rendering.

The Magic Qube (Thermalright) is a 50-LED cooler display: two 7-segment
digits (3 LEDs per segment, wire order c,d,e,g,b,a,f — right digit LED
0-20, left 21-41) plus four corner metric indicators (LED 42-49, two LEDs
each).  Both digits render one 2-digit value (left = tens, right = units)
while a single corner indicator names the metric, rotating through four
phases like CZ1.  The layout was mapped empirically on hardware.
"""
from __future__ import annotations

import pytest

from trcc.core.models import HardwareMetrics, LedStyle
from trcc.services.led_segment import (
    DISPLAYS,
    MagicQubeDisplay,
    _qube_digit_segmap,
    get_display,
)

# 3-LED groups for the RIGHT digit (base 0), keyed by 7-seg label —
# the hardware-validated wire order c, d, e, g, b, a, f.
_RIGHT: dict[str, tuple[int, int, int]] = {
    "c": (0, 1, 2), "d": (3, 4, 5), "e": (6, 7, 8), "g": (9, 10, 11),
    "b": (12, 13, 14), "a": (15, 16, 17), "f": (18, 19, 20),
}
_INDICATORS = {42, 43, 44, 45, 46, 47, 48, 49}
_BORDER = set(range(50, 65))   # contour LEDs, always lit


def _lit(mask: list[bool]) -> set[int]:
    return {i for i, v in enumerate(mask) if v}


def _segments(labels: str, base: int = 0) -> set[int]:
    out: set[int] = set()
    for seg in labels:
        out.update(i + base for i in _RIGHT[seg])
    return out


def test_registered_in_displays() -> None:
    """MAGIC_QUBE resolves to a MagicQubeDisplay instance."""
    assert isinstance(DISPLAYS[LedStyle.MAGIC_QUBE], MagicQubeDisplay)
    assert isinstance(get_display(LedStyle.MAGIC_QUBE), MagicQubeDisplay)


def test_mask_size_and_phase_count() -> None:
    d = MagicQubeDisplay()
    assert d.mask_size == 65
    assert d.phase_count == 4


def test_border_always_lit() -> None:
    """The 15 contour LEDs (50-64) stay lit regardless of value or phase."""
    d = MagicQubeDisplay()
    for phase in range(4):
        mask = d.compute_mask(HardwareMetrics(cpu_temp=0.0), phase=phase)
        assert all(mask[i] for i in _BORDER)


def test_segmap_follows_hardware_wire_order() -> None:
    """The segment→LED map follows the hardware wire order c,d,e,g,b,a,f."""
    assert _qube_digit_segmap(0) == _RIGHT
    left = _qube_digit_segmap(21)
    assert left["c"] == (21, 22, 23)
    assert left["f"] == (39, 40, 41)


def test_renders_two_digit_value_and_indicator() -> None:
    """cpu_temp=52 → '5' left, '2' right, CPU°C lit.

    Matches the hardware-validated render (qube.py 'show 5 2 cpuc').
    """
    d = MagicQubeDisplay()
    mask = d.compute_mask(HardwareMetrics(cpu_temp=52.0), phase=0, temp_unit="C")
    expected = _segments("abdeg") | _segments("acdfg", base=21) | {42, 43} | _BORDER
    assert _lit(mask) == expected


def test_leading_zero_suppressed_on_left_digit() -> None:
    """A single-digit value blanks the tens digit (no leading zero)."""
    d = MagicQubeDisplay()
    lit = _lit(d.compute_mask(HardwareMetrics(cpu_temp=7.0), phase=0))
    assert not any(21 <= i <= 41 for i in lit)             # left digit dark
    assert (lit - _INDICATORS - _BORDER) == _segments("abc")  # '7' on the right


@pytest.mark.parametrize("phase,indicator", [
    (0, (42, 43)),
    (1, (44, 45)),
    (2, (46, 47)),
    (3, (48, 49)),
])
def test_phase_rotation_lights_matching_indicator(
    phase: int, indicator: tuple[int, int],
) -> None:
    """Each phase lights exactly its corner indicator."""
    d = MagicQubeDisplay()
    metrics = HardwareMetrics(
        cpu_temp=33.0, gpu_temp=33.0, cpu_percent=33.0, gpu_usage=33.0,
    )
    mask = d.compute_mask(metrics, phase=phase)
    assert (_lit(mask) & _INDICATORS) == set(indicator)


def test_phase_wraps_modulo_four() -> None:
    """Phase index wraps — phase 4 renders identically to phase 0."""
    d = MagicQubeDisplay()
    m = HardwareMetrics(cpu_temp=42.0)
    assert d.compute_mask(m, phase=4) == d.compute_mask(m, phase=0)


def test_value_clamped_to_99() -> None:
    """Values above 99 clamp to '99' — the display has only two digits."""
    d = MagicQubeDisplay()
    over = _lit(d.compute_mask(HardwareMetrics(cpu_temp=150.0), phase=0))
    assert over == _lit(d.compute_mask(HardwareMetrics(cpu_temp=99.0), phase=0))
