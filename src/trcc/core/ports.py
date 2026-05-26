"""Ports — ABCs that adapters implement.

Pure contract definitions.  Adapter implementations live in
`trcc.adapters.*`.  Services and App depend on these ABCs, never on
concrete implementations.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from .events import EventBus
    from .models import (
        DeviceInfo,
        HandshakeResult,
        ProductInfo,
        RawFrame,
        SensorReading,
    )
    from .protocol import DeviceProfile


# =========================================================================
# Transports — byte movers, one ABC per wire family
# =========================================================================
#
# Two transport families cover every protocol:
#
#   BulkTransport  — raw USB bulk/interrupt read/write (HID, BULK, LY, LED)
#   ScsiTransport  — SCSI CDB + data phase, kernel-native where possible
#                    (Linux SG_IO, Windows DeviceIoControl, macOS/BSD BOT)
#
# Protocols hold one of these; they don't care which OS subclass is
# injected.  Platform.open(vid, pid, wire) returns the right transport
# for (OS, wire).


class BulkTransport(ABC):
    """Abstract USB bulk/interrupt transport.  One per open device handle."""

    @abstractmethod
    def open(self) -> bool:
        """Open the device and claim interface.  True on success."""

    @abstractmethod
    def close(self) -> None:
        """Release interface and close the handle."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the transport currently holds an open handle."""

    @abstractmethod
    def write(self, endpoint: int, data: bytes,
              timeout_ms: int = 100) -> int:
        """Bulk-write bytes to an OUT endpoint.  Returns bytes transferred."""

    @abstractmethod
    def read(self, endpoint: int, length: int,
             timeout_ms: int = 100) -> bytes:
        """Bulk-read up to *length* bytes from an IN endpoint."""


class ScsiTransport(ABC):
    """Abstract SCSI transport.  One per open device handle.

    Uses CDB-level primitives so the kernel (Linux SG_IO, Windows
    DeviceIoControl) can bundle CDB + data + status in a single syscall
    where the OS supports it.  macOS/BSD fall back to userspace BOT.
    """

    @abstractmethod
    def open(self) -> bool:
        """Open the device.  True on success."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the transport currently holds an open handle."""

    @abstractmethod
    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
        """Send a 16-byte CDB with a data-out payload.  True on CSW status 0."""

    @abstractmethod
    def read_cdb(self, cdb: bytes, length: int,
                 timeout_ms: int = 5000) -> bytes:
        """Send a 16-byte CDB and read *length* bytes of data-in."""


# Transport type variable — constrained to the two transport ABCs.
# Each Device subclass binds T to the transport it needs, so
# `self._transport.write(...)` narrows correctly per device.
T = TypeVar("T", BulkTransport, ScsiTransport)


# =========================================================================
# Device — one per physical device, knows its wire protocol
# =========================================================================


class Device(ABC, Generic[T]):
    """A physical USB device we control.

    Concrete subclasses (ScsiLcd, HidLcd, BulkLcd, LyLcd, Led) own their
    wire protocol and declare the transport they need via the type
    parameter: `class ScsiLcd(Device[ScsiTransport])`.  The transport
    is DI'd at construction — devices never build their own.

    All devices share the same outward contract: connect / send /
    disconnect.  They know nothing about the OS, Platform, or other
    devices.
    """

    def __init__(self, info: ProductInfo, transport: T) -> None:
        self.info = info
        self._transport: T = transport
        self._handshake: HandshakeResult | None = None
        # Auto-recovery state — tracks consecutive disconnect-class
        # send failures + rate-limits the warning log.  Reset on every
        # successful send via ``_recovery.note_success``.
        from .device_recovery import RecoveryTracker
        self._recovery = RecoveryTracker(self.info.key)

    @abstractmethod
    def connect(self) -> HandshakeResult:
        """Open the transport and perform the wire-protocol handshake."""

    @abstractmethod
    def send(self, payload: Any) -> bool:
        """Send a payload in device-native format.  Protocol-specific shape."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the transport and release state."""

    @property
    def is_connected(self) -> bool:
        return self._handshake is not None

    @property
    def is_led(self) -> bool:
        """True for LED-control devices; False for LCD-frame devices."""
        return False

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived geometry + encoding profile.

        Set by LCD subclasses when ``connect()`` parses the PM/FBL bytes.
        LED devices and pre-handshake state both return None — callers
        that build frames must fall back to ``info.native_resolution``.
        """
        return None


# =========================================================================
# Sensor sources — one ABC per hardware role
# =========================================================================
#
# Every reading is Optional[float].  None means "this hardware doesn't
# expose it" — a headless VM has no CPU temp, an APU has no discrete
# GPU, a server has no fans.  Overlays skip None silently so barebones
# and $5k rigs use the same themes, show what they have.
#
# Units are normalized at the source:
#     temp → °C     clock → MHz     power → W
#     memory → MB   percent → 0-100
#
# Overlay keys use normalized, vendor-neutral names:
#     cpu:temp  cpu:usage  cpu:freq  cpu:power
#     gpu:primary:temp  gpu:0:temp  gpu:nvidia:0:temp
#     memory:used  memory:percent
#     fan:cpu:rpm  fan:gpu:percent


class CpuSource(ABC):
    """Primary CPU.  usage/freq nearly always present; temp/power may be None."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def temp(self) -> float | None:
        """CPU package temperature in °C, or None."""

    @abstractmethod
    def usage(self) -> float | None:
        """CPU utilization 0-100, or None."""

    @abstractmethod
    def freq(self) -> float | None:
        """Current CPU frequency in MHz, or None."""

    @abstractmethod
    def power(self) -> float | None:
        """Package power draw in W, or None."""


