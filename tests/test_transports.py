"""Transport ABCs + Device[T] DI contract.

Exercises the real protocol logic (ScsiLcd.connect poll+init, send
chunking) with fake transports — no USB, no ioctl.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trcc.adapters.device import transport as transport_module
from trcc.adapters.device.scsi_lcd import ScsiLcd
from trcc.adapters.device.transport import HidApiTransport, PyUsbBulkTransport
from trcc.adapters.device.usb_bot_scsi import UsbBotScsiTransport
from trcc.adapters.system import linux
from trcc.core.models import Kind, ProductInfo, Wire


def _scsi_product() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="Test", product="Test SCSI LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )


def test_scsi_lcd_connect_issues_poll_then_init(fake_scsi) -> None:
    """connect() must: open transport → read_cdb(poll) → send_cdb(init)."""
    # Poll read returns an FBL byte + non-boot-signature bytes
    fake_scsi.read_script = [bytes([100, 0, 0, 0, 0, 0, 0, 0]) + b"\x00" * 100]
    dev = ScsiLcd(_scsi_product(), fake_scsi)

    handshake = dev.connect()

    assert fake_scsi.is_open is True
    assert handshake.model_id == 100
    assert handshake.resolution == (320, 320)
    # First CDB must be the poll command (0xF5)
    assert len(fake_scsi.sent) == 1, "init CDB was sent"
    poll_and_init_cdb_first_byte = fake_scsi.sent[0][0][0]
    assert poll_and_init_cdb_first_byte == 0xF5, (
        f"expected 0x1F5 init CDB after poll, got CDB[0]={poll_and_init_cdb_first_byte:#x}"
    )


def test_scsi_lcd_send_chunks_full_frame(fake_scsi) -> None:
    """A 320×320 RGB565 frame splits into 0xE100 chunks."""
    fake_scsi.read_script = [bytes([100]) + b"\x00" * 200]
    dev = ScsiLcd(_scsi_product(), fake_scsi)
    dev.connect()
    fake_scsi.sent.clear()

    payload = b"\x00" * (320 * 320 * 2)   # 204_800 bytes
    assert dev.send(payload) is True

    total_bytes = sum(len(data) for _, data in fake_scsi.sent)
    assert total_bytes == len(payload), "full payload sent"
    # Each chunk is 0xE100 or a remainder
    chunk_sizes = {len(data) for _, data in fake_scsi.sent}
    assert 0xE100 in chunk_sizes or 0x10000 in chunk_sizes


def test_scsi_lcd_send_raises_when_not_connected(fake_scsi) -> None:
    """send() without connect() must raise TransportError, not crash silently."""
    import pytest

    from trcc.core.errors import TransportError

    dev = ScsiLcd(_scsi_product(), fake_scsi)
    with pytest.raises(TransportError):
        dev.send(b"\x00" * 100)


def test_scsi_lcd_disconnect_closes_transport(fake_scsi) -> None:
    fake_scsi.read_script = [bytes([100]) + b"\x00" * 200]
    dev = ScsiLcd(_scsi_product(), fake_scsi)
    dev.connect()
    assert fake_scsi.is_open is True

    dev.disconnect()

    assert fake_scsi.is_open is False


def test_usb_bot_scsi_wraps_bulk_with_cbw_csw(fake_bulk) -> None:
    """UsbBotScsiTransport.send_cdb frames CBW + data + CSW via BulkTransport."""
    # Script a valid CSW (status=0) for the single op
    fake_bulk.read_script = [b"USBS" + b"\x00" * 8 + b"\x00"]   # CSW with status=0
    transport = UsbBotScsiTransport(fake_bulk)

    assert transport.open() is True
    ok = transport.send_cdb(b"\xF5" + b"\x00" * 15, b"payload", timeout_ms=100)

    assert ok is True
    # 2 writes expected: CBW (31 bytes) + data
    assert len(fake_bulk.writes) == 2
    cbw_endpoint, cbw = fake_bulk.writes[0]
    assert len(cbw) == 31, "CBW is 31 bytes"
    assert cbw[:4] == b"USBC", "CBW signature"
    _, data = fake_bulk.writes[1]
    assert data == b"payload"


def test_usb_bot_scsi_fails_on_non_zero_csw(fake_bulk) -> None:
    """CSW status != 0 must make send_cdb return False."""
    fake_bulk.read_script = [b"USBS" + b"\x00" * 8 + b"\x01"]   # status=1
    transport = UsbBotScsiTransport(fake_bulk)
    transport.open()

    ok = transport.send_cdb(b"\xF5" + b"\x00" * 15, b"x")

    assert ok is False


def test_hidapi_transport_uses_classic_open_and_normalizes_report_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hid.device() opens separately and counts the report ID byte."""
    instances = []

    class FakeHidDevice:
        def __init__(self) -> None:
            self.opened = None
            self.nonblocking = None
            instances.append(self)

        def open(self, vid: int, pid: int) -> None:
            self.opened = (vid, pid)

        def set_nonblocking(self, value: int) -> None:
            self.nonblocking = value

        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            pass

    monkeypatch.setattr(transport_module, "HIDAPI_AVAILABLE", True)
    monkeypatch.setattr(
        transport_module,
        "hidapi",
        SimpleNamespace(device=FakeHidDevice),
        raising=False,
    )
    transport = HidApiTransport(0x0416, 0x5302, device_release=0x0407)

    assert transport.open() is True
    assert instances[0].opened == (0x0416, 0x5302)
    assert instances[0].nonblocking == 0
    assert transport.write(0x02, b"x" * 512) == 512
    assert transport.uses_hid_reports is True
    assert transport.device_release == 0x0407


def test_linux_selects_hidapi_only_for_warframe_se_release_407(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Other 0416:5302 firmware revisions stay on the existing libusb path."""
    sentinel = object()
    monkeypatch.setattr(
        linux,
        "usb_find",
        lambda **_: SimpleNamespace(bcdDevice=0x0407),
    )
    monkeypatch.setattr(linux, "HidApiTransport", lambda *_, **__: sentinel)

    assert linux.LinuxPlatform().open_bulk(0x0416, 0x5302) is sentinel

    monkeypatch.setattr(
        linux,
        "usb_find",
        lambda **_: SimpleNamespace(bcdDevice=0x0406),
    )
    assert isinstance(
        linux.LinuxPlatform().open_bulk(0x0416, 0x5302),
        PyUsbBulkTransport,
    )
