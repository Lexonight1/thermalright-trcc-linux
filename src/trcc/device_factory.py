"""
Device Protocol Factory — unified API for SCSI, HID LCD, and HID LED devices.

Observer pattern: DeviceProtocol ABC defines the contract. ScsiProtocol,
HidProtocol, and LedProtocol are separate implementations with identical API.
Observers register callbacks for send_complete, error, and state changes.

The factory creates the right protocol class based on device PID/implementation.

Usage::

    from trcc.device_factory import DeviceProtocolFactory

    protocol = DeviceProtocolFactory.get_protocol(device_info)
    protocol.on_send_complete = lambda ok: print(f"sent: {ok}")
    protocol.on_error = lambda msg: print(f"err: {msg}")
    protocol.send_image(rgb565_data, width, height)      # LCD devices
    protocol.send_led_data(colors, is_on, True, 100)     # LED devices
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# =========================================================================
# DeviceProtocol ABC — the contract both SCSI and HID implement
# =========================================================================

class DeviceProtocol(ABC):
    """Abstract protocol interface for LCD device communication.

    Both ScsiProtocol and HidProtocol implement this identical API.
    The app codes against DeviceProtocol, never against a specific backend.

    Observer callbacks:
        on_send_complete(success: bool) — fired after each send attempt
        on_error(message: str) — fired on any protocol error
        on_state_changed(key: str, value) — fired on state transitions
    """

    def __init__(self):
        # Observer callbacks
        self.on_send_complete: Optional[Callable[[bool], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_state_changed: Optional[Callable[[str, object], None]] = None

    @abstractmethod
    def send_image(self, image_data: bytes, width: int, height: int) -> bool:
        """Send image data to the LCD device.

        Args:
            image_data: Pixel bytes (RGB565 for SCSI, JPEG for HID).
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            True if the send succeeded.
        """

    @abstractmethod
    def close(self) -> None:
        """Release resources (USB transport, SCSI state, etc.)."""

    @abstractmethod
    def get_info(self) -> 'ProtocolInfo':
        """Get protocol/backend info for GUI display."""

    @property
    @abstractmethod
    def protocol_name(self) -> str:
        """Protocol identifier: 'scsi' or 'hid'."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether the required backend (sg_raw / pyusb / hidapi) is installed."""

    def send_led_data(
        self,
        led_colors: List[Tuple[int, int, int]],
        is_on: Optional[List[bool]] = None,
        global_on: bool = True,
        brightness: int = 100,
    ) -> bool:
        """Send LED color data to an RGB LED device.

        Default implementation returns False (not an LED device).
        Only LedProtocol overrides this.
        """
        return False

    def handshake(self) -> Optional[object]:
        """Perform device handshake (HID devices only).

        Returns handshake info or None for protocols that don't handshake.
        """
        return None

    @property
    def is_led(self) -> bool:
        """Whether this protocol is for LED control (not LCD)."""
        return False

    def _notify_send_complete(self, success: bool):
        """Notify observers of send result."""
        if self.on_send_complete:
            self.on_send_complete(success)

    def _notify_error(self, message: str):
        """Notify observers of an error."""
        if self.on_error:
            self.on_error(message)

    def _notify_state_changed(self, key: str, value: object):
        """Notify observers of a state change."""
        if self.on_state_changed:
            self.on_state_changed(key, value)


# =========================================================================
# ScsiProtocol — SCSI/sg_raw implementation
# =========================================================================

class ScsiProtocol(DeviceProtocol):
    """LCD communication via SCSI protocol (sg_raw).

    Wraps scsi_device.py. Uses subprocess per send (stateless transport).
    """

    def __init__(self, device_path: str):
        super().__init__()
        self._path = device_path

    def send_image(self, image_data: bytes, width: int, height: int) -> bool:
        try:
            from .scsi_device import send_image_to_device
            success = send_image_to_device(self._path, image_data, width, height)
            self._notify_send_complete(success)
            return success
        except Exception as e:
            self._notify_error(f"SCSI send failed ({self._path}): {e}")
            self._notify_send_complete(False)
            return False

    def close(self) -> None:
        pass  # SCSI uses subprocess per call, nothing to release

    def get_info(self) -> 'ProtocolInfo':
        import shutil
        sg_raw = shutil.which("sg_raw") is not None
        return ProtocolInfo(
            protocol="scsi",
            device_type=1,
            protocol_display="SCSI (sg_raw)",
            device_type_display="SCSI RGB565",
            active_backend="sg_raw" if sg_raw else "none",
            backends={"sg_raw": sg_raw, "pyusb": False, "hidapi": False},
        )

    @property
    def protocol_name(self) -> str:
        return "scsi"

    @property
    def is_available(self) -> bool:
        import shutil
        return shutil.which("sg_raw") is not None

    def __repr__(self) -> str:
        return f"ScsiProtocol(path={self._path!r})"


