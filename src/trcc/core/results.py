"""Result dataclasses returned by Commands.

Results are the Command API's return values — the universal language UIs
render.  Every Command has one concrete Result type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .models import (
    DeviceInfo,
    HandshakeResult,
    HardwareMetrics,
    LedHandshakeResult,
    ProductInfo,
    SensorReading,
    WebPreviewInfo,
)


@dataclass(frozen=True, slots=True)
class Result:
    """Base result — every Command returns one of these (or a subclass)."""
    ok: bool = True
    message: str = ""


@dataclass(frozen=True, slots=True)
class DiscoverResult(Result):
    products: list[ProductInfo] = field(default_factory=list)
    devices: list[DeviceInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConnectResult(Result):
    key: str = ""
    handshake: HandshakeResult | None = None
    led_handshake: LedHandshakeResult | None = None
    # OS-correct guidance when ``ok`` is False (e.g. "run as administrator").
    # ``list[str]`` (not tuple) so it round-trips the reflective IPC encoder
    # as a JSON array with no decode coercion.
    hints: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConnectionIssuesResult(Result):
    """Current device-connect failures — the queryable model state.

    Each issue is the failed ``ConnectResult`` (key + message + per-OS
    hints), reused as-is (DRY).  Any UI dispatches ``DeviceConnectionIssues``
    to pull what failed and why — the bus-pure way to learn state that may
    have happened before the UI subscribed.
    """
    issues: list[ConnectResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DisconnectResult(Result):
    key: str = ""


@dataclass(frozen=True, slots=True)
class SendResult(Result):
    key: str = ""
    bytes_sent: int = 0


@dataclass(frozen=True, slots=True)
class RenderResult(Result):
    """Built + sent one frame through the render pipeline."""
    key: str = ""
    bytes_sent: int = 0
    theme_name: str = ""


@dataclass(frozen=True, slots=True)
class SensorInfoEntry:
    """Descriptor-only view of a sensor — no value, just identity."""
    sensor_id: str
    category: str
    unit: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class SensorsListResult(Result):
    """Result of :class:`ListSensors` — every sensor the enumerator knows.

    Distinct from :class:`SensorsResult` which carries fresh values.
    Use this for UIs that need to populate a sensor-picker dropdown
    without paying the polling cost on every refresh.
    """
    sensors: list[SensorInfoEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EnsureDataDownloadResult(Result):
    """Forced theme/web/mask archive install for a resolution."""
    width: int = 0
    height: int = 0
    themes_ok: bool = False
    web_ok: bool = False
    masks_ok: bool = False


@dataclass(frozen=True, slots=True)
class ExportConfigResult(Result):
    """Per-device JSON snapshot written to disk."""
    key: str = ""
    output_path: str = ""


@dataclass(frozen=True, slots=True)
class ImportConfigResult(Result):
    """Per-device JSON snapshot restored from disk."""
    key: str = ""
    input_path: str = ""


@dataclass(frozen=True, slots=True)
class RenderDcResult(Result):
    """Stand-alone DC render output (overlay preview, no device).

    ``output_path`` is the on-disk PNG when the caller asked for a
    file; empty string when the result came back in-memory.
    ``element_count`` reports how many elements the DC parser found.
    """
    output_path: str = ""
    width: int = 0
    height: int = 0
    element_count: int = 0


@dataclass(frozen=True, slots=True)
class ThemeResult(Result):
    key: str = ""
    theme_name: str = ""
    theme_path: str = ""
    # SaveTheme sets this when the save was refused only because a theme
    # of the same name already exists — the UI uses it to offer a
    # one-click overwrite confirmation rather than making the user
    # invent a new name.
    target_exists: bool = False


@dataclass(frozen=True, slots=True)
class OrientationResult(Result):
    key: str = ""
    degrees: int = 0


@dataclass(frozen=True, slots=True)
class BrightnessResult(Result):
    key: str = ""
    percent: int = 100


@dataclass(frozen=True, slots=True)
class FitModeResult(Result):
    key: str = ""
    mode: str = "width"


@dataclass(frozen=True, slots=True)
class OverlayResult(Result):
    key: str = ""
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class SplitModeResult(Result):
    key: str = ""
    mode: int = 0


@dataclass(frozen=True, slots=True)
class MaskApplyResult(Result):
    key: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class MaskPositionResult(Result):
    key: str = ""
    position: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class MaskVisibilityResult(Result):
    key: str = ""
    visible: bool = True


@dataclass(frozen=True, slots=True)
class ThemeExportResult(Result):
    theme_name: str = ""
    archive_path: str = ""


@dataclass(frozen=True, slots=True)
class ThemeImportResult(Result):
    theme_name: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class ThemeListEntry:
    """One row in a ThemesListResult — name + resolution + path + preview + origin.

    ``preview`` is the absolute path to the tile image (``Theme.png`` →
    ``00.png`` → any ``*.png``, else ``""``) so every UI builds the same
    browser tile from the Command result instead of re-walking the dir.

    ``origin`` is the theme's data root — ``"user"`` (saved under
    ``user_data_dir``) or ``"shipped"`` (pkg / cloud-downloaded).  Location-
    derived, the canonical replacement for name-based heuristics: it drives the
    user/default filter and any "my themes" badge, identically in every UI.
    """
    name: str
    resolution: tuple[int, int]
    path: str
    preview: str = ""
    origin: Literal["user", "shipped"] = "shipped"


@dataclass(frozen=True, slots=True)
class ThemesListResult(Result):
    """All themes discovered under a directory."""
    directory: str = ""
    themes: list[ThemeListEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WebThemesListResult(Result):
    """Downloaded cloud-theme previews for a resolution."""
    width: int = 0
    height: int = 0
    entries: list[WebPreviewInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VideoResult(Result):
    key: str = ""
    path: str = ""
    frame_count: int = 0


@dataclass(frozen=True, slots=True)
class ScreencastResult(Result):
    """Result of ``StartScreencast`` / ``StopScreencast`` — lifecycle of
    the per-device screen-capture session.

    ``active`` reflects the post-Command state (True after Start, False
    after Stop) so callers can render a status line without re-querying.
    ``x, y, w, h`` echo the requested region; ``audio`` echoes the
    visualiser flag — both useful for daemon/API clients reading the
    response over the wire.
    """
    key: str = ""
    active: bool = False
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    audio: bool = False


@dataclass(frozen=True, slots=True)
class BootAnimationResult(Result):
    """One compressed boot-animation upload to a SCSI LCD."""
    key: str = ""
    frames_uploaded: int = 0
    frames_total: int = 0


@dataclass(frozen=True, slots=True)
class LedColorsResult(Result):
    key: str = ""
    colors: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SensorsResult(Result):
    readings: list[SensorReading] = field(default_factory=list)
    # Typed snapshot, personalized to user prefs — the GUI reads this
    # directly (no fragile sensor_id→attr remap).  ALWAYS a real object:
    # the OS metrics dispatcher always produces a snapshot (empty default
    # only for the construct-without-args/test path), so consumers never
    # branch on absence.
    metrics: HardwareMetrics = field(default_factory=HardwareMetrics)


@dataclass(frozen=True, slots=True)
class SetupResult(Result):
    exit_code: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AutostartResult(Result):
    """Current autostart state + path for diagnostic UIs."""
    enabled: bool = False
    path: str = ""


@dataclass(frozen=True, slots=True)
class TempUnitResult(Result):
    unit: str = "C"


@dataclass(frozen=True, slots=True)
class LanguageResult(Result):
    language: str = "en"


@dataclass(frozen=True, slots=True)
class GpuDeviceResult(Result):
    gpu_key: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshIntervalResult(Result):
    seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class ActiveDeviceResult(Result):
    active_device: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformInfoResult(Result):
    """Snapshot of identity + path + permission info for diagnostic UIs."""
    distro_name: str = ""
    install_method: str = ""
    config_dir: str = ""
    data_dir: str = ""
    user_content_dir: str = ""
    log_file: str = ""
    permission_warnings: list[str] = field(default_factory=list)


# =========================================================================
# Listings + snapshots (read-only Commands)
# =========================================================================


@dataclass(frozen=True, slots=True)
class LedStyleEntry:
    """One row in a LedStylesListResult."""
    style: str           # LedStyle.value
    model_name: str
    pm_byte: int
    style_sub: int = 0
    segment_count: int = 0   # SegmentDisplay.mask_size (0 = non-segment style)
    zone_count: int = 0      # independent zones (len(zone_led_map), 0 = single)


@dataclass(frozen=True, slots=True)
class LedStylesListResult(Result):
    styles: list[LedStyleEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LedModesListResult(Result):
    modes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GpuEntry:
    """One row in a GpusListResult."""
    key: str
    name: str
    is_discrete: bool


@dataclass(frozen=True, slots=True)
class GpusListResult(Result):
    gpus: list[GpuEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FanEntry:
    """One row in a FansListResult — a discovered fan with its live reading.

    ``rpm`` / ``percent`` let a diagnostic UI show which fan is actually
    spinning (#145/#207).
    """
    key: str
    name: str
    rpm: int | None = None
    percent: float | None = None


@dataclass(frozen=True, slots=True)
class FansListResult(Result):
    fans: list[FanEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LcdSnapshotResult(Result):
    """Per-device LCD state snapshot — what's currently persisted."""
    key: str = ""
    orientation: int = 0
    brightness: int = 100
    current_theme: str | None = None
    overlay_enabled: bool = True
    mask_path: str | None = None
    mask_visible: bool = True
    mask_position: tuple[int, int] | None = None
    fit_mode: str = "fit"
    split_mode: int = 0
    time_format: str = "24h"
    date_format: str = ""
    temp_unit: str = "C"


@dataclass(frozen=True, slots=True)
class LedSnapshotResult(Result):
    """Per-device LED state snapshot — what's currently persisted."""
    key: str = ""
    mode: str = "STATIC"
    color: tuple[int, int, int] = (0, 0, 0)
    brightness: int = 100
    global_on: bool = True
    test_mode: bool = False
    temp_source: str = "cpu"
    load_source: str = "cpu"
    zone_sync: bool = False
    zone_sync_interval_ticks: int = 13
    selected_zone: int = 0
    zone_count: int = 0
    segment_count: int = 0


@dataclass(frozen=True, slots=True)
class ControlCenterSnapshotResult(Result):
    """AppSettings snapshot."""
    language: str = "en"
    temp_unit: str = "C"
    active_device: str | None = None
    active_gpu: str | None = None
    refresh_interval_s: float = 2.0
    hdd_enabled: bool = False


@dataclass(frozen=True, slots=True)
class HddEnabledResult(Result):
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class BackgroundModeResult(Result):
    key: str = ""
    mode: str = "theme"


@dataclass(frozen=True, slots=True)
class BackgroundResult(Result):
    """Result of ``SetBackground`` — file applied as a static / video bg.

    Distinct from :class:`BackgroundModeResult` (theme bg vs override
    mode toggle) and :class:`VideoResult` (PlayVideo / StopVideo
    lifecycle).  Carries the resolved path + the inferred kind so a
    caller can branch on "did we just start a video".
    """
    key: str = ""
    path: str = ""
    kind: str = ""   # "image" | "video"


@dataclass(frozen=True, slots=True)
class OverlayBackgroundResult(Result):
    key: str = ""
    color: tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True, slots=True)
