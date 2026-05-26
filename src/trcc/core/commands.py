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
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from .errors import (
    DeviceDisconnectedError,
    DeviceNotConnectedError,
    DeviceNotFoundError,
    HandshakeError,
    ThemeError,
    TransportError,
)
from .events import (
    BackgroundChanged,
    BrightnessChanged,
    DateFormatChanged,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    ErrorOccurred,
    FitModeChanged,
    FrameSent,
    GpuDeviceChanged,
    HddEnabledChanged,
    LanguageChanged,
    LedColorsChanged,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OrientationChanged,
    OverlayChanged,
    RefreshIntervalChanged,
    ScreencastStarted,
    ScreencastStopped,
    SplitModeChanged,
    TempUnitChanged,
    ThemeExported,
    ThemeImported,
    ThemeLoaded,
    ThemeSaved,
    TimeFormatChanged,
    VideoStarted,
    VideoStopped,
)
from .led_models import LEDMode, LedRuntimeState
from .models import FitMode, OverlayElement
from .registry import find_product
from .results import (
    AutostartResult,
    BackgroundModeResult,
    BackgroundResult,
    BootAnimationResult,
    BrightnessResult,
    ClockFormatResult,
    CloudCategoryEntry,
    CloudThemeEntryResult,
    CloudThemeLoadResult,
    CloudThemesListResult,
    ConnectResult,
    ControlCenterSnapshotResult,
    DateFormatResult,
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
    ScreencastResult,
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
    TimeFormatResult,
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

    ``LOG_LEVEL`` controls how App.dispatch logs the command's entry +
    successful outcome.  Default INFO.  Per-tick commands (RenderAndSend,
    SendFrame, RenderLed, ReadSensors, *Snapshot) override to DEBUG so
    they show only under ``-vv``; they fire dozens of times per second
    and would drown the log at INFO.
    """

    LOG_LEVEL: ClassVar[int] = logging.INFO

    @abstractmethod
    def execute(self, app: App) -> R_co: ...


# =========================================================================
# Discovery / connection
# =========================================================================


@dataclass(frozen=True, slots=True)
class DiscoverDevices(Command[DiscoverResult]):
    """List attached devices that match the product registry.

    Also kicks off a per-resolution data install for each discovered
    product the first time we see that resolution — so the GUI's theme
    / web preview / mask grids aren't empty on first launch.  Subsequent
    discoveries are no-ops because ``DataInstallService`` short-circuits
    on already-populated dirs.
    """

    def execute(self, app: App) -> DiscoverResult:
        live = app.platform.scan_devices()
        products = []
        seen_resolutions: set[tuple[int, int]] = set()
        for info in live:
            product = find_product(info.vid, info.pid)
            if product is not None:
                products.append(product)
                app.events.publish(DeviceDiscovered(
                    key=info.key, product_name=product.product,
                ))
                if product.native_resolution != (0, 0):
                    seen_resolutions.add(product.native_resolution)
        # One install pass per unique resolution.  Each install is itself
        # idempotent (skips populated dirs), so this is safe to re-run.
        for resolution in seen_resolutions:
            try:
                app.data_install.ensure_all(resolution)
            except Exception:
                # Data install is best-effort — GUI degrades gracefully
                # to empty grids rather than blocking discovery.
                log.exception("DiscoverDevices: ensure_all(%s) failed",
                              resolution)
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

        # Variant override: handshake reveals the PM/SUB fingerprint, which
        # disambiguates products sharing one (VID, PID).  Patch the device's
        # ProductInfo with the resolved button_image / panel_cutout so the
        # GUI sidebar shows the right product picture and the renderer
        # masks any panel cutout.  Without this, every device falls back to
        # the registry default (A1CZTV "ransom" button).
        from dataclasses import replace as _dc_replace

        from .variants import get_variant_override
        override = get_variant_override(
            vid, pid, handshake.pm_byte, handshake.sub_byte,
        )
        if override is not None:
            patch: dict[str, object] = {}
            if override.button_image:
                patch["button_image"] = override.button_image
            if override.panel_cutout is not None:
                patch["panel_cutout"] = override.panel_cutout
            if patch:
                device.info = _dc_replace(device.info, **patch)
                log.info(
                    "ConnectDevice %s: variant override PM=%d SUB=%d → %s",
                    self.key, handshake.pm_byte, handshake.sub_byte,
                    override.button_image or "(cutout-only)",
                )

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

    Per-tick payload — logged at DEBUG so a default INFO run isn't
    drowned in frame chatter.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
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
            _publish_if_disconnect(app, self.key, e)
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
    (``trcc display color 0402:3922 ff0000`` turns the screen red)
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
            _publish_if_disconnect(app, self.key, e)
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

    Per-tick: logged at DEBUG so a default INFO run isn't drowned.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
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

        # Personalize raw readings here so the renderer receives the
        # same already-converted, already-filtered dict that periodic
        # SensorsUpdated subscribers see.  Single conversion site for
        # the entire metrics path — matches legacy's
        # ``PollingMetricsLoop._poll_metrics`` shape.
        from ..services.metrics_personalize import personalize_readings
        s = app.settings.app
        sensors = personalize_readings(
            app.platform.sensors().read_all(),
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )

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
            _publish_if_disconnect(app, self.key, e)
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
        log.info("LoadTheme: key=%s path=%s", self.key, self.path)
        try:
            theme = app.themes.load(self.path)
        except ThemeError as e:
            log.warning("LoadTheme: theme load failed for %s: %s",
                        self.path, e)
            app.events.publish(ErrorOccurred(message=str(e), kind="theme",
                                             key=self.key))
            return ThemeResult(ok=False, key=self.key, message=str(e))

        # Persist the absolute path — names are display strings, paths
        # are the stable reference RestoreLastTheme needs.
        app.settings.set_current_theme(self.key, str(theme.path.resolve()))
        # Single source of truth for "stop the previous video + clear
        # the cloud-background override + invalidate the scene cache":
        # ``StopVideo``.  Centralising here means publishing VideoStopped
        # for the UI handler's timer observer, clearing background_path
        # for the next render, and unloading the playback all happen in
        # one Command instead of three loose mutations — and a future
        # change to "what stop means" only has to touch StopVideo.
        StopVideo(key=self.key).execute(app)
        app.active_themes[self.key] = theme
        app.events.publish(ThemeLoaded(key=self.key, theme_name=theme.name))
        log.info(
            "LoadTheme: %s loaded — prior playback + cloud-bg override "
            "cleared, scene cache invalidated, active theme persisted",
            theme.name,
        )

        # If device is attached + connected + Renderer available, send an
        # immediate first frame.  Otherwise the theme is saved for the
        # next connect / tick.
        theme_path_str = str(theme.path.resolve())

        # Legacy themes (config.json shape) can carry an attached mask
        # under a top-level ``mask`` key pointing to a mask subdir +
        # ``mask_position`` (x, y) + ``mask_visible`` bool.  Same fields
        # come out of the DC binary reader's trailer.  Apply each so
        # DisplayService picks them up on the next render — without
        # this, themes with masks render unmasked or at the wrong offset.
        # Legacy themes (config.json shape) can carry an attached mask
        # under a top-level ``mask`` key pointing to a mask subdir +
        # ``mask_position`` (x, y) + ``mask_visible`` bool.  Same fields
        # come out of the DC binary reader's trailer.
        embedded_mask = theme.config.get("mask")
        if isinstance(embedded_mask, str) and embedded_mask:
            mask_path = Path(embedded_mask)
            apply = ApplyMask(key=self.key, path=mask_path).execute(app)
            if apply.ok:
                log.info("LoadTheme: applied embedded mask %s (%s)",
                         mask_path, theme.name)
            else:
                log.warning(
                    "LoadTheme: theme %s declares mask %s but ApplyMask "
                    "failed: %s", theme.name, mask_path, apply.message,
                )

        # Theme's OWN 01.png mask: the DC trailer's mask_position is
        # the mask CENTER on the canvas; the renderer wants the
        # TOP-LEFT.  Same conversion ApplyMask runs — port the legacy
        # behavior here so a freshly-loaded theme renders its bundled
        # mask at the right spot, not stored-center-as-top-left.
        from ..services.overlay import OverlayService
        theme_mask = theme.path / _LEGACY_MASK_FILENAME
        pos = theme.config.get("mask_position")
        if (
            theme_mask.is_file() and isinstance(pos, (list, tuple))
            and len(pos) == 2
        ):
            device = app.devices.get(self.key)
            canvas: tuple[int, int] = (0, 0)
            if device is not None and device.profile is not None:
                canvas = device.profile.resolution
            if canvas != (0, 0) and app._renderer is not None:  # pyright: ignore[reportPrivateUsage]
                try:
                    img = app._renderer.open_image(theme_mask)  # pyright: ignore[reportPrivateUsage]
                    mw, mh = app._renderer.surface_size(img)  # pyright: ignore[reportPrivateUsage]
                except Exception as e:
                    log.warning(
                        "LoadTheme: failed to size %s (%s) — using stored center",
                        theme_mask, e,
                    )
                    mw, mh = (0, 0)
                if mw > 0 and mh > 0:
                    px, py = OverlayService.calculate_mask_position(
                        theme.path, (mw, mh), canvas,
                    )
                    SetMaskPosition(key=self.key, x=px, y=py).execute(app)
                    log.info(
                        "LoadTheme: %s mask %dx%d on %dx%d canvas → "
                        "top-left (%d, %d) [center stored = %r]",
                        theme.name, mw, mh, canvas[0], canvas[1], px, py,
                        list(pos),
                    )
        # NB: do NOT dispatch SetMaskVisible from theme.config here.
        # Legacy's ``OverlayService.theme_mask_visible`` defaults True
        # and only toggles via explicit user action — the DC trailer's
        # ``mask_visible`` field is a theme-design metadata flag
        # ("this theme had a mask"), not a runtime visibility override.

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

        # Video-backed themes (Theme.{mp4,mov,webm,zt}) go through the
        # PlayVideo pipeline so the same VideoStarted event fires for
        # local + cloud + user-loaded videos — UI handler subscribes
        # once and starts its animation timer for any of them.  Static
        # themes (00.png) keep the build-frame-and-send path below.
        video_path = app.themes.video_path(theme)
        if video_path is not None:
            log.info(
                "LoadTheme: %s has bundled video %s — dispatching PlayVideo",
                theme.name, video_path.name,
            )
            play = PlayVideo(key=self.key, path=video_path).execute(app)
            if not play.ok:
                return ThemeResult(
                    ok=False, key=self.key, theme_name=theme.name,
                    theme_path=theme_path_str,
                    message=f"Theme '{theme.name}': {play.message}",
                )
            return ThemeResult(
                ok=True, key=self.key, theme_name=theme.name,
                theme_path=theme_path_str,
                message=(f"Theme '{theme.name}' loaded — playing "
                         f"{video_path.name} ({play.frame_count} frame(s))"),
            )

        # Read live sensors so the first frame the device sees after a
        # theme load has real metric values painted, not the no-sensor
        # warning at every overlay element.  RenderAndSend takes over
        # from the next tick onward.  Personalize via the same helper
        # used by MetricsLoop + ReadSensors + RenderAndSend so this
        # one-shot first-frame matches the cadence the periodic
        # broadcast will deliver.
        from ..services.metrics_personalize import personalize_readings
        s_app = app.settings.app
        try:
            sensors = personalize_readings(
                app.platform.sensors().read_all(),
                temp_unit=s_app.temp_unit,
                hdd_enabled=s_app.hdd_enabled,
            )
        except Exception as e:
            log.warning(
                "LoadTheme: sensors.read_all() raised %s — first frame "
                "will paint with empty sensors; tick observer will recover",
                e,
            )
            sensors = {}
        try:
            frame = app.display.build_frame(
                info=device.info, theme=theme, sensors=sensors,
                profile=device.profile,
            )
            sent = device.send(frame)
        except (TransportError, Exception) as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="render",
                                             key=self.key))
            _publish_if_disconnect(app, self.key, e)
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
    to ``user_theme_dir(w, h) / name`` — the per-resolution layout
    (matches legacy's ``data_dir / theme{w}{h} / name``).  The new
    directory is a fully independent theme; editing it doesn't affect
    the source.
    """
    key: str
    name: str

    def execute(self, app: App) -> ThemeResult:
        log.info("SaveTheme: key=%s name=%s", self.key, self.name)
        if not _is_safe_theme_name(self.name):
            log.warning("SaveTheme: rejected unsafe name %r", self.name)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"invalid theme name {self.name!r} "
                         "(no path separators, '..', leading '.', or NUL bytes)"),
            )

        theme = app.active_themes.get(self.key)
        if theme is None:
            log.warning("SaveTheme: no active theme for %s — refusing to save",
                        self.key)
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"no active theme for {self.key} — load one first"),
            )

        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "SaveTheme: cannot resolve resolution for %s "
                "— device must be connected with a known profile",
                self.key,
            )
            return ThemeResult(
                ok=False, key=self.key, theme_name=self.name,
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        import shutil
        target = app.platform.paths().user_theme_dir(w, h) / self.name
        log.info("SaveTheme: source=%s target=%s resolution=%dx%d",
                 theme.path, target, w, h)
        if target.exists():
            log.warning("SaveTheme: target %s already exists — refusing",
                        target)
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
    """Zip a theme under ``user_theme_dir(w, h) / theme_name`` to an archive path.

    Device-scoped — resolution comes from the device the caller named via
    ``key``, matching legacy's ``dev.export_config(path)`` shape where the
    device's own ``lcd_size`` supplied the per-resolution directory.

    The archive_path is written wherever the caller specifies (CLI / API
    are responsible for sanitizing that path at their edge).
    """
    key: str
    theme_name: str
    archive_path: Path

    def execute(self, app: App) -> ThemeExportResult:
        log.info("ExportTheme: key=%s theme=%s archive=%s",
                 self.key, self.theme_name, self.archive_path)
        if not _is_safe_theme_name(self.theme_name):
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=f"invalid theme name {self.theme_name!r}",
            )

        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ExportTheme: cannot resolve resolution for %s "
                "— device must be connected with a known profile",
                self.key,
            )
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.archive_path),
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        source = app.platform.paths().user_theme_dir(w, h) / self.theme_name
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
class ExportOverlay(Command[ThemeExportResult]):
    """Copy a theme's overlay config file to ``output_path``.

    Legacy ``export_config(lcd, path)`` exported the OVERLAY CONFIG
    ONLY — a single ``config1.dc`` (legacy binary) or
    ``trcc.json`` (next/-native).  Next/'s :class:`ExportTheme`
    zips the WHOLE directory (00.png + 01.png + Theme.png + …),
    which is heavy when a user just wants to share their metric-
    grid layout.

    Pick the source file in this order:
      1. ``theme_dir/config1.dc`` (legacy binary — most compatible
         with Windows TRCC users sharing layouts).
      2. ``theme_dir/trcc.json`` (next/-native JSON).
      3. Error if neither exists.

    Reuses :class:`ThemeExportResult` — the shape (theme_name +
    archive_path) fits.  Publishes :class:`ThemeExported` for
    consistency with the whole-theme path.

    Device-scoped — resolution comes from the device the caller named
    via ``key``, matching legacy's ``dev.export_config(path)`` shape.
    """
    key: str
    theme_name: str
    output_path: Path

    def execute(self, app: App) -> ThemeExportResult:
        log.info("ExportOverlay: key=%s theme=%s out=%s",
                 self.key, self.theme_name, self.output_path)
        if not _is_safe_theme_name(self.theme_name):
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=f"invalid theme name {self.theme_name!r}",
            )

        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ExportOverlay: cannot resolve resolution for %s",
                self.key,
            )
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        source_dir = app.platform.paths().user_theme_dir(w, h) / self.theme_name
        if not source_dir.is_dir():
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=f"theme {self.theme_name!r} not found at {source_dir}",
            )

        # Prefer the legacy binary config so Windows TRCC users can
        # import the overlay layout without next/ around.
        candidates = (
            source_dir / "config1.dc",
            source_dir / "trcc.json",
        )
        source = next((c for c in candidates if c.is_file()), None)
        if source is None:
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=(f"theme {self.theme_name!r} has no overlay config "
                         f"(no config1.dc or trcc.json in {source_dir})"),
            )

        try:
            import shutil
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, self.output_path)
        except OSError as e:
            return ThemeExportResult(
                ok=False, theme_name=self.theme_name,
                archive_path=str(self.output_path),
                message=f"failed to copy overlay config: {e}",
            )

        app.events.publish(ThemeExported(
            theme_name=self.theme_name,
            archive_path=str(self.output_path),
        ))
        return ThemeExportResult(
            ok=True, theme_name=self.theme_name,
            archive_path=str(self.output_path),
            message=(f"overlay config for '{self.theme_name}' exported to "
                     f"{self.output_path} (source: {source.name})"),
        )


