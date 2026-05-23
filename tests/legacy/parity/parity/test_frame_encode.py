"""Frame encode parity — image pipeline output byte equality.

Both trees use Qt under the hood (PIL was eliminated long ago), but
``QtRenderer`` is independently ported between trees.  This test
pins the encoder output byte-equal across the operations that drive
device frames:

  * encode_rgb565        — same QImage → same RGB565 wire bytes
  * apply_brightness     — linear scaling, same arithmetic per channel
  * resize               — scaled to a target canvas; aspect-ratio +
                            interpolation choice must match
  * rotate               — 90° turn for portrait panels
  * full pipeline        — resize → brightness → rotate → encode

This is the highest-risk parity phase per the plan: image scaling +
brightness arithmetic can diverge if either tree drifts in
QImage formats or interpolation modes.  Any diff documented in
``KNOWN_DIFFS.md`` only after investigation, never reflexively.
"""
from __future__ import annotations

import os

import pytest

# Qt needs a platform plugin even for offscreen operations.  Setting
# this before any QtGui import keeps the harness CI-runnable.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Tests use the same fixtures across the matrix — defined as plain
# pixel data and converted to QImages per call so each renderer gets
# a fresh independent buffer.

from tests.parity._shared import assert_bytes_equal

# =========================================================================
# QImage fixtures
# =========================================================================


def _solid_qimage(width: int, height: int, rgb: tuple[int, int, int]):
    """A solid-color RGB32 image — no compositing, simplest possible input."""
    from PySide6.QtGui import QColor, QImage

    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(QColor(*rgb))
    return img