# =========================================================================
# HidProtocol — HID/USB bulk implementation
# =========================================================================

class HidProtocol(DeviceProtocol):
    """LCD communication via HID USB bulk protocol (pyusb or hidapi).

    Wraps hid_device.py. Transport opens lazily on first send.
    Prefers pyusb, falls back to hidapi.
    """

    def __init__(self, vid: int, pid: int, device_type: int):
        super().__init__()
        self._vid = vid
        self._pid = pid
        self._device_type = device_type
        self._transport = None
        self._handshake_info = None

    def handshake(self):
        """Perform HID LCD handshake and return DeviceInfo.

        Opens transport if needed.  Returns hid_device.DeviceInfo with
        PM, SUB, FBL, resolution, and raw_response for debugging.
        """
        try:
            if self._transport is None:
                self._transport = self._create_transport()
                self._transport.open()
                self._notify_state_changed("transport_open", True)

            from .hid_device import HidDeviceType2, HidDeviceType3
            if self._device_type == 2:
                handler = HidDeviceType2(self._transport)
            elif self._device_type == 3:
                handler = HidDeviceType3(self._transport)
            else:
                return None

            self._handshake_info = handler.handshake()
            self._notify_state_changed("handshake_complete", True)
            return self._handshake_info
        except Exception as e:
            self._notify_error(f"HID handshake failed: {e}")
            return None

    def send_image(self, image_data: bytes, width: int, height: int) -> bool:
        try:
            from .hid_device import send_image_to_hid_device
            if self._transport is None:
                self._transport = self._create_transport()
                self._transport.open()
                self._notify_state_changed("transport_open", True)
            success = send_image_to_hid_device(
                self._transport, image_data, self._device_type
            )
            self._notify_send_complete(success)
            return success
        except Exception as e:
            self._notify_error(f"HID send failed: {e}")
            self._notify_send_complete(False)
            return False

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._notify_state_changed("transport_open", False)

    def get_info(self) -> 'ProtocolInfo':
        backends = _get_hid_backends()
        if backends["pyusb"]:
            active = "pyusb"
        elif backends["hidapi"]:
            active = "hidapi"
        else:
            active = "none"
        backends["sg_raw"] = False
        return ProtocolInfo(
            protocol="hid",
            device_type=self._device_type,
            protocol_display="HID (USB bulk)",
            device_type_display=DEVICE_TYPE_NAMES.get(
                self._device_type, f"Type {self._device_type}"
            ),
            active_backend=active,
            backends=backends,
            transport_open=self._transport is not None
                           and getattr(self._transport, 'is_open', False),
        )

    def _create_transport(self):
        """Create the best available USB transport."""
        from .hid_device import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
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
    def protocol_name(self) -> str:
        return "hid"

    @property
    def is_available(self) -> bool:
        backends = _get_hid_backends()
        return backends["pyusb"] or backends["hidapi"]

    def __repr__(self) -> str:
        return (
            f"HidProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x}, "
            f"type={self._device_type})"
        )


# =========================================================================
# LedProtocol — HID LED RGB controller
# =========================================================================

