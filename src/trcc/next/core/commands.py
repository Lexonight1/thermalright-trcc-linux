"""Commands — the universal UI contract.

Every user action is one Command class.  UIs build Commands, hand them
to App.dispatch, and render the returned Result.  Adding a new UI = new
adapter over the same Command classes.  Adding a new action = new
Command class.

Commands own their orchestration: they call services, talk to devices,
publish events, return a Result.  They are the business-logic layer
between UIs and the domain.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

from .errors import (
    DeviceNotConnectedError,
    DeviceNotFoundError,
    HandshakeError,
    ThemeError,
    TransportError,
)
from .events import (
    BrightnessChanged,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    ErrorOccurred,
    FitModeChanged,
    FrameSent,
    GpuDeviceChanged,
    LanguageChanged,
    LedColorsChanged,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OrientationChanged,
    OverlayChanged,
    RefreshIntervalChanged,
    SplitModeChanged,
    TempUnitChanged,
    ThemeExported,
    ThemeImported,
    ThemeLoaded,
    ThemeSaved,
    VideoStarted,
    VideoStopped,
)
from .led_models import LEDMode, LedRuntimeState
from .models import FitMode, OverlayElement
from .registry import find_product
from .results import (
    AutostartResult,
    BackgroundModeResult,
    BootAnimationResult,
    BrightnessResult,
    ClockFormatResult,
    CloudCategoryEntry,
    CloudThemeEntryResult,
    CloudThemeLoadResult,
    CloudThemesListResult,
    ConnectResult,
    ControlCenterSnapshotResult,
    DebugReportPayload,
    DeleteThemeResult,
    DisconnectResult,
    DiscoverResult,
    DiskEntry,
    DiskIndexResult,
    DisksListResult,
    DoctorResultPayload,
    FileEntry,
    FirstRunStatusResult,
    FitModeResult,
    FontsListResult,
    GpuDeviceResult,
    GpuEntry,
    GpusListResult,
    HddEnabledResult,
    HealthCheckEntry,
    HealthReportResult,
    KeepaliveResult,
    LanguageEntry,
    LanguageResult,
    LanguagesListResult,
    LcdSnapshotResult,
    LedColorsResult,
    LedModesListResult,
    LedSnapshotResult,
    LedStyleEntry,
    LedStylesListResult,
    LoopVideoResult,
    MaskApplyResult,
    MaskPositionResult,
    MasksListResult,
    MaskUploadResult,
    MaskVisibilityResult,
    MemoryRatioResult,
    OrientationResult,
    OverlayBackgroundResult,
    OverlayConfigResult,
    OverlayElementDeleteResult,
    OverlayElementEntry,
    OverlayElementResult,
    OverlayResult,
    PauseVideoResult,
    PlatformInfoResult,
    QuickstartResult,
    QuickstartStepEntry,
    RefreshIntervalResult,
    RenderResult,
    Result,
    SeekVideoResult,
    SendResult,
    SensorsResult,
    SetupResult,
    SlideshowResult,
    SplitModeResult,
    TempUnitResult,
    ThemeDcExportResult,
    ThemeExportResult,
    ThemeImportResult,
    ThemeListEntry,
    ThemeResult,
    ThemesListResult,
    UpdateCheckResult,
    UpgradeResult,
    VideoResult,
    WeekStartResult,
)

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)


# =========================================================================
# Base
# =========================================================================


R_co = TypeVar("R_co", bound=Result, covariant=True)


class Command(ABC, Generic[R_co]):
    """A user action.  Exactly one execute method; returns one Result.

    Parameterised on the concrete Result subclass so that
    ``app.dispatch(DiscoverDevices())`` is typed as ``DiscoverResult``,
    not the Result base — callers get the subclass's fields (products,
    readings, etc.) without casting.
    """

    @abstractmethod
    def execute(self, app: App) -> R_co: ...


# =========================================================================
# Discovery / connection
# =========================================================================


@dataclass(frozen=True, slots=True)
class DiscoverDevices(Command[DiscoverResult]):
    """List attached devices that match the product registry."""

    def execute(self, app: App) -> DiscoverResult:
        live = app.platform.scan_devices()
        products = []
        for info in live:
            product = find_product(info.vid, info.pid)
            if product is not None:
                products.append(product)
                app.events.publish(DeviceDiscovered(
                    key=info.key, product_name=product.product,
                ))
        return DiscoverResult(
            ok=True,
            message=f"{len(products)} device(s) found",
            products=products,
            devices=live,
        )


@dataclass(frozen=True, slots=True)
class ConnectDevice(Command[ConnectResult]):
    """Attach + handshake with a discovered device."""
    key: str

    def execute(self, app: App) -> ConnectResult:
        try:
            vid_str, pid_str = self.key.split(":")
            vid, pid = int(vid_str, 16), int(pid_str, 16)
        except ValueError:
            return ConnectResult(
                ok=False, key=self.key,
                message=f"Invalid device key: {self.key!r} (expected 'vvvv:pppp')",
            )

        try:
            device = app.attach(vid, pid)
        except DeviceNotFoundError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="not_found",
                                             key=self.key))
            return ConnectResult(ok=False, key=self.key, message=str(e))

        try:
            handshake = device.connect()
        except (HandshakeError, TransportError) as e:
            app.detach(self.key)
            app.events.publish(ErrorOccurred(message=str(e), kind="handshake",
                                             key=self.key))
            return ConnectResult(ok=False, key=self.key, message=str(e))

        app.events.publish(DeviceConnected(
            key=self.key, resolution=handshake.resolution,
        ))
        return ConnectResult(
            ok=True, key=self.key,
            message=f"Connected: {handshake.resolution}",
            handshake=handshake,
        )


@dataclass(frozen=True, slots=True)
class DisconnectDevice(Command[DisconnectResult]):
    """Close the transport and drop the device."""
    key: str

    def execute(self, app: App) -> DisconnectResult:
        if self.key not in app.devices:
            return DisconnectResult(
                ok=False, key=self.key,
                message=f"Not attached: {self.key}",
            )
        app.detach(self.key)
        app.events.publish(DeviceDisconnected(key=self.key))
        return DisconnectResult(ok=True, key=self.key, message="Disconnected")


# =========================================================================
# LCD — frames, orientation, brightness
# =========================================================================


@dataclass(frozen=True, slots=True)
class SendFrame(Command[SendResult]):
    """Push already-built frame bytes to the device.

    Bypasses the theme/render pipeline (Phase 5+) — useful for scripts
    and end-to-end smoke tests.
    """
    key: str
    data: bytes

    def execute(self, app: App) -> SendResult:
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, message=str(e))
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )
        try:
            ok = device.send(self.data)
        except TransportError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key))
            return SendResult(ok=False, key=self.key, message=str(e))
        bytes_sent = len(self.data) if ok else 0
        if ok:
            app.keepalive.store(self.key, self.data)
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key,
            message=f"Sent {bytes_sent} bytes" if ok else "Send returned False",
            bytes_sent=bytes_sent,
        )


@dataclass(frozen=True, slots=True)
class SendColor(Command[SendResult]):
    """Push a solid-color frame to a connected LCD device.

    Bypasses the theme/render pipeline — useful as a primary diagnostic
    (``trcc-next display color 0402:3922 ff0000`` turns the screen red)
    and as the smallest path that exercises every link in the wire chain:
    handshake-derived profile → DisplayService.build_solid_color_frame →
    Device.send.
    """
    key: str
    r: int
    g: int
    b: int

    def execute(self, app: App) -> SendResult:
        for label, value in (("r", self.r), ("g", self.g), ("b", self.b)):
            if not 0 <= value <= 255:
                return SendResult(
                    ok=False, key=self.key, bytes_sent=0,
                    message=f"{label} out of range (0-255): {value}",
                )

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )

        try:
            frame = app.display.build_solid_color_frame(
                info=device.info,
                color=(self.r, self.g, self.b),
                profile=device.profile,
            )
            ok = device.send(frame)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))

        bytes_sent = len(frame) if ok else 0
        if ok:
            app.keepalive.store(self.key, frame)
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key, bytes_sent=bytes_sent,
            message=(f"Sent {bytes_sent} bytes (#{self.r:02x}{self.g:02x}{self.b:02x})"
                     if ok else "Send returned False"),
        )


@dataclass(frozen=True, slots=True)
class RenderAndSend(Command[RenderResult]):
    """Render the device's active theme with live sensors, push to the wire.

    Called by tickers — GUI QTimer, CLI `display play` loop, API tick
    endpoint — every ~AppSettings.refresh_interval_s.  Uses the
    DisplayService scene cache so only the changed layer rebuilds per
    tick (sensors moved → redraw overlay; video cursor advanced →
    rebuild bg; otherwise pure cache hit + composite).
    """
    key: str

    def execute(self, app: App) -> RenderResult:
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return RenderResult(ok=False, key=self.key, message=str(e))
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )

        theme = app.active_themes.get(self.key)
        if theme is None:
            return RenderResult(
                ok=False, key=self.key,
                message="No active theme — dispatch LoadTheme first",
            )

        sensors = app.platform.sensors().read_all()

        try:
            frame = app.display.build_frame(
                info=device.info, theme=theme, sensors=sensors,
                profile=device.profile,
            )
            ok = device.send(frame)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            return RenderResult(
                ok=False, key=self.key, theme_name=theme.name,
                message=str(e),
            )

        if ok:
            app.keepalive.store(self.key, frame)
            app.events.publish(FrameSent(key=self.key, bytes_sent=len(frame)))
        return RenderResult(
            ok=ok, key=self.key,
            bytes_sent=len(frame), theme_name=theme.name,
            message=(f"Rendered + sent {len(frame)} bytes"
                     if ok else "Render built frame but send returned False"),
        )


@dataclass(frozen=True, slots=True)
class LoadTheme(Command[ThemeResult]):
    """Parse a theme, persist it, render the first frame, and send it.

    If the device isn't attached, the theme is still persisted so it
    takes effect on next connect.  If no Renderer is attached to the
    App, the send step is skipped (parse + persist only).
    """
    key: str
    path: Path

    def execute(self, app: App) -> ThemeResult:
        try:
            theme = app.themes.load(self.path)
        except ThemeError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="theme",
                                             key=self.key))
            return ThemeResult(ok=False, key=self.key, message=str(e))

        # Persist the absolute path — names are display strings, paths
        # are the stable reference RestoreLastTheme needs.
        app.settings.set_current_theme(self.key, str(theme.path.resolve()))
        app.active_themes[self.key] = theme
        app.display.invalidate(self.key)  # drop stale scene cache from prev theme
        app.media.unload(self.key)        # drop stale video frames
        app.events.publish(ThemeLoaded(key=self.key, theme_name=theme.name))

        # If device is attached + connected + Renderer available, send an
        # immediate first frame.  Otherwise the theme is saved for the
        # next connect / tick.
        theme_path_str = str(theme.path.resolve())
        device = app.devices.get(self.key)
        if device is None or not device.is_connected:
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=f"Theme '{theme.name}' saved (device not connected)",
            )
        if app._renderer is None:  # pyright: ignore[reportPrivateUsage]
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=f"Theme '{theme.name}' saved (no Renderer attached)",
            )

        try:
            frame = app.display.build_frame(
                info=device.info, theme=theme, sensors={},
                profile=device.profile,
            )
            sent = device.send(frame)
        except (TransportError, Exception) as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="render",
                                             key=self.key))
            return ThemeResult(
                ok=False, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=f"Render/send failed: {e}",
            )

        if sent:
            app.events.publish(FrameSent(key=self.key, bytes_sent=len(frame)))
            log.info(
                "LoadTheme: %s rendered + sent (%d bytes) from %s",
                theme.name, len(frame), theme_path_str,
            )
        return ThemeResult(
            ok=sent, key=self.key, theme_name=theme.name,
            theme_path=theme_path_str,
            message=(f"Theme '{theme.name}' loaded and sent ({len(frame)} bytes)"
                     if sent else f"Theme '{theme.name}' rendered but send failed"),
        )


# ── Theme persistence: save / export / import ────────────────────────


def _is_safe_theme_name(name: str) -> bool:
    """Reject theme names that could escape ``user_content_dir``.

    No path separators, no ``..``, no absolute prefixes, no NUL bytes,
    no leading dots (avoid hidden dirs).
    """
    if not name or len(name) > 255:
        return False
    if name[0] == ".":
        return False
    bad = {"/", "\\", "\x00"}
    if any(ch in bad for ch in name):
        return False
    return ".." not in name.split("/")


@dataclass(frozen=True, slots=True)
class SaveTheme(Command[ThemeResult]):
    """Duplicate the device's active theme directory under a new name.

    Pure file copy via ``shutil.copytree`` from the active theme's path
    to ``user_content_dir / name``. The new directory is a fully
    independent theme — editing it doesn't affect the source.
    """
    key: str
    name: str

    def execute(self, app: App) -> ThemeResult:
        if not _is_safe_theme_name(self.name):
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"invalid theme name {self.name!r} "
                         "(no path separators, '..', leading '.', or NUL bytes)"),
            )

        theme = app.active_themes.get(self.key)
        if theme is None:
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"no active theme for {self.key} — load one first"),
            )

        import shutil
        target = app.platform.paths().user_content_dir() / self.name
        if target.exists():
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"target already exists: {target} "
                         "(choose a different name)"),
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(theme.path, target)
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=f"failed to copy theme: {e}",
            )

        app.events.publish(ThemeSaved(
            key=self.key, theme_name=self.name, path=str(target),
        ))
        return ThemeResult(
            ok=True, key=self.key, theme_name=self.name,
            message=f"theme saved as '{self.name}' at {target}",
        )


@dataclass(frozen=True, slots=True)
class ExportTheme(Command[ThemeExportResult]):
    """Zip a theme under ``user_content_dir / theme_name`` to an archive path.

    The theme_name must resolve to a directory inside ``user_content_dir``;
    the archive_path is written wherever the caller specifies (CLI/API
    are responsible for sanitizing that path at their edge).
    """
    theme_name: str
    archive_path: Path

    def execute(self, app: App) -> ThemeExportResult:
        if not _is_safe_theme_name(self.theme_name):
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=f"invalid theme name {self.theme_name!r}",
            )

        source = app.platform.paths().user_content_dir() / self.theme_name
        if not source.is_dir():
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=f"theme {self.theme_name!r} not found at {source}",
            )

        try:
            app.themes.export(source, self.archive_path)
        except ThemeError as e:
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=str(e),
            )

        app.events.publish(ThemeExported(
            theme_name=self.theme_name, archive_path=str(self.archive_path),
        ))
        return ThemeExportResult(
            ok=True, theme_name=self.theme_name,
            archive_path=str(self.archive_path),
            message=f"theme '{self.theme_name}' exported to {self.archive_path}",
        )


@dataclass(frozen=True, slots=True)
class ImportTheme(Command[ThemeImportResult]):
    """Unpack a theme archive into ``user_content_dir / name``.

    ``name`` defaults to the archive filename's stem when blank.
    Zip-slip is filtered server-side by ``ThemeService.import_``.
    """
    archive_path: Path
    name: str = ""

    def execute(self, app: App) -> ThemeImportResult:
        chosen_name = self.name.strip() or self.archive_path.stem
        if not _is_safe_theme_name(chosen_name):
            return ThemeImportResult(
                ok=False, theme_name=chosen_name, path="",
                message=f"invalid theme name {chosen_name!r}",
            )

        target = app.platform.paths().user_content_dir() / chosen_name

        try:
            theme = app.themes.import_(self.archive_path, target)
        except ThemeError as e:
            return ThemeImportResult(
                ok=False, theme_name=chosen_name, path=str(target),
                message=str(e),
            )

        app.events.publish(ThemeImported(
            theme_name=chosen_name, path=str(theme.path),
        ))
        return ThemeImportResult(
            ok=True, theme_name=chosen_name, path=str(theme.path),
            message=f"theme imported as '{chosen_name}' at {theme.path}",
        )


@dataclass(frozen=True, slots=True)
class ListThemes(Command[ThemesListResult]):
    """Enumerate themes for a device resolution.

    With ``resolution=(w, h)`` (the GUI/CLI default), walks both
    ``paths.theme_dir(w, h)`` (pkg + cloud-downloaded) and
    ``paths.user_theme_dir(w, h)`` (legacy user-saved location) so
    in-place users see every theme without a migration step.

    With ``directory=``, scans that exact dir (escape hatch for tests
    and ad-hoc browsing).
    """
    resolution: tuple[int, int] | None = None
    directory: Path | None = None

    def execute(self, app: App) -> ThemesListResult:
        if self.directory is not None:
            roots = [self.directory]
        elif self.resolution is not None:
            paths = app.platform.paths()
            w, h = self.resolution
            roots = [paths.theme_dir(w, h), paths.user_theme_dir(w, h)]
        else:
            return ThemesListResult(
                ok=False, directory="", themes=[],
                message="ListThemes requires resolution=(w,h) or directory=...",
            )

        seen: set[Path] = set()
        entries: list[ThemeListEntry] = []
        for root in roots:
            for theme in app.themes.list(root):
                if theme.path in seen:
                    continue
                seen.add(theme.path)
                entries.append(ThemeListEntry(
                    name=theme.name, resolution=theme.resolution,
                    path=str(theme.path),
                ))
        target_str = "; ".join(str(r) for r in roots)
        return ThemesListResult(
            ok=True, directory=target_str, themes=entries,
            message=f"{len(entries)} theme(s) under {target_str}",
        )


@dataclass(frozen=True, slots=True)
class ExportDcTheme(Command[ThemeDcExportResult]):
    """Write a theme out as a legacy-compatible ``config1.dc`` file.

    Reads the named theme under ``user_content_dir``, optionally layers in
    a device's user overlay elements (so the exported DC reflects what
    the user actually sees on screen), and writes ``output_path``.  Used
    by anyone sharing a next/-managed theme back to Windows TRCC or
    legacy Linux users.
    """
    theme_name: str
    output_path: Path
    device_key: str = ""

    def execute(self, app: App) -> ThemeDcExportResult:
        if not _is_safe_theme_name(self.theme_name):
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=f"invalid theme name {self.theme_name!r}",
            )
        theme_dir = (
            app.platform.paths().user_content_dir() / self.theme_name
        )
        if not theme_dir.is_dir():
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=f"theme not found at {theme_dir}",
            )
        user_overlays: list[dict] = []
        if self.device_key:
            settings = app.settings.for_device(self.device_key)
            user_overlays = [
                e.to_dict() for e in settings.user_overlay_elements
            ]
        try:
            written = app.themes.export_dc(
                theme_dir, self.output_path,
                user_overlay_elements=user_overlays,
            )
        except ThemeError as e:
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=str(e),
            )
        return ThemeDcExportResult(
            ok=True, theme_name=self.theme_name,
            output_path=str(written),
            message=f"wrote DC theme to {written}",
        )


# ── Video playback override ──────────────────────────────────────────


_VIDEO_EXTS_OK = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt"})


@dataclass(frozen=True, slots=True)
class PlayVideo(Command[VideoResult]):
    """Decode a video into a per-device playback override.

    While a playback is loaded, ``DisplayService._resolve_background``
    pulls frames from it on every tick instead of the active theme's
    background — so a user can play an arbitrary video without first
    constructing a video-backed theme. Call ``StopVideo`` (or
    ``DisconnectDevice``, which clears the playback) to revert.

    The device must already be attached + connected; the playback's
    frame size is taken from ``device.profile.resolution`` (or
    ``info.native_resolution`` pre-handshake) so frames are pre-scaled
    for the wire.
    """
    key: str
    path: Path
    fps: int = 15

    def execute(self, app: App) -> VideoResult:
        if self.path.suffix.lower() not in _VIDEO_EXTS_OK:
            return VideoResult(
                ok=False, key=self.key, path=str(self.path),
                message=(f"unsupported video extension {self.path.suffix!r} "
                         f"(expected one of {sorted(_VIDEO_EXTS_OK)})"),
            )
        if not self.path.exists():
            return VideoResult(
                ok=False, key=self.key, path=str(self.path),
                message=f"video does not exist: {self.path}",
            )
        if not self.path.is_file():
            return VideoResult(
                ok=False, key=self.key, path=str(self.path),
                message=f"video path is not a regular file: {self.path}",
            )

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return VideoResult(ok=False, key=self.key, path=str(self.path),
                                message=str(e))

        if device.profile is not None:
            size = device.profile.resolution
        else:
            size = device.info.native_resolution

        try:
            playback = app.media.load_video(
                device_key=self.key, path=self.path, size=size,
                fps=self.fps,
            )
        except ThemeError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="video", key=self.key,
            ))
            return VideoResult(ok=False, key=self.key, path=str(self.path),
                                message=str(e))

        # Bust the scene cache so the next render picks up the override.
        _invalidate_scene(app, self.key)

        app.events.publish(VideoStarted(
            key=self.key, path=str(self.path),
            frame_count=playback.frame_count,
        ))
        return VideoResult(
            ok=True, key=self.key, path=str(self.path),
            frame_count=playback.frame_count,
            message=(f"playing {self.path.name} on {self.key} "
                     f"({playback.frame_count} frame(s) @ {playback.fps} fps)"),
        )


@dataclass(frozen=True, slots=True)
class StopVideo(Command[VideoResult]):
    """Clear the device's playback override.

    Idempotent — calling on a device with no playback is a no-op + ok=True
    so scripts can use it as a defensive cleanup.
    """
    key: str

    def execute(self, app: App) -> VideoResult:
        had_playback = app.media.playback(self.key) is not None
        app.media.unload(self.key)
        _invalidate_scene(app, self.key)
        if had_playback:
            app.events.publish(VideoStopped(key=self.key))
        return VideoResult(
            ok=True, key=self.key,
            message=(f"video stopped for {self.key}"
                     if had_playback else f"no video playing for {self.key}"),
        )


@dataclass(frozen=True, slots=True)
class UploadBootAnimation(Command[BootAnimationResult]):
    """Upload a multi-frame compressed boot animation to a SCSI LCD's flash.

    The animation persists across power cycles — it plays from device
    flash on every boot until overwritten.  **SCSI-only**; HID / Bulk /
    LY / LED devices return ok=False with a clear message.

    Each frame is loaded via the renderer (any standard image format),
    resized to the device resolution if needed, and encoded to RGB565
    before zlib compression on the device side.  Supported geometries:
    240×240, 240×320, 320×240, 320×320.  Frame count 1–248.

    ``delays_ds[i]`` is the dwell time before frame ``i+1`` plays, in
    deciseconds (10ths of a second); firmware caps at 25 ds (2.5 s).
    Defaults to 10 ds (1 s) for any frame without an explicit delay.
    """
    key: str
    frame_paths: list[Path]
    delays_ds: list[int]

    def execute(self, app: App) -> BootAnimationResult:
        from ..adapters.device.scsi_lcd import ScsiLcd

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return BootAnimationResult(
                ok=False, key=self.key, message=str(e),
                frames_total=len(self.frame_paths),
            )
        if not isinstance(device, ScsiLcd):
            return BootAnimationResult(
                ok=False, key=self.key,
                message=f"{self.key} is not a SCSI LCD (boot animation is SCSI-only)",
                frames_total=len(self.frame_paths),
            )
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )
        if not self.frame_paths:
            return BootAnimationResult(
                ok=False, key=self.key, message="No frames provided",
            )

        # Resolution must come from the handshake-derived profile — boot
        # anim is gated on a fixed set of geometries the firmware accepts,
        # and a device's registry native_resolution can lie if firmware
        # reports a different FBL byte than the product entry expects.
        profile = device.profile
        resolution = (profile.resolution if profile
                      else device.info.native_resolution)

        encoded: list[bytes] = []
        for path in self.frame_paths:
            try:
                encoded.append(app.display.encode_boot_anim_frame(path, resolution))
            except (OSError, ValueError) as e:
                return BootAnimationResult(
                    ok=False, key=self.key,
                    frames_total=len(self.frame_paths),
                    message=f"Failed to load {path.name}: {e}",
                )

        try:
            uploaded = device.send_boot_animation(encoded, list(self.delays_ds))
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            return BootAnimationResult(
                ok=False, key=self.key, frames_total=len(encoded),
                message=str(e),
            )

        ok = uploaded == len(encoded)
        return BootAnimationResult(
            ok=ok, key=self.key,
            frames_uploaded=uploaded, frames_total=len(encoded),
            message=(f"Uploaded {uploaded} frames to {self.key} flash"
                     if ok else f"Partial upload: {uploaded}/{len(encoded)} frames"),
        )


@dataclass(frozen=True, slots=True)
class SetOrientation(Command[OrientationResult]):
    """Set per-device rotation (0 / 90 / 180 / 270).

    Validates against the product registry — device need not be
    connected yet (users often configure before plugging in).
    """
    key: str
    degrees: int

    def execute(self, app: App) -> OrientationResult:
        try:
            vid_str, pid_str = self.key.split(":")
            vid, pid = int(vid_str, 16), int(pid_str, 16)
        except ValueError:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Invalid device key: {self.key!r}",
            )
        info = find_product(vid, pid)
        if info is None:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Unknown device: {self.key}",
            )
        if self.degrees not in info.orientations:
            return OrientationResult(
                ok=False, key=self.key, degrees=self.degrees,
                message=f"Unsupported orientation for {self.key}: {self.degrees}",
            )
        app.settings.set_orientation(self.key, self.degrees)
        app.events.publish(OrientationChanged(key=self.key, degrees=self.degrees))
        return OrientationResult(
            ok=True, key=self.key, degrees=self.degrees,
            message=f"Orientation set to {self.degrees}°",
        )


@dataclass(frozen=True, slots=True)
class SetBrightness(Command[BrightnessResult]):
    """Set per-device display brightness (0–100)."""
    key: str
    percent: int

    def execute(self, app: App) -> BrightnessResult:
        if not 0 <= self.percent <= 100:
            return BrightnessResult(
                ok=False, key=self.key, percent=self.percent,
                message="Brightness out of range (0–100)",
            )
        app.settings.set_brightness(self.key, self.percent)
        app.events.publish(BrightnessChanged(key=self.key, percent=self.percent))
        return BrightnessResult(
            ok=True, key=self.key, percent=self.percent,
            message=f"Brightness set to {self.percent}%",
        )


# ── Display tweaks (fit mode / overlay / split mode) ────────────────


def _invalidate_scene(app: App, key: str) -> None:
    """Drop the per-device scene cache if the display service is wired.

    Settings changes that affect rendering (fit, mask, overlay, split)
    need to bust the cache so the next render rebuilds with the new
    setting. Pure settings writes don't need it; this helper is the
    seam.
    """
    if app._renderer is not None:  # pyright: ignore[reportPrivateUsage]
        app.display.invalidate(key)


def _resolve_resolution(app: App, key: str) -> tuple[int, int] | None:
    """Best-effort resolution lookup from a device key.

    Tries (1) connected device's handshake profile, (2) its DeviceInfo
    native_resolution, (3) the product registry entry's
    native_resolution.  Returns ``None`` when none yield a known size
    (unknown product or malformed key).
    """
    device = app.devices.get(key)
    if device is not None:
        if device.profile is not None:
            return device.profile.resolution
        if device.info.native_resolution != (0, 0):
            return device.info.native_resolution
    try:
        vid_s, pid_s = key.split(":")
        vid = int(vid_s, 16)
        pid = int(pid_s, 16)
    except ValueError:
        return None
    product = find_product(vid, pid)
    if product is None or product.native_resolution == (0, 0):
        return None
    return product.native_resolution


@dataclass(frozen=True, slots=True)
class SetFitMode(Command[FitModeResult]):
    """Set how the background image/video fits the device canvas.

    Accepts the FitMode value strings — ``"width"`` (letterbox top/
    bottom), ``"height"`` (pillarbox left/right), ``"stretch"`` (fill,
    distort).
    """
    key: str
    mode: str

    def execute(self, app: App) -> FitModeResult:
        try:
            parsed = FitMode(self.mode)
        except ValueError:
            valid = ", ".join(m.value for m in FitMode)
            return FitModeResult(
                ok=False, key=self.key, mode=self.mode,
                message=f"mode must be one of: {valid} — got {self.mode!r}",
            )
        app.settings.set_fit_mode(self.key, parsed)
        _invalidate_scene(app, self.key)
        app.events.publish(FitModeChanged(key=self.key, mode=parsed.value))
        return FitModeResult(
            ok=True, key=self.key, mode=parsed.value,
            message=f"fit mode set to {parsed.value}",
        )


@dataclass(frozen=True, slots=True)
class EnableOverlay(Command[OverlayResult]):
    """Toggle the metric overlay layer for a device.

    When disabled, RenderAndSend skips the text/metric layer entirely —
    just the bg+mask renders. Useful for users who want a clean wallpaper
    without sensor readouts.
    """
    key: str
    enabled: bool

    def execute(self, app: App) -> OverlayResult:
        app.settings.set_overlay_enabled(self.key, self.enabled)
        _invalidate_scene(app, self.key)
        app.events.publish(OverlayChanged(key=self.key, enabled=self.enabled))
        return OverlayResult(
            ok=True, key=self.key, enabled=self.enabled,
            message=(f"overlay {'enabled' if self.enabled else 'disabled'} "
                     f"for {self.key}"),
        )


@dataclass(frozen=True, slots=True)
class SetSplitMode(Command[SplitModeResult]):
    """Set the Dynamic Island style for widescreen panels.

    Value range: 0 (off), 1 (style A), 2 (style B, default), 3 (style C).
    Stored per-device regardless of whether the device is widescreen;
    rendering consults the device profile + resolution to decide whether
    to composite the overlay.
    """
    key: str
    mode: int

    def execute(self, app: App) -> SplitModeResult:
        if self.mode not in (0, 1, 2, 3):
            return SplitModeResult(
                ok=False, key=self.key, mode=self.mode,
                message=(f"split mode must be 0 (off), 1, 2, or 3 — "
                         f"got {self.mode}"),
            )
        app.settings.set_split_mode(self.key, self.mode)
        _invalidate_scene(app, self.key)
        app.events.publish(SplitModeChanged(key=self.key, mode=self.mode))
        return SplitModeResult(
            ok=True, key=self.key, mode=self.mode,
            message=(f"split mode set to {self.mode} for {self.key}"
                     if self.mode else f"split mode disabled for {self.key}"),
        )


# ── Mask Commands ────────────────────────────────────────────────────


_MASK_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})

# Canonical mask filename inside a legacy mask subdir (zt{W}{H}/<id>/01.png).
# Cloud catalog + UploadCustomMask both use this name.
_LEGACY_MASK_FILENAME = "01.png"


def _resolve_mask_path(path: Path) -> Path | None:
    """Resolve a mask reference to a renderable image file.

    Accepts a direct image file OR a legacy mask directory (containing
    ``01.png``).  Returns the file path renderers can ``open_image``,
    or ``None`` when neither shape matches.
    """
    if path.is_file() and path.suffix.lower() in _MASK_IMAGE_EXTS:
        return path
    if path.is_dir():
        legacy = path / _LEGACY_MASK_FILENAME
        if legacy.is_file():
            return legacy
    return None


@dataclass(frozen=True, slots=True)
class ApplyMask(Command[MaskApplyResult]):
    """Set a user-supplied mask image that overrides the active theme's mask.

    Accepts either a direct image file (``.png``/``.jpg``/etc.) or a
    legacy mask directory containing ``01.png`` (the layout used by
    Thermalright's cloud mask catalog and by ``UploadCustomMask``).
    Stores the **resolved absolute file path** so subsequent renders
    aren't affected by ``os.chdir`` between calls.
    """
    key: str
    path: Path

    def execute(self, app: App) -> MaskApplyResult:
        candidate = self.path
        if not candidate.exists():
            return MaskApplyResult(
                ok=False, key=self.key, path=str(candidate),
                message=f"mask file does not exist: {candidate}",
            )
        resolved_file = _resolve_mask_path(candidate)
        if resolved_file is None:
            return MaskApplyResult(
                ok=False, key=self.key, path=str(candidate),
                message=(f"mask path is neither a supported image file "
                         f"nor a legacy mask directory with "
                         f"{_LEGACY_MASK_FILENAME}: {candidate}"),
            )
        resolved = str(resolved_file.resolve())
        app.settings.set_mask_path(self.key, resolved)
        _invalidate_scene(app, self.key)
        app.events.publish(MaskApplied(key=self.key, path=resolved))
        return MaskApplyResult(
            ok=True, key=self.key, path=resolved,
            message=f"mask set to {resolved}",
        )


@dataclass(frozen=True, slots=True)
class SetMaskPosition(Command[MaskPositionResult]):
    """Set the mask offset within the canvas, or pass None to reset to (0, 0)."""
    key: str
    x: int | None
    y: int | None

    def execute(self, app: App) -> MaskPositionResult:
        if (self.x is None) != (self.y is None):
            return MaskPositionResult(
                ok=False, key=self.key,
                message="x and y must both be set or both omitted (None)",
            )
        if self.x is not None and self.y is not None:
            if self.x < 0 or self.y < 0:
                return MaskPositionResult(
                    ok=False, key=self.key,
                    message=(f"mask position must be non-negative; "
                             f"got x={self.x}, y={self.y}"),
                )
            position: tuple[int, int] | None = (self.x, self.y)
        else:
            position = None
        app.settings.set_mask_position(self.key, position)
        _invalidate_scene(app, self.key)
        app.events.publish(MaskPositionChanged(key=self.key, position=position))
        return MaskPositionResult(
            ok=True, key=self.key, position=position,
            message=(f"mask position set to {position}" if position
                     else "mask position cleared (default to 0,0)"),
        )


@dataclass(frozen=True, slots=True)
class SetMaskVisible(Command[MaskVisibilityResult]):
    """Toggle the mask overlay visibility for a device."""
    key: str
    visible: bool

    def execute(self, app: App) -> MaskVisibilityResult:
        app.settings.set_mask_visible(self.key, self.visible)
        _invalidate_scene(app, self.key)
        app.events.publish(
            MaskVisibilityChanged(key=self.key, visible=self.visible),
        )
        return MaskVisibilityResult(
            ok=True, key=self.key, visible=self.visible,
            message=(f"mask {'shown' if self.visible else 'hidden'} "
                     f"for {self.key}"),
        )


# =========================================================================
# LED
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetLedColors(Command[LedColorsResult]):
    """Set LED color array + on/off + brightness on a connected Led device."""
    key: str
    colors: list[tuple[int, int, int]]
    global_on: bool = True
    brightness: int = 100

    def execute(self, app: App) -> LedColorsResult:
        from ..adapters.device.led import Led, LedPayload

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=str(e),
            )

        if not isinstance(device, Led):
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=f"{self.key} is not an LED device",
            )
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )

        payload = LedPayload(
            colors=list(self.colors),
            global_on=self.global_on,
            brightness=self.brightness,
        )
        try:
            ok = device.send(payload)
        except TransportError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key))
            return LedColorsResult(
                ok=False, key=self.key, colors=list(self.colors),
                message=str(e),
            )

        if ok:
            app.events.publish(LedColorsChanged(
                key=self.key, color_count=len(self.colors),
            ))
        return LedColorsResult(
            ok=ok, key=self.key, colors=list(self.colors),
            message=(f"Sent {len(self.colors)} LED color(s)"
                     if ok else "LED send returned False"),
        )


@dataclass(frozen=True, slots=True)
class RenderLed(Command[LedColorsResult]):
    """Compute one LED frame from current settings + sensors and send it.

    Drives both branches in one Command:

      * **Segment-display styles** (most LED panels) — ``compute_mask``
        gives the per-segment on/off pattern from the live sensor
        snapshot; the effects engine fills in the color for every lit
        segment.
      * **Non-segment styles** (LF13, LC2) — no mask; the engine
        directly fills ``style.led_count`` colors.

    Mode comes from ``Settings.for_led(key).mode`` (persisted) unless
    the caller passes an explicit ``color`` override, in which case the
    Command short-circuits to STATIC behavior at *that* color (used by
    the CLI ``led color <key> <hex>`` diagnostic).

    Transient counters live on ``app.led_runtime[key]`` — the engine
    advances them as a side effect so consecutive ``RenderLed``
    dispatches phase forward.
    """
    key: str
    color: tuple[int, int, int] | None = None    # None = use Settings.led.color
    phase: int = 0

    def execute(self, app: App) -> LedColorsResult:
        from ..adapters.device.led import Led, LedPayload
        from ..services.led_segment import (
            LegacyMetricsView,
            compute_mask,
            get_display,
        )

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=str(e),
            )

        if not isinstance(device, Led):
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"{self.key} is not an LED device",
            )
        if not device.is_connected:
            raise DeviceNotConnectedError(
                f"{self.key} not connected — dispatch ConnectDevice first"
            )
        if device.led_handshake is None:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"{self.key} handshake incomplete — no style resolved",
            )

        style = device.led_handshake.style
        if style is None:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=(f"{self.key} firmware PM unknown — no style "
                         "resolved; use SetLedColors instead"),
            )

        led_settings = app.settings.for_led(self.key)
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        device_settings = app.settings.for_device(self.key)

        # Build the flat sensor dict the engine consumes.  Two shapes
        # coexist: the dotted IDs we already produce for SensorReading,
        # and the legacy view used by compute_mask().
        enum = app.platform.sensors()
        descriptors = enum.discover()
        current = enum.read_all()
        from .models import SensorReading
        readings = {
            d.sensor_id: SensorReading(
                sensor_id=d.sensor_id, category=d.category,
                value=current.get(d.sensor_id, 0.0),
                unit=d.unit, label=d.label,
            )
            for d in descriptors
        }
        metrics = LegacyMetricsView(readings)

        # If the caller passed an explicit color, treat it as a STATIC
        # diagnostic at full brightness (same shape RenderLed has always
        # offered — "show this color as bright as the LEDs can go").
        # Otherwise the engine reads everything off LedDeviceSettings.
        explicit_color = self.color
        effective_settings = (
            replace(led_settings,
                    mode=LEDMode.STATIC,
                    color=explicit_color,
                    brightness=100,
                    test_mode=False)
            if explicit_color is not None else led_settings
        )

        display = get_display(style)
        if display is None:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=(f"style {style.value} has no segment display — "
                         "use SetLedColors instead"),
            )

        mask = compute_mask(
            style, metrics, phase=self.phase,
            temp_unit=device_settings.temp_unit,
            is_24h=(device_settings.time_format == "24h"),
        )
        segment_count = len(mask)
        colors = app.led_effects.tick(
            effective_settings, runtime, current,
            led_count=segment_count,
        )

        payload = LedPayload(
            colors=colors,
            is_on=mask,
            global_on=effective_settings.global_on,
            brightness=effective_settings.brightness,
        )
        try:
            ok = device.send(payload)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            return LedColorsResult(
                ok=False, key=self.key, colors=colors,
                message=str(e),
            )

        if ok:
            app.events.publish(LedColorsChanged(
                key=self.key, color_count=len(colors),
            ))
        return LedColorsResult(
            ok=ok, key=self.key, colors=colors,
            message=(f"Rendered {style.value} {effective_settings.mode.name} "
                     f"({sum(mask)}/{len(mask)} LEDs on)"
                     if ok else "LED send returned False"),
        )


# =========================================================================
# LED setter Commands — mode / color / brightness / sources / test mode
# =========================================================================


def _publish_led_settings_changed(app: App, key: str) -> None:
    """Single event for any LED settings mutation — UIs subscribe once."""
    app.events.publish(LedColorsChanged(key=key, color_count=0))


@dataclass(frozen=True, slots=True)
class SetLedMode(Command[LedColorsResult]):
    """Set the global animation mode for an LED device."""
    key: str
    mode: LEDMode

    def execute(self, app: App) -> LedColorsResult:
        app.settings.set_led_mode(self.key, self.mode)
        # Phase counters reset on mode change so animation restarts cleanly
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        runtime.rgb_timer = 0
        runtime.test_timer = 0
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED mode set to {self.mode.name}",
        )


@dataclass(frozen=True, slots=True)
class SetLedColor(Command[LedColorsResult]):
    """Set the global LED color (used in STATIC / BREATHING / COLORFUL modes)."""
    key: str
    color: tuple[int, int, int]

    def execute(self, app: App) -> LedColorsResult:
        for label, value in zip("rgb", self.color, strict=False):
            if not 0 <= value <= 255:
                return LedColorsResult(
                    ok=False, key=self.key, colors=[],
                    message=f"{label} out of range (0-255): {value}",
                )
        app.settings.set_led_color(self.key, self.color)
        _publish_led_settings_changed(app, self.key)
        r, g, b = self.color
        return LedColorsResult(
            ok=True, key=self.key, colors=[self.color],
            message=f"LED color set to #{r:02x}{g:02x}{b:02x}",
        )


@dataclass(frozen=True, slots=True)
class SetLedBrightness(Command[LedColorsResult]):
    """Set the global LED brightness percent (0–100)."""
    key: str
    percent: int

    def execute(self, app: App) -> LedColorsResult:
        if not 0 <= self.percent <= 100:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"brightness out of range (0-100): {self.percent}",
            )
        app.settings.set_led_brightness(self.key, self.percent)
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED brightness set to {self.percent}%",
        )


@dataclass(frozen=True, slots=True)
class EnableLedTestMode(Command[LedColorsResult]):
    """Enable / disable the 4-color diagnostic test cycle."""
    key: str
    enabled: bool

    def execute(self, app: App) -> LedColorsResult:
        app.settings.set_led_test_mode(self.key, self.enabled)
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        runtime.test_timer = 0
        runtime.test_color = 0
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED test mode {'enabled' if self.enabled else 'disabled'}",
        )


@dataclass(frozen=True, slots=True)
class SetLedTempSource(Command[LedColorsResult]):
    """Pick the sensor source for TEMP_LINKED mode (``'cpu'`` or ``'gpu'``)."""
    key: str
    source: str

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_temp_source(self.key, self.source)
        except ValueError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED temp source set to {self.source}",
        )


@dataclass(frozen=True, slots=True)
class ToggleLed(Command[LedColorsResult]):
    """Toggle an LED device on/off — global, or one zone if ``zone`` is given.

    Mirrors legacy ``LedCommands.toggle(led, on, zone=None)``.  Global
    toggle flips ``LedDeviceSettings.global_on``; per-zone toggle flips
    the zone's ``on`` flag (used by zone-aware styles to mute a single
    fan/strip without disturbing the others).
    """
    key: str
    on: bool
    zone: int | None = None

    def execute(self, app: App) -> LedColorsResult:
        if self.zone is None:
            app.settings.set_led_global_on(self.key, self.on)
            target = "global"
        else:
            try:
                app.settings.set_led_zone(self.key, self.zone, on=self.on)
            except IndexError as e:
                return LedColorsResult(
                    ok=False, key=self.key, colors=[], message=str(e),
                )
            target = f"zone {self.zone}"
        _publish_led_settings_changed(app, self.key)
        state = "on" if self.on else "off"
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED {target} turned {state}",
        )


@dataclass(frozen=True, slots=True)
class SetLedLoadSource(Command[LedColorsResult]):
    """Pick the sensor source for LOAD_LINKED mode (``'cpu'`` or ``'gpu'``)."""
    key: str
    source: str

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_load_source(self.key, self.source)
        except ValueError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"LED load source set to {self.source}",
        )


# =========================================================================
# LED zone / segment Commands
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetLedZoneColor(Command[LedColorsResult]):
    """Set one zone's persistent color — mirrors legacy zone-aware setters."""
    key: str
    zone: int
    color: tuple[int, int, int]

    def execute(self, app: App) -> LedColorsResult:
        for label, value in zip("rgb", self.color, strict=False):
            if not 0 <= value <= 255:
                return LedColorsResult(
                    ok=False, key=self.key, colors=[],
                    message=f"{label} out of range (0-255): {value}",
                )
        try:
            app.settings.set_led_zone(self.key, self.zone, color=self.color)
        except IndexError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        r, g, b = self.color
        return LedColorsResult(
            ok=True, key=self.key, colors=[self.color],
            message=f"Zone {self.zone} color set to #{r:02x}{g:02x}{b:02x}",
        )


