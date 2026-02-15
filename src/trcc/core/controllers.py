"""
TRCC Controllers — Thin driving adapters for PyQt6 GUI.

Controllers are waiters: take requests from the view, call the service
layer, and fire callbacks to update the GUI. No business logic lives here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..services import (
    DeviceService,
    MediaService,
    OverlayService,
    ThemeService,
)
from ..services.display import DisplayService
from .models import (
    DeviceInfo,
    PlaybackState,
    ThemeInfo,
    ThemeType,
    VideoState,
)

log = logging.getLogger(__name__)


class ThemeController:
    """Thin waiter for theme discovery and selection.

    Delegates all logic to ThemeService, fires callbacks to GUI.
    """

    CATEGORIES = ThemeService.CATEGORIES

    def __init__(self, svc: ThemeService | None = None):
        self._svc = svc or ThemeService()

        # View callbacks
        self.on_themes_loaded: Optional[Callable[[List[ThemeInfo]], None]] = None
        self.on_theme_selected: Optional[Callable[[ThemeInfo], None]] = None
        self.on_filter_changed: Optional[Callable[[str], None]] = None

    @property
    def svc(self) -> ThemeService:
        return self._svc

    def set_directories(self,
                        local_dir: Optional[Path] = None,
                        web_dir: Optional[Path] = None,
                        masks_dir: Optional[Path] = None):
        self._svc.set_directories(local_dir, web_dir, masks_dir)

    def load_local_themes(self, resolution: Tuple[int, int] = (320, 320)):
        themes = self._svc.load_local_themes(resolution)
        if self.on_themes_loaded:
            self.on_themes_loaded(themes)

    def load_cloud_themes(self):
        themes = self._svc.load_cloud_themes()
        if self.on_themes_loaded:
            self.on_themes_loaded(themes)

    def set_filter(self, mode: str):
        self._svc.set_filter(mode)
        if self.on_filter_changed:
            self.on_filter_changed(mode)

    def set_category(self, category: str):
        self._svc.set_category(category)

    def select_theme(self, theme: ThemeInfo):
        self._svc.select(theme)
        if self.on_theme_selected and theme:
            self.on_theme_selected(theme)

    def get_themes(self) -> List[ThemeInfo]:
        return self._svc.themes

    def get_selected(self) -> Optional[ThemeInfo]:
        return self._svc.selected


class DeviceController:
    """Thin waiter for device detection and selection.

    Delegates to DeviceService, fires callbacks to GUI.
    """

    def __init__(self, svc: DeviceService | None = None):
        self._svc = svc or DeviceService()

        # View callbacks
        self.on_devices_changed: Optional[Callable[[List[DeviceInfo]], None]] = None
        self.on_device_selected: Optional[Callable[[DeviceInfo], None]] = None
        self.on_send_started: Optional[Callable[[], None]] = None
        self.on_send_complete: Optional[Callable[[bool], None]] = None

    @property
    def svc(self) -> DeviceService:
        return self._svc

    def detect_devices(self):
        self._svc.detect()
        if self.on_devices_changed:
            self.on_devices_changed(self._svc.devices)
        if self._svc.selected and self.on_device_selected:
            self.on_device_selected(self._svc.selected)

    def select_device(self, device: DeviceInfo):
        self._svc.select(device)
        if self.on_device_selected:
            self.on_device_selected(device)

    def get_devices(self) -> List[DeviceInfo]:
        return self._svc.devices

    def get_selected(self) -> Optional[DeviceInfo]:
        return self._svc.selected

    def send_image_async(self, rgb565_data: bytes, width: int, height: int):
        if self._svc.is_busy:
            log.debug("send_image_async: busy, skipping")
            return
        log.debug("send_image_async: dispatching %d bytes (%dx%d)",
                  len(rgb565_data), width, height)
        if self.on_send_started:
            self.on_send_started()
        self._svc.send_rgb565_async(rgb565_data, width, height)

    def send_pil_async(self, image: Any, width: int, height: int,
                       byte_order: str = '>'):
        if self._svc.is_busy:
            return
        if self.on_send_started:
            self.on_send_started()
        self._svc.send_pil_async(image, width, height)

    def get_protocol_info(self):
        return self._svc.get_protocol_info()


class VideoController:
    """Thin waiter for video playback.

    Delegates to MediaService, fires callbacks to GUI.
    """

    def __init__(self, svc: MediaService | None = None):
        self._svc = svc or MediaService()

        # View callbacks
        self.on_video_loaded: Optional[Callable[[VideoState], None]] = None
        self.on_frame_ready: Optional[Callable[[Any], None]] = None
        self.on_progress_update: Optional[Callable[[float, str, str], None]] = None
        self.on_state_changed: Optional[Callable[[PlaybackState], None]] = None
        self.on_send_frame: Optional[Callable[[Any], None]] = None

    @property
    def svc(self) -> MediaService:
        return self._svc

    def set_target_size(self, width: int, height: int):
        self._svc.set_target_size(width, height)

    def load(self, path: Path) -> bool:
        success = self._svc.load(path)
        if success and self.on_video_loaded:
            self.on_video_loaded(self._svc.state)
        return success

    def play(self):
        self._svc.play()

    def pause(self):
        self._svc.pause()

    def stop(self):
        self._svc.stop()

    def toggle_play_pause(self):
        self._svc.toggle()

    def seek(self, percent: float):
        self._svc.seek(percent)

    def tick(self) -> Optional[Any]:
        frame, should_send, progress = self._svc.tick()
        if not frame:
            return None
        if self.on_frame_ready:
            self.on_frame_ready(frame)
        if progress and self.on_progress_update:
            self.on_progress_update(*progress)
        if should_send and self.on_send_frame:
            self.on_send_frame(frame)
        return frame

    def get_frame_interval(self) -> int:
        return self._svc.frame_interval_ms

    def is_playing(self) -> bool:
        return self._svc.is_playing

    def has_frames(self) -> bool:
        return self._svc.has_frames

    def get_frame(self, index: Optional[int] = None) -> Optional[Any]:
        return self._svc.get_frame(index)

    @property
    def source_path(self) -> Optional[Path]:
        return self._svc.source_path


class OverlayController:
    """Thin waiter for overlay rendering.

    Delegates to OverlayService, fires callbacks to GUI.
    """

    def __init__(self, svc: OverlayService | None = None):
        self._svc = svc or OverlayService()

        # View callbacks
        self.on_config_changed: Optional[Callable[[], None]] = None

    @property
    def svc(self) -> OverlayService:
        return self._svc

    def set_target_size(self, width: int, height: int):
        self._svc.set_resolution(width, height)

    def enable(self, enabled: bool = True):
        self._svc.enabled = enabled

    def is_enabled(self) -> bool:
        return self._svc.enabled

    def set_background(self, image: Any):
        self._svc.set_background(image)

    @property
    def background(self) -> Any:
        return self._svc.background

    def update_metrics(self, metrics: Dict[str, Any]):
        self._svc.update_metrics(metrics)

    def render(self, background: Optional[Any] = None, *, force: bool = False) -> Any:
        return self._svc.render(background, force=force)

    def set_theme_mask(self, mask_image, position=None):
        self._svc.set_mask(mask_image, position)

    def get_theme_mask(self):
        return self._svc.get_mask()

    def set_mask_visible(self, visible: bool):
        self._svc.set_mask_visible(visible)

    def set_temp_unit(self, unit: int):
        self._svc.set_temp_unit(unit)

    def set_config(self, config: dict):
        self._svc.configure(config)
        if self.on_config_changed:
            self.on_config_changed()

    def set_config_resolution(self, width: int, height: int):
        self._svc.set_config_resolution(width, height)

    def set_scale_enabled(self, enabled: bool):
        self._svc.set_scale_enabled(enabled)

    def load_from_dc(self, dc_path: Path) -> dict:
        return self._svc.load_from_dc(dc_path)

    def set_dc_data(self, data):
        self._svc.set_dc_data(data)

    def get_dc_data(self):
        return self._svc.get_dc_data()

    @property
    def config(self) -> dict:
        return self._svc.config


class LCDDeviceController:
    """Main LCD controller — thin waiter coordinating sub-controllers.

    Delegates all business logic to DisplayService.
    """

    def __init__(self):
        # Create shared services
        device_svc = DeviceService()
        overlay_svc = OverlayService()
        media_svc = MediaService()

        # The head chef
        self._display = DisplayService(device_svc, overlay_svc, media_svc)

        # Sub-controllers (thin waiters over the same services)
        self.themes = ThemeController()
        self.devices = DeviceController(device_svc)
        self.video = VideoController(media_svc)
        self.overlay = OverlayController(overlay_svc)

        # View callbacks
        self.on_preview_update: Optional[Callable[[Any], None]] = None
        self.on_status_update: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_resolution_changed: Optional[Callable[[int, int], None]] = None

        self._setup_callbacks()

    @property
    def lcd_svc(self) -> DisplayService:
        return self._display

    @property
    def working_dir(self) -> Path:
        return self._display.working_dir

    @property
    def lcd_width(self) -> int:
        return self._display.lcd_width

    @property
    def lcd_height(self) -> int:
        return self._display.lcd_height

    @property
    def current_image(self) -> Any:
        return self._display.current_image

    @current_image.setter
    def current_image(self, value: Any):
        self._display.current_image = value

    @property
    def current_theme_path(self) -> Optional[Path]:
        return self._display.current_theme_path

    @current_theme_path.setter
    def current_theme_path(self, value: Optional[Path]):
        self._display.current_theme_path = value

    @property
    def auto_send(self) -> bool:
        return self._display.auto_send

    @auto_send.setter
    def auto_send(self, value: bool):
        self._display.auto_send = value

    @property
    def rotation(self) -> int:
        return self._display.rotation

    @rotation.setter
    def rotation(self, value: int):
        self._display.rotation = value

    @property
    def brightness(self) -> int:
        return self._display.brightness

    @brightness.setter
    def brightness(self, value: int):
        self._display.brightness = value

    def _setup_callbacks(self):
        self.themes.on_theme_selected = self._on_theme_selected
        self.video.on_frame_ready = self._on_video_frame
        self.devices.on_device_selected = self._on_device_selected

    # ── Initialization ────────────────────────────────────────────────

    def initialize(self, data_dir: Path):
        log.debug("Initializing controller, data_dir=%s", data_dir)
        self._display.initialize(data_dir)

        # Set up theme directories from service
        self.themes.set_directories(
            local_dir=self._display.local_dir,
            web_dir=self._display.web_dir,
            masks_dir=self._display.masks_dir,
        )

        self.video.set_target_size(self.lcd_width, self.lcd_height)
        self.overlay.set_target_size(self.lcd_width, self.lcd_height)

        if self.lcd_width and self.lcd_height:
            self.themes.load_local_themes((self.lcd_width, self.lcd_height))

        self.devices.detect_devices()

    def cleanup(self):
        self._display.cleanup()

    # ── Resolution ────────────────────────────────────────────────────

    def set_resolution(self, width: int, height: int, persist: bool = True):
        if width == self.lcd_width and height == self.lcd_height:
            return
        self._display.set_resolution(width, height, persist=persist)

        # Re-sync theme dirs after resolution change
        self.themes.set_directories(
            local_dir=self._display.local_dir,
            web_dir=self._display.web_dir,
            masks_dir=self._display.masks_dir,
        )

        self.video.set_target_size(width, height)
        self.overlay.set_target_size(width, height)

        if width and height:
            self.themes.load_local_themes((width, height))

        if self.on_resolution_changed:
            self.on_resolution_changed(width, height)

    def set_rotation(self, degrees: int):
        image = self._display.set_rotation(degrees)
        if image:
            self._fire_preview(image)
            if self.auto_send:
                self._send_frame_to_lcd(image)

    def set_brightness(self, percent: int):
        image = self._display.set_brightness(percent)
        if image:
            self._fire_preview(image)
            if self.auto_send:
                self._send_frame_to_lcd(image)

    # ── Theme Operations ──────────────────────────────────────────────

    def load_local_theme(self, theme: ThemeInfo):
        result = self._display.load_local_theme(theme)
        image = result.get('image')
        if image:
            self._fire_preview(image)
            if self.auto_send and not result.get('is_animated'):
                self._send_frame_to_lcd(image)
        # Sync VideoController state so GUI starts animation timer
        if result.get('is_animated') and self._display.is_video_playing():
            if self.video.on_state_changed:
                self.video.on_state_changed(PlaybackState.PLAYING)
        self._fire_status(result.get('status', ''))

    def load_cloud_theme(self, theme: ThemeInfo):
        result = self._display.load_cloud_theme(theme)
        image = result.get('image')
        if image:
            self._fire_preview(image)
            if self.auto_send:
                self._send_frame_to_lcd(image)
        # Sync VideoController state so GUI starts animation timer
        if result.get('is_animated') and self._display.is_video_playing():
            if self.video.on_state_changed:
                self.video.on_state_changed(PlaybackState.PLAYING)
        self._fire_status(result.get('status', ''))

    def apply_mask(self, mask_dir: Path):
        image = self._display.apply_mask(mask_dir)
        if image:
            self._fire_preview(image)
            if self.auto_send and not self._display.is_video_playing():
                self._send_frame_to_lcd(image)
        self._fire_status(f"Mask: {mask_dir.name}")

    def load_image_file(self, path: Path):
        image = self._display.load_image_file(path)
        if image:
            self._fire_preview(image)
            if self.auto_send:
                self._send_frame_to_lcd(image)

    def save_theme(self, name: str, data_dir: Path) -> Tuple[bool, str]:
        return self._display.save_theme(name, data_dir)

    def export_config(self, export_path: Path) -> Tuple[bool, str]:
        return self._display.export_config(export_path)

    def import_config(self, import_path: Path, data_dir: Path) -> Tuple[bool, str]:
        return self._display.import_config(import_path, data_dir)

    # ── Video Operations ──────────────────────────────────────────────

    def play_pause(self):
        self.video.toggle_play_pause()

    def seek_video(self, percent: float):
        self.video.seek(percent)

    def video_tick(self):
        result = self._display.video_tick()
        if not result:
            return

        self._fire_preview(result['preview'])

        if result.get('progress'):
            # Progress is forwarded by VideoController callbacks
            pass

        rgb565 = result.get('rgb565')
        if rgb565:
            self.devices.send_image_async(
                rgb565, self.lcd_width, self.lcd_height)

    def get_video_interval(self) -> int:
        return self._display.get_video_interval()

    def is_video_playing(self) -> bool:
        return self._display.is_video_playing()

    # ── Device Operations ─────────────────────────────────────────────

    def send_current_image(self):
        rgb565 = self._display.send_current_image()
        if rgb565:
            self.devices.send_image_async(
                rgb565, self.lcd_width, self.lcd_height)
            self._fire_status("Sent to LCD")

    def render_overlay_and_preview(self):
        image = self._display.render_overlay()
        if image:
            self._fire_preview(image)
        return image

    # ── Callbacks from sub-controllers ────────────────────────────────

    def _on_theme_selected(self, theme: ThemeInfo):
        if theme.theme_type == ThemeType.CLOUD:
            self.load_cloud_theme(theme)
        else:
            self.load_local_theme(theme)

    def _on_video_frame(self, frame: Any):
        self._display.current_image = frame

    def _on_device_selected(self, device: DeviceInfo):
        if device.resolution != (self.lcd_width, self.lcd_height):
            self.set_resolution(*device.resolution)
        self._fire_status(f"Device: {device.path}")

    # ── Private helpers (fire callbacks, send to LCD) ─────────────────

    def _fire_preview(self, image: Any):
        if self.on_preview_update:
            self.on_preview_update(image)

    def _fire_status(self, text: str):
        if self.on_status_update:
            self.on_status_update(text)

    def _fire_error(self, message: str):
        log.error("%s", message)
        if self.on_error:
            self.on_error(message)

    def _send_frame_to_lcd(self, image: Any):
        """Send a processed image to the LCD via DeviceService."""
        device = self.devices.get_selected()
        if not device:
            log.debug("Send skipped — no device selected")
            return
        try:
            encoded = self._display._encode_for_device(image)
            log.debug("Sending %d bytes to %s (%dx%d)",
                      len(encoded), device.path,
                      self.lcd_width, self.lcd_height)
            self.devices.send_image_async(
                encoded, self.lcd_width, self.lcd_height)
        except Exception as e:
            self._fire_error(f"LCD send error: {e}")

    # ── Compat shim: _setup_theme_dirs (used by qt_app_mvc) ──────────

    def _setup_theme_dirs(self, width: int, height: int):
        self._display._setup_dirs(width, height)
        self.themes.set_directories(
            local_dir=self._display.local_dir,
            web_dir=self._display.web_dir,
            masks_dir=self._display.masks_dir,
        )


# =============================================================================
# LED Controller (FormLED equivalent) — needs LEDService (future)
# =============================================================================

class LEDController:
    """Thin waiter for LED state and device communication.

    Delegates all logic to LEDService, fires callbacks to GUI.
    """

    def __init__(self, svc: Any = None):
        from ..services.led import LEDService
        self._svc: LEDService = svc or LEDService()

        # View callbacks
        self.on_state_changed: Optional[Callable] = None
        self.on_preview_update: Optional[Callable] = None
        self.on_send_complete: Optional[Callable[[bool], None]] = None

    @property
    def svc(self) -> Any:
        return self._svc

    @property
    def state(self) -> Any:
        return self._svc.state

    def set_mode(self, mode) -> None:
        self._svc.set_mode(mode)
        self._fire_state_changed()

    def set_color(self, r: int, g: int, b: int) -> None:
        self._svc.set_color(r, g, b)
        self._fire_state_changed()

    def set_brightness(self, brightness: int) -> None:
        self._svc.set_brightness(brightness)
        self._fire_state_changed()

    def toggle_global(self, on: bool) -> None:
        self._svc.toggle_global(on)
        self._fire_state_changed()

    def toggle_segment(self, index: int, on: bool) -> None:
        self._svc.toggle_segment(index, on)
        self._fire_state_changed()

    def set_zone_mode(self, zone: int, mode) -> None:
        self._svc.set_zone_mode(zone, mode)
        self._fire_state_changed()

    def set_zone_color(self, zone: int, r: int, g: int, b: int) -> None:
        self._svc.set_zone_color(zone, r, g, b)
        self._fire_state_changed()

    def set_zone_brightness(self, zone: int, brightness: int) -> None:
        self._svc.set_zone_brightness(zone, brightness)
        self._fire_state_changed()

    def set_sensor_source(self, source: str) -> None:
        self._svc.set_sensor_source(source)

    def set_seg_temp_unit(self, unit: str) -> None:
        self._svc.set_seg_temp_unit(unit)

    def set_clock_format(self, is_24h: bool) -> None:
        self._svc.set_clock_format(is_24h)

    def set_week_start(self, is_sunday: bool) -> None:
        self._svc.set_week_start(is_sunday)

    def update_metrics(self, metrics: Dict) -> None:
        self._svc.update_metrics(metrics)

    def configure_for_style(self, style_id: int) -> None:
        self._svc.configure_for_style(style_id)
        self._svc._hr10_mode = (style_id == 13)
        if self._svc._hr10_mode:
            self._svc._update_hr10_mask()

        # Activate segment display for all digit-display styles (1-11)
        from ..device_led_segment import get_display
        self._svc._seg_display = get_display(style_id)
        self._svc._segment_mode = self._svc._seg_display is not None
        if self._svc._segment_mode:
            self._svc._seg_phase = 0
            self._svc._seg_tick_count = 0
            self._svc._update_segment_mask()
        self._fire_state_changed()

    @property
    def _protocol(self) -> Any:
        return self._svc._protocol

    def set_protocol(self, protocol) -> None:
        self._svc.set_protocol(protocol)

    def set_display_value(self, text: str, indicators: Optional[set] = None) -> None:
        self._svc.set_display_value(text, indicators)

    def tick(self) -> None:
        colors = self._svc.tick()
        if self.on_preview_update:
            self.on_preview_update(colors)
        if self._svc._protocol:
            success = self._svc.send_colors(colors)
            if self.on_send_complete:
                self.on_send_complete(success)

    def _fire_state_changed(self) -> None:
        if self.on_state_changed:
            self.on_state_changed(self._svc.state)


class LEDDeviceController:
    """Main LED controller — thin waiter coordinating LEDController with device.

    Delegates all business logic to LEDService.
    """

    def __init__(self):
        from ..services.led import LEDService
        self._svc = LEDService()
        self.led = LEDController(self._svc)

        # View callbacks
        self.on_status_update: Optional[Callable[[str], None]] = None

    @property
    def svc(self) -> Any:
        return self._svc

    @property
    def _device_key(self) -> Any:
        return self._svc._device_key

    @_device_key.setter
    def _device_key(self, value: Any) -> None:
        self._svc._device_key = value

    def initialize(self, device_info, led_style: int = 1) -> None:
        status = self._svc.initialize(device_info, led_style)
        if self.on_status_update:
            self.on_status_update(status)

    def save_config(self) -> None:
        self._svc.save_config()

    def load_config(self) -> None:
        self._svc.load_config()

    def cleanup(self) -> None:
        self._svc.cleanup()


# =============================================================================
# Convenience function
# =============================================================================

def create_controller(data_dir: Optional[Path] = None) -> LCDDeviceController:
    """Create and initialize the main controller."""
    controller = LCDDeviceController()
    if data_dir:
        controller.initialize(data_dir)
    return controller