class MemorySource(ABC):
    """System RAM."""

    @abstractmethod
    def used(self) -> float | None:
        """Used RAM in MB, or None."""

    @abstractmethod
    def available(self) -> float | None:
        """Available RAM in MB, or None."""

    @abstractmethod
    def total(self) -> float | None:
        """Total RAM in MB, or None."""

    @abstractmethod
    def percent(self) -> float | None:
        """Used fraction 0-100, or None."""


class GpuSource(ABC):
    """One GPU — NVIDIA/AMD/Intel/Apple, discrete or integrated."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable ID, e.g. 'nvidia:0', 'amd:0', 'intel:igpu'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable model name."""

    @property
    @abstractmethod
    def is_discrete(self) -> bool:
        """True for dedicated cards, False for iGPUs sharing CPU memory."""

    @abstractmethod
    def temp(self) -> float | None:
        """Core temperature in °C, or None."""

    @abstractmethod
    def usage(self) -> float | None:
        """Utilization 0-100, or None."""

    @abstractmethod
    def clock(self) -> float | None:
        """Core clock in MHz, or None."""

    @abstractmethod
    def power(self) -> float | None:
        """Board power draw in W, or None."""

    @abstractmethod
    def fan(self) -> float | None:
        """Fan speed 0-100, or None."""

    @abstractmethod
    def vram_used(self) -> float | None:
        """VRAM used in MB, or None."""

    @abstractmethod
    def vram_total(self) -> float | None:
        """VRAM total in MB, or None."""


class FanSource(ABC):
    """One fan — may be role-mapped (cpu/gpu/sys1) or anonymous."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable ID, e.g. 'cpu', 'gpu', 'sys1', 'hwmon:nct6798:fan1'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable label."""

    @abstractmethod
    def rpm(self) -> int | None:
        """Current RPM, or None."""

    @abstractmethod
    def percent(self) -> float | None:
        """Duty cycle 0-100, or None."""


# =========================================================================
# SensorEnumerator — the aggregate: composes one CPU + one memory + N GPUs + N fans
# =========================================================================


class SensorEnumerator(ABC):
    """OS-level sensor root.  Each OS has one implementation.

    Exposes structured access (cpu, memory, gpus, fans) AND a flat
    dict view for overlays keyed by normalized names.
    """

    # ── Structured access ───────────────────────────────────────────
    @abstractmethod
    def cpu(self) -> CpuSource: ...

    @abstractmethod
    def memory(self) -> MemorySource: ...

    @abstractmethod
    def gpus(self) -> list[GpuSource]:
        """All detected GPUs, sorted discrete-first.  Empty if no GPU."""

    @abstractmethod
    def fans(self) -> list[FanSource]:
        """All detected fans.  Empty if none."""

    def primary_gpu(self) -> GpuSource | None:
        """First discrete GPU, else first integrated, else None."""
        gpus = self.gpus()
        for gpu in gpus:
            if gpu.is_discrete:
                return gpu
        return gpus[0] if gpus else None

    # ── Flat dict view (for overlay lookups) ────────────────────────
    @abstractmethod
    def discover(self) -> list[SensorReading]:
        """One SensorReading per normalized key.  Snapshot at call time."""

    @abstractmethod
    def read_all(self) -> dict[str, float]:
        """Current readings keyed by normalized name.  Omits None values."""

    @abstractmethod
    def read_one(self, sensor_id: str) -> float | None:
        """Read a single normalized key."""

    @abstractmethod
    def start_polling(self, interval_s: float = 2.0) -> None: ...

    @abstractmethod
    def stop_polling(self) -> None: ...


