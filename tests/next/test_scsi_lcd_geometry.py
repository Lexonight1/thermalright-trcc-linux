"""ScsiLcd handshake-derived geometry tests.

Verifies that ``ScsiLcd.connect()`` populates ``HandshakeResult.resolution``
and the device's ``profile`` from the FBL byte the device reports
(``response[0]`` of the poll), not from ``info.native_resolution``.

Boot signature (``\\xa1\\xa2\\xa3\\xa4`` at ``resp[4:8]``) retries are
also covered — after up to ``_BOOT_MAX_RETRIES`` cycles the next response
should be the real poll byte.
"""
from __future__ import annotations

import pytest

from trcc.next.adapters.device.scsi_lcd import ScsiLcd
from trcc.next.core.models import Kind, ProductInfo, Wire
from trcc.next.core.protocol import get_profile

from .conftest import FakeScsiTransport

# ── Synthetic poll responses ─────────────────────────────────────────


_BOOT_SIGNATURE = b"\xa1\xa2\xa3\xa4"


def _poll_response(fbl: int, *, size: int = 0xE100) -> bytes:
    """Build a poll-response buffer with the given FBL byte at offset 0."""
    resp = bytearray(size)
    resp[0] = fbl
    return bytes(resp)


def _booting_response(size: int = 0xE100) -> bytes:
    """Response that has the boot signature at resp[4:8] — triggers retry."""
    resp = bytearray(size)
    resp[4:8] = _BOOT_SIGNATURE
    return bytes(resp)


def _make_scsi(transport: FakeScsiTransport, *, fbl: int = 100,
               native_resolution: tuple[int, int] = (320, 320)) -> ScsiLcd:
    info = ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="Frozen Warframe LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=fbl, native_resolution=native_resolution,
        orientations=(0, 90, 180, 270),
    )
    return ScsiLcd(info, transport)


# ── FBL byte derives resolution + profile ───────────────────────────


@pytest.mark.parametrize("fbl,expected_resolution", [
    (100, (320, 320)),    # canonical 320×320 big-endian
    (101, (320, 320)),    # 320×320 variant (same dims, different encoding)
    (102, (320, 320)),    # 320×320 variant
    (54,  (360, 360)),    # 360×360 JPEG square panel
    (72,  (480, 480)),    # 480×480 panel
])
def test_handshake_derives_resolution_from_fbl(
    fake_scsi: FakeScsiTransport,
    fbl: int, expected_resolution: tuple[int, int],
) -> None:
    """SCSI handshake returns FBL-derived resolution, not the registry static."""
    fake_scsi.read_script.append(_poll_response(fbl))
    device = _make_scsi(fake_scsi)

    result = device.connect()

    assert result.resolution == expected_resolution, (
        f"FBL {fbl}: expected {expected_resolution}, got {result.resolution}"
    )
    assert result.fbl == fbl
    assert result.pm_byte == fbl   # SCSI uses PM=FBL convention
    assert device._profile is not None
    assert device._profile.resolution == expected_resolution


def test_handshake_caches_canonical_profile(fake_scsi: FakeScsiTransport) -> None:
    """The cached profile is exactly what ``get_profile(fbl, fbl)`` returns."""
    fake_scsi.read_script.append(_poll_response(100))
    device = _make_scsi(fake_scsi)

    device.connect()

    expected = get_profile(100, 100)
    assert device._profile is not None
    assert device._profile == expected
    assert device._profile.big_endian is True   # FBL=100 is big-endian RGB565
    assert device._profile.jpeg is False
    assert device._profile.rotate is False


# ── Empty poll falls back to registry FBL ────────────────────────────


