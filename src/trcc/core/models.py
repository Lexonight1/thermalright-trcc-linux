"""
TRCC Models - Pure data classes with no GUI dependencies.

These models can be used by any GUI framework (Tkinter, PyQt6, etc.)
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# =============================================================================
# Theme Model
# =============================================================================

class ThemeType(Enum):
    """Type of theme."""
    LOCAL = auto()      # Local theme from Theme{resolution}/ directory
    CLOUD = auto()      # Cloud theme (video) from Web/{W}{H}/ directory
    MASK = auto()       # Mask overlay from Web/zt{W}{H}/ directory
    USER = auto()       # User-created theme


@dataclass
class ThemeInfo:
    """
    Information about a single theme.

    Matches Windows FormCZTV theme data structure.
    """
    name: str
    path: Optional[Path] = None
    theme_type: ThemeType = ThemeType.LOCAL

    # Files within theme directory
    background_path: Optional[Path] = None      # 00.png
    mask_path: Optional[Path] = None            # 01.png
    thumbnail_path: Optional[Path] = None       # Theme.png
    animation_path: Optional[Path] = None       # Theme.zt or video file
    config_path: Optional[Path] = None          # config1.dc

    # Metadata
    resolution: Tuple[int, int] = (320, 320)
    is_animated: bool = False
    is_mask_only: bool = False

    # Cloud theme specific
    video_url: Optional[str] = None
    preview_url: Optional[str] = None
    category: Optional[str] = None  # a=Gallery, b=Tech, c=HUD, etc.

    @classmethod
    def from_directory(cls, path: Path, resolution: Tuple[int, int] = (320, 320)) -> 'ThemeInfo':
        """Create ThemeInfo from a theme directory."""
        bg_path = path / '00.png'
        mask_path = path / '01.png'
        thumb_path = path / 'Theme.png'
        anim_path = path / 'Theme.zt'
        config_path = path / 'config1.dc'

        # Determine if animated — check Theme.zt first, then .mp4 files
        if anim_path.exists():
            is_animated = True
            animation_path = anim_path
        else:
            # Look for MP4 files (saved cloud video themes)
            mp4_files = list(path.glob('*.mp4'))
            if mp4_files:
                is_animated = True
                animation_path = mp4_files[0]
            else:
                is_animated = False
                animation_path = None

        # Determine if mask-only (no background)
        is_mask_only = not bg_path.exists() and mask_path.exists()

        return cls(
            name=path.name,
            path=path,
            theme_type=ThemeType.LOCAL,
            background_path=bg_path if bg_path.exists() else None,
            mask_path=mask_path if mask_path.exists() else None,
            thumbnail_path=thumb_path if thumb_path.exists() else (bg_path if bg_path.exists() else None),
            animation_path=animation_path,
            config_path=config_path if config_path.exists() else None,
            resolution=resolution,
            is_animated=is_animated,
            is_mask_only=is_mask_only,
        )

    @classmethod
    def from_video(cls, video_path: Path, preview_path: Optional[Path] = None) -> 'ThemeInfo':
        """Create ThemeInfo from a cloud video file."""
        name = video_path.stem
        category = name[0] if name else None

        return cls(
            name=name,
            path=video_path.parent,
            theme_type=ThemeType.CLOUD,
            animation_path=video_path,
            thumbnail_path=preview_path,
            is_animated=True,
            category=category,
        )


@dataclass
class ThemeModel:
    """
    Model for managing themes.

    Handles theme discovery, loading, filtering.
    """
    themes: List[ThemeInfo] = field(default_factory=list)
    selected_theme: Optional[ThemeInfo] = None
    filter_mode: str = 'all'  # 'all', 'default', 'user'
    category_filter: Optional[str] = None  # For cloud themes

    # Directories
    local_theme_dir: Optional[Path] = None
    cloud_web_dir: Optional[Path] = None
    cloud_masks_dir: Optional[Path] = None

    # Callbacks for view updates
    on_themes_changed: Optional[Callable[[], None]] = None
    on_selection_changed: Optional[Callable[[Optional[ThemeInfo]], None]] = None

    def set_local_directory(self, path: Path):
        """Set local themes directory."""
        self.local_theme_dir = path

    def set_cloud_directories(self, web_dir: Path, masks_dir: Path):
        """Set cloud theme directories."""
        self.cloud_web_dir = web_dir
        self.cloud_masks_dir = masks_dir

    def load_local_themes(self, resolution: Tuple[int, int] = (320, 320)) -> List[ThemeInfo]:
        """Load themes from local directory."""
        self.themes.clear()

        if not self.local_theme_dir or not self.local_theme_dir.exists():
            return self.themes

        for item in sorted(self.local_theme_dir.iterdir()):
            if item.is_dir():
                # Check for theme files
                has_bg = (item / '00.png').exists()
                has_thumb = (item / 'Theme.png').exists()
                has_mask = (item / '01.png').exists()

                if has_bg or has_thumb or has_mask:
                    theme = ThemeInfo.from_directory(item, resolution)

                    # Apply filter
                    if self._passes_filter(theme):
                        self.themes.append(theme)

        if self.on_themes_changed:
            self.on_themes_changed()

        return self.themes

    def load_cloud_themes(self) -> List[ThemeInfo]:
        """Load cloud video themes."""
        self.themes.clear()

        if not self.cloud_web_dir or not self.cloud_web_dir.exists():
            return self.themes

        for video_file in sorted(self.cloud_web_dir.glob('*.mp4')):
            preview_path = self.cloud_web_dir / f"{video_file.stem}.png"
            theme = ThemeInfo.from_video(
                video_file,
                preview_path if preview_path.exists() else None
            )

            # Apply category filter
            if self.category_filter and self.category_filter != 'all':
                if theme.category != self.category_filter:
                    continue

            self.themes.append(theme)

        if self.on_themes_changed:
            self.on_themes_changed()

        return self.themes

    def _passes_filter(self, theme: ThemeInfo) -> bool:
        """Check if theme passes current filter."""
        if self.filter_mode == 'all':
            return True
        elif self.filter_mode == 'default':
            return theme.theme_type == ThemeType.LOCAL and not theme.name.startswith(('User', 'Custom'))
        elif self.filter_mode == 'user':
            return theme.theme_type == ThemeType.USER or theme.name.startswith(('User', 'Custom'))
        return True

    def select_theme(self, theme: Optional[ThemeInfo]):
        """Select a theme."""
        self.selected_theme = theme
        if self.on_selection_changed:
            self.on_selection_changed(theme)

    def set_filter(self, mode: str):
        """Set filter mode and reload."""
        self.filter_mode = mode

    def set_category(self, category: Optional[str]):
        """Set category filter for cloud themes."""
        self.category_filter = category


# =============================================================================
# Device Model
# =============================================================================

@dataclass
class DeviceInfo:
    """
    Information about a connected LCD device.

    Matches Windows FormCZTV device data.
    """
    name: str
    path: str  # /dev/sgX
    resolution: Tuple[int, int] = (320, 320)

    # Device properties (from detection)
    vendor: Optional[str] = None
    product: Optional[str] = None
    model: Optional[str] = None
    vid: int = 0
    pid: int = 0
    device_index: int = 0  # 0-based ordinal among detected devices
    fbl_code: Optional[int] = None  # Resolution identifier
    protocol: str = "scsi"  # "scsi" or "hid"
    device_type: int = 1  # 1=SCSI, 2=HID Type 2 ("H"), 3=HID Type 3 ("ALi")
    implementation: str = "generic"  # e.g. "thermalright_lcd_v1", "hid_type2", "hid_led"

    # State
    connected: bool = True
    brightness: int = 100  # 0-100%
    rotation: int = 0  # 0, 90, 180, 270

    @property
    def resolution_str(self) -> str:
        """Get resolution as string (e.g., '320x320')."""
        return f"{self.resolution[0]}x{self.resolution[1]}"


@dataclass
class DeviceModel:
    """
    Model for managing LCD devices.

    Handles device detection, selection, communication.
    """
    devices: List[DeviceInfo] = field(default_factory=list)
    selected_device: Optional[DeviceInfo] = None

    # Callbacks
    on_devices_changed: Optional[Callable[[], None]] = None
    on_selection_changed: Optional[Callable[[Optional[DeviceInfo]], None]] = None
    on_send_complete: Optional[Callable[[bool], None]] = None

    # Send state
    _send_busy: bool = False

    def detect_devices(self) -> List[DeviceInfo]:
        """Detect connected LCD devices."""
        try:
            from ..scsi_device import find_lcd_devices
            raw_devices = find_lcd_devices()

            self.devices = [
                DeviceInfo(
                    name=d.get('name', 'LCD'),
                    path=d.get('path', ''),
                    resolution=d.get('resolution', (320, 320)) if isinstance(d.get('resolution'), tuple)
                              else (320, 320),
                    vendor=d.get('vendor'),
                    product=d.get('product'),
                    model=d.get('model'),
                    vid=d.get('vid', 0),
                    pid=d.get('pid', 0),
                    device_index=d.get('device_index', 0),
                    protocol=d.get('protocol', 'scsi'),
                    device_type=d.get('device_type', 1),
                )
                for d in raw_devices
            ]
        except ImportError:
            self.devices = []

        if self.on_devices_changed:
            self.on_devices_changed()

        # Auto-select first device
        if self.devices and not self.selected_device:
            self.select_device(self.devices[0])

        return self.devices

    def select_device(self, device: Optional[DeviceInfo]):
        """Select a device."""
        self.selected_device = device
        if self.on_selection_changed:
            self.on_selection_changed(device)

    def send_image(self, image_data: bytes, width: int, height: int) -> bool:
        """
        Send image data to selected device via factory-routed protocol.

        Uses DeviceProtocolFactory to pick the right protocol (SCSI or HID)
        based on the selected device's protocol field.  The GUI just fires
        this command — the protocol layer handles routing (like Windows
        DelegateFormCZTV vs DelegateFormCZTVHid).

        Args:
            image_data: Pixel bytes (RGB565 for SCSI, JPEG for HID).
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            True if send was successful.
        """
        if not self.selected_device or self._send_busy:
            return False

        try:
            from ..device_factory import DeviceProtocolFactory
            self._send_busy = True

            protocol = DeviceProtocolFactory.get_protocol(self.selected_device)
            success = protocol.send_image(image_data, width, height)

            self._send_busy = False

            if self.on_send_complete:
                self.on_send_complete(success)

            return success

        except Exception as e:
            self._send_busy = False
            print(f"[!] Device send error: {e}")
            return False

    @property
    def is_busy(self) -> bool:
        """Check if device is busy sending."""
        return self._send_busy


# =============================================================================
# Video Model
# =============================================================================

class PlaybackState(Enum):
    """Video playback state."""
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


@dataclass
class VideoState:
    """
    State of video/animation playback.
    """
    state: PlaybackState = PlaybackState.STOPPED
    current_frame: int = 0
    total_frames: int = 0
    fps: float = 16.0
    loop: bool = True

    @property
    def progress(self) -> float:
        """Get playback progress (0-100)."""
        if self.total_frames <= 0:
            return 0.0
        return (self.current_frame / self.total_frames) * 100

    @property
    def current_time_str(self) -> str:
        """Get current time as MM:SS string."""
        if self.fps <= 0:
            return "00:00"
        secs = self.current_frame / self.fps
        return f"{int(secs // 60):02d}:{int(secs % 60):02d}"

    @property
    def total_time_str(self) -> str:
        """Get total time as MM:SS string."""
        if self.fps <= 0:
            return "00:00"
        secs = self.total_frames / self.fps
        return f"{int(secs // 60):02d}:{int(secs % 60):02d}"

    @property
    def frame_interval_ms(self) -> int:
        """Get frame interval in milliseconds."""
        if self.fps <= 0:
            return 62  # Default ~16fps (Windows: 62.5ms per frame)
        return int(1000 / self.fps)


@dataclass
class VideoModel:
    """
    Model for video/animation playback.

    Handles frame loading, playback state, timing.
    """
    state: VideoState = field(default_factory=VideoState)
    frames: List[Any] = field(default_factory=list)  # PIL Images
    source_path: Optional[Path] = None
    target_size: Tuple[int, int] = (320, 320)

    # Callbacks
    on_state_changed: Optional[Callable[[VideoState], None]] = None
    on_frame_ready: Optional[Callable[[Any], None]] = None  # PIL Image

    # Internal player reference
    _player: Any = None

    def load(self, path: Path, preload: bool = True) -> bool:
        """
        Load video/animation file.

        Args:
            path: Path to video file (.mp4, .gif, .zt)
            preload: Whether to preload all frames into memory

        Returns:
            True if loaded successfully
        """
        self.stop()
        self.source_path = path
        self.frames.clear()

        try:
            from ..gif_animator import ThemeZtPlayer, VideoPlayer

            suffix = path.suffix.lower()

            if suffix == '.zt':
                self._player = ThemeZtPlayer(str(path), self.target_size)
            else:
                self._player = VideoPlayer(str(path), self.target_size)

            # Update state
            self.state.total_frames = self._player.frame_count
            self.state.fps = self._player.fps if self._player.fps > 0 else 16
            self.state.current_frame = 0
            self.state.state = PlaybackState.STOPPED

            # Preload frames if requested (matches Windows behavior)
            if preload and hasattr(self._player, 'frames'):
                self.frames = self._player.frames

            if self.on_state_changed:
                self.on_state_changed(self.state)

            return True

        except Exception as e:
            print(f"[!] Failed to load video: {e}")
            return False

    def play(self):
        """Start playback."""
        if self._player:
            self._player.play()
            self.state.state = PlaybackState.PLAYING
            if self.on_state_changed:
                self.on_state_changed(self.state)

    def pause(self):
        """Pause playback."""
        if self._player:
            self._player.pause()
            self.state.state = PlaybackState.PAUSED
            if self.on_state_changed:
                self.on_state_changed(self.state)

    def stop(self):
        """Stop playback."""
        if self._player:
            self._player.stop()
        self.state.state = PlaybackState.STOPPED
        self.state.current_frame = 0
        if self.on_state_changed:
            self.on_state_changed(self.state)

    def seek(self, percent: float):
        """Seek to percentage position."""
        if self.state.total_frames > 0:
            self.state.current_frame = int((percent / 100) * self.state.total_frames)
            self.state.current_frame = max(0, min(self.state.current_frame, self.state.total_frames - 1))

    def get_frame(self, index: Optional[int] = None) -> Optional[Any]:
        """
        Get frame at index (or current frame).

        Returns:
            PIL Image or None
        """
        if index is None:
            index = self.state.current_frame

        # Try preloaded frames first
        if self.frames and 0 <= index < len(self.frames):
            return self.frames[index]

        # Fall back to player
        if self._player:
            self._player.current_frame = index
            return self._player.get_current_frame()

        return None

    def advance_frame(self) -> Optional[Any]:
        """
        Advance to next frame and return it.

        Returns:
            PIL Image or None
        """
        if self.state.state != PlaybackState.PLAYING:
            return None

        frame = self.get_frame()

        # Advance
        self.state.current_frame += 1
        if self.state.current_frame >= self.state.total_frames:
            if self.state.loop:
                self.state.current_frame = 0
            else:
                self.state.state = PlaybackState.STOPPED

        if self.on_frame_ready:
            self.on_frame_ready(frame)

        return frame

    @property
    def is_playing(self) -> bool:
        return self.state.state == PlaybackState.PLAYING


# =============================================================================
# Overlay Model
# =============================================================================

class OverlayElementType(Enum):
    """Type of overlay element."""
    HARDWARE = 0    # CPU temp, GPU usage, etc.
    TIME = 1        # Current time
    WEEKDAY = 2     # Day of week
    DATE = 3        # Current date
    TEXT = 4        # Custom text


@dataclass
class OverlayElement:
    """
    Single overlay element configuration.

    Matches Windows UCXiTongXianShi element data.
    """
    element_type: OverlayElementType = OverlayElementType.TEXT
    enabled: bool = True
    x: int = 10
    y: int = 10
    color: Tuple[int, int, int] = (255, 255, 255)
    font_size: int = 16
    font_name: str = "Microsoft YaHei"

    # Hardware element specific
    metric_key: Optional[str] = None  # e.g., 'cpu_temp', 'gpu_usage'
    format_string: str = "{value}"    # e.g., "CPU: {value}°C"

    # Text element specific
    text: str = ""


@dataclass
class OverlayModel:
    """
    Model for overlay configuration and rendering.

    Manages overlay elements and their rendering onto frames.
    """
    elements: List[OverlayElement] = field(default_factory=list)
    enabled: bool = False
    background: Any = None  # PIL Image
    target_size: Tuple[int, int] = (320, 320)

    # Callbacks
    on_config_changed: Optional[Callable[[], None]] = None

    # Internal renderer
    _renderer: Any = None

    # Preserved DC data for lossless round-trip save
    _dc_data: Optional[Dict[str, Any]] = None

    def set_dc_data(self, dc_data: Optional[Dict[str, Any]]) -> None:
        """Store parsed DC data for lossless save round-trip."""
        self._dc_data = dc_data

    def get_dc_data(self) -> Optional[Dict[str, Any]]:
        """Get stored DC data (display_elements, display_options, etc.)."""
        return self._dc_data

    def clear_dc_data(self) -> None:
        """Clear stored DC data (called on new theme load)."""
        self._dc_data = None

    def add_element(self, element: OverlayElement):
        """Add an overlay element."""
        self.elements.append(element)
        self._update_renderer()
        if self.on_config_changed:
            self.on_config_changed()

    def remove_element(self, index: int):
        """Remove element at index."""
        if 0 <= index < len(self.elements):
            self.elements.pop(index)
            self._update_renderer()
            if self.on_config_changed:
                self.on_config_changed()

    def update_element(self, index: int, element: OverlayElement):
        """Update element at index."""
        if 0 <= index < len(self.elements):
            self.elements[index] = element
            self._update_renderer()
            if self.on_config_changed:
                self.on_config_changed()

    def set_background(self, image: Any):
        """Set background image (PIL Image)."""
        self.background = image
        if self._renderer:
            self._renderer.set_background(image)

    def _update_renderer(self):
        """Update internal renderer with current config."""
        if not self._renderer:
            try:
                from ..overlay_renderer import OverlayRenderer
                self._renderer = OverlayRenderer(*self.target_size)
            except ImportError:
                return

        # Convert elements to renderer config format
        config = {}
        for i, elem in enumerate(self.elements):
            if not elem.enabled:
                continue

            key = f"{elem.element_type.name.lower()}_{i}"
            config[key] = {
                'enabled': True,
                'x': elem.x,
                'y': elem.y,
                'color': elem.color,
                'font_size': elem.font_size,
                'font': elem.font_name,
                'metric': elem.metric_key,
                'format': elem.format_string,
                'text': elem.text,
            }

        self._renderer.set_config(config)

    def render(self, metrics: Optional[Dict[str, Any]] = None) -> Any:
        """
        Render overlay onto background.

        Args:
            metrics: System metrics dict for hardware elements

        Returns:
            PIL Image with overlay rendered
        """
        if not self.enabled or not self._renderer:
            return self.background

        return self._renderer.render(metrics or {})

    def load_from_dc(self, dc_path: Path) -> bool:
        """
        Load overlay config from DC file.

        Args:
            dc_path: Path to config1.dc file

        Returns:
            True if loaded successfully
        """
        try:
            from ..dc_parser import dc_to_overlay_config, parse_dc_file

            dc_data = parse_dc_file(str(dc_path))
            config = dc_to_overlay_config(dc_data)

            # Convert config to elements
            self.elements.clear()
            for key, elem_config in config.items():
                if not elem_config.get('enabled'):
                    continue

                elem = OverlayElement(
                    enabled=True,
                    x=elem_config.get('x', 10),
                    y=elem_config.get('y', 10),
                    color=elem_config.get('color', (255, 255, 255)),
                    font_size=elem_config.get('font_size', 16),
                    metric_key=elem_config.get('metric'),
                    format_string=elem_config.get('format', '{value}'),
                )
                self.elements.append(elem)

            self._update_renderer()
            return True

        except Exception as e:
            print(f"[!] Failed to load DC file: {e}")
            return False


# =============================================================================
# LED Model (FormLED equivalent)
# =============================================================================

class LEDMode(Enum):
    """LED effect modes from FormLED.cs timer functions."""
    STATIC = 0       # DSCL_Timer: solid color
    BREATHING = 1    # DSHX_Timer: fade in/out, period=66 ticks
    COLORFUL = 2     # QCJB_Timer: 6-phase gradient, period=168 ticks
    RAINBOW = 3      # CHMS_Timer: 768-entry table shift
    TEMP_LINKED = 4  # WDLD_Timer: color from CPU/GPU temperature
    LOAD_LINKED = 5  # FZLD_Timer: color from CPU/GPU load %


@dataclass
class LEDZoneState:
    """Per-zone state for multi-zone LED devices.

    Multi-zone devices (styles 2,3,5,6,7,8,11) have 2-4 independent zones,
    each with its own mode, color, brightness, and on/off state.
    From FormLED.cs: myLedMode1-4, rgbR1_1-4, myBrightness1-4, myOnOff1-4.
    """
    mode: LEDMode = LEDMode.STATIC
    color: Tuple[int, int, int] = (255, 0, 0)
    brightness: int = 100  # 0-100 (from FormLED.cs myBrightness, default 65)
    on: bool = True


@dataclass
class LEDState:
    """Complete LED device state matching FormLED.cs globals.

    This is the serializable state that gets persisted and restored.
    Animation counters are transient (not saved).
    """
    # Device configuration (from handshake pm → LedDeviceStyle)
    style: int = 1              # nowLedStyle
    led_count: int = 30         # from LedDeviceStyle.led_count
    segment_count: int = 10     # from LedDeviceStyle.segment_count
    zone_count: int = 1         # from LedDeviceStyle.zone_count

    # Global state
    mode: LEDMode = LEDMode.STATIC    # myLedMode
    color: Tuple[int, int, int] = (255, 0, 0)  # rgbR1, rgbG1, rgbB1
    brightness: int = 100       # myBrightness (0-100)
    global_on: bool = True      # myOnOff

    # Per-segment on/off (ucScreenLED1.isOn[] per logical segment)
    segment_on: List[bool] = field(default_factory=list)

    # Multi-zone states (styles with zone_count > 1)
    zones: List[LEDZoneState] = field(default_factory=list)

    # Animation counters (transient, not persisted)
    rgb_timer: int = 0          # rgbTimer for breathing/gradient/rainbow

    # Sensor linkage (for TEMP_LINKED and LOAD_LINKED modes)
    temp_source: str = "cpu"    # "cpu" or "gpu"
    load_source: str = "cpu"    # "cpu" or "gpu"

    def __post_init__(self):
        if not self.segment_on:
            self.segment_on = [True] * self.segment_count
        if not self.zones and self.zone_count > 1:
            self.zones = [LEDZoneState() for _ in range(self.zone_count)]


@dataclass
class LEDModel:
    """Model for LED state management and effect computation.

    Manages the LED state and computes per-LED colors each tick based on
    the active effect mode. Ported from FormLED.cs timer event handlers.

    The tick() method advances the animation by one step and returns the
    computed LED colors. The controller calls tick() on a 30ms timer.
    """
    state: LEDState = field(default_factory=LEDState)

    # Callbacks (observer pattern)
    on_state_changed: Optional[Callable[['LEDState'], None]] = None
    on_colors_updated: Optional[Callable[[List[Tuple[int, int, int]]], None]] = None

    # Cached sensor metrics (updated by controller from system_info)
    _metrics: Dict[str, Any] = field(default_factory=dict)

    def set_mode(self, mode: LEDMode) -> None:
        """Set LED effect mode."""
        self.state.mode = LEDMode(mode) if not isinstance(mode, LEDMode) else mode
        self.state.rgb_timer = 0  # Reset animation
        self._notify_state_changed()

    def set_color(self, r: int, g: int, b: int) -> None:
        """Set global LED color."""
        self.state.color = (r, g, b)
        self._notify_state_changed()

    def set_brightness(self, brightness: int) -> None:
        """Set global brightness (0-100)."""
        self.state.brightness = max(0, min(100, brightness))
        self._notify_state_changed()

    def toggle_global(self, on: bool) -> None:
        """Set global on/off."""
        self.state.global_on = on
        self._notify_state_changed()

    def toggle_segment(self, index: int, on: bool) -> None:
        """Toggle a single LED segment."""
        if 0 <= index < len(self.state.segment_on):
            self.state.segment_on[index] = on
            self._notify_state_changed()

    def set_zone_mode(self, zone: int, mode: LEDMode) -> None:
        """Set mode for a specific zone (multi-zone devices)."""
        if 0 <= zone < len(self.state.zones):
            self.state.zones[zone].mode = LEDMode(mode) if not isinstance(mode, LEDMode) else mode
            self._notify_state_changed()

    def set_zone_color(self, zone: int, r: int, g: int, b: int) -> None:
        """Set color for a specific zone."""
        if 0 <= zone < len(self.state.zones):
            self.state.zones[zone].color = (r, g, b)
            self._notify_state_changed()

    def set_zone_brightness(self, zone: int, brightness: int) -> None:
        """Set brightness for a specific zone."""
        if 0 <= zone < len(self.state.zones):
            self.state.zones[zone].brightness = max(0, min(100, brightness))
            self._notify_state_changed()

    def update_metrics(self, metrics: Dict[str, Any]) -> None:
        """Update cached sensor metrics for temp/load-linked modes."""
        self._metrics = metrics

    def configure_for_style(self, style_id: int) -> None:
        """Configure state for a specific LED device style."""
        from ..led_device import LED_STYLES
        style = LED_STYLES.get(style_id)
        if style:
            self.state.style = style.style_id
            self.state.led_count = style.led_count
            self.state.segment_count = style.segment_count
            self.state.zone_count = style.zone_count
            self.state.segment_on = [True] * style.segment_count
            if style.zone_count > 1:
                self.state.zones = [LEDZoneState() for _ in range(style.zone_count)]
            else:
                self.state.zones = []
            self._notify_state_changed()

    def tick(self) -> List[Tuple[int, int, int]]:
        """Advance animation one tick and return computed per-segment colors.

        Dispatches to the mode-specific algorithm based on state.mode.
        Called by the controller on a ~30ms timer.

        Returns:
            List of (R, G, B) tuples, one per segment.
        """
        mode = self.state.mode
        if mode == LEDMode.STATIC:
            colors = self._tick_static()
        elif mode == LEDMode.BREATHING:
            colors = self._tick_breathing()
        elif mode == LEDMode.COLORFUL:
            colors = self._tick_colorful()
        elif mode == LEDMode.RAINBOW:
            colors = self._tick_rainbow()
        elif mode == LEDMode.TEMP_LINKED:
            colors = self._tick_temp_linked()
        elif mode == LEDMode.LOAD_LINKED:
            colors = self._tick_load_linked()
        else:
            colors = [(0, 0, 0)] * self.state.segment_count

        if self.on_colors_updated:
            self.on_colors_updated(colors)

        return colors

    # -- Effect algorithms (ported from FormLED.cs) --

    def _tick_static(self) -> List[Tuple[int, int, int]]:
        """DSCL_Timer: all segments = user color.

        From FormLED.cs line 7619.
        """
        r, g, b = self.state.color
        return [(r, g, b)] * self.state.segment_count

    def _tick_breathing(self) -> List[Tuple[int, int, int]]:
        """DSHX_Timer: pulse brightness, period=66 ticks.

        From FormLED.cs line 7709:
            First half  (0-32):  brightness ramps UP
            Second half (33-65): brightness ramps DOWN
            Final = 80% animated + 20% base
        """
        timer = self.state.rgb_timer
        period = 66
        half = period // 2  # 33

        if timer < half:
            factor = timer / half
        else:
            factor = (period - 1 - timer) / half

        # Blend: 80% animated + 20% base (from C#)
        r, g, b = self.state.color
        anim_r = int(r * factor * 0.8 + r * 0.2)
        anim_g = int(g * factor * 0.8 + g * 0.2)
        anim_b = int(b * factor * 0.8 + b * 0.2)

        self.state.rgb_timer = (timer + 1) % period

        return [(anim_r, anim_g, anim_b)] * self.state.segment_count

    def _tick_colorful(self) -> List[Tuple[int, int, int]]:
        """QCJB_Timer: 6-phase color gradient cycle, period=168 ticks.

        From FormLED.cs line 8005:
            Phase 0 (0-27):    Red→Yellow     (G increases)
            Phase 1 (28-55):   Yellow→Green   (R decreases)
            Phase 2 (56-83):   Green→Cyan     (B increases)
            Phase 3 (84-111):  Cyan→Blue      (G decreases)
            Phase 4 (112-139): Blue→Magenta   (R increases)
            Phase 5 (140-167): Magenta→Red    (B decreases)
        """
        timer = self.state.rgb_timer
        period = 168
        phase_len = 28

        phase = timer // phase_len
        offset = timer % phase_len
        t = int(255 * offset / (phase_len - 1)) if phase_len > 1 else 0

        if phase == 0:    # Red → Yellow
            r, g, b = 255, t, 0
        elif phase == 1:  # Yellow → Green
            r, g, b = 255 - t, 255, 0
        elif phase == 2:  # Green → Cyan
            r, g, b = 0, 255, t
        elif phase == 3:  # Cyan → Blue
            r, g, b = 0, 255 - t, 255
        elif phase == 4:  # Blue → Magenta
            r, g, b = t, 0, 255
        else:             # Magenta → Red
            r, g, b = 255, 0, 255 - t

        self.state.rgb_timer = (timer + 1) % period

        return [(r, g, b)] * self.state.segment_count

    def _tick_rainbow(self) -> List[Tuple[int, int, int]]:
        """CHMS_Timer: 768-entry RGB table with per-segment offset.

        From FormLED.cs line 9212:
            Each segment gets offset index into rainbow table.
            Timer advances by 4 each tick.
        """
        from ..led_device import get_rgb_table
        table = get_rgb_table()
        timer = self.state.rgb_timer
        seg_count = self.state.segment_count
        table_len = len(table)  # 768

        colors = []
        for i in range(seg_count):
            # Each segment offset by position in table
            idx = (timer + i * table_len // max(seg_count, 1)) % table_len
            colors.append(table[idx])

        # Advance by 4 per tick (from C#: rgbTimer = (rgbTimer + 4) % 768)
        self.state.rgb_timer = (timer + 4) % table_len

        return colors

    def _tick_temp_linked(self) -> List[Tuple[int, int, int]]:
        """WDLD_Timer: color from temperature thresholds.

        From FormLED.cs line 9377:
            <30°C=cyan, 30-49=green, 50-69=yellow, 70-89=orange, ≥90=red
        """
        from ..led_device import TEMP_COLOR_HIGH, TEMP_COLOR_THRESHOLDS, color_for_value

        source = self.state.temp_source
        temp = self._metrics.get(f"{source}_temp", 0)
        color = color_for_value(temp, TEMP_COLOR_THRESHOLDS, TEMP_COLOR_HIGH)
        return [color] * self.state.segment_count

    def _tick_load_linked(self) -> List[Tuple[int, int, int]]:
        """FZLD_Timer: color from CPU/GPU load thresholds.

        From FormLED.cs line 9824:
            Same thresholds as temperature but for utilization %.
        """
        from ..led_device import LOAD_COLOR_HIGH, LOAD_COLOR_THRESHOLDS, color_for_value

        source = self.state.load_source
        load = self._metrics.get(f"{source}_load", 0)
        color = color_for_value(load, LOAD_COLOR_THRESHOLDS, LOAD_COLOR_HIGH)
        return [color] * self.state.segment_count

    def _notify_state_changed(self) -> None:
        """Notify observers of state change."""
        if self.on_state_changed:
            self.on_state_changed(self.state)
