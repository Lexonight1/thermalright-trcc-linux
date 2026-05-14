"""Overlay rendering service — config, mask, metrics → composited image.

Renderer-agnostic — delegates all image ops to the Renderer ABC.
Returns native surfaces (QImage).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..core._logging import tagged_logger
from ..core.models import HardwareMetrics
from ..core.ports import Renderer
from .system import SystemService

log = logging.getLogger(__name__)

# Metrics computed inside format_metric() from datetime.now() — they aren't
# tied to a hardware sensor so they bypass the HardwareMetrics._populated
# gate. Anything sensor-backed must be in _populated to render.
_COMPUTED_METRICS: frozenset[str] = frozenset({
    'date', 'time', 'weekday',
    'date_year', 'date_month', 'date_day',
    'time_hour', 'time_minute', 'time_second',
    'day_of_week',
})


class OverlayService:
    """Overlay rendering: config, mask, metrics → composited image.

    Supports:
    - Background images from themes
    - Theme masks (partial overlays)
    - Text overlays with customizable position, color, font
    - Time/date with multiple format options
    - Hardware metrics (CPU, GPU, etc.)
    - Dynamic font/coordinate scaling across resolutions
    - Overlay caching: re-render text+mask only when inputs change
    """

    # Base resolution for scaling (most common device)
    BASE_RESOLUTION = 320

    def __init__(self, width: int = 0, height: int = 0,
                 renderer: Renderer | None = None,
                 load_config_json_fn: Any = None,
                 dc_config_cls: Any = None,
                 device_label: str = '') -> None:
        # Per-device child logger
        self.log: logging.Logger = tagged_logger(__name__, device_label)

        # Rendering backend (Strategy pattern) — must be injected
        if renderer is None:
            raise RuntimeError(
                "OverlayService requires a Renderer instance. "
                "Use ControllerBuilder to wire dependencies.")
        self._renderer: Renderer = renderer
        self._load_config_json_fn = load_config_json_fn
        self._dc_config_cls = dc_config_cls

        # Rendering state (public — tests + callers access these directly)
        self.width = width
        self.height = height
        self.config: dict = {}
        self.background: Any = None
        self.theme_mask: Any = None
        self.theme_mask_position: tuple[int, int] = (0, 0)
        self.theme_mask_visible: bool = True  # Windows: isDrawMbImage
        self.flash_skip_index: int = -1  # Windows shanPingCount

        # Format settings (matching Windows TRCC UCXiTongXianShiSub.cs)
        # Time: 0=HH:mm, 1=hh:mm AM/PM, 2=HH:mm (same as 0)
        # Date: 0=yyyy/MM/dd, 1=yyyy/MM/dd, 2=dd/MM/yyyy, 3=MM/dd, 4=dd/MM
        # Temp: 0=Celsius (°C), 1=Fahrenheit (°F)
        self.time_format: int = 0
        self.date_format: int = 0
        self.temp_unit: int = 0
        # Issue #141 — weekday abbreviations follow Settings.lang.
        # Set via ``set_lang()`` from the language signal-chain; default
        # English so the legacy unset path keeps working.
        self.lang: str = 'en'

        # Dynamic font/coordinate scaling
        self._config_resolution: tuple[int, int] = (width, height)
        self._scale_enabled: bool = True

        # Service-only state
        self._enabled: bool = False
        self._metrics: HardwareMetrics = HardwareMetrics()
        self._dc_data: dict[str, Any] | None = None

        # Overlay cache: transparent layer with text + mask, re-rendered
        # only when inputs change (~1/sec).  Per-frame cost drops from
        # full text render to single alpha composite.
        self._overlay_cache: Any | None = None
        self._cache_key: tuple | None = None
        self._overlay_has_content: bool = False  # True if mask/text was drawn

        # Composite cache: final background+overlay result, reused when
        # both overlay layer AND background are unchanged between ticks.
        self._composite_result: Any | None = None
        self._composite_bg_id: int | None = None

    # ── Resolution ───────────────────────────────────────────────────

    def set_resolution(self, w: int, h: int) -> None:
        """Update LCD resolution. Clears font cache and background."""
        self.log.debug("overlay.set_resolution: %dx%d → %dx%d", self.width, self.height, w, h)
        self.width = w
        self.height = h
        self._renderer.clear_font_cache()
        self.background = None
        self._invalidate_cache()

    # ── Enable / disable ─────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self.log.debug("Overlay %s", "enabled" if value else "disabled")
        self._enabled = value

    # ── Format detection ────────────────────────────────────────────

    def _is_native_surface(self, image: Any) -> bool:
        """Check if image is already a native renderer surface."""
        try:
            self._renderer.surface_size(image)
            return True
        except (AttributeError, TypeError):
            return False

    # ── Background ───────────────────────────────────────────────────

    def set_background(self, image: Any) -> None:
        """Set background image.

        Accepts native surfaces (QImage). Optimized for video playback —
        skips copy/resize if image is already the correct size
        (VideoPlayer pre-resizes frames).
        """
        if image is None:
            self.log.debug("overlay.set_background: None")
            self.background = None
            return

        r = self._renderer

        if not self.width or not self.height:
            self.background = image
            return
        img_size = r.surface_size(image)
        target = (self.width, self.height)
        # Skip resize if already correct size (video frames are pre-sized)
        if img_size == target:
            self.background = image
        else:
            self.background = r.resize(
                r.copy_surface(image), self.width, self.height
            )

    # ── Config ───────────────────────────────────────────────────────

    def set_config(self, config: dict) -> None:
        """Set overlay config dict directly."""
        self.log.info("overlay.set_config: %d elements", len(config) if config else 0)
        self.config = config
        self._invalidate_cache()

    def set_config_resolution(self, w: int, h: int) -> None:
        """Set the resolution the current config was designed for.

        Used for dynamic font/coordinate scaling when displaying a config
        designed for one resolution on a device with a different resolution.
        """
        self._config_resolution = (w, h)
        self._invalidate_cache()

    def set_scale_enabled(self, enabled: bool) -> None:
        """Enable or disable dynamic font/coordinate scaling."""
        self._scale_enabled = enabled
        self._renderer.clear_font_cache()
        self._invalidate_cache()

    def _get_scale_factor(self) -> float:
        """Calculate scale factor from config resolution to display resolution.

        Uses the smaller dimension (usually the same for square LCDs) to
        calculate a uniform scale factor.

        Returns:
            Float scale factor (1.0 = no scaling)
        """
        if not self._scale_enabled:
            return 1.0

        cfg_w, cfg_h = self._config_resolution
        cfg_size = min(cfg_w, cfg_h)
        disp_size = min(self.width, self.height)

        if cfg_size <= 0:
            return 1.0

        return disp_size / cfg_size

    def load_from_dc(self, dc_path: Path) -> dict:
        """Load overlay config, preferring config.json over config1.dc.

        Tries config.json first (human-editable), falls back to config1.dc.

        Returns:
            display_options dict (may contain 'animation_file', etc.).
        """
        self.log.info("load_from_dc: %s", dc_path)
        from ..core.models import ThemeDir

        json_path = ThemeDir(dc_path.parent).json if dc_path else None
        if json_path and json_path.exists() and self._load_config_json_fn is not None:
            try:
                result = self._load_config_json_fn(str(json_path))
                if result is not None:
                    overlay_config, display_options = result
                    self.set_config(overlay_config)
                    self.set_config_resolution(self.width, self.height)
                    self.set_dc_data({'display_options': display_options})
                    return display_options
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.log.warning("Failed to load config.json, falling back to DC: %s", e)

        if not dc_path or not dc_path.exists():
            return {}
        if self._dc_config_cls is None:
            self.log.warning("DcConfig class not injected, cannot parse DC file")
            return {}
        try:
            dc = self._dc_config_cls(dc_path)
            overlay_config = dc.to_overlay_config()
            self.set_config(overlay_config)
            self.set_config_resolution(self.width, self.height)
            self.set_dc_data(dc.to_dict())
            return dc.display_options
        except Exception as e:
            # Injected DC parser class — binary parsing surface varies (struct.error et al).
            self.log.error("Failed to parse DC file: %s", e)
            return {}

    # ── Mask ─────────────────────────────────────────────────────────

    def set_mask(self, image: Any, position: tuple[int, int] | None = None) -> None:
        """Set theme mask overlay image."""
        self.set_theme_mask(image, position)

    def set_theme_mask(self, image: Any, position: tuple[int, int] | None = None) -> None:
        """Set theme mask overlay.

        Masks are kept at original size (not stretched) and positioned
        at the bottom by default for partial overlays.
        """
        self.log.debug("overlay.set_theme_mask: image=%s position=%s",
                  type(image).__name__ if image else None, position)
        if image is None:
            self.theme_mask = None
            self.theme_mask_position = (0, 0)
            self._invalidate_cache()
            return

        r = self._renderer
        image = r.convert_to_rgba(image)
        self.theme_mask = image

        if position is not None:
            self.theme_mask_position = position
        else:
            # C# default: center of mask image → top-left (0, 0)
            self.theme_mask_position = (0, 0)

        self._invalidate_cache()

    def get_mask(self) -> tuple[Any, tuple[int, int] | None]:
        """Get current theme mask image and position."""
        return self.theme_mask, self.theme_mask_position

    @staticmethod
    def load_mask_from_path(
        path: Path, renderer: Any, width: int, height: int,
    ) -> Any | None:
        """Load a mask PNG from file or directory, resized to LCD dims.

        Args:
            path: PNG file or directory containing ``01.png``.
            renderer: Renderer instance for image I/O.
            width: LCD width to resize to.
            height: LCD height to resize to.

        Returns:
            Native surface (QImage) or None if path doesn't exist.
        """
        p = Path(path)
        if p.is_dir():
            p = p / '01.png'
        if not p.exists():
            log.warning("Mask not found: %s", p)
            return None
        img = renderer.open_image(str(p))
        if img is None:
            return None
        return renderer.resize(img, width, height)

    @classmethod
    def render_dc_standalone(
        cls,
        dc_path: Path,
        *,
        width: int,
        height: int,
        renderer: Renderer,
        load_config_json_fn: Any,
        dc_config_cls: Any,
        metrics: HardwareMetrics | None = None,
    ) -> tuple[Any, int, dict]:
        """Render a DC config standalone — fresh OverlayService, black bg.

        Returns ``(image, element_count, display_opts)``. Used by CLI/API
        ``overlay render-from-dc`` to preview a DC file without disturbing
        the active display state.
        """
        from .image import ImageService

        overlay = cls(
            width, height, renderer=renderer,
            load_config_json_fn=load_config_json_fn,
            dc_config_cls=dc_config_cls,
        )
        dc_file = dc_path / "config1.dc" if dc_path.is_dir() else dc_path
        display_opts = overlay.load_from_dc(dc_file)
        if metrics is not None:
            overlay.update_metrics(metrics)
        overlay.enabled = True
        overlay.set_background(ImageService.solid_color(0, 0, 0, width, height))
        image = overlay.render()
        elements = len(overlay.config) if overlay.config else 0
        return image, elements, display_opts or {}

    @staticmethod
    def calculate_mask_position(
        dc_config_cls: Any,
        dc_path: Path | None,
        mask_size: tuple[int, int],
        lcd_size: tuple[int, int],
    ) -> tuple[int, int] | None:
        """Compute mask top-left position from DC config or center fallback.

        DC files store mask_position as center coordinates (XvalMB, YvalMB);
        C# draws at (XvalMB - W/2, YvalMB - H/2). Full-size masks → (0, 0).
        Sub-screen masks without a usable DC entry get centered.
        """
        mask_w, mask_h = mask_size
        lcd_w, lcd_h = lcd_size
        if mask_w >= lcd_w and mask_h >= lcd_h:
            return (0, 0)
        centered = ((lcd_w - mask_w) // 2, (lcd_h - mask_h) // 2)
        if not dc_path or not Path(dc_path).exists() or dc_config_cls is None:
            return centered
        try:
            dc = dc_config_cls(dc_path)
            if dc.mask_enabled:
                if (center_pos := dc.mask_settings.get('mask_position')):
                    return (center_pos[0] - mask_w // 2,
                            center_pos[1] - mask_h // 2)
        except Exception as e:
            # Injected DC parser class — binary parsing surface varies.
            log.warning("DC config parse failed for %s — centering mask: %s",
                        dc_path, e)
        return centered

    def set_mask_position(self, position: tuple[int, int]) -> None:
        """Update theme-mask top-left position and invalidate render cache."""
        self.theme_mask_position = position
        self._invalidate_cache()

    def set_mask_visible(self, visible: bool) -> None:
        """Toggle mask visibility without destroying it (Windows SetDrawMengBan)."""
        self.theme_mask_visible = visible
        self._invalidate_cache()

    # ── Temp unit ────────────────────────────────────────────────────

    def set_temp_unit(self, unit: int) -> None:
        """Set temperature display unit (0=Celsius, 1=Fahrenheit)."""
        self.temp_unit = unit
        self._invalidate_cache()

    # ── Language (weekday abbreviations) ──────────────────────────────

    def set_lang(self, lang: str) -> None:
        """Set ISO 639-1 language code for weekday abbreviations (#141)."""
        self.lang = lang or 'en'
        self._invalidate_cache()

    # ── Metrics ──────────────────────────────────────────────────────

    def update_metrics(self, metrics: HardwareMetrics) -> None:
        """Update system metrics for hardware overlay elements."""
        self._metrics = metrics

    @property
    def metrics(self) -> HardwareMetrics | None:
        """Most recently received metrics, or None before first tick."""
        return self._metrics

    # ── Overlay cache ────────────────────────────────────────────────

    def _invalidate_cache(self) -> None:
        """Force overlay re-render on next frame."""
        self._overlay_cache = None
        self._cache_key = None
        self._composite_result = None
        self._composite_bg_id = None

    def would_change(self, metrics: HardwareMetrics) -> bool:
        """Check if rendering with these metrics would produce a new frame.

        Lightweight check — computes cache key without actually rendering.
        Used by metrics timer to skip render+send when nothing changed.
        """
        new_key = self._build_cache_key(metrics)
        if new_key == self._cache_key:
            return False
        if log.isEnabledFor(logging.DEBUG) and self._cache_key is not None:
            _CACHE_KEY_NAMES = (
                'config', 'theme_mask', 'mask_visible', 'mask_position',
                'time_format', 'date_format', 'temp_unit', 'lang',
                'flash_skip', 'scale_factor', 'metrics_hash',
            )
            changed = [
                name for name, old, new in zip(
                    _CACHE_KEY_NAMES, self._cache_key, new_key, strict=False)
                if old != new
            ]
            self.log.debug("overlay cache invalidated — changed: %s", ', '.join(changed))
        return True

    def _build_cache_key(self, metrics: HardwareMetrics) -> tuple:
        """Build cache key from all inputs that affect overlay appearance."""
        return (
            id(self.config),
            id(self.theme_mask),
            self.theme_mask_visible,
            self.theme_mask_position,
            self.time_format,
            self.date_format,
            self.temp_unit,
            self.lang,
            self.flash_skip_index,
            self._get_scale_factor(),
            self._metrics_hash(metrics),
        )

    def _metrics_hash(self, metrics: HardwareMetrics) -> int:
        """Hash the *formatted display strings*, not raw metric values.

        Previous approach used ``round(val)`` which still invalidated the
        cache on sub-display fluctuations (cpu_percent 12.3→12.7 rounds
        to 12→13, but both display as "12%").  By hashing the actual
        text that would be rendered, the overlay only re-draws when the
        screen would visibly change.

        Time/date/weekday elements call datetime.now() during render
        (ignoring the metrics DTO), so we include current time directly
        in the hash to ensure the cache invalidates on minute boundaries.
        """
        if not self.config or not isinstance(self.config, dict):
            return 0
        vals: list[Any] = []
        has_time = False
        has_date = False
        for cfg in self.config.values():
            if not isinstance(cfg, dict) or 'metric' not in cfg:
                continue
            metric_name = cfg['metric']
            match metric_name:
                case 'time':
                    has_time = True
                case 'date' | 'weekday':
                    has_date = True
                case _:
                    # Same gate as _draw_text_elements — sensor-backed
                    # metrics absent from _populated render as nothing, so
                    # their cache key is a sentinel, not a (lying) value.
                    if metric_name not in metrics._populated:
                        vals.append(None)
                    elif (val := getattr(metrics, metric_name, None)) is not None:
                        time_fmt = cfg.get('time_format', self.time_format)
                        date_fmt = cfg.get('date_format', self.date_format)
                        vals.append(SystemService.format_metric(
                            metric_name, val, time_fmt, date_fmt,
                            self.temp_unit, lang=self.lang))
                    else:
                        vals.append(None)
        # Time/date use datetime.now() in render — include in hash
        if has_time or has_date:
            from datetime import datetime
            now = datetime.now()
            if has_time:
                vals.append((now.hour, now.minute))
            if has_date:
                vals.append((now.year, now.month, now.day, now.weekday()))
        return hash(tuple(vals))

    # ── Render ───────────────────────────────────────────────────────

    def render(self, background: Any = None,
               metrics: HardwareMetrics | None = None,
               **_kw: Any) -> Any:
        """Render overlay onto background.

        Callers gate on `.enabled` before calling — this method always renders.

        Args:
            background: Native surface (uses stored background if None).
            metrics: HardwareMetrics DTO (uses stored metrics if None).

        Returns:
            Native surface (QImage).
        """
        self.log.debug("overlay.render: has_bg=%s has_mask=%s enabled=%s",
                  background is not None, self.theme_mask is not None, self.enabled)
        if background is not None and background is not self.background:
            self.set_background(background)
        m = metrics if metrics is not None else self._metrics
        return self._render_overlay(m)

    def _render_overlay(self, metrics: HardwareMetrics | None = None) -> Any:
        """Compositing pipeline — background + cached overlay layer.

        Two cache layers for minimal per-tick cost:
        1. Overlay layer cache (text + mask) — re-rendered only when
           config/metrics change.
        2. Composite cache (background + overlay) — reused when both
           overlay AND background are unchanged (static theme at idle).
        """
        metrics = metrics or HardwareMetrics()
        r = self._renderer

        # Fast path: no overlays, just return background as-is
        has_overlays = (
            (self.theme_mask and self.theme_mask_visible)
            or (self.config and isinstance(self.config, dict))
        )
        if not has_overlays and self.background:
            return self.background

        # Check overlay layer cache
        cache_key = self._build_cache_key(metrics)
        if (overlay_changed := cache_key != self._cache_key or self._overlay_cache is None):
            self._overlay_cache = self._render_overlay_layer(metrics, r)
            self._cache_key = cache_key
            self._composite_result = None  # Invalidate composite

        # Fast path: overlay layer has no visible content (no mask, no text)
        if not self._overlay_has_content and self.background:
            return self.background

        # Check composite cache — skip copy+paste when nothing changed
        bg_id = id(self.background)
        if (self._composite_result is not None
                and not overlay_changed
                and bg_id == self._composite_bg_id):
            return self._composite_result
        result = self._composite_onto_background(r)
        self._composite_result = result
        self._composite_bg_id = bg_id
        return result

    def _composite_onto_background(self, r: Renderer) -> Any:
        """Composite cached overlay layer onto current background.

        Returns native surface (QImage).
        """
        if self.background is None:
            base = r.create_surface(self.width, self.height)
            r.composite(base, self._overlay_cache, (0, 0))
            return r.convert_to_rgb(base)

        base = r.copy_surface(self.background)
        r.composite(base, self._overlay_cache, (0, 0))
        return base

    def _render_overlay_layer(self, metrics: HardwareMetrics,
                              r: Renderer) -> Any:
        """Render mask + text to a transparent overlay surface.

        This is the expensive operation — called only on cache miss.
        Sets _overlay_has_content so callers can skip composite when empty.
        """
        self._overlay_has_content = False
        overlay = r.create_surface(self.width, self.height)

        # Apply theme mask
        if self.theme_mask and self.theme_mask_visible:
            scale = self._get_scale_factor()
            self._overlay_has_content = True
            if abs(scale - 1.0) > 0.01:
                mw, mh = r.surface_size(self.theme_mask)
                mask_surface = r.resize(
                    r.copy_surface(self.theme_mask),
                    int(mw * scale), int(mh * scale))
                pos_x = int(self.theme_mask_position[0] * scale)
                pos_y = int(self.theme_mask_position[1] * scale)
                overlay = r.composite(overlay, mask_surface, (pos_x, pos_y))
            else:
                overlay = r.composite(
                    overlay, self.theme_mask, self.theme_mask_position)

        # Draw text overlays
        if self._draw_text_elements(overlay, metrics, r):
            self._overlay_has_content = True

        return overlay

    def _draw_text_elements(self, surface: Any, metrics: HardwareMetrics,
                            r: Renderer) -> bool:
        """Render text elements onto a surface.

        Shared by _render_overlay_layer (mask+text) and render_text_only
        (text-only for video cache).

        Returns True if any text was drawn.
        """
        if not self.config or not isinstance(self.config, dict):
            return False

        scale = self._get_scale_factor()
        drew_any = False

        for elem_idx, (_key, cfg) in enumerate(self.config.items()):
            if not isinstance(cfg, dict) or not cfg.get('enabled', True):
                continue
            if elem_idx == self.flash_skip_index:
                continue

            base_x = cfg.get('x', 10)
            base_y = cfg.get('y', 10)
            font_cfg = cfg.get('font', {})
            base_font_size = font_cfg.get('size', 24) if isinstance(font_cfg, dict) else 24
            color = cfg.get('color', '#FFFFFF')

            x = int(base_x * scale)
            y = int(base_y * scale)
            font_size = max(8, int(base_font_size * scale))

            # Get text to render
            if 'text' in cfg:
                text = str(cfg['text'])
            elif 'metric' in cfg:
                metric_name = cfg['metric']
                # Date/time/weekday are derived inside format_metric from
                # datetime.now() — they aren't sensor-backed, so they bypass
                # the _populated gate and always render.
                if metric_name not in _COMPUTED_METRICS \
                        and metric_name not in metrics._populated:
                    continue  # No source on this OS — skip the ghost zero.
                if (value := getattr(metrics, metric_name, None)) is not None:
                    time_fmt = cfg.get('time_format', self.time_format)
                    date_fmt = cfg.get('date_format', self.date_format)
                    text = SystemService.format_metric(
                        metric_name, value,
                        time_fmt, date_fmt, self.temp_unit, lang=self.lang)
                else:
                    continue
            else:
                continue

            style = font_cfg.get('style') if isinstance(font_cfg, dict) else None
            bold = style == 'bold'
            italic = style == 'italic'
            font_name = font_cfg.get('name') if isinstance(font_cfg, dict) else None
            font = r.get_font(font_size, bold=bold, italic=italic, font_name=font_name)
            r.draw_text(surface, x, y, text, color, font, anchor='mm')
            drew_any = True

        return drew_any

    def render_text_only(self, metrics: HardwareMetrics) -> tuple[Any, tuple]:
        """Render text elements only (no mask) to transparent RGBA surface.

        Used by VideoFrameCache to separate static mask from dynamic text.
        Returns (native_surface, cache_key) where cache_key tracks text-only changes.
        """
        r = self._renderer
        surface = r.create_surface(self.width, self.height)
        self._draw_text_elements(surface, metrics, r)
        return surface, self._build_text_cache_key(metrics)

    def _build_text_cache_key(self, metrics: HardwareMetrics) -> tuple:
        """Cache key for text-only changes (subset of full cache key)."""
        return (
            self.time_format,
            self.date_format,
            self.temp_unit,
            self.lang,
            self.flash_skip_index,
            self._get_scale_factor(),
            self._metrics_hash(metrics),
        )

    # ── DC data (lossless round-trip) ────────────────────────────────

    def set_dc_data(self, data: dict[str, Any] | None) -> None:
        """Store parsed DC data for lossless save round-trip."""
        self._dc_data = data

    # ── Clear ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Clear rendering state (preserves resolution and format options)."""
        self.config = {}
        self.background = None
        self.theme_mask = None
        self.theme_mask_position = (0, 0)
        self.theme_mask_visible = True
        self._invalidate_cache()
