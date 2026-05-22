"""Bulk Protocol adapter — DeviceProtocol ABC for raw USB bulk LCDs.

Defines `_BulkLikeProtocol` (shared lifecycle template for Bulk + Ly —
both delegate to a `Device` wrapper that owns PyUSB I/O + handshake)
and the concrete `BulkProtocol` for USBLCDNew devices (87AD:70DB).
"""
from __future__ import annotations

import logging
from typing import Any

from trcc.legacy.core.models import HandshakeResult, UsbAddress

from .factory import DeviceProtocol, DeviceProtocolFactory, ProtocolInfo

log = logging.getLogger(__name__)


class _BulkLikeProtocol(DeviceProtocol):
    """Shared base for BulkProtocol + LyProtocol (identical lifecycle).

    Both wrap a `Device` object (BulkDevice / LyDevice) that owns the raw
    USB interaction. Subclasses only differ in the concrete Device class
    they instantiate via `_make_device` + display metadata in `get_info`.
    """

    _label: str = ""  # "Bulk" or "LY" — set by subclass

    def __init__(
        self, vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ):
        super().__init__()
        self._vid = vid
        self._pid = pid
        self._usb_address = usb_address  # disambiguates dual same-VID/PID coolers (#128)
        self._device: Any | None = None

    @staticmethod
    def _make_device(
        vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ) -> Any:
        raise NotImplementedError

    def _ensure_device(self) -> None:
        if self._device is None:
            log.debug("%s: creating device %04X:%04X%s",
                      self._label, self._vid, self._pid,
                      f" @ {self._usb_address}" if self._usb_address else "")
            self._device = self._make_device(self._vid, self._pid, usb_address=self._usb_address)
            assert self._device is not None
            log.debug("%s: starting handshake", self._label)
            result = self._device.handshake()
            self._handshake_result = result
            if result.resolution:
                self._notify_state_changed("handshake_complete", True)
                log.info("%s handshake OK: PM=%d, resolution=%s",
                         self._label, result.model_id, result.resolution)
            else:
                log.warning("%s handshake: no resolution detected (result=%s)",
                            self._label, result)

    def _do_handshake(self) -> HandshakeResult | None:
        self._ensure_device()
        return self._handshake_result

    @property
    def _handshake_label(self) -> str:
        return f"{self._label} {self._vid:04X}:{self._pid:04X}"

    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        def _do_send() -> bool:
            self._ensure_device()
            assert self._device is not None
            return self._device.send_frame(image_data)
        return self._guarded_send(self._label, _do_send)

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except OSError as e:
                log.debug("bulk device close raised: %s", e)
            self._device = None

    @property
    def is_available(self) -> bool:
        backends = DeviceProtocolFactory._get_hid_backends()
        return backends["pyusb"]


class BulkProtocol(_BulkLikeProtocol):
    """LCD via raw USB bulk (USBLCDNew, 87AD:70DB)."""

    _label = "Bulk"

    @staticmethod
    def _make_device(
        vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ) -> Any:
        from .bulk import BulkDevice
        return BulkDevice(vid, pid, usb_address=usb_address)

    def get_info(self) -> ProtocolInfo:
        return self._build_usb_protocol_info(
            "bulk", 4, "USB Bulk (USBLCDNew)", "Raw USB Bulk LCD",
            self._device is not None, pyusb_only=True,
        )

    @property
    def protocol_name(self) -> str:
        return "bulk"

    def __repr__(self) -> str:
        return f"BulkProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x})"
