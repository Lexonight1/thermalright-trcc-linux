"""Phase C canary — one LED packet through legacy + next/ side by side.

The simplest possible parity check: legacy ``LedPacketBuilder`` and
next/'s ``Led._build_packet`` should produce byte-identical output
given the same logical color array + brightness + on-state, with the
same per-style wire remap applied first.

If this fails, every other parity test is moot — the harness itself
isn't working.  If this passes, the rest of Phase C scales.
"""
from __future__ import annotations

import pytest

from tests.parity._shared import (
    assert_bytes_equal,
    gradient_color_array,
    solid_color_array,
    style_by_legacy_id,
)


def _legacy_build(
    *,
    logical_colors: list[tuple[int, int, int]],
    legacy_style_id: int,
    style_sub: int,
    brightness: int,
    global_on: bool = True,
    is_on: list[bool] | None = None,
) -> bytes:
    """Legacy pipeline: remap → build_led_packet → wire bytes."""
    from trcc.adapters.device.led import LedPacketBuilder
    from trcc.core.models.led import remap_led_colors

    remapped = remap_led_colors(logical_colors, legacy_style_id, style_sub)
    return LedPacketBuilder.build_led_packet(
        remapped, is_on=is_on, global_on=global_on, brightness=brightness,
    )


def _next_build(
    *,
    logical_colors: list[tuple[int, int, int]],
    legacy_style_id: int,
    style_sub: int,
    brightness: int,
    global_on: bool = True,
    is_on: list[bool] | None = None,
) -> bytes:
    """Next/ pipeline: remap → Led._build_packet → wire bytes."""
    from trcc.next.adapters.device.led import Led, LedPayload
    from trcc.next.services.led_segment import remap_led_colors

    style = style_by_legacy_id()[legacy_style_id]
    remapped = remap_led_colors(logical_colors, style, style_sub)
    payload = LedPayload(
        colors=remapped,
        is_on=is_on,
        global_on=global_on,
        brightness=brightness,
    )
    return Led._build_packet(payload)


# =========================================================================
# Canary cases
# =========================================================================


def test_canary_pa120_solid_red_full_brightness() -> None:
    """PA120 (legacy style_id=2) — 84 red LEDs, brightness 100, all on.

    PA120 has the largest remap table (84 entries) so a misordered
    remap is most likely to surface here.  Solid red at full
    brightness keeps the *arithmetic* trivial; any diff is purely
    structural.
    """
    legacy = _legacy_build(
        logical_colors=solid_color_array(84, (255, 0, 0)),
        legacy_style_id=2, style_sub=0, brightness=100,
    )
    next_ = _next_build(
        logical_colors=solid_color_array(84, (255, 0, 0)),
        legacy_style_id=2, style_sub=0, brightness=100,
    )
    assert_bytes_equal(legacy, next_, label="PA120 solid red wire bytes")


def test_canary_pa120_gradient_exposes_remap_order() -> None:
    """Same PA120 but a gradient — every LED has a distinct color, so a
    one-index remap mismatch shows up as a 5+ byte diff in the window.

    If the remap order has drifted between trees, this is the test
    that will scream the loudest.
    """
    colors = gradient_color_array(84)
    legacy = _legacy_build(
        logical_colors=colors,
        legacy_style_id=2, style_sub=0, brightness=100,
    )
    next_ = _next_build(
        logical_colors=colors,
        legacy_style_id=2, style_sub=0, brightness=100,
    )
    assert_bytes_equal(legacy, next_, label="PA120 gradient wire bytes")


def test_canary_brightness_scaling_matches() -> None:
    """At brightness=65 (LedDeviceSettings default), the int() vs
    round() trap could surface a ±1 LSB diff on every RGB byte.  This
    test pins that down."""
    legacy = _legacy_build(
        logical_colors=solid_color_array(30, (200, 100, 50)),
        legacy_style_id=1, style_sub=0, brightness=65,
    )
    next_ = _next_build(
        logical_colors=solid_color_array(30, (200, 100, 50)),
        legacy_style_id=1, style_sub=0, brightness=65,
    )
    assert_bytes_equal(legacy, next_, label="brightness-65 wire bytes")


