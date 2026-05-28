"""LED color wire-remap — pure data transform.

Locks the byte-for-byte port from legacy ``LED_REMAP_TABLES`` +
``LED_REMAP_SUB_TABLES`` + ``remap_led_colors``. Same assertions as
the legacy test_led.py block, ported to LedStyle-enum keys.
"""
from __future__ import annotations

import pytest

from trcc.core.led_protocol import (
    LED_REMAP_SUB_TABLES,
    LED_REMAP_TABLES,
    remap_led_colors,
)
from trcc.core.models import LedStyle

# ── Table presence + length ───────────────────────────────────────────


@pytest.mark.parametrize("style,length", [
    # Physical wire-LED counts — independent of the logical mask_size
    # the SegmentDisplay produces (LF12 mask=124 logical but wire=141).
    (LedStyle.PA120, 84),
    (LedStyle.AK120, 64),
    (LedStyle.LC1,   31),
    (LedStyle.LF8,   93),
    (LedStyle.LF12,  141),
    (LedStyle.LF10,  146),
    (LedStyle.LC2,   63),
    (LedStyle.LF11,  38),
])
def test_table_lengths_match_legacy(style: LedStyle, length: int) -> None:
    assert len(LED_REMAP_TABLES[style]) == length


def test_lf25_sub_table_present() -> None:
    """LF25 is style LF8 with sub=1 — the only sub-variant in the registry."""
    assert (LedStyle.LF8, 1) in LED_REMAP_SUB_TABLES


# ── Identity passthrough ──────────────────────────────────────────────


def test_unknown_style_returns_identity() -> None:
    """Styles without a remap table return the input list by identity."""
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    assert remap_led_colors(colors, style=LedStyle.AX120) is colors


def test_none_style_returns_identity() -> None:
    """Pre-handshake / unknown PM = None style → passthrough."""
    colors = [(1, 2, 3)]
    assert remap_led_colors(colors, style=None) is colors


# ── PA120 (style 2) wire-remap correctness ────────────────────────────


def test_pa120_swaps_cpu1_and_cpu2() -> None:
    """Legacy table[0]=1, table[1]=0 → physical 0 gets logical 1's color."""
    colors = [(0, 0, 0)] * 84
    colors[0] = (10, 20, 30)
    colors[1] = (40, 50, 60)
    remapped = remap_led_colors(colors, style=LedStyle.PA120)
    assert remapped[0] == (40, 50, 60)
    assert remapped[1] == (10, 20, 30)


def test_pa120_full_indicator_layout() -> None:
    """All 10 PA120 indicators land on the physical positions legacy spec'd."""
    colors = [(0, 0, 0)] * 84
    colors[2] = (100, 0, 0)
    colors[3] = (0, 100, 0)
    colors[4] = (0, 0, 100)
    colors[5] = (50, 50, 0)
    colors[6] = (10, 10, 10)
    colors[7] = (20, 20, 20)
    colors[8] = (30, 30, 30)
    colors[9] = (40, 40, 40)
    colors[80] = (50, 0, 50)
    colors[81] = (0, 50, 50)
    remapped = remap_led_colors(colors, style=LedStyle.PA120)
    assert remapped[82] == (100, 0, 0)
    assert remapped[83] == (0, 100, 0)
    assert remapped[23] == (0, 0, 100)
    assert remapped[24] == (50, 50, 0)
    assert remapped[41] == (10, 10, 10)
    assert remapped[42] == (40, 40, 40)
    assert remapped[59] == (20, 20, 20)
    assert remapped[60] == (30, 30, 30)
    assert remapped[25] == (0, 50, 50)
    assert remapped[26] == (50, 0, 50)


def test_pa120_uniform_color_preserved() -> None:
    """Uniform color in = uniform color out (only positions shuffle)."""
    color: tuple[int, int, int] = (255, 0, 0)
    colors: list[tuple[int, int, int]] = [color] * 84
    remapped = remap_led_colors(colors, style=LedStyle.PA120)
    assert len(remapped) == 84
    assert all(c == color for c in remapped)


# ── Sub-table precedence ──────────────────────────────────────────────


def test_lf8_sub1_overrides_lf8_base() -> None:
    """LF25 (LF8 + sub=1) uses the sub table, not the base LF8 table."""
    base_len = len(LED_REMAP_TABLES[LedStyle.LF8])
    sub_len = len(LED_REMAP_SUB_TABLES[(LedStyle.LF8, 1)])
    colors = [(1, 0, 0)] * 200    # Sub table indices up to 169

    remapped_base = remap_led_colors(colors, style=LedStyle.LF8)
    remapped_sub = remap_led_colors(colors, style=LedStyle.LF8, style_sub=1)

    assert len(remapped_base) == base_len
    assert len(remapped_sub) == sub_len
    assert sub_len != base_len, (
        "If sub == base length, this test does not actually prove "
        "sub-table precedence"
    )


# ── Out-of-range index handling ───────────────────────────────────────


def test_out_of_range_logical_index_yields_black() -> None:
    """Tables may reference indices past the input length → returns black."""
    # LF8 sub-1 table references logical indices up to 169.
    short = [(1, 2, 3)] * 10
    remapped = remap_led_colors(short, style=LedStyle.LF8, style_sub=1)
    # Most LEDs reference indices > 9, so most should be black.
    assert remapped[-1] == (0, 0, 0)
