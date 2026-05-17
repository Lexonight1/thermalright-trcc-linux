"""Trcc — the unified command facade for GUI, CLI, and API.

The one class every UI talks to. Composes LCDCommands, LEDCommands,
ControlCenterCommands, and an EventBus. Holds discovered devices.
Owns lifecycle for the injected `MetricsLoop` (started by `discover`,
stopped by `cleanup` and OS suspend), data extraction pipeline, and
hotplug.

Parity rule: every method reachable from one UI is reachable from all
three. No shortcuts, no UI-specific extensions. See TRCC_CONTRACT.md.

Pure DI (Mark Seemann): every dependency arrives at construction. No
late ``set_X`` mutation. Composition root (``trcc._boot.trcc()``) is
the one place that wires real adapters; tests pass fakes via the same
keyword arguments.

Usage from a composition root::

    trcc = Trcc(platform, renderer=QtRenderer(),
                ensure_data_fn=DataManager.ensure_all)
    trcc.discover()

From any UI::

    trcc.lcd.set_brightness(0, 50)
    trcc.led.set_color(0, 255, 0, 0)
    trcc.control_center.set_temp_unit('F')
    trcc.events.subscribe('device.list', on_devices_changed)
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from itertools import chain
from typing import TYPE_CHECKING, Any

from .control_center_commands import ControlCenterCommands
from .device.registry import DeviceRegistry
from .events import EventBus, Topic
from .lcd_commands import LCDCommands
from .led_commands import LEDCommands
from .results import DiscoveryResult

if TYPE_CHECKING:
    from ..services.system import SystemService
    from .device.lcd import LCDDevice
    from .device.led import LEDDevice
    from .models import DetectedDevice, DeviceInfo
    from .ports import EnsureDataFn, MetricsLoop, Platform, Renderer

log = logging.getLogger(__name__)


class Trcc:
    """Universal command facade and process context.

    Construction is explicit (takes Platform + optional infra deps).
    One Trcc per process — composition root builds it, UI adapters
    consume it.

    Slotted: no per-instance __dict__, ~40% smaller footprint, faster
    attribute access (direct offset, no dict lookup).
    """

    __slots__ = (
        '_current_metrics', '_download_pack_fn', '_ensure_data_fn',
        '_lcd_devices', '_led_devices', '_list_available_fn',
        '_metrics_loop', '_platform', '_renderer', '_settings',
        '_system_svc', 'control_center', 'events', 'lcd', 'led',
    )

    def __init__(
        self,
        platform: Platform,
        *,
        renderer: Renderer | None = None,
        system_svc: SystemService | None = None,
        ensure_data_fn: EnsureDataFn | None = None,
        download_pack_fn: Callable[..., int] | None = None,
        list_available_fn: Callable[..., None] | None = None,
        settings: Any = None,
    ) -> None:
        self._platform = platform
        self._renderer = renderer
        self._system_svc = system_svc
        self._ensure_data_fn = ensure_data_fn
        self._download_pack_fn = download_pack_fn
        self._list_available_fn = list_available_fn
        # Settings injection (Phase 10A.3). Bound once at construction —
        # composition root (``_boot.trcc()`` and test fixtures) calls
        # ``init_settings(platform)`` and passes the resulting instance.
        # When ``settings`` isn't passed, we fall back to the global ONCE
        # here (not on every property access) — keeps tests that build
        # ``Trcc(platform)`` directly working without the late-binding
        # cost on every consumer's settings lookup.
        if settings is None:
            from ..conf import settings as _global
            settings = _global
        self._settings = settings
        self._current_metrics: Any = None

        self._lcd_devices: DeviceRegistry[LCDDevice] = DeviceRegistry()
        self._led_devices: DeviceRegistry[LEDDevice] = DeviceRegistry()

        self.events = EventBus()
        self.lcd = LCDCommands(self._lcd_devices, self.events, self._settings)
        self.led = LEDCommands(self._led_devices, self.events)
        self.control_center = ControlCenterCommands(
            platform, self.events, self._settings,
            set_temp_unit=self.set_temp_unit,
        )

        # The OS produces the metrics loop — fourth factory in the chain
        # (Platform / Protocol / Device / MetricsLoop). Default is a
        # thread-based PollingMetricsLoop; per-OS overrides plug in native
        # event sources (WMI / IOReport) without touching Trcc.
        self._metrics_loop: MetricsLoop = platform.build_metrics_loop(self)

        # Subscribe to OS suspend/resume so a sleeping machine doesn't leave
        # devices with stale USB handles after wake (issue #144).  Platform
        # default is a no-op; Linux uses logind's PrepareForSleep, other
        # OSes get stubs until reporters surface a need.
        platform.subscribe_power(self._on_suspend, self._on_resume)

    # ── Lifecycle ────────────────────────────────────────────────────
    # No public ``bootstrap`` / ``with_renderer`` / factory classmethods —
    # composition lives in ``trcc._boot.trcc()``. Constructors are final
    # (Pure DI). Tests build a Trcc directly with the kwargs they need.

    @property
    def os(self) -> Platform:
        """The Platform this Trcc is bound to. Read-only — composition is final."""
        return self._platform

    @property
    def settings(self) -> Any:
        """The injected `Settings` instance.

        Bound at construction (see ``__init__``). Consumers reach it via
        ``trcc.settings`` rather than ``from trcc.conf import settings``,
        so test isolation is one ctor arg away — no global patching.
        """
        return self._settings

    # ── Convenience accessors — first-of-kind devices ───────────────────

    @property
    def lcd_device(self) -> LCDDevice | None:
        """First connected LCD device, or None.

        For sustained use access devices via iteration (`for d in trcc:`)
        or the typed lists (``lcd_devices``, ``led_devices``).
        """
        return self._lcd_devices[0] if self._lcd_devices else None

    @property
    def led_device(self) -> LEDDevice | None:
        """First connected LED device, or None."""
        return self._led_devices[0] if self._led_devices else None

    @property
    def lcd_devices(self) -> DeviceRegistry[LCDDevice]:
        """All connected LCD devices, in detection order.

        Returns the live `DeviceRegistry` — callers get rich indexing
        on top of normal iteration / len / membership::

            trcc.lcd_devices[0]                  # by index
            trcc.lcd_devices['/dev/sg0']         # by device_path
            trcc.lcd_devices[(0x0402, 0x3922)]   # by (vid, pid)
        """
        return self._lcd_devices

    @property
    def led_devices(self) -> DeviceRegistry[LEDDevice]:
        """All connected LED devices, in detection order. See :attr:`lcd_devices`."""
        return self._led_devices

    @property
    def renderer(self) -> Renderer | None:
        """The rendering backend wired at construction (``QtRenderer`` in production).

        Read-only handle — needed by the IPC server to encode
        ``Topic.FRAME`` surface payloads for ``TrccProxy`` clients.
        """
        return self._renderer

    # ── Descriptors — wire-safe identity slices for clients ───────────────────
    # Pure DeviceInfo lists (no LCDDevice / LEDDevice references).  Stable
    # across the IPC boundary: TrccProxy uses the same shape via its own
    # implementation that fetches these over the wire.  GUI / CLI / API
    # callers that only need device identity (sidebar buttons, list views)
    # consume these instead of the live device registries.

    def lcd_descriptors(self) -> list[DeviceInfo]:
        """JSON-safe identity descriptors for every connected LCD device.

        Returns the device's own `DeviceInfo` (already a dataclass that
        round-trips through ``to_wire_dict``/``from_wire_dict``).
        """
        return [
            d.device_info for d in self._lcd_devices
            if d.device_info is not None
        ]

    def led_descriptors(self) -> list[DeviceInfo]:
        """JSON-safe identity descriptors for every connected LED device."""
        return [
            d.device_info for d in self._led_devices
            if d.device_info is not None
        ]

    @property
    def has_lcd(self) -> bool:
        """True iff at least one LCD device is connected."""
        return bool(self._lcd_devices)

    @property
    def has_led(self) -> bool:
        """True iff at least one LED device is connected."""
        return bool(self._led_devices)

    def register_lcd(self, device: LCDDevice) -> int:
        """Register an already-built+connected LCD device with the command layer.

        Returns the index the device got.  UI adapters that manage their own
        device lifecycle (e.g. the GUI's per-handler detection flow) call
        this instead of `discover()` so their devices show up in
        `Trcc.lcd._devices` and command dispatch resolves the index.

        Idempotent: if the same device is already registered, returns its
        existing index.
        """
        if device in self._lcd_devices:
            return self._lcd_devices.index(device)
        self._lcd_devices.append(device)
        return len(self._lcd_devices) - 1

    def register_led(self, device: LEDDevice) -> int:
        """Register an already-built+connected LED device.  Same contract as
        `register_lcd`."""
        if device in self._led_devices:
            return self._led_devices.index(device)
        self._led_devices.append(device)
        return len(self._led_devices) - 1

    def discover(
        self, *,
        path: str | None = None,
        ensure_data: bool = True,
    ) -> DiscoveryResult:
        """Enumerate, connect, and register every reachable device.

        Detection is sequential (single USB enumerate call); per-device
        connect runs in parallel — one daemon thread per device, capped
        at 30s — so USB handshakes don't serialize.

        Each successful connect mirrors into ``_lcd_devices`` /
        ``_led_devices``, runs the LCD pipeline init, and publishes
        ``device.connected``. After the pool drains we publish a single
        ``device.list`` snapshot.

        ``ensure_data`` blocks on theme/web/mask extraction for every
        unique LCD resolution before returning, so the UI starts with
        themes present and skips the empty-list-then-populate flash.
        Hotplug uses :meth:`device_connected` instead — non-blocking.

        ``path`` constrains the result to a specific USB path; if no
        connected device matches we return a failed result so CLI
        callers like ``trcc test --device /dev/sg0`` surface the
        error cleanly without a separate post-scan check.
        """
        from .builder import ControllerBuilder
        from .device.lcd import LCDDevice
        from .device.led import LEDDevice
        from .models import PROTOCOL_TRAITS

        builder = ControllerBuilder(self._platform, events=self.events)
        if self._renderer is not None:
            builder = builder.with_renderer(self._renderer)

        try:
            detected = list(self._platform)
        except Exception as e:
            log.exception('discover: detect failed')
            return DiscoveryResult(success=False, error=str(e))

        log.info('discover: detected %d device(s): %s', len(detected),
                 ', '.join(f'{d.path} ({d.protocol})' for d in detected) or '(none)')

        self._lcd_devices.clear()
        self._led_devices.clear()
        _settings = self.settings
        lock = threading.Lock()

        def _connect_one(found: DetectedDevice) -> None:
            traits = PROTOCOL_TRAITS.get(
                getattr(found, 'protocol', 'scsi'), PROTOCOL_TRAITS['scsi'])
            try:
                device = builder.build_device(found)
                connect_result = device.connect(found)
                if not connect_result.get('success'):
                    log.warning('discover: connect failed for %s: %s',
                                found.path, connect_result.get('error'))
                    return
            except Exception:
                log.exception('discover: build/connect raised for %s', found.path)
                return

            info = device.device_info
            res = getattr(info, 'resolution', (0, 0)) if info else (0, 0)
            log.info('discover: connected %s %dx%d', found.path, *res)

            with lock:
                if traits.is_lcd and isinstance(device, LCDDevice):
                    self._lcd_devices.append(device)
                    if _settings is not None:
                        device.initialize_pipeline(_settings)
                elif traits.is_led and isinstance(device, LEDDevice):
                    self._led_devices.append(device)
                self.events.publish(Topic.DEVICE_CONNECTED, device)

        threads = [
            threading.Thread(target=_connect_one, args=(d,), daemon=True)
            for d in detected
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        all_devices = tuple(chain(self._lcd_devices, self._led_devices))
        self.events.publish(Topic.DEVICE_LIST, all_devices)
        log.info('discover: %d of %d device(s) connected (%d LCD, %d LED)',
                 len(all_devices), len(detected),
                 len(self._lcd_devices), len(self._led_devices))

        if path and not any(
            getattr(d, 'device_path', None) == path for d in all_devices
        ):
            return DiscoveryResult(
                success=False, error=f'Device not found: {path}')

        if ensure_data:
            self._ensure_data_blocking()

        # Start the underlying command stream now that devices are connected.
        # Idempotent — safe across suspend/resume and hotplug. RuntimeError
        # when SystemService is missing is tolerated so headless / smoke
        # paths still report a successful discover.
        try:
            self._metrics_loop.start()
        except RuntimeError:
            log.warning('discover: metrics loop not started — SystemService missing')

        lcd_infos = [d.device_info for d in self._lcd_devices]
        led_infos = [d.device_info for d in self._led_devices]
        return DiscoveryResult(
            success=True,
            message=f'Found {len(lcd_infos)} LCD(s), {len(led_infos)} LED(s)',
            lcd_devices=lcd_infos,
            led_devices=led_infos,
        )

    # ── Hotplug ──────────────────────────────────────────────────────

    def device_connected(self, detected: DetectedDevice) -> None:
        """Build, connect, and register a hotplugged device.

        Background data-ensure runs in a thread so the UI keeps
        responding while themes for the new resolution download.
        """
        from .builder import ControllerBuilder
        from .device.lcd import LCDDevice
        from .device.led import LEDDevice
        from .models import PROTOCOL_TRAITS

        builder = ControllerBuilder(self._platform, events=self.events)
        if self._renderer is not None:
            builder = builder.with_renderer(self._renderer)

        log.info('device_connected: hotplug %s (%s)', detected.path, detected.protocol)
        try:
            device = builder.build_device(detected)
            connect_result = device.connect(detected)
            if not connect_result.get('success'):
                log.warning('device_connected: connect failed for %s', detected.path)
                return
        except Exception:
            log.exception('device_connected: build/connect raised for %s', detected.path)
            return

        info = device.device_info
        res = getattr(info, 'resolution', (0, 0)) if info else (0, 0)
        log.info('device_connected: connected %s %dx%d', detected.path, *res)

        traits = PROTOCOL_TRAITS.get(
            getattr(detected, 'protocol', 'scsi'), PROTOCOL_TRAITS['scsi'])
        _settings = self.settings
        if traits.is_lcd and isinstance(device, LCDDevice):
            self._lcd_devices.append(device)
            if _settings is not None:
                device.initialize_pipeline(_settings)
            w, h = res
            if w and h:
                self._ensure_data_background(device, w, h)
        elif traits.is_led and isinstance(device, LEDDevice):
            self._led_devices.append(device)

        self.events.publish(Topic.DEVICE_CONNECTED, device)
        self.events.publish(
            Topic.DEVICE_LIST,
            tuple(chain(self._lcd_devices, self._led_devices)),
        )

    def device_lost(self, path: str) -> None:
        """Remove a device by USB path; publish disconnected + new list."""
        gone: LCDDevice | LEDDevice
        if path in self._lcd_devices:
            gone = self._lcd_devices[path]
            self._lcd_devices.remove(gone)
        elif path in self._led_devices:
            gone = self._led_devices[path]
            self._led_devices.remove(gone)
        else:
            return  # path not found in either registry — no-op
        self.events.publish(Topic.DEVICE_DISCONNECTED, gone)
        self.events.publish(
            Topic.DEVICE_LIST,
            tuple(chain(self._lcd_devices, self._led_devices)),
        )

    # ── Data extraction ──────────────────────────────────────────────

    def _ensure_data_blocking(self) -> None:
        """Run ensure_all() synchronously for every unique LCD resolution.

        Called at the end of discover() so the UI starts with data
        present. Hotplug uses :meth:`_ensure_data_background` instead.
        """
        if (ensure_fn := self._ensure_data_fn) is None:
            log.warning('_ensure_data_blocking: no ensure_fn injected — skipping')
            return
        seen: set[tuple[int, int]] = set()
        for device in self._lcd_devices:
            info = device.device_info
            path = getattr(info, 'path', '?') if info else '?'
            w, h = getattr(info, 'resolution', (0, 0)) if info else (0, 0)
            if not (w and h):
                log.warning('_ensure_data_blocking: skip %s — resolution (0,0)', path)
                continue
            if (w, h) in seen:
                continue
            seen.add((w, h))
            log.info('_ensure_data_blocking: ensuring data %dx%d for %s', w, h, path)
            ensure_fn(
                w, h,
                progress_fn=lambda msg: self.events.publish(
                    Topic.BOOTSTRAP_PROGRESS, msg),
            )
            device.notify_data_ready()
        log.info('_ensure_data_blocking: done — %d resolution(s) processed', len(seen))

    def _ensure_data_background(
        self, device: LCDDevice, w: int, h: int,
    ) -> None:
        """Ensure theme data in a background thread — hotplug path."""
        ensure_fn = self._ensure_data_fn
        info = device.device_info
        path = getattr(info, 'path', '?') if info else '?'
        log.info('_ensure_data_background: starting %dx%d for %s', w, h, path)

        def _bg() -> None:
            try:
                if ensure_fn is not None:
                    ensure_fn(w, h)
                else:
                    log.warning('_ensure_data_background: no ensure_fn for %s', path)
                device.notify_data_ready()
                log.info('_ensure_data_background: done %dx%d for %s', w, h, path)
            except Exception:
                log.exception('_ensure_data_background: failed %dx%d for %s', w, h, path)

        threading.Thread(target=_bg, daemon=True, name='data-extract').start()

    # ── Metrics / animation loop ─────────────────────────────────────

    def set_system_service(self, system_svc: SystemService) -> None:
        """Inject SystemService post-construction (pre-existing tests + dev mocks).

        Composition root prefers the constructor kwarg; this is the
        deprecated late-DI seam kept for the test fixture chain that
        builds a Trcc before the service exists.
        """
        from ..services.system import set_instance
        self._system_svc = system_svc
        set_instance(system_svc)

    @property
    def current_metrics(self) -> Any:
        """Most recently polled metrics with temp unit applied, or None."""
        return self._current_metrics

    def set_current_metrics(self, metrics: Any) -> None:
        """Update the cached metrics snapshot.

        Called by the injected `MetricsLoop` after every poll iteration,
        and by :meth:`set_temp_unit` when the user switches °C ↔ °F.
        Public so the loop port stays decoupled from Trcc's private state.
        """
        self._current_metrics = metrics

    @property
    def system_svc(self) -> SystemService | None:
        """The injected sensor read-out service (or None if not yet set).

        Exposed so the injected `MetricsLoop` can poll metrics without
        reaching into Trcc's private state. Updated by
        :meth:`set_system_service` for the test/dev fixture chain.
        """
        return self._system_svc

    def wake_metrics_loop(self) -> None:
        """Force an immediate poll iteration — used after settings changes.

        Delegates to the injected `MetricsLoop.wake()`. Kept as a method
        on Trcc so callers (control center commands) don't reach into
        the loop directly.
        """
        self._metrics_loop.wake()

    # ── Cross-cutting OS operations (touch every device + the loop) ──

    def set_temp_unit(self, unit: int) -> dict[str, Any]:
        """Persist temp unit + push to every device + refresh metrics.

        Cross-cuts settings, every connected device, and the metrics
        loop — lives on Trcc rather than ControlCenterCommands because
        no facade owns the device list.  ``trcc.set_temp_unit(0|1)`` is
        the canonical cross-UI entrypoint; ``ControlCenterCommands.
        set_temp_unit('C'|'F')`` and ``LCDDevice.set_temp_unit`` are
        thin wrappers that all funnel through here.
        """
        from .models import HardwareMetrics

        self.settings.set_temp_unit(unit)
        fresh = HardwareMetrics.with_temp_unit(self._platform.metrics, unit)
        self._current_metrics = fresh

        for device in chain(self._lcd_devices, self._led_devices):
            device.set_temp_unit(unit)
            device.update_metrics(fresh)

        self.wake_metrics_loop()
        unit_str = 'F' if unit else 'C'
        self.events.publish(Topic.CONTROL_CENTER_TEMP_UNIT, unit_str)
        return {'success': True, 'message': f'Temperature unit set to °{unit_str}'}

    def set_metrics_refresh(self, seconds: int) -> dict[str, Any]:
        """Persist the metrics refresh interval and wake the loop."""
        clamped = max(1, min(100, seconds))
        self.settings.set_refresh_interval(clamped)
        self.wake_metrics_loop()
        self.events.publish(Topic.CONTROL_CENTER_REFRESH, clamped)
        return {'success': True, 'message': f'Refresh interval set to {clamped}s'}

    # ── Data / theme distribution (delegates to injected callables) ──

    def download_themes(self, pack: str = '', force: bool = False) -> int:
        """Download a theme pack, or list available packs when pack=''.

        Returns a CLI-shaped exit code (0 = ok, 1 = error / no pack fn).
        """
        if not pack:
            if self._list_available_fn is not None:
                self._list_available_fn()
            return 0
        if self._download_pack_fn is not None:
            return self._download_pack_fn(pack, force)
        log.warning('download_themes: no download_pack_fn injected')
        return 1

    # ── Power events — Trcc reacts to OS suspend / resume ────────────────
    # Subscription is wired by Platform.subscribe_power() in __init__.
    # On suspend: every device's transport gets dropped so no stale fd
    # survives the kernel's USB power cycle.  On resume: a fresh discover()
    # rebuilds the device list — /dev/sgN may have shifted, USB addresses
    # may differ, the firmware may need a re-handshake.

    def _on_suspend(self) -> None:
        """Drop every device + protocol cache so wake reopens cleanly.

        Publishes SYSTEM_SUSPENDED first so subscribers (GUI screencast,
        long-running streaming services) can stop their own resources
        before the device list goes empty.
        """
        from ..adapters.device.factory import DeviceProtocolFactory
        self.events.publish(Topic.SYSTEM_SUSPENDED)
        self._metrics_loop.stop()
        for dev in self:
            try:
                dev.cleanup()
            except Exception:
                log.exception('on_suspend: cleanup failed for %s', dev)
        self._lcd_devices.clear()
        self._led_devices.clear()
        DeviceProtocolFactory.close_all()
        self.events.publish(Topic.DEVICE_LIST, ())

    def _on_resume(self) -> None:
        """Rediscover after wake — USB may have re-enumerated.

        Publishes SYSTEM_RESUMED after discover() repopulates devices,
        so subscribers know the system is back online.
        """
        try:
            self.discover()
        except Exception:
            log.exception('on_resume: discover failed')
        self.events.publish(Topic.SYSTEM_RESUMED)

    def cleanup(self) -> None:
        """Stop the metrics loop, release every device, drop subscribers.

        Also clears the protocol factory cache — UIs call this on app
        close instead of reaching into ``DeviceProtocolFactory.close_all``.
        """
        from ..adapters.device.factory import DeviceProtocolFactory
        self._metrics_loop.stop()
        for dev in self:
            try:
                dev.cleanup()
            except Exception:
                log.exception('cleanup failed for %s', dev)
        self._lcd_devices.clear()
        self._led_devices.clear()
        DeviceProtocolFactory.close_all()
        self.events.clear()

    # ── Probe / handshake — UI-facing wrappers around the factory ──────────
    # UIs (cli/_device.py probe listing, gui/trcc_app.py handshake worker)
    # used to import DeviceProtocolFactory directly.  These two methods are
    # the single Trcc-side entry points so the UI layer never imports
    # anything from `adapters.device`.

    def probe(self, detected: DetectedDevice) -> dict[str, Any]:
        """Handshake a detected device and extract resolved metadata.

        Returns model / pm / resolution / serial / style as available
        for the device's protocol.  Empty dict when no probe runs or
        the handshake fails — callers degrade gracefully.

        Used by ``trcc devices --probe`` and by tools that need
        per-device details before a full discover/connect cycle.
        """
        from ..adapters.device.factory import DeviceProtocolFactory

        impl = detected.implementation
        result: dict[str, Any] = {}
        log.debug('Trcc.probe: %04x:%04x impl=%s', detected.vid, detected.pid, impl)

        if impl == 'hid_led':
            from ..adapters.device.led import probe_led_model
            try:
                info = probe_led_model(
                    detected.vid, detected.pid, usb_path=detected.usb_path)
            except Exception:
                log.debug('probe: LED handshake failed for %04x:%04x',
                          detected.vid, detected.pid, exc_info=True)
                return {}
            if info and info.model_name:
                result['model'] = info.model_name
                result['pm'] = info.pm
                result['style'] = info.style
            return result

        if impl in ('hid_type2', 'hid_type3'):
            from ..adapters.device.hid import HidHandshakeInfo
            device_info = {
                'vid': detected.vid, 'pid': detected.pid,
                'protocol': detected.protocol,
                'device_type': detected.device_type,
                'implementation': detected.implementation,
                'path': f'hid:{detected.vid:04x}:{detected.pid:04x}',
            }
            try:
                protocol = DeviceProtocolFactory.get_protocol(device_info)
                raw = protocol.handshake()
            except Exception:
                log.debug('probe: HID handshake failed for %04x:%04x',
                          detected.vid, detected.pid, exc_info=True)
                return {}
            if isinstance(raw, HidHandshakeInfo):
                result['pm'] = raw.mode_byte_1
                result['resolution'] = raw.resolution
                if raw.serial:
                    result['serial'] = raw.serial
            return result

        if impl == 'bulk_usblcdnew':
            try:
                protocol = DeviceProtocolFactory.create_protocol(detected)
                hs = protocol.handshake()
                protocol.close()
            except Exception:
                log.debug('probe: bulk handshake failed for %04x:%04x',
                          detected.vid, detected.pid, exc_info=True)
                return {}
            if hs and hs.resolution:
                result['resolution'] = hs.resolution
                result['pm'] = hs.model_id
            return result

        return result

    # ── Factory delegates — for benchmarks / diagnostics that need raw access ──
    # These keep the UI layer free of `adapters.device.factory` imports while
    # still letting tooling (perf bench, debug report) reach the cached
    # protocol map.

    def detect(self) -> list[DetectedDevice]:
        """Run the platform's USB enumeration and return detected devices.

        Re-runs detection on demand — independent of the cached
        ``_lcd_devices`` / ``_led_devices`` populated by ``discover()``.
        Used by perf benchmarks and debug-report tooling.
        """
        return list(self._platform)

    def protocol_for(self, device_info: Any) -> Any:
        """Get-or-create the cached DeviceProtocol for this device info."""
        from ..adapters.device.factory import DeviceProtocolFactory
        return DeviceProtocolFactory.get_protocol(device_info)

    def protocol_info_for(self, device_info: Any = None) -> Any:
        """ProtocolInfo describing backend availability for this device."""
        from ..adapters.device.factory import DeviceProtocolFactory
        return DeviceProtocolFactory.get_protocol_info(device_info)

    def probe_led(self, vid: int, pid: int, *, usb_path: str = '') -> Any:
        """LED-specific probe — model name + PM via cached handshake."""
        from ..adapters.device.led import probe_led_model
        return probe_led_model(vid, pid, usb_path=usb_path)

    def handshake(self, info: DeviceInfo) -> Any | None:
        """One-shot handshake of a known device.

        Returns the raw ``HandshakeResult`` on success, ``None`` on any
        failure. Used by the GUI's reactivation flow; the GUI feeds the
        result into ``DeviceInfo.enrich_from_handshake`` rather than
        re-implementing the field-by-field copy.
        """
        from ..adapters.device.factory import DeviceProtocolFactory
        try:
            protocol = DeviceProtocolFactory.get_protocol(info)
            result = protocol.handshake()
        except Exception:
            log.warning('handshake failed for %s',
                        getattr(info, 'path', info), exc_info=True)
            return None
        return result or None

    # ── Container protocol ───────────────────────────────────────────
    # Trcc IS the registry of connected devices — `for d in trcc` walks
    # every LCD then every LED, `len(trcc)` is total device count, and
    # `bool(trcc)` is True iff anything is connected.

    def __iter__(self) -> Iterator[LCDDevice | LEDDevice]:
        return chain(self._lcd_devices, self._led_devices)

    def __len__(self) -> int:
        return len(self._lcd_devices) + len(self._led_devices)

    def __bool__(self) -> bool:
        return bool(self._lcd_devices) or bool(self._led_devices)

    def __contains__(self, key: object) -> bool:
        """Membership: ``(vid, pid) in trcc`` or ``device in trcc``.

        Accepts a (vid, pid) tuple OR a device instance — matches the
        registry semantics so callers can ask either question.
        """
        if isinstance(key, tuple) and len(key) == 2:
            vid, pid = key
            return any(
                d.device_info is not None
                and d.device_info.vid == vid
                and d.device_info.pid == pid
                for d in self
            )
        return any(d is key for d in self)

    def __getitem__(self, key: int | tuple[int, int]) -> LCDDevice | LEDDevice:
        """Indexing: ``trcc[i]`` walks the iteration; ``trcc[(vid, pid)]`` looks up by id.

        Index across both LCD and LED in iteration order — same order as
        ``for d in trcc``.  KeyError / IndexError on miss, matching the
        normal container contract.
        """
        if isinstance(key, int):
            for i, dev in enumerate(self):
                if i == key:
                    return dev
            raise IndexError(f'Trcc index out of range: {key} (have {len(self)})')
        if isinstance(key, tuple) and len(key) == 2:
            vid, pid = key
            for dev in self:
                info = dev.device_info
                if info is not None and info.vid == vid and info.pid == pid:
                    return dev
            raise KeyError(f'No device with vid:pid {vid:04x}:{pid:04x}')
        raise TypeError(f'Trcc indices must be int or (vid, pid) tuple, got {type(key).__name__}')

    # ── Context manager — deterministic cleanup on `with` exit ─────────

    def __enter__(self) -> Trcc:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()