def test_canary_global_off_zeros_every_led() -> None:
    """``global_on=False`` forces every channel to 0 regardless of the
    requested color — confirms both trees implement the same gate."""
    legacy = _legacy_build(
        logical_colors=solid_color_array(30, (255, 255, 255)),
        legacy_style_id=1, style_sub=0, brightness=100,
        global_on=False,
    )
    next_ = _next_build(
        logical_colors=solid_color_array(30, (255, 255, 255)),
        legacy_style_id=1, style_sub=0, brightness=100,
        global_on=False,
    )
    assert_bytes_equal(legacy, next_, label="global_off wire bytes")


def test_canary_is_on_mask_alternates() -> None:
    """Every-other-LED off — exposes off-channel handling.  Combined
    with a non-trivial color the differences would be loud (255 vs 0
    on every other byte triple)."""
    colors = solid_color_array(30, (255, 0, 0))
    mask = [i % 2 == 0 for i in range(30)]
    legacy = _legacy_build(
        logical_colors=colors,
        legacy_style_id=1, style_sub=0, brightness=100,
        is_on=mask,
    )
    next_ = _next_build(
        logical_colors=colors,
        legacy_style_id=1, style_sub=0, brightness=100,
        is_on=mask,
    )
    assert_bytes_equal(legacy, next_, label="alternating is_on wire bytes")


# =========================================================================
# Smoke: harness itself can import both trees in one process
# =========================================================================


def test_both_trees_importable_simultaneously() -> None:
    """Sanity — the entire point of the harness is that both trees
    coexist in one Python process.  This test imports the canonical
    entry points and asserts they're distinct classes."""
    from trcc.adapters.device.led import LedPacketBuilder as LegacyBuilder
    from trcc.next.adapters.device.led import Led as NextLed

    assert LegacyBuilder is not NextLed
    assert LegacyBuilder.__module__.startswith("trcc.adapters")
    assert NextLed.__module__.startswith("trcc.next.adapters")


# =========================================================================
# Self-test: the differ produces actionable output on mismatch
# =========================================================================


def test_diff_bytes_pinpoints_first_divergence() -> None:
    """If the differ doesn't print an offset, debugging real failures
    is misery.  Pin the format here so a future refactor can't quietly
    regress the hex-window output."""
    from tests.parity._shared import diff_bytes

    a = b"\x00\x11\x22\x33\x44\x55\x66\x77" * 4
    b = b"\x00\x11\x22\x33\x4a\x55\x66\x77" + b"\x00\x11\x22\x33\x44\x55\x66\x77" * 3
    diff = diff_bytes(a, b)

    assert diff is not None
    assert diff.offset == 4
    assert diff.legacy_byte == 0x44
    assert diff.next_byte == 0x4a
    # The rendered context names the offset and shows both windows
    rendered = diff.hex_context()
    assert "0x4" in rendered                # offset 4 in hex
    assert "0x44" in rendered
    assert "0x4a" in rendered


def test_diff_bytes_returns_none_on_equal_inputs() -> None:
    from tests.parity._shared import diff_bytes

    assert diff_bytes(b"\x00" * 32, b"\x00" * 32) is None


@pytest.mark.parametrize("legacy_len,next_len", [(10, 8), (8, 10)])
def test_diff_bytes_reports_length_mismatch(legacy_len: int, next_len: int) -> None:
    """A length difference still surfaces an actionable diff entry."""
    from tests.parity._shared import diff_bytes

    legacy = b"\x00" * legacy_len
    next_ = b"\x00" * next_len
    diff = diff_bytes(legacy, next_)

    assert diff is not None
    assert diff.legacy_total_len == legacy_len
    assert diff.next_total_len == next_len
    # The diff offset is at the boundary
    assert diff.offset == min(legacy_len, next_len)
