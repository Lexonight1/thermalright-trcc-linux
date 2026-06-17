"""App — the hub that wires Platform + Devices + EventBus + Commands.

Holds one Platform (the OS), one dict of live Devices keyed by their
'vid:pid' string, and one EventBus.  UIs dispatch Commands through
`app.dispatch(cmd)`; nothing else touches devices directly.
"""
from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, TypeVar

from .adapters.device import DeviceFactory
from .adapters.repo.github_releases import GitHubReleases
from .adapters.repo.http import UrllibHttpFetcher
from .adapters.theme.cloud import CzhordeCatalog
from .core.commands import Command
from .core.errors import DeviceDisconnectedError, DeviceNotFoundError
from .core.events import (
    BackgroundChanged,
    BrightnessChanged,
    DateFormatChanged,
    DeviceAttached,
    DeviceDisconnected,
    ErrorOccurred,
    EventBus,
    FitModeChanged,
    LedSettingsChanged,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OrientationChanged,
    OverlayChanged,
    SensorsUpdated,
    SplitModeChanged,
    TempUnitChanged,
    TimeFormatChanged,
    VideoStarted,
    VideoStopped,
)
from .core.led_models import LedRuntimeState
from .core.models import HardwareMetrics, Theme, Wire, oriented_resolution
from .core.ports import Device, Platform, Renderer, SendScheduler
from .core.registry import find_product
from .core.results import Result
from .services.cloud_theme import CloudThemeService
from .services.device_sender import DeviceSender
from .services.display import DisplayService
from .services.first_run import FirstRunService
from .services.led_animation_loop import LedAnimationLoop
from .services.led_effects import LEDEffectEngine
from .services.media import MediaService
from .services.metrics_loop import MetricsLoop
from .services.migration import LibraryMigration
from .services.overlay import OverlayService
from .services.quickstart import QuickstartService
from .services.settings import Settings
from .services.slideshow import SlideshowService
from .services.theme import ThemeService

log = logging.getLogger(__name__)


R = TypeVar("R", bound=Result)


# =========================================================================
# App
# =========================================================================


