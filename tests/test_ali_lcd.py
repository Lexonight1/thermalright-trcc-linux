"""AliLcd — USBLCDNew "Ali" bulk protocol tests.

Byte-faithful to ``ThreadSendDeviceDataALi`` (USBLCDNEW.decompiled.cs:548):
- Write endpoint 0x02, read endpoint 0x81.
- 16-byte F5-handshake request + 1024 zero pad; validate ``resp[0] ∈ {101, 102}``.
- Fixed 320x320 RGB565 (204800-B buffer); 16-byte frame header + payload,
  then a 16-byte ack read.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device import DEVICES, _f5
from trcc.adapters.device.ali_lcd import AliLcd
from trcc.core.errors import HandshakeError, TransportError
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeBulkTransport

# ── Fixtures ─────────────────────────────────────────────────────────


def _ali_response(identity: int = 101, *, size: int = 1024) -> bytes:
    """Ali handshake reply — identity byte at ``resp[0]`` (101 or 102)."""
    resp = bytearray(size)
    resp[0] = identity
    return bytes(resp)


def _make_ali(transport: FakeBulkTransport) -> AliLcd:
    info = ProductInfo(
        vid=0x0416, pid=0x5406,
        vendor="Winbond", product="LCD Display",
        wire=Wire.BULK_ALI, kind=Kind.LCD,
        device_type=4, fbl=100, native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )
    return AliLcd(info, transport)


# ── Byte constants match the C# oracle exactly ───────────────────────


def test_handshake_request_bytes():
    # 16-byte command (cs:571-577) + 1024-byte zero pad.
    assert _f5.init_packet()[:16] == bytes([
        0xF5, 0x00, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00,
    ])
    assert len(_f5.init_packet()) == 16 + 1024
    assert _f5.init_packet()[16:] == bytes(1024)


def test_frame_header_bytes():
    # 16-byte frame header (cs:646-650); bytes[12:16] LE == 204800.
    expected_header = bytes([
        0xF5, 0x01, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x03, 0x00,
    ])
    assert expected_header == _f5.frame_header()
    assert int.from_bytes(_f5.frame_header()[12:16], "little") == _f5.DATA_SIZE == 204800


def test_endpoints():
    # Endpoints live on the class (BaseBulkDevice reads them for _exchange).
    assert AliLcd._EP_WRITE == 0x02   # WriteEndpointID.Ep02 (cs:569)
    assert AliLcd._EP_READ == 0x81


# ── Factory dispatch ─────────────────────────────────────────────────


def test_factory_dispatches_bulk_ali_to_ali_lcd():
    assert DEVICES[Wire.BULK_ALI] is AliLcd


# ── Handshake ────────────────────────────────────────────────────────


@pytest.mark.parametrize("identity", [101, 102])
def test_connect_accepts_valid_identity(identity):
    transport = FakeBulkTransport()
    transport.read_script = [_ali_response(identity)]
    dev = _make_ali(transport)

    result = dev.connect()

    assert result.resolution == (320, 320)
    assert result.model_id == identity - 1      # obj3[0] = array[0] - 1 (cs:614)
    assert result.pm_byte == identity
    assert dev.is_connected
    # Handshake request went out on the write endpoint.
    assert transport.writes[0][0] == AliLcd._EP_WRITE
    assert transport.writes[0][1] == _f5.init_packet()


@pytest.mark.parametrize("bad", [0, 1, 100, 103, 200])
def test_connect_rejects_invalid_identity(bad):
    transport = FakeBulkTransport()
    transport.read_script = [_ali_response(bad)]
    dev = _make_ali(transport)

    with pytest.raises(HandshakeError):
        dev.connect()
    assert not dev.is_connected


def test_connect_rejects_empty_response():
    transport = FakeBulkTransport()
    transport.read_script = [b""]
    dev = _make_ali(transport)

    with pytest.raises(HandshakeError):
        dev.connect()


# ── Profile ──────────────────────────────────────────────────────────


def test_profile_is_fixed_320_rgb565():
    dev = _make_ali(FakeBulkTransport())
    profile = dev.profile
    assert profile is not None
    assert profile.resolution == (320, 320)
    assert profile.jpeg is False
    assert profile.big_endian is True


# ── Frame send ───────────────────────────────────────────────────────


def test_send_writes_header_plus_payload_and_reads_ack():
    transport = FakeBulkTransport()
    transport.read_script = [_ali_response(101)]
    dev = _make_ali(transport)
    dev.connect()

    payload = bytes(_f5.DATA_SIZE)          # 204800-B RGB565 canvas
    assert dev.send(payload) is True

    # Last write is the frame: header + payload, on the write endpoint.
    ep, data = transport.writes[-1]
    assert ep == AliLcd._EP_WRITE
    assert data == _f5.frame_header() + payload
    assert len(data) == 16 + _f5.DATA_SIZE


def test_send_before_connect_raises():
    dev = _make_ali(FakeBulkTransport())
    with pytest.raises(TransportError):
        dev.send(bytes(_f5.DATA_SIZE))