# =========================================================================
# Paths — where user data lives on this OS
# =========================================================================


class Paths(ABC):
    """Filesystem locations.  Each OS resolves these differently.

    Resolution-aware helpers (`theme_dir`, `cloud_theme_dir`,
    `cloud_mask_dir`, `user_mask_dir`) are concrete on the ABC because
    every OS uses the same subpath layout — only the root differs.

    Layout convention: ``data_dir()`` is the package + cloud-downloaded
    content root (``~/.trcc/data/``); ``user_content_dir()`` is the
    user-saved content root (``~/.trcc-user/``).  Both are content
    roots — the per-resolution sub-tree shape is identical under each:

        <root>/theme{w}{h}/<name>/
        <root>/web/zt{w}{h}/<name>/

    Reads beneath ``data_dir()`` and ``user_content_dir()`` are
    symmetric; only the root differs.
    """

    @abstractmethod
    def config_dir(self) -> Path: ...

    @abstractmethod
    def data_dir(self) -> Path: ...

    @abstractmethod
    def user_content_dir(self) -> Path: ...

    @abstractmethod
    def log_file(self) -> Path: ...

    def theme_dir(self, width: int, height: int) -> Path:
        """Themes shipped with the app or downloaded from GitHub releases."""
        return self.data_dir() / f"theme{width}{height}"

    def user_theme_dir(self, width: int, height: int) -> Path:
        """Per-resolution user-saved theme dir.

        Mirrors :meth:`theme_dir` shape, rooted at
        :meth:`user_content_dir` instead of :meth:`data_dir`.
        """
        return self.user_content_dir() / f"theme{width}{height}"

    def cloud_theme_dir(self, width: int, height: int) -> Path:
        """Cloud-catalog themes downloaded at runtime."""
        return self.data_dir() / "web" / f"{width}{height}"

    def cloud_mask_dir(self, width: int, height: int) -> Path:
        """Cloud-catalog masks downloaded at runtime."""
        return self.data_dir() / "web" / f"zt{width}{height}"

    def user_mask_dir(self, width: int, height: int) -> Path:
        """User-created masks — survives uninstall + redownload.

        Mirrors :meth:`cloud_mask_dir` shape, rooted at
        :meth:`user_content_dir` instead of :meth:`data_dir`.
        """
        return self.user_content_dir() / "web" / f"zt{width}{height}"


# =========================================================================
# Renderer — pixel operations (PySide6 on all OSes today)
# =========================================================================


class Renderer(ABC):
    """Rendering backend.  Concrete: QtRenderer (adapters/render/qt.py)."""

    # ── Surfaces ──────────────────────────────────────────────────────
    @abstractmethod
    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any: ...

    @abstractmethod
    def open_image(self, path: Path) -> Any: ...

    @abstractmethod
    def surface_size(self, surface: Any) -> tuple[int, int]: ...

    # ── Compositing ───────────────────────────────────────────────────
    @abstractmethod
    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any: ...

    @abstractmethod
    def resize(self, surface: Any, width: int, height: int) -> Any: ...

    @abstractmethod
    def rotate(self, surface: Any, degrees: int) -> Any: ...

    @abstractmethod
    def flip_horizontal(self, surface: Any) -> Any:
        """Return a horizontally-mirrored copy of *surface*.

        Used by the split-mode (Dynamic Island) overlay path:
        authored assets cover the left side of the canvas, so the
        renderer flips them when the device's PanelCutout sits on
        the right.
        """
        ...

    # ── Adjustments ───────────────────────────────────────────────────
    @abstractmethod
    def apply_brightness(self, surface: Any, percent: int) -> Any: ...

    # ── Text ──────────────────────────────────────────────────────────
    # NOTE: (x, y) is the text CENTER, not top-left.  Matches the C#
    # reference (``TRCC.decompiled.cs:52829``) where every overlay
    # element is drawn into ``RectangleF(myX - w/2, myY - h/2, w, h)``.
    # DC files store element coordinates as centers; the renderer is
    # the only layer that knows font metrics, so the center-to-baseline
    # math lives here, not in OverlayService.
    @abstractmethod
    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None: ...

    # ── Encoding ──────────────────────────────────────────────────────
    @abstractmethod
    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes: ...

    @abstractmethod
    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes: ...

    def encode_png(self, surface: Any) -> bytes:
        """Encode the surface as PNG bytes.

        Used by ``GET /devices/{key}/display/preview`` to return a
        dashboard-friendly frame snapshot.  Lossless so screenshots
        + overlay text stay legible, unlike ``encode_jpeg``.

        Non-abstract — concrete Renderers (only QtRenderer in next/
        today) override; the default raises so test fakes that don't
        exercise the preview path stay minimal.
        """
        del surface
        raise NotImplementedError("encode_png not implemented on this Renderer")

    # ── Legacy boundary (video frames) ────────────────────────────────
    @abstractmethod
    def from_raw_rgb24(self, frame: RawFrame) -> Any: ...


