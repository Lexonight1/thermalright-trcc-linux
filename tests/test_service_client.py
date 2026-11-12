"""The App's side of the Windows service, end to end on Linux.

The REAL ``ScsiLcd`` and the REAL ``PawnioCpu`` run over the service's
client adapters, across the real wire, into the real broker -- only the
drive and the PawnIO driver at the far end are fakes.  So what is pinned is
what a non-elevated TRCC on Windows will actually do.
"""
from __future__ import annotations

import socket
import struct
import threading
from typing import Any

import pytest

from trcc.adapters.device.scsi_lcd import ScsiLcd
from trcc.adapters.sensors._pawnio import BUS_BUSY, PawnIoError, PawnIoModule
from trcc.adapters.sensors._pawnio_cpu import PawnioCpu
from trcc.adapters.sensors._superio import SuperIoBoard
from trcc.adapters.system import _service_client
from trcc.adapters.system._service_broker import (
    PrivilegedBroker,
    ServiceClient,
    serve_connection,
)
from trcc.adapters.system._service_client import (
    NOT_RUNNING,
    ServiceScsiTransport,
    ServiceSuperIo,
    open_service_module,
)
from trcc.core.errors import TransportError
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeScsiTransport
from .test_pawnio_cpu import I9_12900K, RYZEN, _FakePawnIo


class _Service:
    """A broker served over socket pairs: each connect is a new client."""

    def __init__(self, broker: PrivilegedBroker) -> None:
        self._broker = broker
        self._stop = threading.Event()
        self.sockets: list[socket.socket] = []
        _OPEN.append(self)

    def connect(self, timeout: float = 5.0) -> ServiceClient:
        ours, theirs = socket.socketpair()
        self.sockets.append(ours)
        threading.Thread(target=serve_connection,
                         args=(self._broker, theirs, self._stop),
                         daemon=True).start()
        return ServiceClient(ours)


_OPEN: list[_Service] = []


@pytest.fixture(autouse=True)
def _close_every_client() -> Any:
    """The App keeps these open for the panel's or the sensor's life; a test
    closes them, or Python reports the sockets as leaked."""
    yield
    while _OPEN:
        for sock in _OPEN.pop().sockets:
            sock.close()


def _scsi_service(monkeypatch, drive: FakeScsiTransport) -> _Service:
    def open_scsi(path: str) -> FakeScsiTransport:
        drive.open()
        return drive
    service = _Service(PrivilegedBroker(
        version="t", find_drive=lambda v, p, u: r"\\.\PhysicalDrive1",
        open_scsi=open_scsi, open_module=lambda n: None))
    monkeypatch.setattr(_service_client, "connect_service", service.connect)
    return service


def test_a_panel_connects_and_draws_through_the_service(monkeypatch) -> None:
    """Poll, init and a frame: the real ScsiLcd, through the service.

    MUTATION CHECK: drop the body from ServiceScsiTransport.send_cdb."""
    monkeypatch.setattr("trcc.adapters.device.scsi_lcd._POST_INIT_DELAY_S", 0.0)
    drive = FakeScsiTransport()
    poll = bytearray(0xE100)
    poll[0] = 100
    drive.read_script.append(bytes(poll))
    _scsi_service(monkeypatch, drive)
    info = ProductInfo(vid=0x0402, pid=0x3922, vendor="ALi Corp", product="LCD",
                       wire=Wire.SCSI, kind=Kind.LCD, device_type=1, fbl=100,
                       native_resolution=(320, 320), orientations=(0, 90, 180, 270))
    device = ScsiLcd(info, ServiceScsiTransport(0x0402, 0x3922))
    assert device.connect().resolution == (320, 320)
    frame = b"\xf8\x00" * (320 * 320)
    assert device.send(frame)
    written = b"".join(data for cdb, data in drive.sent
                       if struct.unpack_from("<I", cdb)[0] & 0xFFFFFF == 0x0101F5)
    assert written == frame


