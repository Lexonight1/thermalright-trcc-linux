"""Result + Info dataclasses for the universal TRCC command layer.

Every Trcc command returns one of these. All frozen, JSON-serializable via
`dataclasses.asdict`. UI adapters render the result their own way:
GUI paints widgets, CLI prints, API serializes to JSON.

Inheritance rule: `OpResult` is the base for every success/failure command.
Specialized results (`FrameResult`, `ThemeResult`, `LEDResult`,
`DiscoveryResult`, `UpdateResult`) inherit from it to gain `.exit_code`
and `.format()` — so every UI adapter renders outcomes the same way.

Reuses existing domain types from `core.models` — `ThemeInfo`, `MaskInfo`,
`SensorInfo`, `OverlayElement`, `DeviceInfo` — instead of redefining.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .models.device import DeviceInfo


# =============================================================================
# Frame — framework-neutral image container.
# Qt renderer produces this; GUI widgets convert to QPixmap at the edge;
# API base64-encodes pixels/encoded; CLI writes to disk via a helper.
# =============================================================================

@dataclass(frozen=True, slots=True)
class Frame:
    """Framework-neutral image. Until Phase 8 ships QtRenderer→bytes
    conversion, `native` holds the raw QImage passthrough for GUI; bytes
    fields are populated lazily when API/CLI need serialization.
    """
    width: int = 0
    height: int = 0
    pixels: bytes = b''                # raw RGBA (populated in phase 8)
    encoded: bytes | None = None       # RGB565 pre-encoded for device
    native: Any = None                 # passthrough for the current Qt path


# =============================================================================
# OpResult — base for every command outcome.
# Subclasses inherit .success, .message, .error, .exit_code, .format().
# =============================================================================

@dataclass(frozen=True, slots=True)
class OpResult:
    """Generic success/failure outcome with a message.

    Base class for every command result. Subclasses add their own payload
    fields (frame, colors, overlay config, etc.).
    """
    success: bool
    message: str = ''
    error: str | None = None

    @property
    def exit_code(self) -> int:
        """0 on success, 1 on failure. Used directly by CLI subcommands."""
        return 0 if self.success else 1

    def format(self) -> str:
        """Framework-neutral one-line description of the outcome.

        CLI prints it via `typer.echo`, GUI shows it in the status label,
        API returns it alongside `asdict(result)`.
        """
        if self.success:
            return self.message or 'OK'
        if self.error:
            return f'Error: {self.error}'
        return 'Error: failed'


# =============================================================================
# Command result subclasses — inherit OpResult, add payload fields.
# =============================================================================

@dataclass(frozen=True, slots=True)
class FrameResult(OpResult):
    """Command that renders a frame (brightness, rotation, overlay edit, …)."""
    frame: Frame | None = None


@dataclass(frozen=True, slots=True)
class ThemeResult(OpResult):
    """Theme load — may carry overlay config + animation metadata."""
    frame: Frame | None = None
    is_animated: bool = False
    interval_ms: int = 0
    overlay_config: dict | None = None
    overlay_enabled: bool = False


@dataclass(frozen=True, slots=True)
class LEDResult(OpResult):
    """LED command result — includes current display colors for preview."""
    display_colors: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DiscoveryResult(OpResult):
    """Device enumeration result — LCDs and LEDs detected."""
    lcd_devices: list[DeviceInfo] = field(default_factory=list)
    led_devices: list[DeviceInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UpdateResult(OpResult):
    """Update check result — latest version + per-package-manager download URLs."""
    current_version: str = ''
    latest_version: str | None = None
    update_available: bool = False
    assets: dict[str, str] = field(default_factory=dict)   # {pkg_mgr: url}


# =============================================================================
# Streaming result — one tick of a video playback, no success/error.
# =============================================================================

@dataclass(frozen=True, slots=True)
class VideoTickResult:
    """One frame from the video playback tick."""
    frame: Frame | None
    frame_index: int
    progress_percent: float
    current_time: str
    total_time: str


# =============================================================================
# Snapshots — read-only state bundles.
# GUI uses on first paint / reactivation; CLI `trcc status`; API `GET /status`.
# =============================================================================

@dataclass(frozen=True, slots=True)
class LCDSnapshot:
    connected: bool
    playing: bool
    auto_send: bool
    overlay_enabled: bool
    brightness: int
    rotation: int
    split_mode: int
    fit_mode: str
    resolution: tuple[int, int]
    current_theme: str | None


@dataclass(frozen=True, slots=True)
class LEDSnapshot:
    connected: bool
    style_id: int
    mode: int
    color: tuple[int, int, int]
    brightness: int
    global_on: bool
    zones: list[dict]
    zone_sync: bool
    zone_sync_interval: int
    selected_zone: int
    segment_on: list[bool]
    clock_24h: bool
    week_sunday: bool
    memory_ratio: int
    disk_index: int
    test_mode: bool


@dataclass(frozen=True, slots=True)
class AppSnapshot:
    version: str
    autostart: bool
    temp_unit: str                     # 'C' | 'F'
    language: str
    hdd_enabled: bool
    refresh_interval: int
    gpu_device: str | None
    gpu_list: list[tuple[str, str]]
    install_method: str                # 'pipx' | 'pip' | 'pacman' | 'dnf' | 'apt'
    distro: str


# =============================================================================
# Info dataclasses — list-result element types not already in core.models.
# =============================================================================

@dataclass(frozen=True, slots=True)
class BackgroundInfo:
    """User-uploaded background image available on the LCD device."""
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class LEDStyleInfo:
    """Describes a supported LED product style — segments, zones, modes."""
    style_id: int
    name: str
    segment_count: int
    zone_count: int
    supported_modes: list[str]


@dataclass(frozen=True, slots=True)
class DiskInfo:
    """Physical disk available for the LF11 disk-select dropdown."""
    index: int
    name: str
    path: str
