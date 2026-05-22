"""App — the hub that wires Platform + Devices + EventBus + Commands.

Holds one Platform (the OS), one dict of live Devices keyed by their
'vid:pid' string, and one EventBus.  UIs dispatch Commands through
`app.dispatch(cmd)`; nothing else touches devices directly.
"""
from __future__ import annotations

import logging
from typing import TypeVar

from .adapters.device.bulk_lcd import BulkLcd
from .adapters.device.hid_lcd import HidLcd
from .adapters.device.led import Led
from .adapters.device.ly_lcd import LyLcd
from .adapters.device.scsi_lcd import ScsiLcd
from .adapters.repo.github_releases import GitHubReleases
from .adapters.repo.http import UrllibHttpFetcher
from .adapters.theme.cloud import CzhordeCatalog
from .core.commands import Command
from .core.errors import DeviceNotFoundError
from .core.events import EventBus
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

    # Wire → Device subclass.  New wire protocol = new entry.
    _DEVICE_CLASSES: dict[Wire, type[Device]] = {
        Wire.SCSI: ScsiLcd,
        Wire.HID: HidLcd,
        Wire.BULK: BulkLcd,
        Wire.LY: LyLcd,
        Wire.LED: Led,
    }

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
                cache_dir=platform.paths().data_dir() / "cloud_themes",
            ),
            paths=platform.paths(),
        )
        # GitHub releases adapter for `check_for_update` Command.
        self.github_releases = GitHubReleases(http=self.http)
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
        self._renderer = renderer
        # DisplayService is lazy: needs a Renderer.  None until one is set.
        self._display: DisplayService | None = None
        if renderer is not None:
            self._wire_display(renderer)
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
        cls = self._DEVICE_CLASSES.get(info.wire)
        if cls is None:
            raise DeviceNotFoundError(
                f"No Device implementation for wire={info.wire.value!r}"
            )
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
        """Disconnect every attached device + stop the hotplug listener."""
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
        """
        log.debug("dispatch: %s", type(cmd).__name__)
        return cmd.execute(self)
