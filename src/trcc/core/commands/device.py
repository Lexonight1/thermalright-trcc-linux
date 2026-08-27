"""LCD device lifecycle, frame, display, overlay, mask, video, screencast Commands."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from .._safe import is_under
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
from ..models import (
    OVERLAY_DEFAULT_CLOCK_SOURCE,
    OVERLAY_DEFAULT_COLOR,
    OVERLAY_DEFAULT_FORMAT,
    OVERLAY_DEFAULT_SIZE,
    FitMode,
    HandshakeResult,
    OverlayElement,
    ThemeDir,
    Wire,
    oriented_resolution,
)
from ..protocol import artwork_variant, mask_variant
from ..registry import find_product
from ..results import (
    ActiveDeviceResult,
    BackgroundModeResult,
    BackgroundResult,
    BootAnimationResult,
    BrightnessResult,
    ConnectionIssuesResult,
    ConnectResult,
    DeviceStateResult,
    DisconnectResult,
    DiscoverResult,
    FitModeResult,
    LcdSnapshotResult,
    LoopVideoResult,
    MaskApplyResult,
    MaskPositionResult,
    MaskVisibilityResult,
    MediaPlayerResult,
    OrientationResult,
    OverlayBackgroundResult,
    OverlayConfigResult,
    OverlayElementDeleteResult,
    OverlayElementResult,
    OverlayLayoutResult,
    OverlayResult,
    PauseVideoResult,
    PreviewResult,
    RenderDcResult,
    RenderResult,
    ScreencastResult,
    SeekVideoResult,
    SendResult,
    SplitModeResult,
    VideoResult,
    VideoStatusResult,
)
from ._base import Command, Query
from ._helpers import (
    _element_to_entry,
    _invalidate_scene,
    _publish_if_disconnect,
    _require_connected_device,
    _resolve_mask_path,
    resolve_overlay_layout,
)

if TYPE_CHECKING:
    from ...app import App
    from ..ports import Device

from ..logs import per_frame
from ..models import MEDIA, MediaKind

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


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
        log.info("DiscoverDevices: scanning")
        live = app.platform.scan_devices()
        # Cache the fingerprints so ConnectDevice→attach can resolve per-device
        # firmware quirks (bcdDevice isn't in the static registry).  (#228)
        app.remember_scan(live)
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
        # One install pass per unique resolution, handed to the background
        # runner: discovery must not wait on ~30 MB of archives, and the
        # runner de-duplicates so ConnectDevice re-submitting the same panel
        # costs nothing.  Grids fill in when ``DataInstalled`` lands.  (#275)
        for resolution in seen_resolutions:
            app.data_install_runner.submit(resolution)
        log.info(
            "DiscoverDevices: %d live, %d recognised, resolutions=%s",
            len(live), len(products), sorted(seen_resolutions),
        )
        return DiscoverResult(
            ok=True,
            message=f"{len(products)} device(s) found",
            products=products,
            devices=live,
        )

def _suspended_panel_hints(app: App, vid: int, pid: int) -> list[str]:
    """Explain a failed handshake when the panel is merely ASLEEP.

    A USB-suspended panel and a dead one produce the identical error --
    ``[Errno 110] Operation timed out`` -- so a reporter (and I) cannot tell
    "working as designed" from "broken".  #150 cost two months partly on that
    ambiguity: the kernel autosuspends the panel ~10s after the frame stream
    stops (by design, #143 -- it is how the firmware sleeps), and the next
    connect times out looking like a hardware fault.

    The app already knows.  It just never said.  Best-effort by design: a
    Platform that cannot read USB power returns None and we add nothing --
    a diagnostic must never become the thing that breaks the diagnosis.
    """
    try:
        power = app.platform.usb_power_state(vid, pid)
    except Exception:
        log.exception("_suspended_panel_hints: usb_power_state raised")
        return []
    if power is None or not power.suspended:
        return []
    asleep = (f" (asleep for {power.suspended_time_ms}ms)"
              if power.suspended_time_ms else "")
    log.warning("connect failed while the panel was USB-SUSPENDED%s "
                "(control=%s) — this is #150, not a broken device",
                asleep, power.control)
    return [
        f"The panel is USB-suspended{asleep}, not broken — it sleeps when "
        f"nothing is drawing to it (power/control={power.control}).",
        "Keep a stream running to hold it awake: trcc display play <key>",
    ]


@dataclass(frozen=True, slots=True)
class ConnectDevice(Command[ConnectResult]):
    """Attach + handshake with a discovered device."""
    key: str

    def _handshake_with_quirk_retry(self, app: App, device: Device,
                                    vid: int, pid: int) -> HandshakeResult:
        """Handshake, retrying once on a firmware's overriding transport.

        The override goes SECOND, never first.  It is keyed on
        ``(vid, pid, bcdDevice)`` and that fingerprint is not unique —
        Thermalright ships several different panels as ``0416:5302`` firmware
        4.07.  At least one of them handshakes correctly on the ordinary
        transport at its true landscape size (#267), while the override has
        never been confirmed to drive real hardware by anyone, including the
        reporter who supplied it.  Trying the proven path first means an
        unproven one can only ever help: it cannot take away a path that was
        already working.
        """
        try:
            return device.connect()
        except (HandshakeError, TransportError) as e:
            if not device.quirks.hid_reports:
                raise
            log.info("ConnectDevice %s: ordinary transport failed (%s) — "
                     "retrying on the firmware's HID output-report transport",
                     self.key, e)
            app.detach(self.key)
            retried = app.attach(vid, pid, quirk_transport=True)
            result = retried.connect()
            log.info("ConnectDevice %s: the quirk transport handshook where "
                     "the ordinary one did not", self.key)
            return result

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
            hints = app.platform.check_permissions()
            app.events.publish(ErrorOccurred(message=str(e), kind="not_found",
                                             key=self.key, hints=hints))
            result = ConnectResult(ok=False, key=self.key, message=str(e),
                                   hints=hints)
            app.note_connect_issue(result)
            return result
        except (ImportError, OSError) as e:
            # Building the transport can fail for reasons that are the user's
            # environment, not a missing device: a firmware quirk selects the
            # HID output-report transport, and hidapi may be absent or be the
            # wrong one of the two PyPI packages that both import as ``hid``
            # (#244, #253).  Those raise from the transport constructor, and an
            # uncaught raise here takes the GUI down instead of telling anyone
            # what to install — so report it like any other connect failure.
            hints = app.platform.check_permissions()
            log.warning("ConnectDevice %s: transport unavailable — %s",
                        self.key, e)
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key, hints=hints))
            result = ConnectResult(ok=False, key=self.key, message=str(e),
                                   hints=hints)
            app.note_connect_issue(result)
            return result

        try:
            handshake = self._handshake_with_quirk_retry(app, device, vid, pid)
            device = app.devices[self.key]      # the retry may have rebuilt it
        except (HandshakeError, TransportError, ImportError, OSError) as e:
            app.detach(self.key)   # clears any prior issue for this key first
            hints = app.platform.check_permissions()
            hints += _suspended_panel_hints(app, vid, pid)
            app.events.publish(ErrorOccurred(message=str(e), kind="handshake",
                                             key=self.key, hints=hints))
            result = ConnectResult(ok=False, key=self.key, message=str(e),
                                   hints=hints)
            app.note_connect_issue(result)
            return result

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
            if override.display_name:
                # The registry's ``product`` is one string for a USB id that
                # covers dozens of coolers, so it names the wrong one for most
                # of them (#272).  The handshake knows which cooler this is;
                # when we have a confirmed name for that fingerprint, use it.
                patch["product"] = override.display_name
            if patch:
                device.info = _dc_replace(device.info, **patch)
                log.info(
                    "ConnectDevice %s: variant override PM=%d SUB=%d → %s",
                    self.key, handshake.pm_byte, handshake.sub_byte,
                    override.button_image or "(cutout-only)",
                )

        # LED style: enrich ``led_style`` from the handshake.  The registry
        # leaves it None ("resolved at runtime"), so without this the LED
        # handler reads None and falls back to a default style.  Prefer the
        # LED adapter's already-resolved style — it honours the header
        # fingerprint, so a Magic Qube (shares PM=208 with CZ1) isn't
        # mis-tagged as CZ1 — and fall back to the header/PM registry from the
        # raw handshake bytes.  resolve_* live in core, so this stays inside
        # the hexagon.
        if device.info.wire is Wire.LED and device.info.led_style is None:
            led_hs = getattr(device, "led_handshake", None)
            led_style = getattr(led_hs, "style", None)
            if led_style is None:
                from ..led_protocol import resolve_handshake
                led_entry = resolve_handshake(
                    handshake.raw_response[:4],
                    handshake.pm_byte, handshake.sub_byte,
                )
                led_style = led_entry.style if led_entry is not None else None
            if led_style is not None:
                device.info = _dc_replace(device.info, led_style=led_style)
                log.info(
                    "ConnectDevice %s: LED style → %s",
                    self.key, led_style.value,
                )

        # Install theme/cloud/mask data for the HANDSHAKE-resolved resolution.
        # Non-square bulk panels report native_resolution=(0,0) and only learn
        # their real size here, so DiscoverDevices' static-resolution pass
        # skipped them — they'd have no data on disk.  ensure_all installs BOTH
        # orientations for non-square panels (so portrait themes/masks exist).
        #
        # SUBMITTED, not called: this is the port of legacy's
        # ``_ensure_data_background(device, w, h)`` — background is the whole
        # point of it.  Run inline it downloaded six archives before connect
        # returned, and since the GUI's splash waits on connect, the main
        # window could not appear until then. (#136, #275)
        # First boot for this panel: if the cooler bolted it in sideways, start
        # the owner at 90 degrees instead of making them find the dial.  The
        # SUB byte already told us at handshake; the C# spends it here too
        # (SetThemeInfo_ThemeML seeds themeDirection).  Re-connects are a
        # no-op — the device already has persisted settings by then.
        profile = getattr(device, "profile", None)
        if profile is not None:
            app.settings.seed_mount_orientation(
                self.key, profile.portrait_mounted)

        w, h = handshake.resolution
        if w and h:
            # Which artwork LIBRARY, not just which size.  Two coolers can
            # share a panel and ship different chrome: the C# picks
            # 1600720u/l/'' from the SUB byte and zt480480y from PM
            # (FormCZTV.cs:1290, :5746).  This is the one place both bytes and
            # the resolution are known together, so it is where the choice is
            # made -- everything downstream takes a resolved suffix.
            variant = artwork_variant((w, h), handshake.sub_byte)
            masks = mask_variant((w, h), handshake.sub_byte, handshake.pm_byte)
            log.info("ConnectDevice %s: data library %dx%d variant=%r mask=%r "
                     "(sub=%d pm=%d)", self.key, w, h, variant, masks,
                     handshake.sub_byte, handshake.pm_byte)
            app.data_install_runner.submit((w, h), variant, masks)

        # Hand the wire to a per-device send worker — it owns every write
        # from here (serialization + Bulk/LY keepalive) until disconnect.
        app.start_sender(self.key)

        app.clear_connect_issue(self.key)   # it came up — drop any past failure
        app.events.publish(DeviceConnected(
            key=self.key, resolution=handshake.resolution,
        ))
        return ConnectResult(
            ok=True, key=self.key,
            message=f"Connected: {handshake.resolution}",
            handshake=handshake,
        )


@dataclass(frozen=True, slots=True)
class EnsureConnected(Command[ConnectResult]):
    """Bring *key* up for a wire command only if it isn't already connected.

    The idempotent counterpart to :class:`ConnectDevice`.  A stateless CLI
    process holds no attached devices, so a wire command (``color``, ``play``,
    ``load-theme`` …) dispatched from a fresh process would fail with "not
    connected".  This attaches + handshakes once; when a daemon/GUI already
    holds the device it is a pure no-op — unlike ``ConnectDevice`` it never
    rebuilds the transport or re-handshakes a live device, so it is safe to
    call before every wire command and once before a render/play loop without
    disrupting an active stream.  (``ConnectDevice`` keeps its always-handshake
    contract for the reconnect / dev-console inject-reply paths.)
    """
    key: str

    def execute(self, app: App) -> ConnectResult:
        existing = app.devices.get(self.key)
        if existing is not None and existing.is_connected:
            log.debug("EnsureConnected %s: already connected — no-op", self.key)
            return ConnectResult(
                ok=True, key=self.key,
                message=f"{self.key} already connected",
            )
        log.info("EnsureConnected %s: not connected — attaching", self.key)
        return app.dispatch(ConnectDevice(key=self.key))


@dataclass(frozen=True, slots=True)
class DeviceConnectionIssues(Query[ConnectionIssuesResult]):
    """Query devices that were found but failed to connect (with per-OS hints).

    The bus-pure way for any UI to learn *current* connect failures — used on
    startup to catch failures that fired before the UI subscribed, and as the
    pull side alongside the ``ErrorOccurred`` push event.  Read-only.
    """

    def execute(self, app: App) -> ConnectionIssuesResult:
        issues = app.connection_issues()
        return ConnectionIssuesResult(
            ok=True, issues=issues,
            message=f"{len(issues)} device(s) failed to connect",
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
            _require_connected_device(app, self.key)
        except (DeviceNotFoundError, DeviceNotConnectedError) as e:
            return SendResult(ok=False, key=self.key, connected=False,
                              message=str(e))
        try:
            ok = app.send(self.key, self.data)
        except TransportError as e:
            app.events.publish(ErrorOccurred(message=str(e), kind="transport",
                                             key=self.key))
            _publish_if_disconnect(app, self.key, e)
            return SendResult(ok=False, key=self.key, connected=True,
                              message=str(e))
        bytes_sent = len(self.data) if ok else 0
        if ok:
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key, connected=True,
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
        except (DeviceNotFoundError, DeviceNotConnectedError) as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              connected=False, message=str(e))

        try:
            frame = app.display.build_solid_color_frame(
                info=device.info,
                color=(self.r, self.g, self.b),
                profile=device.profile,
            )
            ok = app.send(self.key, frame)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            _publish_if_disconnect(app, self.key, e)
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              connected=True, message=str(e))

        bytes_sent = len(frame) if ok else 0
        if ok:
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key, bytes_sent=bytes_sent, connected=True,
            message=(f"Sent {bytes_sent} bytes "
                     f"(#{self.r:02x}{self.g:02x}{self.b:02x})"
                     if ok else "Send returned False"),
        )

@dataclass(frozen=True, slots=True)
class SleepDevice(Command[SendResult]):
    """Blank a connected panel so it goes dark on shutdown / quit (#143).

    The unified "turn the screen off" action every surface dispatches — the
    GUI/daemon shutdown hook (``App.close``), ``trcc display sleep`` (CLI),
    and the ``/sleep`` API route all send this one Command.  Mirrors the C#
    exit path (stop streaming + close the device) but adds an explicit dark
    frame first, so the panel visibly clears instead of holding its last
    image until the firmware idle-sleeps.

    Composes the existing wire paths rather than duplicating them: LCD →
    :class:`SendColor` (a solid-black frame via
    ``DisplayService.build_solid_color_frame``); LED → :class:`SetLedColors`
    with ``global_on=False`` (an all-off payload).  Best-effort by design —
    a device mid-unplug returns ``ok=False`` instead of raising, so it can
    never abort ``App.close`` mid-shutdown.
    """
    key: str

    def execute(self, app: App) -> SendResult:
        log.info("SleepDevice: key=%s", self.key)
        device = app.devices.get(self.key)
        if device is None or not device.is_connected:
            log.info("SleepDevice %s: not connected — nothing to blank",
                     self.key)
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              connected=False,
                              message=f"{self.key} not connected")
        if device.is_led:
            from .led import SetLedColors
            result = SetLedColors(
                key=self.key, colors=[(0, 0, 0)], global_on=False,
            ).execute(app)
            return SendResult(ok=result.ok, key=self.key, bytes_sent=0,
                              message=result.message)
        return SendColor(key=self.key, r=0, g=0, b=0).execute(app)


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
        if MEDIA.kind_of(self.path) is not MediaKind.IMAGE:
            return SendResult(
                ok=False, key=self.key, bytes_sent=0,
                message=(
                    f"Unsupported image extension {self.path.suffix!r}; "
                    f"supported: {', '.join(sorted(MEDIA.exts(MediaKind.IMAGE)))}"
                ),
            )
        try:
            device = _require_connected_device(app, self.key)
        except (DeviceNotFoundError, DeviceNotConnectedError) as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              connected=False, message=str(e))

        try:
            frame = app.display.build_image_frame(
                info=device.info, path=self.path, profile=device.profile,
            )
            ok = app.send(self.key, frame)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            _publish_if_disconnect(app, self.key, e)
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              connected=True, message=str(e))
        except TrccError as e:
            return SendResult(ok=False, key=self.key, bytes_sent=0,
                              connected=True, message=str(e))

        bytes_sent = len(frame) if ok else 0
        if ok:
            app.events.publish(FrameSent(key=self.key, bytes_sent=bytes_sent))
        return SendResult(
            ok=ok, key=self.key, bytes_sent=bytes_sent, connected=True,
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
        except (DeviceNotFoundError, DeviceNotConnectedError) as e:
            return RenderResult(ok=False, key=self.key, connected=False,
                                message=str(e))

        theme = app.active_themes.get(self.key)
        if theme is None:
            return RenderResult(
                ok=False, key=self.key, connected=True,
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
            # Per-tick hot path (metrics observer, video tick): fire-and-forget
            # so producers never block on USB.  A worker write failure surfaces
            # via the sender's on_failure → DeviceDisconnected (increment 9).
            ok = app.send(self.key, frame, wait=False)
        except TransportError as e:
            app.events.publish(ErrorOccurred(
                message=str(e), kind="transport", key=self.key,
            ))
            _publish_if_disconnect(app, self.key, e)
            return RenderResult(
                ok=False, key=self.key, theme_name=theme.name,
                connected=True, message=str(e),
            )

        if ok:
            # Carry the just-rendered surface so the GUI preview shows THIS
            # frame instead of re-rendering the whole pipeline (legacy's
            # publish-the-frame, observe-it shape).
            app.events.publish(FrameSent(
                key=self.key, bytes_sent=len(frame),
                surface=app.display.rendered_surface(self.key),
            ))
        return RenderResult(
            ok=ok, key=self.key, connected=True,
            bytes_sent=len(frame), theme_name=theme.name,
            message=(f"Rendered + sent {len(frame)} bytes"
                     if ok else "Render built frame but send returned False"),
        )


@dataclass(frozen=True, slots=True)
class CurrentFrame(Query[PreviewResult]):
    """The frame the device is showing RIGHT NOW — without rendering one.

    :class:`BuildPreview` renders to answer: it reads the sensors, composites
    the theme and hands back a fresh frame.  This returns the surface the
    render pipeline last produced and cached — no sensor read, no composite.

    Two questions, so two Queries, for the reason :class:`VideoStatus` is not
    :class:`TickDisplay`: a UI that only wants to MIRROR the panel should not
    have to drive a render to do it.  Folding this into ``BuildPreview`` as a
    ``cached=`` flag was considered and rejected — ``TickDisplay``'s own
    docstring records why a flag that changes WHAT WORK HAPPENS is a second
    Command, while ``encode=`` (which only picks the carrier for one render)
    is not.

    ``ok=True`` with ``surface=None`` is a normal answer, not a failure:
    nothing has been rendered yet (pre-load), or the scene was invalidated.

    Polled by preview panels, so logged at DEBUG.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    key: str

    def execute(self, app: App) -> PreviewResult:
        surface = app.display.rendered_surface(self.key)
        if surface is None:
            log.debug("CurrentFrame: %s has no cached frame", self.key)
            return PreviewResult(
                ok=True, key=self.key,
                message="No frame rendered yet",
            )
        width, height = app.renderer.surface_size(surface)
        log.debug("CurrentFrame: %s → cached %dx%d surface",
                  self.key, width, height)
        return PreviewResult(
            ok=True, key=self.key, surface=surface,
            width=width, height=height,
            message=f"Cached frame {width}x{height}",
        )


@dataclass(frozen=True, slots=True)
class TickDisplay(Command[RenderResult]):
    """Advance a video playback one frame, then render + send (one tick).

    The *animation* tick, as distinct from :class:`RenderAndSend`, which is the
    *re-render* tick.  Keeping them as two Commands rather than one with an
    ``advance`` flag is deliberate: ``RenderAndSend`` has six call sites in two
    roles, and a flag makes passing the wrong value easy and its failure silent
    (``DeviceRenderObserver`` fires on every ``SensorsUpdated`` — were it to
    advance, a playing video would speed up whenever sensors moved).

    Exists because the advance used to live in the UIs: the GUI timer, the CLI
    ``display play`` loop and the REST tick route each reached
    ``app.media.playback(key)`` and advanced it by hand.  Three transcriptions
    of one behaviour, none of them daemon-safe — ``AppProxy`` exposes
    ``dispatch`` and nothing else, so ``app.media`` raises under
    ``TRCC_DAEMON=1`` (#249).  qtgui, having no such copy, never advanced at all
    and showed frame 0 forever.

    Deliberately does NOT dispatch ``RestoreDeviceState``: the REST route needs
    that per tick because a stateless poller may arrive with no theme loaded,
    but the GUI would then pay a restore 15-30 times a second.  Self-priming is
    the caller's concern; advancing is this Command's.

    Per-tick: logged at DEBUG so a default INFO run isn't drowned.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    key: str

    def execute(self, app: App) -> RenderResult:
        playback = app.media.playback(self.key)
        if playback is None or not playback.frames:
            # No video on this device — a plain re-render.  Video fields stay
            # None so a UI can tell "not a video" from "frame 0 of a video".
            log.debug("TickDisplay %s: no playback — plain render", self.key)
            return RenderAndSend(key=self.key).execute(app)

        # ``Playback.advance`` self-guards on ``paused`` (returns the current
        # frame without moving the cursor), so pause needs no check here — and
        # the callers that used to hand-roll one lose it rather than move it.
        playback.advance()
        # Resolved values, not intent: the frame actually reached is what
        # reproduces "the video is stuck" / "it plays at the wrong speed"
        # from a reporter's log alone.  Per-tick, so DEBUG.
        frame_log.debug(
            "TickDisplay %s: advanced to frame %d/%d (interval=%dms, paused=%s)",
            self.key, playback.cursor, playback.frame_count,
            playback.interval_ms, playback.paused,
        )
        result = RenderAndSend(key=self.key).execute(app)
        return replace(
            result,
            cursor=playback.cursor,
            frame_count=playback.frame_count,
            interval_ms=playback.interval_ms,
        )


@dataclass(frozen=True, slots=True)
class BuildPreview(Query[PreviewResult]):
    """Render the device's active theme and stop before the wire.

    The third member of the render family — :class:`RenderAndSend` renders and
    pushes, :class:`TickDisplay` advances then renders and pushes, this one
    renders and hands the frame back.

    Exists because all four UIs had transcribed the same four-step block:
    look up the device, look up its active theme, read the sensors, call
    ``display.build_preview_surface``.  Four copies, none daemon-safe
    (``AppProxy`` exposes ``dispatch`` and nothing else, #249) — and they had
    already drifted apart: three fed the renderer RAW readings while the wire
    path fed PERSONALIZED ones, so a user on °F saw the Celsius number under a
    °F glyph, and a user with HDD disabled saw disk metrics the panel omitted.
    Personalizing here is what makes the preview agree with the glass.

    ``encode`` selects what the caller gets back, because that is the only
    thing the four differed by:

      * ``""``    — surface only.  No encode cost; the Qt skins render it.
      * ``"png"`` — lossless bytes; overlay text + CJK glyphs stay legible.
      * ``"jpeg"``— lossy bytes for streaming, where a PNG per frame would
        burn the bandwidth.

    ``sample_cols`` additionally returns a row-major RGB grid, sized from the
    surface's own aspect ratio, for terminal previews.  The surface always
    comes back too when it exists — it is the thing that was built.

    Polled by preview panels, so logged at DEBUG.
    """
    key: str
    encode: Literal["", "png", "jpeg"] = ""
    sample_cols: int = 0

    def execute(self, app: App) -> PreviewResult:
        try:
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            return PreviewResult(ok=False, key=self.key, message=str(e))

        theme = app.active_themes.get(self.key)
        if theme is None:
            log.debug("BuildPreview: %s has no active theme", self.key)
            return PreviewResult(
                ok=True, key=self.key,
                message="No active theme — nothing to preview",
            )

        # Same personalization the wire path applies (RenderAndSend, and
        # SaveTheme when it snapshots the thumbnail): sources deliver °C and
        # every disk key, the user's prefs are applied once, here.
        from ...services.metrics_personalize import personalize_readings
        s = app.settings.app
        sensors = personalize_readings(
            app.platform.sensors().read_all(),
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )

        try:
            surface = app.display.build_preview_surface(
                info=device.info, theme=theme, sensors=sensors,
                profile=device.profile,
            )
            width, height = app.renderer.surface_size(surface)
            image, media_type = self._encode(app, surface)
            pixels = self._sample(app, surface, width, height)
        except Exception as e:
            # A preview that raises must not take the panel down with it —
            # every UI wrapped this call for that reason.  Covers the encode
            # and the sample too: a Renderer that implements neither (a bare
            # test double) is a failed Result, not a crashed panel.  Surfaced
            # through App.dispatch's WARNING, never swallowed.
            log.warning("BuildPreview %s: render raised — %s: %s",
                        self.key, type(e).__name__, e)
            return PreviewResult(
                ok=False, key=self.key, theme_name=theme.name,
                message=f"Preview render failed — {type(e).__name__}: {e}",
            )

        log.debug(
            "BuildPreview %s: theme=%s %dx%d encode=%s bytes=%d grid=%s",
            self.key, theme.name, width, height,
            self.encode or "none", len(image),
            f"{self.sample_cols}x{len(pixels)}" if pixels else "none",
        )
        return PreviewResult(
            ok=True, key=self.key, surface=surface,
            image=image, media_type=media_type,
            width=width, height=height, theme_name=theme.name,
            pixels=pixels,
            message=f"Preview {width}x{height} of {theme.name}",
        )

    def _encode(self, app: App, surface: Any) -> tuple[bytes, str]:
        """Encode *surface* to the requested container, or nothing at all."""
        log.debug("BuildPreview %s: encode=%s", self.key, self.encode or "none")
        if self.encode == "png":
            return app.display.encode_png(surface), "image/png"
        if self.encode == "jpeg":
            return app.display.encode_jpeg(surface), "image/jpeg"
        return b"", ""

    def _sample(
        self, app: App, surface: Any, width: int, height: int,
    ) -> list[list[tuple[int, int, int]]]:
        """Sample *surface* into a ``sample_cols``-wide row-major RGB grid.

        Rows follow the surface's real aspect ratio so a 1920x462 panel does
        not come back as a square, and are kept even because the half-block
        renderer at the other end packs two rows per terminal cell.
        """
        if self.sample_cols <= 0:
            return []
        rows = max(2, round(self.sample_cols * height / width) if width else 2)
        if rows % 2:
            rows += 1
        log.debug("BuildPreview %s: sampling %dx%d grid from %dx%d surface",
                  self.key, self.sample_cols, rows, width, height)
        return app.renderer.get_pixels_rgb(surface, self.sample_cols, rows)


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
        if MEDIA.kind_of(self.path) is not MediaKind.ANIMATED:
            log.warning(
                "PlayVideo.execute: unsupported extension %r (allowed=%s)",
                self.path.suffix, sorted(MEDIA.exts(MediaKind.ANIMATED)),
            )
            return VideoResult(
                ok=False, key=self.key, path=str(self.path),
                message=(f"unsupported video extension {self.path.suffix!r} "
                         f"(expected one of {sorted(MEDIA.exts(MediaKind.ANIMATED))})"),
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
            canvas_size = device.profile.resolution
        else:
            canvas_size = device.info.native_resolution
        # Decode at the ORIENTED canvas — at 90/270 the render composes a
        # portrait canvas (``DisplayService._compose_geometry``), so a portrait
        # cloud video (web/480854) must decode to 480x854, not the native
        # landscape, or it gets squished into the wrong aspect. Same swap rule
        # the whole orientation pipeline keys on.
        orientation = app.settings.for_device(self.key).orientation
        canvas_size = oriented_resolution(canvas_size, orientation)

        # User-made assets in ``user_content_dir`` (``~/.trcc-user/data/``)
        # decode at NATIVE so the render pipeline's fit-mode (width /
        # height / stretch) actually has something to scale.  Program /
        # cloud assets under ``data_dir`` are pre-authored at the
        # device's canvas resolution, so they keep the canvas-size
        # decode (no rescale work for ffmpeg, and ``.zt`` is fixed-size
        # by format).  Anything outside both trees (ad-hoc playback of
        # an arbitrary file) defaults to canvas-size too — the user
        # would need to save it as a theme first to get fit-mode.
        is_user_asset = False
        if self.path.suffix.lower() != ".zt":
            try:
                user_root = app.platform.paths().user_content_dir().resolve()
                is_user_asset = is_under(self.path, user_root)
            except (OSError, AttributeError):
                # ``user_content_dir`` lookup or resolve failed (broken
                # platform paths, missing dir).  Default to canvas-size
                # decode so we never block playback over a probe failure.
                is_user_asset = False
        decode_size: tuple[int, int] | None = (
            None if is_user_asset else canvas_size
        )
        log.info(
            "PlayVideo.execute: target_size=%s (profile=%s, user_asset=%s)",
            "native" if decode_size is None else f"{decode_size[0]}x{decode_size[1]}",
            device.profile is not None, is_user_asset,
        )

        try:
            playback = app.media.load_video(
                device_key=self.key, path=self.path, size=decode_size,
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

        # Persist the override now that we know it LOADS.  Without this a
        # later ``theme save`` has nothing in ``DeviceSettings.background_path``
        # to bake in and the saved theme reloads with no background.
        #
        # This belongs to the Command, not its callers: the CLI and the two
        # API routes each wrote it themselves before dispatching (qtgui never
        # did, so that skin silently lost the background on save), and under
        # ``TRCC_DAEMON=1`` the CLI's write crashed outright — ``AppProxy``
        # exposes only ``dispatch``, so ``app.settings`` raised AttributeError
        # and play-video never even reached the wire (#249).
        app.settings.set_background_path(self.key, str(self.path))

        # Bust the scene cache so the next render picks up the override.
        _invalidate_scene(app, self.key)

        # Animated-vs-static gate: ffmpeg decoded exactly one frame, so this
        # "video"/gif is NOT animated.  Render it ONCE via BackgroundChanged
        # and do NOT publish VideoStarted, so the GUI never starts the 15fps
        # animation timer for a static background.  Restores the legacy
        # PIL frame-count check (lost in the PIL→ffmpeg refactor) using
        # ffmpeg's own decode count.
        if playback.frame_count <= 1:
            log.info(
                "PlayVideo.execute: %s is a STATIC single-frame background "
                "— rendering once, no animation timer", self.path.name,
            )
            app.events.publish(
                BackgroundChanged(key=self.key, path=str(self.path)),
            )
            return VideoResult(
                ok=True, key=self.key, path=str(self.path),
                frame_count=1,
                message=f"static background (1 frame): {self.path.name}",
            )

        # Per-frame interval derived from playback fps — handler observer
        # uses this to start the Qt animation timer.  ``Playback.interval_ms``
        # owns the 1000/fps clamp so TickDisplay reports the same cadence.
        fps = getattr(playback, "fps", 0) or 30
        interval_ms = playback.interval_ms
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
            device = app.get(self.key)
        except DeviceNotFoundError as e:
            log.warning(
                "StartScreencast.execute: device %s not found: %s",
                self.key, e,
            )
            return ScreencastResult(ok=False, key=self.key, message=str(e))

        # An LED controller has no panel to cast to.  Refused HERE, before
        # anything is written, because this Command PERSISTS the region: it
        # used to answer ok=True and leave ``screencast_region`` set on an
        # LED device, which is a wrong-state write rather than a wasted call
        # — and the capture driver reads exactly that field to decide what to
        # grab for.  Shaped like ``SetLedColors``' own guard, inverted.
        if device.is_led:
            log.warning(
                "StartScreencast.execute: %s is an LED controller — refused",
                self.key,
            )
            return ScreencastResult(
                ok=False, key=self.key,
                message=f"{self.key} is an LED controller — it has no panel",
            )

        # A live video playback overlay would race the screencast tick on
        # the same wire — stop it first so the handler owns the surface
        # cleanly.  StopVideo is idempotent so it's safe even if no
        # playback was loaded.
        StopVideo(key=self.key).execute(app)

        # Persist the region as the active display source (clears any
        # background/video override — the toggles are mutually exclusive) so
        # SaveTheme can bake it into a theme's ``screencast`` ref and a reload
        # can resume it.
        app.settings.set_screencast_region(
            self.key, (self.x, self.y, self.w, self.h, self.audio),
        )
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
        app.settings.set_screencast_region(self.key, None)
        app.events.publish(ScreencastStopped(key=self.key))
        return ScreencastResult(
            ok=True, key=self.key, active=False,
            message=f"screencast stopped on {self.key}",
        )

@dataclass(frozen=True, slots=True)
class SetMediaPlayer(Command[MediaPlayerResult]):
    """Set the device's media-player source — a URI (a resource on the computer)
    or a URL (a resource on the web / a stream).

    The media-player toggle's Command, sibling to :class:`SetBackground` /
    :class:`StartScreencast`, so every UI drives it the same way: dispatch,
    don't reach into settings.  Persists the source in
    ``DeviceSettings.media_player_uri`` (mutually exclusive with the other
    display sources) so ``SaveTheme`` can bake it into a theme's ``media_player``
    ref.  A LOCAL file starts playback through the same :class:`PlayVideo`
    pipeline; a web URL is referenced (persisted so a save captures it) — its
    continuous-streaming playback is a separate runtime feature.  Pass an empty
    ``uri`` to clear the source.
    """
    key: str
    uri: str

    def execute(self, app: App) -> MediaPlayerResult:
        log.info("SetMediaPlayer.execute: key=%s uri=%r", self.key, self.uri)
        uri = self.uri.strip()
        if not uri:
            app.settings.set_media_player_uri(self.key, None)
            return MediaPlayerResult(
                ok=True, key=self.key, uri="", playing=False,
                message=f"media-player source cleared for {self.key}",
            )
        try:
            app.get(self.key)
        except DeviceNotFoundError as e:
            log.warning("SetMediaPlayer.execute: device %s not found: %s",
                        self.key, e)
            return MediaPlayerResult(ok=False, key=self.key, uri=uri,
                                     message=str(e))

        if "://" in uri:   # a web URL / stream
            app.settings.set_media_player_uri(self.key, uri)
            log.info("SetMediaPlayer.execute: %r is a web URL — referenced "
                     "(streaming playback is a runtime feature)", uri)
            return MediaPlayerResult(
                ok=True, key=self.key, uri=uri, playing=False,
                message=(f"media-player source set to {uri} "
                         "(referenced; streaming playback pending)"),
            )

        local = Path(uri)   # a resource on the computer
        if not local.is_file():
            log.warning("SetMediaPlayer.execute: source does not exist: %s", uri)
            return MediaPlayerResult(
                ok=False, key=self.key, uri=uri,
                message=f"media-player source does not exist: {uri}",
            )
        app.settings.set_media_player_uri(self.key, uri)
        play = PlayVideo(key=self.key, path=local).execute(app)
        return MediaPlayerResult(
            ok=play.ok, key=self.key, uri=uri, playing=play.ok,
            message=(f"media-player playing {local.name}"
                     if play.ok else play.message),
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
        kind = MEDIA.kind_of(self.path)
        if kind is MediaKind.ANIMATED:
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
        if kind is MediaKind.IMAGE:
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
            ext, sorted(MEDIA.exts(MediaKind.IMAGE)),
            sorted(MEDIA.exts(MediaKind.ANIMATED)),
        )
        return BackgroundResult(
            ok=False, key=self.key, path=str(self.path),
            message=(f"unsupported background extension {ext!r} — "
                     f"expected image {sorted(MEDIA.exts(MediaKind.IMAGE))} or "
                     f"video {sorted(MEDIA.exts(MediaKind.ANIMATED))}"),
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
            log.warning("UploadBootAnimation %s: device not connected — "
                        "dispatched before ConnectDevice", self.key)
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
            # Hold the wire exclusively — a multi-frame boot-anim upload must
            # not interleave with the send worker's frame/keepalive writes.
            with app.exclusive_wire(self.key):
                uploaded = device.send_boot_animation(
                    encoded, list(self.delays_ds),
                )
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
                message=f"Brightness out of range (0–100): {self.percent}",
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
                candidate, ThemeDir.MASK,
            )
            return MaskApplyResult(
                ok=False, key=self.key, path=str(candidate),
                message=(f"mask path is neither a supported image file "
                         f"nor a legacy mask directory with "
                         f"{ThemeDir.MASK}: {candidate}"),
            )
        resolved = str(resolved_file.resolve())
        log.info("ApplyMask: resolved %s → %s", candidate, resolved)
        app.settings.set_mask_path(self.key, resolved)
        # Applying a mask makes it visible.  Legacy kept theme_mask_visible
        # True (Windows isDrawMbImage) and drew the mask whenever one was
        # set; without this the png stays hidden when mask_visible was left
        # False by a prior load — a DC-origin theme parses mask_visible=False
        # (_dc.py), so a saved/cloud mask applies its DC layout but the image
        # never composites ("dc shows, png doesn't").
        app.settings.set_mask_visible(self.key, True)
        log.info("ApplyMask: mask_visible=True for %s", self.key)
        # A mask is a new overlay-layout source — drop any live user edits
        # so the mask's own layout (set below) shows instead of being
        # shadowed by edits made against the previous layout.  Mirrors
        # legacy ``apply_mask``, which cleared the theme's overlay before
        # loading the mask's config1.dc, and the C#'s own order — the mask
        # read REPLACES the single array rather than merging into it
        # (``UCXiTongXianShiSet_UCXiTongXianShiSubArray`` clears then re-adds,
        # 2.1.6 UCXiTongXianShi.cs:100-126).  The repopulate is below, once
        # the mask's own elements are parsed; a mask with no readable DC
        # leaves the layer empty and the render falls back exactly as before.
        # Safe on every caller: LoadTheme's internal ApplyMask runs after
        # LoadTheme has already established the layer, and RestoreLastTheme
        # never calls ApplyMask.
        # NO pre-clear.  The repopulate below REPLACES the layer wholesale
        # when the mask carries a layout, so clearing first buys nothing — and
        # since an empty layer now means "draw nothing" rather than "fall back
        # to the theme", a mask with no readable config1.dc would have blanked
        # the overlay.  The warning branch below already promises the opposite
        # ("keeping theme's overlay layout"); without the pre-clear that
        # promise is true.
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
        mask_dc = ThemeDir(mask_dir).dc
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
                    # A mask is a source change: its layout BECOMES the
                    # device's working layer, exactly as the C# reads the
                    # mask's config1.dc into the same single array a theme
                    # load fills (2.1.6 FormCZTV.cs:5935,
                    # ``ReadSystemConfiguration(mask, readMyMode: false)``).
                    from ..models import OverlayElement
                    app.settings.set_user_overlay_elements(
                        self.key,
                        [OverlayElement.from_dict(el) for el in mask_elements],
                    )
                    log.info(
                        "ApplyMask: %s contributes %d overlay element(s) — "
                        "adopted as the working layer for %s",
                        resolved_file.name, len(mask_elements), self.key,
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
    color: str = OVERLAY_DEFAULT_COLOR
    size: int = OVERLAY_DEFAULT_SIZE
    #: Font family; "" keeps the theme default (OverlayElement.font).
    font: str = ""
    bold: bool = False
    italic: bool = False
    text: str = ""
    metric: str = ""
    format: str = OVERLAY_DEFAULT_FORMAT
    show_unit: bool = True
    source: str = OVERLAY_DEFAULT_CLOCK_SOURCE
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
        existing = {
            e.id for e in
            (app.settings.for_device(self.key).user_overlay_elements or ())
        }
        if eid in existing:
            return OverlayElementResult(
                ok=False, key=self.key, element=None,
                message=f"Overlay element id {eid!r} already exists",
            )
        element = OverlayElement(
            id=eid, type=self.type,  # type: ignore[arg-type]
            x=self.x, y=self.y, color=self.color, size=self.size,
            font=self.font, bold=self.bold, italic=self.italic,
            text=self.text,
            metric=self.metric, format=self.format,
            show_unit=self.show_unit,
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
    font: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    text: str | None = None
    metric: str | None = None
    format: str | None = None
    show_unit: bool | None = None
    source: str | None = None

    def execute(self, app: App) -> OverlayElementResult:
        try:
            element = app.settings.update_user_overlay_element(
                self.key, self.element_id,
                x=self.x, y=self.y, color=self.color, size=self.size,
                font=self.font, bold=self.bold, italic=self.italic,
                text=self.text,
                metric=self.metric, format=self.format,
                show_unit=self.show_unit, source=self.source,
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
        # Resolve against the EFFECTIVE layout on screen (user > mask >
        # theme), not just the user layer — a mask/theme supplies the live
        # elements while the user layer is empty, so a user-only lookup
        # never matched them (the "element 'N' not found" flash bug).
        # Same helper ResolveOverlay uses, so the id a UI was given there is
        # the id looked up here.
        for entry in resolve_overlay_layout(app, self.key).elements:
            if entry.id == self.element_id:
                app.events.publish(OverlayChanged(
                    key=self.key, enabled=True,
                    flash_element_id=self.element_id,
                    flash_duration_ms=self.duration_ms,
                ))
                return OverlayElementResult(
                    ok=True, key=self.key, element=entry,
                    message=f"Flashing overlay element {self.element_id} "
                            f"for {self.duration_ms}ms",
                )
        return OverlayElementResult(
            ok=False, key=self.key, element=None,
            message=f"Overlay element {self.element_id!r} not found",
        )

@dataclass(frozen=True, slots=True)
class ResolveOverlay(Query[OverlayLayoutResult]):
    """Ask what is on the device's screen — the read side of overlay.

    The other seven overlay Commands all mutate.  Nothing could ASK, so both
    Qt skins reached past the bus for the answer (one through an App method,
    one by importing the service outright) and cli/api simply had no way to
    find out what a device is displaying.

    Every element comes back with an id, minted positionally where a theme
    supplied none — see ``services.overlay.effective_overlay_layout``.  That
    id is what :class:`FlashOverlayElement` and
    :class:`UpdateOverlayElement` take, and both resolve through the same
    helper, so an id handed to a UI here is addressable there.

    Always ``ok=True``.  An empty layout is a normal answer, not a failure —
    a device with no theme loaded genuinely has nothing on screen, and
    ``ok=False`` escalates to WARNING through ``App.dispatch``, which would
    warn on every poll of a device sitting at its pre-load state.  Callers
    distinguish the empty answers with :attr:`~OverlayLayoutResult.source`
    and :attr:`~OverlayLayoutResult.enabled`.
    """
    key: str

    def execute(self, app: App) -> OverlayLayoutResult:
        # The whole answer is built in one place so Flash resolves the same
        # ids this hands out.  DEBUG because dispatch already logs entry and
        # outcome at INFO — this line only marks which caller asked.
        log.debug("ResolveOverlay: key=%s", self.key)
        return resolve_overlay_layout(app, self.key)


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
        # Report the device's actual toggle, not a hardcoded True.  This
        # Command sets the LAYOUT; whether the overlay is switched on is
        # ``DeviceSettings.overlay_enabled``'s to say (the single authority —
        # see the ``build_overlay`` comment in services/display.py).
        app.events.publish(OverlayChanged(
            key=self.key,
            enabled=app.settings.for_device(self.key).overlay_enabled,
        ))
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
class VideoStatus(Query[VideoStatusResult]):
    """Ask what a device's video playback is doing.  Read-only.

    The query half of the video surface: :class:`PlayVideo` /
    :class:`StopVideo` / :class:`PauseVideo` / :class:`SeekVideo` /
    :class:`LoopVideo` change it, this one reports it, and
    :class:`TickDisplay` advances it.  Without this, a UI wanting only to
    *know* had to reach for ``app.media.playback(key)`` — which raises under
    ``TRCC_DAEMON=1``, where the UI holds an ``AppProxy``.

    A device with no playback is ``ok=True, playing=False`` with the optional
    fields ``None`` — absence is a normal answer here, not a failure.
    """
    key: str

    def execute(self, app: App) -> VideoStatusResult:
        playback = app.media.playback(self.key)
        if playback is None:
            log.debug("VideoStatus: %s has no playback", self.key)
            return VideoStatusResult(
                ok=True, key=self.key, playing=False,
                message="no playback loaded",
            )
        log.debug("VideoStatus: %s frame %s/%s @ %s fps paused=%s",
                  self.key, playback.cursor, playback.frame_count,
                  playback.fps, playback.paused)
        return VideoStatusResult(
            ok=True, key=self.key,
            playing=True,
            paused=playback.paused,
            cursor=playback.cursor,
            frame_count=playback.frame_count,
            fps=playback.fps,
            loop=playback.loop,
            message=(f"playing frame {playback.cursor}/{playback.frame_count} "
                     f"@ {playback.fps} fps"
                     f"{' (paused)' if playback.paused else ''}"),
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
class LcdSnapshot(Query[LcdSnapshotResult]):
    """Per-device LCD state snapshot — what settings.for_device holds.

    Polled by UIs to refresh state — logged at DEBUG.
    """
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
            slideshow_enabled=s.slideshow_enabled,
            slideshow_interval_s=s.slideshow_interval_s,
            slideshow_themes=tuple(s.slideshow_themes),
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


@dataclass(frozen=True, slots=True)
class DeviceState(Query[DeviceStateResult]):
    """Report what a device is — identity, connection, handshake geometry.

    The query every UI was doing by hand: ``app.devices.get(key)`` followed by
    reads of ``.info`` / ``.profile`` / ``.is_connected``.  CLAUDE.md forbids a
    UI holding the Device, and ``app.devices`` is absent on the ``AppProxy`` a
    daemon-mode UI holds, so all 25 of those sites raised under
    ``TRCC_DAEMON=1`` (#249).

    An UNKNOWN key is ``ok=False``; a KNOWN but unconnected device is
    ``ok=True, connected=False`` with the handshake fields ``None`` — absence
    is a normal answer for a device we simply have not talked to yet.
    """
    key: str

    def execute(self, app: App) -> DeviceStateResult:
        device = app.devices.get(self.key)
        if device is None:
            log.debug("DeviceState: %s is not attached", self.key)
            return DeviceStateResult(
                ok=False, key=self.key,
                message=f"No device attached for {self.key}",
            )
        info = device.info
        profile = device.profile
        handshake = device.handshake
        log.debug("DeviceState: %s %s connected=%s profile=%s",
                  self.key, info.product, device.is_connected,
                  profile.resolution if profile else None)
        return DeviceStateResult(
            ok=True, key=self.key,
            vid=info.vid, pid=info.pid,
            vendor=info.vendor, product=info.product,
            wire=info.wire.value, kind=info.kind.value,
            model=info.model or "",
            button_image=info.button_image or "",
            native_resolution=info.native_resolution,
            connected=device.is_connected,
            is_led=device.is_led,
            resolution=profile.resolution if profile else None,
            jpeg=profile.jpeg if profile else None,
            rotate=profile.rotate if profile else None,
            widescreen=profile.widescreen if profile else None,
            pm_byte=handshake.pm_byte if handshake else None,
            sub_byte=handshake.sub_byte if handshake else None,
            fbl=info.fbl if handshake else None,
            led_style=getattr(info, "led_style", None),
            message=f"{info.product} ({info.wire.value})",
        )
