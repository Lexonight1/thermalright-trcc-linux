"""The F5 protocol is one protocol — prove the two devices still agree.

``AliLcd`` (Wire.BULK_ALI, 0416:5406) and ``HidLcd`` type 3 (0418:5303/5304)
speak the same wire protocol.  Until 2026-08-19 each built its own bytes from
its own literals, and they happened to be identical: the same 1040-byte
handshake request, the same 16-byte frame header, the same 204800/1024/16 sizes,
and an identity test one file spelled ``(101, 102)`` and the other ``(0x65,
0x66)`` — the same two numbers.

Nothing checked that.  A protocol correction applied to one file and not the
other would have shipped silently and only surfaced as a panel that stopped
working, on hardware neither of us owns.

The bytes now come from ``adapters.device._f5``.  These tests assert both
devices still emit exactly the same thing, so the duplicate cannot grow back.

MUTATION CHECK -- flip the last byte of ``_f5.CMD_PREFIX`` (0xC8 -> 0xC9) and
run this file plus ``test_ali_lcd.py``.  MEASURED 2026-08-19: **2 failed** —
``test_f5_constants_are_the_decompiled_values`` and
``test_ali_lcd.py::test_handshake_request_bytes``.

Note WHICH tests failed, because it shows the two halves do different jobs.  The
*agreement* tests below did NOT fail: both devices now read the same constant,
so a mutation moves them together and they still agree — agreement can only
catch DIVERGENCE.  Catching a WRONG value takes the literal assertions, anchored
to the decompile rather than to ourselves.  Sharing the constant is what makes
that second kind of test worth writing once instead of twice.
"""
from __future__ import annotations

from trcc.adapters.device import _f5
from trcc.adapters.device.ali_lcd import AliLcd
from trcc.adapters.device.hid_lcd import HidLcd
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeBulkTransport


def _ali_on(transport: FakeBulkTransport) -> AliLcd:
    info = ProductInfo(
        vid=0x0416, pid=0x5406, vendor="Winbond", product="LCD Display",
        wire=Wire.BULK_ALI, kind=Kind.LCD,
        device_type=4, fbl=100, native_resolution=(320, 320),
    )
    return AliLcd(info, transport)


def _ali() -> AliLcd:
    return _ali_on(FakeBulkTransport())


def _hid_type3_on(transport: FakeBulkTransport) -> HidLcd:
    info = ProductInfo(
        vid=0x0418, pid=0x5303, vendor="ALi Corp", product="LCD Display",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=3, fbl=100, native_resolution=(320, 320),
    )
    return HidLcd(info, transport)


def _hid_type3() -> HidLcd:
    return _hid_type3_on(FakeBulkTransport())


# ── The protocol's own literals ──────────────────────────────────────────


def test_f5_constants_are_the_decompiled_values() -> None:
    """The bytes themselves, asserted against the wire oracle.

    Stated as literals ON PURPOSE: every other assertion here compares the two
    devices to each other, which would pass just as happily if both were wrong.
    This is the one row anchored to the decompile instead of to ourselves.
    """
    assert bytes(
        [0xF5, 0x00, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8]) == _f5.CMD_PREFIX
    assert bytes(
        [0xF5, 0x01, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8]) == _f5.FRAME_PREFIX
    assert _f5.DATA_SIZE == 320 * 320 * 2 == 204800
    assert _f5.RESPONSE_SIZE == 1024
    assert _f5.ACK_SIZE == 16
    assert _f5.INIT_SIZE == 1040
    assert _f5.VALID_IDENTITY == (0x65, 0x66) == (101, 102)


def test_init_packet_shape() -> None:
    """1040 bytes: 16-byte header, then 1024 zeros."""
    pkt = _f5.init_packet()
    assert len(pkt) == _f5.INIT_SIZE == 1040
    assert pkt[:8] == _f5.CMD_PREFIX
    assert pkt[8:16] == bytes([0, 0, 0, 0, 0, 0x04, 0, 0])
    assert pkt[16:] == bytes(_f5.RESPONSE_SIZE), "pad must be all zeros"


def test_frame_header_carries_length_little_endian() -> None:
    """bytes[12:16] LE == the payload length (0x00032000 == 204800)."""
    hdr = _f5.frame_header()
    assert len(hdr) == _f5.HEADER_SIZE == 16
    assert hdr[:8] == _f5.FRAME_PREFIX
    assert hdr[12:16] == bytes([0x00, 0x20, 0x03, 0x00])
    assert int.from_bytes(hdr[12:16], "little") == _f5.DATA_SIZE


# ── The point of the file: the two devices agree ─────────────────────────


def test_both_devices_build_the_same_init_packet() -> None:
    """AliLcd's handshake request == HidLcd type 3's, byte for byte."""
    hid_init = _hid_type3()._build_init_packet()
    assert hid_init == _f5.init_packet(), (
        "HidLcd type 3 no longer builds the shared F5 init packet")
    assert len(hid_init) == 1040


def test_both_devices_build_the_same_frame_header() -> None:
    """AliLcd's frame header == the first 16 bytes of HidLcd type 3's frame."""
    payload = bytes(_f5.DATA_SIZE)
    hid_frame = _hid_type3()._build_frame_type3(payload)
    ali_frame = _ali()._prepare_frame(payload)

    assert hid_frame[:16] == ali_frame[:16] == _f5.frame_header(), (
        "the two devices' frame headers have diverged")
    assert len(hid_frame) == len(ali_frame) == 16 + _f5.DATA_SIZE


def test_both_devices_accept_the_same_identity_bytes() -> None:
    """Both accept resp[0] ∈ {0x65, 0x66} and derive their model as resp[0] - 1.

    Driven through each device's real ``connect()`` via the scripted transport,
    not by calling parsers directly — the identity rule is only interesting if
    the shipping path actually applies it.
    """
    for identity in _f5.VALID_IDENTITY:
        resp = bytearray(_f5.RESPONSE_SIZE)
        resp[0] = identity

        ali_t = FakeBulkTransport()
        ali_t.read_script = [bytes(resp)]
        ali = _ali_on(ali_t).connect()

        hid_t = FakeBulkTransport()
        hid_t.read_script = [bytes(resp)]
        hid = _hid_type3_on(hid_t).connect()

        assert ali.model_id == hid.fbl == identity - 1, (
            f"identity {identity:#x}: the two devices derive different models")
        assert ali.resolution == hid.resolution == (320, 320), (
            "both devices resolve 0x65/0x66 to the same 320x320 canvas")
