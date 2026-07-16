"""HidLcd handshake-derived geometry tests.

Verifies that ``HidLcd.connect()`` populates ``HandshakeResult.resolution``
and ``HandshakeResult.fbl`` from the PM/SUB bytes in the handshake response,
not from ``info.native_resolution``. Until commit 2 of Phase A.0, every
device's resolution was taken from the static registry — which silently
sent frames at the wrong dimensions for any variant whose PM-derived
resolution differed from the registry default.

These tests assert the post-commit behaviour: PM byte in → correct
(width, height) + ``DeviceProfile`` cached on the device, regardless of
what the registry's ``native_resolution`` says.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device.hid_lcd import HidLcd
from trcc.core.errors import HandshakeError
from trcc.core.models import Kind, ProductInfo, Wire
from trcc.core.protocol import get_profile

from .conftest import FakeBulkTransport

# ── Synthetic handshake responses ─────────────────────────────────────


_TYPE2_MAGIC = b"\xda\xdb\xdc\xdd"


def _type2_response(pm: int, sub: int = 0) -> bytes:
    """Build a 512-byte Type 2 handshake response with given PM/SUB bytes."""
    resp = bytearray(512)
    resp[0:4] = _TYPE2_MAGIC
    resp[4] = sub
    resp[5] = pm
    resp[12] = 0x01   # required by validator
    return bytes(resp)


def _type3_response(fbl_indicator: int) -> bytes:
    """Build a 1024-byte Type 3 handshake response.

    Validator requires resp[0] ∈ {0x65, 0x66}. FBL = resp[0] - 1.
    """
    resp = bytearray(1024)
    resp[0] = fbl_indicator
    return bytes(resp)


# ── Test rig ──────────────────────────────────────────────────────────


def _make_type2(transport: FakeBulkTransport, *,
                native_resolution: tuple[int, int] = (240, 320)) -> HidLcd:
    """Build a Type 2 HidLcd over a fake transport (AussieMakerGeek's VID/PID)."""
    info = ProductInfo(
        vid=0x0416, pid=0x5302,
        vendor="Winbond", product="USB Display (HID Type 2)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=2, native_resolution=native_resolution,
        orientations=(0, 90, 180, 270),
    )
    return HidLcd(info, transport)


def _make_type3(transport: FakeBulkTransport,
                native_resolution: tuple[int, int] = (320, 320)) -> HidLcd:
    """Build a Type 3 HidLcd over a fake transport."""
    info = ProductInfo(
        vid=0x0418, pid=0x5303,
        vendor="ALi Corp", product="LCD Display (HID Type 3)",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=3, fbl=100, native_resolution=native_resolution,
        orientations=(0, 90, 180, 270),
    )
    return HidLcd(info, transport)


# ── Type 2: PM byte derives resolution + profile ──────────────────────


@pytest.mark.parametrize("pm,expected_resolution,expected_fbl", [
    # AussieMakerGeek's Frozen Warframe SE — registry says (240, 320),
    # handshake PM=58 → FBL=58 → (320, 240) landscape. Pre-commit-2, this
    # was the wrong value (240, 320); now it must be (320, 240).
    (58, (320, 240), 58),
    # PM=72 → FBL=72 (no override) → (480, 480) square panel.
    (72, (480, 480), 72),
    # PM=32 → FBL=100 (override) → (320, 320) big-endian.
    (32, (320, 320), 100),
    # PM=64 → FBL=114 (override) → (1600, 720) widescreen.
    (64, (1600, 720), 114),
    # PM=10 → FBL=224 (override), but 224 needs PM-disambiguation:
    # _FBL_224_BY_PM[10] = (960, 540).
    (10, (960, 540), 224),
    # PM=5 → FBL=50 (override) → (320, 240).
    (5,  (320, 240),  50),
])
def test_type2_handshake_derives_resolution_from_pm(
    fake_bulk: FakeBulkTransport,
    pm: int, expected_resolution: tuple[int, int], expected_fbl: int,
) -> None:
    """Type 2 handshake must return PM-derived resolution + FBL, not registry static."""
    fake_bulk.read_script.append(_type2_response(pm))
    device = _make_type2(fake_bulk)

    result = device.connect()

    assert result.resolution == expected_resolution, (
        f"PM {pm}: expected {expected_resolution}, got {result.resolution}"
    )
    assert result.fbl == expected_fbl, (
        f"PM {pm}: expected FBL {expected_fbl}, got {result.fbl}"
    )
    assert result.pm_byte == pm
    # Profile is cached on the device for frame builders.
    assert device._profile is not None
    assert device._profile.resolution == expected_resolution


def test_type2_pm_sub_compound_uses_correct_fbl(
    fake_bulk: FakeBulkTransport,
) -> None:
    """(PM, SUB) compound key takes precedence over the PM override table.

    PM=1 SUB=48 should resolve to FBL 114 (1600×720), not FBL 1 (registry default).
    """
    fake_bulk.read_script.append(_type2_response(pm=1, sub=48))
    device = _make_type2(fake_bulk)

    result = device.connect()

    assert result.fbl == 114
    assert result.resolution == (1600, 720)
    assert result.pm_byte == 1
    assert result.sub_byte == 48


# ── Type 3: FBL byte derives resolution + profile ─────────────────────


@pytest.mark.parametrize("indicator,expected_fbl,expected_resolution", [
    (0x65, 100, (320, 320)),   # FBL=100 → 320×320 big-endian RGB565
    (0x66, 101, (320, 320)),   # FBL=101 → 320×320 big-endian RGB565 (variant)
])
def test_type3_handshake_derives_resolution_from_fbl(
    fake_bulk: FakeBulkTransport,
    indicator: int, expected_fbl: int, expected_resolution: tuple[int, int],
) -> None:
    """Type 3 handshake uses FBL byte directly (resp[0] - 1)."""
    fake_bulk.read_script.append(_type3_response(indicator))
    device = _make_type3(fake_bulk)

    result = device.connect()

    assert result.fbl == expected_fbl
    assert result.resolution == expected_resolution
    assert device._profile is not None
    assert device._profile.resolution == expected_resolution


# ── Profile flags reach the frame builder ─────────────────────────────


def test_type2_profile_drives_jpeg_frame_header_resolution(
    fake_bulk: FakeBulkTransport,
) -> None:
    """For a JPEG frame, the 20-byte header must carry the PM-derived (w, h).

    AussieMakerGeek's case (PM=58 → 320×240 landscape): if his pipeline ever
    sends JPEG, the frame header must say 320×240, not (240, 320) registry.
    """
    fake_bulk.read_script.append(_type2_response(pm=58))
    device = _make_type2(fake_bulk)
    device.connect()

    # Build a JPEG-like payload (FF D8 magic + minimum bytes)
    jpeg_payload = b"\xff\xd8" + b"\x00" * 100
    packet = device._build_frame_type2(jpeg_payload)

    # JPEG branch: bytes[6:8] = 0x00 0x00, bytes[8:12] = struct.pack('<HH', w, h)
    assert packet[6:8] == b"\x00\x00", "JPEG mode flag wrong"
    # Width and height in the frame header should now be PM-derived (320, 240),
    # not the registry's (240, 320).
    import struct
    width, height = struct.unpack('<HH', packet[8:12])
    assert (width, height) == (320, 240)


def test_type2_rgb565_frame_header_stays_hardcoded_240x320(
    fake_bulk: FakeBulkTransport,
) -> None:
    """RGB565 mode keeps the C# hardcoded 240×320 header regardless of PM.

    This is the C# protocol contract: in mode 3 (RGB565), the header
    literally says 240×320 for every device. Don't accidentally derive
    this from the profile — the pixel-count consistency the device
    relies on is in the byte count, not the declared dimensions.
    """
    fake_bulk.read_script.append(_type2_response(pm=58))
    device = _make_type2(fake_bulk)
    device.connect()

    # Non-JPEG payload (no FF D8 prefix)
    rgb565_payload = b"\x00" * 100
    packet = device._build_frame_type2(rgb565_payload)

    assert packet[6:8] == b"\x01\x00", "RGB565 mode flag wrong"
    import struct
    width, height = struct.unpack('<HH', packet[8:12])
    assert (width, height) == (240, 320), "RGB565 header must stay 240×320"


# ── Pre-handshake frame build falls back to native_resolution ─────────


def test_pre_handshake_frame_uses_info_native_resolution(
    fake_bulk: FakeBulkTransport,
) -> None:
    """Building a frame before connect() falls back to info.native_resolution.

    Smoke tests / fixture flows occasionally build frames without a real
    handshake. The frame builder must not crash with AttributeError on
    a None profile — it falls back to the registry value.
    """
    device = _make_type2(fake_bulk, native_resolution=(240, 320))
    # No handshake performed
    assert device._profile is None

    jpeg_payload = b"\xff\xd8" + b"\x00" * 100
    packet = device._build_frame_type2(jpeg_payload)

    import struct
    width, height = struct.unpack('<HH', packet[8:12])
    assert (width, height) == (240, 320), (
        "Pre-handshake JPEG frame must use info.native_resolution as fallback"
    )


# ── Disconnect clears cached profile ──────────────────────────────────


def test_disconnect_clears_profile(fake_bulk: FakeBulkTransport) -> None:
    """After disconnect(), _profile is None so the next connect() starts fresh."""
    fake_bulk.read_script.append(_type2_response(pm=58))
    device = _make_type2(fake_bulk)
    device.connect()
    assert device._profile is not None

    device.disconnect()

    assert device._profile is None
    assert device._handshake is None


# ── Spot-check: profile fields match what get_profile() would say ─────


def test_type2_cached_profile_is_canonical(fake_bulk: FakeBulkTransport) -> None:
    """The cached profile is exactly what ``get_profile(fbl, pm)`` returns."""
    fake_bulk.read_script.append(_type2_response(pm=58))
    device = _make_type2(fake_bulk)
    device.connect()

    expected = get_profile(58, 58)
    assert device._profile == expected


# ── Type 2 send: 512-byte chunked delivery (C# ThreadSendDeviceData2) ──


def test_type2_send_chunks_frame_in_512_byte_writes(
    fake_bulk: FakeBulkTransport,
) -> None:
    """The C# hidList2 path delivers the picture as 512-byte writes, never one
    blob — the firmware reads fixed-size reports and only latches the frame when
    it arrives that way (#150: a single bulk write handshakes clean, reports
    every byte transferred, yet the panel stays on its boot logo). The packet
    is 512-aligned, so every chunk must be exactly full and reassemble to the
    built packet byte-for-byte.
    """
    from trcc.adapters.device.hid_lcd import _EP_WRITE, _USB_BULK_ALIGNMENT

    fake_bulk.read_script.append(_type2_response(pm=58))
    device = _make_type2(fake_bulk)
    device.connect()

    # A full RGB565 frame for this panel (320×240×2), unaligned deliberately.
    payload = b"\x00" * (320 * 240 * 2)
    packet = device._build_frame_type2(payload)
    assert len(packet) % _USB_BULK_ALIGNMENT == 0, "packet should be 512-aligned"

    frame_start = len(fake_bulk.writes)   # ignore the handshake write
    assert device.send(payload) is True

    chunks = fake_bulk.writes[frame_start:]
    assert len(chunks) == len(packet) // _USB_BULK_ALIGNMENT, (
        "frame must be split into one write per 512-byte chunk"
    )
    assert all(ep == _EP_WRITE for ep, _ in chunks), "every chunk goes to EP 0x02"
    assert all(len(data) == _USB_BULK_ALIGNMENT for _, data in chunks), (
        "every chunk is exactly 512 bytes"
    )
    assert b"".join(data for _, data in chunks) == packet, (
        "reassembled chunks must equal the built packet"
    )


