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
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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


# =========================================================================
# Protocol Info API — for GUI to query device/backend state
# =========================================================================

# Protocol type names for display
PROTOCOL_NAMES = {
    "scsi": "SCSI (sg_raw)",
    "hid": "HID (USB bulk)",
}

DEVICE_TYPE_NAMES = {
    1: "SCSI RGB565",
    2: "HID Type 2 (H)",
    3: "HID Type 3 (ALi)",
}


@dataclass
class ProtocolInfo:
    """Protocol and backend info for a device — returned to the GUI.

    Usage in GUI::

        info = get_protocol_info(device)
        label.setText(f"{info.protocol_display} via {info.active_backend}")
    """
    # Device identity
    protocol: str = "scsi"        # "scsi" or "hid"
    device_type: int = 1          # 1/2/3
    protocol_display: str = ""    # Human-readable, e.g. "SCSI (sg_raw)"
    device_type_display: str = "" # Human-readable, e.g. "HID Type 3 (ALi)"

    # Active backend for this device
    active_backend: str = ""      # "sg_raw", "pyusb", "hidapi", or "none"

    # What's installed on the system
    backends: Dict[str, bool] = field(default_factory=dict)

    # Transport state (HID only)
    transport_open: bool = False

    @property
    def is_scsi(self) -> bool:
        return self.protocol == "scsi"

    @property
    def is_hid(self) -> bool:
        return self.protocol == "hid"

    @property
    def has_backend(self) -> bool:
        """Whether at least one usable backend is available."""
        if self.protocol == "scsi":
            return self.backends.get("sg_raw", False)
        return self.backends.get("pyusb", False) or self.backends.get("hidapi", False)


def _check_sg_raw() -> bool:
    """Check if sg_raw is available on the system."""
    import shutil
    return shutil.which("sg_raw") is not None


def get_backend_availability() -> Dict[str, bool]:
    """Check which USB/SCSI backends are installed.

    Returns dict with keys: sg_raw, pyusb, hidapi — each True/False.
    """
    hid_pyusb = False
    hid_hidapi = False
    try:
        from .hid_device import PYUSB_AVAILABLE, HIDAPI_AVAILABLE
        hid_pyusb = PYUSB_AVAILABLE
        hid_hidapi = HIDAPI_AVAILABLE
    except ImportError:
        pass

    return {
        "sg_raw": _check_sg_raw(),
        "pyusb": hid_pyusb,
        "hidapi": hid_hidapi,
    }


def get_protocol_info(device_info=None) -> ProtocolInfo:
    """Get protocol/backend info for a device (or system defaults).

    The GUI calls this to display what protocol a device uses and
    which backend library is active.

    Args:
        device_info: DeviceInfo object (or None for system-level info).

    Returns:
        ProtocolInfo with all fields populated.
    """
    backends = get_backend_availability()

    if device_info is None:
        return ProtocolInfo(
            protocol="none",
            device_type=0,
            protocol_display="No device",
            device_type_display="",
            active_backend="none",
            backends=backends,
        )

    protocol = getattr(device_info, 'protocol', 'scsi')
    device_type = getattr(device_info, 'device_type', 1)

    # Determine active backend
    if protocol == "scsi":
        active = "sg_raw" if backends["sg_raw"] else "none"
    elif protocol == "hid":
        if backends["pyusb"]:
            active = "pyusb"
        elif backends["hidapi"]:
            active = "hidapi"
        else:
            active = "none"
    else:
        active = "none"

    # Check transport state from cached sender
    transport_open = False
    key = DeviceSenderFactory._device_key(device_info)
    sender = DeviceSenderFactory._senders.get(key)
    if isinstance(sender, HidSender) and sender._transport is not None:
        transport_open = getattr(sender._transport, 'is_open', False)

    return ProtocolInfo(
        protocol=protocol,
        device_type=device_type,
        protocol_display=PROTOCOL_NAMES.get(protocol, protocol),
        device_type_display=DEVICE_TYPE_NAMES.get(device_type, f"Type {device_type}"),
        active_backend=active,
        backends=backends,
        transport_open=transport_open,
    )
