"""App — the hub that wires Platform + Devices + EventBus + Commands.

Holds one Platform (the OS), one dict of live Devices keyed by their
'vid:pid' string, and one EventBus.  UIs dispatch Commands through
`app.dispatch(cmd)`; nothing else touches devices directly.
"""
from __future__ import annotations

import logging
from typing import Any, TypeVar

from .adapters.device import DeviceFactory
from .adapters.repo.github_releases import GitHubReleases
from .adapters.repo.http import UrllibHttpFetcher
from .adapters.theme.cloud import CzhordeCatalog
from .core.commands import Command
from .core.errors import DeviceNotFoundError
from .core.events import (
    BackgroundChanged,
    BrightnessChanged,
    DateFormatChanged,
    EventBus,
    FitModeChanged,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OverlayChanged,
    SensorsUpdated,
    SplitModeChanged,
    TempUnitChanged,
    TimeFormatChanged,
    VideoStarted,
    VideoStopped,
)
from .core.led_models import LedRuntimeState
from .core.models import Theme, Wire
from .core.ports import Device, Platform, Renderer
from .core.registry import find_product
from .core.results import Result
from .services.cloud_theme import CloudThemeService
from .services.display import DisplayService
from .services.first_run import FirstRunService
from .services.keepalive import KeepaliveService
from .services.led_effects import LEDEffectEngine
from .services.media import MediaService
from .services.metrics_loop import MetricsLoop
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
                 renderer: Renderer | None = None) -> None:
        self.platform = platform
        self.devices: dict[str, Device] = {}
        self.events = EventBus()
        self.settings = Settings(platform.paths())
        self.themes = ThemeService()
        self.media = MediaService()
        # Currently-loaded Theme per device — set by LoadTheme, read by
        # RenderAndSend ticker, cleared on DisconnectDevice.
        self.active_themes: dict[str, Theme] = {}
        # Per-device LED runtime counters — populated lazily by RenderLed,
        # cleared by DisconnectDevice.  Not persisted — these are tick
        # phase counters, not user prefs.
        self.led_runtime: dict[str, LedRuntimeState] = {}
        self.led_effects = LEDEffectEngine()
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
        # Slideshow + keepalive — small per-device state holders.  Both
        # are tick-driven; no background threads inside the services.
        self.slideshow = SlideshowService()
        self.keepalive = KeepaliveService()
        # First-run flag — lightweight marker-file check.  next/'s Paths
        # port resolves to legacy's content layout (see `core/ports.py`),
        # so installed-user themes/masks are visible in place without an
        # explicit migration pass.
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
        # Hotplug listener — caller (daemon, GUI launcher, tests) decides
        # whether to ``start_hotplug``.  In-process CLI scripts that
        # only do one Command don't need it; the daemon and GUI do.
        self._hotplug_started = False

    def set_renderer(self, renderer: Renderer) -> None:
        """Attach a Renderer (headless modes can defer until needed)."""
        self._renderer = renderer
        self._wire_display(renderer)

    def _wire_display(self, renderer: Renderer) -> None:
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
        device = self.devices.get(key)
        if device is None:
            raise DeviceNotFoundError(f"Not attached: {key}")
        return device

    def detach(self, key: str) -> None:
        """Disconnect and drop a device.  Frees the scene cache + active theme."""
        device = self.devices.pop(key, None)
        if device is not None:
            device.disconnect()
        self.active_themes.pop(key, None)
        self.led_runtime.pop(key, None)
        self.media.unload(key)
        self.keepalive.forget(key)
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
        self.metrics_loop.stop()
        self.stop_hotplug()
        for key in list(self.devices):
            self.detach(key)

    # ── Hotplug ───────────────────────────────────────────────────────

    def start_hotplug(self) -> None:
        """Start the OS hotplug listener (idempotent).

        Translates udev / kernel events into ``DeviceAttached`` /
        ``DeviceDetached`` events on ``self.events``.  Long-lived
        processes (daemon, GUI) should call this once on startup;
        one-shot CLI scripts can skip it.
        """
        if self._hotplug_started:
            return
        self.platform.hotplug().start(self.events)
        self._hotplug_started = True

    def stop_hotplug(self) -> None:
        """Stop the hotplug listener (idempotent)."""
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
        from .core.commands import RenderAndSend
        self._RenderAndSend = RenderAndSend
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
            keys = [
                k for k, d in self._app.devices.items()
                if d.is_connected and k in self._app.active_themes
            ]
        for key in keys:
            device = self._app.devices.get(key)
            theme = self._app.active_themes.get(key)
            if (
                device is None or not device.is_connected
                or theme is None
            ):
                log.debug(
                    "DeviceRenderObserver: skip %s for %s "
                    "(connected=%s theme=%s)",
                    type(event).__name__, key,
                    device is not None and device.is_connected,
                    theme is not None,
                )
                continue
            log.debug(
                "DeviceRenderObserver: %s for %s → RenderAndSend",
                type(event).__name__, key,
            )
            self._app.dispatch(self._RenderAndSend(key=key))
