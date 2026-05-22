"""LED packet parity across the full style matrix.

Builds on the canary in ``test_led_packet_canary.py`` — same shape,
parameterized over every legacy LED style (1..12) plus every
documented sub variant.  Catches any miscopy in next/'s remap tables
that the canary's PA120-only coverage would miss.

The led_count for each style comes from legacy's ``LED_STYLES``
registry (single source of truth, per CLAUDE.md — never hardcode
domain values in tests).
"""
from __future__ import annotations

import pytest

from tests.parity._shared import (
    assert_bytes_equal,
    gradient_color_array,
    solid_color_array,
    style_by_legacy_id,
)

# =========================================================================
# Style coverage — driven by legacy's LED_STYLES registry
# =========================================================================


def _style_dimensions() -> list[tuple[int, int]]:
    """Return one ``(legacy_style_id, led_count)`` tuple per LED style.

    Imports the registry inside the function so this module stays
    import-safe before legacy is on the path.
    """
    from trcc.legacy.core.models.led import LED_STYLES

    return [(style_id, style.led_count) for style_id, style in LED_STYLES]


# Sub variants documented in legacy ``LED_REMAP_SUB_TABLES``.  Currently
# only LF8/sub=1 (LF25); the harness picks up new variants automatically
# by reading the legacy table.
def _sub_dimensions() -> list[tuple[int, int, int]]:
    """``(legacy_style_id, style_sub, led_count)`` per documented variant."""
    from trcc.legacy.core.models.led import LED_REMAP_SUB_TABLES, LED_STYLES

    return [
        (style_id, sub, LED_STYLES[style_id].led_count)
        for (style_id, sub) in LED_REMAP_SUB_TABLES
    ]


# =========================================================================
# Both-tree pipeline helpers (mirror the canary file)
# =========================================================================


def _legacy_bytes(
    colors: list[tuple[int, int, int]],
    *,
    legacy_style_id: int,
    style_sub: int,
    brightness: int,
    global_on: bool = True,
    is_on: list[bool] | None = None,
) -> bytes:
    from trcc.legacy.adapters.device.led import LedPacketBuilder
    from trcc.legacy.core.models.led import remap_led_colors

    remapped = remap_led_colors(colors, legacy_style_id, style_sub)
    return LedPacketBuilder.build_led_packet(
        remapped, is_on=is_on, global_on=global_on, brightness=brightness,
    )


def _next_bytes(
    colors: list[tuple[int, int, int]],
    *,
    legacy_style_id: int,
    style_sub: int,
    brightness: int,
    global_on: bool = True,
    is_on: list[bool] | None = None,
) -> bytes:
    from trcc.legacy.adapters.device.led import Led, LedPayload
    from trcc.legacy.services.led_segment import remap_led_colors

    style = style_by_legacy_id()[legacy_style_id]
    remapped = remap_led_colors(colors, style, style_sub)
    payload = LedPayload(
        colors=remapped, is_on=is_on,
        global_on=global_on, brightness=brightness,
    )
    return Led._build_packet(payload)


# =========================================================================
# Matrix tests
# =========================================================================


@pytest.mark.parametrize(
    "legacy_style_id,led_count",
    _style_dimensions(),
    ids=lambda val: str(val),
)
def test_solid_red_full_brightness(legacy_style_id: int, led_count: int) -> None:
    """Every style produces identical bytes for an all-red color array
    at brightness=100.  Easiest possible input — any diff here is a
    pure structural mismatch (remap order, header layout, length)."""
    colors = solid_color_array(led_count, (255, 0, 0))
    legacy = _legacy_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=100,
    )
    next_ = _next_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=100,
    )
    assert_bytes_equal(
        legacy, next_,
        label=f"style {legacy_style_id} solid red full brightness",
    )


@pytest.mark.parametrize(
    "legacy_style_id,led_count",
    _style_dimensions(),
    ids=lambda val: str(val),
)
def test_gradient_full_brightness(legacy_style_id: int, led_count: int) -> None:
    """One-distinct-color-per-LED gradient — proves the remap table
    keys each *physical* index to the right *logical* index across
    the entire wire payload.  A one-position misalignment shows up as
    a multi-byte diff that the byte differ pinpoints clearly."""
    colors = gradient_color_array(led_count)
    legacy = _legacy_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=100,
    )
    next_ = _next_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=100,
    )
    assert_bytes_equal(
        legacy, next_,
        label=f"style {legacy_style_id} gradient full brightness",
    )


@pytest.mark.parametrize(
    "legacy_style_id,led_count",
    _style_dimensions(),
    ids=lambda val: str(val),
)
def test_gradient_at_default_brightness(
    legacy_style_id: int, led_count: int,
) -> None:
    """Brightness=65 is next/'s ``LedDeviceSettings`` default — the
    pre-A.18 LCD-style default of 100 used to hide accidental int()
    vs round() drift.  This row pins brightness-scaling parity at
    the level both trees actually ship to users."""
    colors = gradient_color_array(led_count)
    legacy = _legacy_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=65,
    )
    next_ = _next_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=65,
    )
    assert_bytes_equal(
        legacy, next_,
        label=f"style {legacy_style_id} gradient brightness=65",
    )


@pytest.mark.parametrize(
    "legacy_style_id,led_count",
    _style_dimensions(),
    ids=lambda val: str(val),
)
def test_global_off_zeros_every_channel(
    legacy_style_id: int, led_count: int,
) -> None:
    """``global_on=False`` on every style — even with a non-trivial
    color, every body byte must be zero.  This catches a tree forgetting
    to honor the global gate before the brightness multiply."""
    colors = solid_color_array(led_count, (255, 255, 255))
    legacy = _legacy_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=100,
        global_on=False,
    )
    next_ = _next_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=0, brightness=100,
        global_on=False,
    )
    assert_bytes_equal(
        legacy, next_,
        label=f"style {legacy_style_id} global_off",
    )


@pytest.mark.parametrize(
    "legacy_style_id,style_sub,led_count",
    _sub_dimensions(),
    ids=lambda val: str(val),
)
def test_sub_variant_gradient(
    legacy_style_id: int, style_sub: int, led_count: int,
) -> None:
    """Every documented sub variant gets the same gradient + parity
    treatment.  Today: LF8/sub=1 (LF25).  Auto-extends as new sub
    tables land in either tree."""
    colors = gradient_color_array(led_count)
    legacy = _legacy_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=style_sub,
        brightness=100,
    )
    next_ = _next_bytes(
        colors, legacy_style_id=legacy_style_id, style_sub=style_sub,
        brightness=100,
    )
    assert_bytes_equal(
        legacy, next_,
        label=f"style {legacy_style_id} sub={style_sub} gradient",
    )


# =========================================================================
# Coverage sanity — confirm the matrix actually lights up every style
# =========================================================================


def test_matrix_covers_every_legacy_style() -> None:
    """If a future change drops a style from LED_STYLES, this test
    surfaces it — the matrix shrinks and the parity gate weakens.

    The legacy registry exposes 12 styles (AX120 … LF13); if the count
    drifts we want to know explicitly."""
    from trcc.legacy.core.models.led import LED_STYLES

    assert len(_style_dimensions()) == 12 == len(LED_STYLES)


def test_sub_matrix_covers_every_documented_variant() -> None:
    """Same drift-detection for sub-variant coverage."""
    from trcc.legacy.core.models.led import LED_REMAP_SUB_TABLES

    assert len(_sub_dimensions()) == len(LED_REMAP_SUB_TABLES)
