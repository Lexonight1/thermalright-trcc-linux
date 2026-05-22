"""Ly Protocol adapter — DeviceProtocol ABC for LY USB bulk LCDs.

Same shape as BulkProtocol (both inherit _BulkLikeProtocol), only
differs in the concrete Device class it wraps + display metadata.
"""
from __future__ import annotations

import logging
from typing import Any

from trcc.legacy.core.models import UsbAddress

from .bulk_protocol import _BulkLikeProtocol
from .factory import ProtocolInfo

log = logging.getLogger(__name__)


class LyProtocol(_BulkLikeProtocol):
    """LCD via LY USB bulk (0416:5408 / 0416:5409)."""

    _label = "LY"

    @staticmethod
    def _make_device(
        vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ) -> Any:
        from .ly import LyDevice
        return LyDevice(vid, pid, usb_address=usb_address)

    def get_info(self) -> ProtocolInfo:
        return self._build_usb_protocol_info(
            "ly", 5, "USB Bulk LY", "USB Bulk LY LCD",
            self._device is not None, pyusb_only=True,
        )

    @property
    def protocol_name(self) -> str:
        return "ly"

    def __repr__(self) -> str:
        return f"LyProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x})"
