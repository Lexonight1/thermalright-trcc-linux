"""DisplayService — cached two-layer render pipeline.

Per-device, two caches:

  ┌─ bg_mask  ── fitted background (image or current video frame)
  │              composited with the theme's mask image.  Heavy work:
  │              fit, resize, alpha-composite.  Rebuilt only when
  │              theme changes, orientation changes, or video cursor
  │              advances.
  │
  └─ overlay  ── transparent layer with metric text / static text
                 elements drawn on top.  Rebuilt only when sensor
                 values change OR theme config changes.

Per-tick pipeline is just: blend the two caches, dim for brightness,
rotate to native buffer arrangement, encode for the wire, hand to
Device.send.  Order mirrors the C# ground truth
(fit → overlay → dim → rotate → encode).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..core.models import FitMode, ProductInfo, RawFrame, Theme, Wire
from ..core.ports import Renderer
from .media import MediaService
from .overlay import OverlayService
from .settings import Settings
from .theme import ThemeService

log = logging.getLogger(__name__)


_ENCODING_BY_WIRE: Dict[Wire, str] = {
    Wire.SCSI: "rgb565",
    Wire.HID: "rgb565",
    Wire.BULK: "rgb565",
    Wire.LY: "rgb565",
}

_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


# =========================================================================
# SceneCache — per-device layered cache
# =========================================================================


@dataclass
class SceneCache:
    """Two surfaces + the invalidation keys that govern them."""

    # bg_mask layer
    bg_mask_surface: Any
    bg_mask_key: Tuple[Any, ...]       # (theme_path, visual_size, video_cursor)

    # overlay layer
    overlay_surface: Any
    overlay_key: Tuple[Any, ...]       # (config_id, visual_size, sensor_tuple)


# =========================================================================
# DisplayService
# =========================================================================


class DisplayService:
    """Build device-ready frame bytes, caching the expensive layers."""

    def __init__(
        self,
        renderer: Renderer,
        themes: ThemeService,
        overlay: OverlayService,
        settings: Settings,
        media: MediaService,
    ) -> None:
        self._r = renderer
        self._themes = themes
        self._overlay = overlay
        self._settings = settings
        self._media = media
        self._scenes: Dict[str, SceneCache] = {}

    # ── Top-level pipeline ────────────────────────────────────────────

    def build_frame(
        self,
        info: ProductInfo,
        theme: Theme,
        sensors: Dict[str, float],
    ) -> bytes:
        """One pass — uses the per-device cache; only rebuilds what changed."""
        s = self._settings.for_device(info.key)
        visual_size = self._visual_size(info.native_resolution, s.orientation)

        scene = self._scenes.get(info.key)
        bg_key = self._bg_mask_key(info, theme, visual_size)
        overlay_key = self._overlay_key(theme, visual_size, sensors)

        if scene is None or scene.bg_mask_key != bg_key:
            bg_surface = self._build_bg_mask(info, theme, visual_size)
        else:
            bg_surface = scene.bg_mask_surface

        if scene is None or scene.overlay_key != overlay_key:
            overlay_surface = self._build_overlay(theme, sensors, visual_size)
        else:
            overlay_surface = scene.overlay_surface

        self._scenes[info.key] = SceneCache(
            bg_mask_surface=bg_surface, bg_mask_key=bg_key,
            overlay_surface=overlay_surface, overlay_key=overlay_key,
        )

        # Compose: bg+mask below, overlay on top
        surface = self._r.composite(bg_surface, overlay_surface, position=(0, 0))

        # Brightness dim (before rotation — matches C# order)
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)

        # Rotate content back to native buffer arrangement
        if s.orientation:
            surface = self._r.rotate(surface, 360 - s.orientation)

        return self._encode_for_wire(surface, info)

    def invalidate(self, key: str) -> None:
        """Drop the scene cache for *key* (called on disconnect / theme change)."""
        self._scenes.pop(key, None)

    def invalidate_all(self) -> None:
        self._scenes.clear()

    # ── Layer 1: background + mask ────────────────────────────────────

    def _build_bg_mask(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: Tuple[int, int],
    ) -> Any:
        """Compose fitted background + mask at visual size."""
        canvas = self._r.create_surface(*visual_size, color=(0, 0, 0, 255))

        # Paint the fitted background
        source = self._resolve_background(info, theme, visual_size)
        if source is not None:
            src_w, src_h = self._r.surface_size(source)
            dst_w, dst_h = visual_size
            fit_mode = self._settings.for_device(info.key).fit_mode
            fit_w, fit_h, off_x, off_y = _fit(
                fit_mode, src_w, src_h, dst_w, dst_h,
            )
            fitted = self._r.resize(source, fit_w, fit_h)
            canvas = self._r.composite(canvas, fitted, position=(off_x, off_y))

        # Composite the mask on top of the background (if present)
        mask_path = self._themes.mask_path(theme)
        if mask_path is not None:
            mask = self._r.open_image(mask_path)
            if self._r.surface_size(mask) != visual_size:
                mask = self._r.resize(mask, *visual_size)
            canvas = self._r.composite(canvas, mask, position=(0, 0))

        return canvas

    def _resolve_background(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: Tuple[int, int],
    ) -> Optional[Any]:
        """Return a Renderer surface for the current background frame."""
        path = self._themes.background_path(theme)
        if path is None:
            return None
        ext = path.suffix.lower()

        if ext in _VIDEO_EXTS:
            playback = self._media.playback(info.key)
            if playback is None or not playback.frames:
                try:
                    playback = self._media.load_video(
                        device_key=info.key, path=path, size=visual_size,
                    )
                except Exception as e:
                    log.warning("Video decode failed for %s: %s", path.name, e)
                    return None
            frame: Optional[RawFrame] = playback.advance()
            return self._r.from_raw_rgb24(frame) if frame else None

        if ext in _IMAGE_EXTS:
            return self._r.open_image(path)

        log.debug("Unrecognised background extension %r; skipping", ext)
        return None

    # ── Layer 2: metric overlay ───────────────────────────────────────

    def _build_overlay(
        self,
        theme: Theme,
        sensors: Dict[str, float],
        visual_size: Tuple[int, int],
    ) -> Any:
        """Transparent layer with text + metric elements painted on."""
        overlay_canvas = self._r.create_surface(*visual_size)
        return self._overlay.render(overlay_canvas, theme.config, sensors)

    # ── Cache keys ────────────────────────────────────────────────────

    def _bg_mask_key(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: Tuple[int, int],
    ) -> Tuple[Any, ...]:
        path = self._themes.background_path(theme)
        is_video = path is not None and path.suffix.lower() in _VIDEO_EXTS
        # For video, include the current cursor so each frame busts the cache.
        cursor = None
        if is_video:
            pb = self._media.playback(info.key)
            cursor = pb.cursor if pb else 0
        return (str(theme.path), visual_size, cursor)

    @staticmethod
    def _overlay_key(
        theme: Theme,
        visual_size: Tuple[int, int],
        sensors: Dict[str, float],
    ) -> Tuple[Any, ...]:
        # Sensors turn into a sorted tuple of (id, rounded_value).  Rounding
        # limits cache-busting to meaningful changes (e.g. 45.3 → 45.4 is
        # one redraw; 45.31 → 45.32 is ignored).
        sensor_tuple = tuple(sorted(
            (k, round(v, 1)) for k, v in sensors.items()
        ))
        return (id(theme.config), visual_size, sensor_tuple)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _visual_size(native: Tuple[int, int], orientation: int) -> Tuple[int, int]:
        w, h = native
        return (h, w) if orientation in (90, 270) else (w, h)

    def _encode_for_wire(self, surface: Any, info: ProductInfo) -> bytes:
        encoding = _ENCODING_BY_WIRE.get(info.wire, "rgb565")
        if encoding == "jpeg":
            return self._r.encode_jpeg(surface)
        return self._r.encode_rgb565(surface)


# =========================================================================
# Pure-Python fit algorithm
# =========================================================================


def _fit(
    mode: FitMode,
    src_w: int, src_h: int,
    dst_w: int, dst_h: int,
) -> Tuple[int, int, int, int]:
    """(fit_w, fit_h, x_offset, y_offset)."""
    if mode is FitMode.STRETCH or src_w == 0 or src_h == 0:
        return dst_w, dst_h, 0, 0

    if mode is FitMode.WIDTH:
        fit_w = dst_w
        fit_h = max(1, (src_h * dst_w) // src_w)
        return fit_w, fit_h, 0, (dst_h - fit_h) // 2

    # FitMode.HEIGHT
    fit_h = dst_h
    fit_w = max(1, (src_w * dst_h) // src_h)
    return fit_w, fit_h, (dst_w - fit_w) // 2, 0


# Re-exported for unit tests
fit = _fit


# Silence pyright on unused Path import (kept for future file-path cache keys)
_ = Path