@dataclass(frozen=True, slots=True)
class SetLedZoneSync(Command[LedColorsResult]):
    """Enable/disable the zone-sync carousel for a device."""
    key: str
    enabled: bool

    def execute(self, app: App) -> LedColorsResult:
        app.settings.set_led_zone_sync(self.key, self.enabled)
        runtime = app.led_runtime.setdefault(self.key, LedRuntimeState())
        runtime.zone_sync_ticks = 0
        runtime.zone_sync_current = 0
        _publish_led_settings_changed(app, self.key)
        state = "enabled" if self.enabled else "disabled"
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Zone-sync {state}",
        )


@dataclass(frozen=True, slots=True)
class SetLedZoneSyncInterval(Command[LedColorsResult]):
    """Set how many ticks between zone-sync rotations."""
    key: str
    ticks: int

    def execute(self, app: App) -> LedColorsResult:
        if self.ticks < 1:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=f"interval must be >= 1, got {self.ticks}",
            )
        app.settings.set_led_zone_sync_interval(self.key, self.ticks)
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Zone-sync interval set to {self.ticks} tick(s)",
        )


@dataclass(frozen=True, slots=True)
class SelectZone(Command[LedColorsResult]):
    """Pick the active zone (UI selection state)."""
    key: str
    zone: int

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_selected_zone(self.key, self.zone)
        except ValueError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Selected zone {self.zone}",
        )


