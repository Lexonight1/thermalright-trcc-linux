"""Pydantic request/response schemas for the REST API.

Pydantic models live here (in the UI adapter) — they're HTTP concerns,
not domain concerns.  Core ports stay framework-blind.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, RootModel

from ...core.models import (
    MAX_REFRESH_INTERVAL_S,
    MIN_REFRESH_INTERVAL_S,
    OVERLAY_DEFAULT_CLOCK_SOURCE,
    OVERLAY_DEFAULT_COLOR,
    OVERLAY_DEFAULT_FORMAT,
    OVERLAY_DEFAULT_SIZE,
)
from ...core.results import ConnectionIssuesResult, ConnectResult

# =========================================================================
# Wire views — a Result serialized verbatim, with device bytes as hex
# =========================================================================

T = TypeVar("T")


class WireView(RootModel[T], Generic[T]):
    """A Command Result on the wire, with raw device bytes as lowercase hex.

    ``bytes`` has no JSON form, so every text boundary must choose an
    encoding: the IPC socket picks base64 (lossless round-trip), and the
    diagnostics print hex.  HTTP follows the diagnostics — a handshake read
    off ``/devices/{key}/connect`` then matches, character for character,
    the one in a ``trcc report`` pasted into an issue.

    The choice is declared HERE, in the adapter, rather than on the Result:
    ``core`` stays framework-blind (no pydantic import, no pydantic naming),
    and pydantic propagates this config into the nested dataclasses it
    serializes.  Subclass per Result — that binds ``T`` and gives the
    OpenAPI component a name.
    """
    model_config = ConfigDict(ser_json_bytes="hex")


class ConnectView(WireView[ConnectResult]):
    """``POST /devices/{key}/connect`` — the whole ConnectResult.

    Carries the handshake (including ``raw_response`` as hex), the LED
    handshake, and the per-OS ``hints`` shown when a connect fails.
    """


class ConnectionIssuesView(WireView[ConnectionIssuesResult]):
    """``GET /devices/issues`` — every device that failed to connect, with
    the same per-OS hints the GUIs show."""


# =========================================================================
# Response shapes
# =========================================================================


class ResultBase(BaseModel):
    ok: bool
    message: str = ""


class ProductSchema(BaseModel):
    """HTTP view of ``ProductInfo`` — a projection, NOT a mirror.

    Kept deliberately: ``ProductInfo.key`` is a derived ``@property``, and
    serializing the dataclass directly emits fields only, so the identifier
    every client keys on would silently vanish.  This view materializes it.
    """
    key: str
    vid: int
    pid: int
    vendor: str
    product: str
    wire: str
    kind: str
    native_resolution: tuple[int, int]
    orientations: tuple[int, ...]


class DiscoverResponse(ResultBase):
    products: list[ProductSchema] = []


class ThemeResponse(ResultBase):
    """Deliberately narrower than ``ThemeResult``: ``theme_path`` is a
    server-side absolute path and is not disclosed over HTTP."""
    key: str = ""
    theme_name: str = ""
    # SaveTheme sets this when the save was refused ONLY because a theme of
    # that name exists — lets a REST client offer the same one-click
    # overwrite the GUIs offer, instead of guessing from the message text.
    target_exists: bool = False


# ── Control-center settings ──────────────────────────────────────────


class ImportConfigResponse(ResultBase):
    """Deliberately narrower than ``ImportConfigResult``: ``input_path`` is a
    server-side absolute path and is not disclosed over HTTP."""
    key: str = ""


# =========================================================================
# Request bodies
# =========================================================================


class OrientationRequest(BaseModel):
    degrees: int = Field(..., ge=0, le=270)


class BrightnessRequest(BaseModel):
    percent: int = Field(..., ge=0, le=100)


class ThemeRequest(BaseModel):
    path: str


class LedColorsRequest(BaseModel):
    colors: list[tuple[int, int, int]] = Field(..., min_length=1)
    global_on: bool = True
    brightness: int = Field(100, ge=0, le=100)


class LedRenderRequest(BaseModel):
    """Sensor-driven render.  ``color`` is an optional STATIC override."""
    color: tuple[int, int, int] | None = None
    phase: int = Field(0, ge=0)


class LedModeRequest(BaseModel):
    """Set the global animation mode for an LED device."""
    mode: str = Field(
        ..., pattern="^(static|breathing|colorful|rainbow|temp_linked|load_linked)$",
        description="Case-insensitive mode name.",
    )


class LedColorRequest(BaseModel):
    color: tuple[int, int, int] = Field(...)


class LedBrightnessRequest(BaseModel):
    percent: int = Field(..., ge=0, le=100)


class LedTestModeRequest(BaseModel):
    enabled: bool


class LedSourceRequest(BaseModel):
    """Source body for ``temp-source`` / ``load-source`` endpoints."""
    source: str = Field(..., pattern="^(cpu|gpu)$")


class LedToggleRequest(BaseModel):
    """Toggle body — ``on`` is the desired state; ``zone`` targets one zone."""
    on: bool
    zone: int | None = Field(None, ge=0)


class LedZoneColorRequest(BaseModel):
    zone: int = Field(..., ge=0)
    color: tuple[int, int, int]


class LedZoneModeRequest(BaseModel):
    """Per-zone variant of :class:`LedModeRequest`."""
    zone: int = Field(..., ge=0)
    mode: str = Field(
        ..., pattern="^(static|breathing|colorful|rainbow|temp_linked|load_linked)$",
        description="Case-insensitive mode name.",
    )


class LedZoneBrightnessRequest(BaseModel):
    """Per-zone variant of :class:`LedBrightnessRequest`."""
    zone: int = Field(..., ge=0)
    percent: int = Field(..., ge=0, le=100)


class LedZoneSyncRequest(BaseModel):
    enabled: bool
    interval_ticks: int | None = Field(None, ge=1)


class LedSelectZoneRequest(BaseModel):
    zone: int = Field(..., ge=0)


class LedToggleSegmentRequest(BaseModel):
    index: int = Field(..., ge=0)
    on: bool


# Tier-2 request/response shapes
class ClockFormatRequest(BaseModel):
    is_24h: bool


class WeekStartRequest(BaseModel):
    sunday_first: bool


class MemoryRatioRequest(BaseModel):
    ratio: int          # DDR multiplier: 1, 2, or 4


class DiskIndexRequest(BaseModel):
    index: int = Field(..., ge=0)


class HddEnabledRequest(BaseModel):
    enabled: bool


class BackgroundFileRequest(BaseModel):
    """A file to use as the persistent background override."""
    path: str


class SendImageRequest(BaseModel):
    """An image to show once, without staging it as a theme."""
    path: str


class RenderDcRequest(BaseModel):
    """Render a .dc standalone — no device, so the size is explicit."""
    dc_path: str
    output_path: str
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)


class ExportOverlayRequest(BaseModel):
    """Copy a theme's overlay config file out."""
    key: str
    theme_name: str
    output_path: str


