"""LCDHandler — one per LCD device (C# FormCZTV equivalent).

Self-contained handler for a single LCD device. Owns an LCDDevice,
manages theme/video/overlay/slideshow state, renders + sends frames.
TRCCApp creates one LCDHandler per connected LCD device.

All device mutations call LCDDevice methods directly.
Read-only property accesses (connected, playing, auto_send, etc.) are
direct — they carry no side-effects.
"""
# pyright: reportOptionalMemberAccess=false
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap

from trcc.conf import Settings

from ...core._logging import tagged_logger
from ...core.device.lcd import LCDDevice
from ...core.models import (
    DEFAULT_BRIGHTNESS_LEVEL,
    SPLIT_MODE_RESOLUTIONS,
    DeviceInfo,
    ThemeInfo,
)
from ...core.trcc import Trcc
from ...services.theme import theme_info_from_directory
from .base_handler import BaseHandler

log = logging.getLogger(__name__)


class _DataReadyNotifier(QObject):
    """Thread-safe notifier: emits ready() from background thread to main thread."""
    ready = Signal()


class LCDHandler(BaseHandler):
    """Handler for a single LCD device — like C# FormCZTV.

    Each LCD device gets its own handler with its own LCDDevice.
    All LCD operations route through here: themes, video, overlay,
    brightness, rotation, slideshow, screencast.
    """

    def __init__(
        self,
        lcd: LCDDevice,
        widgets: dict[str, Any],
        make_timer: Any,
        data_dir: Path,
        is_visible_fn: Any = None,
        app: Trcc | None = None,
        lcd_idx: int = 0,
    ) -> None:
        super().__init__(lcd, 'form')
        self._lcd = lcd
        self._app = app           # Trcc for unified command flow
        self._lcd_idx = lcd_idx    # Index into Trcc._lcd_devices
        self._w = widgets  # preview, theme_setting, theme_local, etc.
        self._data_dir = data_dir
        self._is_visible = is_visible_fn or (lambda: True)
        self.log: logging.Logger = log  # module-level until apply_device_config
        # UI focus state — inactive handlers must not mutate shared widgets.
        # Multi-display: multiple LCDHandler instances share one widget set;
        # only the active handler writes to preview / progress bar (PR #120,
        # jwcrowley).
        self._ui_active = False

        # Per-device state
        self._device_key = ''
        self._brightness_level = DEFAULT_BRIGHTNESS_LEVEL
        self._split_mode = 0
        self._ldd_is_split = False
        self._background_active = False
        self._slideshow_index = 0

        # QPixmap cache keyed by frame index: {index: (id(qimage), QPixmap)}
        # Avoids QImage→QPixmap conversion on every tick when L3 cache is warm.
        self._pixmap_cache: dict[int, tuple[int, QPixmap]] = {}
        # Last image identity rendered + sent — guards against redundant
        # preview updates when nothing changed (PR #120 perf).
        self._last_render_id: int | None = None

        # Thread-safe notifier for background data download → UI refresh
        self._data_notifier = _DataReadyNotifier()
        self._data_notifier.ready.connect(self._on_data_ready)

        # Timers (created by parent, owned by this handler)
        self._animation_timer: QTimer = make_timer(self._on_video_tick)
        self._slideshow_timer: QTimer = make_timer(self._on_slideshow_tick)
        self._flash_timer: QTimer = make_timer(self._on_flash_timeout, single_shot=True)

    # ── Public API ───────────────────────────────────────────────────

    @property
    def display(self) -> LCDDevice:
        return self._lcd

    @property
    def device_key(self) -> str:
        return self._device_key

    # ── LCDDevice Config (C# ReadSystemConfiguration) ─────────────────

    def apply_device_config(self, device: DeviceInfo, w: int, h: int) -> None:
        """First-time device setup + full widget refresh."""
        self.log.info("apply_device_config: device_index=%d %04x:%04x %dx%d",
                      device.device_index, device.vid, device.pid, w, h)
        self._ui_active = True
        self._device_key = Settings.device_config_key(
            device.device_index, device.vid, device.pid)
        # Per-device child logger — tags handler logs with device index
        self.log: logging.Logger = tagged_logger(
            __name__, f'lcd:{device.device_index}')
        Settings.save_device_settings(self._device_key, w=w, h=h)
        self._lcd.set_data_ready_callback(self._data_notifier.ready.emit)
        self._refresh(w, h)

    def reactivate(self, w: int, h: int) -> None:
        """Return to known device — device already configured from connect()."""
        self._ui_active = True
        self._refresh(w, h)

    def restore_inactive_state(self) -> None:
        """Restore last theme for an inactive LCD without touching shared widgets.

        Multi-display scenario (PR #120, jwcrowley): all LCDs should keep
        playing their video even when not selected in the GUI sidebar.
        This restores the device state and starts the per-device video
        timer, but skips updating the shared preview/settings widgets
        (which the active handler owns).
        """
        self._ui_active = False
        # Clear stale pixmap cache from any previous session so we start fresh.
        self._pixmap_cache.clear()
        if not self._lcd.connected:
            return
        try:
            self._lcd.restore_device_settings()
            result = self._lcd.restore_last_theme()
        except Exception:
            self.log.exception("restore_inactive_state: failed")
            return

        if not result.get("success"):
            return
        if result.get("is_animated") and self._lcd.playing:
            interval = max(1, int(self._lcd.interval or 33))
            if not self._animation_timer.isActive():
                self.log.info(
                    "restore_inactive_state: starting background video timer "
                    "interval=%dms", interval)
                self._animation_timer.start(interval)

    def _refresh(self, w: int, h: int) -> None:
        """Update widgets from the device's current state.

        LCDDevice is already configured (resolution + dirs) from connect().
        This just syncs the shared GUI widgets to show this device's data.
        """
        self.log.debug("_refresh: device_key=%s resolution=%dx%d", self._device_key, w, h)
        cfg = Settings.get_device_config(self._device_key) if self._device_key else {}

        self._w['preview'].set_resolution(w, h)
        self._w['preview'].set_image(None)
        self._w['image_cut'].set_resolution(w, h)
        self._w['video_cut'].set_resolution(w, h)
        self._w['theme_setting'].set_resolution(w, h)

        auto_loaded = self._update_theme_directories()

        self._restore_brightness(cfg)
        self._restore_rotation(cfg)
        self._restore_split_mode(cfg, w, h)
        self._restore_carousel(cfg)

        if auto_loaded:
            return
        self._restore_theme_and_preview(cfg)

    def _on_data_ready(self) -> None:
        """Background data extraction finished — re-probe dirs and update UI."""
        self.log.info("_on_data_ready: refreshing dirs and theme lists")
        self._lcd.refresh_dirs()
        auto_loaded = self._update_theme_directories()
        self.log.info("_on_data_ready: done, auto_loaded=%s", auto_loaded)

    def _restore_brightness(self, cfg: dict) -> None:
        self._brightness_level = cfg.get('brightness_level', DEFAULT_BRIGHTNESS_LEVEL)
        self.log.info("Restoring brightness: %d%%", self._brightness_level)
        if self._app is not None:
            self._app.lcd.set_brightness(self._lcd_idx, self._brightness_level)
        else:
            self._lcd.set_brightness(self._brightness_level)

    def _restore_rotation(self, cfg: dict) -> None:
        rotation_index = cfg.get('rotation', 0) // 90
        rotation = rotation_index * 90
        self.log.debug("_restore_rotation: rotation=%d", rotation)
        if self._app is not None:
            self._app.lcd.set_rotation(self._lcd_idx, rotation)
        else:
            self._lcd.set_rotation(rotation)
        self._w['rotation_combo'].blockSignals(True)
        self._w['rotation_combo'].setCurrentIndex(rotation_index)
        self._w['rotation_combo'].blockSignals(False)
        ow, oh = self._lcd.canvas_size
        self._w['preview'].set_resolution(ow, oh)
        self._update_theme_directories()

    def _restore_split_mode(self, cfg: dict, w: int, h: int) -> None:
        self._split_mode = cfg.get('split_mode', 2)
        self._ldd_is_split = (w, h) in SPLIT_MODE_RESOLUTIONS
        self.log.debug("_restore_split_mode: split_mode=%d ldd_is_split=%s", self._split_mode, self._ldd_is_split)
        if self._ldd_is_split:
            if not self._split_mode:
                self._split_mode = 2
            if self._app is not None:
                self._app.lcd.set_split_mode(self._lcd_idx, self._split_mode)
            else:
                self._lcd.set_split_mode(self._split_mode)
        else:
            if self._app is not None:
                self._app.lcd.set_split_mode(self._lcd_idx, 0)
            else:
                self._lcd.set_split_mode(0)

    def _restore_carousel(self, cfg: dict) -> None:
        carousel = cfg.get('carousel')
        local = self._w['theme_local']
        if carousel and isinstance(carousel, dict):
            local._lunbo_array = carousel.get('themes', [])
            local._slideshow = carousel.get('enabled', False)
            local._slideshow_interval = carousel.get('interval', 3)
            local.timer_input.setText(str(carousel.get('interval', 3)))
            px = local._lunbo_on if carousel.get('enabled') else local._lunbo_off
            if not px.isNull():
                local.slideshow_btn.setIcon(QIcon(px))
                local.slideshow_btn.setIconSize(local.slideshow_btn.size())
            local._apply_decorations()
            self._update_slideshow_state()
        else:
            self._slideshow_timer.stop()
            local._lunbo_array = []
            local._slideshow = False
            local._apply_decorations()

    def _restore_theme_and_preview(self, cfg: dict) -> None:
        """Restore last theme + overlay, or clear preview if none."""
        self.log.debug("_restore_theme_and_preview: cfg keys=%s", list(cfg.keys()))
        result = self._lcd.restore_last_theme()
        if not result.get("success"):
            self.log.info("_restore_theme_and_preview: no saved theme — %s",
                          result.get("error", "unknown"))
        if result.get("success"):
            image = result.get("image")
            is_animated = result.get("is_animated", False)
            if image:
                self._w['preview'].set_image(image, fast=is_animated)
            overlay_config = result.get("overlay_config")
            overlay_enabled = result.get("overlay_enabled", False)
            if overlay_config:
                self._w['theme_setting'].load_from_overlay_config(overlay_config)
            self._w['theme_setting'].set_overlay_enabled(overlay_enabled)
            if is_animated and self._lcd.playing:
                self._animation_timer.start(self._lcd.interval)
                self._w['preview'].set_playing(True)
                self._w['preview'].show_video_controls(True)
            return

        # No saved theme — show device's current image or clear preview
        image = self._lcd.current_image
        if image:
            self._w['preview'].set_image(image)
        else:
            self._w['preview'].set_image(None)
        # Restore overlay from config even without a saved theme
        overlay_cfg = cfg.get('overlay', {})
        overlay_config = overlay_cfg.get('config')
        overlay_enabled = overlay_cfg.get('enabled', False)
        if overlay_config:
            self._w['theme_setting'].load_from_overlay_config(overlay_config)
        self._w['theme_setting'].set_overlay_enabled(overlay_enabled)

    # ── Theme (C# Theme_Click_Event) ───────────────────────────────

    def _select_theme(self, theme: ThemeInfo, *, send_frame: bool = True) -> None:
        """Select theme and handle result."""
        self.log.info("Theme selected: %s (animated=%s)", theme.name, theme.is_animated)
        self._pixmap_cache.clear()
        payload = self._lcd.select(theme)
        image = payload.get('image')
        is_animated = payload.get('is_animated', False)

        if image:
            self._w['preview'].set_image(image, fast=is_animated)
            if send_frame and self._lcd.auto_send and not is_animated:
                self._lcd.send(image)

        if is_animated and self._lcd.playing:
            self._animation_timer.start(payload.get('interval', 33))
            self._w['preview'].set_playing(True)
            self._w['preview'].show_video_controls(True)

    def select_theme_from_path(self, path: Path, persist: bool = True) -> None:
        """Public entry for theme selection by path (local theme clicks)."""
        self._select_theme_from_path(path, persist=persist)

    def _select_theme_from_path(self, path: Path, persist: bool = True,
                                overlay_config: bool = True) -> None:
        """Load a local/mask theme by directory path."""
        self.log.info("_select_theme_from_path: %s persist=%s overlay_config=%s",
                 path, persist, overlay_config)
        if not path.exists():
            self.log.warning("_select_theme_from_path: path does not exist: %s", path)
            return
        self._slideshow_timer.stop()
        if self._app is not None:
            self._app.lcd.enable_overlay(self._lcd_idx, False)
        else:
            self._lcd.enable_overlay(False)

        # Reset overlay to canvas (landscape) dims — local themes pixel-rotate
        svc = self._lcd._display_svc
        if svc:
            cw, ch = svc.canvas_size
            svc.overlay.set_resolution(cw, ch)
            self.log.debug("_select_theme_from_path: overlay reset to canvas %dx%d", cw, ch)

        # Reset mode toggles (C# ReadSystemConfiguration override)
        self._background_active = False
        self._animation_timer.stop()
        self._lcd.stop()
        self._w['theme_setting'].background_panel.set_enabled(False)
        self._w['theme_setting'].screencast_panel.set_enabled(False)
        self._w['theme_setting'].video_panel.set_enabled(False)

        theme = theme_info_from_directory(path)
        # Suppress send when overlay config will follow — the overlay load
        # owns the single send, avoiding a double-send blink.
        self._select_theme(theme, send_frame=not overlay_config)
        if overlay_config:
            self._load_theme_overlay_config(path, persist=persist)

        if persist and self._device_key:
            self.log.info("Saving theme_name: %s (key=%s)", path.name, self._device_key)
            Settings.save_device_settings(
                self._device_key,
                theme_name=path.name, theme_type='local', mask_id='')
        elif persist and not self._device_key:
            self.log.warning("_select_theme_from_path: not persisting — device_key is empty")

    def select_cloud_theme(self, theme_info: Any) -> None:
        """Handle cloud theme selection (video backgrounds)."""
        self.log.info("select_cloud_theme: %s (video=%s)", theme_info.name,
                 getattr(theme_info, 'video', None))
        self._slideshow_timer.stop()
        self._background_active = False
        self._w['theme_setting'].background_panel.set_enabled(False)
        self._w['theme_setting'].screencast_panel.set_enabled(False)

        if theme_info.video:
            video_path = Path(theme_info.video)
            preview_path = video_path.parent / f"{video_path.stem}.png"
            theme = ThemeInfo.from_video(
                video_path, preview_path if preview_path.exists() else None)
            self._select_theme(theme)
            if self._device_key:
                Settings.save_device_settings(
                    self._device_key,
                    theme_name=video_path.stem, theme_type='cloud')

    def apply_mask(self, mask_info: Any) -> None:
        """Apply mask overlay on top of current content."""
        self.log.info("apply_mask: %s path=%s", mask_info.name, mask_info.path)
        if mask_info.path:
            mask_dir = Path(mask_info.path)
            # DC first — sets overlay resolution + element positions for this mask
            self._load_theme_overlay_config(mask_dir, persist=False)
            # Then mask PNG composites at the correct dims.
            # Trcc.lcd.apply_mask persists mask_id/mask_custom itself.
            is_custom = getattr(mask_info, 'is_custom', False)
            if self._app is not None:
                r = self._app.lcd.apply_mask(self._lcd_idx, mask_dir, is_custom=is_custom)
                image = r.frame.native if r.frame else None
            else:
                result = self._lcd.load_mask_standalone(str(mask_dir))
                image = result.get('image')
                if self._device_key:
                    Settings.save_device_settings(
                        self._device_key,
                        mask_id=mask_dir.name, mask_custom=is_custom)
            if image:
                self._w['preview'].set_image(image)
        else:
            self._w['preview'].set_status(f"Mask: {mask_info.name}")

    def update_mask_position(self, x: int, y: int) -> None:
        """Update mask overlay position and re-render."""
        if self._app is not None:
            self._app.lcd.set_mask_position(self._lcd_idx, x, y)
        else:
            self._lcd.set_mask_position(x, y)
        self._render_and_send()

    def save_theme(self, name: str) -> None:
        # Trcc.lcd.save_theme owns theme_name/theme_type persistence now.
        if self._app is not None:
            r = self._app.lcd.save_theme(self._lcd_idx, name)
            self._w['preview'].set_status(r.message or r.format())
            success = r.success
        else:
            result = self._lcd.save(name)
            self._w['preview'].set_status(result.get('message', ''))
            success = result.get('success', False)
        if success:
            td = self._lcd.theme_dir
            if td:
                self._w['theme_local'].set_theme_directory(td.path)
            self._w['theme_local'].load_themes()

    def export_config(self, path: Path) -> None:
        if self._app is not None:
            r = self._app.lcd.export_config(self._lcd_idx, path)
            self._w['preview'].set_status(r.message or r.format())
        else:
            result = self._lcd.export_config(str(path))
            self._w['preview'].set_status(result.get('message', ''))

    def import_config(self, path: Path) -> None:
        if self._app is not None:
            r = self._app.lcd.import_config(self._lcd_idx, path, self._data_dir)
            self._w['preview'].set_status(r.message or r.format())
            success = r.success
        else:
            result = self._lcd.import_config(str(path), str(self._data_dir))
            self._w['preview'].set_status(result.get('message', ''))
            success = result.get('success', False)
        if success:
            td = self._lcd.theme_dir
            if td:
                self._w['theme_local'].set_theme_directory(td.path)
            self._w['theme_local'].load_themes()

    # ── DC File Loading ────────────────────────────────────────────

    def _save_overlay(self, enabled: bool, config: dict) -> None:
        if self._device_key:
            Settings.save_device_setting(self._device_key, 'overlay', {
                'enabled': enabled, 'config': config,
            })

    def _load_theme_overlay_config(self, theme_dir: Path,
                                    *, persist: bool = True) -> None:
        """Load overlay config from theme's config.json or config1.dc."""
        self.log.info("_load_theme_overlay_config: dir=%s persist=%s", theme_dir, persist)
        overlay_config = self._lcd.load_overlay_config_from_dir(str(theme_dir))

        if not overlay_config:
            self.log.info("_load_theme_overlay_config: no DC found → overlay disabled")
            self._w['theme_setting'].set_overlay_enabled(False)
            if persist:
                self._save_overlay(False, {})
            self._render_and_send()
            return

        self.log.info("_load_theme_overlay_config: DC loaded, %d elements → overlay enabled",
                 len(overlay_config))
        Settings.apply_format_prefs(overlay_config)
        self._w['theme_setting'].set_overlay_enabled(True)
        self._w['theme_setting'].load_from_overlay_config(overlay_config)
        if self._app is not None:
            # Trcc.lcd.set_overlay_config persists overlay.config; then
            # enable_overlay persists overlay.enabled. No need for
            # _save_overlay below when Trcc owns persistence.
            self._app.lcd.set_overlay_config(self._lcd_idx, overlay_config)
            self._app.lcd.enable_overlay(self._lcd_idx, True)
        else:
            self._lcd.set_config(overlay_config)
            self._lcd.enable_overlay(True)
            if persist:
                self._save_overlay(True, overlay_config)
        self._render_and_send()

    # ── Video (C# ucBoFangQiKongZhi1) ─────────────────────────────

    def play_pause(self) -> None:
        self.log.debug("play_pause")
        # Video pause toggles LCDDevice.media state. Use legacy pause() (returns
        # dict with state='playing'|'paused') — pause_video on Trcc is a pure
        # stop, doesn't toggle. Phase 8 adds a toggle_video command.
        result = self._lcd.pause()
        playing = result.get('state') == 'playing'
        self._w['preview'].set_playing(playing)
        if playing:
            self._animation_timer.start(self._lcd.interval)
        else:
            self._animation_timer.stop()

    def stop_video(self) -> None:
        self.log.debug("stop_video")
        if self._app is not None:
            self._app.lcd.stop_video(self._lcd_idx)
        else:
            self._lcd.stop()
        self._animation_timer.stop()
        self._w['preview'].set_playing(False)
        self._w['preview'].show_video_controls(False)

    def seek(self, percent: float) -> None:
        if self._app is not None:
            self._app.lcd.seek_video(self._lcd_idx, percent)
        else:
            self._lcd.seek(percent)

    def set_video_fit_mode(self, mode: str) -> None:
        if self._app is not None:
            r = self._app.lcd.set_fit_mode(self._lcd_idx, mode)
            image = r.frame.native if r.frame else None
        else:
            result = self._lcd.set_fit_mode(mode)
            image = result.get('image')
        if image:
            self._w['preview'].set_image(image)

    def _on_video_tick(self) -> None:
        """Timer callback: advance one video frame."""
        result = self._lcd.video_tick()
        if not result:
            return
        frame_index = result.get('frame_index')
        if frame_index is not None and frame_index % 30 == 0:
            self.log.debug("_on_video_tick: frame=%d encoded=%s", frame_index, result.get('encoded') is not None)

        # Update progress bar (active UI only — widget is shared across handlers)
        if self._ui_active:
            progress = result.get('progress')
            if progress is not None:
                percent, current_time, total_time = progress
                self._w['preview'].set_progress(percent, current_time, total_time)

        # Preview update — active UI only, and skip when window is minimized
        if self._ui_active and self._is_visible():
            preview = result.get('preview')
            if preview is not None:
                index = result.get('frame_index')
                if index is not None:
                    cached = self._pixmap_cache.get(index)
                    preview_id = id(preview)
                    if cached is None or cached[0] != preview_id:
                        # Cap cache to bound memory in long-running videos.
                        if len(self._pixmap_cache) >= 256:
                            self._pixmap_cache.clear()
                        pixmap = QPixmap.fromImage(preview)
                        self._pixmap_cache[index] = (preview_id, pixmap)
                    else:
                        pixmap = cached[1]
                    self._w['preview'].set_image(pixmap, fast=True)
                else:
                    self._w['preview'].set_image(preview, fast=True)

        if not self._lcd.connected:
            return

        # Pre-encoded path
        encoded = result.get('encoded')
        if encoded is not None:
            w, h = self._lcd.lcd_size
            self.log.debug("_on_video_tick: sending encoded frame %s (%dx%d, %d bytes)",
                      result.get('frame_index'), w, h, len(encoded))
            self._lcd.device_service.send_rgb565_async(encoded, w, h)
            return

        # Fallback encode
        send_img = result.get('send_image')
        if send_img:
            w, h = self._lcd.lcd_size
            self.log.debug("_on_video_tick: sending raw frame %s (%dx%d)", result.get('frame_index'), w, h)
            self._lcd.send_async(send_img, w, h)

    # ── Overlay (C# ucXiTongXianShi1) ─────────────────────────────

    def on_overlay_changed(self, element_data: dict) -> None:
        """Forward overlay config change from settings panel."""
        self.log.debug("on_overlay_changed: %d elements", len(element_data) if element_data else 0)
        if not element_data:
            return
        if self._app is not None:
            if not self._lcd.enabled:
                self._app.lcd.enable_overlay(self._lcd_idx, True)
            self._app.lcd.set_overlay_config(self._lcd_idx, element_data)
        else:
            if not self._lcd.enabled:
                self._lcd.enable_overlay(True)
            self._lcd.set_config(element_data)
        if self._lcd.playing and self._lcd.last_metrics is not None:
            self.log.debug("on_overlay_changed: video playing — updating cache text overlay")
            self._lcd.update_video_cache_text(self._lcd.last_metrics)
        else:
            self._render_and_send()

        # Legacy path still needs _save_overlay; Trcc persists internally.
        if self._app is None:
            self._save_overlay(
                self._w['theme_setting'].overlay_grid.overlay_enabled,
                element_data)

    def handle_frame(self, image: Any) -> None:
        """Receive rendered frame from tick loop — update preview widget."""
        self._w['preview'].set_image(image)

    def update_preview(self, image: Any) -> None:
        """Display a frame that was already rendered and sent to the device."""
        self._w['preview'].set_image(image)

    def update_metrics(self, metrics: Any) -> None:
        """Metrics tick: video cache text update only."""
        if not self._lcd.connected or not self._lcd.playing:
            return
        self.log.debug("overlay_tick: video playing — updating cache text overlay")
        self._lcd.update_video_cache_text(metrics)

    def flash_element(self, index: int) -> None:
        """Flash/blink selected overlay element on preview."""
        self._lcd.set_flash_index(index)
        self._flash_timer.start(980)
        self._render_and_send()

    def _on_flash_timeout(self) -> None:
        self._lcd.set_flash_index(-1)
        self._render_and_send()

    # ── Display Settings ───────────────────────────────────────────

    def set_brightness(self, percent: int) -> None:
        self.log.debug("set_brightness: %d%%", percent)
        self._brightness_level = percent
        if self._app is not None:
            r = self._app.lcd.set_brightness(self._lcd_idx, percent)
            image = r.frame.native if r.frame else None
        else:
            result = self._lcd.set_brightness(percent)
            image = result.get('image')
        if image:
            self._w['preview'].set_image(image)
            if self._lcd.auto_send:
                self._lcd.send(image)

    def set_rotation(self, degrees: int) -> None:
        self.log.debug("set_rotation: degrees=%d", degrees)
        if self._app is not None:
            r = self._app.lcd.set_rotation(self._lcd_idx, degrees)
            image = r.frame.native if r.frame else None
        else:
            result = self._lcd.set_rotation(degrees)
            image = result.get('image')
        lcd = self._lcd
        ow, oh = lcd.canvas_size
        self.log.info("set_rotation: rotation=%d output=%dx%d "
                 "masks_dir=%s web_dir=%s rotated=%s",
                 lcd.rotation, ow, oh, lcd.masks_dir, lcd.web_dir, lcd.is_rotated())
        # Resolution BEFORE image — ImageLabel.set_image() scales to widget dims
        self._w['preview'].set_resolution(ow, oh)
        if image:
            self._w['preview'].set_image(image)
        self._update_theme_directories()
        self._reload_cloud_theme_for_rotation()

    def _reload_cloud_theme_for_rotation(self) -> None:
        """If a cloud video is active on a non-square device, load the
        orientation-matched version. Downloads it if not already cached."""
        lcd = self._lcd
        w, h = lcd.lcd_size
        if w == h:
            self.log.debug("_reload_cloud_theme_for_rotation: square device — skipping")
            return
        current = self._lcd.current_theme_path
        if not current or not str(current).endswith('.mp4'):
            self.log.debug("_reload_cloud_theme_for_rotation: no active cloud theme (current=%s)", current)
            return
        new_web = lcd.web_dir
        if not new_web:
            self.log.debug("_reload_cloud_theme_for_rotation: no web_dir for new orientation")
            return

        theme_id = current.stem
        rotated_mp4 = new_web / f"{theme_id}.mp4"

        if not rotated_mp4.exists():
            # Download from the orientation-matched URL
            self.log.info("_reload_cloud_theme_for_rotation: downloading %s to %s",
                     theme_id, new_web)
            self._w['theme_web']._download_cloud_theme(theme_id)
            return

        # Already exists — load it directly
        self.log.info("_reload_cloud_theme_for_rotation: loading %s", rotated_mp4)
        preview = new_web / f"{theme_id}.png"
        theme = ThemeInfo.from_video(
            rotated_mp4, preview if preview.exists() else None)
        self._select_theme(theme)

    def set_split_mode(self, mode: int) -> None:
        self.log.debug("set_split_mode: mode=%d", mode)
        self._split_mode = mode
        if self._app is not None:
            r = self._app.lcd.set_split_mode(self._lcd_idx, mode)
            image = r.frame.native if r.frame else None
        else:
            result = self._lcd.set_split_mode(mode)
            image = result.get('image')
        if image:
            self._w['preview'].set_image(image)
            if self._lcd.auto_send:
                self._lcd.send(image)

    # ── Background / Screencast Toggles ────────────────────────────

    def on_background_toggle(self, enabled: bool) -> None:
        """Handle background display toggle."""
        self.log.debug("on_background_toggle: enabled=%s", enabled)
        self._background_active = enabled
        if enabled:
            self._animation_timer.stop()
            self._lcd.stop()
            self._w['preview'].set_playing(False)
            self._w['preview'].show_video_controls(False)
        self._render_and_send()
        kind = "video" if self._lcd.has_frames else "image"
        self._w['preview'].set_status(
            f"Background: {'On' if enabled else 'Off'} ({kind})")

    def on_screencast_frame(self, image: Any) -> None:
        """Handle captured screencast frame — preview + send to LCD."""
        self._w['preview'].set_image(image)
        self._lcd.send(image)

    # ── Slideshow / Carousel ───────────────────────────────────────

    def _update_slideshow_state(self) -> None:
        self.log.debug("_update_slideshow_state")
        local = self._w['theme_local']
        enabled = local.is_slideshow()
        interval_s = local.get_slideshow_interval()
        themes = local.get_slideshow_themes()

        if enabled and themes:
            self._slideshow_index = 0
            self._slideshow_timer.start(interval_s * 1000)
        else:
            self._slideshow_timer.stop()

        # Trcc.lcd.configure_slideshow + set_slideshow own carousel persistence.
        # Legacy: fall back to direct Settings write.
        if self._app is not None:
            self._app.lcd.configure_slideshow(
                self._lcd_idx, [t.name for t in themes], interval_s)
            self._app.lcd.set_slideshow(self._lcd_idx, enabled)
        elif self._device_key:
            Settings.save_device_setting(self._device_key, 'carousel', {
                'enabled': enabled,
                'interval': interval_s,
                'themes': [t.name for t in themes],
            })

    def on_slideshow_delegate(self) -> None:
        """Handle slideshow toggle from local theme panel."""
        self._update_slideshow_state()

    def _on_slideshow_tick(self) -> None:
        """Auto-rotate to next theme in slideshow."""
        if self._lcd.playing:
            self._lcd.stop()
            self._animation_timer.stop()
        themes = self._w['theme_local'].get_slideshow_themes()
        if not themes:
            self._slideshow_timer.stop()
            return
        self._slideshow_index = (self._slideshow_index + 1) % len(themes)
        theme_info = themes[self._slideshow_index]
        path = Path(theme_info.path)
        if path.exists():
            theme = theme_info_from_directory(path)
            self._select_theme(theme, send_frame=False)
            self._load_theme_overlay_config(path)

    # ── Rendering ──────────────────────────────────────────────────

    def _render_and_send(self) -> None:
        """Render overlay + send to LCD, update preview.

        Skipped when video/screencast is active — those own the device.
        Dedups identical re-renders (PR #120 perf): if the source image
        identity hasn't changed since the last send, skip the round-trip.
        """
        self.log.debug("_render_and_send: playing=%s overlay_enabled=%s has_image=%s",
                  self._lcd.playing, self._lcd.enabled,
                  self._lcd.current_image is not None)
        if self._lcd.playing:
            return
        current = self._lcd.current_image
        render_id = id(current) if current is not None else None
        if render_id is not None and render_id == self._last_render_id:
            self.log.debug("_render_and_send: skip duplicate render id=%s", render_id)
            return
        result = self._lcd.render_and_send()
        self._last_render_id = render_id
        image = result.get('image')
        if image and self._ui_active:
            self._w['preview'].set_image(image)

    def render_and_preview(self) -> Any:
        """Render overlay and update preview (no send)."""
        result = self._lcd.render()
        image = result.get('image')
        if image:
            self._w['preview'].set_image(image)
        return image

    # ── Helpers ─────────────────────────────────────────────────────

    def _update_theme_directories(self) -> bool:
        """Reload theme browser directories for current resolution.

        Returns True if a first-install auto-load happened (caller should
        skip restore_last_theme to avoid a redundant double-load).
        """
        lcd = self._lcd
        ow, oh = lcd.canvas_size
        self.log.debug("_update_theme_directories: output=%dx%d theme_dir=%s "
                  "web_dir=%s masks_dir=%s rotated=%s",
                  ow, oh,
                  lcd.theme_dir.path if lcd.theme_dir else None,
                  lcd.web_dir, lcd.masks_dir, lcd.is_rotated())
        td = lcd.theme_dir
        if td and td.path.exists():
            self._w['theme_local'].set_theme_directory(td.path)
        if lcd.web_dir:
            self._w['theme_web'].set_web_directory(lcd.web_dir)
        self._w['theme_web'].set_resolution(f'{ow}x{oh}')
        if lcd.masks_dir:
            self._w['theme_mask'].set_mask_directory(lcd.masks_dir)
        self._w['theme_mask'].set_resolution(f'{ow}x{oh}')
        self._w['image_cut'].set_resolution(ow, oh)
        self._w['video_cut'].set_resolution(ow, oh)

        # First install: themes just extracted — load first one onto LCD + preview
        if self._lcd.current_image is None and td and td.path.exists():
            saved_cfg = Settings.get_device_config(self._device_key) if self._device_key else {}
            if not saved_cfg.get('theme_name') and not saved_cfg.get('theme_path'):
                for item in sorted(td.path.iterdir()):
                    if item.is_dir() and (item / '00.png').exists():
                        self.log.info("Data ready: auto-loading first theme: %s", item)
                        self._select_theme_from_path(item, persist=True, overlay_config=True)
                        return True
                self.log.debug("_update_theme_directories: no valid theme found for auto-load in %s", td.path)
        return False

    @property
    def is_background_active(self) -> bool:
        return self._background_active

    @is_background_active.setter
    def is_background_active(self, value: bool) -> None:
        self._background_active = value

    @property
    def brightness_level(self) -> int:
        return self._brightness_level

    @property
    def split_mode(self) -> int:
        return self._split_mode

    @property
    def ldd_is_split(self) -> bool:
        return self._ldd_is_split

    # ── Lifecycle ──────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Stop timers and release device resources."""
        self.deactivate()
        self._pixmap_cache.clear()
        self._last_render_id = None
        self._cleanup_device()

    def deactivate(self) -> None:
        """Full pause — stop all timers (called from cleanup)."""
        self._animation_timer.stop()
        self._slideshow_timer.stop()
        self._flash_timer.stop()

    def set_inactive(self) -> None:
        """Soft pause for sidebar switch — keep video playing in background.

        Multi-display: dropping `_ui_active` stops shared-widget writes
        without killing the per-device animation timer, so the LCD keeps
        showing its theme while another device owns the GUI panel.
        """
        self._ui_active = False
        self._slideshow_timer.stop()
        self._flash_timer.stop()

    def _cleanup_device(self) -> None:
        """Release LCD resources — stop playback, send black, disconnect."""
        self._lcd.stop()
        try:
            self._lcd.device_service.stop_send_worker()
            self._lcd.send_color(0, 0, 0)
        except (OSError, RuntimeError) as e:
            # USB I/O during teardown — best-effort black-frame, then move on.
            self.log.debug("LCD teardown black-frame send failed: %s", e)
        self._lcd.cleanup()