@pytest.mark.parametrize(("errno_val", "gone"), [(19, True), (110, False)])
def test_a_drive_gone_in_the_service_is_gone_in_the_app(
        monkeypatch, errno_val: int, gone: bool) -> None:
    """A TransportError is not an OSError, so the broker let it escape and end
    the connection; "gone" never reached the App.  Now it crosses the pipe:
    ENODEV in the service raises disconnect-class here (the recovery threshold
    counts it); anything else stays an ordinary TransportError.

    MUTATION CHECK: drop the broker's ``except TransportError`` -- the
    connection dies and the App sees a closed pipe, not a gone drive."""
    from trcc.core.device_recovery import is_disconnect_error

    drive = FakeScsiTransport()

    def fail(*_a: object) -> bool:
        raise TransportError("the write failed") from OSError(errno_val, "x")

    monkeypatch.setattr(drive, "send_cdb", fail)
    _scsi_service(monkeypatch, drive)
    transport = ServiceScsiTransport(0x0402, 0x3922)
    transport.open()
    frame_cdb = struct.pack("<I", 0x0101F5) + bytes(8) + struct.pack("<I", 0x10000)
    with pytest.raises(TransportError) as raised:
        transport.send_cdb(frame_cdb, bytes(0x10000))
    assert "not a TRCC panel command" not in str(raised.value)   # it reached the drive
    assert is_disconnect_error(raised.value) is gone
    transport.close()


def test_closing_the_transport_releases_the_drive(monkeypatch) -> None:
    drive = FakeScsiTransport()
    _scsi_service(monkeypatch, drive)
    transport = ServiceScsiTransport(0x0402, 0x3922)
    transport.open()
    assert drive.is_open
    transport.close()
    assert not transport.is_open and not drive.is_open


def test_no_service_is_a_transport_error_that_names_the_fix(monkeypatch) -> None:
    def refuse(timeout: float = 5.0) -> Any:
        raise ConnectionRefusedError(NOT_RUNNING)
    monkeypatch.setattr(_service_client, "connect_service", refuse)
    with pytest.raises(TransportError, match="reinstall TRCC"):
        ServiceScsiTransport(0x0402, 0x3922).open()


@pytest.mark.parametrize(("vendor", "registers", "label"), [
    ("IntelMSR.bin", I9_12900K, "Package"), ("AMDFamily17.bin", RYZEN, "Tctl")])
def test_the_cpu_is_read_through_the_service(monkeypatch, vendor, registers,
                                             label) -> None:
    """On an AMD CPU the Intel module refuses with code 50, and that code must
    survive the service for PawnioCpu to try AMD next.

    MUTATION CHECK: drop "code" from the broker's error reply."""
    driver = _FakePawnIo(vendor, registers)
    service = _Service(PrivilegedBroker(
        version="t", find_drive=lambda v, p, u: None, open_scsi=lambda p: None,
        open_module=lambda name: PawnIoModule(f"{name}.bin".encode(),
                                              name=name, device=driver)))
    monkeypatch.setattr(_service_client, "connect_service", service.connect)
    cpu = PawnioCpu(open_module=open_service_module,
                    pci_bus=lambda: lambda: _Null())
    assert cpu.temp() is not None and cpu.temp_label() == label


def test_no_service_tells_doctor_what_fixes_it(monkeypatch) -> None:
    def refuse(timeout: float = 5.0) -> Any:
        raise ConnectionRefusedError(NOT_RUNNING)
    monkeypatch.setattr(_service_client, "connect_service", refuse)
    cpu = PawnioCpu(open_module=open_service_module,
                    pci_bus=lambda: lambda: _Null())
    assert cpu.temp() is None and cpu.reason == NOT_RUNNING


