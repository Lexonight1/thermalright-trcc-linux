"""HID Protocol adapter — DeviceProtocol ABC conformance for HID LCD devices.

Owns its HidDeviceType2/Type3 handler; the factory caches the protocol,
the protocol caches its device handler.  Transport lifecycle inherited
from UsbProtocol (lazy open via the Platform-injected
DeviceProtocolFactory.create_usb_transport).
"""
from __future__ import annotations

import logging

from trcc.legacy.core.models import DEVICE_TYPE_NAMES, HandshakeResult, UsbAddress

from .factory import ProtocolInfo, UsbProtocol
from .hid import HidDevice, HidDeviceType2, HidDeviceType3

log = logging.getLogger(__name__)


class HidProtocol(UsbProtocol):
    """LCD communication via HID USB bulk protocol (pyusb or hidapi).

    Type 2 and Type 3 are firmware variants of the same HID LCD protocol,
    selected by `device_type`. Prefers pyusb, falls back to hidapi.
    """

    def __init__(
        self, vid: int, pid: int, device_type: int,
        *, usb_address: UsbAddress | None = None,
    ):
        super().__init__(vid, pid, usb_address=usb_address)
        self._device_type = device_type
        self._handler: HidDevice | None = None

    def _build_handler(self) -> HidDevice | None:
        """Instantiate the Type 2 or Type 3 device handler."""
        assert self._transport is not None
        if self._device_type == 2:
            return HidDeviceType2(self._transport)
        if self._device_type == 3:
            return HidDeviceType3(self._transport)
        log.warning("Unknown HID device type: %d", self._device_type)
        return None

    def _do_handshake(self) -> HandshakeResult | None:
        """Open HID transport and perform type-specific handshake."""
        self._ensure_transport()
        self._handler = self._build_handler()
        if self._handler is None:
            return None

        if (result := self._handler.handshake()):
            log.info("HID handshake OK: PM=%s, FBL=%s, resolution=%s",
                     result.mode_byte_1, result.fbl, result.resolution)
        else:
            log.warning("HID handshake returned None")
        self._notify_state_changed("handshake_complete", True)
        return result

    @property
    def _handshake_label(self) -> str:
        return f"HID {self._vid:04X}:{self._pid:04X} type {self._device_type}"

    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        def _do_send() -> bool:
            self._ensure_transport()
            if self._handler is None:
                self._handler = self._build_handler()
                if self._handler is None:
                    return False
                self._handler.handshake()
            try:
                return self._handler.send_frame(image_data)
            except Exception:
                # ANY send failure invalidates the cached handler — re-raise the
                # original; the broad catch is intentional (cleanup-then-propagate).
                self._handler = None
                raise
        return self._guarded_send("HID", _do_send)

    def close(self) -> None:
        self._handler = None
        super().close()

    def get_info(self) -> ProtocolInfo:
        return self._build_usb_protocol_info(
            "hid", self._device_type, "HID (USB bulk)",
            DEVICE_TYPE_NAMES.get(self._device_type, f"Type {self._device_type}"),
            self._transport is not None and getattr(self._transport, 'is_open', False),
        )

    @property
    def protocol_name(self) -> str:
        return "hid"

    def __repr__(self) -> str:
        return (
            f"HidProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x}, "
            f"type={self._device_type})"
        )