class ClockFormatResult(Result):
    key: str = ""
    is_24h: bool = True


@dataclass(frozen=True, slots=True)
class TimeFormatResult(Result):
    """Result of ``SetTimeFormat`` — per-device LCD-overlay clock format.

    Distinct from :class:`ClockFormatResult` (which is for LED-segment
    LC2-style devices).  ``fmt`` is the literal "12h" / "24h".
    """
    key: str = ""
    fmt: str = "24h"


@dataclass(frozen=True, slots=True)
class DateFormatResult(Result):
    """Result of ``SetDateFormat`` — per-device LCD-overlay date pattern."""
    key: str = ""
    fmt: str = "yyyy/MM/dd"


@dataclass(frozen=True, slots=True)
class WeekStartResult(Result):
    key: str = ""
    sunday_first: bool = False


@dataclass(frozen=True, slots=True)
class MemoryRatioResult(Result):
    key: str = ""
    ratio: int = 2          # DDR multiplier (1 / 2 / 4)


@dataclass(frozen=True, slots=True)
class DiskIndexResult(Result):
    key: str = ""
    index: int = 0


@dataclass(frozen=True, slots=True)
class PauseVideoResult(Result):
    key: str = ""
    paused: bool = False


@dataclass(frozen=True, slots=True)
class SeekVideoResult(Result):
    key: str = ""
    cursor: int = 0
    frame_count: int = 0


