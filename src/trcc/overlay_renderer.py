"""
OverlayRenderer - Renders system metrics overlay on theme backgrounds.

This module handles rendering text overlays (time, date, CPU temp, etc.)
onto theme backgrounds for LCD display.
"""

import os

from PIL import Image, ImageDraw, ImageFont

# Try to import format_metric for system info display
try:
    from trcc.system_info import format_metric
except ImportError:
    def format_metric(metric, value, time_format=0, date_format=0, temp_unit=0):
        """Fallback format_metric when system_info not available."""
        if 'temp' in metric:
            if temp_unit == 1:  # Fahrenheit
                return f"{value * 9/5 + 32:.0f}°F"
            return f"{value:.0f}°C"
        return str(value)

# Use centralized path definitions
from trcc.paths import FONT_SEARCH_DIRS


class OverlayRenderer:
    """
    Renders system metrics overlay on theme backgrounds.

    Supports:
    - Background images from themes
    - Theme masks (partial overlays)
    - Text overlays with customizable position, color, font
    - Time/date with multiple format options
    - Hardware metrics (CPU, GPU, etc.)
    """

    # Base resolution for scaling (most common device)
    BASE_RESOLUTION = 320

    def __init__(self, width=320, height=320):
        """
        Initialize renderer.

        Args:
            width: LCD width in pixels
            height: LCD height in pixels
        """
        self.width = width
        self.height = height
        self.config = {}
        self.background = None
        self.theme_mask = None
        self.theme_mask_position = (0, 0)
        self.theme_mask_visible = True  # Windows: isDrawMbImage / SetDrawMengBan
        self.font_cache = {}
        self.flash_skip_index = -1  # Windows shanPingCount: skip this element during render

        # Format settings (matching Windows TRCC UCXiTongXianShiSub.cs)
        # Time: 0=HH:mm, 1=hh:mm AM/PM, 2=HH:mm (same as 0)
        # Date: 0=yyyy/MM/dd, 1=yyyy/MM/dd, 2=dd/MM/yyyy, 3=MM/dd, 4=dd/MM
        # Temp: 0=Celsius (°C), 1=Fahrenheit (°F)
        self.time_format = 0
        self.date_format = 0
        self.temp_unit = 0

        # Dynamic font/coordinate scaling
        # Stores the resolution the config was designed for
        self._config_resolution = (width, height)
        self._scale_enabled = True  # Enable scaling by default

    def set_resolution(self, width, height):
        """Update LCD resolution."""
        self.width = width
        self.height = height
        # Clear font cache as sizes will change with new scale
        self.font_cache = {}
        # Clear background as it needs to be resized
        self.background = None

    def set_config_resolution(self, width, height):
        """Set the resolution the current config was designed for.

        Used for dynamic font/coordinate scaling when displaying a config
        designed for one resolution on a device with a different resolution.

        Args:
            width: Config's target width
            height: Config's target height
        """
        self._config_resolution = (width, height)

    def set_scale_enabled(self, enabled):
        """Enable or disable dynamic font/coordinate scaling."""
        self._scale_enabled = enabled
        # Clear font cache when toggling
        self.font_cache = {}

    def _get_scale_factor(self):
        """Calculate scale factor from config resolution to display resolution.

        Uses the smaller dimension (usually the same for square LCDs) to
        calculate a uniform scale factor.

        Returns:
            Float scale factor (1.0 = no scaling)
        """
        if not self._scale_enabled:
            return 1.0

        cfg_w, cfg_h = self._config_resolution
        # Use minimum dimension for uniform scaling
        cfg_size = min(cfg_w, cfg_h)
        disp_size = min(self.width, self.height)

        if cfg_size <= 0:
            return 1.0

        return disp_size / cfg_size

    def set_format_options(self, time_format=0, date_format=0, temp_unit=0):
        """
        Set time, date, and temperature format options.

        Args:
            time_format: 0=HH:mm (24h), 1=hh:mm AM/PM (12h), 2=HH:mm
            date_format: 0=yyyy/MM/dd, 1=yyyy/MM/dd, 2=dd/MM/yyyy, 3=MM/dd, 4=dd/MM
            temp_unit: 0=Celsius (°C), 1=Fahrenheit (°F)
        """
        self.time_format = time_format
        self.date_format = date_format
        self.temp_unit = temp_unit

    def set_temp_unit(self, unit: int):
        """
        Set temperature unit (Windows UCXiTongXianShiTable button0 toggle).

        Args:
            unit: 0=Celsius (°C), 1=Fahrenheit (°F)
        """
        self.temp_unit = unit

    def set_config(self, config):
        """
        Set overlay configuration.

        Args:
            config: Dict mapping element names to their settings.
                   Each element has: x, y, color, font, metric/text, enabled
        """
        self.config = config

    def set_background(self, image):
        """
        Set background image.

        Optimized for video playback - skips copy/resize if image is
        already the correct size (VideoPlayer pre-resizes frames).

        Args:
            image: PIL Image or None to clear
        """
        if image is None:
            self.background = None
            return
        # Skip resize if already correct size (video frames are pre-sized)
        if image.size == (self.width, self.height):
            self.background = image  # Use directly, no copy needed for video
        else:
            self.background = image.copy().resize(
                (self.width, self.height), Image.Resampling.LANCZOS
            )

    def set_theme_mask(self, image, position=None):
        """
        Set theme mask overlay.

        Masks are kept at original size (not stretched) and positioned
        at the bottom by default for partial overlays.

        Args:
            image: PIL Image with alpha channel, or None to clear
            position: (x, y) tuple, or None for auto-position at bottom
        """
        if image is None:
            self.theme_mask = None
            self.theme_mask_position = (0, 0)
            return

        if image.mode != 'RGBA':
            image = image.convert('RGBA')

        self.theme_mask = image

        if position is not None:
            self.theme_mask_position = position
        elif image.height < self.height:
            # Position partial masks at bottom
            self.theme_mask_position = (0, self.height - image.height)
        else:
            self.theme_mask_position = (0, 0)

    def get_font(self, size, bold=False, font_name=None):
        """
        Get font by name with fallback chain.

        If font_name is given, resolves it via fc-match. Otherwise uses
        the default fallback chain (bundled → user → system).

        Args:
            size: Font size in points
            bold: Whether to use bold variant
            font_name: Font family name (e.g. 'DejaVu Sans'), or None for default

        Returns:
            PIL ImageFont instance
        """
        key = (size, bold, font_name)
        if key in self.font_cache:
            return self.font_cache[key]

        # Try resolving by name first (user-picked fonts)
        if font_name and font_name != 'Microsoft YaHei':
            path = self._resolve_font_path(font_name, bold)
            if path:
                self.font_cache[key] = ImageFont.truetype(path, size)
                return self.font_cache[key]

        # Build font search list from centralized FONT_SEARCH_DIRS
        # Priority: bundled MSYH → Noto CJK → Noto Sans → DejaVu
        bold_suffix = '-Bold' if bold else ''
        bold_style = 'Bold' if bold else 'Regular'
        msyh_name = 'MSYHBD.TTC' if bold else 'MSYH.TTC'
        msyh_lower = msyh_name.lower()

        # Preferred font filenames in priority order
        font_filenames = [
            msyh_name, msyh_lower,                                    # Microsoft YaHei (bundled)
            'NotoSansCJK-VF.ttc', 'NotoSansCJK-Regular.ttc',        # Noto CJK
            'NotoSans[wght].ttf', f'NotoSans-{bold_style}.ttf',    # Noto Sans
            f'DejaVuSans{bold_suffix}.ttf',                          # DejaVu
        ]

        paths = []
        for font_dir in FONT_SEARCH_DIRS:
            for fname in font_filenames:
                paths.append(os.path.join(font_dir, fname))

        for path in paths:
            if os.path.exists(path):
                self.font_cache[key] = ImageFont.truetype(path, size)
                return self.font_cache[key]

        # Ultimate fallback
        self.font_cache[key] = ImageFont.load_default()
        return self.font_cache[key]

    def _resolve_font_path(self, font_name, bold=False):
        """Resolve font family name to file path using fc-match."""
        import subprocess
        try:
            style = 'Bold' if bold else 'Regular'
            result = subprocess.run(
                ['fc-match', f'{font_name}:style={style}', '--format=%{file}'],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0 and result.stdout and os.path.exists(result.stdout):
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Manual scan using centralized cross-distro font search dirs
        name_lower = font_name.lower().replace(' ', '')
        for font_dir in FONT_SEARCH_DIRS:
            if not os.path.isdir(font_dir):
                continue
            for fname in os.listdir(font_dir):
                if name_lower in fname.lower().replace(' ', ''):
                    return os.path.join(font_dir, fname)

        return None

    def render(self, metrics=None):
        """
        Render the overlay with current settings.

        Optimized for video playback - returns background directly when
        there's nothing to overlay (no mask, no config).

        Args:
            metrics: Dict of metric values (from system_info.get_all_metrics())

        Returns:
            PIL Image with overlay rendered
        """
        metrics = metrics or {}

        # Fast path: no overlays, just return background as-is (video playback optimization)
        has_overlays = (self.theme_mask and self.theme_mask_visible) or (self.config and isinstance(self.config, dict))
        if not has_overlays and self.background:
            return self.background

        # Create base image
        if self.background is None:
            # Transparent background for themes without 00.png
            img = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
        else:
            img = self.background.copy()
            if img.mode != 'RGBA':
                img = img.convert('RGBA')

        # Apply theme mask (Windows: isDrawMbImage check)
        if self.theme_mask and self.theme_mask_visible:
            # Scale mask and position if needed
            scale = self._get_scale_factor()
            if abs(scale - 1.0) > 0.01:  # Scaling enabled and needed
                mask_w = int(self.theme_mask.width * scale)
                mask_h = int(self.theme_mask.height * scale)
                scaled_mask = self.theme_mask.resize(
                    (mask_w, mask_h), Image.Resampling.LANCZOS)
                pos_x = int(self.theme_mask_position[0] * scale)
                pos_y = int(self.theme_mask_position[1] * scale)
                img.paste(scaled_mask, (pos_x, pos_y), scaled_mask)
            else:
                img.paste(self.theme_mask, self.theme_mask_position, self.theme_mask)

        # Convert to RGB before drawing text (matches Windows GenerateImage pattern).
        # Drawing text on RGBA causes PIL to replace alpha at anti-aliased edges;
        # when pil_to_pixmap composites RGBA onto black, this creates dark fringes.
        # Windows draws text on an RGB GDI+ Bitmap — no alpha issues.
        if img.mode == 'RGBA':
            img = img.convert('RGB')

        # Draw text overlays
        draw = ImageDraw.Draw(img)

        if not self.config or not isinstance(self.config, dict):
            return img

        # Get scale factor for dynamic font/coordinate scaling
        scale = self._get_scale_factor()

        for elem_idx, (key, cfg) in enumerate(self.config.items()):
            if not isinstance(cfg, dict) or not cfg.get('enabled', True):
                continue
            # Windows shanPingCount: skip this element to create flash/blink effect
            if elem_idx == self.flash_skip_index:
                continue

            # Get base values
            base_x = cfg.get('x', 10)
            base_y = cfg.get('y', 10)
            font_cfg = cfg.get('font', {})
            base_font_size = font_cfg.get('size', 24) if isinstance(font_cfg, dict) else 24
            color = cfg.get('color', '#FFFFFF')

            # Apply scaling to coordinates and font size
            x = int(base_x * scale)
            y = int(base_y * scale)
            font_size = max(8, int(base_font_size * scale))  # Min 8pt for readability

            # Get text to render
            if 'text' in cfg:
                text = str(cfg['text'])
            elif 'metric' in cfg:
                metric_name = cfg['metric']
                if metric_name in metrics:
                    time_fmt = cfg.get('time_format', self.time_format)
                    date_fmt = cfg.get('date_format', self.date_format)
                    # Global temp_unit always wins (user's C/F toggle is authoritative)
                    text = format_metric(metric_name, metrics[metric_name],
                                        time_fmt, date_fmt, self.temp_unit)
                else:
                    text = "N/A"
            else:
                continue

            bold = font_cfg.get('style') == 'bold' if isinstance(font_cfg, dict) else False
            font_name = font_cfg.get('name') if isinstance(font_cfg, dict) else None
            font = self.get_font(font_size, bold=bold, font_name=font_name)
            # Use center anchor - Windows TRCC uses center coordinates
            draw.text((x, y), text, fill=color, font=font, anchor='mm')

        return img

    def set_mask_visible(self, visible):
        """Toggle mask visibility without destroying it (Windows SetDrawMengBan)."""
        self.theme_mask_visible = visible

    def clear(self):
        """Clear all settings."""
        self.config = {}
        self.background = None
        self.theme_mask = None
        self.theme_mask_position = (0, 0)
        self.theme_mask_visible = True
