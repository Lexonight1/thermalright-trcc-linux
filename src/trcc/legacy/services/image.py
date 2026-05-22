"""Image processing service — delegates to Renderer ABC (QtRenderer)."""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from ..core.models import JPEG_MAX_BYTES
from ..core.ports import Renderer

log = logging.getLogger(__name__)


class ImageService:
    """Stateless image processing — delegates to the active Renderer."""

    _renderer: ClassVar[Renderer | None] = None

    @classmethod
    def set_renderer(cls, renderer: Renderer) -> None:
        """Set the active renderer (called once at startup)."""
        cls._renderer = renderer

    @classmethod
    def renderer(cls) -> Renderer:
        """Get the active renderer (must be set via set_renderer first)."""
        if cls._renderer is None:
            raise RuntimeError(
                "ImageService.set_renderer() must be called before use. "
                "Use ControllerBuilder to wire dependencies.")
        return cls._renderer

    # ── Encoding (hot path) ───────────────────────────────────────

    @staticmethod
    def to_rgb565(img: Any, byte_order: str = '>') -> bytes:
        """Encode surface to RGB565 bytes."""
        return ImageService.renderer().encode_rgb565(img, byte_order)

    @staticmethod
    def to_jpeg(img: Any, quality: int = 95,
                max_size: int = JPEG_MAX_BYTES) -> bytes:
        """Encode surface to JPEG bytes with size constraint."""
        return ImageService.renderer().encode_jpeg(img, quality, max_size)

    # ── Adjustments (hot path) ────────────────────────────────────

    @staticmethod
    def apply_rotation(image: Any, rotation: int) -> Any:
        """Apply display rotation (0/90/180/270)."""
        return ImageService.renderer().apply_rotation(image, rotation)

    @staticmethod
    def apply_brightness(image: Any, percent: int) -> Any:
        """Apply brightness (0-100, 100 = unchanged)."""
        return ImageService.renderer().apply_brightness(image, percent)

    # ── Surface creation ──────────────────────────────────────────

    @staticmethod
    def solid_color(r: int, g: int, b: int, w: int, h: int) -> Any:
        """Create a solid-color surface."""
        return ImageService.renderer().create_surface(w, h, (r, g, b))

    @staticmethod
    def resize(img: Any, w: int, h: int) -> Any:
        """Resize surface to target dimensions."""
        return ImageService.renderer().resize(img, w, h)

    @staticmethod
    def open_and_resize(path: Any, w: int, h: int) -> Any:
        """Open image file, resize to target dimensions."""
        rnd = ImageService.renderer()
        img = rnd.open_image(path)
        return rnd.resize(img, w, h)

    # Square resolutions that skip the 90° device pre-rotation.
    _SQUARE_NO_ROTATE = {(240, 240), (320, 320), (480, 480)}

    @staticmethod
    def byte_order_for(protocol: str, resolution: tuple[int, int],
                       fbl: int | None = None) -> str:
        """Determine RGB565 byte order for a device."""
        from ..core.encoding import byte_order_for
        return byte_order_for(protocol, resolution, fbl)

    @staticmethod
    def apply_device_rotation(image: Any,
                              resolution: tuple[int, int]) -> Any:
        """Apply device-level pre-rotation for non-square displays."""
        if resolution in ImageService._SQUARE_NO_ROTATE:
            return image
        return ImageService.renderer().apply_rotation(image, 90)

    @staticmethod
    def encode_for_device(img: Any, protocol: str,
                          resolution: tuple[int, int],
                          fbl: int | None,
                          use_jpeg: bool,
                          encode_angle: int = 0) -> tuple[Any, bytes]:
        """Encode surface for LCD device — JPEG or RGB565.

        Returns ``(rotated_surface, wire_bytes)``: the surface in device
        orientation (post-rotation, post-resize, pre-RGB565-mirror) AND
        the encoded byte payload that goes over USB.  The Observer pattern
        relies on the surface to publish ``Topic.FRAME`` so every consumer
        (GUI preview, API WebSocket) sees pixel-for-pixel what the device
        sees — single source of truth.

        encode_angle: device-level rotation (C# RotateImg) computed by
        DisplayService._encode_angle(). Applied before encoding so the
        device firmware receives the correct physical orientation.
        For non-square rotated displays, this converts the portrait
        canvas back to the device's expected orientation.
        """
        from ..core.models import get_profile

        rnd = ImageService.renderer()
        profile = get_profile(fbl) if fbl is not None else None

        # Device encode rotation — converts canvas to device orientation.
        # For non-square 90/270, this also handles the dimension swap
        # (480x1280 → 1280x480) so the separate resize is only needed
        # when encode_angle doesn't produce native dims.
        if encode_angle:
            img = rnd.apply_rotation(img, encode_angle)

        # Ensure image matches device native resolution after rotation.
        # Only needed for non-square displays where rotation or canvas_size
        # may have changed dimensions.
        native_w, native_h = resolution
        if native_w and native_h and native_w != native_h:
            img_w, img_h = rnd.surface_size(img)
            if (img_w, img_h) != (native_w, native_h):
                img = rnd.resize(img, native_w, native_h)

        # `img` is now what the device sees — pass it back so the caller
        # can publish Topic.FRAME with the same surface that becomes USB
        # bytes below.  RGB565 portrait pre-rotation (`profile.rotate`)
        # is wire-format-only so it does NOT alter the published surface.
        device_surface = img

        if use_jpeg or (profile and profile.jpeg):
            return device_surface, ImageService.to_jpeg(img)

        if profile and profile.rotate:
            img = rnd.apply_rotation(img, 90)
        byte_order = profile.byte_order if profile else '<'
        return device_surface, ImageService.to_rgb565(img, byte_order)

    # ── ANSI preview (CLI cold path) ──────────────────────────────

    @staticmethod
    def to_ansi(img: Any, cols: int = 60) -> str:
        """Render surface as ANSI true-color block art for terminal preview."""
        rnd = ImageService.renderer()
        w, h = rnd.surface_size(img)
        rows = max(1, int(cols * h / w))
        rows += rows % 2
        pixels = rnd.get_pixels_rgb(img, cols, rows)

        lines: list[str] = []
        for y in range(0, rows, 2):
            parts: list[str] = []
            for x in range(cols):
                tr, tg, tb = pixels[y][x]
                br, bg_, bb = pixels[y + 1][x] if y + 1 < rows else (0, 0, 0)
                parts.append(
                    f'\033[38;2;{tr};{tg};{tb}m'
                    f'\033[48;2;{br};{bg_};{bb}m\u2580'
                )
            lines.append(''.join(parts) + '\033[0m')
        return '\n'.join(lines)

    @staticmethod
    def to_ansi_cursor_home(img: Any, cols: int = 60) -> str:
        """Same as to_ansi() but prefixed with cursor-home escape."""
        return '\033[H' + ImageService.to_ansi(img, cols)

    # Metric groups for ANSI dashboard
    _METRIC_GROUPS: dict[str, tuple[str, list[str], tuple[int, int, int]]] = {
        'cpu':  ('CPU',  ['cpu_temp', 'cpu_percent', 'cpu_freq', 'cpu_power'], (0, 200, 255)),
        'gpu':  ('GPU',  ['gpu_temp', 'gpu_usage', 'gpu_clock', 'gpu_power'], (0, 255, 100)),
        'mem':  ('MEM',  ['mem_temp', 'mem_percent', 'mem_clock', 'mem_available'], (255, 200, 0)),
        'disk': ('DISK', ['disk_temp', 'disk_activity', 'disk_read', 'disk_write'], (200, 100, 255)),
        'net':  ('NET',  ['net_up', 'net_down', 'net_total_up', 'net_total_down'], (100, 255, 200)),
        'fan':  ('FAN',  ['fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2'], (255, 80, 80)),
        'time': ('TIME', ['date', 'time', 'weekday'], (180, 180, 255)),
    }

    @staticmethod
    def metrics_to_ansi(metrics: Any, cols: int = 60,
                        group: str | None = None) -> str:
        """Render HardwareMetrics as ANSI terminal dashboard."""
        from .system import SystemService

        groups = ImageService._METRIC_GROUPS
        if group:
            key = group.lower()
            if key not in groups:
                return f"Unknown group '{group}'. Choose: {', '.join(groups)}"
            items = {key: groups[key]}
        else:
            items = groups

        line_h = 20
        padding = 8
        total_lines = 0
        for _, (_, fields, _) in items.items():
            total_lines += 1
            for f in fields:
                if f in metrics._populated:
                    total_lines += 1
        h = max(40, padding + total_lines * line_h + padding)
        w = int(cols * 2.5)

        rnd = ImageService.renderer()
        font_label = rnd.get_font(13, bold=True)
        font_value = rnd.get_font(12)
        surf = rnd.create_surface(w, h, (10, 10, 30))

        y = padding
        for _, (label, fields, color) in items.items():
            color_str = '#{:02x}{:02x}{:02x}'.format(*color)
            rnd.draw_text(surf, 8, y, label, color_str, font_label, anchor='lt')
            y += line_h
            for f in fields:
                if f not in metrics._populated:
                    continue
                v = getattr(metrics, f, 0.0)
                formatted = SystemService.format_metric(f, v)
                name = f.replace('_', ' ').title()
                rnd.draw_text(surf, 16, y, f'{name}:', '#8c8ca0', font_value, anchor='lt')
                rnd.draw_text(surf, w // 2, y, formatted, '#dcdcdc', font_value, anchor='lt')
                if 'percent' in f or 'usage' in f or 'activity' in f:
                    bar_x = w * 3 // 4
                    bar_w = int((w - bar_x - 8) * min(v, 100) / 100)
                    rnd.fill_rect(surf, bar_x, y + 2, bar_w, 12, color)
                    rnd.draw_rect_outline(surf, bar_x, y + 2,
                                          w - 8 - bar_x, 12, (50, 50, 50))
                y += line_h

        return ImageService.to_ansi(surf, cols=cols)
