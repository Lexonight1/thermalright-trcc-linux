"""The F5 wire protocol, and the two panel families that speak it.

`0416:5406` (Elite Vision 360, #212) and `0418:5303/5304` speak the SAME
protocol.  Until 2026-08-19 that was two classes — `AliLcd` on its own
`Wire.BULK_ALI`, and `HidLcd`'s type 3 — each building its own bytes from its
own literals, which happened to be identical: the same 1040-byte handshake
request, the same 16-byte frame header, the same 204800/1024/16 sizes, and an
identity test one file spelled `(101, 102)` and the other `(0x65, 0x66)`.

Nothing checked that.  A correction applied to one file and not the other would
have shipped silently and surfaced only as a panel that stopped working.

The bytes moved to `adapters.device._f5` first (`d7c99cf1`), then the duplicate
class was deleted once the wire-keyed keepalive policy that kept it alive became
a per-device fact.  This file carries what `test_ali_lcd.py` used to prove, now
exercised through the one surviving class.

MUTATION CHECK -- flip the last byte of `_f5.CMD_PREFIX` (0xC8 -> 0xC9).
MEASURED 2026-08-19: **1 failed**, `test_f5_constants_are_the_decompiled_values`.
Note that the round-trip tests below do NOT fail: they drive both ends through
the same constant, so a mutation moves them together.  Catching a WRONG value
takes the literal assertions anchored to the decompile; catching a BROKEN path
takes the round-trips.  Both are needed, and neither substitutes for the other.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device import DEVICES, _f5
from trcc.adapters.device.hid_lcd import HidLcd
from trcc.core.errors import HandshakeError, TransportError
from trcc.core.models import Kind, ProductInfo, Wire
from trcc.core.registry import find_product

from .conftest import FakeBulkTransport


def _info(vid: int = 0x0418, pid: int = 0x5303) -> ProductInfo:
    return ProductInfo(
        vid=vid, pid=pid, vendor="ALi Corp", product="LCD Display",
        wire=Wire.HID, kind=Kind.LCD,
        device_type=3, fbl=100, native_resolution=(320, 320),
    )


def _device(transport: FakeBulkTransport, **kw) -> HidLcd:
    return HidLcd(_info(**kw), transport)


def _reply(identity: int = 0x65) -> bytes:
    resp = bytearray(_f5.RESPONSE_SIZE)
    resp[0] = identity
    return bytes(resp)


# ── The protocol's own literals, anchored to the oracle ──────────────────


def test_f5_constants_are_the_decompiled_values() -> None:
    """The bytes themselves.

    Stated as literals ON PURPOSE: every other assertion here drives both ends
    through `_f5`, so it would pass just as happily if the constant were wrong.
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
    """1040 bytes: a 16-byte header, then 1024 zeros."""
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


# ── The shipping path uses it (was: two classes agreeing) ────────────────


def test_type3_builds_the_shared_init_packet() -> None:
    assert _device(FakeBulkTransport())._build_init_packet() == _f5.init_packet()


def test_type3_frame_is_the_shared_header_plus_fixed_payload() -> None:
    frame = _device(FakeBulkTransport())._build_frame_type3(bytes(_f5.DATA_SIZE))
    assert frame[:16] == _f5.frame_header()
    assert len(frame) == 16 + _f5.DATA_SIZE == 204816


# ── Identity, connect and send (carried over from test_ali_lcd.py) ───────


@pytest.mark.parametrize("identity", [101, 102])
def test_connect_accepts_valid_identity(identity: int) -> None:
    """resp[0] in {101, 102}; the model is resp[0] - 1 (-> FBL 100 / 101)."""
    transport = FakeBulkTransport()
    transport.read_script = [_reply(identity)]

    result = _device(transport).connect()

    assert result.fbl == identity - 1
    assert result.resolution == (320, 320)
    assert transport.writes[0][0] == HidLcd._EP_WRITE == 0x02
    assert transport.writes[0][1] == _f5.init_packet()


@pytest.mark.parametrize("bad", [0, 1, 100, 103, 200])
def test_connect_rejects_invalid_identity(bad: int) -> None:
    transport = FakeBulkTransport()
    transport.read_script = [_reply(bad)] * 4
    with pytest.raises(HandshakeError, match="handshake failed after"):
        _device(transport).connect()


def test_connect_rejects_empty_response() -> None:
    transport = FakeBulkTransport()
    transport.read_script = [b""] * 4
    with pytest.raises(HandshakeError, match="handshake failed after"):
        _device(transport).connect()


def test_send_writes_header_plus_payload_and_reads_ack() -> None:
    transport = FakeBulkTransport()
    transport.read_script = [_reply()]
    dev = _device(transport)
    dev.connect()

    dev.send(bytes(_f5.DATA_SIZE))

    endpoint, data = transport.writes[-1]
    assert endpoint == HidLcd._EP_WRITE
    assert data[:16] == _f5.frame_header()
    assert len(data) == 16 + _f5.DATA_SIZE


def test_send_before_connect_raises() -> None:
    """The panel ignores frames until it has answered its identity exchange.

    `AliLcd` guarded this and `HidLcd` did not, so the merge would have dropped
    it.  The window is real: `connect()` stores `_handshake` only AFTER the
    handshake returns, so a connect that RAISED leaves the transport open with
    no handshake and the base guard passes.
    """
    with pytest.raises(TransportError):
        _device(FakeBulkTransport()).send(bytes(_f5.DATA_SIZE))


# ── The merged device is routed to the surviving class ───────────────────


def test_elite_vision_360_is_a_hid_type3_panel() -> None:
    """0416:5406 lost its private wire and class; it must still resolve."""
    product = find_product(0x0416, 0x5406)
    assert product is not None
    assert product.wire is Wire.HID
    assert product.device_type == 3, "must select the F5 (type 3) variant"
    assert product.volatile_frames is True, (
        "its firmware still drops frames — the keepalive moved to the device, "
        "it was not dropped with the wire")
    assert DEVICES[product.wire] is HidLcd
