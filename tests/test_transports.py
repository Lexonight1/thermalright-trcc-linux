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

    # The poll is a READ (``read_cdb``) and the init a WRITE (``send_cdb``), so
    # they land in different lists.  This block used to say "First CDB must be
    # the poll command (0xF5)" while reading ``sent[0]`` — which is the INIT.
    # It passed either way, because the two CDBs differ only in the command
    # word's second byte (0xF5 vs 0x1F5), and the poll was unassertable at all
    # until ``FakeScsiTransport.reads`` existed.  Both halves are checked now;
    # the exact bytes are pinned in ``test_handshake_requests.py``.
    assert len(fake_scsi.reads) == 1, "one poll"
    assert fake_scsi.reads[0][0][:2] == b"\xf5\x00", "poll CDB is cmd 0x00F5"
    assert len(fake_scsi.sent) == 1, "init CDB was sent"
    assert fake_scsi.sent[0][0][:2] == b"\xf5\x01", "init CDB is cmd 0x01F5"


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
    """A device that cannot be opened surfaces as PermissionError_ -- stating
    WHAT happened and naming no OS.

    It used to append "missing udev rules" (or, after #267, "udev rules ARE
    installed") -- Linux advice, from a transport every OS uses, decided by
    probing a Linux path.  On Windows it therefore always said the rules were
    missing.  The advice is the Platform's now (``permission_denied_hint``),
    and its per-OS tests live beside the platforms (#173).
    """
    from trcc.adapters.device import transport as t
    from trcc.core.errors import PermissionError_, TransportError

    class _Failing(_CythonDeviceStub):
        def open(self, vid, pid, serial):
            raise OSError("open failed")

    monkeypatch.setattr(t, "HIDAPI_AVAILABLE", True)
    monkeypatch.setattr(t, "_HID_OPEN_RETRY_S", 0.0)   # no real sleeping

    class _Mod:
        device = _Failing

    monkeypatch.setattr(t, "hidapi", _Mod)
    transport = t.HidApiTransport(0x0416, 0x5302)

    with pytest.raises(PermissionError_) as raised:
        transport.open()
    message = str(raised.value)
    assert "cannot open HID device 0416:5302" in message
    assert "udev" not in message and "WinUSB" not in message
    assert isinstance(raised.value, TransportError), (
        "a denied open must be caught wherever a transport failure is")


def test_a_hid_open_is_retried_before_it_is_called_a_failure() -> None:
    """A panel that re-enumerates must not fail the whole bootstrap.

    Some 0416:5302 firmwares REBOOT on the init packet, so the device is
    genuinely off the bus for a moment.  One attempt turned that into a hard
    failure, and two reporters on that panel independently found that
    launching the app a SECOND time worked (#267) — which is the bug
    describing its own fix.
    """
    from trcc.adapters.device import transport as t

    calls: list[int] = []

    class _FlakyOnce(_CythonDeviceStub):
        def open(self, vid, pid, serial):
            calls.append(1)
            if len(calls) < 2:                 # absent on the first look
                raise OSError("open failed")

    monkeypatch_ = pytest.MonkeyPatch()
    monkeypatch_.setattr(t, "HIDAPI_AVAILABLE", True)
    monkeypatch_.setattr(t, "_HID_OPEN_RETRY_S", 0.0)

    class _Mod:
        device = _FlakyOnce

    monkeypatch_.setattr(t, "hidapi", _Mod)
    try:
        assert t.HidApiTransport(0x0416, 0x5302).open() is True
        assert len(calls) == 2, "the open was not retried"
    finally:
        monkeypatch_.undo()


# ── Endpoint discovery decides what reaches the wire ────────────────────────
#
# ``PyUsbBulkTransport.write``/``read`` prefer whatever ``_detect_endpoints``
# finds over the endpoint the device class passes, so on real hardware a
# class's ``_EP_WRITE`` is a FALLBACK, not what is used.  That matters twice:
# it is why the C# hardcoding EP09 for the LY wire where we pass 0x01 is not a
# divergence, and it means this function -- not the wire adapters -- picks the
# endpoint for every non-SCSI device we ship.
#
# It had NO test.  Measured 2026-09-10: the only mention of
# ``PyUsbBulkTransport`` anywhere in tests/ was a docstring.


class _FakeEndpoint:
    def __init__(self, address: int) -> None:
        self.bEndpointAddress = address


class _FakeInterface:
    def __init__(self, addresses: list[int]) -> None:
        self._eps = [_FakeEndpoint(a) for a in addresses]

    def __iter__(self):
        return iter(self._eps)


class _FakeConfiguration:
    def __init__(self, addresses: list[int]) -> None:
        self._intf = _FakeInterface(addresses)

    def __getitem__(self, key):
        return self._intf


class _FakeUsbDevice:
    """The two calls ``_detect_endpoints`` makes on a pyusb device."""

    def __init__(self, addresses: list[int]) -> None:
        self._cfg = _FakeConfiguration(addresses)

    def get_active_configuration(self):
        return self._cfg


def _transport_with(addresses: list[int]):
    from trcc.adapters.device.transport import PyUsbBulkTransport

    transport = PyUsbBulkTransport(0x0416, 0x5302)
    transport._device = _FakeUsbDevice(addresses)   # pyright: ignore[reportAttributeAccessIssue]
    transport._detect_endpoints()
    return transport


def test_endpoint_detection_picks_the_one_out_and_one_in() -> None:
    """The ordinary panel: exactly one pair, taken as-is (OUT low bit clear)."""
    transport = _transport_with([0x01, 0x81])
    assert transport.ep_out == 0x01
    assert transport.ep_in == 0x81


def test_endpoint_detection_is_not_positional() -> None:
    """Direction comes from the address's high bit, never from descriptor order."""
    transport = _transport_with([0x81, 0x02])
    assert transport.ep_out == 0x02
    assert transport.ep_in == 0x81


def test_a_second_out_endpoint_is_a_warning_not_a_silent_guess(caplog) -> None:
    """Several OUT endpoints means "first" is a GUESS — say so out loud.

    We own no such device, so this is deliberately not a policy change: the
    first is still chosen, exactly as before.  What it must not be is silent.
    The class constant that knows which endpoint this protocol speaks
    (``_EP_WRITE``) is discarded here, so if such a panel ever misbehaves the
    reporter's log has to name the choice that was made — otherwise the symptom
    is a device that does nothing for no visible reason.
    """
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="trcc.adapters.device.transport"):
        transport = _transport_with([0x01, 0x03, 0x81])

    assert transport.ep_out == 0x01, "still the first — behaviour unchanged"
    warning = "\n".join(r.message for r in caplog.records
                        if r.levelno >= _logging.WARNING)
    assert "0x01" in warning and "0x03" in warning, (
        "the warning must name every candidate, not just the winner")


def test_endpoint_detection_survives_a_device_that_cannot_answer() -> None:
    """A probe failure must not take the transport down — it degrades to the
    caller-supplied endpoint, which is what the fallback in write/read is for."""
    from trcc.adapters.device.transport import PyUsbBulkTransport

    transport = PyUsbBulkTransport(0x0416, 0x5302)
    transport._device = object()   # pyright: ignore[reportAttributeAccessIssue]
    transport._detect_endpoints()

    assert transport.ep_out is None and transport.ep_in is None
