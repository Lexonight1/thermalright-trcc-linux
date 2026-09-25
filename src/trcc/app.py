"""App — the hub that wires Platform + Devices + EventBus + Commands.

Holds one Platform (the OS), one dict of live Devices keyed by their
'vid:pid' string, and one EventBus.  UIs dispatch Commands through
`app.dispatch(cmd)`; nothing else touches devices directly.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, TypeVar

from .adapters.device import DEVICES
from .adapters.repo.github_releases import GitHubReleases
from .adapters.repo.http import UrllibHttpFetcher
from .adapters.theme.cloud import CzhordeCatalog
from .adapters.theme.filesystem import FileContentStore
from .core.commands import Command
from .core.errors import DeviceDisconnectedError, DeviceNotFoundError
from .core.events import (
    BackgroundChanged,
    BrightnessChanged,
    DateFormatChanged,
    DeviceAttached,
    DeviceDetached,
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
    SystemResumed,
    TempUnitChanged,
    TimeFormatChanged,
    VideoStarted,
    VideoStopped,
)
from .core.led_models import LedRuntimeState
from .core.libraries import DeviceLibraries
from .core.logs import per_frame
from .core.models import (
    DeviceInfo,
    DeviceQuirks,
    HardwareMetrics,
    Theme,
    format_device_key,
    oriented_resolution,
    quirks_for,
)
from .core.ports import (
    DataInstallRunner,
    Device,
    Diagnostics,
    Platform,
    Renderer,
    SendScheduler,
    SendTask,
    VideoExportRunner,
)
from .core.protocol import background_variant, mask_variant, theme_variant
from .core.registry import find_product
from .core.results import ConnectResult, Result
from .services.audio import AudioCapture
from .services.background import BackgroundSlot
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

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


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
                 send_scheduler: SendScheduler | None = None,
                 data_install_runner: DataInstallRunner | None = None,
                 video_export_runner: VideoExportRunner | None = None,
                 ) -> None:
        log.debug("__init__: platform=%s renderer=%s", platform, renderer)
        self.platform = platform
        self.devices: dict[str, Device] = {}
        # Last scan's live DeviceInfo per key — carries the firmware fingerprint
        # (bcdDevice) that ``attach`` needs to resolve per-device quirks but the
        # static ProductInfo can't know.  Populated by DiscoverDevices.  (#228)
        self._scanned: dict[str, DeviceInfo] = {}
        # Devices that were discovered but failed to connect, keyed by VID:PID.
        # The model's queryable record of "what didn't come up, and why" — read
        # back via the DeviceConnectionIssues command (bus-pure, survives the
        # GUI not existing yet when the failure happened).
        self._connect_issues: dict[str, ConnectResult] = {}
        self.events = EventBus()
        self.settings = Settings(platform.paths())
        self.themes = FileContentStore(platform.paths())
        self.media = MediaService()
        # The current background per device.  Here rather than on
        # DisplayService because ``_wire_display`` rebuilds that service
        # whenever a renderer is attached, and a background that vanished on
        # renderer attach would be a blank panel with no error.
        self.backgrounds = BackgroundSlot()
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
        from .adapters.repo.data_install import HttpDataInstaller
        from .services.data_install import DataInstallService
        self.data_install = DataInstallService(
            paths=platform.paths(),
            installer=HttpDataInstaller(http=self.http),
        )
        # ...and the worker that runs it OFF the caller's thread.  Six
        # archives (~30 MB for a non-square panel) used to download inline
        # from ConnectDevice, which put them on the GUI's startup path — the
        # splash blocked until the last byte landed.  Submitting instead lets
        # connect return immediately; ``DataInstalled`` tells every UI when
        # the grids are worth re-listing.  Injected so tests stay synchronous.
        # (#275)
        if data_install_runner is None:
            from .adapters.infra.data_install_runner import (
                ThreadDataInstallRunner,
            )
            data_install_runner = ThreadDataInstallRunner(
                self.data_install, self.events,
            )
        self.data_install_runner: DataInstallRunner = data_install_runner
        # ...and the same shape for a ``.zt`` encode, for a sharper reason:
        # ffmpeg over a clip runs for minutes and the IPC dispatch timeout is
        # 30 s, so ``ExportVideoClip`` CANNOT encode inline and survive daemon
        # mode.  It submits here and the work reports itself on the bus, which
        # is also how the CLI and the API get a progress bar the two Qt skins
        # used to keep to themselves in a private QThread each.
        if video_export_runner is None:
            from .adapters.infra.video_export_runner import (
                ThreadVideoExportRunner,
            )
            video_export_runner = ThreadVideoExportRunner(self.events)
        self.video_export_runner: VideoExportRunner = video_export_runner
        # Per-device slideshow cursor — tick-driven, no background thread.
        self.slideshow = SlideshowService()
        # ONE microphone for the whole box, so one capture serves however many
        # panels are casting with audio.  Owned here rather than by the window
        # because the spectrum is drawn into the WIRE frame now: it belonged to
        # ``ui/gui``'s tick, which is why the CLI, the API and qtgui could set
        # ``audio=True``, persist it in ``screencast_region``, and never see a
        # bar.  Started by ``StartScreencast``, stopped by ``StopScreencast``
        # once no device still wants it.
        self.audio = AudioCapture()
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
        # Sensor-dashboard layout (the legacy UCSystemInfoOptions grid).
        # Owned here, like ``settings``, because it is user state a Command
        # must be able to read: every UI reaches it through
        # ``GetSensorDashboard`` / ``SetSensorDashboard``, and before this the
        # GUI imported the adapter directly so cli / api / qtgui could not see
        # the file at all.  The path comes off the ``Paths`` port rather than
        # the adapter's own ``Path.home() / ".trcc"`` default — identical on
        # Linux and BSD, and the correct location on Windows and macOS, which
        # do not put config under the home directory.
        from .adapters.infra.sysinfo_config import SysInfoConfig
        self.sysinfo = SysInfoConfig(
            platform.paths().config_dir() / "system_config.json",
        )
        # Diagnostics port — health / doctor / debug-report / package-manager /
        # gpu-reader, bound to this platform.  Core Commands + quickstart reach
        # diagnostics through this injected port (never importing the adapter).
        from .adapters.diagnostics.adapter import DiagnosticsAdapter
        self.diagnostics: Diagnostics = DiagnosticsAdapter(platform)
        # Quickstart — guided first-session orchestrator.  Sequences
        # doctor + scan with explicit step boundaries so any UI renders
        # the same flow.
        self.quickstart = QuickstartService(platform, self.diagnostics)
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
        # ...and release it the moment it goes away.  Every hotplug monitor
        # PUBLISHED DeviceDetached but nothing subscribed, so an unplugged
        # device kept a dead transport in self.devices — the sender spun
        # forever and the replug could never reconnect (#254, #246).
        self.events.subscribe(DeviceDetached, self._on_device_detached)
        # Rotation reload: when a device's orientation flips, reload its
        # active theme + mask from the orientation-keyed resolution dir
        # (theme1280480 ↔ theme4801280, web/zt1280480 ↔ web/zt4801280).
        # Ports the C# rotation handler (UpDateUCComboBox1: SetThemeInfo_ThemeML
        # → ReadFileTheme → Theme_Click reloads the theme from the rotated dir).
        # The cutover dropped this — rotation only switched the GUI browser, so
        # an active theme/mask stayed pinned to the landscape dir.  Core-level so
        # CLI / API / GUI / daemon all benefit.
        self.events.subscribe(OrientationChanged, self._on_orientation_changed)
        # Resume-from-suspend: the USB transport a device opened before the
        # machine slept is stale on wake — writes silently no-op (or the
        # consecutive-failure recovery never trips), so the panel stays blank
        # until a manual restart (#189).  Reconnect every attached device on
        # ``SystemResumed`` (published by the logind PrepareForSleep listener);
        # ConnectDevice reopens the transport and emits DeviceConnected, after
        # which the metrics / LED-animation loops and render observers resume on
        # their own.  Core-level so the daemon, GUI and CLI all recover.
        self.events.subscribe(SystemResumed, self._on_system_resumed)
        # Hotplug listener — caller (daemon, GUI launcher, tests) decides
        # whether to ``start_hotplug``.  In-process CLI scripts that
        # only do one Command don't need it; the daemon and GUI do.
        self._hotplug_started = False
        # Whether the one-shot coldplug has run this process.  Tracks that it
        # RAN, not that it succeeded: ``start_session`` must not repeat a
        # discover the gui's splash worker already did, or a device that
        # failed to connect is retried and recorded twice.
        self._coldplug_done = False
        # Seed the persisted GPU choice into the (singleton) enumerator so a
        # restart / CLI / API / daemon honours it, not just an in-session GUI
        # click — the composition root applies persisted state to the port.
        # Guarded so the common no-preference case never force-builds sensors.
        if self.settings.app.active_gpu:
            self.platform.sensors().set_preferred_gpu(self.settings.app.active_gpu)
        if self.settings.app.active_disk:
            self.platform.sensors().set_preferred_disk(self.settings.app.active_disk)

    def _persist_user_mask_dc(self, event: Any) -> None:
        """On any overlay-metric edit (``OverlayChanged``), rewrite an active
        user mask's ``config1.dc`` so its metric placement is editable +
        durable.  ``event`` is ``Any`` to satisfy the ``Handler`` type, as
        ``_on_visual_change`` does."""
        log.debug("_persist_user_mask_dc: event=%s", event)
        from .core.commands._helpers import persist_user_mask_dc
        persist_user_mask_dc(self, event.key)

    def _release_stale_device(self, key: str) -> None:
        """Stop the sender and close a dead transport, keeping the content.

        The teardown half of a replug.  Deliberately NOT :meth:`detach` —
        that also drops ``active_themes`` / ``led_runtime`` / media /
        slideshow, so the panel would come back BLANK.  Same choice
        :meth:`_on_system_resumed` makes: release only what the vanished USB
        node invalidated, so the next render repaints the same content.

        Sender first, then close, matching :meth:`detach` — otherwise an
        in-flight write races the disconnect.
        """
        self.stop_sender(key)
        device = self.devices.pop(key, None)
        if device is None:
            return
        try:
            device.disconnect()
        except Exception:
            log.exception("_release_stale_device: disconnect %s raised", key)

    def _on_device_detached(self, event: Any) -> None:
        """Hotplug ``DeviceDetached`` → release the handle the unplug killed.

        Without this the device stayed in ``self.devices`` holding a dead
        transport, so (a) its ``DeviceSender`` spun forever logging a write
        failure every ~150 ms, and (b) the replug's ``DeviceAttached`` hit the
        "already connected" guard below and never reconnected — the panel only
        recovered by restarting the process (#254, #246).
        """
        if event.key not in self.devices:
            log.debug("_on_device_detached: %s not attached", event.key)
            return
        log.info("_on_device_detached: %s unplugged — stopping sender + "
                 "releasing stale transport", event.key)
        self._release_stale_device(event.key)

    def _on_device_attached(self, event: Any) -> None:
        """Hotplug ``DeviceAttached`` → connect the device.

        Runs on the hotplug poll thread (same off-main-thread pattern the
        splash ``BootstrapWorker`` already uses for ``ConnectDevice``).  A
        genuinely-connected device is a no-op, so coldplug replays and
        duplicate adds stay idempotent.  On success ``ConnectDevice`` emits
        ``DeviceConnected``, which UIs already observe.
        """
        existing = self.devices.get(event.key)
        if existing is not None and existing.is_connected:
            log.debug("_on_device_attached: %s already connected", event.key)
            return
        if existing is not None:
            # Present but DEAD: the detach event never arrived (coalesced or
            # dropped udev event, or a monitor that only reports adds).  The
            # entry is a corpse holding a stale transport, so tear it down
            # rather than bail — bailing here was the #254 / #246 bug.
            log.info("_on_device_attached: %s present but not connected — "
                     "releasing stale transport before reconnect", event.key)
            self._release_stale_device(event.key)
        log.info("_on_device_attached: connecting %s", event.key)
        from .core.commands import ConnectDevice
        self.dispatch(ConnectDevice(key=event.key))

    def _on_system_resumed(self, _event: Any) -> None:
        """``SystemResumed`` → reconnect every attached device after wake.

        Runs on the power-listener thread — the same off-main-thread pattern
        ``_on_device_attached`` uses to dispatch ``ConnectDevice``.  The device
        is already in ``self.devices`` with a STALE transport, so we rebuild it
        like a replug: stop the old send worker (it is bound to the dead device),
        release the stale transport, then ``ConnectDevice`` re-attaches a fresh
        device + transport, re-handshakes, starts a new sender and emits
        ``DeviceConnected``.  We deliberately do NOT ``detach`` — that would drop
        the active theme / LED runtime and the panel would come back blank;
        keeping them lets the metrics / LED-animation loops and render observers
        repaint the same content on the next tick.  So both the LED segment
        displays and the LCD panels recover without a manual restart (#189).
        """
        from .core.commands import ConnectDevice
        keys = list(self.devices)
        log.info("_on_system_resumed: reconnecting %d device(s) after wake", len(keys))
        for key in keys:
            old = self.devices.get(key)
            log.info("_on_system_resumed: %s — stale transport after suspend, "
                     "stop sender + reopen", key)
            self.stop_sender(key)
            if old is not None:
                try:
                    old.disconnect()
                except Exception:
                    log.exception("_on_system_resumed: disconnect %s raised", key)
            self.dispatch(ConnectDevice(key=key))

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
        #
        # reset_overrides=False: a rotation re-roots the SAME theme to its
        # oriented variant — it is NOT an explicit theme switch.  The original
        # C# app rotates at the render layer and NEVER drops the user's overlay/
        # background/mask edits on rotate; legacy's startup restore likewise
        # re-applied them.  Resetting here (the old default=True) PERSIST-CLEARED
        # user_overlay_elements + stopped video + reverted the cloud background —
        # so on a connect/restart the user's "last preview with changes" was lost
        # before RestoreLastTheme could replay it.  Preserve them; the dedicated
        # cloud-background + mask reload blocks below still re-resolve those two
        # to the oriented resolution.  (Catalog selection + encode are untouched,
        # so the hardware-verified widescreen behavior in #169 is unaffected.)
        if s.current_theme:
            cur = Path(s.current_theme)
            cand = oriented_theme_path(self, key, cur, degrees=event.degrees)
            if cand != cur:
                log.info("_on_orientation_changed: reload theme %s → %s "
                         "(preserve overrides)", cur.name, cand)
                self.dispatch(LoadTheme(key=key, path=cand,
                                        reset_overrides=False))

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
                # Same SKU library on the way out as on the way in: a
                # 480x480 panel at PM 3 lives in zt480480y, and rotating it
                # into plain zt480480 would silently swap its mask set.
                cand = (self.libraries(key).cloud_mask_dir(bw, bh)
                        / mask_path.parent.name)
                if cand.exists() and cand != mask_path.parent:
                    log.info("_on_orientation_changed: reload mask %s → %s",
                             mask_path.parent.name, cand)
                    self.dispatch(ApplyMask(key=key, path=cand))

    def libraries(self, key: str) -> DeviceLibraries:
        """The artwork libraries the device at *key* reads from.

        THE place the per-SKU suffix is decided.  Every consumer -- theme
        browser, mask rotation, theme-name search, the data installer -- takes
        the answer from here rather than deriving it, so there is one reading
        of the handshake and one fallback rule.

        A device that is absent, not yet handshaken, or of unknown resolution
        gets the generic libraries, which is what the overwhelming majority of
        panels use anyway.
        """
        paths = self.platform.paths()
        device = self.devices.get(key)
        hs = getattr(device, "handshake", None) if device is not None else None
        profile = getattr(device, "profile", None) if device is not None else None
        if hs is None or profile is None:
            log.debug("libraries: %s has no handshake/profile — generic", key)
            return DeviceLibraries(paths)
        resolution = profile.resolution
        libs = DeviceLibraries(
            paths,
            # Themes and backgrounds take the SAME letter today -- every rule
            # the C# has for them agrees.  They are separate fields because the
            # C# keeps separate directory families and 360x360 already
            # disagrees (ThemeML360360m exists, GifDirectoryWeb360360m does
            # not), so the seam has to exist before that rule can be added.
            theme_variant=theme_variant(resolution, hs.sub_byte),
            background_variant=background_variant(resolution, hs.sub_byte),
            mask_variant=mask_variant(resolution, hs.sub_byte, hs.pm_byte),
        )
        log.debug("libraries: %s %dx%d sub=%d pm=%d → theme=%r bg=%r mask=%r",
                  key, *resolution, hs.sub_byte, hs.pm_byte,
                  libs.theme_variant, libs.background_variant,
                  libs.mask_variant)
        return libs

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
            # The host's static "can never read this" set, resolved ONCE —
            # see OverlayService.__init__.
            overlay=OverlayService(
                renderer, unsupported=self.platform.sensors().unsupported(),
            ),
            settings=self.settings,
            media=self.media,
            backgrounds=self.backgrounds,
            paths=self.platform.paths(),
        )

    @property
    def display(self) -> DisplayService:
        """DisplayService for rendering.  Raises if no Renderer attached."""
        if self._display is None:
            log.error("App.display: no Renderer attached — set_renderer() was "
                      "never called, so every render path raises from here")
            raise RuntimeError(
                "DisplayService unavailable — call App.set_renderer(...) first"
            )
        frame_log.debug("App.display: %s", type(self._display).__name__)
        return self._display

    @property
    def renderer(self) -> Renderer:
        """The injected ``Renderer`` port — for Commands that don't need DisplayService.

        Stand-alone renderers (DC preview, overlay one-shots) talk to
        the renderer directly without going through DisplayService's
        scene cache.  Raises if no renderer is attached.
        """
        log.debug("renderer")
        if self._renderer is None:
            raise RuntimeError(
                "Renderer unavailable — call App.set_renderer(...) first"
            )
        return self._renderer

    # ── Device lifecycle ──────────────────────────────────────────────

    def attach(self, vid: int, pid: int, *,
               quirk_transport: bool = False, unit: str = "") -> Device:
        """Build and cache a Device for (vid, pid).  Does not connect.

        Resolves the right transport for the device's wire via the
        Platform, then DI's it into the Device constructor.  Device
        classes never touch Platform — they only know their transport.

        *quirk_transport* opts in to a firmware's overriding transport (the
        HID output-report one, #228).  It is **off by default on purpose**:
        that override is keyed on ``(vid, pid, bcdDevice)``, and that
        fingerprint is NOT unique — Thermalright ships several panels as
        ``0416:5302`` firmware 4.07.  At least one of them (#267) handshakes
        correctly on the ordinary transport, at its true landscape size, while
        the override has never been confirmed to drive any real panel by
        anyone, including the reporter who supplied it.  So the proven path
        goes first and ``ConnectDevice`` retries with the override only if
        that fails — an unproven path can then only ever help.
        """
        log.info("attach: %04x:%04x quirk_transport=%s", vid, pid, quirk_transport)
        info = find_product(vid, pid)
        if info is None:
            raise DeviceNotFoundError(
                f"Unknown product: {vid:04x}:{pid:04x}"
            )
        cls = DEVICES[info.wire]
        quirks = self._quirks_for(vid, pid, unit)
        # Which transport a WIRE needs is the Platform's business — it owns the
        # Wire→opener table, so this no longer branches on Wire.SCSI.  What
        # remains is a FIRMWARE override: one revision accepts only HID output
        # reports (#228), and that is keyed on (vid, pid, bcdDevice), not on
        # wire — so it belongs here with the rest of quirk resolution.
        if quirks.hid_reports and quirk_transport:
            from .adapters.device.transport import HidApiTransport
            log.info("attach: %04x:%04x retrying on HID output reports (quirk)",
                     vid, pid)
            transport = HidApiTransport(vid, pid, unit=unit)
        else:
            if quirks.hid_reports:
                log.debug("attach: %04x:%04x has a HID output-report quirk, "
                          "trying the ordinary transport first", vid, pid)
            transport = self.platform.open_transport(
                info.wire, vid, pid, unit=unit)
        device = cls(info, transport)
        # Hand down the OS-specific EACCES hint (resolved here, where Platform
        # is in scope) so the device's recovery tracker can surface it without
        # the device knowing which OS it's on.
        device.set_permission_hint(self.platform.permission_denied_hint())
        # Where this device may persist its own state — resolved through the
        # Paths port here, so no adapter has to reach for Path.home().
        device.set_state_dir(self.platform.paths().config_dir())
        # Inject firmware-specific overrides resolved from the live fingerprint.
        device.set_quirks(quirks)
        # WHICH of several identical units this is (#287).  Keyword with an
        # empty default, so the 31 existing call sites keep meaning exactly
        # what they meant: "the only one of this model".
        device.set_unit(unit)
        self.devices[device.key] = device
        log.debug("App.attach: %s → %s", device.key, cls.__name__)
        return device

    def remember_scan(self, infos: list[DeviceInfo]) -> None:
        """Cache the live DeviceInfo per key from a scan, so a later ``attach``
        can resolve firmware quirks from the fingerprint (bcdDevice).  (#228)"""
        log.debug("remember_scan: infos=%s", infos)
        for info in infos:
            self._scanned[info.key] = info

    def _quirks_for(self, vid: int, pid: int, unit: str = "") -> DeviceQuirks:
        """Resolve firmware quirks from the device's live fingerprint.

        Quirks are keyed on ``(vid, pid, bcdDevice)``, and bcdDevice comes off
        the USB descriptor — so it is knowable at connect time whether or not
        anything scanned first.  We look in the scan cache, and **enumerate to
        fill it when the key is missing** rather than assuming bcdDevice 0.

        That assumption was a real bug (#267).  ``DiscoverDevices`` is the only
        thing that populates the cache, and no CLI wire command runs it —
        ``trcc display color`` / ``load-theme`` / ``led render`` and
        ``trcc device connect`` all go straight to ``ConnectDevice``.  So a
        quirked panel got its quirks in the GUI (which discovers at splash) and
        none at all from the CLI: the Frozen Warframe SE was sent the very init
        packet its quirk exists to suppress, rebooted mid-connect, and the
        frame went to a device that was no longer listening — "153600 bytes
        sent", blank screen.  A Command must not behave differently because
        some *other* Command happened to run first.

        Best-effort: an enumeration failure logs and falls back to the empty
        default, because a quirk lookup must never be the thing that breaks a
        connect.
        """
        # The scan caches a twin under ``vid:pid@unit`` (#287), so the lookup
        # must build the SAME key or every twin misses and gets bcdDevice 0.
        key = format_device_key(vid, pid, unit)
        if key not in self._scanned:
            log.debug("_quirks_for: %s not scanned — enumerating for its "
                      "fingerprint", key)
            try:
                self.remember_scan(self.platform.scan_devices())
            except Exception as e:
                log.warning("_quirks_for: scan for %s failed (%s) — resolving "
                            "quirks without a fingerprint", key, e)
        info = self._scanned.get(key)
        bcd = info.bcd_device if info is not None else 0
        log.debug("_quirks_for: %s bcdDevice=0x%04x", key, bcd)
        return quirks_for(vid, pid, bcd)

    def get(self, key: str) -> Device:
        """Look up an attached device.  Raises if not attached."""
        # Per-frame: the render path looks the device up on every tick, so this
        # was 16 records/second and 37% of everything a default log still
        # wrote after the frame family was gated.  It was missed because
        # app.py was excluded wholesale to special-case ``dispatch`` below.
        frame_log.debug("get: key=%s", key)
        device = self.devices.get(key)
        if device is None:
            raise DeviceNotFoundError(f"Not attached: {key}")
        return device

    # ── Connect-issue model (queried via DeviceConnectionIssues) ─────────

    def note_connect_issue(self, result: ConnectResult) -> None:
        """Record a failed connect so any UI can query why it didn't come up."""
        log.info("note_connect_issue: key=%s msg=%s", result.key, result.message)
        self._connect_issues[result.key] = result

    def clear_connect_issue(self, key: str) -> None:
        """Drop a device's recorded failure (it connected, or it's gone)."""
        if self._connect_issues.pop(key, None) is not None:
            log.info("clear_connect_issue: key=%s", key)

    def connection_issues(self) -> list[ConnectResult]:
        """Current connect failures — the queryable model state."""
        log.debug("connection_issues")
        return list(self._connect_issues.values())

    def detach(self, key: str) -> None:
        """Disconnect and drop a device.  Frees the scene cache + active theme."""
        log.info("detach: key=%s", key)
        self.clear_connect_issue(key)
        # Stop the send worker BEFORE closing the transport so no in-flight
        # write races the disconnect (the scheduler joins the thread).
        self.stop_sender(key)
        device = self.devices.pop(key, None)
        if device is not None:
            device.disconnect()
        self.active_themes.pop(key, None)
        self.led_runtime.pop(key, None)
        self.media.unload(key)
        self.backgrounds.clear(key)
        self.slideshow.reset(key)
        if self._display is not None:
            self._display.invalidate(key)

    def start_session(
        self, on_progress: Callable[[str], None] | None = None,
    ) -> None:
        """Bring a long-lived session up: coldplug, then the live loops.

        The exact partner of :meth:`close`, and the reason it exists: the
        three loop starts were copy-pasted into ``run_daemon``, ``run_gui``
        and ``run_qtgui`` — qtgui prefixing the coldplug, gui running it on
        its splash worker, the daemon not running it at all — while the API
        started none of them.  #148 is what a duplicated bring-up costs:
        one copy drifted out of step, and a reporter running ``trccd.service``
        watched a connected device stay permanently blank until the missing
        ``metrics_loop.start()`` was added to that copy alone.

        The coldplug is the half the daemon never had at all.  Only Linux's
        monitor replays already-present devices as ``DeviceAttached``;
        Windows and FreeBSD watch for *new* events only, and the macOS poller
        deliberately primes its snapshot so present devices raise nothing.
        Without this, a daemon on those three comes up owning USB with
        nothing connected until the user physically replugs.

        Idempotent, like everything it calls: a repeat skips the coldplug and
        the loops early-return.  ``on_progress`` is forwarded to
        :meth:`discover_and_connect` for splash display.
        """
        log.info("start_session: coldplug_done=%s devices=%d",
                 self._coldplug_done, len(self.devices))
        if self._coldplug_done:
            log.info("start_session: coldplug already ran — skipping it")
        else:
            self.discover_and_connect(on_progress)
        self.start_hotplug()
        self.metrics_loop.start()
        self.led_animation_loop.start()

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
        # Blank every panel before releasing it so it goes dark on shutdown
        # instead of holding its last frame lit (#143).  Best-effort: the
        # Command swallows a mid-unplug send, and any unexpected error here
        # must not block device release below.
        from .core.commands import SleepDevice
        for key in list(self.devices):
            try:
                self.dispatch(SleepDevice(key=key))
            except Exception:
                log.exception("close: SleepDevice raised for %s", key)
        for key in list(self.devices):
            self.detach(key)
        self._send_scheduler.shutdown()
        self.data_install_runner.shutdown()
        self.video_export_runner.shutdown()

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
        existing = self.senders.get(key)
        if existing is not None:
            if existing.device is device:
                return
            # A sender for a device we no longer hold.  An unstable replug
            # produces several `add` events in a row, and this method runs on
            # the hotplug thread: one pass can register a sender just after
            # another has released the device it was built for.  Treating
            # "a sender exists" as "the right sender exists" then wedged the
            # panel permanently — the stale worker logged `send() called
            # before connect()` thousands of times while the freshly
            # reconnected device never got a worker at all, because this
            # returned early.  Only killing the process recovered it (#254).
            log.warning(
                "start_sender: %s has a sender bound to a released device — "
                "replacing it (unstable replug)", key,
            )
            self.stop_sender(key)
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

    def add_task(self, task: SendTask) -> None:
        """Drive *task* on the shared scheduler.

        The scheduler is not device-senders-only: its contract is
        ``key``/``wait``/``wake``/``run_once``, which is a periodic task and
        says nothing about writing. This is the seam for anything else that
        needs a cadence without owning a thread — the screencast driver is the
        first. Such a task MUST use a namespaced key: the scheduler evicts by
        key, so a bare device key would stop that device's own sender.
        """
        log.info("add_task: %s", task.key)
        self._send_scheduler.add(task)

    def remove_task(self, key: str) -> None:
        """Stop driving the task registered under *key* (idempotent)."""
        log.info("remove_task: %s", key)
        self._send_scheduler.remove(key)

    def stop_sender(self, key: str) -> None:
        """Stop + drop the send worker for *key* (idempotent).

        Also drops the device's NAMESPACED drivers — screencast and slideshow —
        which live in the same scheduler.  Removing only the bare key would
        leave a disconnected device being captured or rotated forever: the
        driver would keep dispatching, and every turn would fail on a device
        that is no longer attached.  The namespacing exists because the
        scheduler evicts by key and would otherwise have killed this device's
        own sender; that same namespacing is why each removal has to be
        explicit here, and why a NEW driver must be added to this list.
        """
        from .services.screencast_driver import task_key as screencast_task
        from .services.slideshow_driver import task_key as slideshow_task

        self._send_scheduler.remove(screencast_task(key))
        self._send_scheduler.remove(slideshow_task(key))
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

    # ── Coldplug ──────────────────────────────────────────────────────

    def discover_and_connect(
        self, on_progress: Callable[[str], None] | None = None,
    ) -> None:
        """Coldplug: discover attached devices and connect each.

        Dispatches ``DiscoverDevices`` (enumerate the registry against live
        USB), then ``ConnectDevice(key=…)`` per product (attach transport +
        handshake).  Only the connect step populates ``self.devices``, which
        every UI's sidebar / device picker reads — without this loop a
        long-lived UI starts empty and every device-keyed dispatch errors with
        "Not attached: vid:pid".  Long-lived UIs (gui / qtgui) call this once
        on startup; ``start_hotplug`` handles live changes afterwards; one-shot
        CLI scripts skip it.  ``on_progress`` receives a human string per step
        (splash display); connect *failures* are logged + recorded on the App
        model (queried via ``DeviceConnectionIssues``), never raised.
        """
        from .core.commands import ConnectDevice, DiscoverDevices

        def _say(message: str) -> None:
            log.debug("_say: message=%s", message)
            if on_progress is not None:
                on_progress(message)

        log.info("discover_and_connect: starting coldplug")
        self._coldplug_done = True
        _say("Discovering devices…")
        result = self.dispatch(DiscoverDevices())
        # Connect each scanned UNIT, not each catalog product: two identical
        # coolers are one product and two units, and only the scan's key
        # carries the port that tells them apart (#287).  Iterating products
        # attached the same model twice, unit-less, into one Device.
        for info, product in zip(result.devices, result.products, strict=True):
            _say(f"Connecting {product.vendor} {product.product}…")
            connect = self.dispatch(ConnectDevice(key=info.key))
            if not connect.ok:
                log.warning(
                    "discover_and_connect: connect %s failed: %s",
                    info.key, connect.message,
                )
        log.info(
            "discover_and_connect: %d product(s) discovered, %d attached",
            len(result.products), len(self.devices),
        )

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
        # A Command already declares its own frequency: per-tick ones set
        # ``LOG_LEVEL = DEBUG``.  So the sink follows the data rather than a
        # second list that would drift from it — per-tick dispatches join the
        # per-frame family and are silenced with it, while one-shot dispatches
        # ("dispatch LoadTheme(...)") stay in every report, which is what a
        # report is read for.
        sink = frame_log if cmd.LOG_LEVEL <= logging.DEBUG else log
        sink.log(cmd.LOG_LEVEL, "dispatch %r", cmd)
        result = cmd.execute(self)
        if not getattr(result, "ok", True):
            log.warning(
                "%s failed: %s",
                type(cmd).__name__,
                getattr(result, "message", "(no message)"),
            )
        else:
            sink.log(
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
        log.debug("__init__: app=%s", app)
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
            #
            # BUT skip any LED device the animation loop is already ticking:
            # it re-renders that device every 150 ms off the same cached
            # sample, so an extra render here just advances the effect timer
            # one more step right as the metric updates — a brief hiccup the
            # user sees as the numbers "glitching while updating" (#202).  The
            # loop owns the cadence for animating devices; static LED devices
            # (not ticked by the loop) still need this sensor-driven refresh.
            animating = set(self._app.led_animation_loop.animating_keys())
            keys = [
                k for k, d in self._app.devices.items()
                if d.is_connected
                and (d.is_led or k in self._app.active_themes)
                and k not in animating
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
                # Reactive re-render (settings/sensor change), NOT an
                # animation tick — hold the carousel so a slider drag or a
                # sensor broadcast doesn't race the metric page forward.
                self._app.dispatch(self._RenderLed(key=key, advance=False))
                continue
            theme = self._app.active_themes.get(key)
            if theme is None:
                log.debug(
                    "DeviceRenderObserver: skip %s for %s (no theme)",
                    type(event).__name__, key,
                )
                continue
            owner = self._cadence_owner(key)
            if owner is not None:
                log.debug(
                    "DeviceRenderObserver: skip %s for %s (%s owns the panel; "
                    "its tick renders at its own rate)",
                    type(event).__name__, key, owner,
                )
                continue
            log.debug(
                "DeviceRenderObserver: %s for %s → RenderAndSend",
                type(event).__name__, key,
            )
            self._app.dispatch(self._RenderAndSend(key=key))

    def _cadence_owner(self, key: str) -> str | None:
        """Which OTHER producer already drives this panel, if any.

        A reactive render is only worth dispatching when nothing else is
        writing the device.  When something is, the extra frame is at best
        redundant and at worst visible:

        * **screencast** — its tick composes background + mask + metrics at
          capture rate, and its background is the captured region while this
          one's is the theme.  Two DIFFERENT pictures, so at ~7 fps capture
          against a 2 s sensor tick the panel alternated ~13 screen frames
          with one theme frame: the "blinking metrics mask" (2026-09-14).
        * **video** — the animation tick already sends at the video's fps.
          Measured on the mock 320x320 panel: 40 composites and 40 wire writes
          per 40 ``SensorsUpdated``, on top of the tick's own.

        ``paused`` is load-bearing, not a detail.  ``lcd_handler.play_pause``
        stops the animation timer while ``PauseVideo`` leaves the ``Playback``
        in place, so a paused video has a playback and NO producer.  Keying
        off the playback's existence would freeze a paused panel's clock and
        metrics — which is why this reads the flag and not the object.

        Gating the PRODUCER, not the send: metrics still refresh, and sooner,
        because the owning tick composes them faster than the sensor cadence.

        The LED animation loop deliberately does NOT appear here.  It answers
        a different question: its devices are filtered out of the device-wide
        branch above, because a reactive LED render is not a duplicate but a
        deliberately different one — ``RenderLed(advance=False)`` HOLDS the
        carousel so a slider drag cannot race the metric page forward (#193 /
        #202).  Folding it in would be duplication removal that changes
        behaviour.
        """
        if self._app.settings.for_device(key).screencast_region is not None:
            return "screencast"
        playback = self._app.media.playback(key)
        if playback is not None and not playback.paused:
            return "video"
        log.debug("_cadence_owner: %s — nothing else owns this panel", key)
        return None
