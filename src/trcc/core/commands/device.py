"""LCD device lifecycle, frame, display, overlay, mask, video, screencast Commands."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from ..errors import (
    DeviceNotConnectedError,
    DeviceNotFoundError,
    HandshakeError,
    ThemeError,
    TransportError,
    TrccError,
)
from ..events import (
    BackgroundChanged,
    BrightnessChanged,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    ErrorOccurred,
    FitModeChanged,
    FrameSent,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OrientationChanged,
    OverlayChanged,
    ScreencastStarted,
    ScreencastStopped,
    SplitModeChanged,
    VideoStarted,
    VideoStopped,
)
from ..models import FitMode, OverlayElement
from ..registry import find_product
from ..results import (
    ActiveDeviceResult,
    BackgroundModeResult,
    BackgroundResult,
    BootAnimationResult,
    BrightnessResult,
    ConnectResult,
    DisconnectResult,
    DiscoverResult,
    FitModeResult,
    LcdSnapshotResult,
    LoopVideoResult,
    MaskApplyResult,
    MaskPositionResult,
    MaskVisibilityResult,
    OrientationResult,
    OverlayBackgroundResult,
    OverlayConfigResult,
    OverlayElementDeleteResult,
    OverlayElementResult,
    OverlayResult,
    PauseVideoResult,
    RenderDcResult,
    RenderResult,
    ScreencastResult,
    SeekVideoResult,
    SendResult,
    SplitModeResult,
    VideoResult,
)
from ._base import Command
from ._helpers import (
    _BG_IMAGE_EXTS,
    _IMAGE_EXTS,
    _LEGACY_MASK_FILENAME,
    _VIDEO_EXTS_OK,
    _element_to_entry,
    _invalidate_scene,
    _publish_if_disconnect,
    _require_connected_device,
    _resolve_mask_path,
)

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


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

        from ..variants import get_variant_override
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
            device = _require_connected_device(app, self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, message=str(e))
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
            device = _require_connected_device(app, self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))

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
            message=(f"Sent {bytes_sent} bytes "
                     f"(#{self.r:02x}{self.g:02x}{self.b:02x})"
                     if ok else "Send returned False"),
        )

@dataclass(frozen=True, slots=True)
class SendImage(Command[SendResult]):
    """Push an image file to the LCD without staging it as a theme.

    Distinct from :class:`LoadImage` — that materialises a single-image
    theme directory under ``user_content_dir/single-image/`` and
    updates ``DeviceSettings.current_theme``.  ``SendImage`` is the
    no-persist variant: open, resize, encode, send — once, no theme,
    no settings mutation.  Used by API/CLI ``send-image`` uploads where
    the caller wants ephemeral display (boot logos, splash screens,
    quick previews).

    Acceptable extensions: PNG / JPG / JPEG / BMP / WEBP.  Honors
    per-device brightness + orientation + device-side rotation via
    ``DisplayService.build_image_frame``.
    """
    key: str
    path: Path

    def execute(self, app: App) -> SendResult:
        log.info("SendImage: key=%s path=%s", self.key, self.path)
        if not self.path.is_file():
            return SendResult(
                ok=False, key=self.key, bytes_sent=0,
                message=f"Image file not found: {self.path}",
            )
        if self.path.suffix.lower() not in _IMAGE_EXTS:
            return SendResult(
                ok=False, key=self.key, bytes_sent=0,
                message=(
                    f"Unsupported image extension {self.path.suffix!r}; "
                    f"supported: {', '.join(sorted(_IMAGE_EXTS))}"
                ),
            )
        try:
            device = _require_connected_device(app, self.key)
        except DeviceNotFoundError as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))

        try:
            frame = app.display.build_image_frame(
                info=device.info, path=self.path, profile=device.profile,
            )
            ok = device.send(frame)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            _publish_if_disconnect(app, self.key, e)
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))
        except TrccError as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              message=str(e))

        bytes_sent = len(frame) if ok else 0
        if ok:
            app.keepalive.store(self.key, frame)
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key, bytes_sent=bytes_sent,
            message=(f"Sent {bytes_sent} bytes from {self.path.name}"
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
            device = _require_connected_device(app, self.key)
        except DeviceNotFoundError as e:
            return RenderResult(ok=False, key=self.key, message=str(e))

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
        from ...services.metrics_personalize import personalize_readings
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
class RenderDcStandalone(Command[RenderDcResult]):
    """Render a DC config standalone — no active device, no theme load.

    Used by theme-developer previews (``trcc display overlay``) and
    API ``POST /display/render-dc`` to see what a ``config1.dc`` would
    look like against a solid-black background at an explicit
    resolution.  Writes the result as PNG to ``output_path`` so it's
    inspectable in any image viewer.  ``output_path`` is required —
    callers that want bytes-in-result subscribe to the future API
    streaming variant instead.

    Sensors come from the platform's enumerator so metric elements
    show live values; clock elements use the current wall clock.
    """
    dc_path: Path
    output_path: Path
    width: int = 320
    height: int = 320

    def execute(self, app: App) -> RenderDcResult:
        from ...services.overlay import OverlayService

        log.info(
            "RenderDcStandalone: dc=%s out=%s size=%dx%d",
            self.dc_path, self.output_path, self.width, self.height,
        )
        if self.width <= 0 or self.height <= 0:
            return RenderDcResult(
                ok=False, output_path=str(self.output_path),
                width=self.width, height=self.height,
                message=(f"invalid render size {self.width}x{self.height} "
                         "(both dimensions must be > 0)"),
            )
        try:
            readings = {r.sensor_id: r.value for r in app.platform.sensors().discover()}
        except Exception:
            log.debug("RenderDcStandalone: sensor read failed", exc_info=True)
            readings = {}
        try:
            image, count, _parsed = OverlayService.render_dc_standalone(
                renderer=app.renderer,
                dc_path=self.dc_path,
                width=self.width, height=self.height,
                sensors=readings,
            )
        except ThemeError as e:
            return RenderDcResult(
                ok=False, output_path=str(self.output_path),
                width=self.width, height=self.height,
                message=f"DC parse failed: {e}",
            )
        png = app.renderer.encode_png(image)
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_bytes(png)
        except OSError as e:
            return RenderDcResult(
                ok=False, output_path=str(self.output_path),
                width=self.width, height=self.height,
                element_count=count,
                message=f"Render OK but write failed: {e}",
            )
        return RenderDcResult(
            ok=True, output_path=str(self.output_path),
            width=self.width, height=self.height,
            element_count=count,
            message=(f"Rendered {count} element(s) to "
                     f"{self.output_path} ({self.width}x{self.height})"),
        )

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
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return BootAnimationResult(
                ok=False, key=self.key, message=str(e),
                frames_total=len(self.frame_paths),
            )
        # Capability gate BEFORE the connection check: boot anim is
        # SCSI-only regardless of connection state, so a HID/LED device
        # gets the clear "not a SCSI LCD" message even when unconnected.
        if not device.can_boot_animate:
            return BootAnimationResult(
                ok=False, key=self.key, frames_total=len(self.frame_paths),
                message=f"{self.key} is not a SCSI LCD (boot animation is SCSI-only)",
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
        from ...services import _dc as Dc
        from ...services.overlay import OverlayService
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
                    from ..models import OverlayElement
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
class ToggleVideo(Command[PauseVideoResult]):
    """Flip video playback between paused / playing — single-verb helper.

    Reads the current pause state and dispatches the inverse via
    :class:`PauseVideo`.  Useful for spacebar-style keybinds in the
    GUI / single-button CLI scripts where the caller doesn't know
    (or care) whether the video is currently paused.
    """
    key: str

    def execute(self, app: App) -> PauseVideoResult:
        playback = app.media.playback(self.key)
        if playback is None:
            return PauseVideoResult(
                ok=False, key=self.key, paused=False,
                message=f"No active video playback for {self.key}",
            )
        new_state = not playback.paused
        return PauseVideo(key=self.key, paused=new_state).execute(app)

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
class SetActiveDevice(Command[ActiveDeviceResult]):
    """Persist the user's currently-selected device key.

    Used by multi-device UIs (CLI ``device select``, GUI sidebar
    switch) to remember which device the user last steered.  Passing
    ``None`` clears the selection.  No device-side effect — just app
    state.  Callers resolve any ordinal-to-key mapping at their edge
    before dispatch.
    """
    key: str | None

    def execute(self, app: App) -> ActiveDeviceResult:
        log.info("SetActiveDevice.execute: key=%r", self.key)
        app.settings.set_active_device(self.key)
        return ActiveDeviceResult(
            ok=True, active_device=self.key,
            message=("active device cleared" if self.key is None
                     else f"active device set to {self.key}"),
        )