class App:
    """Application hub.

    * `platform` — the OS (Linux/Windows/macOS/BSD), DI'd at construction.
    * `devices`  — key → Device, populated when ConnectDevice runs.
    * `events`   — EventBus for async updates to UIs.

    UIs never hold Device references directly.  They dispatch Commands
    and subscribe to events.
    """

    def __init__(self, platform: Platform,
                 renderer: Renderer | None = None,
                 send_scheduler: SendScheduler | None = None) -> None:
        self.platform = platform
        self.devices: dict[str, Device] = {}
        self.events = EventBus()
        self.settings = Settings(platform.paths())
        self.themes = ThemeService(platform.paths())
        self.media = MediaService()
        # Currently-loaded Theme per device — set by LoadTheme, read by
        # RenderAndSend ticker, cleared on DisconnectDevice.
        self.active_themes: dict[str, Theme] = {}
        # Per-device LED runtime counters — populated lazily by RenderLed,
        # cleared by DisconnectDevice.  Not persisted — these are tick
        # phase counters, not user prefs.
        self.led_runtime: dict[str, LedRuntimeState] = {}
        self.led_effects = LEDEffectEngine()
        # Latest RAW sensor sample, cached from the MetricsLoop broadcast
        # (once per refresh_interval_s).  Per-tick consumers — RenderLed driven
        # by the 150 ms LedAnimationLoop — read THIS instead of re-polling the
        # sensors ~7×/s.  A per-tick re-poll resampled instantaneous readings,
        # so the displayed metric flickered between ticks (the "sporadic
        # metrics" bug).  Kept raw (un-personalized) so consumers apply their
        # own temp-unit conversion exactly as a fresh read did.  ``None`` until
        # the first broadcast — consumers fall back to a one-off read.
        self.last_raw_readings: dict[str, float] | None = None
        self.last_raw_snapshot: HardwareMetrics | None = None
        # Cloud theme catalog + service.  HTTP adapter is the only seam
        # that talks to the network; tests inject a fake fetcher.
        self.http = UrllibHttpFetcher()
        self.cloud_themes = CloudThemeService(
            catalog=CzhordeCatalog(
                http=self.http,
                # Catalog cache layout is ``cache_dir/<resolution>/<id>.mp4``
                # — identical to ``paths.cloud_theme_dir(w, h)``.  Point
                # the catalog at ``data/web`` directly so downloaded
                # mp4s land where the GUI grid + ``CloudThemeService``
                # already look (no duplicate copy under cloud_themes/).
                cache_dir=platform.paths().data_dir() / "web",
            ),
            paths=platform.paths(),
        )
        # GitHub releases adapter for `check_for_update` Command.
        self.github_releases = GitHubReleases(http=self.http)
        # Per-resolution data installer (themes + cloud previews +
        # masks).  Dispatched from DiscoverDevices when an attached
        # device's resolution hasn't been seen before.
        from .adapters.repo.data_install import DataInstaller
        from .services.data_install import DataInstallService
        self.data_install = DataInstallService(
            paths=platform.paths(),
            installer=DataInstaller(http=self.http),
        )
        # Per-device slideshow cursor — tick-driven, no background thread.
        self.slideshow = SlideshowService()
        # Per-device send workers (actors) — one owns each device's wire,
        # serializing every write + keepalive-resending volatile (Bulk/LY)
        # firmware (absorbing the former KeepaliveService cache).  Created on
        # connect, dropped on disconnect.  The
        # scheduler (execution) is injected so tests drive it deterministically
        # (``SyncSendScheduler``); production defaults to a thread per device.
        # See ``doc/SEND_FOUNDATION.md``.
        self.senders: dict[str, DeviceSender] = {}
        if send_scheduler is None:
            from .adapters.infra.send_scheduler import ThreadSendScheduler
            send_scheduler = ThreadSendScheduler()
        self._send_scheduler: SendScheduler = send_scheduler
        # One-time library migration: early cutover builds saved user
        # content directly under user_content_dir (`theme{w}{h}`, `web`);
        # the current layout roots it under `data/` (mirroring the shipped
        # `.trcc/data/` tree).  Move any old-location dirs in, merge-style
        # and never clobbering.  `run()` is idempotent + swallows its own
        # I/O errors, so it can't block startup.
        LibraryMigration(platform.paths()).run()
        # First-run flag — lightweight marker-file check.
        self.first_run = FirstRunService(platform.paths())
        # Quickstart — guided first-session orchestrator.  Sequences
        # doctor + scan with explicit step boundaries so any UI renders
        # the same flow.
        self.quickstart = QuickstartService(platform)
        # Periodic sensor broadcaster — owns the cadence that publishes
        # ``SensorsUpdated`` so the GUI's system_info / activity_sidebar
        # widgets and the DeviceRenderObserver (overlay refresh) all
        # tick from one source.  Not auto-started — caller (GUI, daemon)
        # calls ``app.metrics_loop.start()`` so headless CLI one-shots
        # don't pay for a polling thread.
        self.metrics_loop = MetricsLoop(self)
        # Fast (~150 ms) LED effect/carousel animation tick.  Separate from the
        # slow sensor broadcast — breathing/colour-cycle/rainbow/carousel need
        # the C#-cadence to actually animate.  Same opt-in start as metrics_loop.
        self.led_animation_loop = LedAnimationLoop(self)
        self._renderer = renderer
        # DisplayService is lazy: needs a Renderer.  None until one is set.
        self._display: DisplayService | None = None
        if renderer is not None:
            self._wire_display(renderer)
        # Device-render observer — single subscriber that maps any
        # visual-mutation event onto a fresh RenderAndSend, so the
        # device (and the preview widget downstream of FrameSent) both
        # see the new composite without ad-hoc dispatch in each
        # Command.  See _DeviceRenderObserver below.
        self._render_observer = _DeviceRenderObserver(self)
        # Persist edited metric placement back to an active USER mask's
        # config1.dc, so a user-uploaded mask stays an editable
        # {01.png, config1.dc} unit (cloud masks are read-only — the helper
        # no-ops for them).  See _helpers.persist_user_mask_dc.
        self.events.subscribe(OverlayChanged, self._persist_user_mask_dc)
        # Hotplug bridge: connect a device the moment it appears.  The monitor
        # only PUBLISHES DeviceAttached — without this subscriber the splash
        # discover is the only connect path, so a device plugged in after
        # launch (or present-but-not-ready during the boot discover) never
        # connects (#139).  Core-level so CLI / API / GUI / daemon all benefit.
        self.events.subscribe(DeviceAttached, self._on_device_attached)
        # Rotation reload: when a device's orientation flips, reload its
        # active theme + mask from the orientation-keyed resolution dir
        # (theme1280480 ↔ theme4801280, web/zt1280480 ↔ web/zt4801280).
        # Ports the C# rotation handler (UpDateUCComboBox1: SetThemeInfo_ThemeML
        # → ReadFileTheme → Theme_Click reloads the theme from the rotated dir).
        # The cutover dropped this — rotation only switched the GUI browser, so
        # an active theme/mask stayed pinned to the landscape dir.  Core-level so
        # CLI / API / GUI / daemon all benefit.
        self.events.subscribe(OrientationChanged, self._on_orientation_changed)
        # Hotplug listener — caller (daemon, GUI launcher, tests) decides
        # whether to ``start_hotplug``.  In-process CLI scripts that
        # only do one Command don't need it; the daemon and GUI do.
        self._hotplug_started = False
        # Seed the persisted GPU choice into the (singleton) enumerator so a
        # restart / CLI / API / daemon honours it, not just an in-session GUI
        # click — the composition root applies persisted state to the port.
        # Guarded so the common no-preference case never force-builds sensors.
        if self.settings.app.active_gpu:
            self.platform.sensors().set_preferred_gpu(self.settings.app.active_gpu)

    def _persist_user_mask_dc(self, event: Any) -> None:
        """On any overlay-metric edit (``OverlayChanged``), rewrite an active
        user mask's ``config1.dc`` so its metric placement is editable +
        durable.  ``event`` is ``Any`` to satisfy the ``Handler`` type, as
        ``_on_visual_change`` does."""
        from .core.commands._helpers import persist_user_mask_dc
        persist_user_mask_dc(self, event.key)

    def _on_device_attached(self, event: Any) -> None:
        """Hotplug ``DeviceAttached`` → connect the device.

        Runs on the hotplug poll thread (same off-main-thread pattern the
        splash ``BootstrapWorker`` already uses for ``ConnectDevice``).  Guarded
        idempotent so coldplug replays + duplicate adds are no-ops.  On success
        ``ConnectDevice`` emits ``DeviceConnected``, which UIs already observe.
        """
        if event.key in self.devices:
            log.debug("_on_device_attached: %s already connected", event.key)
            return
        log.info("_on_device_attached: connecting %s", event.key)
        from .core.commands import ConnectDevice
        self.dispatch(ConnectDevice(key=event.key))

    def _on_orientation_changed(self, event: Any) -> None:
        """``OrientationChanged`` → reload the active theme + mask from the
        orientation-keyed resolution dir.

        Non-square panels store theme / web-mask catalogs per oriented
        resolution (``theme1280480`` vs ``theme4801280``, ``web/zt1280480`` vs
        ``web/zt4801280``).  On rotation the active content must follow — the
        port of the C# ``UpDateUCComboBox1`` reload.  Theme first, then the
        user mask (so an explicitly-applied mask wins over the theme's bundled
        one).  Each reload is best-effort + skipped when no rotated variant is
        on disk (the renderer pixel-rotates the landscape art as fallback,
        matching C# ``isFanZhuan``).  ``event`` is ``Any`` to satisfy the
        ``Handler`` type, as the other subscribers do.
        """
        key = event.key
        device = self.devices.get(key)
        if device is None or device.profile is None:
            log.debug("_on_orientation_changed: %s not connected — skip", key)
            return
        bw, bh = oriented_resolution(device.profile.resolution, event.degrees)
        paths = self.platform.paths()
        s = self.settings.for_device(key)
        log.info("_on_orientation_changed: %s degrees=%d catalog=%dx%d",
                 key, event.degrees, bw, bh)

        from .core.commands import ApplyMask, LoadCloudTheme, LoadTheme
        from .core.commands._helpers import oriented_theme_path

        web_root = paths.data_dir() / "web"

        # Active theme → reload from the rotated-resolution theme dir.  Shared
        # resolver with RestoreLastTheme so connect-restore + runtime rotation
        # agree on the oriented variant (#136).
        if s.current_theme:
            cur = Path(s.current_theme)
            cand = oriented_theme_path(self, key, cur, degrees=event.degrees)
            if cand != cur:
                log.info("_on_orientation_changed: reload theme %s → %s",
                         cur.name, cand)
                self.dispatch(LoadTheme(key=key, path=cand))

        # Active cloud background → re-apply from the rotated-resolution web dir.
        # Stored as ``web/{res}/<id>``; ``LoadCloudTheme`` now materialises per
        # ORIENTED resolution, so re-dispatching it re-fetches ``web/{bw}{bh}/<id>``
        # and re-applies — the port of the C# ``buttonSelectBackgroundImage`` →
        # ``ucThemeWeb1.CheakDirectionB`` on every rotation.  After the theme
        # reload (which may reset the override) and before the mask (so the mask
        # still composites on top).  User-uploaded backgrounds (under
        # ``user_content_dir``) are native-res and stay.
        bgp = s.background_path
        if bgp:
            bg_path = Path(bgp)
            if (bg_path.parent.parent == web_root
                    and bg_path.parent.name.isdigit()
                    and bg_path.parent.name != f"{bw}{bh}"):
                log.info("_on_orientation_changed: reload cloud background "
                         "%s → web/%dx%d", bg_path.name, bw, bh)
                self.dispatch(LoadCloudTheme(key=key, theme_id=bg_path.stem))

        # Active user mask → re-resolve to the rotated zt dir (after the theme,
        # so it overrides the theme's bundled mask).  Only cloud zt masks have
        # per-orientation variants; user-uploaded masks are native-res and stay.
        mp = s.mask_path
        if mp and s.mask_visible:
            mask_path = Path(mp)
            zt_parent = mask_path.parent.parent  # .../web/zt{w}{h}
            if (zt_parent.parent == web_root
                    and zt_parent.name.startswith("zt")):
                cand = paths.cloud_mask_dir(bw, bh) / mask_path.parent.name
                if cand.exists() and cand != mask_path.parent:
                    log.info("_on_orientation_changed: reload mask %s → %s",
                             mask_path.parent.name, cand)
                    self.dispatch(ApplyMask(key=key, path=cand))

    def set_renderer(self, renderer: Renderer) -> None:
        """Attach a Renderer (headless modes can defer until needed)."""
        log.info("set_renderer: renderer=%s", type(renderer).__name__)
        self._renderer = renderer
        self._wire_display(renderer)

    def _wire_display(self, renderer: Renderer) -> None:
        log.debug("_wire_display: renderer=%s", type(renderer).__name__)
        self._display = DisplayService(
            renderer=renderer,
            themes=self.themes,
            overlay=OverlayService(renderer),
            settings=self.settings,
            media=self.media,
        )

    @property
    def display(self) -> DisplayService:
        """DisplayService for rendering.  Raises if no Renderer attached."""
        if self._display is None:
            raise RuntimeError(
                "DisplayService unavailable — call App.set_renderer(...) first"
            )
        return self._display

    @property
    def renderer(self) -> Renderer:
        """The injected ``Renderer`` port — for Commands that don't need DisplayService.

        Stand-alone renderers (DC preview, overlay one-shots) talk to
        the renderer directly without going through DisplayService's
        scene cache.  Raises if no renderer is attached.
        """
        if self._renderer is None:
            raise RuntimeError(
                "Renderer unavailable — call App.set_renderer(...) first"
            )
        return self._renderer

    # ── Device lifecycle ──────────────────────────────────────────────

    def attach(self, vid: int, pid: int) -> Device:
        """Build and cache a Device for (vid, pid).  Does not connect.

        Resolves the right transport for the device's wire via the
        Platform, then DI's it into the Device constructor.  Device
        classes never touch Platform — they only know their transport.
        """
        log.info("attach: %04x:%04x", vid, pid)
        info = find_product(vid, pid)
        if info is None:
            raise DeviceNotFoundError(
                f"Unknown product: {vid:04x}:{pid:04x}"
            )
        cls = DeviceFactory.for_wire(info.wire)
        # SCSI needs a kernel-native passthrough transport; everything
        # else speaks plain USB bulk.  Platform picks the right impl.
        if info.wire is Wire.SCSI:
            transport = self.platform.open_scsi(vid, pid)
        else:
            transport = self.platform.open_bulk(vid, pid)
        device = cls(info, transport)
        self.devices[device.key] = device
        log.debug("App.attach: %s → %s", device.key, cls.__name__)
        return device

    def get(self, key: str) -> Device:
        """Look up an attached device.  Raises if not attached."""
        log.debug("get: key=%s", key)
        device = self.devices.get(key)
        if device is None:
            raise DeviceNotFoundError(f"Not attached: {key}")
        return device

    def detach(self, key: str) -> None:
        """Disconnect and drop a device.  Frees the scene cache + active theme."""
        log.info("detach: key=%s", key)
        # Stop the send worker BEFORE closing the transport so no in-flight
        # write races the disconnect (the scheduler joins the thread).
        self.stop_sender(key)
        device = self.devices.pop(key, None)
        if device is not None:
            device.disconnect()
        self.active_themes.pop(key, None)
        self.led_runtime.pop(key, None)
        self.media.unload(key)
        self.slideshow.reset(key)
        if self._display is not None:
            self._display.invalidate(key)

    def close(self) -> None:
        """Disconnect every attached device + stop background threads.

        The metrics_loop, hotplug listener, and any open device transports
        all hold OS resources (threads, file descriptors, USB handles).
        Stopping them explicitly lets the process exit cleanly when the
        UI quits — otherwise a polling thread can keep the interpreter
        alive past ``QApplication.quit()``.
        """
        log.info("close: devices=%d", len(self.devices))
        self.metrics_loop.stop()
        self.led_animation_loop.stop()
        self.stop_hotplug()
        for key in list(self.devices):
            self.detach(key)
        self._send_scheduler.shutdown()

    # ── Send workers ──────────────────────────────────────────────────

    def start_sender(self, key: str) -> None:
        """Create + start the per-device send worker (idempotent).

        Called after a successful ``ConnectDevice`` handshake.  The worker
        owns the device's wire from here until ``stop_sender``.
        """
        device = self.devices.get(key)
        if device is None:
            log.warning("start_sender: %s not attached", key)
            return
        if key in self.senders:
            return
        sender = DeviceSender(
            device, volatile=device.needs_keepalive,
            on_failure=self._on_sender_failure,
        )
        self.senders[key] = sender
        self._send_scheduler.add(sender)
        log.info("start_sender: %s volatile=%s", key, device.needs_keepalive)

    def _on_sender_failure(self, key: str, exc: BaseException | None) -> None:
        """A worker's fire-and-forget write failed — publish the same events a
        Command's ``except TransportError`` would (the producer that submitted
        it isn't waiting, so the sender routes the failure here)."""
        if exc is None:
            log.warning("_on_sender_failure: %s write returned False", key)
            return
        self.events.publish(ErrorOccurred(
            message=str(exc), kind="transport", key=key,
        ))
        if isinstance(exc, DeviceDisconnectedError):
            log.info("_on_sender_failure: %s auto-disconnect (recovery threshold)",
                     key)
            self.events.publish(DeviceDisconnected(key=key))

    def stop_sender(self, key: str) -> None:
        """Stop + drop the send worker for *key* (idempotent)."""
        sender = self.senders.pop(key, None)
        if sender is None:
            return
        log.info("stop_sender: %s", key)
        self._send_scheduler.remove(key)

    def send(self, key: str, payload: Any, *, wait: bool = True) -> bool:
        """Submit a frame to a device's send worker.

        The single funnel for every wire write.  ``wait=True`` (default)
        blocks for the device's success bool — one-shot CLI/API sends;
        ``wait=False`` is fire-and-forget for the per-frame hot path.
        """
        sender = self.senders.get(key)
        if sender is None:
            log.warning("send: no sender for %s (not connected?)", key)
            return False
        return sender.submit(payload, wait=wait)

    def exclusive_wire(self, key: str) -> AbstractContextManager[None]:
        """Context manager holding a device's wire exclusively for a
        multi-frame upload (boot animation), so the worker's frame/keepalive
        writes can't interleave.  ``nullcontext`` when there's no sender."""
        sender = self.senders.get(key)
        if sender is None:
            log.debug("exclusive_wire: no sender for %s — nullcontext", key)
            return nullcontext()
        return sender.exclusive()

    # ── Hotplug ───────────────────────────────────────────────────────

    def start_hotplug(self) -> None:
        """Start the OS hotplug listener (idempotent).

        Translates udev / kernel events into ``DeviceAttached`` /
        ``DeviceDetached`` events on ``self.events``.  Long-lived
        processes (daemon, GUI) should call this once on startup;
        one-shot CLI scripts can skip it.
        """
        log.info("start_hotplug: started=%s", self._hotplug_started)
        if self._hotplug_started:
            return
        self.platform.hotplug().start(self.events)
        self._hotplug_started = True

    def stop_hotplug(self) -> None:
        """Stop the hotplug listener (idempotent)."""
        log.info("stop_hotplug: started=%s", self._hotplug_started)
        if not self._hotplug_started:
            return
        self.platform.hotplug().stop()
        self._hotplug_started = False

    # ── Command dispatch ──────────────────────────────────────────────

    def dispatch(self, cmd: Command[R]) -> R:
        """Execute a Command and return its Result.

        Generic in the Result subclass so the caller sees the concrete
        Result type (e.g. DiscoverResult with .products) without casting.
        UIs should only reach the rest of the app through this method.

        Logging is single-chokepoint: every dispatch emits one log line
        at ``cmd.LOG_LEVEL`` on entry and one on outcome.  Failures
        (``result.ok`` is False) escalate to WARNING regardless of the
        command's default level so they're always visible.  Per-tick
        commands set ``LOG_LEVEL = DEBUG`` so they only show under -vv.
        """
        log.log(cmd.LOG_LEVEL, "dispatch %r", cmd)
        result = cmd.execute(self)
        if not getattr(result, "ok", True):
            log.warning(
                "%s failed: %s",
                type(cmd).__name__,
                getattr(result, "message", "(no message)"),
            )
        else:
            log.log(
                cmd.LOG_LEVEL,
                "%s ok: %s",
                type(cmd).__name__,
                getattr(result, "message", ""),
            )
        return result


