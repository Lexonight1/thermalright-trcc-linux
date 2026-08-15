"""A frame that isn't the shape we declare must say so (#262).

The bulk header tells the firmware how to de-block the payload.  When the two
disagree the panel paints only the overlap — chriszerbin's "send-image fills
roughly the top/left third".  It failed silently: the command reported
success, the log said the frame went out, and only the glass disagreed.
"""
from __future__ import annotations

import logging

import pytest

from trcc.adapters.device.bulk_lcd import jpeg_dimensions


def _jpeg(width: int, height: int) -> bytes:
    """A real JPEG of exactly *width* x *height*, via the shipping renderer."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    img = QImage(width, height, QImage.Format.Format_RGB888)
    img.fill(0xFF3060A0)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 90)
    buf.close()
    return bytes(ba)


def test_jpeg_dimensions_reads_the_real_shape_without_decoding() -> None:
    """Header-only, so it is affordable on every frame."""
    assert jpeg_dimensions(_jpeg(854, 480)) == (854, 480)
    assert jpeg_dimensions(_jpeg(480, 854)) == (480, 854)


def test_it_declines_on_non_jpeg_and_junk() -> None:
    assert jpeg_dimensions(b"") is None
    assert jpeg_dimensions(b"\x00" * 64) is None
    assert jpeg_dimensions(b"\xff\xd8") is None          # SOI then nothing


def test_262s_exact_mismatch_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """854x480 panel, 480x854 payload — the reported case, named in the log.

    MUTATION CHECK: remove the ``_warn_if_payload_shape_disagrees`` call from
    ``_prepare_frame`` and this fails with an empty warning list.
    """
    from tests.conftest import FakeBulkTransport
    from trcc.adapters.device.bulk_lcd import BulkLcd, bulk_profile
    from trcc.core.registry import find_product

    device = BulkLcd(find_product(0x87AD, 0x70DB), FakeBulkTransport())
    _, device._profile = bulk_profile(11, 5)       # 854x480, JPEG

    with caplog.at_level(logging.WARNING):
        device._prepare_frame(_jpeg(480, 854))     # transposed, as #262 sends

    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a shape mismatch must not pass silently"
    assert "480x854" in warnings[0] and "854x480" in warnings[0]


def test_a_correct_frame_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The guard must not cry wolf on the theme path, which is already right."""
    from tests.conftest import FakeBulkTransport
    from trcc.adapters.device.bulk_lcd import BulkLcd, bulk_profile
    from trcc.core.registry import find_product

    device = BulkLcd(find_product(0x87AD, 0x70DB), FakeBulkTransport())
    _, device._profile = bulk_profile(11, 5)

    with caplog.at_level(logging.WARNING):
        device._prepare_frame(_jpeg(854, 480))     # what build_frame produces

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
