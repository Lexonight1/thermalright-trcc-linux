"""
TRCC Controllers - Business logic that coordinates Models and Views.

Controllers are GUI-framework independent. They:
1. Own and manage Models
2. Provide methods that Views call for user actions
3. Emit callbacks that Views subscribe to for updates
"""

import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Callable, Any, Dict, Tuple, List

from .models import (
    ThemeInfo, ThemeModel, ThemeType,
    DeviceInfo, DeviceModel,
    VideoState, VideoModel, PlaybackState,
    OverlayElement, OverlayModel,
)
from ..paths import get_saved_resolution, save_resolution


class ThemeController:
    """
    Controller for theme management.

    Coordinates ThemeModel with theme browser views.
    """

    # Category mappings (Windows prefix -> display name)
    CATEGORIES = {
        'all': 'All',
        'a': 'Gallery',
        'b': 'Tech',
        'c': 'HUD',
        'd': 'Light',
        'e': 'Nature',
        'y': 'Aesthetic',
    }

    def __init__(self):
        self.model = ThemeModel()

        # View callbacks
        self.on_themes_loaded: Optional[Callable[[List[ThemeInfo]], None]] = None
        self.on_theme_selected: Optional[Callable[[ThemeInfo], None]] = None
        self.on_filter_changed: Optional[Callable[[str], None]] = None

        # Wire up model callbacks
        self.model.on_themes_changed = self._on_model_themes_changed
        self.model.on_selection_changed = self._on_model_selection_changed

    def set_directories(self,
                        local_dir: Optional[Path] = None,
                        videos_dir: Optional[Path] = None,
                        masks_dir: Optional[Path] = None):
        """Set theme directories."""
        if local_dir:
            self.model.set_local_directory(local_dir)
        if videos_dir or masks_dir:
            self.model.set_cloud_directories(videos_dir, masks_dir)

    def load_local_themes(self, resolution: Tuple[int, int] = (320, 320)):
        """Load local themes from directory."""
        self.model.load_local_themes(resolution)

    def load_cloud_themes(self):
        """Load cloud video themes."""
        self.model.load_cloud_themes()

    def set_filter(self, mode: str):
        """Set filter mode ('all', 'default', 'user')."""
        self.model.set_filter(mode)
        if self.on_filter_changed:
            self.on_filter_changed(mode)

    def set_category(self, category: str):
        """Set category filter for cloud themes."""
        self.model.set_category(category if category != 'all' else None)

    def select_theme(self, theme: ThemeInfo):
        """Select a theme."""
        self.model.select_theme(theme)

    def get_themes(self) -> List[ThemeInfo]:
        """Get current theme list."""
        return self.model.themes

    def get_selected(self) -> Optional[ThemeInfo]:
        """Get currently selected theme."""
        return self.model.selected_theme

    def _on_model_themes_changed(self):
        """Handle model themes changed."""
        if self.on_themes_loaded:
            self.on_themes_loaded(self.model.themes)

    def _on_model_selection_changed(self, theme: Optional[ThemeInfo]):
        """Handle model selection changed."""
        if self.on_theme_selected and theme:
            self.on_theme_selected(theme)


