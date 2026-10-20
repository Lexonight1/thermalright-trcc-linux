"""What the TRCC Windows service does for a non-elevated App -- and nothing else.

Two things on Windows need administrator rights: SCSI pass-through to a
``\\\\.\\PhysicalDriveN`` (measured on the VM: ERROR_ACCESS_DENIED as Medium)
and the PawnIO driver (its device admits SYSTEM and Administrators).  The
service (``win_service.py``) runs as SYSTEM and does exactly those two jobs
for any signed-in user's TRCC, so neither ``trcc.exe`` nor ``trcc-gui.exe``
has to ask for administrator.

A SYSTEM service is a privilege boundary, so every request is checked
against what TRCC itself does -- measured from the code, 2026-10-16:

* **SCSI** -- the service finds the drive itself, from a USB id that is a
  TRCC SCSI panel in ``core.registry``; a client never names a drive.  Only
  16-byte CDBs whose opcode is ``0xF5``, the vendor command every TRCC SCSI
  write uses (poll ``0xF5``, init ``0x1F5``, frames ``0x101F5``, boot
  animation ``0x201F5``/``0x301F5``).  A disk's own READ/WRITE are refused.
* **PawnIO** -- only the two bundled modules, loaded from the service's own
  install folder, and only the register reads ``_pawnio_cpu`` makes.

The wire is one length-prefixed message each way: ``<u32 header length>
<u32 body length> <JSON header> <body bytes>`` -- frames are ~200 KB and
JSON would base64 them.  Pure Python over socket-shaped streams, so all of
it is tested on Linux; the service and the pipe are thin around it.
"""
from __future__ import annotations

import json
import logging
import re
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...core.device_recovery import is_disconnect_error
from ...core.errors import TransportError
from ...core.logs import per_frame
from ...core.models import Wire
from ...core.registry import ALL_DEVICES
from ..infra.named_pipe import CLIENT_ACCESS
from ..sensors._cpu_registers import (
    AMD_PKG_ENERGY_STAT,
    AMD_RAPL_PWR_UNIT,
    AMD_THM_TCON_CUR_TMP,
    INTEL_PACKAGE_THERM_STATUS,
    INTEL_PKG_ENERGY_STATUS,
    INTEL_RAPL_POWER_UNIT,
    INTEL_TEMPERATURE_TARGET,
)

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The service's pipe.  Owner SYSTEM; interactive users may read and write
#: it but not create instances of it; Medium label so non-elevated
#: processes are admitted.  The rights mask is ``named_pipe.CLIENT_ACCESS``.
SERVICE_PIPE = r"\\.\pipe\trcc-service"
SERVICE_SDDL = f"O:SYD:P(A;;GA;;;SY)(A;;{CLIENT_ACCESS:#x};;;IU)S:(ML;;NW;;;ME)"
#: The Windows service's name, for ``sc.exe`` and the installer.
SERVICE_NAME = "TRCCService"

_HEADER_MAX = 64 * 1024
#: A frame is ~200 KB at 320x320 RGB565 and boot-animation chunks are
#: compressed; 8 MB is far above both and still bounds a hostile client.
_BODY_MAX = 8 * 1024 * 1024
_READ_MAX = 1024 * 1024
_CDB_LEN = 16
_TRCC_OPCODE = 0xF5
#: Which of two identical panels, as the scan names it (``usb_path``,
#: ``bus-port.port``).  Bounded: it arrives from an unprivileged client.
_UNIT = re.compile(r"\d{1,3}-\d{1,3}(?:\.\d{1,3}){0,6}")

#: The USB ids of every TRCC SCSI panel -- the only drives the service opens.
SCSI_PANELS: frozenset[tuple[int, int]] = frozenset(
    key for key, info in ALL_DEVICES.items() if info.wire is Wire.SCSI)
