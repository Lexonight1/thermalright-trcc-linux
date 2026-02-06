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

        # Determine if animated
        is_animated = anim_path.exists()

        # Determine if mask-only (no background)
        is_mask_only = not bg_path.exists() and mask_path.exists()

        return cls(
            name=path.name,
            path=path,
            theme_type=ThemeType.LOCAL,
            background_path=bg_path if bg_path.exists() else None,
            mask_path=mask_path if mask_path.exists() else None,
            thumbnail_path=thumb_path if thumb_path.exists() else (bg_path if bg_path.exists() else None),
            animation_path=anim_path if anim_path.exists() else None,
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
