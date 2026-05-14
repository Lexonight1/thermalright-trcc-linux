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
    protocol.send_data(rgb565_data, width, height)      # LCD devices
    # LED: LedProtocol.send_data(colors, is_on, True, 100)
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from trcc.core.models import (
    DEVICE_TYPE_NAMES,
    PROTOCOL_NAMES,
    HandshakeResult,
    UsbAddress,
)

if TYPE_CHECKING:
    from trcc.core.models import DeviceInfo

log = logging.getLogger(__name__)


# USB errno constants.
_ERRNO_EACCES = 13  # Permission denied — udev rules missing.
_ERRNO_EBUSY = 16   # Device claimed by another process (e.g. GUI).


def _has_usb_errno(exc: Exception, errno_val: int) -> bool:
    """Check if exception chain contains a USB error with the given errno."""
    cur: BaseException | None = exc
    while cur is not None:
        if getattr(cur, "errno", None) == errno_val:
            return True
        cur = cur.__cause__
    return False


def _permission_denied_hint() -> str:
    """Platform-aware hint for USB permission denied errors."""
    from trcc.core.platform import LINUX, MACOS
    if LINUX:
        return "run 'trcc setup-udev' to configure USB device permissions"
    if MACOS:
        return "try running with sudo, or check System Settings > Privacy & Security"
    return "ensure you have permission to access USB devices"


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
        self.on_send_complete: Callable[[bool], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_state_changed: Callable[[str, object], None] | None = None
        # Handshake state — common to all protocols
        self._handshake_result: HandshakeResult | None = None
        self._last_error: Exception | None = None

    @abstractmethod
    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
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

    def handshake(self) -> HandshakeResult | None:
        """Template Method: perform handshake, cache result, handle errors.

        Subclasses implement _do_handshake() with protocol-specific logic.
        """
        try:
            result = self._do_handshake()
            self._handshake_result = result
            if result:
                self._cache_handshake(result)
            return result
        except Exception as e:
            if _has_usb_errno(e, _ERRNO_EACCES):
                log.warning(
                    "%s permission denied — %s",
                    self._handshake_label,
                    _permission_denied_hint())
            elif _has_usb_errno(e, _ERRNO_EBUSY):
                log.warning("%s in use by another process",
                            self._handshake_label)
            else:
                log.exception("%s handshake failed", self._handshake_label)
            self._last_error = e
            self._notify_error(f"{self._handshake_label} handshake failed: {e}")
            return None

    @abstractmethod
    def _do_handshake(self) -> HandshakeResult | None:
        """Protocol-specific handshake logic. Called by handshake()."""

    @property
    def _handshake_label(self) -> str:
        """Human-readable label for error messages (e.g. 'HID 0416:5302')."""
        return self.protocol_name

    @property
    def handshake_info(self) -> HandshakeResult | None:
        """Cached handshake result (None if not yet handshaked)."""
        return self._handshake_result

    @property
    def last_error(self) -> Exception | None:
        """Last exception from handshake."""
        return self._last_error

    def _cache_handshake(self, result: HandshakeResult) -> None:
        """Save handshake result so `trcc report` can read it while GUI runs."""
        try:
            from trcc.conf import save_last_handshake
            save_last_handshake({
                "protocol": self.protocol_name,
                "model_id": result.model_id,
                "resolution": list(result.resolution) if result.resolution else None,
                "serial": result.serial,
                "raw": result.raw_response.hex() if result.raw_response else "",
            })
        except (OSError, ValueError, TypeError) as e:
            log.debug("Failed to cache handshake result: %s", e)

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

    def _guarded_send(self, label: str, fn: Callable[[], bool]) -> bool:
        """Execute a send operation with error handling and observer notification."""
        try:
            if (success := fn()):
                log.debug("Frame sent: %s", label)
            else:
                log.debug("Frame send returned False: %s", label)
            self._notify_send_complete(success)
            return success
        except Exception as e:
            log.warning("Frame send failed (%s): %s — device may be disconnected",
                        label, e)
            self._notify_error(f"{label} send failed: {e}")
            self._notify_send_complete(False)
            return False

    @staticmethod
    def _build_usb_protocol_info(
        protocol: str, device_type: int, protocol_display: str,
        device_type_display: str, transport_open: bool,
        *, pyusb_only: bool = False,
    ) -> 'ProtocolInfo':
        """Build ProtocolInfo for USB-transport protocols (HID, LED, Bulk)."""
        backends = DeviceProtocolFactory._get_hid_backends()
        if pyusb_only:
            active = "pyusb" if backends["pyusb"] else "none"
        elif backends["pyusb"]:
            active = "pyusb"
        elif backends["hidapi"]:
            active = "hidapi"
        else:
            active = "none"
        backends["sg_raw"] = False
        return ProtocolInfo(
            protocol=protocol, device_type=device_type,
            protocol_display=protocol_display,
            device_type_display=device_type_display,
            active_backend=active, backends=backends,
            transport_open=transport_open,
        )


# =========================================================================
# LazyTransportProtocol — shared lazy-open + close lifecycle
# =========================================================================

class LazyTransportProtocol(DeviceProtocol):
    """Intermediate base for protocols that lazily open a transport.

    Owns the structural pattern that's identical across SCSI / HID / LED /
    Bulk / LY: open the transport on first use, fire ``transport_open``
    observers, close on shutdown.  Subclasses provide the transport
    factory in ``_open_transport``; everything else rides on this base.

    Audit finding 2026-05-09: SCSI and USB protocols had two parallel
    copies of this lifecycle — same flow, slightly different log lines.
    """

    def __init__(self) -> None:
        super().__init__()
        self._transport = None

    @abstractmethod
    def _open_transport(self) -> Any:
        """Open and return the underlying transport.

        Called once on first ``_ensure_transport``.  Returning ``None``
        is allowed (e.g. SCSI when the transport factory wasn't injected) —
        callers should bail gracefully when ``self._transport is None``
        post-call.
        """

    def _ensure_transport(self) -> None:
        """Lazily open the transport on first use."""
        if self._transport is None:
            self._transport = self._open_transport()
            if self._transport is not None:
                self._notify_state_changed("transport_open", True)

    def _close_transport(self) -> None:
        """Close the transport and notify observers.  Idempotent."""
        if self._transport is not None:
            try:
                self._transport.close()
            except OSError as e:
                log.debug("transport close raised: %s", e)
            self._transport = None
            self._notify_state_changed("transport_open", False)

    def close(self) -> None:
        self._close_transport()


# =========================================================================
# UsbProtocol — shared USB transport lifecycle for HID + LED
# =========================================================================

class UsbProtocol(LazyTransportProtocol):
    """Base for USB-transport protocols (HID LCD, LED).

    Subclasses implement protocol-specific handshake, send, and info;
    transport lifecycle is inherited from ``LazyTransportProtocol``.
    """

    def __init__(
        self, vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ):
        super().__init__()
        self._vid = vid
        self._pid = pid
        self._usb_address = usb_address  # disambiguates dual same-VID/PID coolers (#128)

    def _open_transport(self) -> Any:
        log.debug("Opening %s transport: %04X:%04X%s",
                  self.protocol_name, self._vid, self._pid,
                  f" @ {self._usb_address}" if self._usb_address else "")
        transport = DeviceProtocolFactory.create_usb_transport(
            self._vid, self._pid, usb_address=self._usb_address)
        transport.open()
        return transport

    @property
    def _handshake_label(self) -> str:
        return f"{self.protocol_name.upper()} {self._vid:04X}:{self._pid:04X}"

    @property
    def is_available(self) -> bool:
        backends = DeviceProtocolFactory._get_hid_backends()
        return backends["pyusb"] or backends["hidapi"]



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
        protocol.send_data(data, w, h)

        # When done:
        DeviceProtocolFactory.close_all()
    """

    _protocols: dict[str, DeviceProtocol] = {}
    _scsi_transport_fn: ClassVar[Callable | None] = None

    # Registry map: protocol name → factory function.
    # Populated at module bottom after concrete Protocol classes import —
    # avoids the circular (factory ⇄ protocol files) at class-body time.
    _PROTOCOL_REGISTRY: ClassVar[dict[str, Callable[..., DeviceProtocol]]] = {}

    @classmethod
    def set_scsi_transport(cls, fn: Callable) -> None:
        """Inject the OS-specific SCSI transport factory.

        Called once at startup by ControllerBuilder with
        os_platform.create_scsi_transport.
        """
        cls._scsi_transport_fn = fn

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

        Routes to the appropriate protocol class via _PROTOCOL_REGISTRY.

        Args:
            device_info: Object with protocol, vid, pid, path, device_type.

        Returns:
            DeviceProtocol subclass instance.

        Raises:
            ValueError: If protocol is unknown.
        """
        protocol = getattr(device_info, 'protocol', 'scsi')
        log.debug("create_protocol: protocol=%s", protocol)

        factory_fn = cls._PROTOCOL_REGISTRY.get(protocol)
        if factory_fn is None:
            raise ValueError(f"Unknown protocol: {protocol!r}")

        result = factory_fn(device_info)
        path = getattr(device_info, 'path', f'{device_info.vid:04X}:{device_info.pid:04X}')
        log.info("create_protocol: %s for %s", type(result).__name__, path)
        return result

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
            log.debug("get_protocol: cache miss for %s — creating", key)
            cls._protocols[key] = cls.create_protocol(device_info)
        else:
            log.debug("get_protocol: cache hit for %s", key)
        return cls._protocols[key]

    @classmethod
    def remove_protocol(cls, device_info) -> None:
        """Remove and close a cached protocol."""
        key = cls._device_key(device_info)
        if (proto := cls._protocols.pop(key, None)) is not None:
            proto.close()

    @classmethod
    def close_all(cls) -> None:
        """Close all cached protocols and clear the cache."""
        for proto in cls._protocols.values():
            try:
                proto.close()
            except OSError as e:
                log.debug("close_all: protocol.close raised: %s", e)
        cls._protocols.clear()

    @classmethod
    def get_cached_count(cls) -> int:
        """Number of cached protocols (for testing)."""
        return len(cls._protocols)

    # =================================================================
    # USB transport + backend helpers
    # =================================================================

    @staticmethod
    def _check_sg_raw() -> bool:
        """Check if sg_raw is available on the system."""
        import shutil
        return shutil.which("sg_raw") is not None

    @staticmethod
    def create_usb_transport(
        vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ):
        """Create the best available USB transport (pyusb preferred, hidapi fallback).

        ``usb_address`` (bus + address) binds the transport to a specific physical
        USB device — required when two coolers share the same VID:PID (#128).
        """
        from .hid import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
        if PYUSB_AVAILABLE:
            from .hid import PyUsbTransport
            return PyUsbTransport(vid, pid, usb_address=usb_address)
        elif HIDAPI_AVAILABLE:
            from .hid import HidApiTransport
            return HidApiTransport(vid, pid, usb_address=usb_address)
        else:
            raise ImportError(
                "No USB backend available. Install pyusb or hidapi:\n"
                "  pip install pyusb   (+ apt install libusb-1.0-0)\n"
                "  pip install hidapi  (+ apt install libhidapi-dev)"
            )

    @staticmethod
    def _get_hid_backends() -> dict[str, bool]:
        """Check HID backend availability."""
        try:
            from .hid import HIDAPI_AVAILABLE, PYUSB_AVAILABLE
            return {"pyusb": PYUSB_AVAILABLE, "hidapi": HIDAPI_AVAILABLE}
        except ImportError:
            return {"pyusb": False, "hidapi": False}

    @classmethod
    def get_backend_availability(cls) -> dict[str, bool]:
        """Check which USB/SCSI backends are installed.

        Returns dict with keys: sg_raw, pyusb, hidapi — each True/False.
        """
        hid = cls._get_hid_backends()
        return {
            "sg_raw": cls._check_sg_raw(),
            "pyusb": hid["pyusb"],
            "hidapi": hid["hidapi"],
        }

    @classmethod
    def get_protocol_info(cls, device_info=None) -> 'ProtocolInfo':
        """Get protocol/backend info for a device (or system defaults).

        If a cached protocol exists for this device, delegates to its get_info().
        Otherwise builds ProtocolInfo from backend availability.

        Args:
            device_info: DeviceInfo object (or None for system-level info).

        Returns:
            ProtocolInfo with all fields populated.
        """
        if device_info is None:
            backends = cls.get_backend_availability()
            return ProtocolInfo(
                protocol="none",
                device_type=0,
                protocol_display="No device",
                device_type_display="",
                active_backend="none",
                backends=backends,
            )

        # If there's a cached protocol, ask it directly
        key = cls._device_key(device_info)
        if (proto := cls._protocols.get(key)) is not None:
            return proto.get_info()

        # No cached protocol — build info from scratch
        backends = cls.get_backend_availability()
        protocol = getattr(device_info, 'protocol', 'scsi')
        device_type = getattr(device_info, 'device_type', 1)

        from trcc.core.models import PROTOCOL_TRAITS
        traits = PROTOCOL_TRAITS.get(protocol)
        if traits is None:
            active = "none"
        elif backends.get(traits.backend_key, False):
            active = traits.backend_key
        elif traits.fallback_backend and backends.get(traits.fallback_backend, False):
            active = traits.fallback_backend
        else:
            active = "none"

        return ProtocolInfo(
            protocol=protocol,
            device_type=device_type,
            protocol_display=PROTOCOL_NAMES.get(protocol, protocol),
            device_type_display=DEVICE_TYPE_NAMES.get(device_type, f"Type {device_type}"),
            active_backend=active,
            backends=backends,
            transport_open=False,
        )


# =========================================================================
# Protocol Info API — for GUI to query device/backend state
# =========================================================================

# Domain data re-exported from core.models (canonical location):
# PROTOCOL_NAMES, DEVICE_TYPE_NAMES, LED_DEVICE_TYPE_NAME


@dataclass
class ProtocolInfo:
    """Protocol and backend info for a device — returned to the GUI.

    Usage in GUI::

        info = DeviceProtocolFactory.get_protocol_info(device)
        label.setText(f"{info.protocol_display} via {info.active_backend}")
    """
    protocol: str = "scsi"
    device_type: int = 1
    protocol_display: str = ""
    device_type_display: str = ""
    active_backend: str = ""
    backends: dict[str, bool] = field(default_factory=dict)
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
        from trcc.core.models import PROTOCOL_TRAITS
        traits = PROTOCOL_TRAITS.get(self.protocol)
        if traits is None:
            return False
        if self.backends.get(traits.backend_key, False):
            return True
        return bool(traits.fallback_backend
                    and self.backends.get(traits.fallback_backend, False))



# =========================================================================
# ProtocolFactory — one factory subclass per protocol kind.
#
# Mirrors the ``PlatformFactory`` idiom in ``adapters/system/__init__.py``:
# ABC + ``@ProtocolFactory.register('name')`` self-registering subclasses +
# single ``ProtocolFactory.for_info(info)`` chokepoint that dispatches by
# the protocol *name* carried on ``DeviceInfo``. Two factory layers, one
# pattern — readers learn it once and apply it everywhere.
#
# Imports are at the bottom to avoid the circular dependency (each protocol
# module imports DeviceProtocol/UsbProtocol/ProtocolInfo from this module).
# =========================================================================

from .bulk_protocol import BulkProtocol  # noqa: E402
from .hid_protocol import HidProtocol  # noqa: E402
from .led_protocol import LedProtocol  # noqa: E402
from .ly_protocol import LyProtocol  # noqa: E402
from .scsi_protocol import ScsiProtocol  # noqa: E402


class ProtocolFactory(ABC):
    """Abstract factory for a single ``DeviceProtocol`` class.

    Subclasses self-register via ``@ProtocolFactory.register('name')``. The
    registry is consulted via ``ProtocolFactory.for_info(info)`` — the
    single chokepoint that turns a ``DeviceInfo`` into the right
    ``DeviceProtocol`` instance (DI'd into ``Device`` at construction).

    Open/Closed: new protocol support = one new decorated subclass. Zero
    touchpoints elsewhere.
    """

    _registry: ClassVar[dict[str, type['ProtocolFactory']]] = {}

    @classmethod
    def register(cls, name: str):
        """Mark a factory subclass as the one to use for ``info.protocol == name``."""
        def deco(sub: type['ProtocolFactory']) -> type['ProtocolFactory']:
            cls._registry[name] = sub
            return sub
        return deco

    @classmethod
    def for_info(cls, info: 'DeviceInfo') -> DeviceProtocol:
        """Build the right ``DeviceProtocol`` for a ``DeviceInfo`` (dispatch by name)."""
        factory_cls = cls._registry.get(info.protocol)
        if factory_cls is None:
            raise ValueError(f"Unknown protocol: {info.protocol!r}")
        return factory_cls().make(info)

    @abstractmethod
    def make(self, info: 'DeviceInfo') -> DeviceProtocol:
        """Construct the protocol instance from a ``DeviceInfo``."""


@ProtocolFactory.register('scsi')
class ScsiProtocolFactory(ProtocolFactory):
    """Build ``ScsiProtocol`` for ``info.protocol == 'scsi'``."""

    def make(self, info: 'DeviceInfo') -> DeviceProtocol:
        return ScsiProtocol(info.path, info.vid, info.pid, usb_address=info.usb_address)


@ProtocolFactory.register('hid')
class HidProtocolFactory(ProtocolFactory):
    """Build ``HidProtocol`` (Type 2 or Type 3 chosen via ``info.device_type``)."""

    def make(self, info: 'DeviceInfo') -> DeviceProtocol:
        return HidProtocol(
            vid=info.vid, pid=info.pid,
            usb_address=info.usb_address,
            device_type=info.device_type,
        )


@ProtocolFactory.register('bulk')
class BulkProtocolFactory(ProtocolFactory):
    """Build ``BulkProtocol`` for raw-USB-bulk LCDs (USBLCDNew)."""

    def make(self, info: 'DeviceInfo') -> DeviceProtocol:
        return BulkProtocol(vid=info.vid, pid=info.pid, usb_address=info.usb_address)


@ProtocolFactory.register('ly')
class LyProtocolFactory(ProtocolFactory):
    """Build ``LyProtocol`` for LY USB-bulk LCDs (0416:5408 / 0416:5409)."""

    def make(self, info: 'DeviceInfo') -> DeviceProtocol:
        return LyProtocol(vid=info.vid, pid=info.pid, usb_address=info.usb_address)


@ProtocolFactory.register('led')
class LedProtocolFactory(ProtocolFactory):
    """Build ``LedProtocol`` for RGB LED controllers (HID 64-byte reports)."""

    def make(self, info: 'DeviceInfo') -> DeviceProtocol:
        return LedProtocol(vid=info.vid, pid=info.pid, usb_address=info.usb_address)


# Bridge the new factory subclasses into the orchestration class's registry —
# keeps every legacy caller (``DeviceProtocolFactory.create_protocol`` /
# ``.get_protocol``) working without rewrites.


def _legacy_bridge(factory_cls: type[ProtocolFactory]) -> Callable[['DeviceInfo'], DeviceProtocol]:
    """Adapt a ``ProtocolFactory`` subclass into the legacy 1-arg builder.

    The legacy registry expects ``builder(info) -> DeviceProtocol``; the
    new factory subclasses expose ``instance.make(info)``.  This wraps
    one instance call in a named closure — no anonymous curried lambda.
    """
    factory_instance = factory_cls()
    def build(info: 'DeviceInfo') -> DeviceProtocol:
        return factory_instance.make(info)
    return build


DeviceProtocolFactory._PROTOCOL_REGISTRY = {
    name: _legacy_bridge(factory_cls)
    for name, factory_cls in ProtocolFactory._registry.items()
}