class LedProtocol(DeviceProtocol):
    """LED device communication via HID 64-byte reports (FormLED equivalent).

    Unlike HidProtocol (LCD images), LedProtocol sends LED color arrays
    for RGB LED effects. Uses the same UsbTransport as HidProtocol.
    """

    def __init__(self, vid: int, pid: int):
        super().__init__()
        self._vid = vid
        self._pid = pid
        self._transport = None
        self._sender = None
        self._handshake_info = None

    def send_image(self, image_data: bytes, width: int, height: int) -> bool:
        """No-op — LED devices don't display images."""
        return False

    def send_led_data(
        self,
        led_colors: List[Tuple[int, int, int]],
        is_on: Optional[List[bool]] = None,
        global_on: bool = True,
        brightness: int = 100,
    ) -> bool:
        """Send LED color data to the device.

        Args:
            led_colors: List of (R, G, B) tuples, one per LED.
            is_on: Per-LED on/off state. None means all on.
            global_on: Global on/off switch.
            brightness: Global brightness 0-100.

        Returns:
            True if the send succeeded.
        """
        try:
            if self._transport is None:
                self._transport = self._create_transport()
                self._transport.open()
                self._notify_state_changed("transport_open", True)

            if self._sender is None:
                from .led_device import LedHidSender
                self._sender = LedHidSender(self._transport)

            from .led_device import LedPacketBuilder
            packet = LedPacketBuilder.build_led_packet(
                led_colors, is_on, global_on, brightness
            )
            success = self._sender.send_led_data(packet)
            self._notify_send_complete(success)
            return success
        except Exception as e:
            self._notify_error(f"LED send failed: {e}")
            self._notify_send_complete(False)
            return False

    def handshake(self):
        """Perform LED device handshake and return device info.

        The firmware only responds to the handshake once after power-on.
        Subsequent calls return the cached result.

        Returns:
            LedHandshakeInfo with pm byte and resolved device style.
        """
        # Return cached result — device firmware ignores re-handshakes
        if self._handshake_info is not None:
            return self._handshake_info

        try:
            if self._transport is None:
                self._transport = self._create_transport()
                self._transport.open()
                self._notify_state_changed("transport_open", True)

            if self._sender is None:
                from .led_device import LedHidSender
                self._sender = LedHidSender(self._transport)

            self._handshake_info = self._sender.handshake()
            self._notify_state_changed("handshake_complete", True)
            return self._handshake_info
        except Exception as e:
            self._notify_error(f"LED handshake failed: {e}")
            return None

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._sender = None
            self._notify_state_changed("transport_open", False)

    def get_info(self) -> 'ProtocolInfo':
        backends = _get_hid_backends()
        if backends["pyusb"]:
            active = "pyusb"
        elif backends["hidapi"]:
            active = "hidapi"
        else:
            active = "none"
        backends["sg_raw"] = False
        return ProtocolInfo(
            protocol="led",
            device_type=1,
            protocol_display="LED (HID 64-byte)",
            device_type_display="RGB LED Controller",
            active_backend=active,
            backends=backends,
            transport_open=self._transport is not None
                           and getattr(self._transport, 'is_open', False),
        )

    def _create_transport(self):
        """Create the best available USB transport."""
        from .hid_device import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
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
    def protocol_name(self) -> str:
        return "led"

    @property
    def is_available(self) -> bool:
        backends = _get_hid_backends()
        return backends["pyusb"] or backends["hidapi"]

    @property
    def is_led(self) -> bool:
        return True

    @property
    def handshake_info(self):
        """Cached handshake info (None if not yet handshaked)."""
        return self._handshake_info

    def __repr__(self) -> str:
        return f"LedProtocol(vid=0x{self._vid:04x}, pid=0x{self._pid:04x})"


# =========================================================================
# Backward-compatible aliases (old names still work)
# =========================================================================

# Old name → new name so existing code/tests don't break
DeviceSender = DeviceProtocol
ScsiSender = ScsiProtocol
HidSender = HidProtocol
LedSender = LedProtocol


# =========================================================================
# Factory
# =========================================================================

class DeviceProtocolFactory:
    """Factory that creates and caches protocol instances.

    Protocols are cached by device identity so USB transports stay open
    across successive frame sends. SCSI is the default/primary protocol.

    Usage::

        protocol = DeviceProtocolFactory.get_protocol(device_info)
        protocol.on_send_complete = lambda ok: update_ui(ok)
        protocol.send_image(data, w, h)

        # When done:
        DeviceProtocolFactory.close_all()
    """

    _protocols: Dict[str, DeviceProtocol] = {}

    @classmethod
    def _device_key(cls, device_info) -> str:
        """Build a cache key from device info."""
        vid = getattr(device_info, 'vid', 0)
        pid = getattr(device_info, 'pid', 0)
        path = getattr(device_info, 'path', '')
        return f"{vid:04x}_{pid:04x}_{path}"

    @classmethod
    def create_protocol(cls, device_info) -> DeviceProtocol:
        """Create a new protocol for the given device (not cached).

        Routes to ScsiProtocol or HidProtocol based on device_info.protocol.
        SCSI is the default when protocol is unset.

        Args:
            device_info: Object with protocol, vid, pid, path, device_type.

        Returns:
            DeviceProtocol subclass instance.

        Raises:
            ValueError: If protocol is unknown.
        """
        protocol = getattr(device_info, 'protocol', 'scsi')
        implementation = getattr(device_info, 'implementation', '')

        if protocol == 'scsi':
            return ScsiProtocol(device_info.path)
        elif protocol == 'hid':
            # LED devices use a different protocol than LCD HID devices
            if implementation == 'hid_led':
                return LedProtocol(
                    vid=device_info.vid,
                    pid=device_info.pid,
                )
            return HidProtocol(
                vid=device_info.vid,
                pid=device_info.pid,
                device_type=getattr(device_info, 'device_type', 2),
            )
        else:
            raise ValueError(f"Unknown protocol: {protocol!r}")

    @classmethod
    def get_protocol(cls, device_info) -> DeviceProtocol:
        """Get or create a cached protocol for the device.

        Args:
            device_info: Object with protocol, vid, pid, path, device_type.

        Returns:
            Cached DeviceProtocol instance.
        """
        key = cls._device_key(device_info)
        if key not in cls._protocols:
            cls._protocols[key] = cls.create_protocol(device_info)
        return cls._protocols[key]

    @classmethod
    def remove_protocol(cls, device_info) -> None:
        """Remove and close a cached protocol."""
        key = cls._device_key(device_info)
        proto = cls._protocols.pop(key, None)
        if proto is not None:
            proto.close()

    @classmethod
    def close_all(cls) -> None:
        """Close all cached protocols and clear the cache."""
        for proto in cls._protocols.values():
            try:
                proto.close()
            except Exception:
                pass
        cls._protocols.clear()

    @classmethod
    def get_cached_count(cls) -> int:
        """Number of cached protocols (for testing)."""
        return len(cls._protocols)