# =========================================================================
# ScreenCapture — grab a region of the user's desktop as raw RGB bytes
# =========================================================================


class ScreenCapture(ABC):
    """Port for "grab a rectangle off the desktop right now".

    Adapters: :class:`QtScreenCapture` for Qt apps (X11 + Wayland
    fallback).  Used by the screencast pipeline to feed live desktop
    pixels into the device.

    Returns a :class:`RawFrame` with RGB24 pixel data sized exactly to
    the requested rectangle — callers handle scale/fit/encode.
    """

    @abstractmethod
    def grab_region(self, x: int, y: int, width: int, height: int) -> RawFrame:
        """Capture *width* × *height* pixels starting at (*x*, *y*).

        Raise :class:`OSError` (or subclass) on capture failure — the
        caller decides whether to retry, stop the screencast, or surface
        the error to the user.
        """
        ...


# =========================================================================
# HttpFetcher — minimal HTTP GET, abstracted so tests can intercept
# =========================================================================


class HttpFetcher(ABC):
    """Tiny port for "fetch bytes from URL" used by cloud-theme adapters.

    Separate from full ``requests``/``httpx`` use because next/'s needs
    are minimal — GET a small/medium body with a timeout, multi-server
    fallback handled by the caller.  Tests inject a fake that returns
    canned bytes; production uses ``UrllibHttpFetcher``.
    """

    @abstractmethod
    def fetch(self, url: str, timeout_s: float = 30.0) -> bytes:
        """Fetch a URL's body.  Raise on non-200 status or transport error."""
        ...


# =========================================================================
# AutostartManager — OS-specific boot-time launch configuration
# =========================================================================


class AutostartManager(ABC):
    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def enable(self) -> None: ...

    @abstractmethod
    def disable(self) -> None: ...

    @abstractmethod
    def refresh(self) -> None: ...


# =========================================================================
# HotplugMonitor — OS-specific add/remove + sleep/wake listener
# =========================================================================


class HotplugMonitor(ABC):
    """Background listener that pushes hardware events onto the EventBus.

    Implementations spawn one daemon thread that translates OS-native
    udev / IOKit / WM_DEVICECHANGE notifications into
    :class:`DeviceAttached` / :class:`DeviceDetached` (for registry-known
    vid:pid combos) and, where the OS exposes it,
    :class:`SystemSuspending` / :class:`SystemResumed`.

    UIs / Commands never call into the monitor directly — they subscribe
    to the EventBus.
    """

    @abstractmethod
    def start(self, bus: EventBus) -> None:
        """Begin listening.  Idempotent — calling twice is a no-op."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening + clean up the listener thread."""

    @property
    @abstractmethod
    def is_running(self) -> bool: ...


# =========================================================================
# Platform — OS root, one instance per app
# =========================================================================