@dataclass(frozen=True, slots=True)
class ImportTheme(Command[ThemeImportResult]):
    """Unpack a theme archive into ``user_theme_dir(w, h) / name``.

    Device-scoped — resolution comes from the device the caller named
    via ``key``, matching legacy's ``dev.import_config(path, data_dir)``
    shape where the device's ``lcd_size`` selected the per-resolution
    directory.

    ``name`` defaults to the archive filename's stem when blank.
    Zip-slip is filtered server-side by ``ThemeService.import_``.
    """
    key: str
    archive_path: Path
    name: str = ""

    def execute(self, app: App) -> ThemeImportResult:
        log.info("ImportTheme: key=%s archive=%s name=%r",
                 self.key, self.archive_path, self.name)
        chosen_name = self.name.strip() or self.archive_path.stem
        if not _is_safe_theme_name(chosen_name):
            return ThemeImportResult(
                ok=False, theme_name=chosen_name, path="",
                message=f"invalid theme name {chosen_name!r}",
            )

        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ImportTheme: cannot resolve resolution for %s",
                self.key,
            )
            return ThemeImportResult(
                ok=False, theme_name=chosen_name, path="",
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        target = app.platform.paths().user_theme_dir(w, h) / chosen_name

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

    Reads the named theme under ``user_theme_dir(w, h)``, layers in the
    device's user overlay elements (so the exported DC reflects what the
    user actually sees on screen), and writes ``output_path``.  Device-
    scoped — resolution comes from ``key``, matching legacy's
    device-driven export shape.  Used by anyone sharing a next/-managed
    theme back to Windows TRCC or legacy Linux users.
    """
    key: str
    theme_name: str
    output_path: Path

    def execute(self, app: App) -> ThemeDcExportResult:
        log.info("ExportDcTheme: key=%s theme=%s out=%s",
                 self.key, self.theme_name, self.output_path)
        if not _is_safe_theme_name(self.theme_name):
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=f"invalid theme name {self.theme_name!r}",
            )

        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "ExportDcTheme: cannot resolve resolution for %s",
                self.key,
            )
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=(f"cannot resolve resolution for {self.key} "
                         "(connect the device first)"),
            )
        w, h = resolution

        theme_dir = (
            app.platform.paths().user_theme_dir(w, h) / self.theme_name
        )
        if not theme_dir.is_dir():
            return ThemeDcExportResult(
                ok=False, theme_name=self.theme_name,
                output_path=str(self.output_path),
                message=f"theme not found at {theme_dir}",
            )
        settings = app.settings.for_device(self.key)
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
        log.info("PlayVideo.execute: key=%s path=%s fps=%d",
                 self.key, self.path, self.fps)
        if self.path.suffix.lower() not in _VIDEO_EXTS_OK:
            log.warning(
                "PlayVideo.execute: unsupported extension %r (allowed=%s)",
                self.path.suffix, sorted(_VIDEO_EXTS_OK),
            )
            return VideoResult(
                ok=False, key=self.key, path=str(self.path),
                message=(f"unsupported video extension {self.path.suffix!r} "
                         f"(expected one of {sorted(_VIDEO_EXTS_OK)})"),
            )
        if not self.path.exists():
            log.warning("PlayVideo.execute: path does not exist: %s",
                        self.path)
            return VideoResult(
                ok=False, key=self.key, path=str(self.path),
                message=f"video does not exist: {self.path}",
            )
        if not self.path.is_file():
            log.warning("PlayVideo.execute: path is not a regular file: %s",
                        self.path)
            return VideoResult(
                ok=False, key=self.key, path=str(self.path),
                message=f"video path is not a regular file: {self.path}",
            )

        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            log.warning("PlayVideo.execute: device %s not found: %s",
                        self.key, e)
            return VideoResult(ok=False, key=self.key, path=str(self.path),
                                message=str(e))

        if device.profile is not None:
            size = device.profile.resolution
        else:
            size = device.info.native_resolution
        log.info("PlayVideo.execute: target_size=%dx%d (profile=%s)",
                 size[0], size[1], device.profile is not None)

        try:
            playback = app.media.load_video(
                device_key=self.key, path=self.path, size=size,
                fps=self.fps,
            )
        except ThemeError as e:
            log.warning(
                "PlayVideo.execute: load_video raised ThemeError: %s", e,
            )
            app.events.publish(ErrorOccurred(
                message=str(e), kind="video", key=self.key,
            ))
            return VideoResult(ok=False, key=self.key, path=str(self.path),
                                message=str(e))

        # Bust the scene cache so the next render picks up the override.
        _invalidate_scene(app, self.key)
        # Per-frame interval derived from playback fps — handler observer
        # uses this to start the Qt animation timer.  Clamped to >=1 ms
        # so a degenerate fps=0 cannot stall the event loop.
        fps = getattr(playback, "fps", 0) or 30
        interval_ms = max(1, int(1000 / fps))
        log.info(
            "PlayVideo.execute: playback loaded — %d frames @ %d fps "
            "(interval=%dms, VideoStarted published)",
            playback.frame_count, fps, interval_ms,
        )

        app.events.publish(VideoStarted(
            key=self.key, path=str(self.path),
            frame_count=playback.frame_count,
            interval_ms=interval_ms,
        ))
        return VideoResult(
            ok=True, key=self.key, path=str(self.path),
            frame_count=playback.frame_count,
            message=(f"playing {self.path.name} on {self.key} "
                     f"({playback.frame_count} frame(s) @ {playback.fps} fps)"),
        )


@dataclass(frozen=True, slots=True)
class StopVideo(Command[VideoResult]):
    """Clear the device's playback override AND the persisted bg override.

    Idempotent — calling on a device with no playback is a no-op + ok=True
    so scripts can use it as a defensive cleanup.

    Clears ``DeviceSettings.background_path`` so the next render falls
    back to the active theme's bundled background.  Without this, the
    next ``RenderAndSend`` (triggered by ``VideoStopped`` via
    ``DeviceRenderObserver``) would find ``background_path`` still set,
    take the override branch in ``DisplayService._resolve_background``,
    and silently re-decode the same video via ``MediaService.load_video``
    — turning "stop" into "rewind to frame 0".
    """
    key: str

    def execute(self, app: App) -> VideoResult:
        had_playback = app.media.playback(self.key) is not None
        had_override = (
            app.settings.for_device(self.key).background_path is not None
        )
        app.media.unload(self.key)
        if had_override:
            log.info(
                "StopVideo: clearing background_path override for %s",
                self.key,
            )
            app.settings.set_background_path(self.key, None)
        _invalidate_scene(app, self.key)
        if had_playback:
            app.events.publish(VideoStopped(key=self.key))
        return VideoResult(
            ok=True, key=self.key,
            message=(f"video stopped for {self.key}"
                     if had_playback else f"no video playing for {self.key}"),
        )


# =========================================================================
# Screencast
# =========================================================================


@dataclass(frozen=True, slots=True)
class StartScreencast(Command[ScreencastResult]):
    """Begin a screen-capture session for a device.

    Mirrors :class:`PlayVideo` — the GUI ``ScreencastHandler`` is the
    subscriber that actually runs the Qt capture timer; this Command
    just publishes :class:`ScreencastStarted` so handler/CLI/API/daemon
    callers all enter the same one-way event flow.

    The Command itself is intentionally side-effect-light:
      * it does NOT touch the wire (no SendFrame here — the handler's
        per-frame tick drives that),
      * it does NOT persist anything in :class:`DeviceSettings`
        (screencast is a transient session, not a saved bg override),
      * it stops any prior video playback so the override stack matches
        what the user sees on the device.

    Validates region geometry — refuses zero-area or negative sizes so
    a typo in CLI args is caught at dispatch time instead of being a
    silent no-op in the handler timer.
    """
    key: str
    x: int
    y: int
    w: int
    h: int
    audio: bool = False

    def execute(self, app: App) -> ScreencastResult:
        log.info(
            "StartScreencast.execute: key=%s region=(%d,%d %dx%d) audio=%s",
            self.key, self.x, self.y, self.w, self.h, self.audio,
        )
        if self.w <= 0 or self.h <= 0:
            log.warning(
                "StartScreencast.execute: invalid region %dx%d for %s",
                self.w, self.h, self.key,
            )
            return ScreencastResult(
                ok=False, key=self.key,
                message=(f"invalid screencast region {self.w}x{self.h} "
                         f"(both dimensions must be > 0)"),
            )

        try:
            app.get(self.key)
        except DeviceNotFoundError as e:
            log.warning(
                "StartScreencast.execute: device %s not found: %s",
                self.key, e,
            )
            return ScreencastResult(ok=False, key=self.key, message=str(e))

        # A live video playback overlay would race the screencast tick on
        # the same wire — stop it first so the handler owns the surface
        # cleanly.  StopVideo is idempotent so it's safe even if no
        # playback was loaded.
        StopVideo(key=self.key).execute(app)

        app.events.publish(ScreencastStarted(
            key=self.key,
            x=self.x, y=self.y, w=self.w, h=self.h,
            audio=self.audio,
        ))
        return ScreencastResult(
            ok=True, key=self.key, active=True,
            x=self.x, y=self.y, w=self.w, h=self.h, audio=self.audio,
            message=(f"screencast started on {self.key} "
                     f"({self.w}x{self.h} @ {self.x},{self.y})"),
        )


@dataclass(frozen=True, slots=True)
class StopScreencast(Command[ScreencastResult]):
    """End the screen-capture session for a device.

    Idempotent — calling on a device that has no active session returns
    ``ok=True`` so scripts can use it as a defensive cleanup.  Publishes
    :class:`ScreencastStopped`; the GUI ``ScreencastHandler`` reacts by
    stopping its Qt capture timer + tearing down PipeWire/audio plumbing.
    """
    key: str

    def execute(self, app: App) -> ScreencastResult:
        log.info("StopScreencast.execute: key=%s", self.key)
        app.events.publish(ScreencastStopped(key=self.key))
        return ScreencastResult(
            ok=True, key=self.key, active=False,
            message=f"screencast stopped on {self.key}",
        )


# Image extensions supported as a static background override.  Kept
# narrower than ``_MASK_IMAGE_EXTS`` on purpose — the renderer pipes
# whatever ``QtRenderer.open_image`` accepts; this is the GUI-visible
# safelist for "pick a file as my background".
_BG_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


@dataclass(frozen=True, slots=True)
class SetBackground(Command[BackgroundResult]):
    """Apply a file as the device's persistent background override.

    Sister Command to :class:`PlayVideo` — both write to
    ``DeviceSettings.background_path``, which ``DisplayService``
    consults BEFORE the active theme's bundled background.  Use this
    one for STATIC IMAGES (``PlayVideo`` for animated bg) so each
    Command is single-purpose:

      * image  → store path, invalidate scene cache, publish
        ``BackgroundChanged`` so ``DeviceRenderObserver`` schedules
        one ``RenderAndSend`` to push the new bg onto the wire.
      * video  → forwarded to ``PlayVideo`` so the full playback
        pipeline (decode → ``VideoStarted`` → animation timer) is
        the same regardless of how the path entered the system.

    Stops any prior video first so the new image isn't immediately
    overwritten by the next animation tick.
    """
    key: str
    path: Path

    def execute(self, app: App) -> BackgroundResult:
        log.info(
            "SetBackground.execute: key=%s path=%s", self.key, self.path,
        )
        if not self.path.exists():
            log.warning(
                "SetBackground.execute: path does not exist: %s", self.path,
            )
            return BackgroundResult(
                ok=False, key=self.key, path=str(self.path),
                message=f"background file does not exist: {self.path}",
            )
        if not self.path.is_file():
            log.warning(
                "SetBackground.execute: not a regular file: %s", self.path,
            )
            return BackgroundResult(
                ok=False, key=self.key, path=str(self.path),
                message=f"background path is not a regular file: {self.path}",
            )

        ext = self.path.suffix.lower()
        if ext in _VIDEO_EXTS_OK:
            log.info(
                "SetBackground.execute: %s has video ext — delegating to "
                "PlayVideo", self.path.name,
            )
            app.settings.set_background_path(self.key, str(self.path))
            play = PlayVideo(key=self.key, path=self.path).execute(app)
            return BackgroundResult(
                ok=play.ok, key=self.key, path=str(self.path), kind="video",
                message=play.message,
            )
        if ext in _BG_IMAGE_EXTS:
            # Drop any prior video first — its animation timer would
            # otherwise tick over the static image we're about to set.
            # Routes through StopVideo (publishes VideoStopped, stops
            # the handler timer), then we set the new bg path.  StopVideo
            # would normally clear ``background_path``, but we set it
            # right after on the same dispatch thread, so the next
            # render reads the new value.
            StopVideo(key=self.key).execute(app)
            app.settings.set_background_path(self.key, str(self.path))
            _invalidate_scene(app, self.key)
            app.events.publish(
                BackgroundChanged(key=self.key, path=str(self.path)),
            )
            log.info(
                "SetBackground.execute: image bg %s applied (BackgroundChanged "
                "published)", self.path.name,
            )
            return BackgroundResult(
                ok=True, key=self.key, path=str(self.path), kind="image",
                message=f"background set to {self.path.name} for {self.key}",
            )
        log.warning(
            "SetBackground.execute: unsupported extension %r (image=%s, "
            "video=%s)",
            ext, sorted(_BG_IMAGE_EXTS), sorted(_VIDEO_EXTS_OK),
        )
        return BackgroundResult(
            ok=False, key=self.key, path=str(self.path),
            message=(f"unsupported background extension {ext!r} — "
                     f"expected image {sorted(_BG_IMAGE_EXTS)} or "
                     f"video {sorted(_VIDEO_EXTS_OK)}"),
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
    """Set per-device display brightness (0–100).

    Brightness is a software dimmer applied by the Renderer during
    composite; the device protocol has no separate brightness command.
    Persisting alone is therefore not enough — for the user to see a
    brighter / dimmer screen we must invalidate the cached scene and
    push a re-rendered frame.  Legacy ``LCDDevice.set_brightness``
    does this in one step (``display_svc.set_brightness`` →
    ``_publish_frame``); we mirror that here by invalidating + dispatching
    ``RenderAndSend`` on the connected-with-active-theme path.
    """
    key: str
    percent: int

    def execute(self, app: App) -> BrightnessResult:
        if not 0 <= self.percent <= 100:
            return BrightnessResult(
                ok=False, key=self.key, percent=self.percent,
                message="Brightness out of range (0–100)",
            )
        app.settings.set_brightness(self.key, self.percent)
        _invalidate_scene(app, self.key)
        app.events.publish(BrightnessChanged(key=self.key, percent=self.percent))
        # Re-render happens via the visual-event observer wired in
        # App.__init__ — every visual-affecting mutation publishes its
        # event and the observer dispatches one RenderAndSend.
        return BrightnessResult(
            ok=True, key=self.key, percent=self.percent,
            message=f"Brightness set to {self.percent}%",
        )


# ── Display tweaks (fit mode / overlay / split mode) ────────────────


def _publish_if_disconnect(app: App, key: str, exc: BaseException) -> None:
    """Publish ``DeviceDisconnected`` if *exc* is the auto-detach signal.

    Called inside every Command's ``except TransportError`` block.
    The exception from ``Device.send`` is :class:`DeviceDisconnectedError`
    when the recovery tracker hit the consecutive-failure threshold —
    transport is already closed by the device, ``is_connected`` is
    False.  Observers (sidebar, system tray, daemon clients) listen
    for ``DeviceDisconnected`` to re-run discovery.

    Plain :class:`TransportError` (transient bus errors) does NOT
    publish the event — the device is still attached, the caller just
    saw one bad send.
    """
    if isinstance(exc, DeviceDisconnectedError):
        log.info("auto-disconnect: %s closed after recovery threshold", key)
        app.events.publish(DeviceDisconnected(key=key))


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
        log.info("ApplyMask: key=%s path=%s", self.key, self.path)
        candidate = self.path
        if not candidate.exists():
            log.warning("ApplyMask: mask path does not exist: %s", candidate)
            return MaskApplyResult(
                ok=False, key=self.key, path=str(candidate),
                message=f"mask file does not exist: {candidate}",
            )
        resolved_file = _resolve_mask_path(candidate)
        if resolved_file is None:
            log.warning(
                "ApplyMask: %s is neither a supported image nor a legacy "
                "mask dir with %s — rejecting",
                candidate, _LEGACY_MASK_FILENAME,
            )
            return MaskApplyResult(
                ok=False, key=self.key, path=str(candidate),
                message=(f"mask path is neither a supported image file "
                         f"nor a legacy mask directory with "
                         f"{_LEGACY_MASK_FILENAME}: {candidate}"),
            )
        resolved = str(resolved_file.resolve())
        log.info("ApplyMask: resolved %s → %s", candidate, resolved)
        app.settings.set_mask_path(self.key, resolved)
        # Auto-position the mask using its own config1.dc — legacy
        # ``OverlayService.calculate_mask_position`` behaviour: full-size
        # masks at (0,0); sub-screen masks read center coords from the
        # sibling DC trailer and convert to top-left; missing/unreadable
        # DC = center on the canvas.  Without this, sub-screen masks
        # (most of the cloud catalog) draw at (0,0) and look invisible.
        from ..services import _dc as Dc
        from ..services.overlay import OverlayService
        try:
            mask_img = app.display._r.open_image(resolved_file)  # pyright: ignore[reportPrivateUsage]
            mw, mh = app.display._r.surface_size(mask_img)  # pyright: ignore[reportPrivateUsage]
        except Exception as e:
            log.warning("ApplyMask: failed to size %s (%s) — position skipped",
                        resolved_file, e)
            mw, mh = (0, 0)
        device = app.devices.get(self.key)
        canvas: tuple[int, int] = (0, 0)
        if device is not None and device.profile is not None:
            canvas = device.profile.resolution
        mask_dir = (
            self.path if self.path.is_dir() else resolved_file.parent
        )
        if mw > 0 and mh > 0 and canvas != (0, 0):
            px, py = OverlayService.calculate_mask_position(
                mask_dir, (mw, mh), canvas,
            )
            app.settings.set_mask_position(self.key, (px, py))
            log.info(
                "ApplyMask: %s sized %dx%d on %dx%d canvas → position (%d, %d)",
                resolved_file.name, mw, mh, canvas[0], canvas[1], px, py,
            )
        # Mask's own config1.dc takes over the metric overlay — legacy
        # ``ThemeLoader.apply_mask`` clears the theme's overlay then
        # ``load_from_dc(mask_dir/config1.dc)``.  Each mask carries its
        # own element list with coordinates aligned to its cutouts; the
        # theme's elements get replaced (not stacked) on apply.
        mask_dc = mask_dir / "config1.dc"
        theme = app.active_themes.get(self.key)
        if theme is not None and mask_dc.is_file():
            try:
                dc = Dc.File(mask_dc).read()
            except Exception as e:
                log.warning("ApplyMask: %s DC unreadable (%s) — keeping "
                            "theme's overlay layout", mask_dc, e)
            else:
                mask_elements = dc.get("elements") or []
                if mask_elements:
                    # Store on DeviceSettings — not theme.config — so the
                    # mask layout survives a theme swap.  Legacy
                    # ``OverlayService`` held overlay state independently
                    # of the theme dict, so picking a new background
                    # after a mask didn't drop the mask's metric layout.
                    # Mirror that here: settings is the persistent home
                    # for "what the user is currently rendering on top".
                    from .models import OverlayElement
                    mask_overlay = [
                        OverlayElement.from_dict(el) for el in mask_elements
                    ]
                    app.settings.set_mask_overlay_elements(
                        self.key, mask_overlay,
                    )
                    log.info(
                        "ApplyMask: %s contributes %d overlay element(s) — "
                        "stored on DeviceSettings.mask_overlay_elements",
                        resolved_file.name, len(mask_elements),
                    )
        _invalidate_scene(app, self.key)
        app.events.publish(MaskApplied(key=self.key, path=resolved))
        # DeviceRenderObserver wired in App.__init__ subscribes to
        # MaskApplied and dispatches RenderAndSend — no per-Command
        # re-render here.
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
            _publish_if_disconnect(app, self.key, e)
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

    Per-tick: logged at DEBUG so a default INFO run isn't drowned.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
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
            _publish_if_disconnect(app, self.key, e)
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
class SetTimeFormat(Command[TimeFormatResult]):
    """Set the LCD-overlay clock format (12h or 24h) for a device.

    Persisted on ``DeviceSettings.time_format`` and read per-render
    by :func:`DisplayService.compute_clock`.  Publishes
    :class:`TimeFormatChanged` so ``DeviceRenderObserver`` re-renders
    the LCD immediately.

    Distinct from :class:`SetClockFormat` (which is for LED-segment
    LC2-style displays and writes ``led_clock_24h``).
    """
    key: str
    fmt: str   # "12h" or "24h"

    def execute(self, app: App) -> TimeFormatResult:
        if self.fmt not in ("12h", "24h"):
            return TimeFormatResult(
                ok=False, key=self.key, fmt=self.fmt,
                message=f"fmt must be '12h' or '24h', got {self.fmt!r}",
            )
        app.settings.set_time_format(self.key, self.fmt)  # type: ignore[arg-type]
        app.display.invalidate(self.key)
        app.events.publish(TimeFormatChanged(key=self.key, fmt=self.fmt))
        return TimeFormatResult(
            ok=True, key=self.key, fmt=self.fmt,
            message=f"time format set to {self.fmt} for {self.key}",
        )


@dataclass(frozen=True, slots=True)
class SetDateFormat(Command[DateFormatResult]):
    """Set the LCD-overlay date pattern for a device.

    Pattern uses ICU-ish tokens (``yyyy/MM/dd``, ``dd.MM.yyyy``, etc.)
    translated by ``_clock._translate_date_pattern`` to a Python
    strftime string.  Persisted on ``DeviceSettings.date_format``.
    """
    key: str
    fmt: str

    def execute(self, app: App) -> DateFormatResult:
        if not self.fmt:
            return DateFormatResult(
                ok=False, key=self.key, fmt=self.fmt,
                message="fmt must not be empty",
            )
        app.settings.set_date_format(self.key, self.fmt)
        app.display.invalidate(self.key)
        app.events.publish(DateFormatChanged(key=self.key, fmt=self.fmt))
        return DateFormatResult(
            ok=True, key=self.key, fmt=self.fmt,
            message=f"date format set to {self.fmt!r} for {self.key}",
        )


@dataclass(frozen=True, slots=True)
class SetGlobalTimeFormat(Command[TimeFormatResult]):
    """Set the global default clock format for every device.

    Companion to :class:`SetTimeFormat` (per-device).  Writes
    ``AppSettings.time_format`` and fans out to every existing
    ``DeviceSettings.time_format``; subscribers (DeviceRenderObserver)
    re-render each LCD on the next tick because we publish one
    :class:`TimeFormatChanged` per device key.

    Per-device override remains available — call ``SetTimeFormat``
    after this Command to deviate one LCD from the global.
    """
    fmt: str

    def execute(self, app: App) -> TimeFormatResult:
        log.info("SetGlobalTimeFormat.execute: fmt=%s", self.fmt)
        if self.fmt not in ("12h", "24h"):
            log.warning(
                "SetGlobalTimeFormat.execute: invalid fmt %r", self.fmt,
            )
            return TimeFormatResult(
                ok=False, key="", fmt=self.fmt,
                message=f"fmt must be '12h' or '24h', got {self.fmt!r}",
            )
        keys = app.settings.set_global_time_format(self.fmt)  # type: ignore[arg-type]
        for key in keys:
            app.display.invalidate(key)
            app.events.publish(TimeFormatChanged(key=key, fmt=self.fmt))
        log.info(
            "SetGlobalTimeFormat.execute: fanned out to %d device(s)",
            len(keys),
        )
        return TimeFormatResult(
            ok=True, key="", fmt=self.fmt,
            message=(f"global time format set to {self.fmt} "
                     f"({len(keys)} device(s) updated)"),
        )


@dataclass(frozen=True, slots=True)
class SetGlobalDateFormat(Command[DateFormatResult]):
    """Set the global default date pattern for every device.

    Companion to :class:`SetDateFormat` (per-device).  Same fan-out
    shape as :class:`SetGlobalTimeFormat`.
    """
    fmt: str

    def execute(self, app: App) -> DateFormatResult:
        log.info("SetGlobalDateFormat.execute: fmt=%r", self.fmt)
        if not self.fmt:
            log.warning("SetGlobalDateFormat.execute: empty fmt")
            return DateFormatResult(
                ok=False, key="", fmt=self.fmt,
                message="fmt must not be empty",
            )
        keys = app.settings.set_global_date_format(self.fmt)
        for key in keys:
            app.display.invalidate(key)
            app.events.publish(DateFormatChanged(key=key, fmt=self.fmt))
        log.info(
            "SetGlobalDateFormat.execute: fanned out to %d device(s)",
            len(keys),
        )
        return DateFormatResult(
            ok=True, key="", fmt=self.fmt,
            message=(f"global date format set to {self.fmt!r} "
                     f"({len(keys)} device(s) updated)"),
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
        # Wake subscribers (MetricsLoop) so the broadcast refreshes
        # with the new HDD-filter state immediately, not after a full
        # refresh interval.  Same event-driven pattern SetTempUnit
        # and SetRefreshInterval use.
        app.events.publish(HddEnabledChanged(enabled=self.enabled))
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
    """Delete the theme directory at ``path``.

    Path-based — matches legacy's ``delete_theme(lcd, path)`` shape: the
    caller already resolved the theme's location (from the picker / list /
    filesystem walk), so the Command doesn't have to re-derive it from
    name + resolution.  Confined to the user-content tree to keep this
    Command from accidentally wiping system data.
    """
    path: Path

    def execute(self, app: App) -> DeleteThemeResult:
        log.info("DeleteTheme: path=%s", self.path)
        root = app.platform.paths().user_content_dir()
        try:
            target = self.path.resolve()
            root_resolved = root.resolve()
            target.relative_to(root_resolved)
        except (OSError, ValueError) as e:
            log.warning(
                "DeleteTheme: refusing to delete %s — not under %s (%s)",
                self.path, root, e,
            )
            return DeleteThemeResult(
                ok=False, theme_name=self.path.name, path=str(self.path),
                message=(f"refusing to delete {self.path} — "
                         f"not inside {root}"),
            )

        try:
            deleted = app.themes.delete(target.parent, target.name)
        except ThemeError as e:
            return DeleteThemeResult(
                ok=False, theme_name=target.name, path=str(target),
                message=str(e),
            )
        return DeleteThemeResult(
            ok=True, theme_name=deleted.name, path=str(deleted),
            message=f"Deleted theme at {deleted}",
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
    isn't installed, so headless callers can probe safely.

    Headless callers (CLI / API / tests) reach this with no
    ``QGuiApplication`` instance.  ``QFontDatabase.families()``
    segfaults inside ``libQt6Gui`` when called before the GUI
    application initialises the font subsystem — bypass that by
    bringing up an offscreen ``QGuiApplication`` first, idempotently.
    """

    def execute(self, app: App) -> FontsListResult:
        del app
        try:
            from PySide6.QtGui import (  # type: ignore[import-not-found]
                QFontDatabase,
                QGuiApplication,
            )
        except ImportError:
            return FontsListResult(
                ok=True, fonts=[],
                message="Qt not available — no fonts enumerable",
            )

        # libQt6Gui's font subsystem needs a QGuiApplication to be
        # alive; without one, ``QFontDatabase.families()`` aborts the
        # process (no Python exception to catch).  Spin one up offscreen
        # if the caller didn't.  Idempotent — re-creating would raise.
        if QGuiApplication.instance() is None:
            import os
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            try:
                QGuiApplication([])
            except RuntimeError as e:
                return FontsListResult(
                    ok=False, fonts=[],
                    message=f"QGuiApplication init failed: {e}",
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
    """Per-device LCD state snapshot — what settings.for_device holds.

    Polled by UIs to refresh state — logged at DEBUG.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
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
    """Per-device LED state snapshot.

    Polled by UIs to refresh state — logged at DEBUG.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
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
    """App-wide settings snapshot.

    Polled by UIs to refresh state — logged at DEBUG.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG

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
      2. ``user_theme_dir(w,h)/<name>``      — user-saved layout
      3. ``cloud_theme_dir(w,h)/<name>``     — cloud cache
      4. ``user_content_dir()/single-image/<name_after_image_prefix>``
         — LoadImage's flat single-image cache (different layout, not
         a theme; only consulted for ``image:<name>`` keys)

    Each candidate must be a directory containing a theme config
    (``trcc.json`` or ``config1.dc``) — guarded by
    ``ThemeService`` semantics.

    The pre-cutover ``user_content_dir()/<name>`` flat candidate was
    dropped — every next/ theme writer now lands at the per-resolution
    path.  Users with legacy flat themes on disk must run
    ``tools/migrate_legacy_themes.py`` once to move them into place.
    """
    paths = app.platform.paths()
    resolution = _resolve_resolution(app, key)
    candidates: list[Path] = []
    if resolution is not None:
        w, h = resolution
        candidates.append(paths.theme_dir(w, h) / name)
        candidates.append(paths.user_theme_dir(w, h) / name)
        candidates.append(paths.cloud_theme_dir(w, h) / name)
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
    """Download a cloud video and apply it as the device's background.

    Despite the name (kept for API compat), a cloud "theme" is just a
    video background — picking one swaps what plays behind the active
    theme's overlay + mask, not the theme itself.  Matches legacy
    ``select_cloud_theme``: it wraps the MP4 in ``ThemeInfo.from_video``
    and starts an animation timer; the active overlay state stays.

    Execution:
      1. Resolve the device's render resolution.
      2. ``CloudThemeService.materialise`` downloads the MP4 flat into
         ``paths.cloud_theme_dir(w, h)/<id>.mp4`` and generates the
         first-frame PNG + animated GIF previews for the GUI.
      3. Persist the new background path on ``DeviceSettings.background_path``
         so it survives an app restart.
      4. Dispatch ``PlayVideo(key, path=<mp4>)`` — that's the path
         MediaService + DisplayService already use to render a video
         background on every tick.
    """
    key: str
    theme_id: str

    def execute(self, app: App) -> CloudThemeLoadResult:
        log.info("LoadCloudTheme: key=%s theme_id=%s", self.key, self.theme_id)
        from ..adapters.repo.http import HttpFetchError
        resolution = _resolve_resolution(app, self.key)
        if resolution is None:
            log.warning(
                "LoadCloudTheme: cannot resolve resolution for %s — "
                "device not connected and no registry entry",
                self.key,
            )
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=(f"Cannot resolve resolution for {self.key} — "
                         "connect the device or register the product first"),
            )
        log.info("LoadCloudTheme: materialising %s @ %dx%d",
                 self.theme_id, resolution[0], resolution[1])
        try:
            mp4_path = app.cloud_themes.materialise(
                self.theme_id, resolution,
            )
        except ValueError as e:
            log.warning("LoadCloudTheme: ValueError materialising %s: %s",
                        self.theme_id, e)
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=str(e),
            )
        except HttpFetchError as e:
            log.warning("LoadCloudTheme: download failed for %s: %s",
                        self.theme_id, e)
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=f"Cloud download failed: {e}",
            )
        except OSError as e:
            log.warning("LoadCloudTheme: local IO failed for %s: %s: %s",
                        self.theme_id, type(e).__name__, e)
            return CloudThemeLoadResult(
                ok=False, key=self.key, theme_id=self.theme_id, theme_path="",
                message=f"Local IO failed: {e}",
            )

        log.info(
            "LoadCloudTheme: %s ready at %s — setting background override + "
            "dispatching PlayVideo",
            self.theme_id, mp4_path,
        )
        # Persist the new background on the device — survives restart.
        app.settings.set_background_path(self.key, str(mp4_path))
        # MediaService.load_video populates the playback; DisplayService's
        # ``_resolve_background`` short-circuits to ``playback.current``
        # when a playback exists, so this is the entire "play this video
        # as the bg" wire (overlay + mask stay untouched).
        play_result = PlayVideo(key=self.key, path=mp4_path).execute(app)
        return CloudThemeLoadResult(
            ok=play_result.ok,
            key=self.key,
            theme_id=self.theme_id,
            theme_path=str(mp4_path),
            message=play_result.message,
        )


