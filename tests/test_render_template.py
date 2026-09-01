"""Renderer.build_frame Template Method — the consolidation increment 2c core.

Exercises the concrete compose→encode skeleton on the real ``QtRenderer`` (the
one shipping Renderer), additive and independent of ``DisplayService``:

* ``bg_fit`` reproduces the C# native-or-black width test (increment 2b) — a
  background wider than the canvas leaves it solid black, never letterboxed.
* ``build_frame`` picks the oriented compose canvas (via ``plan_orientation``)
  and encodes the right wire payload (JPEG vs RGB565) at every display angle.

Byte-for-byte equivalence with the live ``DisplayService.build_frame`` is the
gate for increment 2c.2 (when the service routes through here); this file pins
the skeleton's own contract.
"""
from __future__ import annotations

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.core.models import RenderContent
from trcc.core.protocol import DeviceProfile

ANGLES = (0, 90, 180, 270)

_MJOLNIR = DeviceProfile(320, 240, jpeg=True, rotate=True)   # bulk JPEG rotate
_SQUARE = DeviceProfile(320, 320, big_endian=True)           # RGB565 non-rotate


@pytest.fixture
def r() -> QtRenderer:
    return QtRenderer()


def _fill(r: QtRenderer, w: int, h: int, rgb: tuple[int, int, int]) -> object:
    return r.create_surface(w, h, color=(*rgb, 255))


def _corner(r: QtRenderer, surface: object) -> tuple[int, int, int]:
    """Top-left pixel via the Renderer contract (no toolkit import)."""
    return r.get_pixels_rgb(surface, 4, 4)[0][0]


# ── bg_fit: the C# native-or-black width test (2b) ──────────────────────────

def test_bg_fit_program_wider_than_canvas_stays_black(r: QtRenderer) -> None:
    # 320-wide landscape bg on a 240-wide portrait canvas → black (no letterbox).
    canvas = _fill(r, 240, 320, (0, 0, 0))
    bg = _fill(r, 320, 240, (20, 30, 60))
    out = r.bg_fit(canvas, RenderContent(bg, None))
    assert _corner(r, out) == (0, 0, 0)


def test_bg_fit_program_fits_canvas_draws_native(r: QtRenderer) -> None:
    # 240-wide portrait bg on a 240-wide portrait canvas → native fill.
    canvas = _fill(r, 240, 320, (0, 0, 0))
    bg = _fill(r, 240, 320, (20, 30, 60))
    out = r.bg_fit(canvas, RenderContent(bg, None))
    assert _corner(r, out) == (20, 30, 60)


def test_bg_fit_user_content_composited_as_is(r: QtRenderer) -> None:
    # User uploads arrive pre-fitted → composited regardless of width.
    canvas = _fill(r, 240, 320, (0, 0, 0))
    bg = _fill(r, 320, 240, (20, 30, 60))
    out = r.bg_fit(canvas, RenderContent(bg, None, background_is_user=True))
    assert _corner(r, out) == (20, 30, 60)


def test_bg_fit_none_background_stays_black(r: QtRenderer) -> None:
    canvas = _fill(r, 320, 240, (0, 0, 0))
    out = r.bg_fit(canvas, RenderContent(None, None))
    assert _corner(r, out) == (0, 0, 0)


# ── build_frame: oriented canvas + encode dispatch ──────────────────────────

@pytest.mark.parametrize("angle", ANGLES)
def test_build_frame_jpeg_panel_emits_jpeg(r: QtRenderer, angle: int) -> None:
    bg = _fill(r, 320, 240, (20, 30, 60))
    payload = r.build_frame(
        _MJOLNIR, RenderContent(bg, None), angle, content_is_portrait=False,
    )
    assert payload[:2] == b"\xff\xd8" and payload[-2:] == b"\xff\xd9"


@pytest.mark.parametrize("angle", ANGLES)
def test_build_frame_square_emits_rgb565_of_canvas(r: QtRenderer, angle: int) -> None:
    # Non-rotate square: canvas is 320×320 at every angle → 2 bytes/pixel.
    bg = _fill(r, 320, 320, (20, 30, 60))
    payload = r.build_frame(
        _SQUARE, RenderContent(bg, None), angle, content_is_portrait=False,
    )
    assert len(payload) == 320 * 320 * 2


def test_build_frame_rotate_panel_transposes_canvas_for_portrait(r: QtRenderer) -> None:
    # Portrait content on a rotate panel at 90 → transposed 240×320 canvas.
    bg = _fill(r, 240, 320, (20, 30, 60))
    portrait = DeviceProfile(320, 240, rotate=True)   # RGB565 rotate
    at90 = r.build_frame(
        portrait, RenderContent(bg, None), 90, content_is_portrait=True,
    )
    assert len(at90) == 240 * 320 * 2


# ── encode_rgb565: the wire bytes, which nothing tested ─────────────────────
#
# Every ``encode_rgb565`` in ``tests/`` is a STUB on a fake renderer (conftest,
# test_ipc_server, test_mask_rendering, test_overlay_clock, test_display_rotation,
# test_boot_animation_command).  The real encoder — the one producing the bytes
# that every RGB565 device receives — had no test at all.
#
# These assert the FORMAT, not merely today's output, so they characterise the
# contract rather than pinning an implementation: pure red is 0xF800 in RGB565
# because the packing is 5-6-5, and ``byte_order`` decides which half goes first.


def _mixed(r: QtRenderer, w: int, h: int) -> object:
    """A surface with two distinct colours, so a byte swap is observable."""
    base = _fill(r, w, h, (255, 0, 0))                 # red   -> 0xF800
    return r.composite(base, _fill(r, w // 2, h, (0, 0, 255)), (0, 0))  # blue -> 0x001F


def test_encode_rgb565_packs_five_six_five(r: QtRenderer) -> None:
    """Red is 0xF800 and blue 0x001F — the 5-6-5 packing, not a guess."""
    data = r.encode_rgb565(_fill(r, 2, 1, (255, 0, 0)), byte_order=">")
    assert data == b"\xf8\x00\xf8\x00", data.hex()

    data = r.encode_rgb565(_fill(r, 2, 1, (0, 0, 255)), byte_order=">")
    assert data == b"\x00\x1f\x00\x1f", data.hex()


def test_encode_rgb565_byte_order_swaps_each_pixel(r: QtRenderer) -> None:
    """``byte_order`` swaps the two halves of every 16-bit word, nothing else.

    Asserted on a MIXED surface: on a uniform one whose two bytes happened to
    be equal, a broken swap would pass.
    """
    surface = _mixed(r, 8, 4)
    big = r.encode_rgb565(surface, byte_order=">")
    little = r.encode_rgb565(surface, byte_order="<")

    assert len(big) == len(little) == 8 * 4 * 2
    assert big != little, "the surface must contain a pixel whose bytes differ"
    assert big[0::2] == little[1::2]
    assert big[1::2] == little[0::2]


def test_encode_rgb565_length_is_two_bytes_per_pixel(r: QtRenderer) -> None:
    """Row padding must be stripped: Qt pads scanlines to a 4-byte boundary.

    A 3-wide surface is 6 bytes per row of pixel data but Qt allocates 8, so an
    encoder that returned the raw buffer would hand the device 33% too much and
    shear the image.
    """
    assert len(r.encode_rgb565(_fill(r, 3, 5, (255, 0, 0)), byte_order=">")) == 3 * 5 * 2
