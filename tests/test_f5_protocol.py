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

import threading
import time
from typing import Any

import pytest
import usb.core
import usb.util

from trcc.adapters.device import DEVICES, _f5
from trcc.adapters.device.hid_lcd import HidLcd
from trcc.adapters.device.transport import PyUsbBulkTransport
from trcc.core.errors import HandshakeError, TransportError
from trcc.core.models import Kind, ProductInfo, Wire, quirks_for
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
    assert _f5.VALID_IDENTITY == (0x36, 0x65, 0x66) == (54, 101, 102)
    # ``ThreadSendDeviceDataALi`` gates on exactly these three
    # (``DCReadWriteAsync.cs:759``) and sizes its frame buffer from the first of
    # them (``:776``).  This row asserted ``(0x65, 0x66)`` until 2026-08-29 —
    # anchored, correctly, to the decompile available at the time, which was the
    # WRONG RELEASE.  Re-anchored to the release we port.
    assert _f5.payload_size(0x36) == 320 * 240 * 2 == 153600
    assert _f5.payload_size(0x65) == _f5.payload_size(0x66) == _f5.DATA_SIZE


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


def test_identity_54_connects_and_takes_a_320x240_frame() -> None:
    """The identity we used to reject, end to end.

    ``ThreadSendDeviceDataALi`` accepts 54 alongside 101/102 and then sizes its
    buffer at 153600 rather than 204800.  Both halves matter: accepting the
    panel but still sending it 204800 bytes would be a worse bug than refusing
    it, so this asserts the FRAME, not just the handshake.
    """
    transport = FakeBulkTransport()
    transport.read_script = [_reply(54)]
    dev = _device(transport)

    result = dev.connect()

    assert result.fbl == 53
    assert result.resolution == (320, 240)

    dev.send(bytes(320 * 240 * 2))
    _, data = transport.writes[-1]
    assert len(data) == 16 + 153600
    assert int.from_bytes(data[12:16], "little") == 153600


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
    assert DEVICES[product.wire] is HidLcd


# ── The C#'s ALi worker: how 0416:5406 is asked, not only what (#330) ────


class _Recording(FakeBulkTransport):
    """The fake transport, noting every port call in order."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def reconfigure(self) -> None:
        self.calls.append("reconfigure")

    def exchange(self, out_endpoint: int, data: Any, in_endpoint: int,
                 length: int, timeout_ms: int = 100) -> bytes:
        self.calls.append("exchange")
        return super().exchange(out_endpoint, data, in_endpoint, length, timeout_ms)

    def write(self, endpoint: int, data: Any, timeout_ms: int = 100) -> int:
        self.calls.append("write")
        return super().write(endpoint, data, timeout_ms)

    def read(self, endpoint: int, length: int, timeout_ms: int = 100) -> bytes:
        self.calls.append("read")
        return super().read(endpoint, length, timeout_ms)


@pytest.fixture
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handshake's settles and retry pauses, not slept through."""
    import trcc.adapters.device._base as base
    import trcc.adapters.device.hid_lcd as hid_lcd

    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    monkeypatch.setattr(hid_lcd.time, "sleep", lambda s: None)


def _elite_vision(transport: FakeBulkTransport, bcd: int = 0x0200) -> HidLcd:
    """0416:5406 as the App builds it: its quirks set from its fingerprint."""
    device = _device(transport, vid=0x0416, pid=0x5406)
    device.set_quirks(quirks_for(0x0416, 0x5406, bcd))
    return device


@pytest.mark.usefixtures("no_waits")
def test_5406_is_asked_the_way_the_alis_worker_asks() -> None:
    """SET_CONFIGURATION + claim, then the read posted with the write -- the
    only way the C# talks to 0416:5406 (DCReadWriteAsync.cs:709-751).
    MUTATION CHECK: return the base exchange in HidLcd._init_exchange."""
    transport = _Recording()
    transport.read_script = [_reply(0x65)]
    result = _elite_vision(transport).connect()
    assert (result.fbl, transport.calls) == (100, ["reconfigure", "exchange", "write", "read"])
    assert transport.writes == [(0x02, _f5.init_packet())]