#: The PawnIO modules TRCC ships, by name (``<name>.bin`` in ``pawnio/``).
PAWNIO_MODULES = frozenset({"IntelMSR", "AMDFamily17"})
#: Every (function, register) ``_pawnio_cpu`` reads -- reads only.
PAWNIO_READS: frozenset[tuple[str, int]] = frozenset({
    ("ioctl_read_msr", INTEL_TEMPERATURE_TARGET),
    ("ioctl_read_msr", INTEL_PACKAGE_THERM_STATUS),
    ("ioctl_read_msr", INTEL_RAPL_POWER_UNIT),
    ("ioctl_read_msr", INTEL_PKG_ENERGY_STATUS),
    ("ioctl_read_msr", AMD_RAPL_PWR_UNIT),
    ("ioctl_read_msr", AMD_PKG_ENERGY_STAT),
    ("ioctl_read_smn", AMD_THM_TCON_CUR_TMP),
})


class Refused(Exception):
    """A request outside what TRCC does; the client is told why."""


# ── The wire ─────────────────────────────────────────────────────────

def _recv_exactly(stream: Any, size: int) -> bytes:
    """*size* bytes, or ``ConnectionError`` when the peer leaves first."""
    frame_log.debug("_recv_exactly: %d", size)
    chunks: list[bytes] = []
    left = size
    while left:
        chunk = stream.recv(min(left, 65536))
        if not chunk:
            raise ConnectionError("peer closed mid-message")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def send_message(stream: Any, header: dict[str, Any], body: bytes = b"") -> None:
    """One message: lengths, JSON header, raw body."""
    frame_log.debug("send_message: %s +%d bytes", header.get("op", header), len(body))
    encoded = json.dumps(header).encode()
    stream.sendall(struct.pack("<II", len(encoded), len(body)) + encoded + body)


def recv_message(stream: Any) -> tuple[dict[str, Any], bytes]:
    """The next message; ``ConnectionError`` at the end of the stream and
    ``Refused`` for one too large to be TRCC's."""
    frame_log.debug("recv_message")
    first = stream.recv(8)
    if not first:
        raise ConnectionError("peer closed")
    head = first + (_recv_exactly(stream, 8 - len(first)) if len(first) < 8 else b"")
    header_len, body_len = struct.unpack("<II", head)
    if header_len > _HEADER_MAX or body_len > _BODY_MAX:
        raise Refused(f"message too large ({header_len}+{body_len} bytes)")
    raw = _recv_exactly(stream, header_len)
    try:
        header = json.loads(raw.decode())
    except (ValueError, RecursionError) as e:
        # Not UTF-8, not JSON, or nested past the parser: a garbage header is
        # a refusal like any other -- it used to escape serve_connection and
        # end the thread with a traceback no log received.
        raise Refused(f"the header is not JSON ({type(e).__name__})") from e
    if not isinstance(header, dict):
        raise Refused("the header is not a JSON object")
    return header, _recv_exactly(stream, body_len) if body_len else b""


# ── The service side ─────────────────────────────────────────────────

@dataclass
class Session:
    """One client's open handles; all closed when it leaves."""

    scsi: dict[int, Any] = field(default_factory=dict)
    pawnio: dict[int, Any] = field(default_factory=dict)
    next_id: int = 1

    def add(self, table: dict[int, Any], thing: Any) -> int:
        """Keep *thing*; its handle number for the client."""
        handle = self.next_id
        self.next_id += 1
        table[handle] = thing
        log.debug("Session.add: handle %d", handle)
        return handle

    def close(self) -> None:
        """Close everything this client left open."""
        log.info("Session.close: %d SCSI, %d PawnIO handle(s)",
                 len(self.scsi), len(self.pawnio))
        for thing in [*self.scsi.values(), *self.pawnio.values()]:
            try:
                thing.close()
            except Exception:
                log.exception("Session.close: a handle failed to close")
        self.scsi.clear()
        self.pawnio.clear()


def check_cdb(cdb: bytes) -> None:
    """Refuse any CDB TRCC does not send: 16 bytes, opcode ``0xF5``.

    MUTATION CHECK in test_service_broker: drop the opcode test."""
    if len(cdb) != _CDB_LEN or cdb[0] != _TRCC_OPCODE:
        log.warning("check_cdb: refused %d-byte CDB opcode 0x%02x",
                    len(cdb), cdb[0] if cdb else -1)
        raise Refused("not a TRCC panel command")
    frame_log.debug("check_cdb: ok 0x%08x", int.from_bytes(cdb[:4], "little"))


