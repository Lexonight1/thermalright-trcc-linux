"""The Windows service's allow-lists, wire and session -- run on Linux.

The two invariants are checked against what TRCC DOES, not against a list
written beside the code: the real ``ScsiLcd`` and the real ``PawnioCpu`` run
against recording fakes, and every command they produce must pass.  A new
CDB or register that the service would refuse fails here, not on a user's
Windows box with the panel dark.
"""
from __future__ import annotations

import socket
import struct
import threading
from typing import Any

import pytest

from trcc.adapters.device.scsi_lcd import ScsiLcd
from trcc.adapters.sensors._pawnio import IOCTL_EXECUTE, PawnIoModule
from trcc.adapters.sensors._pawnio_cpu import PawnioCpu
from trcc.adapters.system._service_broker import (
    PAWNIO_READS,
    SCSI_PANELS,
    PrivilegedBroker,
    Refused,
    ServiceClient,
    ServiceError,
    Session,
    check_cdb,
    check_pawnio_read,
    recv_message,
    send_message,
    serve_connection,
)
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeScsiTransport
from .test_pawnio_cpu import I9_12900K, RYZEN, _FakePawnIo

# ── What TRCC sends, recorded from the real code ─────────────────────


def _panel(transport: FakeScsiTransport, monkeypatch) -> ScsiLcd:
    monkeypatch.setattr("trcc.adapters.device.scsi_lcd._POST_INIT_DELAY_S", 0.0)
    monkeypatch.setattr("trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0)
    monkeypatch.setattr("trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0)
    poll = bytearray(0xE100)
    poll[0] = 100                                     # FBL 100: 320x320
    transport.read_script.append(bytes(poll))
    info = ProductInfo(vid=0x0402, pid=0x3922, vendor="ALi Corp",
                       product="LCD", wire=Wire.SCSI, kind=Kind.LCD,
                       device_type=1, fbl=100, native_resolution=(320, 320),
                       orientations=(0, 90, 180, 270))
    device = ScsiLcd(info, transport)
    device.connect()
    return device


def test_every_cdb_trcc_sends_passes_the_service(monkeypatch) -> None:
    """Poll, init, a whole frame and a boot animation -- all of it.

    MUTATION CHECK: narrow check_cdb to the frame command only."""
    transport = FakeScsiTransport()
    device = _panel(transport, monkeypatch)
    device.send(b"\x00" * (320 * 320 * 2))
    assert device.send_boot_animation([b"\x00" * (320 * 320 * 2)] * 2, [5, 5]) == 2
    cdbs = [cdb for cdb, _ in transport.reads] + [cdb for cdb, _ in transport.sent]
    assert len({c[:4] for c in cdbs}) >= 4            # poll, init, frame, anim
    for cdb in cdbs:
        check_cdb(cdb)


@pytest.mark.parametrize("cdb", [
    bytes([0x28]) + bytes(15),                        # READ(10)
    bytes([0x2A]) + bytes(15),                        # WRITE(10)
    bytes([0x35]) + bytes(15),                        # SYNCHRONIZE CACHE
    b"\xf5" + bytes(14),                              # 15 bytes
    b"",
])
def test_a_disks_own_commands_are_refused(cdb: bytes) -> None:
    """MUTATION CHECK: drop the opcode test in check_cdb."""
    with pytest.raises(Refused):
        check_cdb(cdb)


class _RecordingPawnIo(_FakePawnIo):
    """PawnIO that remembers every (function, register) it executed."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.executed: set[tuple[str, int]] = set()

    def ioctl(self, handle: int, code: int, payload: bytes, out_size: int) -> bytes:
        if code == IOCTL_EXECUTE:
            name = payload[:32].rstrip(b"\0").decode()
            (address,) = struct.unpack_from("<Q", payload, 32)
            self.executed.add((name, address))
        return super().ioctl(handle, code, payload, out_size)


@pytest.mark.parametrize(("module", "registers"), [
    ("IntelMSR.bin", I9_12900K), ("AMDFamily17.bin", RYZEN)])
def test_every_register_trcc_reads_passes_the_service(module, registers) -> None:
    """MUTATION CHECK: drop one register from PAWNIO_READS."""
    device = _RecordingPawnIo(module, registers)
    cpu = PawnioCpu(open_module=lambda f: PawnIoModule(f.encode(), name=f,
                                                       device=device),
                    pci_bus=lambda: lambda: _null())
    cpu.temp()
    cpu.power()
    cpu.power()
    assert device.executed and device.executed <= PAWNIO_READS
    for function, register in device.executed:
        check_pawnio_read(function, [register])


class _null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.mark.parametrize(("function", "inputs"), [
    ("ioctl_write_msr", [0x1A2, 0]),                  # a write
    ("ioctl_read_msr", [0x10]),                       # TSC -- not ours
    ("ioctl_read_smn", [0x0]),
    ("ioctl_read_msr", [0x1A2, 0x1B1]),               # two inputs
])
def test_anything_else_through_pawnio_is_refused(function, inputs) -> None:
    with pytest.raises(Refused):
        check_pawnio_read(function, inputs)


# ── The broker ───────────────────────────────────────────────────────

class _Module:
    def __init__(self) -> None:
        self.closed = False

    def execute(self, function: str, inputs: list[int], outputs: int) -> tuple[int, ...]:
        return (0x882F_0000,)

    def close(self) -> None:
        self.closed = True


def _broker(transport: FakeScsiTransport, opened: list[str],
            asked: list[str] | None = None) -> PrivilegedBroker:
    def open_scsi(path: str) -> FakeScsiTransport:
        opened.append(path)
        transport.open()
        return transport

    def find_drive(vid: int, pid: int, unit: str) -> str:
        (asked if asked is not None else []).append(unit)
        return r"\\.\PhysicalDrive1"
    return PrivilegedBroker(version="9.9.9", find_drive=find_drive,
                            open_scsi=open_scsi, open_module=lambda n: _Module())


def test_a_trcc_panel_opens_and_takes_its_commands() -> None:
    transport, opened, session = FakeScsiTransport(), [], Session()
    broker = _broker(transport, opened)
    vid, pid = next(iter(SCSI_PANELS))
    reply, _ = broker.handle(session, {"op": "scsi.open", "vid": vid, "pid": pid}, b"")
    frame_cdb = struct.pack("<I", 0x101F5) + bytes(8) + struct.pack("<I", 4)
    sent, _ = broker.handle(session, {"op": "scsi.send", "handle": reply["handle"],
                                      "timeout_ms": 100}, frame_cdb + b"abcd")
    assert opened == [r"\\.\PhysicalDrive1"]
    assert sent == {"ok": True, "sent": True}
    assert transport.sent == [(frame_cdb, b"abcd")]


@pytest.mark.parametrize("ids", [(0x87AD, 0x70DB), (0x1234, 0x5678)])
def test_a_device_that_is_not_a_trcc_scsi_panel_is_never_opened(ids) -> None:
    """A bulk panel's id, or any USB disk's: the service opens neither.

    MUTATION CHECK: drop the SCSI_PANELS test in _scsi_open."""
    opened: list[str] = []
    broker = _broker(FakeScsiTransport(), opened)
    reply, _ = broker.handle(Session(), {"op": "scsi.open", "vid": ids[0],
                                         "pid": ids[1]}, b"")
    assert reply["refused"] and opened == []


@pytest.mark.parametrize(("header", "unit"), [({}, ""), ({"unit": "2-5"}, "2-5"),
                                             ({"unit": "1-3.2.4"}, "1-3.2.4")])
def test_the_socket_a_client_names_is_the_one_the_service_looks_in(header, unit) -> None:
    """Which of two identical panels (#287) crosses the pipe; an older client
    that names none still opens the only one.

    MUTATION CHECK: drop ``unit`` from the find_drive call."""
    asked: list[str] = []
    vid, pid = next(iter(SCSI_PANELS))
    reply, _ = _broker(FakeScsiTransport(), [], asked).handle(
        Session(), {"op": "scsi.open", "vid": vid, "pid": pid, **header}, b"")
    assert reply["ok"] and asked == [unit]


@pytest.mark.parametrize("unit", ["..\\PhysicalDrive0", "1-5; x", "1", "1-",
                                  "1234-5", "1-1.2.3.4.5.6.7.8", "1@4", " 1-5"])
def test_a_unit_that_is_not_a_usb_socket_is_refused_before_any_lookup(unit) -> None:
    """The unit comes from an unprivileged process: only ``bus-port.port``.

    MUTATION CHECK: drop the ``_UNIT`` test in _scsi_open."""
    asked: list[str] = []
    opened: list[str] = []
    vid, pid = next(iter(SCSI_PANELS))
    reply, _ = _broker(FakeScsiTransport(), opened, asked).handle(
        Session(), {"op": "scsi.open", "vid": vid, "pid": pid, "unit": unit}, b"")
    assert reply["refused"] and asked == [] and opened == []


def test_a_refused_cdb_never_reaches_the_drive() -> None:
    transport, session = FakeScsiTransport(), Session()
    broker = _broker(transport, [])
    vid, pid = next(iter(SCSI_PANELS))
    handle = broker.handle(session, {"op": "scsi.open", "vid": vid, "pid": pid}, b"")[0]["handle"]
    write10 = bytes([0x2A]) + bytes(15)
    reply, _ = broker.handle(session, {"op": "scsi.send", "handle": handle,
                                       "timeout_ms": 100}, write10 + b"data")
    assert reply["refused"] and transport.sent == []


def test_a_clients_handles_close_when_it_leaves() -> None:
    transport, session = FakeScsiTransport(), Session()
    broker = _broker(transport, [])
    vid, pid = next(iter(SCSI_PANELS))
    broker.handle(session, {"op": "scsi.open", "vid": vid, "pid": pid}, b"")
    module_reply, _ = broker.handle(session, {"op": "pawnio.load",
                                              "module": "IntelMSR"}, b"")
    module = session.pawnio[module_reply["handle"]]
    session.close()
    assert not transport.is_open and module.closed


def test_only_the_modules_trcc_ships_load() -> None:
    reply, _ = _broker(FakeScsiTransport(), []).handle(
        Session(), {"op": "pawnio.load", "module": "SmbusI801"}, b"")
    assert reply["refused"]


# ── The wire, end to end ─────────────────────────────────────────────

def test_a_client_and_the_service_talk_over_a_stream() -> None:
    """send/recv framing and serve_connection, over a real socket pair."""
    ours, theirs = socket.socketpair()
    stop = threading.Event()
    transport = FakeScsiTransport()
    worker = threading.Thread(target=serve_connection,
                              args=(_broker(transport, []), theirs, stop))
    worker.start()
    client = ServiceClient(ours)
    assert client.call({"op": "hello"})[0]["version"] == "9.9.9"
    vid, pid = next(iter(SCSI_PANELS))
    handle = client.call({"op": "scsi.open", "vid": vid, "pid": pid})[0]["handle"]
    transport.read_script.append(b"\x64" + bytes(99))
    poll = struct.pack("<I", 0xF5) + bytes(8) + struct.pack("<I", 100)
    _, data = client.call({"op": "scsi.read", "handle": handle, "length": 100,
                           "timeout_ms": 100}, poll)
    assert data[0] == 0x64
    with pytest.raises(ServiceError, match="not a TRCC panel command"):
        client.call({"op": "scsi.read", "handle": handle, "length": 100,
                     "timeout_ms": 100}, bytes([0x28]) + bytes(15))
    client.close()
    worker.join(timeout=2)
    assert not worker.is_alive() and not transport.is_open


def test_an_oversized_message_is_refused_before_it_is_read() -> None:
    ours, theirs = socket.socketpair()
    ours.sendall(struct.pack("<II", 10, 512 * 1024 * 1024))
    with pytest.raises(Refused):
        recv_message(theirs)
    ours.close()
    theirs.close()


def test_framing_round_trips_a_body() -> None:
    ours, theirs = socket.socketpair()
    send_message(ours, {"op": "x", "n": 1}, b"\x00\xff" * 1000)
    assert recv_message(theirs) == ({"op": "x", "n": 1}, b"\x00\xff" * 1000)
    ours.close()
    theirs.close()


def test_the_installer_registers_the_service_the_code_names() -> None:
    """One fact in two places -- the installer's sc.exe lines and
    SERVICE_NAME -- so a rename in one fails here, not on install."""
    from pathlib import Path

    from trcc.adapters.system._service_broker import SERVICE_NAME
    iss = (Path(__file__).resolve().parent.parent / "installer"
           / "trcc.iss").read_text(encoding="utf-8")
    for line in (f"create {SERVICE_NAME} binPath= ", f"config {SERVICE_NAME} ",
                 f"start {SERVICE_NAME}", f"stop {SERVICE_NAME}",
                 f"delete {SERVICE_NAME}"):
        assert line in iss, line
    assert 'trcc-gui.exe\\"" service""' in iss    # the CLI's `trcc service`