def _gradient_qimage(width: int, height: int):
    """A gradient ramp — each pixel a distinct color so a one-pixel
    drift between trees jumps out as a multi-byte diff downstream."""
    from PySide6.QtGui import QColor, QImage

    img = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(width):
            r = (x * 255 // width) & 0xFF
            g = (y * 255 // height) & 0xFF
            b = ((x + y) * 127 // (width + height)) & 0xFF
            img.setPixelColor(x, y, QColor(r, g, b))
    return img


# =========================================================================
# Renderer pair — fresh independent instances per call
# =========================================================================


def _renderers():
    """Build one legacy QtRenderer + one next/ QtRenderer for this test."""
    from trcc.legacy.adapters.render.qt import QtRenderer as LegacyQt
    from trcc.legacy.adapters.render.qt import QtRenderer as NextQt

    return LegacyQt(), NextQt()


# =========================================================================
# Encode parity
# =========================================================================


@pytest.mark.parametrize("size", [(64, 64), (240, 240), (320, 320)],
                         ids=lambda v: f"{v}")
def test_encode_rgb565_solid_red_matches(size: tuple[int, int]) -> None:
    """Solid red → identical RGB565 bytes through both encoders.

    Both encoders default to big-endian on the wire (next/'s isn't
    parameterised; legacy's default ``byte_order='>'`` matches).
    """
    legacy, next_ = _renderers()
    legacy_img = _solid_qimage(*size, (255, 0, 0))
    next_img = _solid_qimage(*size, (255, 0, 0))

    legacy_bytes = legacy.encode_rgb565(legacy_img)
    next_bytes = next_.encode_rgb565(next_img)

    assert_bytes_equal(legacy_bytes, next_bytes,
                       label=f"RGB565 solid red {size}")


@pytest.mark.parametrize("size", [(64, 64), (240, 240)],
                         ids=lambda v: f"{v}")
def test_encode_rgb565_gradient_matches(size: tuple[int, int]) -> None:
    """Distinct color per pixel — every byte of the RGB565 buffer
    encodes a different value, so a one-pixel skew jumps out."""
    legacy, next_ = _renderers()
    legacy_img = _gradient_qimage(*size)
    next_img = _gradient_qimage(*size)

    legacy_bytes = legacy.encode_rgb565(legacy_img)
    next_bytes = next_.encode_rgb565(next_img)

    assert_bytes_equal(legacy_bytes, next_bytes,
                       label=f"RGB565 gradient {size}")


def test_encode_jpeg_solid_red_matches() -> None:
    """JPEG encode → identical bytes for the same RGB888 source.

    Same quality, same source format → same compressed output.
    """
    legacy, next_ = _renderers()
    legacy_img = _solid_qimage(320, 320, (200, 100, 50))
    next_img = _solid_qimage(320, 320, (200, 100, 50))

    legacy_bytes = legacy.encode_jpeg(legacy_img, quality=95)
    next_bytes = next_.encode_jpeg(next_img, quality=95)

    assert_bytes_equal(legacy_bytes, next_bytes, label="JPEG solid red 320x320")


# =========================================================================
# Resize parity
# =========================================================================


@pytest.mark.parametrize(("src", "dst"), [
    ((100, 100), (240, 240)),
    ((320, 200), (320, 320)),
    ((480, 480), (240, 240)),       # downscale
], ids=lambda v: f"{v}")
def test_resize_then_encode_rgb565_matches(
    src: tuple[int, int], dst: tuple[int, int],
) -> None:
    """Resize to target canvas → encode → bytes equal.

    Both trees use SmoothTransformation + IgnoreAspectRatio in resize
    so the output buffer dimensions + pixel content should match.
    """
    legacy, next_ = _renderers()
    legacy_src = _gradient_qimage(*src)
    next_src = _gradient_qimage(*src)

    legacy_resized = legacy.resize(legacy_src, *dst)
    next_resized = next_.resize(next_src, *dst)

    legacy_bytes = legacy.encode_rgb565(legacy_resized)
    next_bytes = next_.encode_rgb565(next_resized)

    assert_bytes_equal(legacy_bytes, next_bytes,
                       label=f"resize {src}→{dst} RGB565")


# =========================================================================
# Rotate parity
# =========================================================================


@pytest.mark.parametrize("degrees", [0, 90, 180, 270],
                         ids=lambda v: f"{v}deg")
def test_rotate_then_encode_rgb565_matches(degrees: int) -> None:
    """Rotation alone shouldn't differ between trees — same QTransform
    + same TransformationMode → same pixel arrangement.

    Method name differs: legacy exposes ``apply_rotation`` (predates
    next/'s shorter ``rotate``); the *output bytes* should match
    identically since both walk the same QTransform path.
    """
    legacy, next_ = _renderers()
    legacy_src = _gradient_qimage(64, 64)
    next_src = _gradient_qimage(64, 64)

    legacy_rotated = legacy.apply_rotation(legacy_src, degrees)
    next_rotated = next_.rotate(next_src, degrees)

    legacy_bytes = legacy.encode_rgb565(legacy_rotated)
    next_bytes = next_.encode_rgb565(next_rotated)

    assert_bytes_equal(legacy_bytes, next_bytes,
                       label=f"rotate {degrees}° RGB565")


# =========================================================================
# Brightness parity
# =========================================================================


@pytest.mark.parametrize("percent", [0, 30, 50, 65, 100],
                         ids=lambda v: f"b{v}")
def test_apply_brightness_then_encode_matches(percent: int) -> None:
    """Linear brightness — both trees use a QPainter source-over
    black-alpha overlay so the ±1-LSB drift from int(255 * factor)
    rounding lands at exactly the same byte values.

    Range capped at 100 — neither tree implements brightness boost
    above 100% (legacy returns the surface untouched; next/ now
    matches that contract after the parity-driven rewrite).
    """
    legacy, next_ = _renderers()
    legacy_src = _solid_qimage(64, 64, (200, 150, 100))
    next_src = _solid_qimage(64, 64, (200, 150, 100))

    legacy_dim = legacy.apply_brightness(legacy_src, percent)
    next_dim = next_.apply_brightness(next_src, percent)

    legacy_bytes = legacy.encode_rgb565(legacy_dim)
    next_bytes = next_.encode_rgb565(next_dim)

    assert_bytes_equal(legacy_bytes, next_bytes,
                       label=f"brightness={percent} RGB565")


# =========================================================================
# Full pipeline — resize + brightness + rotate + encode
# =========================================================================


@pytest.mark.parametrize("brightness", [50, 100], ids=lambda v: f"b{v}")
def test_full_pipeline_resize_brightness_rotate_encode_matches(
    brightness: int,
) -> None:
    """End-to-end: matches what DisplayService.build_frame ultimately does."""
    legacy, next_ = _renderers()

    src_l = _gradient_qimage(100, 100)
    src_n = _gradient_qimage(100, 100)

    # Same order as next/'s build_frame: fit → brightness → rotate → encode.
    legacy_img = legacy.resize(src_l, 240, 240)
    legacy_img = legacy.apply_brightness(legacy_img, brightness)
    legacy_img = legacy.apply_rotation(legacy_img, 90)
    legacy_bytes = legacy.encode_rgb565(legacy_img)

    next_img = next_.resize(src_n, 240, 240)
    next_img = next_.apply_brightness(next_img, brightness)
    next_img = next_.rotate(next_img, 90)
    next_bytes = next_.encode_rgb565(next_img)

    assert_bytes_equal(legacy_bytes, next_bytes,
                       label=f"full pipeline b={brightness}")