def check_pawnio_read(function: str, inputs: list[int]) -> None:
    """Refuse any PawnIO call but the register reads TRCC makes."""
    if len(inputs) != 1 or (function, inputs[0]) not in PAWNIO_READS:
        log.warning("check_pawnio_read: refused %s %s", function, inputs)
        raise Refused(f"{function} {inputs} is not a register TRCC reads")
    frame_log.debug("check_pawnio_read: ok %s 0x%x", function, inputs[0])


class PrivilegedBroker:
    """Answers one request at a time for one :class:`Session`.

    *find_drive(vid, pid, unit)* names a panel's drive or None; *open_scsi(path)*
    returns an opened ``ScsiTransport``; *open_module(name)* returns a loaded
    ``PawnIoModule`` -- injected, so Linux tests run the real checks.
    """

    def __init__(self, *, version: str,
                 find_drive: Callable[[int, int, str], str | None],
                 open_scsi: Callable[[str], Any],
                 open_module: Callable[[str], Any]) -> None:
        self._version = version
        self._find_drive = find_drive
        self._open_scsi = open_scsi
        self._open_module = open_module
        log.info("PrivilegedBroker: version %s, %d SCSI panel id(s)",
                 version, len(SCSI_PANELS))

    def handle(self, session: Session, header: dict[str, Any],
               body: bytes) -> tuple[dict[str, Any], bytes]:
        """Run one request; a refusal or failure is an answer, never a raise."""
        op = str(header.get("op", ""))
        frame_log.debug("PrivilegedBroker.handle: %s", op)
        try:
            return self._run(session, op, header, body)
        except Refused as e:
            return {"ok": False, "refused": True, "error": str(e)}, b""
        except TransportError as e:
            # Not an OSError, so it used to escape and end the connection.
            # "gone" crosses the pipe, and the App raises disconnect-class.
            gone = is_disconnect_error(e)
            log.warning("PrivilegedBroker: %s failed (gone=%s): %s", op, gone, e)
            return {"ok": False, "gone": gone, "error": str(e)}, b""
        except OSError as e:
            # The Windows error travels with it: PawnIO's "not for this CPU"
            # (50) is how the App picks the Intel or the AMD module.
            code = getattr(e, "code", None) or e.errno or 0
            log.warning("PrivilegedBroker: %s failed: code %s: %s", op, code, e)
            return {"ok": False, "code": code, "error": str(e)}, b""
        except (ValueError, KeyError, TypeError, OverflowError) as e:
            # OverflowError: JSON's 1e999 is float inf, and int(inf) raises it.
            log.warning("PrivilegedBroker: %s failed: %s: %s", op,
                        type(e).__name__, e)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}, b""

    def _run(self, session: Session, op: str, header: dict[str, Any],
             body: bytes) -> tuple[dict[str, Any], bytes]:
        frame_log.debug("PrivilegedBroker._run: %s", op)
        match op:
            case "hello":
                return {"ok": True, "version": self._version}, b""
            case "scsi.open":
                return self._scsi_open(session, int(header["vid"]),
                                       int(header["pid"]),
                                       str(header.get("unit", ""))), b""
            case "scsi.send":
                transport = session.scsi[int(header["handle"])]
                check_cdb(body[:_CDB_LEN])
                sent = transport.send_cdb(body[:_CDB_LEN], body[_CDB_LEN:],
                                          int(header["timeout_ms"]))
                # A write the panel declined is an answer, not an error.
                return {"ok": True, "sent": bool(sent)}, b""
            case "scsi.read":
                transport = session.scsi[int(header["handle"])]
                check_cdb(body)
                length = int(header["length"])
                if not 0 < length <= _READ_MAX:
                    raise Refused(f"a {length}-byte read is not TRCC's")
                data = transport.read_cdb(body, length, int(header["timeout_ms"]))
                return {"ok": True}, bytes(data)
            case "scsi.close":
                transport = session.scsi.pop(int(header["handle"]))
                transport.close()
                log.info("PrivilegedBroker: closed SCSI handle %s", header["handle"])
                return {"ok": True}, b""
            case "pawnio.load":
                return self._pawnio_load(session, str(header["module"])), b""
            case "pawnio.execute":
                module = session.pawnio[int(header["handle"])]
                function = str(header["function"])
                inputs = [int(v) for v in header["inputs"]]
                check_pawnio_read(function, inputs)
                outputs = module.execute(function, inputs, 1)
                return {"ok": True, "outputs": [int(v) for v in outputs]}, b""
            case "pawnio.close":
                session.pawnio.pop(int(header["handle"])).close()
                return {"ok": True}, b""
        log.warning("PrivilegedBroker: unknown op %r", op)
        raise Refused(f"unknown request {op!r}")

    def _scsi_open(self, session: Session, vid: int, pid: int,
                   unit: str) -> dict[str, Any]:
        """Open the panel *vid*:*pid* in socket *unit* -- a TRCC SCSI panel,
        or refused."""
        if (vid, pid) not in SCSI_PANELS:
            log.warning("PrivilegedBroker: %04x:%04x is no TRCC SCSI panel -- "
                        "refused", vid, pid)
            raise Refused(f"{vid:04x}:{pid:04x} is not a TRCC SCSI panel")
        if unit and not _UNIT.fullmatch(unit):
            log.warning("PrivilegedBroker: unit %r is not a USB socket -- refused",
                        unit[:40])
            raise Refused("that is not a USB socket")
        path = self._find_drive(vid, pid, unit)
        if path is None:
            raise FileNotFoundError(
                f"no drive for {vid:04x}:{pid:04x}{f' in {unit}' if unit else ''}")
        transport = self._open_scsi(path)
        handle = session.add(session.scsi, transport)
        log.info("PrivilegedBroker: %04x:%04x -> %s as handle %d",
                 vid, pid, path, handle)
        return {"ok": True, "handle": handle}

    def _pawnio_load(self, session: Session, name: str) -> dict[str, Any]:
        """Load the bundled module *name*; another CPU's is an answer."""
        if name not in PAWNIO_MODULES:
            raise Refused(f"{name!r} is not a module TRCC ships")
        module = self._open_module(name)
        handle = session.add(session.pawnio, module)
        log.info("PrivilegedBroker: PawnIO %s as handle %d", name, handle)
        return {"ok": True, "handle": handle}