class DeviceController:
    """
    Controller for device management.

    Coordinates DeviceModel with device sidebar view.
    """

    def __init__(self):
        self.model = DeviceModel()

        # View callbacks
        self.on_devices_changed: Optional[Callable[[List[DeviceInfo]], None]] = None
        self.on_device_selected: Optional[Callable[[DeviceInfo], None]] = None
        self.on_send_started: Optional[Callable[[], None]] = None
        self.on_send_complete: Optional[Callable[[bool], None]] = None

        # Wire up model callbacks
        self.model.on_devices_changed = self._on_model_devices_changed
        self.model.on_selection_changed = self._on_model_selection_changed
        self.model.on_send_complete = self._on_model_send_complete

    def detect_devices(self):
        """Scan for connected LCD devices."""
        self.model.detect_devices()

    def select_device(self, device: DeviceInfo):
        """Select a device."""
        self.model.select_device(device)

    def get_devices(self) -> List[DeviceInfo]:
        """Get list of detected devices."""
        return self.model.devices

    def get_selected(self) -> Optional[DeviceInfo]:
        """Get currently selected device."""
        return self.model.selected_device

    def send_image_async(self, rgb565_data: bytes, width: int, height: int):
        """
        Send image to device in background thread.

        Non-blocking - emits on_send_complete when done.
        """
        if self.model.is_busy:
            return  # Skip if already sending

        if self.on_send_started:
            self.on_send_started()

        def send_worker():
            self.model.send_image(rgb565_data, width, height)

        thread = threading.Thread(target=send_worker, daemon=True)
        thread.start()

    def _on_model_devices_changed(self):
        """Handle model devices changed."""
        if self.on_devices_changed:
            self.on_devices_changed(self.model.devices)

    def _on_model_selection_changed(self, device: Optional[DeviceInfo]):
        """Handle model selection changed."""
        if self.on_device_selected and device:
            self.on_device_selected(device)

    def _on_model_send_complete(self, success: bool):
        """Handle model send complete."""
        if self.on_send_complete:
            self.on_send_complete(success)


class VideoController:
    """
    Controller for video/animation playback.

    Coordinates VideoModel with preview view and timer.
    """

    # LCD send interval: send every Nth frame.
    # Timer fires at 16fps (matching Windows extraction rate), so send every
    # frame.  Windows runs its timer at ~60Hz and sends every 4th tick (~15fps).
    LCD_SEND_INTERVAL = 1

    def __init__(self):
        self.model = VideoModel()

        # View callbacks
        self.on_video_loaded: Optional[Callable[[VideoState], None]] = None
        self.on_frame_ready: Optional[Callable[[Any], None]] = None  # PIL Image
        self.on_progress_update: Optional[Callable[[float, str, str], None]] = None
        self.on_state_changed: Optional[Callable[[PlaybackState], None]] = None

        # LCD send callback (injected by parent controller)
        self.on_send_frame: Optional[Callable[[Any], None]] = None

        # Animation state
        self._timer_callback: Optional[Callable[[], None]] = None
        self._frame_counter = 0

        # Wire up model callbacks
        self.model.on_state_changed = self._on_model_state_changed
        self.model.on_frame_ready = self._on_model_frame_ready

    def set_target_size(self, width: int, height: int):
        """Set target frame size."""
        self.model.target_size = (width, height)

    def load(self, path: Path) -> bool:
        """
        Load video file.

        Returns True if successful.
        """
        success = self.model.load(path, preload=True)
        if success and self.on_video_loaded:
            self.on_video_loaded(self.model.state)
        return success

    def play(self):
        """Start playback."""
        self.model.play()
        self._frame_counter = 0

    def pause(self):
        """Pause playback."""
        self.model.pause()

    def stop(self):
        """Stop playback."""
        self.model.stop()

    def toggle_play_pause(self):
        """Toggle between play and pause."""
        if self.model.is_playing:
            self.pause()
        else:
            self.play()

    def seek(self, percent: float):
        """Seek to percentage position."""
        self.model.seek(percent)

    def tick(self) -> Optional[Any]:
        """
        Called by timer to advance frame.

        Returns current frame (PIL Image) or None.
        """
        if not self.model.is_playing:
            return None

        frame = self.model.advance_frame()

        if frame:
            # Update progress
            if self.on_progress_update:
                state = self.model.state
                self.on_progress_update(
                    state.progress,
                    state.current_time_str,
                    state.total_time_str
                )

            # Send to LCD with frame skipping
            self._frame_counter += 1
            if self._frame_counter >= self.LCD_SEND_INTERVAL:
                self._frame_counter = 0
                if self.on_send_frame:
                    self.on_send_frame(frame)

        return frame

    def get_frame_interval(self) -> int:
        """Get frame interval in milliseconds for timer."""
        return self.model.state.frame_interval_ms

    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self.model.is_playing

    def _on_model_state_changed(self, state: VideoState):
        """Handle model state changed."""
        if self.on_state_changed:
            self.on_state_changed(state.state)

    def _on_model_frame_ready(self, frame: Any):
        """Handle model frame ready."""
        if self.on_frame_ready:
            self.on_frame_ready(frame)


