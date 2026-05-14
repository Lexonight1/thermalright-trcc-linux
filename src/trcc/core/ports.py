"""Core ports — ABCs that define contracts for adapter implementations.

Ports live in core/ so both services/ and adapters/ can import them
without violating hexagonal dependency direction.

SOLID:
    S — Each ABC has one responsibility
    O — New device types extend Device without modifying existing code
    L — LCDDevice/LEDDevice fully substitutable as Device
    I — Device ABC: 4 methods. Renderer ABC: domain-focused groups.
        Replaces DisplayPort (47) and LEDPort (30) — ISP violations.
    D — All adapters depend on these core abstractions
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from trcc.core.models import JPEG_MAX_BYTES, DetectedDevice, HardwareMetrics

if TYPE_CHECKING:
    from trcc.core.models import SensorInfo, UsbAddress


@dataclass(frozen=True, slots=True)
class RawFrame:
    """Raw decoded video frame — pure bytes, no framework deps.

    Produced by media decoders (VideoDecoder, ThemeZtDecoder).
    Converted to native renderer surfaces by the render adapter.
    """
    data: bytes
    width: int
    height: int


class Renderer(ABC):
    """Port: rendering backend for the full image pipeline.

    Covers overlay compositing, image adjustments (brightness, rotation),
    device encoding (RGB565, JPEG), and file I/O.

    Concrete implementation:
        - QtRenderer (adapters/render/qt.py) — PySide6 QImage/QPainter
    """

    # ── Surface lifecycle ─────────────────────────────────────────

    @abstractmethod
    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        """Create a new rendering surface (blank transparent or solid color)."""

    @abstractmethod
    def copy_surface(self, surface: Any) -> Any:
        """Defensive copy of a surface."""

    @abstractmethod
    def convert_to_rgba(self, surface: Any) -> Any:
        """Ensure surface has alpha channel."""

    @abstractmethod
    def convert_to_rgb(self, surface: Any) -> Any:
        """Ensure surface is RGB (strip alpha)."""

    @abstractmethod
    def surface_size(self, surface: Any) -> tuple[int, int]:
        """Return (width, height) of a surface."""

    # ── Compositing ───────────────────────────────────────────────

    @abstractmethod
    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        """Alpha-composite *overlay* onto *base* at *position*."""

    @abstractmethod
    def resize(self, surface: Any, width: int, height: int) -> Any:
        """Resize surface with high-quality resampling."""

    # ── Text ──────────────────────────────────────────────────────

    @abstractmethod
    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, font: Any, anchor: str = 'mm') -> None:
        """Draw text onto surface at (x, y)."""

    @abstractmethod
    def get_font(self, size: int, bold: bool = False,
                 italic: bool = False,
                 font_name: str | None = None) -> Any:
        """Resolve and cache a font at given size."""

    @abstractmethod
    def clear_font_cache(self) -> None:
        """Flush font cache (e.g. after resolution change)."""

    # ── Image adjustments ─────────────────────────────────────────

    @abstractmethod
    def apply_brightness(self, surface: Any, percent: int) -> Any:
        """Apply brightness adjustment (100 = unchanged, 0 = black)."""

    @abstractmethod
    def apply_rotation(self, surface: Any, degrees: int) -> Any:
        """Rotate surface by 0/90/180/270 degrees."""

    # ── Device encoding ───────────────────────────────────────────

    @abstractmethod
    def encode_rgb565(self, surface: Any, byte_order: str = '>') -> bytes:
        """Encode surface to RGB565 bytes for LCD device."""

    @abstractmethod
    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = JPEG_MAX_BYTES) -> bytes:
        """Encode surface to JPEG bytes with size constraint."""

    # ── File I/O ──────────────────────────────────────────────────

    @abstractmethod
    def open_image(self, path: Any) -> Any:
        """Load image file into native surface."""

    # ── Drawing primitives ────────────────────────────────────────

    @abstractmethod
    def fill_rect(self, surface: Any, x: int, y: int,
                  w: int, h: int, color: tuple[int, ...]) -> None:
        """Fill a rectangle on the surface with solid color."""

    @abstractmethod
    def draw_rect_outline(self, surface: Any, x: int, y: int,
                          w: int, h: int, color: tuple[int, ...]) -> None:
        """Draw an unfilled rectangle outline on the surface."""

    @abstractmethod
    def get_pixels_rgb(self, surface: Any, cols: int,
                       rows: int) -> list[list[tuple[int, int, int]]]:
        """Return pixel grid scaled to cols×rows as (r, g, b) tuples.

        Used for ANSI terminal output — cold path, not hot path.
        """

    # ── Legacy boundary ───────────────────────────────────────────

    @abstractmethod
    def from_raw_rgb24(self, frame: RawFrame) -> Any:
        """Convert RawFrame (RGB24 bytes) → native surface."""

    # ── Wire serialization (for IPC frame events) ─────────────────

    @abstractmethod
    def encode_for_wire(self, surface: Any) -> bytes:
        """Encode a native surface to JSON-safe bytes (PNG by convention).

        Used by ``IPCServer`` to forward ``Topic.FRAME`` payloads to
        ``TrccProxy`` clients over the manifold socket.  Implementations
        SHOULD use a lossless format so the GUI's preview matches the
        device output pixel-for-pixel.
        """

    @abstractmethod
    def decode_from_wire(self, data: bytes) -> Any:
        """Symmetric to :meth:`encode_for_wire` — bytes → native surface.

        Used by ``EventBusProxy`` to reconstruct surfaces in the GUI
        process after they arrive over the wire.
        """


# =========================================================================
class DeviceConfigService:
    """Per-device config persistence — shared base for LCD and LED.

    Concrete implementation of device_key, persist, get_config.
    Subclasses add device-specific methods (apply_format_prefs for LCD,
    save_state/load_state for LED).
    """

    def __init__(
        self,
        config_key_fn: Callable[..., str],
        save_setting_fn: Callable[..., None],
        get_config_fn: Callable[..., dict],
    ) -> None:
        self._config_key_fn = config_key_fn
        self._save_fn = save_setting_fn
        self._get_fn = get_config_fn

    def device_key(self, dev: Any) -> str:
        """Compute per-device config key from device info."""
        return self._config_key_fn(dev.device_index, dev.vid, dev.pid)

    def persist(self, dev: Any, field: str, value: Any) -> None:
        """Save a single setting for a device."""
        if dev:
            self._save_fn(self.device_key(dev), field, value)

    def get_config(self, dev: Any) -> dict:
        """Read full per-device config dict."""
        if not dev:
            return {}
        return self._get_fn(self.device_key(dev))


# =========================================================================
# Infrastructure port types — injected into services via DI
# =========================================================================

# Type alias for device detection callable.
# Concrete: DeviceDetector.detect
DetectDevicesFn = Callable[[], list[Any]]

# Type alias for LED model probe callable.
# Concrete: probe_led_model
ProbeLedModelFn = Callable[..., Any]


@runtime_checkable
class DeviceProtocol(Protocol):
    """Port: protocol for communicating with a USB device."""

    def handshake(self) -> Any: ...
    def send_data(self, *args: Any, **kwargs: Any) -> bool: ...


# Type alias for protocol factory callable.
# Concrete: DeviceProtocolFactory.get_protocol
GetProtocolFn = Callable[[Any], DeviceProtocol]

# Type alias for protocol info query callable.
# Concrete: DeviceProtocolFactory.get_protocol_info
GetProtocolInfoFn = Callable[[Any], Any]

# Type alias for data archive extraction callable.
# Concrete: DataManager.ensure_all(width, height, progress_fn=None)
EnsureDataFn = Callable[..., None]

# Type alias for DC config file parser factory.
# Concrete: DcConfig (class itself, called as DcConfig(path))
DcConfigFactory = Callable[..., Any]

# Type alias for config.json loader.
# Concrete: dc_parser.load_config_json
LoadConfigJsonFn = Callable[[str], Any]

# Type alias for theme export callable.
# Concrete: dc_writer.export_theme
ExportThemeFn = Callable[[str, str], None]

# Type alias for theme import callable.
# Concrete: dc_writer.import_theme
ImportThemeFn = Callable[[str, str], None]

# Type alias for privileged command builder.
# Concrete: hardware._privileged_cmd
PrivilegedCmdFn = Callable[[str, list[str]], list[str]]

# Type aliases for platform hardware info callables.
GetMemoryInfoFn = Callable[[], list[dict[str, str]]]
GetDiskInfoFn = Callable[[], list[dict[str, str]]]


@dataclass(frozen=True, slots=True)
class DoctorPlatformConfig:
    """Platform-specific constants for the doctor health check.

    Each OS adapter returns one of these from doctor_config().
    doctor.py reads the fields and stays OS-blind.
    """
    distro_name: str
    pkg_manager: str | None
    check_libusb: bool
    extra_binaries: list[tuple[str, bool, str]]   # (name, required, note)
    run_gpu_check: bool
    run_udev_check: bool
    run_selinux_check: bool
    run_rapl_check: bool
    run_polkit_check: bool
    run_winusb_check: bool
    enable_ansi: bool


@dataclass(frozen=True, slots=True)
class ReportPlatformConfig:
    """Platform-specific constants for the diagnostic report.

    Each OS adapter returns one of these from report_config().
    debug_report.py reads the fields and stays OS-blind.
    """
    distro_name: str
    collect_lsusb: bool
    collect_udev: bool
    collect_selinux: bool
    collect_rapl: bool
    collect_device_permissions: bool


# =========================================================================
# Sensor Enumerator ABC — contract for platform sensor adapters
# =========================================================================


class SensorEnumerator(ABC):
    """Port: hardware sensor discovery and reading.

    Each platform adapter (Linux, Windows, macOS, BSD) implements this ABC
    to provide sensor data via native sources (hwmon, LHM, IOKit, sysctl).

    Concrete implementations:
        - SensorEnumerator (adapters/system/linux_platform.py)
        - SensorEnumerator (adapters/system/windows_platform.py)
        - SensorEnumerator (adapters/system/macos_platform.py)
        - SensorEnumerator (adapters/system/bsd_platform.py)
    """

    @abstractmethod
    def discover(self) -> list[SensorInfo]:
        """Scan hardware for available sensors. Call once at startup."""

    @abstractmethod
    def get_sensors(self) -> list[SensorInfo]:
        """Return previously discovered sensors."""

    @abstractmethod
    def get_by_category(self, category: str) -> list[SensorInfo]:
        """Filter sensors by category."""

    @abstractmethod
    def read_all(self) -> dict[str, float]:
        """Return current sensor readings (non-blocking, from cache)."""

    @abstractmethod
    def read_one(self, sensor_id: str) -> float | None:
        """Read a single sensor by ID."""

    @abstractmethod
    def start_polling(self, interval: float = 2.0) -> None:
        """Start background polling thread."""

    @abstractmethod
    def stop_polling(self) -> None:
        """Stop background polling thread."""

    @abstractmethod
    def set_poll_interval(self, seconds: float) -> None:
        """Set background poll interval (user's data refresh setting)."""

    @abstractmethod
    def map_defaults(self) -> dict[str, str]:
        """Map legacy metric keys to sensor IDs for overlay rendering.

        Returns dict like {'cpu_temp': 'hwmon:coretemp:temp1', ...}.
        """

    @abstractmethod
    def set_preferred_gpu(self, gpu_key: str) -> None:
        """Set user-selected GPU for metric mapping."""

    @abstractmethod
    def get_gpu_list(self) -> list[tuple[str, str]]:
        """Return discovered GPUs as (gpu_key, display_name) pairs."""


# =========================================================================
# Autostart Manager ABC — shared ensure() logic, OS-specific mechanisms
# =========================================================================


class AutostartManager(ABC):
    """Port: platform-specific autostart mechanism.

    Each platform adapter implements the four abstract methods.
    The concrete ensure() method provides first-launch auto-enable logic
    shared across all platforms.

    Concrete implementations:
        - LinuxAutostartManager   (adapters/system/linux_platform.py)  — XDG .desktop
        - WindowsAutostartManager (adapters/system/windows_platform.py) — winreg Run key
        - MacOSAutostartManager   (adapters/system/macos_platform.py)  — Launch Agent plist
        - LinuxAutostartManager   reused for BSD (XDG .desktop)
    """

    @staticmethod
    def get_exec() -> str:
        """Resolve full path to trcc binary (shared across all platforms).

        Resolution order:
        1. PyInstaller bundle — sys.executable (trcc.exe / trcc)
        2. pip/pipx install  — shutil.which('trcc')
        3. git clone fallback — PYTHONPATH=<src> python -m trcc.cli
        """
        import shutil
        import sys
        from pathlib import Path

        if getattr(sys, 'frozen', False):
            return sys.executable
        trcc_path = shutil.which('trcc')
        if trcc_path:
            return trcc_path
        src_dir = str(Path(__file__).parent.parent.parent)
        return f'env PYTHONPATH={src_dir} {sys.executable} -m trcc.cli'

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True if autostart is currently configured."""

    @abstractmethod
    def enable(self) -> None:
        """Register autostart entry for the current user."""

    @abstractmethod
    def disable(self) -> None:
        """Remove autostart entry for the current user."""

    @abstractmethod
    def refresh(self) -> None:
        """Update the autostart entry if the binary path has changed."""

    def ensure(self) -> bool:
        """Auto-enable on first launch; refresh on subsequent launches.

        On first launch: calls enable() and marks config as configured.
        On subsequent launches: calls refresh() to keep path current.
        Returns the current autostart state.
        """
        from ..conf import load_config, save_config

        config = load_config()
        if not config.get('autostart_configured'):
            self.enable()
            config['autostart_configured'] = True
            save_config(config)
            return True

        self.refresh()
        return self.is_enabled()


# =========================================================================
# Metrics ABC — OS-specific HardwareMetrics builder (snapshot per instance)
# =========================================================================


class Metrics(ABC):
    """Port: build a :class:`HardwareMetrics` snapshot in ``__init__``.

    Each subclass represents ONE OS's composition strategy.  The
    constructor IS the build process — each subclass calls a sequence
    of private read methods to populate ``self.record``:

      * **Linux** — datetime + SensorEnumerator + lm_sensors +
        dmidecode + smartctl.  Short __init__, mostly leveraging the
        hwmon/psutil enumerator.
      * **Windows** — datetime + SensorEnumerator (which already
        chains HWiNFO + LHM + MSAcpi + psutil + pynvml) + WMI one-shots
        + future ADLX/IntelL0.  Longer __init__.
      * **macOS** — datetime + SensorEnumerator (IOKit + powermetrics
        + psutil) + future macmon ANE reads.
      * **BSD** — datetime + SensorEnumerator (sysctl + hw.sensors +
        psutil).

    The OS subclass's ``__init__`` length tells the truth about how
    hard metrics composition is on that OS.  The public contract is
    simply: instantiate, read ``self.record``.

    Subprocess and other long-lived state belong on the Platform
    subclass and get passed into the Metrics constructor — that way
    per-tick instantiation doesn't re-run subprocess calls.
    """

    @abstractmethod
    def __init__(self) -> None:
        """Subclasses MUST override — the ``__init__`` body IS the build pass.

        The override should call ``super().__init__()`` to allocate the
        empty ``self.record`` then invoke whatever read methods (one for
        each OS source) populate it.  This contract — "the constructor is
        the read pass" — is enforced by making ``__init__`` abstract so
        ``Metrics()`` cannot be instantiated directly.
        """
        from trcc.core.models import HardwareMetrics as _HM
        self.record: HardwareMetrics = _HM()

    def _read_datetime(self) -> None:
        """Shared: populate the datetime fields from ``datetime.now()``.

        These metrics are never sensor-backed, so they bypass the
        ``HardwareMetrics._populated`` gate inside renderers.  Every
        OS calls this from its ``__init__``.
        """
        import datetime as _dt
        m = self.record
        now = _dt.datetime.now()
        m.date_year = now.year
        m.date_month = now.month
        m.date_day = now.day
        m.time_hour = now.hour
        m.time_minute = now.minute
        m.time_second = now.second
        m.day_of_week = now.weekday()
        m._populated.update((
            'date_year', 'date_month', 'date_day',
            'time_hour', 'time_minute', 'time_second',
            'day_of_week', 'date', 'time', 'weekday',
        ))

    def _read_sensors(self, enumerator: SensorEnumerator) -> None:
        """Shared: copy enumerator readings into the typed DTO + readings dict.

        Walks ``enumerator.map_defaults()`` (the OS's metric_key →
        sensor_id mapping) and copies whatever the OS provided into
        ``self.record``.  Also stores the raw cache on
        ``self.record.readings`` so consumers iterating by sensor_id
        (GUI sensor panel) read from the same record.
        """
        from trcc.core.models.sensor import FLOAT_FIELDS

        m = self.record
        readings = enumerator.read_all()
        m.readings = readings
        for attr_name, sensor_id in enumerator.map_defaults().items():
            if sensor_id in readings and hasattr(m, attr_name):
                value: float = readings[sensor_id]
                if attr_name not in FLOAT_FIELDS:
                    value = int(value)
                setattr(m, attr_name, value)
                m._populated.add(attr_name)


# =========================================================================
# Metrics Loop ABC — the underlying command stream that ticks every device
# =========================================================================


class MetricsLoop(ABC):
    """Port: the per-process loop that drives device animation + sensor poll.

    Owns two cadences in one mechanism:
      * **Tick** — drive `device.tick()` for every connected device, publish
        `Topic.FRAME` results. Fast (50ms on the polling default).
      * **Poll** — read `trcc.os.metrics`, push to every device's
        `update_metrics()`, publish `Topic.METRICS`. Cadence = the user's
        ``settings.refresh_interval``.

    The mechanism is OS-discretionary. Default `PollingMetricsLoop` is a
    thread-based polling loop used by every OS today. Future per-OS
    overrides can switch to native event sources (WMI events on Windows,
    IOReportSubscribe on macOS) without changing the contract.

    Concrete implementations:
        - PollingMetricsLoop (services/metrics_loop.py)
        - NullMetricsLoop    (services/metrics_loop.py) — for tests + proxy clients
    """

    @abstractmethod
    def start(self) -> None:
        """Begin ticking + polling. Idempotent — safe to call when running."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the loop and release any threads. Idempotent."""

    @abstractmethod
    def wake(self) -> None:
        """Force an immediate poll iteration (used after settings changes)."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """True iff the loop is currently active."""


# =========================================================================
# Platform ABC — OS foundation, drop in an OS so devices can speak to it
# =========================================================================


class Platform(ABC):
    """Port: OS foundation. Shared logic here, each OS overrides what differs.

    One instance per app, DI'd via ControllerBuilder. Devices, services,
    and views call Platform methods — never touch OS-specific code directly.

    Concrete implementations:
        adapters/system/linux_platform.py   — LinuxPlatform
        adapters/system/windows_platform.py — WindowsPlatform
        adapters/system/macos_platform.py   — MacOSPlatform
        adapters/system/bsd_platform.py     — BSDPlatform
    """

    def __init__(self) -> None:
        self._sensor_enum: SensorEnumerator | None = None

    # ── Pythonic surface — the four idioms callers should use ────────────

    @classmethod
    def for_current_os(cls) -> Platform:
        """Construct the Platform implementation for the host OS.

        Dispatch lives in :mod:`trcc.adapters.system.__init__` (where the
        concrete subclasses are importable).  This classmethod is the
        canonical entry point — callers read like English::

            platform = Platform.for_current_os()
            for device in platform: ...
        """
        from trcc.adapters.system import PlatformFactory
        return PlatformFactory.current()

    def __iter__(self) -> Iterator[DetectedDevice]:
        """Enumerate every USB device present right now.

        Re-runs detection on every call — there's no caching, the host
        OS may have hot-plugged or unplugged devices.  Equivalent to
        ``self.detect_devices()`` but reads as ``for d in platform``.
        """
        return iter(self.detect_devices())

    @cached_property
    def sensors(self) -> SensorEnumerator:
        """The OS-specific sensor enumerator, computed once.

        Pythonic replacement for ``self.create_sensor_enumerator()``.
        Both APIs return the same instance — they share the same backing
        cache on ``self._sensor_enum``.
        """
        return self.create_sensor_enumerator()

    # ── Universal (concrete — same on all OSes) ──────────────────────

    def config_dir(self) -> str:
        """User config directory (~/.trcc/)."""
        from trcc.core.paths import USER_CONFIG_DIR
        return USER_CONFIG_DIR

    def data_dir(self) -> str:
        """User data directory (~/.trcc/data/)."""
        from trcc.core.paths import USER_DATA_DIR
        return USER_DATA_DIR

    def user_content_dir(self) -> str:
        """User-created content directory (~/.trcc-user/)."""
        from trcc.core.paths import USER_CONTENT_DIR
        return USER_CONTENT_DIR

    def web_dir(self, width: int, height: int) -> str:
        """Cloud theme web directory for a resolution."""
        from trcc.core.paths import get_web_dir
        return get_web_dir(width, height)

    def web_masks_dir(self, width: int, height: int) -> str:
        """Cloud masks directory for a resolution."""
        from trcc.core.paths import get_web_masks_dir
        return get_web_masks_dir(width, height)

    def user_masks_dir(self, width: int, height: int) -> str:
        """User-created masks directory for a resolution."""
        from trcc.core.paths import get_user_masks_dir
        return get_user_masks_dir(width, height)

    def create_sensor_enumerator(self) -> SensorEnumerator:
        """Return the OS-specific sensor enumerator (cached)."""
        if self._sensor_enum is None:
            self._sensor_enum = self._make_sensor_enumerator()
        return self._sensor_enum

    # ── Metrics — delegated to OS-specific Metrics builder ───────────
    #
    # ``platform.metrics`` is THE method every observer of system
    # readings calls.  PollingMetricsLoop reads it once per tick,
    # broadcasts on Topic.METRICS, every UI thread renders from the
    # same record.
    #
    # Delegation: Platform doesn't compose metrics itself — it owns
    # too many other concerns (USB, screen capture, autostart, DPI).
    # The OS subclass returns its own ``Metrics`` instance whose
    # ``__init__`` calls every read method needed to fill the record.
    # Linux's __init__ is short; Windows's is longer (HWiNFO + LHM +
    # MSAcpi + ...).  The diversity lives in the OS Metrics class.

    @property
    def metrics(self) -> HardwareMetrics:
        """Current hardware metrics for this OS — single source of truth.

        Delegates to the OS-specific :class:`Metrics` builder.  Each
        call constructs a fresh ``Metrics`` whose ``__init__`` runs the
        OS's read methods; the resulting ``record`` is what every
        observer sees on ``Topic.METRICS``.
        """
        return self._make_metrics().record

    @abstractmethod
    def _make_metrics(self) -> Metrics:
        """Construct the OS-specific :class:`Metrics` snapshot.

        Each Platform subclass returns a fresh ``Metrics`` instance —
        whose ``__init__`` calls every read method needed to populate
        ``HardwareMetrics``.  Subprocess caches (lm_sensors, dmidecode,
        smartctl) belong on the Platform subclass and get passed into
        the Metrics constructor to avoid re-running on every tick.
        """

    def install_method(self) -> str:
        """Detect how trcc-linux was installed (pip, pacman, pyinstaller, etc.)."""
        from trcc.core.platform import detect_install_method
        return detect_install_method()

    def screen_capture_params(
        self, x: int, y: int, w: int, h: int,
    ) -> tuple[str, str, list[str]] | None:
        """Return (fmt, inp, region_args) for ffmpeg screen capture, or None."""
        fmt = self._screen_capture_format()
        if not fmt:
            return None
        if fmt == 'gdigrab':
            region = ['-offset_x', str(x), '-offset_y', str(y),
                      '-video_size', f'{w}x{h}'] if (w and h) else []
            return fmt, 'desktop', region
        if fmt == 'avfoundation':
            region = ['-video_size', f'{w}x{h}'] if (w and h) else []
            return fmt, '1:none', region
        # x11grab (Linux/BSD)
        import os
        display = os.environ.get('DISPLAY')
        if not display:
            return None
        inp = f'{display}+{x},{y}' if (w and h) else display
        region = ['-video_size', f'{w}x{h}'] if (w and h) else []
        return fmt, inp, region

    # ── Defaults (concrete — override where needed) ──────────────────

    def _screen_capture_format(self) -> str | None:
        """Screen capture format string. Override per OS."""
        return None

    def configure_dpi(self) -> None:
        """Apply DPI config before QApplication. Windows overrides."""

    def configure_stdout(self) -> None:
        """Reconfigure stdout encoding. Windows overrides for UTF-8."""

    def wire_ipc_raise(self, app: Any, window: Any) -> None:
        """Wire IPC signal to raise window on second instance. POSIX overrides."""

    def subscribe_power(
        self,
        on_suspend: Callable[[], None],
        on_resume: Callable[[], None],
    ) -> None:
        """Subscribe to system suspend / resume notifications.

        OS-specific signal sources:
          - Linux:  org.freedesktop.login1.Manager.PrepareForSleep (D-Bus)
          - Windows: WM_POWERBROADCAST (PBT_APMSUSPEND / PBT_APMRESUMESUSPEND)
          - macOS:  NSWorkspaceWillSleepNotification / DidWakeNotification
          - BSD:    devd power events

        Default implementation is a no-op; concrete platforms override.
        Trcc subscribes once at construction so every UI gets the same
        suspend/resume cleanup without each one wiring its own listener.
        """
        del on_suspend, on_resume  # default: no-op stub, subclasses override

    def build_metrics_loop(self, trcc: Any) -> MetricsLoop:
        """Construct the OS-appropriate `MetricsLoop` for this host.

        Default returns a thread-based `PollingMetricsLoop` — works on
        every OS today. Override per-OS for native event sources
        (e.g. WindowsPlatform → WMI events, MacOSPlatform → IOReport).
        Devices stay thin: they consume `update_metrics()` calls without
        caring which mechanism produced them.

        ``trcc`` is the host whose ``lcd_devices`` / ``led_devices`` /
        ``events`` / ``settings`` / ``system_svc`` the loop reads.
        """
        from ..services.metrics_loop import PollingMetricsLoop
        return PollingMetricsLoop(trcc)

    def resolve_assets_dir(self, pkg_assets_dir: Any) -> Any:
        """Resolve GUI assets directory. Non-Linux copies to user dir."""
        return pkg_assets_dir

    def minimize_on_close(self) -> bool:
        """Minimize to taskbar on close? Windows overrides -> True."""
        return False

    def no_devices_hint(self) -> str | None:
        """Hint when no devices detected. Windows overrides with WinUSB note."""
        return None

    def install_desktop(self) -> int:
        """Install .desktop menu entry. Linux overrides."""
        return 1

    def needs_setup(self) -> bool:
        """Check if critical system integration is missing."""
        return False

    def auto_setup(self) -> None:
        """First-run auto-setup prompt."""

    def check_permissions(self, devices: list[Any]) -> list[str]:
        """Return permission warning messages. Linux overrides."""
        return []

    def get_system_files(self) -> list[str]:
        """System-level file paths installed by this platform."""
        return []

    # ── Abstract (each OS must implement) ────────────────────────────

    @abstractmethod
    def _make_sensor_enumerator(self) -> SensorEnumerator:
        """Create the OS-specific sensor enumerator instance."""

    @abstractmethod
    def create_scsi_transport(self, path: str,
                              vid: int = 0, pid: int = 0,
                              *, usb_address: UsbAddress | None = None) -> Any:
        """Create OS-specific SCSI transport for a device path.

        ``usb_address`` disambiguates dual same-VID/PID coolers (#128). Linux
        and Windows kernel SCSI passthrough bind by path and ignore it;
        macOS / BSD USB BOT use it to pick the right physical device.
        """

    @abstractmethod
    def detect_devices(self) -> list[DetectedDevice]:
        """Discover USB devices currently plugged in (per-OS implementation).

        Each Platform subclass implements this with its native discovery
        (sysfs / WMI / IOKit / sysctl). Callers iterate ``for d in platform``
        or call this directly; either way the return shape is identical
        across the 4 supported OSes — that's what makes the chain work.
        """

    @abstractmethod
    def run_setup(self, auto_yes: bool = False) -> int:
        """Run the full interactive setup wizard. Returns exit code."""

    @abstractmethod
    def install_rules(self) -> int:
        """Install device access rules (udev on Linux, WinUSB guide on Windows)."""

    @abstractmethod
    def check_deps(self) -> list:
        """Check all system dependencies. Returns list of DepResult."""

    @abstractmethod
    def get_pkg_manager(self) -> str | None:
        """Detect the native package manager (dnf, winget, brew, pkg, etc.)."""

    @abstractmethod
    def distro_name(self) -> str:
        """Human-readable OS/distro name."""

    @abstractmethod
    def doctor_config(self) -> DoctorPlatformConfig:
        """Return platform-specific constants for the doctor health check."""

    @abstractmethod
    def report_config(self) -> ReportPlatformConfig:
        """Return platform-specific constants for the diagnostic report."""

    @abstractmethod
    def archive_tool_install_help(self) -> str:
        """Platform-specific instructions for installing 7z/p7zip."""

    @abstractmethod
    def ffmpeg_install_help(self) -> str:
        """Platform-specific instructions for installing ffmpeg."""

    @abstractmethod
    def get_memory_info(self) -> list[dict[str, str]]:
        """Return DRAM slot info (dmidecode on Linux, WMI on Windows, etc.)."""

    @abstractmethod
    def get_disk_info(self) -> list[dict[str, str]]:
        """Return physical disk info (lsblk on Linux, WMI on Windows, etc.)."""

    @abstractmethod
    def acquire_instance_lock(self) -> object | None:
        """Acquire exclusive single-instance lock. Returns handle or None."""

    @abstractmethod
    def raise_existing_instance(self) -> None:
        """Signal the already-running instance to raise its window."""

    @abstractmethod
    def autostart_enable(self) -> None:
        """Enable autostart for the current user."""

    @abstractmethod
    def autostart_disable(self) -> None:
        """Disable autostart for the current user."""

    @abstractmethod
    def autostart_enabled(self) -> bool:
        """Return True if autostart is currently configured."""
