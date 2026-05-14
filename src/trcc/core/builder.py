"""ControllerBuilder — assembles devices with dependency injection.

Platform is injected via constructor. The factory classmethod
for_current_os() is the single OS check — overrideable by subclasses
for testing or platform extension.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ports import (
    Platform,
    Renderer,
)

if TYPE_CHECKING:
    from ..services.system import SystemService
    from .device import Device
    from .device.lcd import LCDDevice

log = logging.getLogger(__name__)


class ControllerBuilder:
    """Assembles Device / SystemService with injected deps.

    Platform is injected at construction — all build methods use
    self._os. No OS checks anywhere except for_current_os().

    Usage::

        builder = ControllerBuilder.for_current_os().with_renderer(renderer)
        device  = builder.build_device(detected)
        system  = builder.build_system()
    """

    def __init__(self, platform: Platform, events: Any = None) -> None:
        self._os = platform
        self._renderer: Renderer | None = None
        self._data_dir: Path | None = None
        # EventBus is injected when the builder is constructed by Trcc — it
        # gets threaded into LCDDevice / LEDDevice so device-level state
        # changes can publish Topic.FRAME directly (Observer SSoT).  Free-
        # standing builder uses (CLI / tests / dev/mock_gui without a Trcc)
        # leave it None and the device skips publish silently.
        self._events = events

    # ── Factory entry point (overrideable) ─────────────────────────

    _OS_PLATFORMS: dict[str, tuple[str, str]] = {
        'win32':  ('trcc.adapters.system.windows_platform', 'WindowsPlatform'),
        'darwin': ('trcc.adapters.system.macos_platform',   'MacOSPlatform'),
        'linux':  ('trcc.adapters.system.linux_platform',   'LinuxPlatform'),
        'bsd':    ('trcc.adapters.system.bsd_platform',     'BSDPlatform'),
    }

    @classmethod
    def for_current_os(cls) -> ControllerBuilder:
        """Create a builder with the OS-appropriate Platform.

        Delegates to ``PlatformFactory.current()`` — the single OS chokepoint.
        Honors ``TRCC_MOCK`` so test/dev runs get ``MockPlatform`` without
        the caller knowing or caring.
        """
        from trcc.adapters.system import PlatformFactory
        return cls(PlatformFactory.current())

    def bootstrap(self, verbosity: int = 0) -> None:
        """Bootstrap the platform: logging → OS setup → settings.

        Called by the InitPlatformCommand handler in TrccApp — never directly
        by composition roots. Commands are boss.
        """
        from ..adapters.infra.logging_setup import StandardLoggingConfigurator
        from ..conf import init_settings

        StandardLoggingConfigurator().configure(verbosity=verbosity)
        self._os.configure_stdout()
        init_settings(self._os)

    # ── Fluent setters ─────────────────────────────────────────────

    @property
    def os(self) -> Platform:
        """The Platform for this builder."""
        return self._os

    def with_renderer(self, renderer: Renderer) -> ControllerBuilder:
        self._renderer = renderer
        return self

    def with_data_dir(self, data_dir: Path) -> ControllerBuilder:
        self._data_dir = data_dir
        return self

    # ── Shared wiring helpers ─────────────────────────────────────

    def _build_device_svc(self) -> Any:
        """Create DeviceService with platform-injected callables."""
        from ..adapters.device.factory import DeviceProtocolFactory
        from ..adapters.device.led import probe_led_model
        from ..services import DeviceService
        DeviceProtocolFactory.set_scsi_transport(self._os.create_scsi_transport)
        return DeviceService(
            detect_fn=self._os.detect_devices,
            probe_led_fn=probe_led_model,
            get_protocol=DeviceProtocolFactory.get_protocol,
            get_protocol_info=DeviceProtocolFactory.get_protocol_info,
        )

    def _build_config_callables(self) -> dict:
        """Return Settings config callables for device persistence."""
        from ..conf import Settings
        return {
            'config_key_fn': Settings.device_config_key,
            'save_setting_fn': Settings.save_device_setting,
            'get_config_fn': Settings.get_device_config,
        }

    # ── Build methods ──────────────────────────────────────────────

    def build_device(self, detected: Any = None) -> Device:
        """Build a Device — orchestrates the three-factory chain.

            detected (DetectedDevice / DeviceInfo / None)
              → DeviceInfo.from_detected          (typed DTO chokepoint)
              → DeviceFactory.for_info(info, self) — dispatches by *kind*
                    ↳ LEDDeviceFactory   → LEDDevice(protocol=…, …)
                    ↳ LCDDeviceFactory   → LCDDevice(protocol=…, …)

        The DeviceFactory subclass internally calls
        ``DeviceProtocolFactory.get_protocol(info)`` so the protocol is
        DI'd by name into the Device's constructor.

        ``detected=None`` (API standalone) → LCDDevice with no protocol;
        discovers later via ``device.connect(detected)``.
        """
        from .device.factory import DeviceFactory
        from .models import DetectedDevice, DeviceInfo

        info: DeviceInfo | None
        if isinstance(detected, DetectedDevice):
            info = DeviceInfo.from_detected(detected)
        elif isinstance(detected, DeviceInfo):
            info = detected
        else:
            info = None

        return DeviceFactory.for_info(info, self)

    def build_system(self, settings: Any = None) -> SystemService:
        """Build and return a SystemService.

        ``settings`` is optional — SystemService falls back to the global
        ``conf.settings`` when None, which keeps free-standing builder use
        (CLI / tests / dev/mock_gui without an explicit Settings) working.
        """
        from ..services.system import SystemService
        return SystemService(
            platform=self._os,
            settings=settings,
        )

    # 5 thin wrappers (build_ensure_data_fn, build_download_fns,
    # build_detect_fn, build_device_svc, build_hardware_fns) deleted
    # in Phase 3 — callers now go straight to the source:
    #   ensure_data_fn         → DataManager.ensure_all
    #   download_fns           → from theme_downloader import download_pack, list_available
    #   detect_fn              → list(platform)  (or platform.detect_devices())
    #   device_svc             → builder._build_device_svc()  (still internal)
    #   memory/disk_info       → platform.get_memory_info / platform.get_disk_info

    def device_from_service(self, device_svc) -> LCDDevice:
        """Build an LCDDevice from an existing DeviceService (API standalone)."""
        from .device.lcd import LCDDevice
        build_fn = self._make_build_services_fn()
        return LCDDevice.from_service(
            device_svc,
            renderer=self._renderer,
            build_services_fn=build_fn,
        )

    # ── Internal ───────────────────────────────────────────────────

    def _make_build_services_fn(self):
        """Create a factory that wires LCD services from a DeviceService."""
        from ..adapters.infra.dc_config import DcConfig
        from ..adapters.infra.dc_parser import load_config_json
        from ..adapters.infra.dc_writer import export_theme, import_theme
        from ..adapters.infra.media_player import ThemeZtDecoder, VideoDecoder
        from ..services import DisplayService, MediaService, OverlayService, ThemeService
        from ..services.image import ImageService

        # Platform provides path resolution (web_dir, etc.)
        path_resolver = self._os

        def _build(device_svc, renderer=None):
            r = renderer or ImageService.renderer()
            overlay_svc = OverlayService(
                renderer=r,
                load_config_json_fn=load_config_json,
                dc_config_cls=DcConfig,
            )
            media_svc = MediaService(
                video_decoder_cls=VideoDecoder,
                zt_decoder_cls=ThemeZtDecoder,
            )
            theme_svc = ThemeService(
                export_theme_fn=export_theme,
                import_theme_fn=import_theme,
                load_config_json_fn=load_config_json,
                dc_config_cls=DcConfig,
            )
            try:
                import psutil as _psutil
                _proc = _psutil.Process()
                def _cpu_percent() -> float:
                    return _proc.cpu_percent(interval=0.5)
            except ImportError:
                log.warning("psutil not available — CPU baseline logging disabled")
                _cpu_percent = None  # type: ignore[assignment]
            display_svc = DisplayService(
                device_svc, overlay_svc, media_svc,
                theme_svc=theme_svc,
                cpu_percent_fn=_cpu_percent,
                path_resolver=path_resolver,
            )
            return {
                'display_svc': display_svc,
                'theme_svc': theme_svc,
                'renderer': r,
                'dc_config_cls': DcConfig,
                'load_config_json_fn': load_config_json,
            }

        return _build
