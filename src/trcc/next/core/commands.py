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

from abc import ABC, abstractmethod
from dataclasses import dataclass
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
from .models import FitMode
from .registry import find_product
from .results import (
    AutostartResult,
    BootAnimationResult,
    BrightnessResult,
    ConnectResult,
    DisconnectResult,
    DiscoverResult,
    FitModeResult,
    GpuDeviceResult,
    LanguageResult,
    LedColorsResult,
    MaskApplyResult,
    MaskPositionResult,
    MaskVisibilityResult,
    OrientationResult,
    OverlayResult,
    PlatformInfoResult,
    RefreshIntervalResult,
    RenderResult,
    Result,
    SendResult,
    SensorsResult,
    SetupResult,
    SplitModeResult,
    TempUnitResult,
    ThemeExportResult,
    ThemeImportResult,
    ThemeResult,
    VideoResult,
)

if TYPE_CHECKING:
    from ..app import App


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

        app.settings.set_current_theme(self.key, theme.name)
        app.active_themes[self.key] = theme
        app.display.invalidate(self.key)  # drop stale scene cache from prev theme
        app.media.unload(self.key)        # drop stale video frames
        app.events.publish(ThemeLoaded(key=self.key, theme_name=theme.name))

        # If device is attached + connected + Renderer available, send an
        # immediate first frame.  Otherwise the theme is saved for the
        # next connect / tick.
        device = app.devices.get(self.key)
        if device is None or not device.is_connected:
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                message=f"Theme '{theme.name}' saved (device not connected)",
            )
        if app._renderer is None:  # pyright: ignore[reportPrivateUsage]
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
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
                message=f"Render/send failed: {e}",
            )

        if sent:
            app.events.publish(FrameSent(key=self.key, bytes_sent=len(frame)))
        return ThemeResult(
            ok=sent, key=self.key, theme_name=theme.name,
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


@dataclass(frozen=True, slots=True)
class ApplyMask(Command[MaskApplyResult]):
    """Set a user-supplied mask image that overrides the active theme's mask.

    Validates the path exists, is a regular file, and has an image
    extension. Stores the **resolved absolute path** so subsequent
    renders aren't affected by ``os.chdir`` between calls.
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
        if not candidate.is_file():
            return MaskApplyResult(
                ok=False, key=self.key, path=str(candidate),
                message=f"mask path is not a regular file: {candidate}",
            )
        if candidate.suffix.lower() not in _MASK_IMAGE_EXTS:
            return MaskApplyResult(
                ok=False, key=self.key, path=str(candidate),
                message=(f"mask must be one of {sorted(_MASK_IMAGE_EXTS)}, "
                         f"got {candidate.suffix!r}"),
            )
        resolved = str(candidate.resolve())
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
    """Compute the segment-display mask from current sensors + send one frame.

    Pulls the handshake-resolved ``style`` + ``style_sub`` off the
    device, builds a logical color array of ``[color] * mask_size``,
    asks the matching ``SegmentDisplay`` for the on/off mask given the
    current sensor snapshot + phase, and dispatches the resulting
    ``LedPayload`` through ``Led.send`` (which applies the wire-remap
    automatically).

    Styles without a segment display (LF13, unknown PM) fall back to a
    no-op send of the requested colors — the caller still drives the
    LEDs through ``SetLedColors`` for those panels.
    """
    key: str
    color: tuple[int, int, int] = (255, 0, 0)
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
        display = get_display(style)
        if display is None:
            return LedColorsResult(
                ok=False, key=self.key, colors=[],
                message=(f"style {style.value} has no segment display — "
                         "use SetLedColors instead"),
            )

        # Pull current sensor snapshot as SensorReading dict so
        # LegacyMetricsView can do its attribute → sensor_id translation.
        enum = app.platform.sensors()
        descriptors = enum.discover()
        current = enum.read_all()
        from .models import SensorReading
        readings = {
            d.sensor_id: SensorReading(
                sensor_id=d.sensor_id,
                category=d.category,
                value=current.get(d.sensor_id, 0.0),
                unit=d.unit,
                label=d.label,
            )
            for d in descriptors
        }
        metrics = LegacyMetricsView(readings)

        settings = app.settings.for_device(self.key)
        mask = compute_mask(
            style,
            metrics,
            phase=self.phase,
            temp_unit=settings.temp_unit,
            is_24h=(settings.time_format == "24h"),
        )
        colors = [self.color] * len(mask)

        payload = LedPayload(
            colors=colors,
            is_on=mask,
            global_on=True,
            brightness=settings.brightness,
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
        style_name = style.value
        return LedColorsResult(
            ok=ok, key=self.key, colors=colors,
            message=(f"Rendered {style_name} mask ({sum(mask)}/{len(mask)} LEDs on)"
                     if ok else "LED send returned False"),
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
class SetLanguage(Command[LanguageResult]):
    """Set the UI language code (ISO 639-1, e.g. 'en', 'zh', 'fr')."""
    language: str

    def execute(self, app: App) -> LanguageResult:
        lang = self.language.strip()
        if not lang:
            return LanguageResult(
                ok=False, language=self.language,
                message="language code cannot be empty",
            )
        app.settings.set_language(lang)
        app.events.publish(LanguageChanged(language=lang))
        return LanguageResult(
            ok=True, language=lang, message=f"language set to {lang}",
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