class OverlayController:
    """
    Controller for overlay configuration and rendering.

    Coordinates OverlayModel with settings view.
    """

    def __init__(self):
        self.model = OverlayModel()

        # View callbacks
        self.on_config_changed: Optional[Callable[[], None]] = None

        # Metrics update
        self._metrics: Dict[str, Any] = {}

        # Wire up model callbacks
        self.model.on_config_changed = self._on_model_config_changed

    def set_target_size(self, width: int, height: int):
        """Set target render size."""
        self.model.target_size = (width, height)

    def enable(self, enabled: bool = True):
        """Enable or disable overlay rendering."""
        self.model.enabled = enabled

    def is_enabled(self) -> bool:
        """Check if overlay is enabled."""
        return self.model.enabled

    def add_element(self, element: OverlayElement):
        """Add an overlay element."""
        self.model.add_element(element)

    def remove_element(self, index: int):
        """Remove element at index."""
        self.model.remove_element(index)

    def update_element(self, index: int, element: OverlayElement):
        """Update element at index."""
        self.model.update_element(index, element)

    def get_elements(self) -> List[OverlayElement]:
        """Get all overlay elements."""
        return self.model.elements

    def set_background(self, image: Any):
        """Set background image for rendering."""
        self.model.set_background(image)

    def update_metrics(self, metrics: Dict[str, Any]):
        """Update system metrics for hardware overlay elements."""
        self._metrics = metrics

    def render(self, background: Optional[Any] = None) -> Any:
        """
        Render overlay onto background.

        Args:
            background: Optional PIL Image to use as background

        Returns:
            PIL Image with overlay rendered
        """
        if background:
            self.model.set_background(background)
        return self.model.render(self._metrics)

    def _ensure_renderer(self):
        """Ensure the internal renderer is initialized. Returns it or None."""
        if not self.model._renderer:
            self.model._update_renderer()
        return self.model._renderer

    def set_theme_mask(self, mask_image, position=None):
        """Set theme mask overlay image."""
        renderer = self._ensure_renderer()
        if renderer:
            renderer.set_theme_mask(mask_image, position)

    def get_theme_mask(self):
        """Get current theme mask image and position."""
        renderer = self._ensure_renderer()
        if renderer:
            return renderer.theme_mask, renderer.theme_mask_position
        return None, None

    def set_temp_unit(self, unit: int):
        """Set temperature display unit (0=Celsius, 1=Fahrenheit)."""
        renderer = self._ensure_renderer()
        if renderer:
            renderer.set_temp_unit(unit)

    def set_config(self, config: dict):
        """Set overlay config dict directly (from DC parsing)."""
        renderer = self._ensure_renderer()
        if renderer:
            renderer.set_config(config)

    def load_config(self, dc_path: Path) -> bool:
        """Load overlay config from DC file."""
        return self.model.load_from_dc(dc_path)

    def _on_model_config_changed(self):
        """Handle model config changed."""
        if self.on_config_changed:
            self.on_config_changed()