# ── Edge cases: serial parsing, validation failures, malformed bytes ──


def _type2_response_with_serial(pm: int, serial_hex: bytes) -> bytes:
    """Type 2 response carrying a 16-byte serial at resp[20:36].

    Marker byte resp[16] = 0x10 signals that the serial slot is
    populated (legacy convention — without it the parser ignores
    bytes 20-36 even if they're non-zero).
    """
    resp = bytearray(512)
    resp[0:4] = _TYPE2_MAGIC
    resp[4] = 0     # sub
    resp[5] = pm
    resp[12] = 0x01
    resp[16] = 0x10
    resp[20:36] = serial_hex[:16].ljust(16, b"\x00")
    return bytes(resp)


def test_type2_serial_extracted_when_marker_set(
    fake_bulk: FakeBulkTransport,
) -> None:
    """Type 2 handshake carries an optional 16-byte serial at resp[20:36].
    The parser only reads it when resp[16] == 0x10 (legacy marker)."""
    serial_bytes = bytes.fromhex("DEADBEEFCAFEBABE0102030405060708")
    fake_bulk.read_script.append(_type2_response_with_serial(58, serial_bytes))
    device = _make_type2(fake_bulk)

    result = device.connect()

    assert result.serial == "DEADBEEFCAFEBABE0102030405060708"