@dataclass(frozen=True, slots=True)
class ToggleSegment(Command[LedColorsResult]):
    """Flip one segment's on/off state (segment-display devices)."""
    key: str
    index: int
    on: bool

    def execute(self, app: App) -> LedColorsResult:
        try:
            app.settings.set_led_segment_on(self.key, self.index, self.on)
        except IndexError as e:
            return LedColorsResult(
                ok=False, key=self.key, colors=[], message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        state = "on" if self.on else "off"
        return LedColorsResult(
            ok=True, key=self.key, colors=[],
            message=f"Segment {self.index} turned {state}",
        )


# =========================================================================
# LED sub-controls (clock / week / memory / disk)
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetClockFormat(Command[ClockFormatResult]):
    """12h/24h clock display for LC2-style LED segment devices."""
    key: str
    is_24h: bool

    def execute(self, app: App) -> ClockFormatResult:
        app.settings.set_led_clock_24h(self.key, self.is_24h)
        _publish_led_settings_changed(app, self.key)
        fmt = "24h" if self.is_24h else "12h"
        return ClockFormatResult(
            ok=True, key=self.key, is_24h=self.is_24h,
            message=f"Clock format set to {fmt}",
        )


@dataclass(frozen=True, slots=True)
class SetWeekStart(Command[WeekStartResult]):
    """Week-start convention: ``True`` = Sunday-first, ``False`` = Monday-first."""
    key: str
    sunday_first: bool

    def execute(self, app: App) -> WeekStartResult:
        app.settings.set_led_week_start(self.key, self.sunday_first)
        _publish_led_settings_changed(app, self.key)
        which = "Sunday" if self.sunday_first else "Monday"
        return WeekStartResult(
            ok=True, key=self.key, sunday_first=self.sunday_first,
            message=f"Week starts on {which}",
        )


@dataclass(frozen=True, slots=True)
class SetMemoryRatio(Command[MemoryRatioResult]):
    """Pick the memory display mode: ratio (percentage) or absolute (GB)."""
    key: str
    ratio_mode: bool

    def execute(self, app: App) -> MemoryRatioResult:
        app.settings.set_led_memory_ratio(self.key, self.ratio_mode)
        _publish_led_settings_changed(app, self.key)
        mode = "ratio (%)" if self.ratio_mode else "absolute (GB)"
        return MemoryRatioResult(
            ok=True, key=self.key, ratio_mode=self.ratio_mode,
            message=f"Memory display set to {mode}",
        )


@dataclass(frozen=True, slots=True)
class SetDiskIndex(Command[DiskIndexResult]):
    """Pick which disk to surface read/write stats for."""
    key: str
    index: int

    def execute(self, app: App) -> DiskIndexResult:
        try:
            app.settings.set_led_disk_index(self.key, self.index)
        except ValueError as e:
            return DiskIndexResult(
                ok=False, key=self.key, index=self.index, message=str(e),
            )
        _publish_led_settings_changed(app, self.key)
        return DiskIndexResult(
            ok=True, key=self.key, index=self.index,
            message=f"Disk index set to {self.index}",
        )


# =========================================================================
# ControlCenter / Display Tier-2 setters
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetHddEnabled(Command[HddEnabledResult]):
    """Toggle HDD metrics inclusion in sensor broadcasts."""
    enabled: bool

    def execute(self, app: App) -> HddEnabledResult:
        app.settings.set_hdd_enabled(self.enabled)
        state = "enabled" if self.enabled else "disabled"
        return HddEnabledResult(
            ok=True, enabled=self.enabled,
            message=f"HDD metrics {state}",
        )


@dataclass(frozen=True, slots=True)
class SetBackgroundMode(Command[BackgroundModeResult]):
    """Pick what fills the LCD behind overlays.

    Modes: ``'theme'`` (theme background), ``'color'`` (solid fill),
    ``'transparent'`` (no background, used by screencast overlay).
    """
    key: str
    mode: str

    def execute(self, app: App) -> BackgroundModeResult:
        try:
            app.settings.set_background_mode(self.key, self.mode)  # type: ignore[arg-type]
        except ValueError as e:
            return BackgroundModeResult(
                ok=False, key=self.key, mode=self.mode, message=str(e),
            )
        # Drop the scene cache so the next tick re-renders with the new bg.
        app.display.invalidate(self.key)
        return BackgroundModeResult(
            ok=True, key=self.key, mode=self.mode,
            message=f"Background mode set to {self.mode}",
        )


@dataclass(frozen=True, slots=True)
class SetOverlayBackground(Command[OverlayBackgroundResult]):
    """Set the solid color used when background_mode='color'."""
    key: str
    color: tuple[int, int, int]

    def execute(self, app: App) -> OverlayBackgroundResult:
        try:
            app.settings.set_overlay_background(self.key, self.color)
        except ValueError as e:
            return OverlayBackgroundResult(
                ok=False, key=self.key, color=self.color, message=str(e),
            )
        app.display.invalidate(self.key)
        r, g, b = self.color
        return OverlayBackgroundResult(
            ok=True, key=self.key, color=self.color,
            message=f"Overlay background set to #{r:02x}{g:02x}{b:02x}",
        )


# =========================================================================
# Tier 4 — user overlay element CRUD
# =========================================================================


def _element_to_entry(e: OverlayElement) -> OverlayElementEntry:
    """Flat OverlayElementEntry view for Result types."""
    return OverlayElementEntry(
        id=e.id, type=e.type, x=e.x, y=e.y, color=e.color, size=e.size,
        bold=e.bold, italic=e.italic, text=e.text, metric=e.metric,
        format=e.format, source=e.source,
    )


@dataclass(frozen=True, slots=True)
class AddOverlayElement(Command[OverlayElementResult]):
    """Add a user-edited element to a device's overlay layer.

    ``element_id`` is auto-generated (UUID4) when omitted so callers don't
    have to think about it.  Returned in the result so subsequent
    Update/Delete Commands can reference it.
    """
    key: str
    type: str = "text"
    x: int = 0
    y: int = 0
    color: str = "#ffffff"
    size: int = 16
    bold: bool = False
    italic: bool = False
    text: str = ""
    metric: str = ""
    format: str = "{value}"
    source: str = "time"
    element_id: str = ""

    def execute(self, app: App) -> OverlayElementResult:
        if self.type not in ("text", "metric", "clock"):
            return OverlayElementResult(
                ok=False, key=self.key, element=None,
                message=f"Invalid element type {self.type!r} (expected "
                        "'text' / 'metric' / 'clock')",
            )
        import uuid
        eid = self.element_id or f"el_{uuid.uuid4().hex[:8]}"
        existing = {e.id for e in app.settings.for_device(self.key).user_overlay_elements}
        if eid in existing:
            return OverlayElementResult(
                ok=False, key=self.key, element=None,
                message=f"Overlay element id {eid!r} already exists",
            )
        element = OverlayElement(
            id=eid, type=self.type,  # type: ignore[arg-type]
            x=self.x, y=self.y, color=self.color, size=self.size,
            bold=self.bold, italic=self.italic, text=self.text,
            metric=self.metric, format=self.format,
            source=self.source,  # type: ignore[arg-type]
        )
        app.settings.add_user_overlay_element(self.key, element)
        app.display.invalidate(self.key)
        app.events.publish(OverlayChanged(key=self.key, enabled=True))
        return OverlayElementResult(
            ok=True, key=self.key, element=_element_to_entry(element),
            message=f"Added overlay element {eid}",
        )


@dataclass(frozen=True, slots=True)
class UpdateOverlayElement(Command[OverlayElementResult]):
    """Mutate fields on an existing user-edited overlay element."""
    key: str
    element_id: str
    x: int | None = None
    y: int | None = None
    color: str | None = None
    size: int | None = None
    bold: bool | None = None
    italic: bool | None = None
    text: str | None = None
    metric: str | None = None
    format: str | None = None
    source: str | None = None

    def execute(self, app: App) -> OverlayElementResult:
        try:
            element = app.settings.update_user_overlay_element(
                self.key, self.element_id,
                x=self.x, y=self.y, color=self.color, size=self.size,
                bold=self.bold, italic=self.italic, text=self.text,
                metric=self.metric, format=self.format, source=self.source,
            )
        except KeyError as e:
            return OverlayElementResult(
                ok=False, key=self.key, element=None, message=str(e),
            )
        app.display.invalidate(self.key)
        app.events.publish(OverlayChanged(key=self.key, enabled=True))
        return OverlayElementResult(
            ok=True, key=self.key, element=_element_to_entry(element),
            message=f"Updated overlay element {self.element_id}",
        )


@dataclass(frozen=True, slots=True)
class DeleteOverlayElement(Command[OverlayElementDeleteResult]):
    """Remove a user-edited overlay element by id."""
    key: str
    element_id: str

    def execute(self, app: App) -> OverlayElementDeleteResult:
        try:
            app.settings.delete_user_overlay_element(self.key, self.element_id)
        except KeyError as e:
            return OverlayElementDeleteResult(
                ok=False, key=self.key, element_id=self.element_id,
                message=str(e),
            )
        app.display.invalidate(self.key)
        app.events.publish(OverlayChanged(key=self.key, enabled=True))
        return OverlayElementDeleteResult(
            ok=True, key=self.key, element_id=self.element_id,
            message=f"Deleted overlay element {self.element_id}",
        )


@dataclass(frozen=True, slots=True)
class FlashOverlayElement(Command[OverlayElementResult]):
    """Briefly highlight one element so the user can locate it on screen.

    Implementation note: the flash is a UI affordance, not a wire-level
    blink.  The Command returns the element (with its current state) and
    publishes an ``OverlayChanged`` event with ``flash_element_id`` set;
    the GUI subscribes and animates a highlight box for ``duration_ms``.
    Headless UIs that ignore the event get no visible effect — that's
    correct (CLI/API users have nothing to flash *at*).
    """
    key: str
    element_id: str
    duration_ms: int = 1500

    def execute(self, app: App) -> OverlayElementResult:
        elements = app.settings.for_device(self.key).user_overlay_elements
        for e in elements:
            if e.id == self.element_id:
                app.events.publish(OverlayChanged(
                    key=self.key, enabled=True,
                    flash_element_id=self.element_id,
                    flash_duration_ms=self.duration_ms,
                ))
                return OverlayElementResult(
                    ok=True, key=self.key, element=_element_to_entry(e),
                    message=f"Flashing overlay element {self.element_id} "
                            f"for {self.duration_ms}ms",
                )
        return OverlayElementResult(
            ok=False, key=self.key, element=None,
            message=f"Overlay element {self.element_id!r} not found",
        )


@dataclass(frozen=True, slots=True)
class SetOverlayConfig(Command[OverlayConfigResult]):
    """Replace the user-overlay layer wholesale.

    Useful when the GUI ships a full edit (drag-out from a panel).
    Each ``elements`` entry is a flat dict matching ``OverlayElement.to_dict``.
    """
    key: str
    elements: tuple[dict, ...] = ()

    def execute(self, app: App) -> OverlayConfigResult:
        parsed: list[OverlayElement] = []
        for raw in self.elements:
            element = OverlayElement.from_dict(dict(raw))
            if not element.id:
                return OverlayConfigResult(
                    ok=False, key=self.key, elements=[],
                    message="Every element must carry an id",
                )
            if element.type not in ("text", "metric", "clock"):
                return OverlayConfigResult(
                    ok=False, key=self.key, elements=[],
                    message=f"Invalid element type {element.type!r}",
                )
            parsed.append(element)
        app.settings.set_user_overlay_elements(self.key, parsed)
        app.display.invalidate(self.key)
        app.events.publish(OverlayChanged(key=self.key, enabled=True))
        return OverlayConfigResult(
            ok=True, key=self.key,
            elements=[_element_to_entry(e) for e in parsed],
            message=f"Overlay set to {len(parsed)} element(s)",
        )


# =========================================================================
# Tier 3 — video transport + theme/mask CRUD + assorted listings
# =========================================================================


@dataclass(frozen=True, slots=True)
class PauseVideo(Command[PauseVideoResult]):
    """Toggle the per-device video playback pause flag."""
    key: str
    paused: bool

    def execute(self, app: App) -> PauseVideoResult:
        playback = app.media.playback(self.key)
        if playback is None:
            return PauseVideoResult(
                ok=False, key=self.key, paused=self.paused,
                message=f"No active video playback for {self.key}",
            )
        playback.pause(self.paused)
        state = "paused" if self.paused else "playing"
        return PauseVideoResult(
            ok=True, key=self.key, paused=self.paused,
            message=f"Video {state}",
        )


@dataclass(frozen=True, slots=True)
class SeekVideo(Command[SeekVideoResult]):
    """Jump the playback cursor to a specific frame."""
    key: str
    frame: int

    def execute(self, app: App) -> SeekVideoResult:
        playback = app.media.playback(self.key)
        if playback is None:
            return SeekVideoResult(
                ok=False, key=self.key, cursor=0, frame_count=0,
                message=f"No active video playback for {self.key}",
            )
        if self.frame < 0:
            return SeekVideoResult(
                ok=False, key=self.key,
                cursor=playback.cursor, frame_count=playback.frame_count,
                message=f"frame must be >= 0, got {self.frame}",
            )
        playback.seek(self.frame)
        app.display.invalidate(self.key)
        return SeekVideoResult(
            ok=True, key=self.key,
            cursor=playback.cursor, frame_count=playback.frame_count,
            message=f"Seeked to frame {playback.cursor}",
        )


@dataclass(frozen=True, slots=True)
class LoopVideo(Command[LoopVideoResult]):
    """Toggle whether playback wraps to frame 0 or sticks at the last frame."""
    key: str
    loop: bool

    def execute(self, app: App) -> LoopVideoResult:
        playback = app.media.playback(self.key)
        if playback is None:
            return LoopVideoResult(
                ok=False, key=self.key, loop=self.loop,
                message=f"No active video playback for {self.key}",
            )
        playback.set_loop(self.loop)
        state = "looping" if self.loop else "single-pass"
        return LoopVideoResult(
            ok=True, key=self.key, loop=self.loop,
            message=f"Video set to {state}",
        )


@dataclass(frozen=True, slots=True)
class DeleteTheme(Command[DeleteThemeResult]):
    """Delete a theme directory under user_content_dir."""
    name: str

    def execute(self, app: App) -> DeleteThemeResult:
        root = app.platform.paths().user_content_dir()
        try:
            target = app.themes.delete(root, self.name)
        except ThemeError as e:
            return DeleteThemeResult(
                ok=False, theme_name=self.name, path="",
                message=str(e),
            )
        return DeleteThemeResult(
            ok=True, theme_name=self.name, path=str(target),
            message=f"Deleted theme {self.name!r}",
        )


@dataclass(frozen=True, slots=True)
class UploadCustomMask(Command[MaskUploadResult]):
    """Copy a mask image into the user-mask dir for the device's resolution.

    Writes the source under ``paths.user_mask_dir(w, h)/custom_<name>/``
    as both ``01.png`` (the canonical mask renderers consume) and
    ``Theme.png`` (the preview thumbnail Thermalright's cloud masks
    use).  Matches legacy's mask-on-disk shape so cloud + user masks
    coexist and the legacy mask browser picks them both up.

    Then dispatches ApplyMask so the new mask wires onto the device.
    """
    key: str
    source: Path

    def execute(self, app: App) -> MaskUploadResult:
        import shutil

        if not self.source.is_file():
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=f"Source not a file: {self.source}",
            )
        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=(f"Cannot resolve resolution for {self.key} — "
                         "connect the device or register the product first"),
            )
        masks_root = app.platform.paths().user_mask_dir(*resolution)
        mask_dir = masks_root / f"custom_{self.source.stem}"
        try:
            mask_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=f"Failed to ensure masks dir: {e}",
            )
        mask_file = mask_dir / _LEGACY_MASK_FILENAME
        preview_file = mask_dir / "Theme.png"
        try:
            shutil.copy2(self.source, mask_file)
            # Preview = mask itself.  Legacy's cloud catalog ships a
            # distinct Theme.png, but user uploads only have the one
            # image; copying it satisfies legacy-port browsers that
            # gate visibility on Theme.png existence.
            shutil.copy2(self.source, preview_file)
        except OSError as e:
            return MaskUploadResult(
                ok=False, key=self.key, path="",
                message=f"Copy failed: {e}",
            )
        apply_result = ApplyMask(key=self.key, path=mask_file).execute(app)
        if not apply_result.ok:
            return MaskUploadResult(
                ok=False, key=self.key, path=str(mask_file),
                message=f"Uploaded but apply failed: {apply_result.message}",
            )
        return MaskUploadResult(
            ok=True, key=self.key, path=str(mask_file),
            message=f"Mask uploaded + applied: {mask_dir.name}",
        )