class Platform(ABC):
    """OS abstraction.  DI'd into App at startup.

    Responsibilities:
        - Enumerate attached devices (scan_devices).
        - Open USB handles (open_usb).
        - Expose sensors, paths, autostart.
        - Run OS-specific setup (udev, WinUSB guide, etc.).
    """

    # ── Transport factories — one per wire family ────────────────────
    @abstractmethod
    def open_bulk(self, vid: int, pid: int,
                  serial: str | None = None) -> BulkTransport:
        """Return an unopened BulkTransport for a USB-bulk device.

        Used by HID / BULK / LY / LED protocols.  Every OS can do this
        via libusb, so the concrete class is usually shared.
        """

    @abstractmethod
    def open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        """Return an unopened ScsiTransport for a SCSI-LCD device.

        Used by SCSI protocols.  Each OS has a native path:
            Linux   → SG_IO ioctl on /dev/sgN
            Windows → DeviceIoControl on the raw volume
            macOS   → USB BOT (no SG equivalent)
            BSD     → USB BOT
        """

    @abstractmethod
    def scan_devices(self) -> list[DeviceInfo]:
        """Enumerate currently-attached supported devices."""

    # ── Filesystem ────────────────────────────────────────────────────
    @abstractmethod
    def paths(self) -> Paths: ...

    # ── Sensors ───────────────────────────────────────────────────────
    @abstractmethod
    def sensors(self) -> SensorEnumerator: ...

    # ── Autostart ─────────────────────────────────────────────────────
    @abstractmethod
    def autostart(self) -> AutostartManager: ...

    # ── Hotplug ───────────────────────────────────────────────────────
    @abstractmethod
    def hotplug(self) -> HotplugMonitor:
        """Return the OS hotplug listener.

        Caller manages lifecycle — typically the daemon starts it once
        on boot and stops it on shutdown.  Sub-Platforms that can't
        observe USB hotplug yield a no-op monitor.
        """

    # ── One-time setup (udev rules / WinUSB guide / etc.) ─────────────
    @abstractmethod
    def setup(self, interactive: bool = True) -> int:
        """Run OS-specific setup.  Returns a shell-style exit code."""

    @abstractmethod
    def check_permissions(self) -> list[str]:
        """Return a list of user-facing permission warnings, empty if OK."""

    # ── OS identity (for UIs, diagnostics, install hints) ─────────────
    @abstractmethod
    def distro_name(self) -> str: ...

    @abstractmethod
    def install_method(self) -> str:
        """How this app was installed: pip, rpm, deb, pacman, app-bundle..."""

    # ── GUI / hardware-probe convenience ──────────────────────────────
    def minimize_on_close(self) -> bool:
        """True if the GUI should minimize-to-tray on close instead of hiding.

        Default ``False`` (hide-to-tray) matches Linux/macOS/BSD behaviour.
        Windows overrides to ``True`` to match user expectations there.
        """
        return False

    def configure_stdout(self) -> None:
        """Adjust the interpreter's stdout/stderr at startup if the OS
        needs it (Windows ↔ cp1252 console).

        Default no-op for Linux / macOS / BSD — their consoles already
        speak UTF-8.  Called from every UI entry point BEFORE
        ``configure_logging`` so the StreamHandler attaches to an
        already-UTF-8-safe stream.
        """

    def memory_info(self) -> list[dict[str, str]]:
        """Return DRAM slot descriptors for LC1-style memory displays.

        Each dict carries keys like ``size`` / ``type`` / ``speed`` /
        ``manufacturer`` / ``tcas`` / ``trcd`` / … as discovered.  An OS
        with no probe yields an empty list — the caller renders ``NC``.
        """
        return []

    def disk_info(self) -> list[dict[str, str]]:
        """Return attached-disk descriptors for LF11-style disk displays.

        Each dict carries ``name`` / ``model`` / ``size`` / ``type`` /
        optional ``health``.  An OS with no probe yields an empty list.
        """
        return []

    # ── OS-selection factory ──────────────────────────────────────────
    _BY_OS: dict[str, tuple[str, str]] = {
        "linux": ("trcc.adapters.system.linux", "LinuxPlatform"),
        "win32": ("trcc.adapters.system.windows", "WindowsPlatform"),
        "darwin": ("trcc.adapters.system.macos", "MacOSPlatform"),
        "bsd": ("trcc.adapters.system.bsd", "BSDPlatform"),
    }

    @classmethod
    def detect(cls) -> Platform:
        """Pick the right Platform subclass for the running OS."""
        import importlib
        import sys

        key = sys.platform
        if "bsd" in key:
            key = "bsd"
        if key not in cls._BY_OS:
            key = "linux"
        module_path, class_name = cls._BY_OS[key]
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)()


# =========================================================================
# Callable type aliases (infrastructure DI)
# =========================================================================

DetectDevicesFn = Callable[[], list["DeviceInfo"]]
