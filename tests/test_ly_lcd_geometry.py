"""LyLcd handshake-derived geometry tests.

Two PID variants:
    0x5408 (LY)   — PM = 64 + resp[20]  (resp[20] clamped to ≥1 when ≤3)
                    SUB = resp[22] + 1
    0x5409 (LY1)  — PM = 50 + resp[36]
                    SUB = resp[22]

Both variants resolve to FBL 192 (1920×462 widescreen JPEG) by default,
disambiguated to (1280, 480) or (1920, 440) for PMs 68/69 via _FBL_192_BY_PM.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device.ly_lcd import LyLcd
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeBulkTransport

# ── Synthetic handshake response ──────────────────────────────────────


def _ly_response(*, resp20: int = 0, resp22: int = 0, resp36: int = 0,
                 size: int = 512) -> bytes:
    """Build an LY handshake response.

    Validator requires len >= 37 and resp[0]=3, resp[1]=0xFF, resp[8]=1.
    PM extraction reads resp[20] (LY) or resp[36] (LY1); SUB reads resp[22].
    """
    resp = bytearray(size)
    resp[0] = 3
    resp[1] = 0xFF
    resp[8] = 1
    resp[20] = resp20
    resp[22] = resp22
    resp[36] = resp36
    return bytes(resp)


def _make_ly(transport: FakeBulkTransport, *, pid: int = 0x5408,
             native_resolution: tuple[int, int] = (1920, 462)) -> LyLcd:
    info = ProductInfo(
        vid=0x0416, pid=pid,
        vendor="Winbond",
        product=f"Trofeo Vision 9.16 (pid=0x{pid:04x})",
        wire=Wire.LY, kind=Kind.LCD,
        device_type=5, fbl=192, native_resolution=native_resolution,
        orientations=(0, 180),
    )
    return LyLcd(info, transport)


# ── LY (0x5408): PM = 64 + resp[20] ─────────────────────────────────


@pytest.mark.parametrize("resp20,expected_pm", [
    (0, 65),    # ≤3 clamped to 1 → PM=65
    (1, 65),    # ≤3 clamped to 1 → PM=65
    (2, 65),    # ≤3 clamped to 1 → PM=65
    (3, 65),    # ≤3 clamped to 1 → PM=65
    (4, 68),    # 4 → PM=68 (disambiguated to 1280×480)
    (5, 69),    # 5 → PM=69 (disambiguated to 1920×440)
])
def test_ly_pm_extraction_with_clamp(
    fake_bulk: FakeBulkTransport, resp20: int, expected_pm: int,
) -> None:
    """LY variant clamps resp[20] ≤ 3 to 1 before adding 64 (C# parity)."""
    fake_bulk.read_script.append(_ly_response(resp20=resp20))
    device = _make_ly(fake_bulk, pid=0x5408)

    result = device.connect()

    assert result.pm_byte == expected_pm


@pytest.mark.parametrize("resp20,expected_resolution", [
    (1, (1920, 462)),   # PM=65 → FBL=192 base
    (2, (1920, 462)),   # clamped to 1 → PM=65 → FBL=192 base
    (4, (1280, 480)),   # PM=68 → FBL=192, disambiguated to 1280×480
    (5, (1920, 440)),   # PM=69 → FBL=192, disambiguated to 1920×440
])
def test_ly_handshake_resolution_uses_fbl_192_disambiguation(
    fake_bulk: FakeBulkTransport,
    resp20: int, expected_resolution: tuple[int, int],
) -> None:
    """LY pulls resolution from get_profile(192, PM) — disambiguated by PM."""
    fake_bulk.read_script.append(_ly_response(resp20=resp20))
    device = _make_ly(fake_bulk, pid=0x5408)

    result = device.connect()

    assert result.resolution == expected_resolution
    assert device._profile is not None
    assert device._profile.resolution == expected_resolution
    # FBL=192 is always JPEG + rotate=True
    assert device._profile.jpeg is True
    assert device._profile.rotate is True


# ── LY1 (0x5409): PM = 50 + resp[36] ────────────────────────────────


@pytest.mark.parametrize("resp36,expected_pm,expected_resolution", [
    (15, 65, (1920, 462)),    # 50+15=65 → FBL=192
    (16, 66, (1920, 462)),    # 50+16=66 → FBL=192
    (18, 68, (1280, 480)),    # 50+18=68 → FBL=192 → disambiguated
    (19, 69, (1920, 440)),    # 50+19=69 → FBL=192 → disambiguated
])
def test_ly1_pm_extraction_and_resolution(
    fake_bulk: FakeBulkTransport,
    resp36: int, expected_pm: int, expected_resolution: tuple[int, int],
) -> None:
    """LY1 reads resp[36] (not resp[20]) and adds 50."""
    fake_bulk.read_script.append(_ly_response(resp36=resp36))
    device = _make_ly(fake_bulk, pid=0x5409)

    result = device.connect()

    assert result.pm_byte == expected_pm
    assert result.resolution == expected_resolution


# ── chunk_cmd remains PID-driven (not profile-derived) ──────────────


def test_chunk_cmd_byte_8_value_per_variant(
    fake_bulk: FakeBulkTransport,
) -> None:
    """LY uses chunk header byte[8]=1; LY1 uses byte[8]=2."""
    device_ly = LyLcd(
        ProductInfo(
            vid=0x0416, pid=0x5408,
            vendor="Winbond", product="LY",
            wire=Wire.LY, kind=Kind.LCD,
            device_type=5, fbl=192, native_resolution=(1920, 462),
            orientations=(0, 180),
        ),
        FakeBulkTransport(),
    )
    device_ly1 = LyLcd(
        ProductInfo(
            vid=0x0416, pid=0x5409,
            vendor="Winbond", product="LY1",
            wire=Wire.LY, kind=Kind.LCD,
            device_type=5, fbl=192, native_resolution=(1920, 462),
            orientations=(0, 180),
        ),
        FakeBulkTransport(),
    )
    assert device_ly._chunk_cmd == 1
    assert device_ly1._chunk_cmd == 2


# ── Disconnect clears profile ────────────────────────────────────────


def test_disconnect_clears_profile(fake_bulk: FakeBulkTransport) -> None:
    fake_bulk.read_script.append(_ly_response(resp20=1))
    device = _make_ly(fake_bulk)
    device.connect()
    assert device._profile is not None

    device.disconnect()

    assert device._profile is None
    assert device._handshake is None


# ── Public profile property ──────────────────────────────────────────


def test_profile_property_exposes_cached_profile(
    fake_bulk: FakeBulkTransport,
) -> None:
    fake_bulk.read_script.append(_ly_response(resp20=4))   # PM=68 → 1280×480
    device = _make_ly(fake_bulk, pid=0x5408)
    device.connect()

    assert device.profile is device._profile
    assert device.profile is not None
    assert device.profile.resolution == (1280, 480)
    assert device.profile.jpeg is True


def test_profile_property_is_none_pre_handshake(
    fake_bulk: FakeBulkTransport,
) -> None:
    device = _make_ly(fake_bulk)
    assert device.profile is None


# ── Wire-frame size cap (#251) ────────────────────────────────────────
#
# The LY firmware silently DROPS a JPEG over roughly half a megabyte:
# send() completes, the ACK reads back, and the glass keeps the previous
# frame with nothing in the log.  encode_jpeg has always had a
# shrink-quality loop; the wire path just never passed it a target.


def test_ly_handshake_sets_the_firmware_frame_cap(
    fake_bulk: FakeBulkTransport,
) -> None:
    """The cap is a WIRE property, so LyLcd sets it at handshake."""
    from trcc.adapters.device.ly_lcd import _MAX_FRAME_BYTES

    fake_bulk.read_script.append(_ly_response(resp20=4))
    device = _make_ly(fake_bulk, pid=0x5408)
    device.connect()

    assert device.profile is not None
    assert device.profile.max_frame_bytes == _MAX_FRAME_BYTES
    assert 0 < _MAX_FRAME_BYTES <= 1024 * 1024, "sane firmware limit"


def test_other_wires_stay_uncapped_by_default() -> None:
    """Default 0 = uncapped, so no other panel's output changes."""
    from trcc.core.protocol import DeviceProfile, get_profile

    assert DeviceProfile(width=320, height=320).max_frame_bytes == 0
    assert get_profile(100).max_frame_bytes == 0


def test_encode_payload_shrinks_an_oversized_frame() -> None:
    """An oversized JPEG must degrade in quality, not vanish (#251)."""
    import random

    from PySide6.QtGui import QColor, QGuiApplication, QImage

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.protocol import DeviceProfile

    _ = QGuiApplication.instance() or QGuiApplication([])
    renderer = QtRenderer()

    # High-entropy noise — the worst case for JPEG, and what the reporter
    # hit with dithered collages.  Small canvas keeps the test fast; the
    # cap is scaled down to match so the ratio is realistic.
    random.seed(7)
    img = QImage(480, 480, QImage.Format.Format_RGB888)
    for y in range(img.height()):
        for x in range(img.width()):
            img.setPixelColor(x, y, QColor(random.randint(0, 255),
                                           random.randint(0, 255),
                                           random.randint(0, 255)))

    uncapped = renderer.encode_payload(
        img, DeviceProfile(width=480, height=480, jpeg=True))
    cap = len(uncapped) // 2
    capped = renderer.encode_payload(
        img, DeviceProfile(width=480, height=480, jpeg=True,
                           max_frame_bytes=cap))

    assert len(capped) < len(uncapped), "cap had no effect"
    assert len(capped) <= cap, "capped frame still exceeds the firmware limit"