# =========================================================================
# _DeviceRenderObserver — single subscriber that bridges
# visual-mutation events to a fresh composite + wire push.
#
# Architecture:
#   Settings change (brightness / mask / overlay / fit / split …)
#     → Command persists + invalidates scene + publishes event
#     → THIS observer subscribes to the event
#     → dispatches RenderAndSend
#         → DisplayService.build_frame → device.send → FrameSent published
#     → preview widget (separate observer via bus_bridge) consumes FrameSent
#
# Both the device (wire) and the preview (widget) are downstream
# observers of the same composited frame.  This class is the bridge
# that turns "settings changed" into "frame composited" — without it
# the composite never rebuilds for static themes and the click is
# silent.
# =========================================================================


class _DeviceRenderObserver:
    """Listen for visual-mutation events; trigger one re-render each."""

    def __init__(self, app: App) -> None:
        self._app = app
        # Lazy import — RenderAndSend lives in core.commands, which
        # already imports from app.py via TYPE_CHECKING.
        from .core.commands import RenderAndSend, RenderLed
        self._RenderAndSend = RenderAndSend
        self._RenderLed = RenderLed
        for event_cls in (
            BrightnessChanged, MaskApplied, MaskPositionChanged,
            MaskVisibilityChanged, OverlayChanged, FitModeChanged,
            SplitModeChanged, SensorsUpdated,
            # Video background events trigger a render so a fresh
            # PlayVideo / StopVideo immediately shows the new bg on
            # the preview + device, without waiting for the next
            # sensor tick.
            VideoStarted, VideoStopped,
            # Image background override (SetBackground Command) — same
            # treatment: one render pushes the new bg to the wire.
            BackgroundChanged,
            # ``SetTempUnit`` mutates every LCD's overlay output (°C ↔
            # °F).  Subscribing here means each connected device picks
            # up the change on the very next render — UIs don't have
            # to loop over handlers and force a re-render themselves.
            TempUnitChanged,
            # Per-device clock + date format changes — DisplayService
            # reads DeviceSettings.{time_format,date_format} in
            # compute_clock; the Command's invalidate already drops
            # the scene cache, this just kicks the re-render so the
            # user sees the change without waiting for the next
            # sensor tick.
            TimeFormatChanged,
            DateFormatChanged,
            # Any LED settings mutation (mode, colour, selected metric
            # page/zone, carousel) — re-render the device + preview right
            # away instead of waiting for the next sensor tick.  RenderLed
            # publishes LedColorsChanged, NOT LedSettingsChanged, so this
            # can't feed back into itself.
            LedSettingsChanged,
        ):
            app.events.subscribe(event_cls, self._on_visual_change)

    def _on_visual_change(self, event: Any) -> None:
        """Any visual-mutation event → one RenderAndSend per affected key.

        Per-device events (BrightnessChanged, MaskApplied, etc.) carry
        a ``key`` field and re-render only that device.  Device-wide
        events (SensorsUpdated) re-render every connected device with
        an active theme — the new sensor reading affects every overlay.
        """
        if self._app._renderer is None:  # pyright: ignore[reportPrivateUsage]
            return
        keys: list[str]
        evt_key = getattr(event, "key", "")
        if evt_key:
            keys = [evt_key]
        else:
            # Device-wide event (SensorsUpdated): re-render every connected
            # device — LCDs with a theme, AND LED devices (their segment
            # display IS the live metric, no theme involved).  LED was
            # dropped from this list at the cutover, which is why LED panels
            # stopped updating on sensor ticks.
            keys = [
                k for k, d in self._app.devices.items()
                if d.is_connected
                and (d.is_led or k in self._app.active_themes)
            ]
        for key in keys:
            device = self._app.devices.get(key)
            if device is None or not device.is_connected:
                continue
            # LED: segments are computed straight from live sensors — no
            # theme.  RenderLed sends to the device AND drives the preview.
            if device.is_led:
                log.debug(
                    "DeviceRenderObserver: %s for %s → RenderLed",
                    type(event).__name__, key,
                )
                self._app.dispatch(self._RenderLed(key=key))
                continue
            theme = self._app.active_themes.get(key)
            if theme is None:
                log.debug(
                    "DeviceRenderObserver: skip %s for %s (no theme)",
                    type(event).__name__, key,
                )
                continue
            log.debug(
                "DeviceRenderObserver: %s for %s → RenderAndSend",
                type(event).__name__, key,
            )
            self._app.dispatch(self._RenderAndSend(key=key))