class ZoneSyncZonesRequest(BaseModel):
    """Which zones take part in the zone-sync carousel."""
    zones: list[bool]


class SlideshowDriveRequest(BaseModel):
    """Start or stop the slideshow DRIVER — distinct from enabling the
    slideshow, which only persists the setting."""
    drive: bool = True


class BackgroundModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(theme|color|transparent)$")


class OverlayBackgroundRequest(BaseModel):
    color: tuple[int, int, int]


# Tier 3 schemas
class PauseVideoRequest(BaseModel):
    paused: bool


class SeekVideoRequest(BaseModel):
    frame: int = Field(..., ge=0)


class LoopVideoRequest(BaseModel):
    loop: bool


class MaskUploadRequest(BaseModel):
    """Server-side mask path — same shape as theme import requests."""
    source: str


# Overlay element CRUD
class OverlayElementSchema(BaseModel):
    """One element inside a ``SetOverlayConfig`` body — request-side only.

    Responses carry the domain ``OverlayElementEntry`` verbatim; this is the
    validated *input* shape.
    """
    id: str
    type: str
    x: int = 0
    y: int = 0
    color: str = OVERLAY_DEFAULT_COLOR
    size: int = OVERLAY_DEFAULT_SIZE
    bold: bool = False
    italic: bool = False
    text: str = ""
    metric: str = ""
    format: str = OVERLAY_DEFAULT_FORMAT
    show_unit: bool = True
    source: str = OVERLAY_DEFAULT_CLOCK_SOURCE


class OverlayElementAddRequest(BaseModel):
    type: str = Field(..., pattern="^(text|metric|clock)$")
    x: int = 0
    y: int = 0
    color: str = OVERLAY_DEFAULT_COLOR
    size: int = Field(OVERLAY_DEFAULT_SIZE, ge=1)
    bold: bool = False
    italic: bool = False
    text: str = ""
    metric: str = ""
    format: str = OVERLAY_DEFAULT_FORMAT
    show_unit: bool = True
    source: str = OVERLAY_DEFAULT_CLOCK_SOURCE
    element_id: str = ""


