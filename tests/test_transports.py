"""Transport ABCs + Device[T] DI contract.

Exercises the real protocol logic (ScsiLcd.connect poll+init, send
chunking) with fake transports — no USB, no ioctl.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device.scsi_lcd import ScsiLcd
from trcc.adapters.device.usb_bot_scsi import UsbBotScsiTransport
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


# =========================================================================
# HidApiTransport — the two `hid` bindings (#244 / #253)
# =========================================================================
#
# Two different PyPI packages import as ``hid`` with incompatible APIs, and
# our own packaging ships a different one per distro (cython-hidapi via
# pyproject/RPM/Arch, apmorton's via the Debian .deb).  v9.9.3 picked the
# cython class but drove apmorton's API: it never called ``.open()`` and
# died on ``.nonblocking``, crashing every quirked HID panel at connect.
# These lock each binding's call sequence so a regression is loud.


class _CythonDeviceStub:
    """Mimics ``hid.device``: no-arg ctor, ``.open()``, no ``__dict__``."""

    def __init__(self, *args, **kwargs) -> None:
        object.__setattr__(self, "calls", [f"ctor(args={len(args)},kw={sorted(kwargs)})"])

    def open(self, vid, pid, serial) -> None:
        self.calls.append(f"open({vid:#06x},{pid:#06x},{serial})")

    def set_nonblocking(self, value) -> None:
        self.calls.append(f"set_nonblocking({value})")

    def __setattr__(self, name, value) -> None:
        raise AttributeError(f"'hid.device' object has no attribute {name!r}")


class _ApmortonDeviceStub:
    """Mimics ``hid.Device``: opens in the ctor, ``nonblocking`` property."""

    def __init__(self, vid=None, pid=None, serial=None, path=None) -> None:
        object.__setattr__(
            self, "calls",
            [f"ctor(vid={vid:#06x},pid={pid:#06x},serial={serial})"],
        )

    @property
    def nonblocking(self):
        return None

    @nonblocking.setter
    def nonblocking(self, value) -> None:
        self.calls.append(f"nonblocking={value}")


def _drive(monkeypatch, binding, stub) -> list[str]:
    """Run ``binding.open`` against ``stub`` and return the calls it made."""
    monkeypatch.setattr(binding, "device_class", classmethod(lambda cls: stub))
    handle = binding.open(0x0416, 0x5302, None)
    return handle.calls


def test_cython_binding_opens_then_sets_nonblocking(monkeypatch) -> None:
    """cython-hidapi: construct bare, then .open(vid,pid,serial), then
    set_nonblocking(0).  The missing .open() was half of #244."""
    from trcc.adapters.device.transport import _CythonHidBinding

    assert _drive(monkeypatch, _CythonHidBinding, _CythonDeviceStub) == [
        "ctor(args=0,kw=[])",
        "open(0x0416,0x5302,None)",
        "set_nonblocking(0)",
    ]


def test_apmorton_binding_opens_via_ctor_and_property(monkeypatch) -> None:
    """apmorton hid: vid/pid/serial go to the ctor; nonblocking is a property."""
    from trcc.adapters.device.transport import _ApmortonHidBinding

    assert _drive(monkeypatch, _ApmortonHidBinding, _ApmortonDeviceStub) == [
        "ctor(vid=0x0416,pid=0x5302,serial=None)",
        "nonblocking=0",
    ]


def test_cython_binding_never_assigns_nonblocking_attribute(monkeypatch) -> None:
    """The exact v9.9.3 crash: assigning .nonblocking on hid.device raises.

    The stub raises on ANY attribute set, so this fails loudly if the
    apmorton API is ever driven against the cython class again.
    """
    from trcc.adapters.device.transport import _CythonHidBinding

    _drive(monkeypatch, _CythonHidBinding, _CythonDeviceStub)  # must not raise


def test_binding_detect_prefers_the_installed_class(monkeypatch) -> None:
    """detect() returns the child whose class the `hid` module actually has."""
    from trcc.adapters.device import transport as t

    monkeypatch.setattr(t, "HIDAPI_AVAILABLE", True)

    class _OnlyCython:
        device = _CythonDeviceStub

    monkeypatch.setattr(t, "hidapi", _OnlyCython)
    assert t._HidBinding.detect() is t._CythonHidBinding

    class _OnlyApmorton:
        Device = _ApmortonDeviceStub

    monkeypatch.setattr(t, "hidapi", _OnlyApmorton)
    assert t._HidBinding.detect() is t._ApmortonHidBinding

    monkeypatch.setattr(t, "hidapi", object())
    assert t._HidBinding.detect() is None


def test_open_errors_covers_apmortons_non_oserror(monkeypatch) -> None:
    """apmorton raises hid.HIDException, which is NOT an OSError — the
    binding must declare it or a raw exception escapes ConnectDevice."""
    from trcc.adapters.device import transport as t

    monkeypatch.setattr(t, "HIDAPI_AVAILABLE", True)

    class _HIDException(Exception):
        pass

    class _Mod:
        Device = _ApmortonDeviceStub
        HIDException = _HIDException

    monkeypatch.setattr(t, "hidapi", _Mod)
    errors = t._ApmortonHidBinding.open_errors()
    assert OSError in errors
    assert _HIDException in errors


def test_transport_open_wraps_absent_device_in_permission_error(monkeypatch) -> None:
    """A device that cannot be opened surfaces as PermissionError_ with the
    udev hint — never a bare OSError/HIDException from the binding."""
    from trcc.adapters.device import transport as t
    from trcc.core.errors import PermissionError_

    class _Failing(_CythonDeviceStub):
        def open(self, vid, pid, serial):
            raise OSError("open failed")

    monkeypatch.setattr(t, "HIDAPI_AVAILABLE", True)

    class _Mod:
        device = _Failing

    monkeypatch.setattr(t, "hidapi", _Mod)
    transport = t.HidApiTransport(0x0416, 0x5302)

    with pytest.raises(PermissionError_, match="trcc system setup"):
        transport.open()
