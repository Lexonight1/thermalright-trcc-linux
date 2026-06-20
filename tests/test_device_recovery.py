"""Send-path auto-recovery: stale-handle (EIO) reconnect + threshold escalation.

Covers GitHub #189 — after suspend/resume the kernel re-enumerates the USB
device, leaving the open handle stale so every write returns ``EIO``.  The
shared ``Device._send_with_recovery`` template must (1) classify ``EIO`` as a
disconnect-class error, (2) heal it with one in-place close→open→re-handshake
retry, and (3) escalate to ``DeviceDisconnectedError`` once a device is truly
gone.  Driven through the real ``Led`` adapter (the device in #189).
"""
from __future__ import annotations

import pytest

from trcc.adapters.device.led import _HID_REPORT_SIZE, _MAGIC, Led, LedPayload
from trcc.adapters.device.scsi_lcd import ScsiLcd
from trcc.core.device_recovery import DISCONNECT_FAILURE_THRESHOLD, is_disconnect_error
from trcc.core.errors import DeviceDisconnectedError
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeBulkTransport, FakeScsiTransport

_EIO = 5


def _led_info() -> ProductInfo:
    return ProductInfo(
        vid=0x0416, pid=0x8001,
        vendor="Winbond",
        product="LED Controller (FormLED)",
        wire=Wire.LED, kind=Kind.LED,
        device_type=1,
    )


def _scripted_handshake(pm: int = 1, sub: int = 0) -> bytes:
    buf = bytearray(_HID_REPORT_SIZE)
    buf[0:4] = _MAGIC
    buf[4] = sub
    buf[5] = pm
    buf[12] = 1
    return bytes(buf)


class _FlakyBulkTransport(FakeBulkTransport):
    """FakeBulkTransport that raises ``EIO`` on the next ``fail_writes`` writes.

    ``fail_writes`` is armed by the test AFTER the initial clean handshake, so
    only the send (and its reconnect) hit the failures.  Counts ``open()`` so
    tests can assert a reconnect happened.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fail_writes = 0
        self.open_calls = 0

    def open(self) -> bool:
        self.open_calls += 1
        return super().open()

    def write(self, endpoint: int, data: bytes, timeout_ms: int = 100) -> int:
        if self.fail_writes > 0:
            self.fail_writes -= 1
            raise OSError(_EIO, "Input/output error")
        return super().write(endpoint, data, timeout_ms)


def _connected_led() -> tuple[Led, _FlakyBulkTransport]:
    transport = _FlakyBulkTransport()
    transport.read_script.append(_scripted_handshake())
    led = Led(_led_info(), transport)
    led.connect()
    return led, transport


def _payload() -> LedPayload:
    return LedPayload(colors=[(200, 0, 0)] * 30, brightness=100)


def test_eio_is_classified_as_disconnect() -> None:
    """EIO must reach the disconnect-class set so the threshold can trip (#189)."""
    assert is_disconnect_error(OSError(_EIO, "I/O error")) is True
    # Control: a transient (ETIMEDOUT) is NOT disconnect-class.
    assert is_disconnect_error(OSError(110, "timed out")) is False


def test_led_send_reconnects_on_eio_then_succeeds() -> None:
    """A single EIO heals in place: one reconnect + retry, send returns True."""
    led, transport = _connected_led()
    transport.read_script.append(_scripted_handshake())  # for the reconnect's re-handshake
    transport.fail_writes = 1                            # first write of the send fails

    assert led.send(_payload()) is True
    # initial connect opened once; the reconnect opened again.
    assert transport.open_calls >= 2


def test_led_send_escalates_to_disconnect_after_threshold() -> None:
    """A truly-gone device (every write EIO) escalates at the failure threshold."""
    led, transport = _connected_led()
    transport.fail_writes = 9999  # every write fails, including reconnect attempts

    # Each send: attempt-0 fails → reconnect (also fails, swallowed) → attempt-1
    # fails → tracker +1.  Below threshold returns False; the Nth raises.
    for _ in range(DISCONNECT_FAILURE_THRESHOLD - 1):
        assert led.send(_payload()) is False
    with pytest.raises(DeviceDisconnectedError):
        led.send(_payload())


# ── Cross-wire: the same template governs SCSI (increment 2) ──────────────────


class _FlakyScsiTransport(FakeScsiTransport):
    """FakeScsiTransport that raises EIO on the next ``fail_send`` CDBs."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_send = 0
        self.open_calls = 0

    def open(self) -> bool:
        self.open_calls += 1
        return super().open()

    def send_cdb(self, cdb: bytes, data: bytes, timeout_ms: int = 5000) -> bool:
        if self.fail_send > 0:
            self.fail_send -= 1
            raise OSError(_EIO, "Input/output error")
        return super().send_cdb(cdb, data, timeout_ms)


def _poll_response(fbl: int = 100, size: int = 0xE100) -> bytes:
    resp = bytearray(size)
    resp[0] = fbl
    return bytes(resp)


def _connected_scsi() -> tuple[ScsiLcd, _FlakyScsiTransport]:
    transport = _FlakyScsiTransport()
    transport.read_script.append(_poll_response())
    info = ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="Frozen Warframe LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )
    scsi = ScsiLcd(info, transport)
    scsi.connect()
    return scsi, transport


def test_scsi_soft_failure_returns_false_without_reconnect() -> None:
    """A declined CDB (send_cdb→False) is a soft failure: no reconnect, no escalation."""
    scsi, transport = _connected_scsi()
    opens_after_connect = transport.open_calls
    transport.send_should_succeed = False  # send_cdb returns False (no raise)

    assert scsi.send(b"\x00" * 100) is False
    assert transport.open_calls == opens_after_connect      # no reconnect
    assert scsi._recovery.consecutive_failures == 0          # tracker untouched


def test_scsi_send_reconnects_on_eio_then_succeeds() -> None:
    """The shared template heals a stale SCSI handle too (cross-wire parity)."""
    scsi, transport = _connected_scsi()
    transport.read_script.append(_poll_response())  # for the reconnect's re-handshake
    transport.fail_send = 1                          # first CDB of the send raises EIO

    assert scsi.send(b"\x00" * 100) is True
    assert transport.open_calls >= 2