class OverlayElementUpdateRequest(BaseModel):
    x: int | None = None
    y: int | None = None
    color: str | None = None
    size: int | None = Field(None, ge=1)
    bold: bool | None = None
    italic: bool | None = None
    text: str | None = None
    metric: str | None = None
    format: str | None = None
    show_unit: bool | None = None
    source: str | None = None


class OverlayFlashRequest(BaseModel):
    duration_ms: int = Field(1500, ge=100, le=10000)


class OverlayConfigRequest(BaseModel):
    elements: list[OverlayElementSchema] = []


# Cloud themes
class WebThemeSchema(BaseModel):
    """One cloud-theme preview on disk for a resolution.

    ``preview_url`` / ``download_url`` resolve against the API itself —
    the preview is served by the ``/static/web`` mount.
    """
    id: str
    category: str
    preview_url: str
    has_video: bool
    download_url: str


class CloudThemeLoadRequest(BaseModel):
    theme_id: str


class ThemeDcExportRequest(BaseModel):
    """Server-side path to write the legacy DC file to.

    ``key`` is required — the device's resolution scopes the source
    lookup and the device's user-overlay elements are layered into the
    exported DC.
    """
    key: str = Field(..., min_length=1)
    output_path: str


# Diagnostics
class DebugReportRequest(BaseModel):
    output_path: str | None = None
    log_tail_lines: int = Field(1000, ge=0, le=100_000)


# Update + upgrade
class UpgradeRequest(BaseModel):
    dry_run: bool = False


# Slideshow + keepalive
class SlideshowToggleRequest(BaseModel):
    enabled: bool


class SlideshowConfigureRequest(BaseModel):
    themes: list[str] | None = None
    interval_s: float | None = Field(None, ge=1.0)


class KeepaliveRequest(BaseModel):
    """Keepalive-loop parameters.

    * ``count`` — ``0`` means run until interrupted (legacy parity);
      ``>=1`` runs that many iterations.
    * ``interval_s`` — fast-resend cadence.  Default 0.150 s is below
      the 2-3 s firmware-revert threshold on Bulk/LY devices.
    * ``metric_interval_s`` — how often to re-render the overlay so
      sensor values stay current.  Set to 0 to disable refresh.
    """
    count: int = Field(0, ge=0)
    interval_s: float = Field(0.150, ge=0.05)
    metric_interval_s: float = Field(1.0, ge=0.0)


# First-run + legacy migration
class ColorRequest(BaseModel):
    """Solid-color frame request — three 0-255 channels."""
    r: int = Field(..., ge=0, le=255)
    g: int = Field(..., ge=0, le=255)
    b: int = Field(..., ge=0, le=255)


class FitModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(width|height|stretch)$")


class OverlayRequest(BaseModel):
    enabled: bool


class SplitModeRequest(BaseModel):
    mode: int = Field(..., ge=0, le=3)


class MaskApplyRequest(BaseModel):
    """Path to a mask image file (resolved server-side for security)."""
    path: str = Field(..., min_length=1)


class MaskPositionRequest(BaseModel):
    """Either both x,y set (offsets ≥ 0) or both None (clear)."""
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)


class MaskVisibilityRequest(BaseModel):
    visible: bool


class ThemeSaveRequest(BaseModel):
    """Save the device's active theme under a new name (basename only)."""
    key: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


class ThemeExportRequest(BaseModel):
    """Export an existing theme by name to a server-side archive path.

    ``key`` scopes the lookup to the device's resolution dir.
    """
    key: str = Field(..., min_length=1)
    theme_name: str = Field(..., min_length=1)
    archive_path: str = Field(..., min_length=1)


class ThemeImportRequest(BaseModel):
    """Import a theme from a server-side archive path.

    ``key`` scopes the target to the device's resolution dir.
    ``name`` defaults to the archive filename stem when blank.
    """
    key: str = Field(..., min_length=1)
    archive_path: str = Field(..., min_length=1)
    name: str = ""


class DeleteThemeRequest(BaseModel):
    """Delete a theme directory by absolute path.

    Path-based to match the Command shape — the caller already has the
    resolved path from a prior list call.  Server confines deletion to
    ``user_content_dir`` regardless.
    """
    path: str = Field(..., min_length=1)