@dataclass(frozen=True, slots=True)
class ListMasks(Command[MasksListResult]):
    """Enumerate masks for a device resolution.

    With ``resolution=(w, h)`` (default for the GUI), scans both the
    cloud-downloaded mask dir (``paths.cloud_mask_dir``) and the
    user-created mask dir (``paths.user_mask_dir``).

    With ``directory=``, scans that exact dir (escape hatch for tests
    and CLI use).

    Masks come in two on-disk shapes:

    * **Legacy directory** — ``<root>/<id>/01.png`` (cloud catalog +
      ``UploadCustomMask``).  Returned with ``name=<id>``, ``path=01.png``.
    * **Flat image** — ``<root>/<name>.png`` (forward-compat for any
      future flat layout).  Returned with ``name=<filename>``.
    """
    resolution: tuple[int, int] | None = None
    directory: Path | None = None

    def execute(self, app: App) -> MasksListResult:
        if self.directory is not None:
            roots = [self.directory]
        elif self.resolution is not None:
            paths = app.platform.paths()
            w, h = self.resolution
            roots = [paths.cloud_mask_dir(w, h), paths.user_mask_dir(w, h)]
        else:
            return MasksListResult(
                ok=False, directory="", masks=[],
                message="ListMasks requires resolution=(w,h) or directory=...",
            )

        seen: set[Path] = set()
        entries: list[FileEntry] = []
        for root in roots:
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                resolved = _resolve_mask_path(entry)
                if resolved is None or resolved in seen:
                    continue
                seen.add(resolved)
                # For legacy subdirs, surface the subdir name (e.g.
                # "000a") so users see the catalog id, not "01.png".
                display_name = entry.name if entry.is_dir() else resolved.name
                entries.append(FileEntry(name=display_name, path=str(resolved)))
        target_str = "; ".join(str(r) for r in roots)
        return MasksListResult(
            ok=True, directory=target_str, masks=entries,
            message=f"{len(entries)} mask(s) under {target_str}",
        )


