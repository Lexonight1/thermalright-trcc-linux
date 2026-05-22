"""Overlay models — DC format DTOs, overlay element config, builders."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum

from .sensor import HARDWARE_METRICS

# =============================================================================
# DC File Format DTOs (config1.dc overlay configuration)
# =============================================================================

@dataclass(frozen=True, slots=True)
class FontConfig:
    """Font configuration from .dc file."""
    name: str
    size: float
    style: int      # 0=Regular, 1=Bold, 2=Italic
    unit: int       # GraphicsUnit
    charset: int
    color_argb: tuple  # (alpha, red, green, blue)


@dataclass(frozen=True, slots=True)
class ElementConfig:
    """Element position and font config."""
    x: int
    y: int
    font: FontConfig | None = None
    enabled: bool = True


class OverlayMode(IntEnum):
    """Display element mode — matches Windows myMode values 0..4."""
    HARDWARE = 0
    TIME = 1
    WEEKDAY = 2
    DATE = 3
    CUSTOM = 4


@dataclass(slots=True)
class DisplayElement:
    """
    Display element from UCXiTongXianShiSub (time, date, weekday, hardware info, custom text).

    myMode values:
        0 = Hardware info (CPU/GPU metrics)
        1 = Time
        2 = Weekday (SUN, MON, TUE, etc.)
        3 = Date
        4 = Custom text

    myModeSub values (format variants):
        For mode 1 (Time):
            0 = HH:mm (24-hour)
            1 = hh:mm AM/PM (12-hour)
            2 = HH:mm (same as 0)
        For mode 3 (Date):
            0 = yyyy/MM/dd
            1 = yyyy/MM/dd (same as 0)
            2 = dd/MM/yyyy
            3 = MM/dd
            4 = dd/MM
    """
    mode: int           # Display type (0=hardware, 1=time, 2=weekday, 3=date, 4=custom)
    mode_sub: int       # Format variant
    x: int              # X position
    y: int              # Y position
    main_count: int = 0     # For hardware info - sensor category
    sub_count: int = 0      # For hardware info - specific sensor
    font_name: str = "Microsoft YaHei"
    font_size: float = 24.0
    font_style: int = 0  # 0=Regular, 1=Bold, 2=Italic
    font_unit: int = 3   # GraphicsUnit.Point
    font_charset: int = 134  # GB2312 (Windows default: new Font("微软雅黑", 36f, 0, 3, 134))
    color_argb: tuple = (255, 255, 255, 255)  # ARGB
    text: str = ""      # Custom text content

    @property
    def mode_name(self) -> str:
        """Get human-readable mode name."""
        try:
            return OverlayMode(self.mode).name.lower()
        except ValueError:
            return f'unknown_{self.mode}'

    @property
    def color_hex(self) -> str:
        """Get color as hex string."""
        _, r, g, b = self.color_argb
        return f"#{r:02x}{g:02x}{b:02x}"


# =============================================================================
# Overlay element types — UI/grid-level representation.
# =============================================================================

class OverlayElementType(Enum):
    """Type of overlay element."""
    HARDWARE = 0    # CPU temp, GPU usage, etc.
    TIME = 1        # Current time
    WEEKDAY = 2     # Day of week
    DATE = 3        # Current date
    TEXT = 4        # Custom text


@dataclass(slots=True)
class OverlayElement:
    """
    Single overlay element configuration.

    Matches Windows UCXiTongXianShi element data.
    """
    element_type: OverlayElementType = OverlayElementType.TEXT
    enabled: bool = True
    x: int = 10
    y: int = 10
    color: tuple[int, int, int] = (255, 255, 255)
    font_size: int = 16
    font_name: str = "Microsoft YaHei"

    # Hardware element specific
    metric_key: str | None = None  # e.g., 'cpu_temp', 'gpu_usage'
    format_string: str = "{value}"    # e.g., "CPU: {value}°C"

    # Text element specific
    text: str = ""


@dataclass(slots=True)
class OverlayElementConfig:
    """Overlay grid element config — UI-level representation.

    Replaces the untyped dict from _default_element_config().
    Used by OverlayGridPanel._configs and OverlayElementWidget.config.
    """
    mode: OverlayMode = OverlayMode.TIME
    mode_sub: int = 0
    x: int = 100
    y: int = 100
    main_count: int = 0
    sub_count: int = 1
    color: str = '#FFFFFF'
    font_name: str = 'Microsoft YaHei'
    font_size: int = 36
    font_style: int = 0
    text: str = ''


# =============================================================================
# Overlay config builders (CLI/API metric spec → OverlayService config dict)
# =============================================================================

# Valid metric keys for overlay elements (hardware sensors + time/date/weekday).
VALID_OVERLAY_KEYS: frozenset[str] = frozenset(
    set(HARDWARE_METRICS.values()) | {'time', 'date', 'weekday'}
)


def parse_metric_spec(
    spec: str,
    index: int,
    default_color: str = 'ffffff',
    default_size: int = 14,
    default_font: str = 'Microsoft YaHei',
    default_style: str = 'regular',
) -> tuple[str, dict]:
    """Parse a metric spec string into an overlay config element.

    Format: ``key:x,y[:color[:size[:font[:style]]]]``

    Examples:
        ``"gpu_temp:10,20"``                      → uses all defaults
        ``"cpu_percent:10,50:ff0000"``             → red, default size
        ``"time:150,10:ffffff:24"``                → white, 24px
        ``"gpu_temp:10,20:ff0000:18:Arial:bold"``  → red, 18px, Arial bold
        ``"cpu_temp:10,50::16:Courier"``            → default color, 16px, Courier

    Returns:
        (element_key, config_dict) for ``OverlayService.set_config()``.

    Raises:
        ValueError: if spec is malformed or metric key is invalid.
    """
    parts = spec.split(':')
    if len(parts) < 2:
        raise ValueError(
            f"Invalid metric spec '{spec}' — expected 'key:x,y' "
            f"(e.g. 'gpu_temp:10,20')")

    metric_key = parts[0]
    if metric_key not in VALID_OVERLAY_KEYS:
        raise ValueError(
            f"Unknown metric key '{metric_key}'. "
            f"Valid keys: {', '.join(sorted(VALID_OVERLAY_KEYS))}")

    try:
        coords = parts[1].split(',')
        x, y = int(coords[0]), int(coords[1])
    except (ValueError, IndexError) as e:
        raise ValueError(
            f"Invalid coordinates in '{spec}' — expected 'key:x,y' "
            f"(e.g. 'gpu_temp:10,20')") from e

    color = default_color
    size = default_size
    font_name = default_font
    style = default_style
    if len(parts) >= 3 and parts[2]:
        color = parts[2]
    if len(parts) >= 4 and parts[3]:
        try:
            size = int(parts[3])
        except ValueError as e:
            raise ValueError(
                f"Invalid size in '{spec}' — expected integer") from e
    if len(parts) >= 5 and parts[4]:
        font_name = parts[4]
    if len(parts) >= 6 and parts[5]:
        style = parts[5]

    element_key = f"cli_elem_{index}"
    config: dict = {
        'x': x,
        'y': y,
        'color': f"#{color.lstrip('#')}",
        'font': {
            'size': size,
            'style': style,
            'name': font_name,
        },
        'enabled': True,
        'metric': metric_key,
    }

    # Add format fields for time/date/temp metrics
    if metric_key == 'time':
        config['time_format'] = 0
    elif metric_key == 'date':
        config['date_format'] = 0
    elif metric_key.endswith('_temp'):
        config['temp_unit'] = 0

    return element_key, config


def build_overlay_config(
    metrics: list[str],
    *,
    default_color: str = 'ffffff',
    default_font_size: int = 14,
    default_font: str = 'Microsoft YaHei',
    default_style: str = 'regular',
    temp_unit: int = 0,
    time_format: int = 0,
    date_format: int = 0,
) -> dict:
    """Build an overlay config dict from CLI metric spec strings.

    Args:
        metrics: List of spec strings (``"key:x,y[:color[:size]]"``).
        default_color: Global hex color for elements without per-metric override.
        default_font_size: Global font size (px).
        default_font: Global font family name.
        default_style: Global font style (``'regular'`` or ``'bold'``).
        temp_unit: Temperature unit (0=Celsius, 1=Fahrenheit).
        time_format: Time format (0=24h HH:MM, 1=12h hh:MM).
        date_format: Date format (0=yyyy/MM/dd, 1=same, 2=dd/MM/yyyy, etc.).

    Returns:
        Dict suitable for ``OverlayService.set_config()``.

    Raises:
        ValueError: if any metric spec is invalid.
    """
    config: dict = {}
    for i, spec in enumerate(metrics):
        key, elem = parse_metric_spec(
            spec, i, default_color, default_font_size,
            default_font, default_style)
        # Apply global format overrides
        if 'time_format' in elem:
            elem['time_format'] = time_format
        if 'date_format' in elem:
            elem['date_format'] = date_format
        if 'temp_unit' in elem:
            elem['temp_unit'] = temp_unit
        config[key] = elem
    return config


# =============================================================================
# Overlay asset mappings
# =============================================================================

# Overlay element mode → background icon asset
OVERLAY_MODE_IMAGES: dict[OverlayMode, str] = {
    OverlayMode.HARDWARE: 'overlay_mode_hardware.png',
    OverlayMode.TIME: 'overlay_mode_time.png',
    OverlayMode.WEEKDAY: 'overlay_mode_weekday.png',
    OverlayMode.DATE: 'overlay_mode_date.png',
    OverlayMode.CUSTOM: 'overlay_mode_text.png',
}

# Overlay element selection highlight asset
OVERLAY_SELECT_IMAGE = 'overlay_select.png'

# Date format mode_sub → button icon asset
DATE_FORMAT_IMAGES: dict[int, str] = {
    1: 'display_mode_date_ymd.png',
    2: 'display_mode_date_dmy.png',
    3: 'display_mode_date_md.png',
    4: 'display_mode_date_dm.png',
}

# Display mode action → icon asset
ACTION_ICON_IMAGES: dict[str, str] = {
    "Image": "display_mode_icon_image.png",
    "Video": "display_mode_icon_video.png",
    "Load": "display_mode_icon_mask.png",
    "Upload": "display_mode_icon_image.png",
    "VideoLoad": "display_mode_icon_livestream.png",
    "GIF": "display_mode_icon_gif.png",
    "Network": "display_mode_icon_network.png",
}


__all__ = [
    'ACTION_ICON_IMAGES',
    'DATE_FORMAT_IMAGES',
    'OVERLAY_MODE_IMAGES',
    'OVERLAY_SELECT_IMAGE',
    'VALID_OVERLAY_KEYS',
    'DisplayElement',
    'ElementConfig',
    'FontConfig',
    'OverlayElement',
    'OverlayElementConfig',
    'OverlayElementType',
    'OverlayMode',
    'build_overlay_config',
    'parse_metric_spec',
]