@dataclass(frozen=True, slots=True)
class LoopVideoResult(Result):
    key: str = ""
    loop: bool = True


@dataclass(frozen=True, slots=True)
class DeleteThemeResult(Result):
    theme_name: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class MaskUploadResult(Result):
    key: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One mask row: the mask dir (``path``) + its preview image (``preview``).

    ``preview`` is the ``Theme.png`` tile (falling back to the ``01.png``
    overlay) resolved by ``ThemeService.discover_masks`` — carried here so every
    UI can render a thumbnail from the Command result, instead of re-deriving it
    from the path (the gap that forced gui to call the service directly)."""
    name: str
    path: str
    preview: str = ""


@dataclass(frozen=True, slots=True)
class MasksListResult(Result):
    directory: str = ""
    masks: list[FileEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FontsListResult(Result):
    fonts: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DiskEntry:
    """One row in a DisksListResult."""
    index: int
    device: str
    mountpoint: str


@dataclass(frozen=True, slots=True)
class DisksListResult(Result):
    disks: list[DiskEntry] = field(default_factory=list)


# Overlay element CRUD
@dataclass(frozen=True, slots=True)
class OverlayElementEntry:
    """Flat view of one OverlayElement (id + type + fields)."""
    id: str
    type: str
    x: int = 0
    y: int = 0
    color: str = "#ffffff"
    size: int = 16
    bold: bool = False
    italic: bool = False
    text: str = ""
    metric: str = ""
    format: str = "{value}"
    show_unit: bool = True
    source: str = "time"


@dataclass(frozen=True, slots=True)
class OverlayElementResult(Result):
    """Single-element response (add / update / flash)."""
    key: str = ""
    element: OverlayElementEntry | None = None


@dataclass(frozen=True, slots=True)
class OverlayElementDeleteResult(Result):
    key: str = ""
    element_id: str = ""


@dataclass(frozen=True, slots=True)
class OverlayConfigResult(Result):
    """Bulk SetOverlayConfig response — full element list."""
    key: str = ""
    elements: list[OverlayElementEntry] = field(default_factory=list)


# Cloud themes
@dataclass(frozen=True, slots=True)
class CloudCategoryEntry:
    prefix: str
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class CloudThemeEntryResult:
    id: str
    category: str
    category_name: str
    # On-disk preview PNG (``web/{w}{h}/<id>.png``), resolved by ListCloudThemes
    # when a resolution is given; "" when unresolved or not yet extracted.  Lets
    # any UI thumbnail the catalog without re-deriving the path.
    preview: str = ""


@dataclass(frozen=True, slots=True)
class CloudThemesListResult(Result):
    category: str = "all"
    categories: list[CloudCategoryEntry] = field(default_factory=list)
    themes: list[CloudThemeEntryResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CloudThemeLoadResult(Result):
    key: str = ""
    theme_id: str = ""
    theme_path: str = ""


@dataclass(frozen=True, slots=True)
class LanguageEntry:
    """One row in a LanguagesListResult — code + native name + translated count."""
    code: str
    name: str
    translated_keys: int


@dataclass(frozen=True, slots=True)
class LanguagesListResult(Result):
    """Every language the i18n table can resolve."""
    languages: list[LanguageEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ThemeDcExportResult(Result):
    """Wrote a theme out as legacy ``config1.dc``."""
    theme_name: str = ""
    output_path: str = ""


# Diagnostics
@dataclass(frozen=True, slots=True)
class HealthCheckEntry:
    name: str
    severity: str           # OK / WARN / FAIL
    message: str
    fix_hint: str = ""


@dataclass(frozen=True, slots=True)
class HealthReportResult(Result):
    checks: list[HealthCheckEntry] = field(default_factory=list)
    fail_count: int = 0
    warn_count: int = 0
    worst_severity: str = "OK"


@dataclass(frozen=True, slots=True)
class DoctorResultPayload(Result):
    checks: list[HealthCheckEntry] = field(default_factory=list)
    fail_count: int = 0
    warn_count: int = 0
    exit_code: int = 0
    rendered: str = ""


@dataclass(frozen=True, slots=True)
class DebugReportPayload(Result):
    output_path: str = ""
    rendered_text: str = ""


# Update check + upgrade
@dataclass(frozen=True, slots=True)
class UpdateCheckResult(Result):
    local_version: str = ""
    latest_version: str = ""
    latest_tag: str = ""
    release_url: str = ""
    update_available: bool = False


@dataclass(frozen=True, slots=True)
class UpgradeResult(Result):
    """Outcome of running the OS package manager upgrade subprocess."""
    package_manager: str = ""
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


# Slideshow + keepalive
@dataclass(frozen=True, slots=True)
class SlideshowResult(Result):
    key: str = ""
    enabled: bool = False
    interval_s: float = 60.0
    themes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class KeepaliveResult(Result):
    """Outcome of one KeepAliveLoop tick (or batch)."""
    key: str = ""
    frames_resent: int = 0
    bytes_resent: int = 0


# First-run
@dataclass(frozen=True, slots=True)
class FirstRunStatusResult(Result):
    is_first_run: bool = True
    marker_path: str = ""


@dataclass(frozen=True, slots=True)
class QuickstartStepEntry:
    name: str
    status: str   # ok / warn / fail / skipped
    message: str
    next_step_hint: str = ""


@dataclass(frozen=True, slots=True)
class QuickstartResult(Result):
    """Stepwise trace of the guided first-session flow."""
    steps: list[QuickstartStepEntry] = field(default_factory=list)
    completed_ok: bool = False
    device_key: str = ""
