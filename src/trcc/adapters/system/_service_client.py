"""The App's side of TRCC's Windows service: the same ports, through it.

A non-elevated TRCC cannot open a SCSI panel's drive or the PawnIO driver
itself (measured on the win11 VM: ERROR_ACCESS_DENIED, PawnIO code 5).
``WindowsPlatform`` then hands out these instead of the direct ones -- the
same ``ScsiTransport`` port and the same PawnIO module shape -- and every
byte goes through the SYSTEM service (``win_service.py``), which checks it
against what TRCC does (``_service_broker.py``).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ...core.errors import TransportError
from ...core.logs import per_frame
from ..device.scsi_transport import BaseScsiTransport, CdbResult
from ..sensors._pawnio import PawnIoError
from ._service_broker import (
    SERVICE_NAME,
    SERVICE_PIPE,
    SERVICE_SDDL,
    ServiceClient,
    ServiceError,
)

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: What a user is told when the service is not there to answer.
NOT_RUNNING = (f"TRCC's Windows service ({SERVICE_NAME}) is not running -- "
               "reinstall TRCC, or run it as administrator")


def connect_service(timeout: float = 5.0) -> ServiceClient:
    """A connection to the service; ``ConnectionRefusedError`` naming the fix
    when it is not installed or not started."""
    from ..infra.named_pipe import NamedPipeChannel
    log.debug("connect_service: timeout=%s", timeout)
    try:
        stream = NamedPipeChannel.for_service(
            SERVICE_PIPE, SERVICE_SDDL).connect(timeout)
    except ConnectionRefusedError as e:
        log.warning("connect_service: %s", NOT_RUNNING)
        raise ConnectionRefusedError(NOT_RUNNING) from e
    return ServiceClient(stream)


class ServiceScsiTransport(BaseScsiTransport):
    """A SCSI panel, driven by the service: it finds and opens the drive.

    The service's answer is the raw command: ``gone`` crosses the pipe, so a
    drive that left raises disconnect-class here as it would in-process.
    Logging, the failure policy and open/close are ``BaseScsiTransport``'s.
    """

    def __init__(self, vid: int, pid: int, unit: str = "") -> None:
        self._vid, self._pid, self._unit = vid, pid, unit
        self._client: ServiceClient | None = None
        self._handle = 0
        log.info("ServiceScsiTransport: %04x:%04x unit=%s through %s", vid, pid,
                 unit or "(only)", SERVICE_NAME)
        super().__init__(f"{vid:04x}:{pid:04x} via {SERVICE_NAME}")

    def _open_handle(self) -> bool:
        """Ask the service for the panel; ``TransportError`` saying why not."""
        log.info("ServiceScsiTransport.open: %04x:%04x", self._vid, self._pid)
        try:
            client = connect_service()
        except ConnectionRefusedError as e:
            raise TransportError(str(e)) from e
        try:
            reply, _ = client.call({"op": "scsi.open", "vid": self._vid,
                                    "pid": self._pid, "unit": self._unit})
        except ServiceError as e:
            client.close()
            log.warning("ServiceScsiTransport.open: %s", e)
            raise TransportError(f"{self._vid:04x}:{self._pid:04x}: {e}") from e
        self._client, self._handle = client, int(reply["handle"])
        return True

    def _close_handle(self) -> None:
        log.info("ServiceScsiTransport.close: %04x:%04x", self._vid, self._pid)
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.call({"op": "scsi.close", "handle": self._handle})
        except OSError as e:
            log.debug("ServiceScsiTransport.close: %s", e)
        client.close()

    def _live(self) -> ServiceClient:
        frame_log.debug("ServiceScsiTransport._live")
        if self._client is None:
            raise TransportError(f"{self._vid:04x}:{self._pid:04x} is not open")
        return self._client

    def _send(self, cdb: bytes, data: bytes, timeout_ms: int) -> CdbResult:
        frame_log.debug("ServiceScsiTransport._send: %d bytes", len(data))
        try:
            reply, _ = self._live().call(
                {"op": "scsi.send", "handle": self._handle,
                 "timeout_ms": timeout_ms}, bytes(cdb) + bytes(data))
        except ServiceError as e:
            return _service_failure(e, "send")
        return CdbResult(ok=bool(reply.get("sent")),
                         detail="the service's send did not complete")

    def _read(self, cdb: bytes, length: int, timeout_ms: int) -> CdbResult:
        frame_log.debug("ServiceScsiTransport._read: %d", length)
        try:
            _, data = self._live().call(
                {"op": "scsi.read", "handle": self._handle, "length": length,
                 "timeout_ms": timeout_ms}, bytes(cdb))
        except ServiceError as e:
            return _service_failure(e, "read")
        return CdbResult(ok=True, data=data)


def _service_failure(e: ServiceError, op: str) -> CdbResult:
    """A gone drive is a result; anything else the service refused is raised,
    as it always was."""
    log.debug("_service_failure: %s gone=%s %s", op, e.gone, e)
    if e.gone:
        return CdbResult(ok=False, gone=True, detail=f"the service: {e}")
    raise TransportError(f"the service could not {op}: {e}") from e


class ServicePawnModule:
    """A PawnIO module the service loaded -- ``PawnIoModule``'s shape."""

    def __init__(self, client: ServiceClient, handle: int, name: str) -> None:
        self._client, self._handle, self._name = client, handle, name
        log.debug("ServicePawnModule: %s as %d", name, handle)

    def execute(self, function: str, inputs: Sequence[int],
                outputs: int) -> tuple[int, ...]:
        frame_log.debug("ServicePawnModule.execute: %s %s", function, inputs)
        try:
            reply, _ = self._client.call(
                {"op": "pawnio.execute", "handle": self._handle,
                 "function": function, "inputs": list(inputs)})
        except ServiceError as e:
            raise PawnIoError(e.code, str(e)) from e
        return tuple(int(v) for v in reply["outputs"][:outputs])

    def close(self) -> None:
        log.debug("ServicePawnModule.close: %s", self._name)
        try:
            self._client.call({"op": "pawnio.close", "handle": self._handle})
        except OSError as e:
            log.debug("ServicePawnModule.close: %s", e)
        self._client.close()


