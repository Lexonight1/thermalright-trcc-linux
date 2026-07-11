"""BulkLcd handshake-derived geometry + JPEG/RGB565 dispatch tests.

USBLCDNew protocol: PM byte at ``resp[24]``, SUB at ``resp[36]``.
- Resolution comes from ``pm_to_fbl`` + ``get_profile`` (mirrors legacy
  ``_bulk_resolution``).
- JPEG vs RGB565 is the Bulk-specific override — every PM uses JPEG
  (cmd=2) except PM=32 which forces RGB565 (cmd=3). This override is
  layered on top of the FBL profile.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device.bulk_lcd import BulkLcd
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeBulkTransport

# ── Synthetic handshake response ──────────────────────────────────────


def _bulk_response(pm: int, sub: int = 0, *, size: int = 1024) -> bytes:
    """Build a Bulk handshake response with given PM/SUB bytes.

    Validator requires len >= 41 and resp[24] != 0; PM lives at resp[24],
    SUB at resp[36].
    """
    resp = bytearray(size)
    resp[24] = pm
    resp[36] = sub
    return bytes(resp)


def _make_bulk(transport: FakeBulkTransport, *,
               vid: int = 0x87AD, pid: int = 0x70DB,
               native_resolution: tuple[int, int] = (480, 480)) -> BulkLcd:
    info = ProductInfo(
        vid=vid, pid=pid,
        vendor="ChiZhu Tech", product="GrandVision 360 AIO",
        wire=Wire.BULK, kind=Kind.LCD,
        device_type=4, fbl=72, native_resolution=native_resolution,
        orientations=(0, 90, 180, 270),
    )
    return BulkLcd(info, transport)


# ── PM byte derives resolution ───────────────────────────────────────


@pytest.mark.parametrize("pm,expected_resolution,expected_fbl", [
    # PM=5 → FBL=50 → 320×240 landscape
    (5,  (320, 240), 50),
    # PM=7 → FBL=64 → 640×480
    (7,  (640, 480), 64),
    # PM=32 → FBL=100 → 320×320 (the RGB565 override case)
    (32, (320, 320), 100),
    # PM=64 → FBL=114 → 1600×720 widescreen
    (64, (1600, 720), 114),
    # Widescreen 224-family — the C# FormCZTVInit m==2 sub-branches.
    (9,  (854, 480), 224),   # pm 9  → is854x480
    (10, (960, 540), 224),   # pm 10 → is960x540
    (12, (800, 480), 224),   # pm 12 → is800x480
    (65, (1920, 462), 192),  # pm 65 → is1920x462
    # PM=50 — a poll-byte "SPI mode 2" value the GrandVision 360 (87ad:70db)
    # reports.  FormCZTVInit has NO case 50 → stays 480×480.  Previously the
    # accreted _BULK_KNOWN_PMS mis-mapped it to 320×240. (#176)
    (50, (480, 480), 72),
    # 224/192-by-PM poll-byte values (HID/LY resolve these via pm_to_fbl, but
    # FormCZTVInit never maps them on the bulk path) → 480×480. (#176)
    (13, (480, 480), 72),
    (63, (480, 480), 72),
    # PM unknown to the bulk FBL table → C# FormCZTVInit (myDeviceMode==2)
    # default FBL=72 (480×480).  NOT the PM echoed as a bogus FBL. (#169)
    (200, (480, 480), 72),
])
def test_handshake_derives_resolution_from_pm(
    fake_bulk: FakeBulkTransport,
    pm: int, expected_resolution: tuple[int, int], expected_fbl: int,
) -> None:
    fake_bulk.read_script.append(_bulk_response(pm))
    device = _make_bulk(fake_bulk)

    result = device.connect()

    assert result.resolution == expected_resolution, (
        f"PM {pm}: expected {expected_resolution}, got {result.resolution}"
    )
    assert result.fbl == expected_fbl
    assert result.pm_byte == pm
    assert device._profile is not None
    assert device._profile.resolution == expected_resolution


@pytest.mark.parametrize("pm,sub", [
    (1, 0), (1, 1), (1, 47), (2, 0), (4, 0), (200, 0), (255, 9),
])
def test_unknown_bulk_pm_defaults_to_480(
    fake_bulk: FakeBulkTransport, pm: int, sub: int,
) -> None:
    """A PM (or PM=1 SUB) the C# FormCZTVInit table doesn't recognise stays
    on the 480×480 base FBL=72 — never the 320×320 that pm_to_fbl's PM-echo
    fallback would have produced. Mirrors the official app's default. (#169)"""
    fake_bulk.read_script.append(_bulk_response(pm, sub))
    device = _make_bulk(fake_bulk)

    result = device.connect()

    assert result.resolution == (480, 480), (
        f"PM={pm} SUB={sub}: expected 480×480 default, got {result.resolution}"
    )
    assert result.fbl == 72


