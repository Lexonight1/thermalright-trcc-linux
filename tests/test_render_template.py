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