def serve_connection(broker: PrivilegedBroker, stream: Any,
                     stop: threading.Event) -> None:
    """Answer one client until it leaves or the service stops; its handles
    close with it."""
    log.info("serve_connection: a client connected")
    session = Session()
    try:
        while not stop.is_set():
            try:
                header, body = recv_message(stream)
            except ConnectionError:
                break
            except Refused as e:
                log.warning("serve_connection: %s -- dropping the client", e)
                break
            reply, data = broker.handle(session, header, body)
            send_message(stream, reply, data)
    except OSError as e:
        log.info("serve_connection: the client went: %s", e)
    finally:
        session.close()
        stream.close()
        log.info("serve_connection: done")


# ── The client side ──────────────────────────────────────────────────

class ServiceError(OSError):
    """The service answered no -- refused, or the hardware failed there.
    ``code`` is the Windows error it failed with there, 0 for a refusal."""

    def __init__(self, message: str, code: int = 0, refused: bool = False,
                 gone: bool = False) -> None:
        log.debug("ServiceError: code %d refused=%s gone=%s %s", code, refused,
                  gone, message)
        super().__init__(message)
        self.code = code
        self.refused = refused
        self.gone = gone


class ServiceClient:
    """One connection to the service; calls are serialised on it."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        log.debug("ServiceClient: connected")

    def call(self, header: dict[str, Any],
             body: bytes = b"") -> tuple[dict[str, Any], bytes]:
        """One request and its answer; ``ServiceError`` when it says no."""
        frame_log.debug("ServiceClient.call: %s", header.get("op"))
        with self._lock:
            send_message(self._stream, header, body)
            reply, data = recv_message(self._stream)
        if not reply.get("ok"):
            raise ServiceError(str(reply.get("error", "the service said no")),
                               int(reply.get("code") or 0),
                               bool(reply.get("refused")),
                               bool(reply.get("gone")))
        return reply, data

    def close(self) -> None:
        log.debug("ServiceClient.close")
        self._stream.close()