@pytest.mark.parametrize(("elevated", "service"), [(True, False), (False, True)])
def test_the_platform_picks_direct_or_the_service(monkeypatch, elevated,
                                                  service) -> None:
    """Elevated, TRCC opens the drive and PawnIO itself; otherwise the
    service does.  One decision, in WindowsPlatform.

    MUTATION CHECK: always take the direct path."""
    from trcc.adapters.system import windows
    monkeypatch.setattr(windows, "_is_elevated", lambda: elevated)
    monkeypatch.setattr(windows, "find_physical_drive",
                        lambda v, p, u: r"\\.\PhysicalDrive1")
    transport = windows.WindowsPlatform()._open_scsi(0x0402, 0x3922)
    assert isinstance(transport, ServiceScsiTransport) is service
    cpu = windows.make_pawnio_cpu()
    assert (cpu._open is open_service_module) is service


@pytest.mark.parametrize("elevated", [True, False])
def test_the_socket_reaches_the_drive_lookup_either_way(monkeypatch,
                                                         elevated) -> None:
    """Which of two identical panels (#287): opened directly, the lookup gets
    the unit; through the service, the unit crosses the pipe to it.

    MUTATION CHECK: drop ``unit`` from either ServiceScsiTransport call."""
    from trcc.adapters.system import windows
    asked: list[str] = []

    def find(vid: int, pid: int, unit: str) -> str:
        asked.append(unit)
        return r"\\.\PhysicalDrive2"
    monkeypatch.setattr(windows, "_is_elevated", lambda: elevated)
    monkeypatch.setattr(windows, "find_physical_drive", find)
    drive = FakeScsiTransport()

    def open_scsi(path: str) -> FakeScsiTransport:
        drive.open()
        return drive
    service = _Service(PrivilegedBroker(version="t", find_drive=find,
                                        open_scsi=open_scsi,
                                        open_module=lambda n: None))
    monkeypatch.setattr(_service_client, "connect_service", service.connect)
    transport = windows.WindowsPlatform()._open_scsi(0x0402, 0x3922, unit="2-5")
    if not elevated:
        assert transport.open()
        transport.close()
    assert asked == ["2-5"]


class _Null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


class _BusyBoard:
    """The service's Super I/O reader: one good sample, then a held ISA bus."""

    SAMPLE = {"chips": [{"slot": 0, "chip": "NCT6798D", "fans": [1250],
                         "temps": {"SYSTIN": 34}}]}

    def __init__(self, opened: list[int]) -> None:
        opened.append(1)
        self.calls = 0

    def sample(self) -> dict[str, Any]:
        self.calls += 1
        if self.calls > 1:
            raise PawnIoError(BUS_BUSY, "the ISA bus is busy -- another "
                                        "monitor holds Global\\Access_ISABUS.HTP.Method")
        return self.SAMPLE

    def close(self) -> None:
        pass


def test_a_busy_isa_bus_in_the_service_keeps_the_connection_and_the_fans(
        monkeypatch) -> None:
    """#340 across the pipe.  The busy code must survive the service for the
    board to hold its last sample, and the client must not hang up on an
    answer: every reconnect opened a new reader in the service, which finds the
    chip again -- keying a Super I/O once per busy tick.

    MUTATION CHECK: drop the ``not e.gone`` branch in ServiceSuperIo.sample ->
    opened == [1, 1] over 2 sockets (measured): the busy tick hangs up, and the
    next one opens a second reader, whose first sample is good."""
    opened: list[int] = []
    service = _Service(PrivilegedBroker(
        version="t", find_drive=lambda v, p, u: None, open_scsi=lambda p: None,
        open_module=lambda name: None, open_superio=lambda: _BusyBoard(opened)))
    monkeypatch.setattr(_service_client, "connect_service", service.connect)
    now = [0.0]
    board = SuperIoBoard(ServiceSuperIo().sample, clock=lambda: now[0])
    seen = []
    for t in (0.0, 1.0, 2.0):
        now[0] = t
        seen.append(board.latest())
    assert (seen, opened, len(service.sockets)) == ([_BusyBoard.SAMPLE] * 3, [1], 1)