@dataclass(frozen=True, slots=True)
class ListFonts(Command[FontsListResult]):
    """List font families Qt can find on the system.

    Uses ``QFontDatabase.families()`` — same source the GUI uses for
    its font picker.  Returns an empty list (not an error) when Qt
    isn't initialised, so headless callers can probe safely.
    """

    def execute(self, app: App) -> FontsListResult:
        del app
        fonts: list[str] = []
        try:
            from PySide6.QtGui import QFontDatabase  # type: ignore[import-not-found]
        except ImportError:
            return FontsListResult(
                ok=True, fonts=[],
                message="Qt not available — no fonts enumerable",
            )
        try:
            fonts = sorted(QFontDatabase.families())
        except RuntimeError as e:
            return FontsListResult(
                ok=False, fonts=[],
                message=f"QFontDatabase error: {e}",
            )
        return FontsListResult(
            ok=True, fonts=fonts,
            message=f"{len(fonts)} font(s)",
        )


@dataclass(frozen=True, slots=True)
class ListDisks(Command[DisksListResult]):
    """Enumerate disks via psutil — used by SetDiskIndex callers."""

    def execute(self, app: App) -> DisksListResult:
        del app
        disks: list[DiskEntry] = []
        try:
            import psutil  # type: ignore[import-untyped]
        except ImportError:
            return DisksListResult(
                ok=True, disks=[],
                message="psutil not available — no disk enumeration",
            )
        try:
            partitions = psutil.disk_partitions(all=False)
        except (OSError, RuntimeError) as e:
            return DisksListResult(
                ok=False, disks=[],
                message=f"disk enumeration failed: {e}",
            )
        for index, p in enumerate(partitions):
            disks.append(DiskEntry(
                index=index, device=p.device, mountpoint=p.mountpoint,
            ))
        return DisksListResult(
            ok=True, disks=disks,
            message=f"{len(disks)} disk(s)",
        )


