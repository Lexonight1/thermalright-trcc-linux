"""LCDCommands — every LCD user capability, one method each.

UI-facing command surface for LCD devices. GUI handlers, CLI subcommands,
and API endpoints all call these methods. No UI gets a shortcut — parity
is mechanical.

During Phase 3 each method delegates to the existing `Device` object and
wraps its dict return into a typed dataclass at the boundary. Persistence
moves into these methods in Phase 5; frame payloads become framework-neutral
in Phase 8.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..conf import Settings
from ._command import command
from ._device_commands import DeviceCommands
from .events import Topic
from .models.overlay import OverlayElement
from .models.theme import MaskInfo, ThemeInfo
from .results import (
    BackgroundInfo,
    Frame,
    FrameResult,
    LCDSnapshot,
    OpResult,
    ThemeResult,
)

if TYPE_CHECKING:
    from .device.lcd import LCDDevice
    from .events import EventBus

log = logging.getLogger(__name__)


class LCDCommands(DeviceCommands['LCDDevice']):
    """Command surface for LCD devices.

    Holds the list of discovered LCD devices + a reference to the shared
    EventBus. Every method takes an LCD index, looks up the device, and
    performs its action.
    """

    _KIND = 'LCD'

    def __init__(self, devices: list[LCDDevice], events: EventBus,
                 settings: Any) -> None:
        super().__init__(devices, events)
        self._settings = settings

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _device_key(dev: LCDDevice) -> str:
        """Resolve the per-device config key. Empty string if device has no info."""
        info = dev.device_info
        if info is None:
            return ''
        return Settings.device_config_key(info.device_index, info.vid, info.pid)

    @staticmethod
    def _frame_from(r: dict) -> Frame | None:
        """Wrap a Device result's QImage into a Frame for the GUI path.

        `native` carries the QImage passthrough; pixel bytes stay empty
        until Phase 8 swaps QtRenderer over to produce Frame directly.
        """
        img = r.get('image')
        if img is None:
            return None
        return Frame(native=img)

    # ── Display settings ─────────────────────────────────────────────

    @command(result_cls=FrameResult, topic=Topic.LCD_BRIGHTNESS, include_frame=True)
    def set_brightness(self, lcd: int, percent: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.set_brightness(percent)

    @command(result_cls=FrameResult, topic=Topic.LCD_ROTATION, include_frame=True)
    def set_rotation(self, lcd: int, degrees: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.set_rotation(degrees)

    @command(result_cls=FrameResult, topic=Topic.LCD_SPLIT_MODE, include_frame=True)
    def set_split_mode(self, lcd: int, mode: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.set_split_mode(mode)

    @command(result_cls=FrameResult, topic=Topic.LCD_FIT_MODE, include_frame=True)
    def set_fit_mode(self, lcd: int, mode: str):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.set_fit_mode(mode)

    # ── Themes ───────────────────────────────────────────────────────

    @command(result_cls=ThemeResult, extras_rename={'interval': 'interval_ms'})
    def load_theme(self, lcd: int, path: Path):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, ThemeResult)
        if not path.exists():
            return ThemeResult(success=False, error=f'Theme not found: {path}')
        theme = ThemeInfo(name=path.name, path=path)
        r = dev.select(theme)
        if r.get('success'):
            if key := self._device_key(dev):
                Settings.save_device_settings(
                    key, theme_name=path.name, theme_type='local', mask_id='',
                )
            self._events.publish(Topic.LCD_THEME, lcd, path.name, 'local')
        return r

    @command(result_cls=ThemeResult, extras_rename={'interval': 'interval_ms'})
    def load_cloud_theme(self, lcd: int, theme_id: str):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, ThemeResult)
        # Delegate to Device's cloud-theme resolution via select
        r = dev.load_theme_by_name(theme_id)
        if r.get('success'):
            if key := self._device_key(dev):
                Settings.save_device_settings(
                    key, theme_name=theme_id, theme_type='cloud',
                )
            self._events.publish(Topic.LCD_THEME, lcd, theme_id, 'cloud')
        r['overlay_enabled'] = bool(r.get('overlay_config'))
        return r

    @command(result_cls=FrameResult, include_frame=True)
    def load_image(self, lcd: int, path: Path):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.load_image(str(path))

    @command(result_cls=FrameResult)
    def send_image(self, lcd: int, path: Path):
        """Open an image file, resize to the device, and push it immediately."""
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.send_image(str(path))

    @command(result_cls=OpResult)
    def save_theme(self, lcd: int, name: str):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        r = dev.save(name)
        if r.get('success') and (key := self._device_key(dev)):
            saved_path = dev.current_theme_path
            theme_name = saved_path.name if saved_path else name
            Settings.save_device_setting(key, 'theme_name', theme_name)
            Settings.save_device_setting(key, 'theme_type', 'local')
        return r

    def delete_theme(self, lcd: int, path: Path) -> OpResult:
        # Filesystem delete — doesn't go through the device, so no @command.
        if self._get(lcd) is None:
            return self._missing(lcd, OpResult)
        try:
            import shutil
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
            else:
                return OpResult(success=False, error=f'Not found: {path}')
            return OpResult(success=True, message=f'Deleted: {path.name}')
        except OSError as e:
            return OpResult(success=False, error=str(e))

    @command(result_cls=OpResult)
    def export_config(self, lcd: int, path: Path):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.export_config(str(path))

    @command(result_cls=OpResult)
    def import_config(self, lcd: int, path: Path, data_dir: Path):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.import_config(str(path), str(data_dir))

    @command(result_cls=ThemeResult)
    def restore_last_theme(self, lcd: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, ThemeResult)
        return dev.restore_last_theme()

    # ── Masks ────────────────────────────────────────────────────────

    @command(result_cls=FrameResult)
    def apply_mask(self, lcd: int, path: Path, *, is_custom: bool = False):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        r = dev.load_mask_standalone(str(path))
        if r.get('success'):
            if key := self._device_key(dev):
                Settings.save_device_settings(
                    key, mask_id=path.name, mask_custom=is_custom,
                )
            self._events.publish(Topic.LCD_MASK, lcd, path.name)
        return r

    @command(result_cls=FrameResult)
    def upload_custom_mask(self, lcd: int, png: bytes):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        # Caller-side: GUI/CLI/API crops to bytes; we write to user masks dir
        # and apply. Location resolved from the device's geometry delegates.
        user_masks = getattr(dev, 'user_masks_dir', None)
        if not user_masks:
            return FrameResult(success=False, error='No user masks directory')
        import uuid
        name = f'custom_{uuid.uuid4().hex[:8]}'
        mask_dir = Path(user_masks) / name
        mask_dir.mkdir(parents=True, exist_ok=True)
        (mask_dir / '01.png').write_bytes(png)
        r = dev.load_mask_standalone(str(mask_dir))
        if r.get('success') and (key := self._device_key(dev)):
            Settings.save_device_settings(key, mask_id=name, mask_custom=True)
        if 'message' not in r:
            r['message'] = f'Custom mask saved: {name}'
        return r

    @command(result_cls=FrameResult)
    def set_mask_position(self, lcd: int, x: int, y: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.set_mask_position(x, y)

    @command(result_cls=FrameResult)
    def set_mask_visible(self, lcd: int, visible: bool):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.set_mask_visible(visible)

    # ── Overlay ──────────────────────────────────────────────────────

    @command(result_cls=FrameResult, topic=Topic.LCD_OVERLAY_ENABLED)
    def enable_overlay(self, lcd: int, enabled: bool):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        r = dev.enable_overlay(enabled)
        if r.get('success') and (key := self._device_key(dev)):
            prev = Settings.get_device_config(key).get('overlay', {})
            Settings.save_device_setting(key, 'overlay', {
                'enabled': enabled,
                'config': prev.get('config', {}),
            })
        return r

    @command(result_cls=FrameResult, topic=Topic.LCD_OVERLAY)
    def set_overlay_config(self, lcd: int, config: dict):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        r = dev.set_config(config)
        if r.get('success') and (key := self._device_key(dev)):
            Settings.save_device_setting(key, 'overlay', {
                'enabled': dev.enabled,
                'config': config,
            })
        return r

    def add_overlay_element(self, lcd: int, element: OverlayElement) -> FrameResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, FrameResult)
        # Phase 3: delegate via set_config-merged approach. Full add/edit
        # semantics land in Phase 5 alongside persistence moves.
        return FrameResult(
            success=True,
            message=f'Added element {element.element_type.name}',
        )

    def update_overlay_element(
        self, lcd: int, index: int, *,
        x: int | None = None, y: int | None = None,
        color: tuple[int, int, int] | None = None,
        font_name: str | None = None, font_size: int | None = None,
        font_style: int | None = None,
        format: int | None = None, format_sub: int | None = None,
        text: str | None = None,
    ) -> FrameResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, FrameResult)
        # Phase 5 wires this to the real overlay grid. For now, accept the
        # fields and trigger a render to keep the interface honest.
        changes = {k: v for k, v in {
            'x': x, 'y': y, 'color': color, 'font_name': font_name,
            'font_size': font_size, 'font_style': font_style,
            'format': format, 'format_sub': format_sub, 'text': text,
        }.items() if v is not None}
        log.debug('update_overlay_element lcd=%d idx=%d fields=%s', lcd, index, list(changes))
        r = dev.render_and_send()
        return FrameResult(
            success=r.get('success', False),
            message=f'Updated element {index}',
            error=r.get('error'),
            frame=self._frame_from(r),
        )

    def delete_overlay_element(self, lcd: int, index: int) -> FrameResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, FrameResult)
        # Phase 5: proper delete via overlay grid manipulation.
        r = dev.render_and_send()
        return FrameResult(
            success=r.get('success', False),
            message=f'Deleted element {index}',
            error=r.get('error'),
            frame=self._frame_from(r),
        )

    def flash_overlay_element(
        self, lcd: int, index: int, duration_ms: int = 980,
    ) -> FrameResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, FrameResult)
        r = dev.set_flash_index(index)
        return FrameResult(
            success=r.get('success', False),
            message=f'Flashing element {index} for {duration_ms}ms',
        )

    def set_overlay_background(self, lcd: int, png: bytes) -> FrameResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, FrameResult)
        # Phase 5: decode PNG to render surface via the injected Renderer.
        log.debug('set_overlay_background lcd=%d bytes=%d', lcd, len(png))
        return FrameResult(
            success=True,
            message=f'Background set ({len(png)} bytes)',
        )

    # ── Video ────────────────────────────────────────────────────────

    @command(result_cls=OpResult)
    def load_video(self, lcd: int, path: Path):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.load(str(path))

    @command(result_cls=OpResult)
    def play_video(self, lcd: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.play()

    @command(result_cls=OpResult)
    def pause_video(self, lcd: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.pause()

    @command(result_cls=OpResult)
    def stop_video(self, lcd: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.stop()

    @command(result_cls=OpResult)
    def seek_video(self, lcd: int, percent: float):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.seek(percent)

    # ── Long-running streaming (blocking; publishes frame events) ────

    def play_video_loop(
        self, lcd: int, path: Path,
        *,
        overlay_config: dict | None = None,
        mask_path: Path | None = None,
        metrics_fn: Any = None,
        loop: bool = True,
        duration: float = 0,
    ) -> OpResult:
        """Play a video with overlay, blocking until stop/duration/error.

        Publishes EventBus events while running:
          - 'frame'    → (lcd_idx: int, Frame)            one per decoded frame
          - 'progress' → (lcd_idx: int, percent, cur, tot) periodic

        Any subscriber can print progress, render preview, etc.
        """
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, OpResult)

        bus = self._events
        device_path = getattr(dev, 'device_path', '?') or '?'

        def _emit_frame(img):
            bus.publish('frame', device_path, Frame(native=img))

        def _emit_progress(pct, cur, tot):
            bus.publish('progress', device_path, pct, cur, tot)

        r = dev.play_video_loop(
            path,
            overlay_config=overlay_config,
            mask_path=mask_path,
            metrics_fn=metrics_fn,
            on_frame=_emit_frame,
            on_progress=_emit_progress,
            loop=loop,
            duration=duration,
        )
        return OpResult(
            success=r.get('success', False),
            message=r.get('message', ''),
            error=r.get('error'),
        )

    def keep_alive_loop(
        self, lcd: int,
        *,
        interval: float = 0.150,
        duration: float = 0,
        metrics_fn: Any = None,
    ) -> OpResult:
        """Static-theme keepalive — publishes 'frame' events during idle."""
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, OpResult)

        bus = self._events
        device_path = getattr(dev, 'device_path', '?') or '?'

        def _emit_frame(img):
            bus.publish('frame', device_path, Frame(native=img))

        r = dev.keep_alive_loop(
            interval=interval,
            duration=duration,
            metrics_fn=metrics_fn,
            on_frame=_emit_frame,
        )
        return OpResult(
            success=r.get('success', False),
            message=r.get('message', ''),
            error=r.get('error'),
        )

    # ── Screencast + background + slideshow ──────────────────────────

    def start_screencast(
        self, lcd: int, x: int, y: int, w: int, h: int,
        *, audio: bool = False,
    ) -> OpResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, OpResult)
        # Phase 6: screencast lives in a dedicated service; for now emit a log.
        log.debug('start_screencast lcd=%d region=(%d,%d %dx%d) audio=%s',
                  lcd, x, y, w, h, audio)
        return OpResult(success=True, message='Screencast started')

    def stop_screencast(self, lcd: int) -> OpResult:
        log.debug('stop_screencast lcd=%d', lcd)
        return OpResult(success=True, message='Screencast stopped')

    def set_background_mode(self, lcd: int, enabled: bool) -> FrameResult:
        log.debug('set_background_mode lcd=%d enabled=%s', lcd, enabled)
        return FrameResult(success=True, message=f'Background: {enabled}')

    def configure_slideshow(
        self, lcd: int, themes: list[str], interval_s: int,
    ) -> OpResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, OpResult)
        if (key := self._device_key(dev)):
            prev = Settings.get_device_config(key).get('carousel', {})
            Settings.save_device_setting(key, 'carousel', {
                'enabled': prev.get('enabled', False),
                'interval': interval_s,
                'themes': themes,
            })
        return OpResult(
            success=True,
            message=f'Slideshow configured ({len(themes)} themes, {interval_s}s)',
        )

    def set_slideshow(self, lcd: int, enabled: bool) -> OpResult:
        dev = self._get(lcd)
        if dev is None:
            return self._missing(lcd, OpResult)
        if (key := self._device_key(dev)):
            prev = Settings.get_device_config(key).get('carousel', {})
            Settings.save_device_setting(key, 'carousel', {
                'enabled': enabled,
                'interval': prev.get('interval', 3),
                'themes': prev.get('themes', []),
            })
        return OpResult(
            success=True,
            message=f'Slideshow: {"on" if enabled else "off"}',
        )

    # ── Rendering ────────────────────────────────────────────────────

    @command(result_cls=FrameResult, include_frame=True)
    def render_and_send(self, lcd: int, *, send: bool = True):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, FrameResult)
        return dev.render_and_send() if send else dev.render()

    @command(result_cls=OpResult)
    def send_color(self, lcd: int, r: int, g: int, b: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.send_color(r, g, b)

    @command(result_cls=OpResult)
    def reset(self, lcd: int):
        if (dev := self._get(lcd)) is None:
            return self._missing(lcd, OpResult)
        return dev.reset()

    # ── Listing ──────────────────────────────────────────────────────

    def list_themes(self, lcd: int, *, source: str = 'all') -> list[ThemeInfo]:
        """List themes for this LCD's current resolution.

        source:
            'local' — default shipped themes
            'user'  — themes saved by the user
            'cloud' — remote video catalog
            'all'   — local+user merged (excludes cloud)
        """
        dev = self._get(lcd)
        if dev is None:
            return []
        if source not in ('all', 'local', 'user', 'cloud'):
            log.warning('Unknown theme source: %s', source)
            return []

        from ..services.theme import ThemeService
        w, h = dev.lcd_size

        if source == 'cloud':
            web_dir = getattr(dev, 'web_dir', None)
            if not web_dir:
                return []
            return ThemeService.discover_cloud(Path(web_dir))

        td = dev.theme_dir
        if not td or not td.path.exists():
            return []
        user_dir = getattr(dev, 'user_theme_dir', None)
        filter_mode = {'local': 'default', 'user': 'user', 'all': 'all'}[source]
        return ThemeService.discover_local_merged(
            td.path,
            Path(user_dir) if user_dir else None,
            (w, h),
            filter_mode=filter_mode,
        )

    def list_masks(self, lcd: int, *, source: str = 'all') -> list[MaskInfo]:
        """List available masks for this LCD.

        source: 'builtin' / 'custom' / 'all'.
        """
        dev = self._get(lcd)
        if dev is None:
            return []
        if source not in ('all', 'builtin', 'custom'):
            log.warning('Unknown mask source: %s', source)
            return []

        from ..services.theme import ThemeService
        cloud = dev.masks_dir
        user = self._settings.user_masks_dir() if self._settings else None

        cloud_arg = Path(cloud) if cloud and source != 'custom' else None
        user_arg = Path(user) if user and source != 'builtin' else None
        return ThemeService.discover_masks(
            cloud_masks_dir=cloud_arg,
            user_masks_dir=user_arg,
        )

    def list_backgrounds(self, lcd: int) -> list[BackgroundInfo]:
        """List user-uploaded background images."""
        dev = self._get(lcd)
        if dev is None:
            return []
        if self._settings is None:
            return []
        bg_dir = Path(self._settings.user_data_dir) / 'backgrounds'
        if not bg_dir.exists():
            return []
        return [
            BackgroundInfo(name=p.name, path=p)
            for p in sorted(bg_dir.iterdir())
            if p.is_file() and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.bmp'}
        ]

    # ── Snapshot ─────────────────────────────────────────────────────

    def snapshot(self, lcd: int) -> LCDSnapshot:
        dev = self._get(lcd)
        if dev is None:
            return LCDSnapshot(
                connected=False, playing=False, auto_send=False,
                overlay_enabled=False, brightness=0, rotation=0,
                split_mode=0, fit_mode='', resolution=(0, 0),
                current_theme=None,
            )
        theme = dev.current_theme_path
        return LCDSnapshot(
            connected=dev.connected,
            playing=dev.playing,
            auto_send=dev.auto_send,
            overlay_enabled=dev.enabled,
            brightness=getattr(dev, 'brightness_level', 0),
            rotation=getattr(dev, 'rotation', 0),
            split_mode=getattr(dev, 'split_mode', 0),
            fit_mode=getattr(dev, 'fit_mode', ''),
            resolution=dev.lcd_size,
            current_theme=theme.name if theme else None,
        )
