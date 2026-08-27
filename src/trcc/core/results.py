"""Result dataclasses returned by Commands.

Results are the Command API's return values — the universal language UIs
render.  Every Command has one concrete Result type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import (
    OVERLAY_DEFAULT_CLOCK_SOURCE,
    OVERLAY_DEFAULT_COLOR,
    OVERLAY_DEFAULT_FORMAT,
    OVERLAY_DEFAULT_SIZE,
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
    # Same tri-state as ``RenderResult.connected`` — see there for why
    # ``None`` and ``False`` must not be collapsed.
    connected: bool | None = None


@dataclass(frozen=True, slots=True)
class RenderResult(Result):
    """Built + sent one frame through the render pipeline.

    The three video fields are ``None`` when the device has no playback —
    distinct from ``0``, which a UI needs in order to tell "this theme is not a
    video" from "frame 0 of a video".  They exist so a ticking UI can render its
    own progress/counter and pace its own timer WITHOUT reaching into
    ``MediaService``: under ``TRCC_DAEMON=1`` the UI holds an ``AppProxy`` that
    exposes ``dispatch`` and nothing else, so ``app.media`` is a crash (#249).

    Same shape, and the same reason, as ``VideoStarted.interval_ms`` — derived
    server-side "so UIs don't have to query MediaService themselves".
    """
    key: str = ""
    bytes_sent: int = 0
    theme_name: str = ""
    cursor: int | None = None
    frame_count: int | None = None
    interval_ms: int | None = None
    # Was the device reachable?  ``False`` = absent or not connected — the
    # caller's own fault-handling case; ``True`` = we reached it, so any
    # failure here is something else; ``None`` = the Command failed before it
    # ever asked (a bad argument, a missing file).
    #
    # Test ``is False``, never falsiness: ``None`` is "did not ask", not
    # "disconnected", and collapsing them is the bug this field exists to
    # avoid.  Needed because the daemon flattens a raised
    # ``DeviceNotConnectedError`` into a message STRING, so without it a UI
    # would have to match on that text to tell a dead device from a dead theme.
    connected: bool | None = None


@dataclass(frozen=True, slots=True)
class PreviewResult(Result):
    """One rendered frame, stopped before the wire — what the panel shows.

    Every UI previews the same way, so every UI needs a different carrier for
    it: the two Qt skins want the renderer surface, the REST routes want
    encoded bytes, the terminal wants sampled pixels.  All three ride here and
    the caller reads the one it asked for — a second Command per output format
    would be the same render three times.

    ``surface`` is the live Renderer surface (``QImage`` under ``QtRenderer``)
    and is **in-process only** — ``ipc._to_wire`` drops it to ``None`` with a
    warning, the same contract ``FrameSent.surface`` already has.  A daemon
    client asks with ``encode="png"`` and reads :attr:`image` instead.

    ``ok=True`` with ``surface=None`` means "nothing to preview yet" (no theme
    loaded) — a normal pre-load state, not a failure, same ruling as
    ``VideoStatus`` on a device with no playback.  ``ok=False`` is reserved for
    a device that isn't attached or a render that raised.
    """
    key: str = ""
    surface: Any = None
    image: bytes = b""
    media_type: str = ""
    width: int = 0
    height: int = 0
    theme_name: str = ""
    # Row-major ``[y][x] -> (r, g, b)``, populated only when the caller asks
    # for a sample grid (the CLI's ANSI preview).  JSON-safe, unlike surface.
    pixels: list[list[tuple[int, int, int]]] = field(default_factory=list)


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
    """Downloaded cloud-theme previews for a resolution.

    ``directory`` is the library actually scanned, which is NOT always
    ``{width}{height}``: a per-SKU panel reads ``1600720l`` and one whose
    variant archive has not landed falls back to the generic name.  Carrying
    it means a caller that needs to address those files (the API builds a
    ``/static/web/<dir>/<id>.png`` URL) reads the answer instead of
    re-spelling it from the resolution and getting it wrong.
    """
    width: int = 0
    height: int = 0
    directory: str = ""
    entries: list[WebPreviewInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VideoResult(Result):
    key: str = ""
    path: str = ""
    frame_count: int = 0


@dataclass(frozen=True, slots=True)
class DeviceStateResult(Result):
    """What a device IS — identity, live connection, handshake-derived geometry.

    Twenty-five sites across all four UIs did ``app.devices.get(key)`` and then
    read attributes off the live ``Device``.  Two problems with that: CLAUDE.md
    says plainly *"UIs never hold Device references directly"*, and
    ``app.devices`` does not exist on the ``AppProxy`` a daemon-mode UI holds,
    so every one of them raised under ``TRCC_DAEMON=1`` (#249).

    Flattened deliberately.  Handing back ``ProductInfo`` and ``DeviceProfile``
    objects would keep the UI holding domain objects and would not survive the
    daemon socket; every field here is what a UI actually read.

    **The handshake-derived fields are ``None`` before a handshake — not 0 and
    not False.**  ``resolution`` unknown is a different state from a 0x0 panel,
    and ``rotate=None`` ("we have not asked the hardware yet") is different
    from ``rotate=False`` ("this panel does not rotate").  A UI drawing a
    preview must be able to tell those apart.
    """
    key: str = ""
    # Identity — from the product registry, known before any handshake.
    vid: int = 0
    pid: int = 0
    vendor: str = ""
    product: str = ""
    wire: str = ""
    kind: str = ""
    model: str = ""
    button_image: str = ""
    native_resolution: tuple[int, int] = (0, 0)
    # Live connection state.
    connected: bool = False
    is_led: bool = False
    # Handshake-derived — None until the device has answered.
    resolution: tuple[int, int] | None = None
    pm_byte: int | None = None
    sub_byte: int | None = None
    fbl: int | None = None
    jpeg: bool | None = None
    rotate: bool | None = None
    widescreen: bool | None = None
    led_style: str | None = None


@dataclass(frozen=True, slots=True)
class PathsResult(Result):
    """Where this install keeps things — the answer to "where do I put it?".

    A UI has legitimate reasons to know a directory: stage an HTTP upload,
    open the log, point a file picker at the user's backgrounds.  Every one of
    them reached ``app.platform.paths()`` to find out, which is a crash under
    ``TRCC_DAEMON=1`` where the UI holds an ``AppProxy`` exposing ``dispatch``
    and nothing else (#249).

    Paths travel as **strings**, not ``Path``: this Result crosses the daemon
    socket as JSON, and a caller that wants a ``Path`` says so at the point of
    use.

    The resolution-scoped fields are ``None`` — **not ``""``** — when the
    Command was given no resolution.  An empty string is falsy and
    indistinguishable from a real empty path, so it collapses "you did not ask
    for this" into the same value as "this is empty", exactly the way
    ``fps or 30`` collapses "no video" into "30 fps".  ``None`` means the
    question was not asked; a string is an answer.
    """
    # Roots — always populated, so a plain str is honest.
    config_dir: str = ""
    data_dir: str = ""
    user_content_dir: str = ""
    user_data_dir: str = ""
    log_file: str = ""
    uploads_dir: str = ""
    media_player_dir: str = ""
    screencast_dir: str = ""
    # Resolution-scoped — None until ``GetPaths.resolution`` is supplied.
    theme_dir: str | None = None
    user_theme_dir: str | None = None
    user_mask_dir: str | None = None
    user_background_dir: str | None = None
    cloud_theme_dir: str | None = None
    cloud_mask_dir: str | None = None


@dataclass(frozen=True, slots=True)
class VideoStatusResult(Result):
    """Read-only playback state for a device — the QUESTION, not the tick.

    ``TickDisplay`` already returns cursor / frame_count / interval_ms, but it
    *renders and sends a frame* to do it.  A UI that only wants to know
    "is there a video, and what is it doing?" — to label a status line, choose
    a timer interval, or decide whether to show transport controls — had no way
    to ask, so every UI reached into ``app.media.playback(key)`` directly.  That
    is a crash under ``TRCC_DAEMON=1``, where the UI holds an ``AppProxy`` that
    exposes ``dispatch`` and nothing else (#249).

    **The optional fields are ``None`` when there is no playback — distinct
    from ``0``.** Same rule, and the same reason, as :class:`RenderResult`: a
    caller has to be able to tell "this theme is not a video" from "frame 0 of a
    video", and "fps unknown" from "fps is zero".  Collapsing those with a
    falsy test is how a still image ends up driving a 30 fps timer.
    """
    key: str = ""
    playing: bool = False
    paused: bool = False
    cursor: int | None = None
    frame_count: int | None = None
    fps: int | None = None
    loop: bool = False


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
class MediaPlayerResult(Result):
    """Result of ``SetMediaPlayer`` — the media-player source (a local URI or a
    web URL/stream).  ``playing`` is True when a local source started playback;
    a web URL is referenced (persisted so a SaveTheme captures it) but its
    streaming playback is a separate runtime feature.  ``uri`` echoes the source
    for a daemon/API client reading the response over the wire.
    """
    key: str = ""
    uri: str = ""
    playing: bool = False


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
    # What to tell a user who sees no devices — per-OS, because the answer is
    # "install the udev rules" on Linux and something else everywhere else.
    # Here rather than on its own Command: it is platform identity, which is
    # what this Result already is, and the gui asking for it was reaching
    # ``app.platform`` — a crash under TRCC_DAEMON=1 (#249).
    no_devices_hint: str = ""


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
    # Slideshow config.  Absent until 2026-08-25, which made this Result
    # unable to answer its own docstring: the gui restoring slideshow state
    # had to keep reaching for ``settings.for_device`` — a crash under
    # TRCC_DAEMON=1 — because the snapshot did not carry these three.
    # ``themes`` is a tuple: a Result is frozen, and a list inside one is a
    # mutable field on an immutable DTO.
    slideshow_enabled: bool = False
    slideshow_interval_s: float = 60.0
    slideshow_themes: tuple[str, ...] = ()
    # Added 2026-08-27.  Their absence was the reason two qtgui panels kept
    # reaching ``app.settings.for_device`` — an AttributeError under
    # TRCC_DAEMON=1 — and ``configuration_panel`` said so in a comment:
    # "Background fields come from Settings since the snapshot doesn't carry
    # overlay_background; read directly."  A snapshot that omits what its
    # callers need sends them around it.
    background_mode: str = "theme"
    overlay_background: tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True, slots=True)
class LedZoneEntry:
    """One zone's persisted state, as the LED snapshot reports it.

    ``mode`` is the ``LEDMode`` NAME, matching ``LedSnapshotResult.mode`` — the
    same fact spelled the same way in both places.  ``LEDMode`` is an IntEnum,
    so a consumer wanting the wire number does ``LEDMode[entry.mode].value``
    rather than this DTO carrying the name and the number and letting them
    drift apart.
    """
    mode: str = "STATIC"
    color: tuple[int, int, int] = (0, 0, 0)
    brightness: int = 100
    on: bool = True


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
    # Per-zone state and the carousel mask.  Absent until 2026-08-25, which
    # left ``zone_count`` reporting HOW MANY zones there are with no way to ask
    # what any of them IS — so the gui kept reaching ``settings.for_led``, a
    # crash under TRCC_DAEMON=1.  Tuples, because a Result is frozen.
    zones: tuple[LedZoneEntry, ...] = ()
    zone_sync_zones: tuple[bool, ...] = ()


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
    # Devices whose scene cache was dropped because they were SHOWING the
    # deleted theme.  Reported so a UI can blank the right previews without
    # re-deriving which devices those were — it cannot see the scene cache.
    invalidated: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MaskUploadResult(Result):
    key: str = ""
    path: str = ""


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One mask row: the mask dir (``path``) + its preview image (``preview``).

    ``preview`` is the ``Theme.png`` tile (falling back to the ``01.png``
    overlay) resolved by ``ContentStore.discover_masks`` — carried here so every
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
    """Frozen snapshot of one :class:`OverlayElement`.

    NOT redundant with the domain object, despite the identical fields:
    ``OverlayElement`` is MUTABLE and lives inside ``Settings`` (see
    ``Settings.update_user_overlay_element``, which ``setattr``s it in
    place).  Handing that object out on a Result would let any UI mutate
    persisted app state through a return value.  This is the copy that
    crosses the boundary.

    Defaults come from ``core.models`` so the two cannot drift.
    """
    id: str
    type: str
    x: int = 0
    y: int = 0
    color: str = OVERLAY_DEFAULT_COLOR
    size: int = OVERLAY_DEFAULT_SIZE
    #: Font family, or "" for the theme default — see OverlayElement.font.
    font: str = ""
    bold: bool = False
    italic: bool = False
    text: str = ""
    metric: str = ""
    format: str = OVERLAY_DEFAULT_FORMAT
    show_unit: bool = True
    source: str = OVERLAY_DEFAULT_CLOCK_SOURCE


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


@dataclass(frozen=True, slots=True)
class OverlayLayoutResult(Result):
    """What is actually on screen for a device — the read side of overlay.

    Every other overlay Result reports a mutation.  This one answers the
    question the GUIs were reaching past the bus to ask, and that cli/api
    could not ask at all: which layer is live, what is drawn, and whether
    it draws.

    :attr:`enabled` is part of the answer rather than a separate lookup —
    ``DisplayService`` gates the whole overlay on the device's own
    ``overlay_enabled``, so with it off the honest answer to "what is on
    screen" is *nothing*, whatever :attr:`elements` lists.

    :attr:`source` is the winning layer (``"user"`` / ``"mask"`` /
    ``"theme"``), so a caller can tell a theme layout of zero elements from
    a user layout that was deliberately emptied.
    """
    key: str = ""
    elements: list[OverlayElementEntry] = field(default_factory=list)
    source: str = ""
    enabled: bool = True
    theme_name: str = ""


@dataclass(frozen=True, slots=True)
class ThemeDirectoriesResult(Result):
    """Which browser directories a device's theme/mask panels point at.

    The dims a catalog is sized to follow the ROTATED size when the user has
    turned the panel, else the canvas.  Only the LOCAL theme browser falls back
    to the landscape directory when no portrait one exists on disk — the render
    pipeline pixel-rotates that art at encode time (#136).  ``portrait_fallback``
    reports whether that happened, because the caller browses at a different
    resolution when it did.

    Paths travel as **strings**, like :class:`PathsResult`: this crosses the
    daemon socket as JSON, and a caller that wants a ``Path`` says so at the
    point of use.
    """
    key: str = ""
    catalog_size: tuple[int, int] = (0, 0)
    theme_dir: str = ""
    user_theme_dir: str = ""
    web_dir: str = ""
    masks_dir: str = ""
    portrait_fallback: bool = False


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
class SlideshowAdvanceResult(Result):
    """One slideshow tick — which theme comes next, if any.

    Separate from :class:`SlideshowResult`, which reports what is CONFIGURED.
    ``theme_name is None`` with ``ok=True`` is the ordinary answer: the
    interval has not elapsed, so nothing rotates.  ``due`` says which of the
    two "nothing happened" cases it was, because "not yet" and "no slideshow
    running" need different reactions from a caller driving a timer.
    """
    key: str = ""
    theme_name: str | None = None
    due: bool = False
    # Is a slideshow configured AND enabled for this device at all?
    #
    # ``due`` alone collapses "not yet" into "nothing running", and those want
    # opposite reactions from a timer-driver: keep waiting, or stop ticking.
    # The distinction lived only in ``message`` at first, which is the same
    # string-matching this codebase rejects for ``connected``.
    running: bool = False


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