# =========================================================================
# Listings (read-only — registries + enums)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ListLedStyles(Command[LedStylesListResult]):
    """Enumerate every LED style the PM registry can resolve."""

    def execute(self, app: App) -> LedStylesListResult:
        del app
        from .led_protocol import _PM_REGISTRY
        styles = [
            LedStyleEntry(
                style=entry.style.value,
                model_name=entry.model_name,
                pm_byte=pm,
                style_sub=entry.style_sub,
            )
            for pm, entry in sorted(_PM_REGISTRY.items())
        ]
        return LedStylesListResult(
            ok=True, styles=styles,
            message=f"{len(styles)} style entry(ies)",
        )


@dataclass(frozen=True, slots=True)
class ListLedModes(Command[LedModesListResult]):
    """Enumerate the LEDMode enum names (STATIC, BREATHING, RAINBOW, …)."""

    def execute(self, app: App) -> LedModesListResult:
        del app
        modes = [m.name for m in LEDMode]
        return LedModesListResult(
            ok=True, modes=modes,
            message=f"{len(modes)} mode(s)",
        )


@dataclass(frozen=True, slots=True)
class ListGpus(Command[GpusListResult]):
    """Enumerate every GPU the sensors aggregator exposes."""

    def execute(self, app: App) -> GpusListResult:
        sensors = app.platform.sensors()
        # The BaselineSensors aggregator stores GpuSource entries directly;
        # not every aggregator has the same shape, so duck-type the .gpus
        # attribute and fall back to a sensor-descriptor scan otherwise.
        gpu_objs = getattr(sensors, "_gpus", None) or getattr(sensors, "gpus", None)
        gpus: list[GpuEntry] = []
        if gpu_objs is not None:
            for g in gpu_objs:
                gpus.append(GpuEntry(
                    key=g.key, name=g.name, is_discrete=g.is_discrete,
                ))
        return GpusListResult(
            ok=True, gpus=gpus,
            message=f"{len(gpus)} GPU(s) detected",
        )


# =========================================================================
# Snapshots (read-only state dumps)
# =========================================================================


@dataclass(frozen=True, slots=True)
class LcdSnapshot(Command[LcdSnapshotResult]):
    """Per-device LCD state snapshot — what settings.for_device holds."""
    key: str

    def execute(self, app: App) -> LcdSnapshotResult:
        s = app.settings.for_device(self.key)
        return LcdSnapshotResult(
            ok=True, key=self.key,
            orientation=s.orientation,
            brightness=s.brightness,
            current_theme=s.current_theme,
            overlay_enabled=s.overlay_enabled,
            mask_path=s.mask_path,
            mask_visible=s.mask_visible,
            mask_position=s.mask_position,
            fit_mode=s.fit_mode.value,
            split_mode=s.split_mode,
            time_format=s.time_format,
            date_format=s.date_format,
            temp_unit=s.temp_unit,
            message=f"LCD snapshot for {self.key}",
        )


@dataclass(frozen=True, slots=True)
class LedSnapshot(Command[LedSnapshotResult]):
    """Per-device LED state snapshot."""
    key: str

    def execute(self, app: App) -> LedSnapshotResult:
        s = app.settings.for_led(self.key)
        return LedSnapshotResult(
            ok=True, key=self.key,
            mode=s.mode.name,
            color=s.color,
            brightness=s.brightness,
            global_on=s.global_on,
            test_mode=s.test_mode,
            temp_source=s.temp_source,
            load_source=s.load_source,
            zone_sync=s.zone_sync,
            zone_sync_interval_ticks=s.zone_sync_interval_ticks,
            selected_zone=s.selected_zone,
            zone_count=len(s.zones),
            segment_count=len(s.segment_on),
            message=f"LED snapshot for {self.key}",
        )


@dataclass(frozen=True, slots=True)
class ControlCenterSnapshot(Command[ControlCenterSnapshotResult]):
    """App-wide settings snapshot."""

    def execute(self, app: App) -> ControlCenterSnapshotResult:
        a = app.settings.app
        return ControlCenterSnapshotResult(
            ok=True,
            language=a.language,
            temp_unit=a.temp_unit,
            active_device=a.active_device,
            active_gpu=a.active_gpu,
            refresh_interval_s=a.refresh_interval_s,
            hdd_enabled=a.hdd_enabled,
            message="App settings snapshot",
        )


# =========================================================================
# Theme — restore last loaded theme
# =========================================================================


@dataclass(frozen=True, slots=True)
class RestoreLastTheme(Command[ThemeResult]):
    """Re-load the theme persisted in Settings for *key*.

    Convenience wrapper around LoadTheme — looks up the last
    ``current_theme`` for this device and re-dispatches LoadTheme so
    the render pipeline catches up on connect or after a restart.

    ``current_theme`` is normally the theme's absolute path (written by
    LoadTheme).  Legacy values can be bare theme names like
    ``"image:00"`` or ``"Custom_Theme1"`` — those trigger a heuristic
    search across the device's known theme roots so existing users
    don't lose their last selection on upgrade.
    """
    key: str

    def execute(self, app: App) -> ThemeResult:
        settings = app.settings.for_device(self.key)
        stored = settings.current_theme
        if not stored:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"No persisted theme for {self.key}",
            )

        # Absolute or already-resolvable path → use it directly.
        candidate = Path(stored)
        if candidate.is_dir():
            return LoadTheme(key=self.key, path=candidate).execute(app)

        # Legacy bare-name value — search the known theme roots.
        resolved = _search_theme_by_name(app, self.key, stored)
        if resolved is None:
            return ThemeResult(
                ok=False, key=self.key, theme_name=stored,
                message=(f"Persisted theme {stored!r} not found in any "
                         "known theme root for this device"),
            )
        return LoadTheme(key=self.key, path=resolved).execute(app)


def _search_theme_by_name(
    app: App, key: str, name: str,
) -> Path | None:
    """Locate a theme directory by name across this device's roots.

    Used by RestoreLastTheme to recover legacy ``current_theme`` values
    (display names like ``"image:00"``, ``"Custom_Theme1"``) that
    pre-date persisting the absolute path.

    Search order:
      1. ``theme_dir(w,h)/<name>``           — pkg + GitHub-downloaded
      2. ``user_theme_dir(w,h)/<name>``      — legacy user-saved layout
      3. ``user_content_dir()/<name>``       — next/ flat user saves
      4. ``user_content_dir()/single-image/<name_after_image_prefix>``
      5. ``cloud_theme_dir(w,h)/<name>``     — cloud cache

    Each candidate must be a directory containing a theme config
    (``trcc.json`` or ``config1.dc``) — guarded by
    ``ThemeService`` semantics.
    """
    paths = app.platform.paths()
    resolution = _resolve_resolution(app, key)
    candidates: list[Path] = []
    if resolution is not None:
        w, h = resolution
        candidates.append(paths.theme_dir(w, h) / name)
        candidates.append(paths.user_theme_dir(w, h) / name)
        candidates.append(paths.cloud_theme_dir(w, h) / name)
    candidates.append(paths.user_content_dir() / name)
    # "image:foo" → single-image/foo (LoadImage layout).
    if name.startswith("image:"):
        candidates.append(
            paths.user_content_dir() / "single-image" / name[len("image:"):],
        )
    from ..services.theme import _has_theme_marker
    for c in candidates:
        if c.is_dir() and _has_theme_marker(c):
            return c
    return None


# =========================================================================
# Cloud themes — list + load (downloads + applies)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ListCloudThemes(Command[CloudThemesListResult]):
    """List themes available in Thermalright's hosted catalog.

    Pass ``category="a"`` (or any registered prefix) to scope the list,
    or ``"all"`` (the default) for everything.  Pure read — no network
    until ``LoadCloudTheme`` runs, since the catalog itself is static.
    """
    category: str = "all"

    def execute(self, app: App) -> CloudThemesListResult:
        try:
            themes = app.cloud_themes.list_themes(self.category)
        except ValueError as e:
            return CloudThemesListResult(
                ok=False, category=self.category, message=str(e),
            )
        categories = [
            CloudCategoryEntry(prefix=c.prefix, name=c.name, count=c.count)
            for c in app.cloud_themes.categories()
        ]
        entries = [
            CloudThemeEntryResult(
                id=t.id, category=t.category, category_name=t.category_name,
            )
            for t in themes
        ]
        return CloudThemesListResult(
            ok=True, category=self.category,
            categories=categories, themes=entries,
            message=f"{len(entries)} cloud theme(s) in {self.category!r}",
        )


@dataclass(frozen=True, slots=True)
class LoadCloudTheme(Command[CloudThemeLoadResult]):
    """Download a cloud theme and load it on a device.

    Materialises the cloud MP4 into a per-resolution theme directory
    under ``paths.cloud_theme_dir(w, h) / <theme_id>`` (idempotent —
    subsequent calls are no-ops if the dir already exists), then
    dispatches LoadTheme to render the first frame.
    """
    key: str
    theme_id: str

    def execute(self, app: App) -> CloudThemeLoadResult:
        from ..adapters.repo.http import HttpFetchError
        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=(f"Cannot resolve resolution for {self.key} — "
                         "connect the device or register the product first"),
            )
        try:
            theme_dir = app.cloud_themes.materialise(
                self.theme_id, resolution,
            )
        except ValueError as e:
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=str(e),
            )
        except HttpFetchError as e:
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=f"Cloud download failed: {e}",
            )
        except OSError as e:
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=f"Local IO failed: {e}",
            )

        load_result = LoadTheme(key=self.key, path=theme_dir).execute(app)
        return CloudThemeLoadResult(
            ok=load_result.ok,
            key=self.key,
            theme_id=self.theme_id,
            theme_path=str(theme_dir),
            message=load_result.message,
        )


# =========================================================================
# Sensors
# =========================================================================


@dataclass(frozen=True, slots=True)
class ReadSensors(Command[SensorsResult]):
    """Return current sensor readings.

    Pulls descriptor metadata (label / unit / category) from
    `discover()` and fresh values from `read_all()`, then merges the
    two so every returned `SensorReading` carries the current value.
    """

    def execute(self, app: App) -> SensorsResult:
        from .models import SensorReading
        enum = app.platform.sensors()
        descriptors = enum.discover()
        current = enum.read_all()
        readings = [
            SensorReading(
                sensor_id=d.sensor_id,
                category=d.category,
                value=current.get(d.sensor_id, 0.0),
                unit=d.unit,
                label=d.label,
            )
            for d in descriptors
        ]
        return SensorsResult(
            ok=True,
            message=f"{len(readings)} sensor(s)",
            readings=readings,
        )


# =========================================================================
# System
# =========================================================================


@dataclass(frozen=True, slots=True)
class RunSetup(Command[SetupResult]):
    """OS-specific one-time setup (udev, WinUSB guide, etc.)."""
    interactive: bool = True

    def execute(self, app: App) -> SetupResult:
        warnings = app.platform.check_permissions()
        code = app.platform.setup(interactive=self.interactive)
        return SetupResult(
            ok=code == 0,
            message=f"Setup completed with exit code {code}",
            exit_code=code,
            warnings=warnings,
        )


# =========================================================================
# Diagnostics — health, doctor, debug report
# =========================================================================


def _health_entries(checks: list) -> list[HealthCheckEntry]:
    """Map adapter HealthCheckResult → Result-layer HealthCheckEntry."""
    return [
        HealthCheckEntry(
            name=c.name, severity=c.severity,
            message=c.message, fix_hint=c.fix_hint,
        )
        for c in checks
    ]


@dataclass(frozen=True, slots=True)
class RunHealthCheck(Command[HealthReportResult]):
    """Run the full health check suite and return the structured report.

    Cheap (every check times out fast) — safe to call from a GUI panel
    on a refresh button.
    """

    def execute(self, app: App) -> HealthReportResult:
        from ..adapters.diagnostics.health import run_health_checks
        report = run_health_checks(app.platform)
        return HealthReportResult(
            ok=report.fail_count == 0,
            checks=_health_entries(report.checks),
            fail_count=report.fail_count,
            warn_count=report.warn_count,
            worst_severity=report.worst_severity,
            message=(f"{report.fail_count} fail / {report.warn_count} warn"
                     f" / {len(report.checks)} checks"),
        )


