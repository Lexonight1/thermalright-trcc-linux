"""
Device Sender Factory — routes image sends to SCSI or HID protocol.

Factory pattern: given a DeviceInfo, creates the right sender implementation.
Senders are cached per device so transports stay open across frames.

Usage::

    from trcc.device_factory import DeviceSenderFactory

    sender = DeviceSenderFactory.get_sender(device_info)
    sender.send(rgb565_data, width, height)
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional


# =========================================================================
# Abstract sender
# =========================================================================

class DeviceSender(ABC):
    """Abstract base for protocol-specific image senders."""

    @abstractmethod
    def send(self, image_data: bytes, width: int, height: int) -> bool:
        """Send image data to the LCD device.

        Args:
            image_data: RGB565 pixel bytes (for SCSI) or JPEG (for HID).
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            True if the send succeeded.
        """

    @abstractmethod
    def close(self) -> None:
        """Release resources (USB transport, etc.)."""

    @property
    @abstractmethod
    def protocol(self) -> str:
        """Protocol identifier ('scsi' or 'hid')."""


# =========================================================================
# SCSI sender
# =========================================================================

class ScsiSender(DeviceSender):
    """Sends frames via SCSI protocol (sg_raw)."""

    def __init__(self, device_path: str):
        self._path = device_path

    def send(self, image_data: bytes, width: int, height: int) -> bool:
        from .scsi_device import send_image_to_device
        return send_image_to_device(self._path, image_data, width, height)

    def close(self) -> None:
        pass  # SCSI uses subprocess per call, nothing to release

    @property
    def protocol(self) -> str:
        return "scsi"

    def __repr__(self) -> str:
        return f"ScsiSender(path={self._path!r})"


# =========================================================================
# HID sender
# =========================================================================

class HidSender(DeviceSender):
    """Sends frames via HID USB bulk protocol (pyusb or hidapi)."""

    def __init__(self, vid: int, pid: int, device_type: int):
        self._vid = vid
        self._pid = pid
        self._device_type = device_type
        self._transport = None

    def send(self, image_data: bytes, width: int, height: int) -> bool:
        from .hid_device import send_image_to_hid_device
        if self._transport is None:
            self._transport = self._create_transport()
            self._transport.open()
        return send_image_to_hid_device(self._transport, image_data, self._device_type)

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None

    def _create_transport(self):
        """Create the best available USB transport."""
        from .hid_device import PYUSB_AVAILABLE, HIDAPI_AVAILABLE
        if PYUSB_AVAILABLE:
            from .hid_device import PyUsbTransport
            return PyUsbTransport(self._vid, self._pid)
        elif HIDAPI_AVAILABLE:
            from .hid_device import HidApiTransport
            return HidApiTransport(self._vid, self._pid)
        else:
            raise ImportError(
                "No USB backend available. Install pyusb or hidapi:\n"
                "  pip install pyusb   (+ apt install libusb-1.0-0)\n"
                "  pip install hidapi  (+ apt install libhidapi-dev)"
            )

    @property
    def protocol(self) -> str:
        return "hid"

    def __repr__(self) -> str:
        return (
            f"HidSender(vid=0x{self._vid:04x}, pid=0x{self._pid:04x}, "
            f"type={self._device_type})"
        )


# =========================================================================
# Factory
# =========================================================================

class DeviceSenderFactory:
    """Factory that creates and caches protocol-specific senders.

    Senders are cached by a key derived from the device's identity
    (protocol + vid:pid + path) so that USB transports stay open
    across successive frame sends.

    Usage::

        sender = DeviceSenderFactory.get_sender(device_info)
        sender.send(rgb565_data, width, height)

        # When done with all devices:
        DeviceSenderFactory.close_all()
    """

    _senders: Dict[str, DeviceSender] = {}

    @classmethod
    def _device_key(cls, device_info) -> str:
        """Build a cache key from device info."""
        vid = getattr(device_info, 'vid', 0)
        pid = getattr(device_info, 'pid', 0)
        path = getattr(device_info, 'path', '')
        return f"{vid:04x}_{pid:04x}_{path}"

    @classmethod
    def create_sender(cls, device_info) -> DeviceSender:
        """Create a new sender for the given device (not cached).

        Args:
            device_info: Object with protocol, vid, pid, path, device_type
                         attributes (e.g. DeviceInfo from models.py).

        Returns:
            DeviceSender subclass instance.

        Raises:
            ValueError: If protocol is unknown.
        """
        protocol = getattr(device_info, 'protocol', 'scsi')

        if protocol == 'scsi':
            return ScsiSender(device_info.path)
        elif protocol == 'hid':
            return HidSender(
                vid=device_info.vid,
                pid=device_info.pid,
                device_type=getattr(device_info, 'device_type', 2),
            )
        else:
            raise ValueError(f"Unknown protocol: {protocol!r}")

    @classmethod
    def get_sender(cls, device_info) -> DeviceSender:
        """Get or create a cached sender for the device.

        Args:
            device_info: Object with protocol, vid, pid, path, device_type.

        Returns:
            Cached DeviceSender instance.
        """
        key = cls._device_key(device_info)
        if key not in cls._senders:
            cls._senders[key] = cls.create_sender(device_info)
        return cls._senders[key]

    @classmethod
    def remove_sender(cls, device_info) -> None:
        """Remove and close a cached sender."""
        key = cls._device_key(device_info)
        sender = cls._senders.pop(key, None)
        if sender is not None:
            sender.close()

    @classmethod
    def close_all(cls) -> None:
        """Close all cached senders and clear the cache."""
        for sender in cls._senders.values():
            try:
                sender.close()
            except Exception:
                pass
        cls._senders.clear()

    @classmethod
    def get_cached_count(cls) -> int:
        """Number of cached senders (for testing)."""
        return len(cls._senders)