# Backward-compatible alias
DeviceSenderFactory = DeviceProtocolFactory


# =========================================================================
# Protocol Info API — for GUI to query device/backend state
# =========================================================================

PROTOCOL_NAMES = {
    "scsi": "SCSI (sg_raw)",
    "hid": "HID (USB bulk)",
    "led": "LED (HID 64-byte)",
}

DEVICE_TYPE_NAMES = {
    1: "SCSI RGB565",
    2: "HID Type 2 (H)",
    3: "HID Type 3 (ALi)",
}

LED_DEVICE_TYPE_NAME = "RGB LED Controller"


@dataclass
class ProtocolInfo:
    """Protocol and backend info for a device — returned to the GUI.

    Usage in GUI::

        info = get_protocol_info(device)
        label.setText(f"{info.protocol_display} via {info.active_backend}")
    """
    protocol: str = "scsi"
    device_type: int = 1
    protocol_display: str = ""
    device_type_display: str = ""
    active_backend: str = ""
    backends: Dict[str, bool] = field(default_factory=dict)
    transport_open: bool = False

    @property
    def is_scsi(self) -> bool:
        return self.protocol == "scsi"

    @property
    def is_hid(self) -> bool:
        return self.protocol == "hid"

    @property
    def is_led(self) -> bool:
        return self.protocol == "led"

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


def _get_hid_backends() -> Dict[str, bool]:
    """Check HID backend availability."""
    try:
        from .hid_device import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
        return {"pyusb": PYUSB_AVAILABLE, "hidapi": HIDAPI_AVAILABLE}
    except ImportError:
        return {"pyusb": False, "hidapi": False}


def get_backend_availability() -> Dict[str, bool]:
    """Check which USB/SCSI backends are installed.

    Returns dict with keys: sg_raw, pyusb, hidapi — each True/False.
    """
    hid = _get_hid_backends()
    return {
        "sg_raw": _check_sg_raw(),
        "pyusb": hid["pyusb"],
        "hidapi": hid["hidapi"],
    }


def get_protocol_info(device_info=None) -> ProtocolInfo:
    """Get protocol/backend info for a device (or system defaults).

    If a cached protocol exists for this device, delegates to its get_info().
    Otherwise builds ProtocolInfo from backend availability.

    Args:
        device_info: DeviceInfo object (or None for system-level info).

    Returns:
        ProtocolInfo with all fields populated.
    """
    if device_info is None:
        backends = get_backend_availability()
        return ProtocolInfo(
            protocol="none",
            device_type=0,
            protocol_display="No device",
            device_type_display="",
            active_backend="none",
            backends=backends,
        )

    # If there's a cached protocol, ask it directly
    key = DeviceProtocolFactory._device_key(device_info)
    proto = DeviceProtocolFactory._protocols.get(key)
    if proto is not None:
        return proto.get_info()

    # No cached protocol — build info from scratch
    backends = get_backend_availability()
    protocol = getattr(device_info, 'protocol', 'scsi')
    device_type = getattr(device_info, 'device_type', 1)

    implementation = getattr(device_info, 'implementation', '')

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

    # LED devices report as "led" protocol
    if implementation == "hid_led":
        return ProtocolInfo(
            protocol="led",
            device_type=1,
            protocol_display=PROTOCOL_NAMES.get("led", "LED"),
            device_type_display=LED_DEVICE_TYPE_NAME,
            active_backend=active,
            backends=backends,
            transport_open=False,
        )

    return ProtocolInfo(
        protocol=protocol,
        device_type=device_type,
        protocol_display=PROTOCOL_NAMES.get(protocol, protocol),
        device_type_display=DEVICE_TYPE_NAMES.get(device_type, f"Type {device_type}"),
        active_backend=active,
        backends=backends,
        transport_open=False,
    )