def test_empty_poll_falls_back_to_info_fbl(fake_scsi: FakeScsiTransport) -> None:
    """When poll returns empty bytes, use ``info.fbl`` (or 100 as last resort).

    Mirrors the legacy behavior: the device sometimes returns nothing on
    the first poll; we still complete the handshake using the registry's
    declared FBL so the rest of the pipeline has a sane profile.
    """
    fake_scsi.read_script.append(b"")
    device = _make_scsi(fake_scsi, fbl=100)

    result = device.connect()

    assert result.fbl == 100
    assert result.resolution == (320, 320)
    assert device._profile is not None


# ── Boot-signature retry ─────────────────────────────────────────────


def test_boot_signature_triggers_retry_until_real_response(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boot-signature response (A1 A2 A3 A4 at resp[4:8]) should retry.

    The transport returns the boot signature twice, then a real FBL byte
    on the third poll. The connect() should sleep + retry and end up with
    the third response.
    """
    # Avoid the 3-second sleep between retries in tests
    monkeypatch.setattr("trcc.next.adapters.device.scsi_lcd._BOOT_WAIT_S", 0.0)

    fake_scsi.read_script.append(_booting_response())
    fake_scsi.read_script.append(_booting_response())
    fake_scsi.read_script.append(_poll_response(54))   # finally ready
    device = _make_scsi(fake_scsi)

    result = device.connect()

    assert result.fbl == 54
    assert result.resolution == (360, 360)


# ── send() uses profile.resolution after handshake ───────────────────


def test_send_uses_profile_resolution_for_chunking(
    fake_scsi: FakeScsiTransport,
) -> None:
    """Frame chunking is based on the handshake-derived resolution.

    A 320×320 frame totals 204,800 bytes RGB565 → chunked at 64KB
    (large-display threshold). Each chunk goes through one send_cdb.
    """
    fake_scsi.read_script.append(_poll_response(100))   # 320×320
    device = _make_scsi(fake_scsi)
    device.connect()

    # Clear the recorded init-CDB so we count only frame chunks
    fake_scsi.sent.clear()

    ok = device.send(b"\x00" * (320 * 320 * 2))

    assert ok is True
    # 204,800 / 65,536 = 3 chunks (+1 partial = 4 total)
    assert len(fake_scsi.sent) == 4


def test_send_small_display_uses_small_chunk_size(
    fake_scsi: FakeScsiTransport,
) -> None:
    """A 240×240 device's send chunks at 0xE100 (57,600) not 0x10000."""
    # FBL=36 → 240×240 (pixels=57,600 ≤ 76,800 small-display threshold)
    fake_scsi.read_script.append(_poll_response(36))
    device = _make_scsi(fake_scsi)
    device.connect()

    fake_scsi.sent.clear()
    ok = device.send(b"\x00" * (240 * 240 * 2))   # 115,200 bytes

    assert ok is True
    # 115,200 / 57,600 = 2 chunks exactly
    assert len(fake_scsi.sent) == 2
    # Each chunk data length matches the small chunk size
    for _cdb, data in fake_scsi.sent:
        assert len(data) == 0xE100


# ── Disconnect clears profile ────────────────────────────────────────


def test_disconnect_clears_profile_and_handshake(
    fake_scsi: FakeScsiTransport,
) -> None:
    fake_scsi.read_script.append(_poll_response(100))
    device = _make_scsi(fake_scsi)
    device.connect()
    assert device._profile is not None

    device.disconnect()

    assert device._profile is None
    assert device._handshake is None


# ── Public profile property visible to callers ───────────────────────


def test_profile_property_exposes_cached_profile(
    fake_scsi: FakeScsiTransport,
) -> None:
    """``device.profile`` must return the same DeviceProfile cached at handshake."""
    fake_scsi.read_script.append(_poll_response(54))
    device = _make_scsi(fake_scsi)
    device.connect()

    assert device.profile is device._profile
    assert device.profile is not None
    assert device.profile.resolution == (360, 360)


def test_profile_property_is_none_pre_handshake(
    fake_scsi: FakeScsiTransport,
) -> None:
    """Before connect(), profile is None — matches Device ABC default."""
    device = _make_scsi(fake_scsi)
    assert device.profile is None