@pytest.mark.parametrize("pm,sub,expected", [
    (1, 48, (1600, 720)),   # known SUB override survives the guard
    (1, 49, (1920, 462)),
])
def test_pm1_known_sub_still_overrides(
    fake_bulk: FakeBulkTransport, pm: int, sub: int,
    expected: tuple[int, int],
) -> None:
    """PM=1 with SUB 48/49 must still resolve its widescreen override — the
    unknown-PM guard only catches the unrecognised SUBs. (#169)"""
    fake_bulk.read_script.append(_bulk_response(pm, sub))
    device = _make_bulk(fake_bulk)

    assert device.connect().resolution == expected


def _formcztv_bulk_resolution(pm: int) -> tuple[int, int]:
    """The resolution the C# ``FormCZTVInit(72, 2, pm, 0)`` yields — the spec.

    Independent statement of the C# rule (the ``switch(pm)`` + ``m==2`` branches);
    the test asserts the real ``BulkLcd`` handshake matches it for every PM.
    """
    override = {
        5: (320, 240), 7: (640, 480), 32: (320, 320),
        64: (1600, 720), 65: (1920, 462),
        9: (854, 480), 10: (960, 540), 11: (854, 480), 12: (800, 480),
    }
    return override.get(pm, (480, 480))


@pytest.mark.parametrize("pm", range(1, 71))
def test_bulk_resolution_matches_formcztvinit_over_full_pm_space(
    fake_bulk: FakeBulkTransport, pm: int,
) -> None:
    """The wall: EVERY bulk PM resolves exactly as the C# FormCZTVInit(72,2,pm,0).

    The bulk path is 100% FormCZTVInit (the C# passes the PM straight in), so
    it's fully bench-decidable — every bulk panel the C# supports resolves
    correctly before anyone plugs one in.  This sweep is the guard against a
    poll-byte PM re-accreting into ``_BULK_KNOWN_PMS`` (the #176 root cause). (#176)
    """
    fake_bulk.read_script.append(_bulk_response(pm))
    result = _make_bulk(fake_bulk).connect()
    expected = _formcztv_bulk_resolution(pm)
    assert result.resolution == expected, (
        f"PM={pm}: bulk resolved {result.resolution}, C# FormCZTVInit says {expected}"
    )


@pytest.mark.parametrize("pm,expected_baseline", [
    (6, 180),   # FW360 Ultra mounts 180° rotated — needs a device baseline (#137)
    (1, 0),     # other PMs carry no hardware-mount baseline
    (5, 0),
    (64, 0),
])
def test_pm_resolves_encode_baseline(
    fake_bulk: FakeBulkTransport, pm: int, expected_baseline: int,
) -> None:
    """PM=6 (FW360, 480×480) resolves a 180° device-only encode baseline so the
    wire frame is pre-rotated to read upright on the glass; every other PM
    resolves 0 (no change). (#137)"""
    fake_bulk.read_script.append(_bulk_response(pm))
    device = _make_bulk(fake_bulk)
    device.connect()

    assert device.profile is not None
    assert device.profile.encode_baseline == expected_baseline


# ── Bulk-specific JPEG/RGB565 override ───────────────────────────────


def test_pm_32_uses_rgb565_overriding_profile_jpeg(
    fake_bulk: FakeBulkTransport,
) -> None:
    """PM=32 forces RGB565 — Bulk's specific exception to its JPEG default."""
    fake_bulk.read_script.append(_bulk_response(pm=32))
    device = _make_bulk(fake_bulk)
    device.connect()

    assert device._profile is not None
    assert device._profile.jpeg is False, "PM=32 must produce profile.jpeg=False"