def test_type2_serial_blank_without_marker(
    fake_bulk: FakeBulkTransport,
) -> None:
    """No marker byte → serial stays empty even if bytes 20-36 are set."""
    resp = bytearray(_type2_response(pm=58))
    # Populate bytes 20-36 but DON'T set the resp[16] = 0x10 marker.
    resp[20:36] = b"\xde\xad\xbe\xef" + b"\x00" * 12
    fake_bulk.read_script.append(bytes(resp))
    device = _make_type2(fake_bulk)

    result = device.connect()

    assert result.serial == ""


def test_type2_rejects_wrong_magic(fake_bulk: FakeBulkTransport) -> None:
    """Validator demands resp[0:4] == DA DB DC DD.  Anything else raises."""
    bad = bytearray(512)
    bad[0:4] = b"\xAA\xBB\xCC\xDD"   # wrong magic
    bad[12] = 0x01
    fake_bulk.read_script.append(bytes(bad))
    device = _make_type2(fake_bulk)

    with pytest.raises(HandshakeError):
        device.connect()


def test_type3_rejects_short_response(fake_bulk: FakeBulkTransport) -> None:
    """Type 3 validator requires len >= 14 — a truncated buffer raises."""
    fake_bulk.read_script.append(b"\x65" + b"\x00" * 8)   # only 9 bytes
    device = _make_type3(fake_bulk)

    with pytest.raises(HandshakeError):
        device.connect()