class ServiceSuperIo:
    """The board's Super I/O, read by the service: ``SuperIoReader.sample``'s
    shape.  One connection, made again after the service restarts."""

    def __init__(self) -> None:
        log.debug("ServiceSuperIo.__init__")
        self._client: ServiceClient | None = None

    def sample(self) -> dict[str, Any]:
        """One sample; ``OSError`` when the service cannot give one."""
        if self._client is None:
            self._client = connect_service()
        try:
            reply, _ = self._client.call({"op": "superio.sample"})
        except OSError:
            # Gone or refused: the next tick connects afresh.
            self._client.close()
            self._client = None
            raise
        frame_log.debug("ServiceSuperIo.sample: %s", reply["sample"].get("chip"))
        return dict(reply["sample"])


def open_service_module(file_name: str) -> Any:
    """``PawnioCpu``'s *open_module*, through the service: ``IntelMSR.bin``
    -> the service loads ``IntelMSR``.  Failures arrive as ``PawnIoError``
    with the driver's own code, so "not for this CPU" still means "try the
    next module"."""
    name = file_name.removesuffix(".bin")
    log.info("open_service_module: %s", name)
    try:
        client = connect_service()
    except ConnectionRefusedError as e:
        raise PawnIoError(0, NOT_RUNNING) from e
    try:
        reply, _ = client.call({"op": "pawnio.load", "module": name})
    except ServiceError as e:
        client.close()
        raise PawnIoError(e.code, str(e)) from e
    return ServicePawnModule(client, int(reply["handle"]), name)
