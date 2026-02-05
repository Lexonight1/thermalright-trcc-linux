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
    def format_metric(name, value, time_fmt=0, date_fmt=0, temp_unit=0):
        """Fallback format_metric when system_info not available."""
        if 'temp' in name:
            if temp_unit == 1:  # Fahrenheit
                return f"{value * 9/5 + 32:.0f}°F"
            return f"{value:.0f}°C"
        return str(value)

# Use centralized path definitions
from trcc.paths import ASSETS_DIR


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

    def set_resolution(self, width, height):
        """Update LCD resolution."""
        self.width = width
        self.height = height
        # Clear background as it needs to be resized
        self.background = None

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

        home = os.path.expanduser("~")
        fonts_dir = os.path.join(ASSETS_DIR, "fonts")

        # Default fallback chain
        paths = [
            # Bundled fonts - Microsoft YaHei (matches Windows TRCC)
            os.path.join(fonts_dir, "MSYHBD.TTC" if bold else "MSYH.TTC"),
            os.path.join(fonts_dir, "msyhbd.ttc" if bold else "msyh.ttc"),
            # User local fonts
            f"{home}/.local/share/fonts/{'MSYHBD.TTC' if bold else 'MSYH.TTC'}",
            f"{home}/.local/share/fonts/{'msyhbd.ttc' if bold else 'msyh.ttc'}",
            f"{home}/.fonts/{'MSYHBD.TTC' if bold else 'MSYH.TTC'}",
            f"{home}/.fonts/{'msyhbd.ttc' if bold else 'msyh.ttc'}",
            # System fonts - Noto Sans CJK
            "/usr/share/fonts/google-noto-sans-cjk-vf-fonts/NotoSansCJK-VF.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            # System fonts - Noto Sans
            "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf",
            f"/usr/share/fonts/google-noto/NotoSans-{'Bold' if bold else 'Regular'}.ttf",
            f"/usr/share/fonts/truetype/noto/NotoSans-{'Bold' if bold else 'Regular'}.ttf",
            # Fallback - DejaVu
            f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
            f"/usr/share/fonts/dejavu-sans-fonts/DejaVuSans{'-Bold' if bold else ''}.ttf",
        ]

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

        # Manual scan: bundled assets, user local, /usr/local
        home = os.path.expanduser("~")
        scan_dirs = [
            os.path.join(ASSETS_DIR, "fonts"),
            os.path.join(home, '.local/share/fonts'),
            os.path.join(home, '.fonts'),
            '/usr/local/share/fonts',
        ]
        name_lower = font_name.lower().replace(' ', '')
        for font_dir in scan_dirs:
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

        for elem_idx, (key, cfg) in enumerate(self.config.items()):
            if not isinstance(cfg, dict) or not cfg.get('enabled', True):
                continue
            # Windows shanPingCount: skip this element to create flash/blink effect
            if elem_idx == self.flash_skip_index:
                continue

            x = cfg.get('x', 10)
            y = cfg.get('y', 10)
            font_cfg = cfg.get('font', {})
            font_size = font_cfg.get('size', 24) if isinstance(font_cfg, dict) else 24
            color = cfg.get('color', '#FFFFFF')

            # Get text to render
            if 'text' in cfg:
                text = str(cfg['text'])
            elif 'metric' in cfg:
                metric_name = cfg['metric']
                if metric_name in metrics:
                    time_fmt = cfg.get('time_format', self.time_format)
                    date_fmt = cfg.get('date_format', self.date_format)
                    # Per-element temp_unit or global default (Windows myModeSub pattern)
                    temp_unit = cfg.get('temp_unit', self.temp_unit)
                    text = format_metric(metric_name, metrics[metric_name],
                                        time_fmt, date_fmt, temp_unit)
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