@dataclass(frozen=True, slots=True)
class RunDoctor(Command[DoctorResultPayload]):
    """Run health checks + render a CLI-friendly summary + exit code."""

    def execute(self, app: App) -> DoctorResultPayload:
        from ..adapters.diagnostics.doctor import (
            render_doctor_output,
            run_doctor,
        )
        doctor = run_doctor(app.platform)
        return DoctorResultPayload(
            ok=doctor.is_healthy,
            checks=_health_entries(doctor.report.checks),
            fail_count=doctor.report.fail_count,
            warn_count=doctor.report.warn_count,
            exit_code=doctor.exit_code,
            rendered=render_doctor_output(doctor.report),
            message=("Healthy" if doctor.is_healthy
                     else f"{doctor.report.fail_count} check(s) failed"),
        )


@dataclass(frozen=True, slots=True)
class GenerateDebugReport(Command[DebugReportPayload]):
    """Build a debug report bundle for the user to paste into a GitHub issue.

    With ``output_path`` set, the bundle lands on disk at that path and
    the rendered text comes back in the result.  With no output_path,
    the bundle is rendered into memory only — useful for the API to
    return the text body directly.
    """
    output_path: Path | None = None
    log_tail_lines: int = 1000

    def execute(self, app: App) -> DebugReportPayload:
        from ..adapters.diagnostics.debug_report import (
            build_debug_report,
            write_debug_report,
        )
        report = build_debug_report(
            app.platform, log_tail_lines=self.log_tail_lines,
        )
        rendered = report.render_text()
        out: str = ""
        if self.output_path is not None:
            try:
                written = write_debug_report(report, self.output_path)
            except OSError as e:
                return DebugReportPayload(
                    ok=False, output_path=str(self.output_path),
                    rendered_text=rendered,
                    message=f"Generated report but write failed: {e}",
                )
            out = str(written)
        return DebugReportPayload(
            ok=True, output_path=out, rendered_text=rendered,
            message=(f"Wrote debug report to {out}" if out
                     else "Generated debug report (in-memory)"),
        )


# =========================================================================
# Update check + upgrade
# =========================================================================


@dataclass(frozen=True, slots=True)
class RunQuickstart(Command[QuickstartResult]):
    """Walk the new-user happy path: doctor → scan.

    Each step's outcome is returned as a structured entry so any UI
    renders the same sequence.  Stops at the first FAIL.  Doesn't
    attempt a hardware handshake on its own — callers decide whether
    to ``ConnectDevice`` on the first-found device based on user
    confirmation.
    """

    def execute(self, app: App) -> QuickstartResult:
        report = app.quickstart.run_all()
        steps = [
            QuickstartStepEntry(
                name=s.name, status=s.status,
                message=s.message, next_step_hint=s.next_step_hint,
            )
            for s in report.steps
        ]
        return QuickstartResult(
            ok=report.completed_ok or not report.failed_step,
            steps=steps,
            completed_ok=report.completed_ok,
            device_key=report.device_key_connected,
            message=(
                "Quickstart complete." if report.completed_ok
                else (
                    f"Quickstart stopped at: {report.failed_step.name}"
                    if report.failed_step
                    else "Quickstart finished with warnings."
                )
            ),
        )


_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


@dataclass(frozen=True, slots=True)
class LoadImage(Command[ThemeResult]):
    """Show one image on the LCD as the background of a single-image theme.

    Smallest path for "show me this picture" — internally materialises
    a one-file theme directory under
    ``user_content_dir/single-image/<filename>`` then dispatches
    ``LoadTheme``.  Idempotent: re-running with the same image re-uses
    the staged directory rather than re-creating it.

    Errors surface as structured Results.  Acceptable extensions:
    PNG / JPG / JPEG / BMP / WEBP.
    """
    key: str
    path: Path

    def execute(self, app: App) -> ThemeResult:
        if not self.path.is_file():
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Image file not found: {self.path}.  "
                    "Check the path and try again."
                ),
            )
        if self.path.suffix.lower() not in _IMAGE_EXTS:
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Unsupported image extension {self.path.suffix!r}.  "
                    f"Supported: {', '.join(sorted(_IMAGE_EXTS))}."
                ),
            )
        import json
        import shutil

        # Stage the image as a minimal next/-shape theme under a stable
        # subdirectory so re-runs are cheap and the theme appears in
        # `theme list` like any other.
        target_root = app.platform.paths().user_content_dir() / "single-image"
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Cannot create single-image theme directory: {e}",
            )
        theme_name = self.path.stem
        theme_dir = target_root / theme_name
        if theme_dir.exists() and not theme_dir.is_dir():
            theme_dir = (
                target_root / f"{theme_name}-from-{self.path.parent.name}"
            )
        try:
            theme_dir.mkdir(parents=True, exist_ok=True)
            target_image = theme_dir / self.path.name
            if (
                not target_image.is_file()
                or target_image.stat().st_size != self.path.stat().st_size
            ):
                shutil.copy2(self.path, target_image)
            config_path = theme_dir / "trcc.json"
            if not config_path.is_file():
                config_path.write_text(
                    json.dumps({
                        "name": f"image:{theme_name}",
                        "elements": [],
                    }, indent=2) + "\n",
                    encoding="utf-8",
                )
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Failed to stage image as theme: {e}",
            )
        return LoadTheme(key=self.key, path=theme_dir).execute(app)


_VIDEO_EXTS_FOR_LOAD = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt",
})