class FormCZTVController:
    """
    Main controller for LCD management.

    Coordinates all sub-controllers and provides unified interface
    for the main application view.

    This is the primary entry point for GUI views.
    """

    def __init__(self):
        # Sub-controllers
        self.themes = ThemeController()
        self.devices = DeviceController()
        self.video = VideoController()
        self.overlay = OverlayController()

        # Working directory (Windows GifDirectory pattern)
        self.working_dir = Path(tempfile.mkdtemp(prefix='trcc_work_'))

        # Current state — read saved resolution (default 320x320)
        saved_w, saved_h = get_saved_resolution()
        self.lcd_width = saved_w
        self.lcd_height = saved_h
        self.current_image: Optional[Any] = None  # PIL Image
        self.current_theme_path: Optional[Path] = None
        self.auto_send = True
        self.rotation = 0         # directionB: 0, 90, 180, 270
        self.brightness = 50      # myLddVal mapped: L1=25, L2=50, L3=100

        # View callbacks (unified interface)
        self.on_preview_update: Optional[Callable[[Any], None]] = None  # PIL Image
        self.on_status_update: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_resolution_changed: Optional[Callable[[int, int], None]] = None

        # Wire up sub-controller callbacks
        self._setup_callbacks()

    def _setup_callbacks(self):
        """Wire up sub-controller callbacks."""
        # Theme selection -> load and preview
        self.themes.on_theme_selected = self._on_theme_selected

        # Video frame -> preview and send (overlay applied before LCD send)
        self.video.on_frame_ready = self._on_video_frame
        self.video.on_send_frame = self._on_video_send_frame

        # Device selection -> update resolution
        self.devices.on_device_selected = self._on_device_selected

    def _clear_working_dir(self):
        """Clear and recreate working directory (matches Windows Theme_Click_Event)."""
        if self.working_dir.exists():
            shutil.rmtree(self.working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)

    def _copy_theme_to_working_dir(self, theme_path: Path):
        """Copy theme files to working dir (matches Windows CopyDireToDire)."""
        self._clear_working_dir()
        for f in theme_path.iterdir():
            if f.is_file():
                shutil.copy2(str(f), str(self.working_dir / f.name))

    def cleanup(self):
        """Clean up working directory on exit."""
        if self.working_dir and self.working_dir.exists():
            shutil.rmtree(self.working_dir, ignore_errors=True)

    def initialize(self, data_dir: Path):
        """
        Initialize controller with data directory.

        Sets up theme directories and detects devices.
        """
        self._data_dir = data_dir

        # Set LCD target size for video and overlay
        self.video.set_target_size(self.lcd_width, self.lcd_height)
        self.overlay.set_target_size(self.lcd_width, self.lcd_height)

        # Set theme directories
        theme_dir = data_dir / f'Theme{self.lcd_width}{self.lcd_height}'
        videos_dir = data_dir / 'videos'
        masks_dir = data_dir / 'cloud_masks' / f'zt{self.lcd_width}{self.lcd_height}'

        self.themes.set_directories(
            local_dir=theme_dir if theme_dir.exists() else None,
            videos_dir=videos_dir if videos_dir.exists() else None,
            masks_dir=masks_dir,
        )

        # Load initial themes
        self.themes.load_local_themes((self.lcd_width, self.lcd_height))

        # Detect devices
        self.devices.detect_devices()

    def set_resolution(self, width: int, height: int, persist: bool = True):
        """Set LCD resolution, update sub-controllers, and optionally persist to config."""
        if width == self.lcd_width and height == self.lcd_height:
            return
        self.lcd_width = width
        self.lcd_height = height
        self.video.set_target_size(width, height)
        self.overlay.set_target_size(width, height)

        if persist:
            save_resolution(width, height)

        # Reload theme directories for new resolution
        if hasattr(self, '_data_dir') and self._data_dir:
            theme_dir = self._data_dir / f'Theme{width}{height}'
            masks_dir = self._data_dir / 'cloud_masks' / f'zt{width}{height}'
            videos_dir = self._data_dir / 'videos'
            self.themes.set_directories(
                local_dir=theme_dir if theme_dir.exists() else None,
                videos_dir=videos_dir if videos_dir.exists() else None,
                masks_dir=masks_dir,
            )
            self.themes.load_local_themes((width, height))

        if self.on_resolution_changed:
            self.on_resolution_changed(width, height)

    def set_rotation(self, degrees: int):
        """Set display rotation (0, 90, 180, 270).

        Windows: directionB is applied in ImageTo565 before send.
        Square displays: 0→none, 90→CCW90, 180→180, 270→CW90
        Rectangular displays: extra +90° base (not yet needed, all current devices square).
        """
        self.rotation = degrees % 360
        # Refresh preview and resend with new rotation
        if self.current_image:
            image = self.current_image
            if self.overlay.is_enabled():
                image = self.overlay.render(image)
            self._update_preview(image)
            if self.auto_send:
                self._send_frame_to_lcd(self.current_image)

    def set_brightness(self, percent: int):
        """Set display brightness (25, 50, 100)."""
        self.brightness = max(0, min(100, percent))
        # Refresh preview and resend with new brightness (same pattern as set_rotation)
        if self.current_image:
            image = self.current_image
            if self.overlay.is_enabled():
                image = self.overlay.render(image)
            self._update_preview(image)
            if self.auto_send:
                self._send_frame_to_lcd(self.current_image)

    # =========================================================================
    # Theme Operations
    # =========================================================================

    def load_local_theme(self, theme: ThemeInfo):
        """Load a local theme with DC config, mask, and overlay.

        Matches Windows Theme_Click_Event → ReadSystemConfiguration pattern:
        stop previous animation, reset overlay, copy to working dir, then load.
        """
        # Stop any running video/animation first (Tkinter: stop_animation())
        self.video.stop()

        # Full overlay reset (Tkinter: set_background(None), set_theme_mask(None), overlay_config={})
        self.overlay.enable(False)
        self.overlay.set_background(None)
        self.overlay.set_theme_mask(None)
        self.overlay.set_config({})
        self.current_image = None

        self.current_theme_path = theme.path

        # Copy theme to working dir (Windows: CopyDireToDire(storage → GifDirectory))
        self._copy_theme_to_working_dir(theme.path)

        # Parse DC configuration file from working dir
        dc_path = self.working_dir / 'config1.dc'
        overlay_config = {}
        if dc_path.exists():
            try:
                from ..dc_parser import parse_dc_file, dc_to_overlay_config
                dc_data = parse_dc_file(str(dc_path))
                overlay_config = dc_to_overlay_config(dc_data)
                self.overlay.set_config(overlay_config)
            except Exception as e:
                print(f"[!] Failed to parse DC file: {e}")

        # Load background / animation from working dir
        bg_path = self.working_dir / '00.png'
        zt_path = self.working_dir / 'Theme.zt'
        if theme.is_animated and theme.animation_path:
            self.video.load(theme.animation_path)
            self.video.play()
        elif zt_path.exists():
            self.video.load(zt_path)
            self.video.play()
        elif bg_path.exists():
            self._load_static_image(bg_path)
        elif theme.is_mask_only:
            self._create_mask_background(theme)

        # Load mask (01.png) from working dir with position from DC config
        mask_path = self.working_dir / '01.png'
        if mask_path.exists():
            self._load_theme_mask(mask_path, dc_path if dc_path.exists() else None)

        self._update_status(f"Theme: {theme.name}")

    def load_cloud_theme(self, theme: ThemeInfo):
        """Load a cloud video theme as background.

        Cloud videos are backgrounds — overlay (mask + metrics) persists.
        Matches Tkinter load_video(): only stop_animation(), no overlay reset.
        """
        # Stop any running video/animation (Tkinter: stop_animation())
        self.video.stop()

        # Don't touch overlay — cloud videos are backgrounds, overlays stay on top

        if theme.animation_path:
            self.video.load(theme.animation_path)
            self.video.play()
            self._update_status(f"Cloud Theme: {theme.name}")

    def _load_static_image(self, path: Path):
        """Load a static image file."""
        try:
            from PIL import Image
            img = Image.open(path)
            img = img.resize((self.lcd_width, self.lcd_height), Image.Resampling.LANCZOS)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            self.current_image = img

            # Apply overlay if enabled
            if self.overlay.is_enabled():
                img = self.overlay.render(img)

            self._update_preview(img)

            if self.auto_send:
                self._send_frame_to_lcd(img)

        except Exception as e:
            self._handle_error(f"Failed to load image: {e}")

    def _create_mask_background(self, theme: ThemeInfo):
        """Create transparent background for mask-only theme."""
        try:
            from PIL import Image
            img = Image.new('RGB', (self.lcd_width, self.lcd_height), (0, 0, 0))
            self.current_image = img
            self._update_preview(img)
        except Exception as e:
            self._handle_error(f"Failed to create mask background: {e}")

    def _parse_mask_position(self, dc_path, mask_img):
        """Parse mask position from DC file and convert center to top-left coords.

        DC files store mask_position as center coordinates. Full-size masks go at (0,0).

        Returns:
            (x, y) top-left tuple, or None.
        """
        if mask_img.width >= self.lcd_width and mask_img.height >= self.lcd_height:
            return (0, 0)

        if not dc_path or not Path(dc_path).exists():
            return None

        try:
            from ..dc_parser import parse_dc_file
            dc_data = parse_dc_file(str(dc_path))
            mask_settings = dc_data.get('mask_settings', {})
            if mask_settings.get('mask_enabled'):
                center_pos = mask_settings.get('mask_position')
                if center_pos:
                    return (
                        center_pos[0] - mask_img.width // 2,
                        center_pos[1] - mask_img.height // 2,
                    )
        except Exception:
            pass

        return None

    def _load_theme_mask(self, mask_path: Path, dc_path: Path = None):
        """Load mask image with position from DC config.

        Reference: form_cztv.py:1077-1093
        """
        try:
            from PIL import Image
            mask_img = Image.open(mask_path)
            mask_position = self._parse_mask_position(dc_path, mask_img)
            self.overlay.set_theme_mask(mask_img, mask_position)
        except Exception as e:
            print(f"[!] Failed to load mask: {e}")

    def apply_mask(self, mask_dir: Path):
        """Apply a cloud mask to the current theme.

        Loads 01.png and config1.dc from mask_dir, copies them to working dir,
        sets overlay config, and updates preview.

        Windows pattern: mask edits go to working dir, not theme storage.
        Reference: form_cztv.py:1327-1410
        """
        mask_path = mask_dir / '01.png'
        config_path = mask_dir / 'config1.dc'

        if not mask_path.exists():
            self._handle_error(f"Mask not found: {mask_path}")
            return

        try:
            from PIL import Image

            # Copy mask files to working dir (Windows: edits go to GifDirectory)
            shutil.copy2(str(mask_path), str(self.working_dir / '01.png'))
            if config_path.exists():
                shutil.copy2(str(config_path), str(self.working_dir / 'config1.dc'))

            mask_img = Image.open(mask_path)
            mask_position = self._parse_mask_position(config_path, mask_img)

            # Also load overlay config from mask's DC
            if config_path.exists():
                try:
                    from ..dc_parser import parse_dc_file, dc_to_overlay_config
                    dc_data = parse_dc_file(str(config_path))
                    overlay_config = dc_to_overlay_config(dc_data)
                    self.overlay.set_config(overlay_config)
                except Exception as e:
                    print(f"[!] Failed to parse mask config: {e}")

            self.overlay.set_theme_mask(mask_img, mask_position)

            # Re-render preview
            img = self.render_overlay_and_preview()
            if img and self.auto_send:
                self._send_frame_to_lcd(img)

            self._update_status(f"Applied mask: {mask_dir.name}")

        except Exception as e:
            self._handle_error(f"Failed to apply mask: {e}")

    def load_image_file(self, path: Path):
        """Load a static image file (from settings panel 'Load Image')."""
        self._load_static_image(path)

    def save_theme(self, name: str, data_dir: Path) -> Tuple[bool, str]:
        """Save current config as a local theme.

        Windows pattern (buttonBCZT_Click): CopyDireToDire(working_dir → storage).
        Reference: form_cztv.py:1596-1677
        """
        if not self.current_image:
            return False, "No image to save"

        theme_path = data_dir / f'Theme{self.lcd_width}{self.lcd_height}' / name
        try:
            # Ensure current background is in working dir
            bg_path = self.working_dir / '00.png'
            if not bg_path.exists() and self.current_image:
                self.current_image.save(str(bg_path))

            # Generate thumbnail in working dir
            thumb_path = self.working_dir / 'Theme.png'
            if self.current_image:
                thumb = self.current_image.copy()
                thumb.thumbnail((120, 120))
                thumb.save(str(thumb_path))

            # Write config1.dc to working dir if dc_writer available
            try:
                from ..dc_writer import save_theme as dc_save_theme
                mask_img, mask_pos = self.overlay.get_theme_mask()
                dc_save_theme(
                    str(self.working_dir),
                    background_image=self.current_image,
                    mask_image=mask_img,
                    mask_position=mask_pos,
                    display_width=self.lcd_width,
                    display_height=self.lcd_height,
                )
            except ImportError:
                pass  # No dc_writer, working dir already has files

            # Copy working dir → theme storage (Windows: CopyDireToDire)
            theme_path.mkdir(parents=True, exist_ok=True)
            for f in self.working_dir.iterdir():
                if f.is_file():
                    shutil.copy2(str(f), str(theme_path / f.name))

            self.current_theme_path = theme_path
            return True, f"Saved: {name}"
        except Exception as e:
            return False, f"Save failed: {e}"

    def export_config(self, export_path: Path) -> Tuple[bool, str]:
        """Export current theme as .tr file.

        Reference: form_cztv.py:1678-1729
        """
        if not self.current_theme_path:
            return False, "No theme loaded"

        try:
            if str(export_path).endswith('.tr'):
                from ..dc_writer import export_theme
                export_theme(str(self.current_theme_path), str(export_path))
            else:
                import json
                config = {
                    'theme_path': str(self.current_theme_path),
                    'resolution': f'{self.lcd_width}x{self.lcd_height}',
                }
                with open(str(export_path), 'w') as f:
                    json.dump(config, f, indent=2)
            return True, f"Exported: {export_path.name}"
        except Exception as e:
            return False, f"Export failed: {e}"

    def import_config(self, import_path: Path, data_dir: Path) -> Tuple[bool, str]:
        """Import theme from .tr or JSON file.

        Reference: form_cztv.py:1730-1796
        """
        try:
            if str(import_path).endswith('.tr'):
                from ..dc_writer import import_theme
                name = import_path.stem
                theme_path = data_dir / f'Theme{self.lcd_width}{self.lcd_height}' / name
                import_theme(str(import_path), str(theme_path))
                theme = ThemeInfo.from_directory(theme_path)
                self.load_local_theme(theme)
                return True, f"Imported: {name}"
            else:
                import json
                with open(str(import_path)) as f:
                    config = json.load(f)
                tp = config.get('theme_path')
                if tp and Path(tp).exists():
                    theme = ThemeInfo.from_directory(Path(tp))
                    self.load_local_theme(theme)
                    return True, f"Imported config from {import_path.name}"
                return False, "Theme path in config not found"
        except Exception as e:
            return False, f"Import failed: {e}"

    # =========================================================================
    # Video Operations
    # =========================================================================

    def play_pause(self):
        """Toggle video play/pause."""
        self.video.toggle_play_pause()

    def seek_video(self, percent: float):
        """Seek video to percentage."""
        self.video.seek(percent)

    def video_tick(self):
        """Called by GUI timer to advance video frame."""
        frame = self.video.tick()
        if frame:
            self.current_image = frame
            # Apply overlay
            if self.overlay.is_enabled():
                frame = self.overlay.render(frame)
            self._update_preview(frame)

    def get_video_interval(self) -> int:
        """Get video frame interval for timer setup."""
        return self.video.get_frame_interval()

    def is_video_playing(self) -> bool:
        """Check if video is playing."""
        return self.video.is_playing()

    # =========================================================================
    # Device Operations
    # =========================================================================

    def _on_video_send_frame(self, frame: Any):
        """Send video frame to LCD with overlay applied.

        Matches Tkinter _animate_video(): render overlay on frame, then send.
        Called every LCD_SEND_INTERVAL frames by VideoController.tick().
        """
        if self.overlay.is_enabled():
            frame = self.overlay.render(frame)
        self._send_frame_to_lcd(frame)

    def send_current_image(self):
        """Send current image to LCD."""
        if self.current_image:
            self._send_frame_to_lcd(self.current_image)
            self._update_status("Sent to LCD")

    def _apply_rotation(self, image: Any) -> Any:
        """Apply display rotation to image.

        Windows ImageTo565 for square displays:
          directionB 0 → no rotation
          directionB 90 → RotateImg(270°CW) = PIL ROTATE_90 (CCW)
          directionB 180 → RotateImg(180°) = PIL ROTATE_180
          directionB 270 → RotateImg(90°CW) = PIL ROTATE_270 (CCW)
        """
        from PIL import Image as PILImage
        if self.rotation == 90:
            return image.transpose(PILImage.Transpose.ROTATE_270)
        elif self.rotation == 180:
            return image.transpose(PILImage.Transpose.ROTATE_180)
        elif self.rotation == 270:
            return image.transpose(PILImage.Transpose.ROTATE_90)
        return image

    def _apply_brightness(self, image: Any) -> Any:
        """Apply brightness adjustment to image.

        L1=25%, L2=50%, L3=100%. At 100% the image is unchanged.
        """
        if self.brightness >= 100:
            return image
        from PIL import ImageEnhance
        return ImageEnhance.Brightness(image).enhance(self.brightness / 100.0)

    def _send_frame_to_lcd(self, image: Any):
        """Send PIL Image to LCD device (with rotation and brightness applied)."""
        if not self.devices.get_selected():
            return

        try:
            adjusted = self._apply_brightness(image)
            rotated = self._apply_rotation(adjusted)
            rgb565_data = self._image_to_rgb565(rotated)
            self.devices.send_image_async(rgb565_data, self.lcd_width, self.lcd_height)
        except Exception as e:
            self._handle_error(f"LCD send error: {e}")

    def _image_to_rgb565(self, img: Any) -> bytes:
        """Convert PIL Image to RGB565 bytes."""
        import numpy as np

        if img.mode != 'RGB':
            img = img.convert('RGB')

        arr = np.array(img, dtype=np.uint16)
        r = (arr[:, :, 0] >> 3) & 0x1F
        g = (arr[:, :, 1] >> 2) & 0x3F
        b = (arr[:, :, 2] >> 3) & 0x1F
        rgb565 = (r << 11) | (g << 5) | b
        return rgb565.astype('>u2').tobytes()

    # =========================================================================
    # Callbacks from sub-controllers
    # =========================================================================

    def _on_theme_selected(self, theme: ThemeInfo):
        """Handle theme selection."""
        if theme.theme_type == ThemeType.CLOUD:
            self.load_cloud_theme(theme)
        else:
            self.load_local_theme(theme)

    def _on_video_frame(self, frame: Any):
        """Handle video frame ready."""
        self.current_image = frame

    def _on_device_selected(self, device: DeviceInfo):
        """Handle device selection."""
        # Update resolution if device has different res
        if device.resolution != (self.lcd_width, self.lcd_height):
            self.set_resolution(*device.resolution)
        self._update_status(f"Device: {device.path}")

    # =========================================================================
    # View update helpers
    # =========================================================================

    def _update_preview(self, image: Any):
        """Update preview in view (with rotation and brightness applied)."""
        if self.on_preview_update:
            adjusted = self._apply_brightness(image)
            rotated = self._apply_rotation(adjusted)
            self.on_preview_update(rotated)

    def render_overlay_and_preview(self):
        """Re-render overlay on current_image and update preview.

        Returns the rendered image (with overlay applied), or None.
        """
        if not self.current_image:
            return None
        img = self.overlay.render(self.current_image)
        self._update_preview(img)
        return img

    def _update_status(self, text: str):
        """Update status in view."""
        if self.on_status_update:
            self.on_status_update(text)

    def _handle_error(self, message: str):
        """Handle error."""
        print(f"[!] {message}")
        if self.on_error:
            self.on_error(message)


# =============================================================================
# Convenience function for creating main controller
# =============================================================================

def create_controller(data_dir: Optional[Path] = None) -> FormCZTVController:
    """
    Create and initialize the main controller.

    Args:
        data_dir: Optional data directory path

    Returns:
        Initialized FormCZTVController
    """
    controller = FormCZTVController()

    if data_dir:
        controller.initialize(data_dir)

    return controller