def test_type3_rejects_wrong_indicator(fake_bulk: FakeBulkTransport) -> None:
    """Type 3 validator demands resp[0] ∈ {0x65, 0x66}."""
    resp = bytearray(1024)
    resp[0] = 0x99   # not a valid indicator
    fake_bulk.read_script.append(bytes(resp))
    device = _make_type3(fake_bulk)

    with pytest.raises(HandshakeError):
        device.connect()


def test_type3_fbl_66_variant(fake_bulk: FakeBulkTransport) -> None:
    """Type 3 FBL=101 (resp[0]=0x66) is the 320×320 variant some Ali Corp
    units report — different SKU, same canvas as FBL=100."""
    fake_bulk.read_script.append(_type3_response(0x66))
    device = _make_type3(fake_bulk)

    result = device.connect()

    assert result.fbl == 101
    assert result.pm_byte == 101
    assert result.resolution == (320, 320)


def test_type3_serial_extracted_from_resp_10_14(
    fake_bulk: FakeBulkTransport,
) -> None:
    """Type 3 carries a 4-byte serial at resp[10:14]."""
    resp = bytearray(_type3_response(0x65))
    resp[10:14] = b"\xAB\xCD\xEF\x01"
    fake_bulk.read_script.append(bytes(resp))
    device = _make_type3(fake_bulk)

    result = device.connect()

    assert result.serial == "ABCDEF01"