@pytest.mark.usefixtures("no_waits")
@pytest.mark.parametrize("pid", [0x5303, 0x5304])
def test_the_other_type3_panels_keep_their_exchange(pid: int) -> None:
    """0418:5303/5304 speak F5 too, but the C# never drives them with the ALi
    worker: nothing about how they are asked changes."""
    transport = _Recording()
    transport.read_script = [_reply(0x65)]
    device = _device(transport, vid=0x0418, pid=pid)
    device.set_quirks(quirks_for(0x0418, pid, 0x0100))
    device.connect()
    assert transport.calls == ["write", "read"]


@pytest.mark.usefixtures("no_waits")
def test_every_attempt_reconfigures_as_the_c_reopens_for_every_attempt() -> None:
    """MUTATION CHECK: reconfigure once, before the retry loop."""
    transport = _Recording()                       # never answers
    with pytest.raises(HandshakeError):
        _elite_vision(transport).connect()
    assert transport.calls.count("reconfigure") == 3


# ── PyUsbBulkTransport: reconfigure + exchange on a stand-in pyusb device ─


class _UsbDevice:
    """A pyusb device: records the order of what it is asked."""

    def __init__(self, refuse_configuration: bool = False) -> None:
        self.order: list[str] = []
        self.read_started = threading.Event()
        self._refuse = refuse_configuration

    def set_configuration(self, value: int) -> None:
        self.order.append(f"set_configuration({value})")
        if self._refuse:
            raise usb.core.USBError("Operation not supported")

    def read(self, endpoint: int, length: int, timeout: int) -> bytearray:
        self.order.append("read")
        self.read_started.set()
        time.sleep(0.05)
        return bytearray(_reply(0x65)[:length])

    def write(self, endpoint: int, data: Any, timeout: int) -> int:
        # A firmware that answers at once: the reply is only caught when the
        # read is already posted -- so the write waits for it, briefly.
        self.read_started.wait(1.0)
        self.order.append("write")
        return len(data)


def _pyusb(device: _UsbDevice, monkeypatch: pytest.MonkeyPatch) -> PyUsbBulkTransport:
    monkeypatch.setattr(usb.util, "release_interface",
                        lambda dev, i: device.order.append(f"release({i})"))
    monkeypatch.setattr(usb.util, "claim_interface",
                        lambda dev, i: device.order.append(f"claim({i})"))
    transport = PyUsbBulkTransport(0x0416, 0x5406)
    transport._device, transport._is_open = device, True
    transport._ep_out, transport._ep_in = 0x02, 0x81
    return transport


def test_reconfigure_releases_sets_the_configuration_and_claims_again(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """libusb will not change the configuration while the interface is held.
    MUTATION CHECK: drop the release."""
    device = _UsbDevice()
    _pyusb(device, monkeypatch).reconfigure()
    assert device.order == ["release(0)", "set_configuration(1)", "claim(0)"]


def test_a_refused_set_configuration_does_not_fail_the_handshake(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """WinUSB may refuse it; the Elite Vision works there today, and must go
    on working.  MUTATION CHECK: let the USBError out of reconfigure."""
    device = _UsbDevice(refuse_configuration=True)
    _pyusb(device, monkeypatch).reconfigure()
    assert device.order == ["release(0)", "set_configuration(1)", "claim(0)"]


def test_a_failed_claim_back_fails_the_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    device = _UsbDevice()
    transport = _pyusb(device, monkeypatch)

    def refuse(dev: Any, i: int) -> None:
        raise usb.core.USBError("Resource busy")

    monkeypatch.setattr(usb.util, "claim_interface", refuse)
    with pytest.raises(TransportError, match=r"^USB claim after reconfigure failed: "):
        transport.reconfigure()


def test_the_exchange_posts_the_read_before_the_write(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTATION CHECK: write, then read (the port's default)."""
    device = _UsbDevice()
    reply = _pyusb(device, monkeypatch).exchange(0x02, _f5.init_packet(), 0x81,
                                                 _f5.RESPONSE_SIZE, 5000)
    assert (reply[0], device.order) == (0x65, ["read", "write"])
