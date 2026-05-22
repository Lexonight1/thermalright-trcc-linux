"""SCSI Protocol adapter — DeviceProtocol ABC conformance for ScsiDevice.

The Protocol is the hexagonal adapter layer: it implements the
DeviceProtocol contract (handshake, send_data, close, observer callbacks)
and delegates raw SCSI I/O to `scsi.py::ScsiDevice`. Transport is
DI'd via the Platform-injected `DeviceProtocolFactory._scsi_transport_fn`.
"""
from __future__ import annotations

import logging
from typing import Any

from trcc.legacy.core.models import HandshakeResult, UsbAddress

from .factory import DeviceProtocolFactory, LazyTransportProtocol, ProtocolInfo

log = logging.getLogger(__name__)


class ScsiProtocol(LazyTransportProtocol):
    """LCD communication via SCSI protocol — transport-agnostic.

    Lazy-opens a SCSI transport via the Platform-injected factory.
    Delegates framing, handshake, and frame chunking to ``scsi.py::ScsiDevice``.

    ``usb_address`` is accepted for signature uniformity with the other
    protocol classes and forwarded to the platform's SCSI transport factory.
    Linux kernel SCSI passthrough ignores it (``/dev/sgN`` is the binding);
    macOS / BSD USB BOT use it to disambiguate dual same-VID/PID coolers (#128).
    """

    def __init__(
        self, path: str, vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ):
        super().__init__()
        self._path = path
        self._vid = vid
        self._pid = pid
        self._usb_address = usb_address

    def _open_transport(self) -> Any:
        fn = DeviceProtocolFactory._scsi_transport_fn
        if fn is None:
            # Pre-boot ScsiProtocol instance (created before the
            # composition root finished wiring) — graceful no-op; the
            # caller bails on transport=None and a later call after
            # boot completes succeeds.  Debug-level: not a defect.
            log.debug("SCSI transport factory not yet wired — skipping open")
            return None
        log.debug("Opening SCSI transport: %s%s",
                  self._path,
                  f" @ {self._usb_address}" if self._usb_address else "")
        transport = fn(self._path, self._vid, self._pid,
                       usb_address=self._usb_address)
        transport.open()
        return transport

    def _do_handshake(self) -> HandshakeResult | None:
        from .scsi import ScsiDevice
        self._ensure_transport()
        if self._transport is None:
            return None
        dev = ScsiDevice(self._path, self._transport)
        return dev.handshake()

    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        from .scsi import ScsiDevice
        self._ensure_transport()
        if self._transport is None:
            return False
        return self._guarded_send(
            "SCSI",
            lambda: ScsiDevice.send_frame_via_transport(
                self._transport, image_data, width, height),
        )

    def get_info(self) -> ProtocolInfo:
        backend = type(self._transport).__name__ if self._transport else "none"
        return ProtocolInfo(
            protocol="scsi",
            device_type=1,
            protocol_display=f"SCSI ({backend})",
            device_type_display="SCSI RGB565",
            active_backend=backend,
            backends={backend: True},
        )

    @property
    def protocol_name(self) -> str:
        return "scsi"

    @property
    def is_available(self) -> bool:
        return self._transport is not None

    def __repr__(self) -> str:
        return f"ScsiProtocol(transport={type(self._transport).__name__})"