@dataclass(frozen=True, slots=True)
class LoadVideo(Command[ThemeResult]):
    """Play a video on the LCD as a single-video theme.

    Conceptually parallel to :class:`LoadImage`: turn an arbitrary file
    into a one-shot theme directory + dispatch :class:`LoadTheme`.

    For ``.zt`` inputs the source is copied straight in.  For real video
    files (``.mp4``, ``.mov``, ``.webm``, etc.) the file is transcoded
    into a ``Theme.zt`` via :class:`VideoExporter`, sized to the
    device's native resolution and optionally clipped to ``start_ms`` →
    ``end_ms``.

    The device must be attached (so we know its native resolution).
    Errors surface as structured Results — never exceptions to UIs.
    """
    key: str
    path: Path
    start_ms: int = 0
    end_ms: int | None = None
    rotation: int = 0

    def execute(self, app: App) -> ThemeResult:
        if not self.path.is_file():
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Video file not found: {self.path}.  "
                    "Check the path and try again."
                ),
            )
        if self.path.suffix.lower() not in _VIDEO_EXTS_FOR_LOAD:
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Unsupported video extension {self.path.suffix!r}.  "
                    f"Supported: {', '.join(sorted(_VIDEO_EXTS_FOR_LOAD))}."
                ),
            )

        target_w, target_h = self._resolve_target_size(app)
        if target_w == 0 or target_h == 0:
            return ThemeResult(
                ok=False, key=self.key,
                message=(
                    f"Don't know the target resolution for {self.key}.  "
                    "Connect the device first (or pass a key that matches "
                    "a row in the product registry)."
                ),
            )

        import json
        import shutil

        target_root = app.platform.paths().user_content_dir() / "single-video"
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Cannot create single-video theme directory: {e}",
            )
        theme_name = self.path.stem
        theme_dir = target_root / theme_name
        if theme_dir.exists() and not theme_dir.is_dir():
            theme_dir = (
                target_root / f"{theme_name}-from-{self.path.parent.name}"
            )
        try:
            theme_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ThemeResult(
                ok=False, key=self.key,
                message=f"Cannot create theme directory: {e}",
            )

        # Either copy a .zt straight in or transcode the source.
        zt_target = theme_dir / "Theme.zt"
        if self.path.suffix.lower() == ".zt":
            try:
                if (
                    not zt_target.is_file()
                    or zt_target.stat().st_size != self.path.stat().st_size
                ):
                    shutil.copy2(self.path, zt_target)
            except OSError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Failed to stage .zt as theme: {e}",
                )
        else:
            from ..services.video_export import (
                VideoExporter,
                VideoExportError,
                VideoExportRequest,
                probe_duration_ms,
            )
            end_ms = self.end_ms
            if end_ms is None:
                probed = probe_duration_ms(self.path)
                end_ms = probed if probed > 0 else self.start_ms + 10_000
            request = VideoExportRequest(
                source=self.path,
                start_ms=self.start_ms,
                end_ms=end_ms,
                target_w=target_w,
                target_h=target_h,
                rotation=self.rotation,
            )
            try:
                produced = VideoExporter().export_zt(request)
            except VideoExportError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Video export failed: {e}",
                )
            try:
                shutil.move(str(produced), str(zt_target))
                # Clean up the now-empty temp dir VideoExporter created.
                temp_parent = produced.parent
                shutil.rmtree(temp_parent, ignore_errors=True)
            except OSError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Failed to install Theme.zt into theme dir: {e}",
                )

        config_path = theme_dir / "trcc-next.json"
        if not config_path.is_file():
            try:
                config_path.write_text(
                    json.dumps({
                        "name": f"video:{theme_name}",
                        "elements": [],
                    }, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as e:
                return ThemeResult(
                    ok=False, key=self.key,
                    message=f"Failed to write theme config: {e}",
                )
        return LoadTheme(key=self.key, path=theme_dir).execute(app)

    def _resolve_target_size(self, app: App) -> tuple[int, int]:
        """Prefer an attached device's profile, fall back to the registry."""
        device = app.devices.get(self.key)
        if device is not None:
            if device.profile is not None:
                return device.profile.resolution
            if device.info.native_resolution != (0, 0):
                return device.info.native_resolution
        # Pre-attach: parse vid/pid and ask the registry directly so
        # users can stage video themes ahead of plugging in the device.
        try:
            vid_s, pid_s = self.key.split(":")
            vid = int(vid_s, 16)
            pid = int(pid_s, 16)
        except ValueError:
            return (0, 0)
        product = find_product(vid, pid)
        if product is None:
            return (0, 0)
        return product.native_resolution


@dataclass(frozen=True, slots=True)
class ResetDevice(Command[DisconnectResult]):
    """Disconnect + drop cached state for a device.

    Equivalent of legacy's ``reset()`` — gives users a clean slate
    without having to remember to call disconnect explicitly.  Re-runs
    of ConnectDevice after this start with no cached frame or theme.
    """
    key: str

    def execute(self, app: App) -> DisconnectResult:
        if self.key not in app.devices:
            return DisconnectResult(
                ok=False, key=self.key,
                message=f"{self.key} is not attached — nothing to reset.",
            )
        app.detach(self.key)
        app.events.publish(DeviceDisconnected(key=self.key))
        return DisconnectResult(
            ok=True, key=self.key,
            message=f"Reset {self.key} — caches cleared, theme dropped.",
        )


@dataclass(frozen=True, slots=True)
class GetFirstRunStatus(Command[FirstRunStatusResult]):
    """Has trcc-next finished onboarding on this machine?

    GUI uses this on launch to decide whether to surface a welcome
    screen; CLI users see it via ``trcc-next system first-run-status``.
    """

    def execute(self, app: App) -> FirstRunStatusResult:
        return FirstRunStatusResult(
            ok=True,
            is_first_run=app.first_run.is_first_run(),
            marker_path=str(app.first_run.marker_path),
            message=(
                "Welcome — looks like this is your first run."
                if app.first_run.is_first_run()
                else "Setup already completed previously."
            ),
        )


@dataclass(frozen=True, slots=True)
class MarkFirstRunDone(Command[FirstRunStatusResult]):
    """Tell next/ the onboarding flow has been completed.

    GUI calls this after the welcome panel; CLI users almost never
    need to call it directly (the doctor / setup commands could choose
    to mark it, but we keep that intentional rather than implicit).
    """

    def execute(self, app: App) -> FirstRunStatusResult:
        app.first_run.mark_completed()
        return FirstRunStatusResult(
            ok=True, is_first_run=False,
            marker_path=str(app.first_run.marker_path),
            message="First-run marker written.",
        )


@dataclass(frozen=True, slots=True)
class CheckForUpdate(Command[UpdateCheckResult]):
    """Ask GitHub Releases whether a newer trcc-linux is available.

    Network call — uses the App's shared HttpFetcher.  Comparison is
    coarse semver (X.Y.Z) tolerant of v-prefix and pre-release suffixes.
    """

    def execute(self, app: App) -> UpdateCheckResult:
        from .. import __version__ as next_version_module
        from ..adapters.repo.github_releases import is_newer
        from ..adapters.repo.http import HttpFetchError

        local = getattr(next_version_module, "__version__", "0.0.0")
        try:
            latest = app.github_releases.latest()
        except HttpFetchError as e:
            return UpdateCheckResult(
                ok=False, local_version=local,
                message=f"Update check failed: {e}",
            )
        available = is_newer(latest.version, local)
        msg = (
            f"Update available: {latest.tag} (you have {local})"
            if available
            else f"Up to date at {local}"
        )
        return UpdateCheckResult(
            ok=True,
            local_version=local,
            latest_version=latest.version,
            latest_tag=latest.tag,
            release_url=latest.html_url,
            update_available=available,
            message=msg,
        )


@dataclass(frozen=True, slots=True)
class RunUpgrade(Command[UpgradeResult]):
    """Run the OS package-manager upgrade for trcc-linux.

    Maps the detected package manager to the right command and spawns a
    subprocess.  We never pipe untrusted input — the argv is a fixed
    list per package manager.  ``dry_run=True`` returns the command
    without executing it so UIs can show the user what would run.
    """
    dry_run: bool = False

    def execute(self, app: App) -> UpgradeResult:
        del app
        import subprocess

        from ..adapters.diagnostics.health import detect_package_manager

        pm = detect_package_manager()
        if pm is None:
            return UpgradeResult(
                ok=False, package_manager="",
                message="No supported package manager detected on this system",
            )
        cmd = _UPGRADE_COMMANDS.get(pm)
        if cmd is None:
            return UpgradeResult(
                ok=False, package_manager=pm,
                message=f"No upgrade recipe for package manager {pm!r}",
            )
        if self.dry_run:
            return UpgradeResult(
                ok=True, package_manager=pm, command=list(cmd),
                message=f"Would run: {' '.join(cmd)}",
            )
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=600.0, check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            return UpgradeResult(
                ok=False, package_manager=pm, command=list(cmd),
                message=f"Upgrade subprocess failed: {type(e).__name__}: {e}",
            )
        return UpgradeResult(
            ok=proc.returncode == 0,
            package_manager=pm,
            command=list(cmd),
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            message=(f"Upgrade completed (exit {proc.returncode})"
                     if proc.returncode == 0
                     else f"Upgrade failed (exit {proc.returncode})"),
        )


_UPGRADE_COMMANDS: dict[str, tuple[str, ...]] = {
    "dnf":          ("sudo", "dnf", "upgrade", "-y", "trcc-linux"),
    "apt":          ("sudo", "apt", "upgrade", "-y", "trcc-linux"),
    "pacman":       ("sudo", "pacman", "-Syu", "--noconfirm", "trcc-linux"),
    "zypper":       ("sudo", "zypper", "update", "-y", "trcc-linux"),
    "xbps-install": ("sudo", "xbps-install", "-u", "trcc-linux"),
    "apk":          ("sudo", "apk", "upgrade", "trcc-linux"),
}


# =========================================================================
# Slideshow + keepalive
# =========================================================================


def _slideshow_snapshot(settings, key: str) -> SlideshowResult:
    s = settings.for_device(key)
    return SlideshowResult(
        ok=True, key=key,
        enabled=s.slideshow_enabled,
        interval_s=s.slideshow_interval_s,
        themes=list(s.slideshow_themes),
        message=(f"Slideshow {'on' if s.slideshow_enabled else 'off'} "
                 f"({len(s.slideshow_themes)} theme(s), "
                 f"every {s.slideshow_interval_s:.0f}s)"),
    )


@dataclass(frozen=True, slots=True)
class ConfigureSlideshow(Command[SlideshowResult]):
    """Set the slideshow theme list + interval for a device.

    Either field can be omitted to leave it untouched.  Resets the
    SlideshowService cursor so the next tick picks up the new list
    from index 0.
    """
    key: str
    themes: tuple[str, ...] | None = None
    interval_s: float | None = None

    def execute(self, app: App) -> SlideshowResult:
        if self.interval_s is not None and self.interval_s < 1.0:
            return SlideshowResult(
                ok=False, key=self.key,
                message=(f"interval_s must be >= 1, got {self.interval_s}"),
            )
        app.settings.configure_slideshow(
            self.key,
            themes=list(self.themes) if self.themes is not None else None,
            interval_s=self.interval_s,
        )
        app.slideshow.reset(self.key)
        return _slideshow_snapshot(app.settings, self.key)


@dataclass(frozen=True, slots=True)
class SetSlideshow(Command[SlideshowResult]):
    """Toggle the slideshow on or off without changing the theme list."""
    key: str
    enabled: bool

    def execute(self, app: App) -> SlideshowResult:
        app.settings.set_slideshow_enabled(self.key, self.enabled)
        if self.enabled:
            app.slideshow.reset(self.key)
        return _slideshow_snapshot(app.settings, self.key)


@dataclass(frozen=True, slots=True)
class KeepAliveLoop(Command[KeepaliveResult]):
    """Resend the device's last frame ``count`` times with ``interval_s`` gaps.

    Used for Bulk/LY firmware that loses the displayed image when the
    internal buffer ages out.  ``count=1`` does one resend immediately
    (useful as a tick action); larger counts run a tight loop for a
    fixed number of iterations.

    Foreground / blocking — CLI users see this as `trcc display
    keepalive <key>` and Ctrl-C it; the daemon dispatches it on a
    timer so it never blocks the event loop.
    """
    key: str
    count: int = 1
    interval_s: float = 5.0

    def execute(self, app: App) -> KeepaliveResult:
        import time

        if self.count < 1:
            return KeepaliveResult(
                ok=False, key=self.key,
                message=f"count must be >= 1, got {self.count}",
            )
        last = app.keepalive.last_frame(self.key)
        if last is None:
            return KeepaliveResult(
                ok=False, key=self.key,
                message=("No cached frame for keepalive — render at least "
                         "once before starting the loop"),
            )
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return KeepaliveResult(
                ok=False, key=self.key, message=str(e),
            )
        if not device.is_connected:
            return KeepaliveResult(
                ok=False, key=self.key,
                message=f"{self.key} not connected — dispatch ConnectDevice first",
            )

        frames_resent = 0
        bytes_resent = 0
        for i in range(self.count):
            if i > 0:
                time.sleep(max(0.0, self.interval_s))
            try:
                sent = device.send(last)
            except TransportError as e:
                return KeepaliveResult(
                    ok=False, key=self.key,
                    frames_resent=frames_resent,
                    bytes_resent=bytes_resent,
                    message=f"Keepalive send failed at iter {i}: {e}",
                )
            if not sent:
                return KeepaliveResult(
                    ok=False, key=self.key,
                    frames_resent=frames_resent,
                    bytes_resent=bytes_resent,
                    message=f"device.send returned False at iter {i}",
                )
            app.keepalive.mark_sent(self.key)
            frames_resent += 1
            bytes_resent += len(last)
        return KeepaliveResult(
            ok=True, key=self.key,
            frames_resent=frames_resent, bytes_resent=bytes_resent,
            message=f"Resent last frame {frames_resent} time(s)",
        )


@dataclass(frozen=True, slots=True)
class GetPlatformInfo(Command[PlatformInfoResult]):
    """Snapshot of OS identity + paths + permission warnings.

    Used by diagnostic UIs (`trcc info`, GUI about panel).  Keeps UIs
    from reaching directly into `app.platform` — they dispatch this and
    render the Result like any other Command.
    """

    def execute(self, app: App) -> PlatformInfoResult:
        p = app.platform
        paths = p.paths()
        return PlatformInfoResult(
            ok=True,
            message=f"Platform: {p.distro_name()}",
            distro_name=p.distro_name(),
            install_method=p.install_method(),
            config_dir=str(paths.config_dir()),
            data_dir=str(paths.data_dir()),
            user_content_dir=str(paths.user_content_dir()),
            log_file=str(paths.log_file()),
            permission_warnings=p.check_permissions(),
        )


# ── Autostart ────────────────────────────────────────────────────────


def _autostart_path(app: App) -> str:
    """Extract the manager's filesystem path when available."""
    mgr = app.platform.autostart()
    return str(getattr(mgr, "path", "")) or ""


@dataclass(frozen=True, slots=True)
class GetAutostartStatus(Command[AutostartResult]):
    """Report whether auto-launch-on-login is enabled."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        enabled = mgr.is_enabled()
        path = _autostart_path(app)
        return AutostartResult(
            ok=True,
            message="enabled" if enabled else "disabled",
            enabled=enabled, path=path,
        )


@dataclass(frozen=True, slots=True)
class EnableAutostart(Command[AutostartResult]):
    """Install the OS-specific autostart entry (per-user, no sudo)."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.enable()
        return AutostartResult(
            ok=True, message="autostart enabled",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
        )


@dataclass(frozen=True, slots=True)
class DisableAutostart(Command[AutostartResult]):
    """Remove the OS-specific autostart entry."""

    def execute(self, app: App) -> AutostartResult:
        mgr = app.platform.autostart()
        mgr.disable()
        return AutostartResult(
            ok=True, message="autostart disabled",
            enabled=mgr.is_enabled(), path=_autostart_path(app),
        )


# =========================================================================
# Control-center settings (app-global; no device key)
# =========================================================================


@dataclass(frozen=True, slots=True)
class SetTempUnit(Command[TempUnitResult]):
    """Set the global temperature unit ("C" or "F") and propagate to every device.

    Cross-cutting setter — keeps AppSettings.temp_unit + every connected
    device's DeviceSettings.temp_unit in lockstep so overlay renderers
    see a consistent unit regardless of which device emits the next
    frame.
    """
    unit: str

    def execute(self, app: App) -> TempUnitResult:
        if self.unit not in ("C", "F"):
            return TempUnitResult(
                ok=False, unit=self.unit,
                message=f"unit must be 'C' or 'F', got {self.unit!r}",
            )
        app.settings.set_global_temp_unit(self.unit)   # type: ignore[arg-type]
        app.events.publish(TempUnitChanged(unit=self.unit))
        return TempUnitResult(
            ok=True, unit=self.unit,
            message=f"temp unit set to {self.unit}",
        )


@dataclass(frozen=True, slots=True)
class ListLanguages(Command[LanguagesListResult]):
    """Enumerate every language code the i18n table supports.

    Pure read — no I/O.  UIs use the returned list to populate language
    pickers; CLI users see it via ``trcc system list-languages``.
    """

    def execute(self, app: App) -> LanguagesListResult:
        del app
        from .i18n import LANGUAGE_NAMES, TRANSLATIONS
        entries: list[LanguageEntry] = []
        for code in sorted(LANGUAGE_NAMES):
            entries.append(LanguageEntry(
                code=code,
                name=LANGUAGE_NAMES[code],
                translated_keys=len(TRANSLATIONS.get(code, {})),
            ))
        return LanguagesListResult(
            ok=True, languages=entries,
            message=f"{len(entries)} language(s) registered",
        )


@dataclass(frozen=True, slots=True)
class SetLanguage(Command[LanguageResult]):
    """Set the UI language code (ISO 639-1, e.g. 'en', 'zh', 'fr').

    Validates against the i18n table so unknown codes are rejected with a
    structured error instead of silently persisting and breaking
    ``tr()`` lookups for every subsequent string.
    """
    language: str

    def execute(self, app: App) -> LanguageResult:
        from .i18n import LANGUAGE_NAMES
        lang = self.language.strip()
        if not lang:
            return LanguageResult(
                ok=False, language=self.language,
                message="language code cannot be empty",
            )
        if lang not in LANGUAGE_NAMES:
            return LanguageResult(
                ok=False, language=self.language,
                message=(f"unknown language code {lang!r}; "
                         "use `system list-languages` to see supported codes"),
            )
        app.settings.set_language(lang)
        app.events.publish(LanguageChanged(language=lang))
        return LanguageResult(
            ok=True, language=lang,
            message=f"language set to {lang} ({LANGUAGE_NAMES[lang]})",
        )


@dataclass(frozen=True, slots=True)
class SetGpuDevice(Command[GpuDeviceResult]):
    """Pick the primary GPU by sensor key (e.g. 'nvidia:0', 'amd:0').

    Pass an empty string to clear the override and let
    ``SensorEnumerator.primary_gpu()`` pick automatically.
    """
    gpu_key: str

    def execute(self, app: App) -> GpuDeviceResult:
        normalized: str | None = self.gpu_key.strip() or None
        app.settings.set_active_gpu(normalized)
        app.events.publish(GpuDeviceChanged(gpu_key=normalized))
        return GpuDeviceResult(
            ok=True, gpu_key=normalized,
            message=(f"active gpu set to {normalized}" if normalized
                     else "active gpu cleared (auto)"),
        )


@dataclass(frozen=True, slots=True)
class SetRefreshInterval(Command[RefreshIntervalResult]):
    """Set the global metrics-refresh / render-and-send tick interval.

    Clamped to [0.1, 60.0] seconds — anything outside that range either
    starves the CPU (too low) or makes the LCD feel unresponsive (too
    high).
    """
    seconds: float

    def execute(self, app: App) -> RefreshIntervalResult:
        if not 0.1 <= self.seconds <= 60.0:
            return RefreshIntervalResult(
                ok=False, seconds=self.seconds,
                message=(f"refresh interval must be in [0.1, 60.0] seconds, "
                         f"got {self.seconds}"),
            )
        app.settings.set_refresh_interval(self.seconds)
        app.events.publish(RefreshIntervalChanged(seconds=self.seconds))
        return RefreshIntervalResult(
            ok=True, seconds=self.seconds,
            message=f"refresh interval set to {self.seconds:.2f}s",
        )