# =========================================================================
# Sensors
# =========================================================================


@dataclass(frozen=True, slots=True)
class ReadSensors(Command[SensorsResult]):
    """Return current sensor readings — personalized to user prefs.

    Pulls descriptor metadata (label / unit / category) from
    ``discover()`` and fresh values from ``read_all()``, applies user
    prefs through :func:`metrics_personalize.personalize_readings`,
    then merges so every returned ``SensorReading`` carries the
    personalized value.

    Same conversion + filter path as ``MetricsLoop._publish_once`` —
    one-shot callers (CLI / API / tests / GUI view-switch one-shot)
    receive the same shape the periodic broadcast carries.  When the
    user disables HDD, ``disk:*`` readings are excluded entirely
    (matches legacy's ``_populated.discard`` semantics); when the
    user picks °F, temp readings carry °F values AND ``unit="°F"``
    (so callers that key off ``.unit`` don't have to know temp_unit
    separately).

    Polled per refresh tick — logged at DEBUG so a default INFO run
    isn't drowned.
    """

    LOG_LEVEL: ClassVar[int] = logging.DEBUG

    def execute(self, app: App) -> SensorsResult:
        from ..services.metrics_personalize import personalize_readings
        from .models import SensorReading

        enum = app.platform.sensors()
        descriptors = enum.discover()
        raw = enum.read_all()
        s = app.settings.app
        personalized = personalize_readings(
            raw,
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )
        # Filter descriptors to only those that survived
        # personalization (HDD-disable drops disk:* keys entirely so
        # callers don't see them at value=0).  Temperature unit
        # override: if the sensor is a temp and the user picked °F,
        # the value is already in °F — adjust .unit so callers that
        # render unit suffixes don't mislabel.
        readings: list[SensorReading] = []
        is_fahrenheit = s.temp_unit == "F"
        for d in descriptors:
            if d.sensor_id not in personalized:
                continue
            unit = d.unit
            if is_fahrenheit and d.sensor_id.endswith(":temp") and unit == "°C":
                unit = "°F"
            readings.append(SensorReading(
                sensor_id=d.sensor_id,
                category=d.category,
                value=personalized[d.sensor_id],
                unit=unit,
                label=d.label,
            ))
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

        config_path = theme_dir / "trcc.json"
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
    """Has trcc finished onboarding on this machine?

    GUI uses this on launch to decide whether to surface a welcome
    screen; CLI users see it via ``trcc system first-run-status``.
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
    """Keep a Bulk/LY device's screen pinned by re-sending frames on a tick.

    Bulk and LY firmware revert to the built-in Thermalright logo after
    ~2-3 s without a fresh frame.  This Command runs the resend loop
    legacy used to ship as ``DisplayService.run_static_loop`` so
    static themes survive on those devices.

    Two cadences:

      * ``interval_s`` (default 0.150 s) — how often to resend.  Below
        the 2-3 s firmware-revert threshold by an order of magnitude.
        Cheap because we resend the cached bytes from
        :class:`KeepaliveService`; no image re-encoding.
      * ``metric_interval_s`` (default 1.0 s) — how often to refresh
        the overlay metrics by dispatching :class:`RenderAndSend`.
        Without this, CPU/GPU temp on a static theme would freeze at
        the value captured when keepalive started.

    ``count`` selects loop shape:

      * ``count == 0`` — open-ended; loops until ``KeyboardInterrupt``
        (legacy parity).
      * ``count >= 1`` — fixed iterations; useful as a tick action
        (daemon dispatches with ``count=1`` per tick).

    Foreground / blocking — CLI users see this as ``trcc display
    keepalive <key>`` and Ctrl-C it.
    """
    key: str
    count: int = 0
    interval_s: float = 0.150
    metric_interval_s: float = 1.0

    def execute(self, app: App) -> KeepaliveResult:
        import time

        if self.count < 0:
            return KeepaliveResult(
                ok=False, key=self.key,
                message=f"count must be >= 0, got {self.count}",
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
        last_metric_at = time.monotonic()
        i = 0
        try:
            while self.count == 0 or i < self.count:
                if i > 0:
                    time.sleep(max(0.0, self.interval_s))

                # Periodic metric refresh — re-render through the
                # full pipeline so overlay values update with live
                # sensor readings; the new frame goes into the
                # keepalive cache via RenderAndSend's existing
                # ``app.keepalive.store`` call.
                now = time.monotonic()
                if (self.metric_interval_s > 0
                        and now - last_metric_at >= self.metric_interval_s):
                    render_result = RenderAndSend(key=self.key).execute(app)
                    last_metric_at = now
                    if render_result.ok:
                        last = app.keepalive.last_frame(self.key) or last
                        frames_resent += 1
                        bytes_resent += render_result.bytes_sent
                        i += 1
                        continue
                    # RenderAndSend already published ErrorOccurred /
                    # DeviceDisconnected; treat as keepalive failure.
                    return KeepaliveResult(
                        ok=False, key=self.key,
                        frames_resent=frames_resent,
                        bytes_resent=bytes_resent,
                        message=(f"Keepalive RenderAndSend failed at iter {i}: "
                                 f"{render_result.message}"),
                    )

                # Fast path: resend the cached bytes, no re-encode.
                try:
                    sent = device.send(last)
                except TransportError as e:
                    _publish_if_disconnect(app, self.key, e)
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
                i += 1
        except KeyboardInterrupt:
            return KeepaliveResult(
                ok=True, key=self.key,
                frames_resent=frames_resent, bytes_resent=bytes_resent,
                message=f"Keepalive interrupted after {frames_resent} frame(s)",
            )
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
        log.info("SetRefreshInterval.execute: seconds=%.2f", self.seconds)
        if not 0.1 <= self.seconds <= 60.0:
            log.warning(
                "SetRefreshInterval.execute: out-of-range %.2f rejected "
                "(allowed [0.1, 60.0])", self.seconds,
            )
            return RefreshIntervalResult(
                ok=False, seconds=self.seconds,
                message=(f"refresh interval must be in [0.1, 60.0] seconds, "
                         f"got {self.seconds}"),
            )
        old = app.settings.app.refresh_interval_s
        app.settings.set_refresh_interval(self.seconds)
        app.events.publish(RefreshIntervalChanged(seconds=self.seconds))
        log.info(
            "SetRefreshInterval.execute: settings.app.refresh_interval_s "
            "%.2f -> %.2f (RefreshIntervalChanged published)",
            old, self.seconds,
        )
        return RefreshIntervalResult(
            ok=True, seconds=self.seconds,
            message=f"refresh interval set to {self.seconds:.2f}s",
        )