@pytest.mark.parametrize("pm", [5, 7, 9, 10, 50, 64, 65, 100, 200])
def test_non_pm32_uses_jpeg(
    fake_bulk: FakeBulkTransport, pm: int,
) -> None:
    """Every PM except 32 uses JPEG (Bulk default). Spans known + unknown PMs."""
    fake_bulk.read_script.append(_bulk_response(pm=pm))
    device = _make_bulk(fake_bulk)
    device.connect()

    assert device._profile is not None
    assert device._profile.jpeg is True, f"PM {pm} must produce profile.jpeg=True"


# ── Frame send uses profile resolution + cmd byte ────────────────────


def test_send_jpeg_uses_cmd_2_with_profile_resolution(
    fake_bulk: FakeBulkTransport,
) -> None:
    """Frame header: cmd=2 (JPEG) + width/height from the cached profile."""
    fake_bulk.read_script.append(_bulk_response(pm=5))   # → FBL=50, 320×240
    device = _make_bulk(fake_bulk)
    device.connect()
    fake_bulk.writes.clear()

    payload = b"\xff\xd8" + b"\x00" * 100
    ok = device.send(payload)

    assert ok is True
    # First write contains the 64-byte header + start of payload
    _ep, data = fake_bulk.writes[0]
    import struct
    cmd_word = struct.unpack("<I", data[4:8])[0]
    width = struct.unpack("<I", data[8:12])[0]
    height = struct.unpack("<I", data[12:16])[0]
    assert cmd_word == 2, "JPEG mode must use cmd=2"
    assert (width, height) == (320, 240), \
        "Header carries profile.resolution, not registry static"


def test_send_pm32_uses_cmd_3_rgb565(
    fake_bulk: FakeBulkTransport,
) -> None:
    """PM=32 sends with cmd=3 (RGB565 override) + profile-derived (320, 320)."""
    fake_bulk.read_script.append(_bulk_response(pm=32))
    device = _make_bulk(fake_bulk)
    device.connect()
    fake_bulk.writes.clear()

    ok = device.send(b"\x00" * 100)

    assert ok is True
    _ep, data = fake_bulk.writes[0]
    import struct
    cmd_word = struct.unpack("<I", data[4:8])[0]
    width = struct.unpack("<I", data[8:12])[0]
    height = struct.unpack("<I", data[12:16])[0]
    assert cmd_word == 3, "PM=32 must use cmd=3 (RGB565)"
    assert (width, height) == (320, 320)


# ── Frame magic + structure intact ───────────────────────────────────


def test_send_frame_header_starts_with_handshake_magic(
    fake_bulk: FakeBulkTransport,
) -> None:
    """First 4 header bytes = 12 34 56 78 (USBLCDNew magic, shared with handshake)."""
    fake_bulk.read_script.append(_bulk_response(pm=5))
    device = _make_bulk(fake_bulk)
    device.connect()
    fake_bulk.writes.clear()

    device.send(b"\xff\xd8" + b"\x00" * 100)

    _ep, data = fake_bulk.writes[0]
    assert data[0:4] == bytes([0x12, 0x34, 0x56, 0x78])


# ── Disconnect clears profile ────────────────────────────────────────


def test_disconnect_clears_profile(fake_bulk: FakeBulkTransport) -> None:
    fake_bulk.read_script.append(_bulk_response(pm=5))
    device = _make_bulk(fake_bulk)
    device.connect()
    assert device._profile is not None

    device.disconnect()

    assert device._profile is None
    assert device._handshake is None


# ── Public profile property ──────────────────────────────────────────


def test_profile_property_exposes_cached_profile(
    fake_bulk: FakeBulkTransport,
) -> None:
    fake_bulk.read_script.append(_bulk_response(pm=5))
    device = _make_bulk(fake_bulk)
    device.connect()

    assert device.profile is device._profile
    assert device.profile is not None
    assert device.profile.resolution == (320, 240)
    assert device.profile.jpeg is True


def test_profile_property_is_none_pre_handshake(
    fake_bulk: FakeBulkTransport,
) -> None:
    device = _make_bulk(fake_bulk)
    assert device.profile is None


# ── Send before handshake raises ─────────────────────────────────────


def test_send_before_handshake_raises_transport_error(
    fake_bulk: FakeBulkTransport,
) -> None:
    """send() must refuse when no profile is cached (transport-open guard)."""
    fake_bulk.open()
    device = _make_bulk(fake_bulk)
    # transport is "open" via the fake, but profile is None — guard kicks in

    from trcc.core.errors import TransportError
    with pytest.raises(TransportError):
        device.send(b"\xff\xd8" + b"\x00" * 100)
