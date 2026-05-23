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
from typing import Any

from ..core.models import DeviceSettings, FitMode, ProductInfo, RawFrame, Theme
from ..core.ports import Renderer
from ..core.protocol import DeviceProfile, get_profile
from ._clock import compute_clock
from .media import MediaService
from .overlay import OverlayService
from .settings import Settings
from .theme import ThemeService

log = logging.getLogger(__name__)


_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


# =========================================================================
# SceneCache — per-device layered cache
# =========================================================================


@dataclass
class SceneCache:
    """Two surfaces + the invalidation keys that govern them."""

    # bg_mask layer
    bg_mask_surface: Any
    bg_mask_key: tuple[Any, ...]       # (theme_path, visual_size, video_cursor)

    # overlay layer
    overlay_surface: Any
    overlay_key: tuple[Any, ...]       # (config_id, visual_size, sensor_tuple)


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
        self._scenes: dict[str, SceneCache] = {}

    # ── Top-level pipeline ────────────────────────────────────────────

    def build_frame(
        self,
        info: ProductInfo,
        theme: Theme,
        sensors: dict[str, float],
        *,
        profile: DeviceProfile | None = None,
    ) -> bytes:
        """One pass — uses the per-device cache; only rebuilds what changed.

        ``profile`` is the handshake-derived `DeviceProfile` from the
        connected Device (HidLcd / ScsiLcd / …). When provided, it drives:
            * the render canvas size (``profile.resolution`` — landscape
              for portrait panels, so layers compose in their logical
              orientation),
            * device-side rotation before encode (``profile.rotate=True``
              transposes landscape → portrait buffer),
            * encoding choice (``profile.jpeg`` vs RGB565).

        When ``profile`` is None (LED, pre-handshake, callers that don't
        thread it through yet), behavior matches the pre-profile path:
        canvas = ``info.native_resolution``, no device rotation, RGB565.
        """
        resolved_profile = self._resolve_profile(info, profile)
        base_size = resolved_profile.resolution

        s = self._settings.for_device(info.key)
        visual_size = self._visual_size(base_size, s.orientation)

        # Per-frame — DEBUG so `-vv` users see the build context without
        # drowning a default INFO log.
        log.debug(
            "build_frame %s: theme=%r visual=%dx%d orientation=%d brightness=%d",
            info.key, theme.name, visual_size[0], visual_size[1],
            s.orientation, s.brightness,
        )

        clock = compute_clock(
            time_format=s.time_format,
            date_format=s.date_format,
            language=self._settings.app.language,
        )
        log.debug(
            "build_frame %s: clock=%s (time_format=%s date_format=%s lang=%s)",
            info.key, sorted(clock.keys()),
            s.time_format, s.date_format,
            self._settings.app.language,
        )

        scene = self._scenes.get(info.key)
        bg_key = self._bg_mask_key(info, theme, visual_size)
        overlay_key = self._overlay_key(info, theme, visual_size, sensors, clock)

        bg_hit = scene is not None and scene.bg_mask_key == bg_key
        ovl_hit = scene is not None and scene.overlay_key == overlay_key
        log.debug(
            "build_frame %s: scene cache bg=%s overlay=%s",
            info.key,
            "HIT" if bg_hit else "MISS",
            "HIT" if ovl_hit else "MISS",
        )

        if scene is None or scene.bg_mask_key != bg_key:
            bg_surface = self._build_bg_mask(info, theme, visual_size)
        else:
            bg_surface = scene.bg_mask_surface

        if scene is None or scene.overlay_key != overlay_key:
            overlay_surface = self._build_overlay(
                info, theme, sensors, visual_size, clock,
            )
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
            log.debug("build_frame %s: applying brightness %d%%",
                      info.key, s.brightness)
            surface = self._r.apply_brightness(surface, s.brightness)

        # User-orientation rotation
        if s.orientation:
            log.debug("build_frame %s: user rotate %d°",
                      info.key, 360 - s.orientation)
            surface = self._r.rotate(surface, 360 - s.orientation)

        # Device-side rotation: portrait panels render content in landscape
        # for composition, then rotate 90° to match the device's portrait
        # buffer arrangement before encoding. Matches the C# pipeline
        # ("RGB565-LE rotated" in legacy report output).
        if resolved_profile.rotate:
            log.debug("build_frame %s: device rotate 90° (portrait panel)",
                      info.key)
            surface = self._r.rotate(surface, 90)

        return self._encode_for_wire(surface, resolved_profile)

    def build_preview_surface(
        self,
        info: ProductInfo,
        theme: Theme,
        sensors: dict[str, float],
        *,
        profile: DeviceProfile | None = None,
    ) -> Any:
        """Same pipeline as ``build_frame`` but returns the surface pre-encode.

        Used by the GUI preview panel — gives callers a renderable
        Renderer surface (QImage for QtRenderer) without paying for the
        RGB565/JPEG encode step.  Honors user orientation + brightness
        + device-side rotation so what the preview shows matches what
        the device would receive byte-for-byte.
        """
        resolved_profile = self._resolve_profile(info, profile)
        base_size = resolved_profile.resolution

        s = self._settings.for_device(info.key)
        visual_size = self._visual_size(base_size, s.orientation)

        clock = compute_clock(
            time_format=s.time_format,
            date_format=s.date_format,
            language=self._settings.app.language,
        )

        # Same cache lookup as build_frame so a preview tick doesn't
        # invalidate it for the wire path.
        scene = self._scenes.get(info.key)
        bg_key = self._bg_mask_key(info, theme, visual_size)
        overlay_key = self._overlay_key(info, theme, visual_size, sensors, clock)

        if scene is None or scene.bg_mask_key != bg_key:
            bg_surface = self._build_bg_mask(info, theme, visual_size)
        else:
            bg_surface = scene.bg_mask_surface
        if scene is None or scene.overlay_key != overlay_key:
            overlay_surface = self._build_overlay(
                info, theme, sensors, visual_size, clock,
            )
        else:
            overlay_surface = scene.overlay_surface
        self._scenes[info.key] = SceneCache(
            bg_mask_surface=bg_surface, bg_mask_key=bg_key,
            overlay_surface=overlay_surface, overlay_key=overlay_key,
        )

        surface = self._r.composite(bg_surface, overlay_surface, position=(0, 0))
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)
        if s.orientation:
            surface = self._r.rotate(surface, 360 - s.orientation)
        if resolved_profile.rotate:
            surface = self._r.rotate(surface, 90)
        return surface

    def build_solid_color_frame(
        self,
        *,
        info: ProductInfo,
        color: tuple[int, int, int],
        profile: DeviceProfile | None = None,
    ) -> bytes:
        """Build a frame of a single solid color, ready for ``Device.send``.

        Bypasses the theme/overlay scene cache — just creates a uniform
        surface at the profile's resolution, applies device rotation if
        the profile demands it, and encodes for the wire. Used by the
        ``SendColor`` Command + diagnostic CLI ``display color`` path.

        Apply brightness from per-device settings too, so a user who's
        dimmed their display still sees a dimmed color test instead of
        a bright wash.
        """
        resolved = self._resolve_profile(info, profile)
        w, h = resolved.resolution
        # Surface is opaque RGB; alpha not needed for solid fill.
        surface = self._r.create_surface(w, h, color=(*color, 255))

        s = self._settings.for_device(info.key)
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)

        # Device-side rotation transposes the buffer for portrait panels.
        if resolved.rotate:
            surface = self._r.rotate(surface, 90)

        return self._encode_for_wire(surface, resolved)

    def build_screencast_frame(
        self,
        *,
        info: ProductInfo,
        frame: RawFrame,
        profile: DeviceProfile | None = None,
    ) -> bytes:
        """Encode a single captured screen region for the device wire.

        Used by the screencast tick: GUI grabs a region, hands the raw
        RGB24 to this method, gets back ready-to-send bytes.  Skips the
        theme/overlay pipeline entirely — screencasts replace the
        background and (usually) the user runs them with
        ``background_mode = "transparent"`` so overlay elements still
        paint on top once we layer them in.

        Honors per-device brightness + device-side rotation so the
        live capture matches the rest of the device's behaviour.
        """
        resolved = self._resolve_profile(info, profile)
        target_w, target_h = resolved.resolution

        surface = self._r.from_raw_rgb24(frame)
        if (
            self._r.surface_size(surface) != (target_w, target_h)
        ):
            surface = self._r.resize(surface, target_w, target_h)

        s = self._settings.for_device(info.key)
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)
        if s.orientation:
            surface = self._r.rotate(surface, 360 - s.orientation)
        if resolved.rotate:
            surface = self._r.rotate(surface, 90)

        return self._encode_for_wire(surface, resolved)

    def invalidate(self, key: str) -> None:
        """Drop the scene cache for *key* (called on disconnect / theme change)."""
        self._scenes.pop(key, None)

    def invalidate_all(self) -> None:
        self._scenes.clear()

    # ── One-off encoding (used by Commands that bypass the scene cache) ──

    def encode_boot_anim_frame(
        self,
        image_path: Path,
        resolution: tuple[int, int],
    ) -> bytes:
        """Encode one image to RGB565 bytes at the given resolution.

        Used by UploadBootAnimation — boot-animation frames are always
        RGB565 regardless of the device's normal wire format, and the
        firmware applies its own rotation, so we skip both the JPEG
        branch and the profile's portrait-rotation step.
        """
        surface = self._r.open_image(image_path)
        if self._r.surface_size(surface) != resolution:
            surface = self._r.resize(surface, *resolution)
        return self._r.encode_rgb565(surface)

    # ── Layer 1: background + mask ────────────────────────────────────

    def _build_bg_mask(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> Any:
        """Compose fitted background + mask at visual size."""
        canvas = self._r.create_surface(*visual_size, color=(0, 0, 0, 255))
        s = self._settings.for_device(info.key)

        # Paint the fitted background
        source = self._resolve_background(info, theme, visual_size)
        if source is not None:
            src_w, src_h = self._r.surface_size(source)
            dst_w, dst_h = visual_size
            fit_w, fit_h, off_x, off_y = _fit(
                s.fit_mode, src_w, src_h, dst_w, dst_h,
            )
            log.info(
                "build_bg_mask %s: background %dx%d → fit %s → %dx%d at (%d, %d)",
                info.key, src_w, src_h,
                s.fit_mode.value if hasattr(s.fit_mode, "value") else s.fit_mode,
                fit_w, fit_h, off_x, off_y,
            )
            fitted = self._r.resize(source, fit_w, fit_h)
            canvas = self._r.composite(canvas, fitted, position=(off_x, off_y))
        else:
            log.warning(
                "build_bg_mask %s: no background source resolved for theme %r — "
                "canvas stays solid black",
                info.key, theme.name,
            )

        # Mask layer: per-device override (ApplyMask Command) takes
        # precedence over the theme's bundled mask; mask_visible=False
        # skips the layer entirely. Position defaults to (0, 0).
        mask_source = self._resolve_mask_source(s, theme)
        if mask_source is not None:
            mask = self._r.open_image(mask_source)
            mw, mh = self._r.surface_size(mask)
            position = s.mask_position or (0, 0)
            log.info(
                "build_bg_mask %s: mask %s (%dx%d) at top-left (%d, %d) "
                "[visible=%s]",
                info.key, mask_source, mw, mh, position[0], position[1],
                s.mask_visible,
            )
            canvas = self._r.composite(canvas, mask, position=position)
        else:
            log.info(
                "build_bg_mask %s: no mask composited (visible=%s, "
                "override=%r, theme_mask=%r)",
                info.key, s.mask_visible, s.mask_path,
                self._themes.mask_path(theme),
            )

        return canvas

    def _resolve_mask_source(
        self,
        device_settings: DeviceSettings,
        theme: Theme,
    ) -> Path | None:
        """Pick which mask file (if any) to render for this device.

        Order: per-device override → theme's bundled mask → None. Returns
        None when ``mask_visible`` is False so the caller skips the layer.
        """
        if not device_settings.mask_visible:
            log.debug("resolve_mask_source: mask_visible=False → None")
            return None
        if device_settings.mask_path is not None:
            override = Path(device_settings.mask_path)
            if override.exists():
                log.debug("resolve_mask_source: using override %s", override)
                return override
            log.warning(
                "resolve_mask_source: override %s does not exist — "
                "falling back to theme bundled mask",
                override,
            )
        theme_mask = self._themes.mask_path(theme)
        log.debug(
            "resolve_mask_source: using theme bundled mask %s",
            theme_mask,
        )
        return theme_mask

    def _resolve_background(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> Any | None:
        """Return a Renderer surface for the current background frame.

        Playback (set by ``PlayVideo`` or by a prior video-theme render)
        takes precedence — lets users play arbitrary videos without
        replacing the active theme. When no playback exists, fall back
        to the theme's bundled background image or video.
        """
        # Playback override: PlayVideo Command pre-loads a video into
        # MediaService; StopVideo clears it. While a playback exists,
        # ignore the theme background entirely.
        #
        # Render reads the CURRENT frame without advancing — advancing
        # is owned by the per-handler animation tick (or a future
        # legacy-style PollingMetricsLoop tick).  Pre-fix advance() was
        # called here AND in ``_on_video_tick`` AND on every observer-
        # triggered RenderAndSend, so the cursor moved 2-3 steps per
        # wall-clock tick — playback looked 2-3× too fast.
        playback = self._media.playback(info.key)
        if playback is not None and playback.frames:
            frame: RawFrame | None = playback.current
            log.debug(
                "resolve_background %s: video playback active "
                "(%d frames, cursor=%d)",
                info.key, len(playback.frames), playback.cursor,
            )
            return self._r.from_raw_rgb24(frame) if frame else None

        path = self._themes.background_path(theme)
        if path is None:
            log.warning(
                "resolve_background %s: theme %r has no background "
                "(no 00.png or Theme.{mp4,mov,webm,zt} in %s)",
                info.key, theme.name, theme.path,
            )
            return None
        ext = path.suffix.lower()
        log.info("resolve_background %s: theme %r → %s",
                 info.key, theme.name, path)

        if ext in _VIDEO_EXTS:
            try:
                playback = self._media.load_video(
                    device_key=info.key, path=path, size=visual_size,
                )
            except Exception as e:
                log.warning("resolve_background %s: video decode failed for "
                            "%s: %s: %s",
                            info.key, path.name, type(e).__name__, e)
                return None
            log.info(
                "resolve_background %s: video loaded (%d frames) from %s",
                info.key, len(playback.frames), path,
            )
            frame = playback.current
            return self._r.from_raw_rgb24(frame) if frame else None

        if ext in _IMAGE_EXTS:
            return self._r.open_image(path)

        log.warning(
            "resolve_background %s: unrecognised background extension %r "
            "at %s — skipping",
            info.key, ext, path,
        )
        return None

    # ── Layer 2: metric overlay ───────────────────────────────────────

    def _build_overlay(
        self,
        info: ProductInfo,
        theme: Theme,
        sensors: dict[str, float],
        visual_size: tuple[int, int],
        clock: dict[str, str],
    ) -> Any:
        """Transparent layer with text + metric + clock elements painted on.

        Theme-bundled elements paint first; user-edited elements
        (``DeviceSettings.user_overlay_elements``) paint on top.
        """
        overlay_canvas = self._r.create_surface(*visual_size)
        s = self._settings.for_device(info.key)
        user_dicts = [e.to_dict() for e in s.user_overlay_elements]
        theme_elements = theme.config.get("elements") or []
        log.info(
            "build_overlay %s: theme=%r theme_elements=%d user_elements=%d "
            "overlay_enabled=%s",
            info.key, theme.name, len(theme_elements), len(user_dicts),
            theme.config.get("overlay_enabled", True),
        )
        return self._overlay.render(
            overlay_canvas, theme.config, sensors,
            clock=clock, user_elements=user_dicts,
        )

    # ── Cache keys ────────────────────────────────────────────────────

    def _bg_mask_key(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> tuple[Any, ...]:
        path = self._themes.background_path(theme)
        is_video = path is not None and path.suffix.lower() in _VIDEO_EXTS
        # For video, include the current cursor so each frame busts the cache.
        cursor = None
        if is_video:
            pb = self._media.playback(info.key)
            cursor = pb.cursor if pb else 0
        # Mask state belongs in this key so the bg+mask layer rebuilds when
        # ApplyMask / SetMaskPosition / SetMaskVisible run. The Commands
        # already explicitly invalidate, but including it defends against
        # any path that mutates Settings without going through Commands.
        s = self._settings.for_device(info.key)
        mask_sig = (s.mask_path, s.mask_position, s.mask_visible, s.fit_mode)
        return (str(theme.path), visual_size, cursor, mask_sig)

    def _overlay_key(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
        sensors: dict[str, float],
        clock: dict[str, str],
    ) -> tuple[Any, ...]:
        # Sensors turn into a sorted tuple of (id, rounded_value).  Rounding
        # limits cache-busting to meaningful changes (e.g. 45.3 → 45.4 is
        # one redraw; 45.31 → 45.32 is ignored).
        sensor_tuple = tuple(sorted(
            (k, round(v, 1)) for k, v in sensors.items()
        ))
        clock_tuple = tuple(sorted(clock.items()))
        # User-edited elements fingerprint — flip changes whenever the user
        # adds / updates / deletes elements, so the cached overlay surface
        # rebuilds without an explicit invalidate from each Command.
        s = self._settings.for_device(info.key)
        user_sig = tuple(
            (e.id, e.type, e.x, e.y, e.color, e.size,
             e.bold, e.italic, e.text, e.metric, e.format, e.source)
            for e in s.user_overlay_elements
        )
        return (id(theme.config), visual_size, sensor_tuple, clock_tuple,
                user_sig)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _visual_size(base: tuple[int, int], orientation: int) -> tuple[int, int]:
        """Render canvas dimensions = ``base`` swapped for 90/270 orientation."""
        w, h = base
        return (h, w) if orientation in (90, 270) else (w, h)

    @staticmethod
    def _resolve_profile(
        info: ProductInfo, override: DeviceProfile | None,
    ) -> DeviceProfile:
        """Pick the profile to drive frame building.

        Preference: caller-supplied (from a live handshake) → registry
        FBL lookup → synthesized fallback matching the pre-profile
        behavior (native_resolution, RGB565, no rotation).
        """
        if override is not None:
            return override
        if info.fbl is not None:
            return get_profile(info.fbl)
        w, h = info.native_resolution
        return DeviceProfile(width=w, height=h, jpeg=False, rotate=False)

    def _encode_for_wire(self, surface: Any, profile: DeviceProfile) -> bytes:
        if profile.jpeg:
            return self._r.encode_jpeg(surface)
        return self._r.encode_rgb565(surface)


# =========================================================================
# Pure-Python fit algorithm
# =========================================================================


def _fit(
    mode: FitMode,
    src_w: int, src_h: int,
    dst_w: int, dst_h: int,
) -> tuple[int, int, int, int]:
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