class CreateThemeResponse(ResultBase):
    """Result of a multipart-upload create-theme request.

    ``animated`` is True when the uploaded background was a supported
    video format (mp4/mov/webm/mkv/avi/zt) and a video playback was
    started; False for a static image.  ``resolution`` echoes the
    device's pixel size for the caller.
    """
    key: str = ""
    animated: bool = False
    resolution: str = ""


class AutostartRequest(BaseModel):
    """``POST /system/autostart`` body — toggle the OS autostart entry."""
    enabled: bool


class AppStatusEntry(BaseModel):
    """One device in :class:`AppStatusResponse`.

    Small shape — full per-device state lives under the device-specific
    snapshot routes (``/devices/{key}/display/snapshot`` for LCD,
    ``/devices/{key}/led/snapshot`` for LED).
    """
    key: str = ""
    product: str = ""
    connected: bool = False


class AppStatusResponse(ResultBase):
    """Unified ``GET /system/status`` snapshot: app prefs + device counts.

    Composed from in-process state — no Command dispatched.  Clients
    that want full per-device detail follow up with the device-specific
    snapshot routes.  Legacy parity with ``GET /app/status``.
    """
    language: str = ""
    temp_unit: str = "C"
    hdd_enabled: bool = False
    refresh_interval_s: float = 2.0
    active_gpu: str | None = None
    autostart_enabled: bool = False
    lcd_devices: list[AppStatusEntry] = []
    led_devices: list[AppStatusEntry] = []


class DaemonKillResponse(ResultBase):
    """Result of a ``POST /trcc/kill`` request."""


class DaemonStatusResponse(ResultBase):
    """Snapshot of the running daemon.

    ``running=False`` indicates no daemon is bound on the singleton
    socket; in that state the other fields are zero / undefined.
    """
    running: bool = False
    pid: int = 0
    uptime_seconds: int = 0
    lcd_count: int = 0
    led_count: int = 0


class ScreencastStartRequest(BaseModel):
    """Body for ``POST /devices/{key}/display/screencast/start``."""
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)
    audio: bool = False


class MediaPlayerRequest(BaseModel):
    """Body for ``POST /devices/{key}/display/media-player``."""
    uri: str = Field(
        "", description="A local file path, or a web URL/stream. '' clears.",
    )


class VideoStatusResponse(ResultBase):
    """Current playback state for a device's video background override.

    ``playing`` is False when there is no active playback for the
    device; the other fields are zero / empty in that case.  Read-only —
    state-changing routes are :class:`PlayVideo` / :class:`StopVideo` /
    :class:`PauseVideo` / :class:`SeekVideo` / :class:`LoopVideo`.
    """
    key: str = ""
    playing: bool = False
    paused: bool = False
    cursor: int = 0
    frame_count: int = 0
    fps: int = 0
    loop: bool = False


class BootAnimationRequest(BaseModel):
    """Boot-animation upload — a directory of frames + per-frame dwell."""
    frames_dir: str = Field(
        ..., description="Filesystem directory containing 1–248 image frames "
                         "(PNG / JPG / BMP / WebP), sorted alphabetically.",
    )
    delay_ds: int = Field(
        10, ge=1, le=25,
        description="Uniform dwell per frame in deciseconds (10 = 1.0 s, max 25 = 2.5 s).",
    )


class PlayVideoRequest(BaseModel):
    """Play a video as a device's background. Server-side path."""
    path: str = Field(..., min_length=1)
    fps: int = Field(15, ge=1, le=60)


# ── Control-center settings ──────────────────────────────────────────


class TempUnitRequest(BaseModel):
    unit: str = Field(..., pattern="^[CF]$")


class LanguageRequest(BaseModel):
    language: str = Field(..., min_length=1)


class GpuDeviceRequest(BaseModel):
    gpu_key: str = Field("", description="Sensor key, e.g. 'nvidia:0'. '' = auto.")


class RefreshIntervalRequest(BaseModel):
    seconds: float = Field(
        ..., ge=MIN_REFRESH_INTERVAL_S, le=MAX_REFRESH_INTERVAL_S,
    )


class TimeFormatRequest(BaseModel):
    fmt: str = Field(..., pattern="^(12h|24h)$")
    key: str | None = None


class DateFormatRequest(BaseModel):
    fmt: str = Field(..., min_length=1)
    key: str | None = None
