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

from ..core.models import (
    SPLIT_OVERLAY_MAP,
    DeviceSettings,
    FitMode,
    ProductInfo,
    RawFrame,
    Theme,
)
from ..core.ports import Renderer
from ..core.protocol import DeviceProfile, get_profile
from ._clock import compute_clock
from .media import MediaService
from .overlay import OverlayService, resolve_overlay_elements
from .settings import Settings
from .theme import ThemeService
from .video_cache import VideoFrameCache

log = logging.getLogger(__name__)


_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


# Devices whose canvas is widescreen split-eligible.  Currently just
# Levita (1600x720); listed as a set so future widescreen panels are
# a one-line addition.
_WIDESCREEN_SPLIT_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset({
    (1600, 720),
    (720, 1600),    # rotated portrait of the same panel
})


def _is_widescreen_split(visual_size: tuple[int, int]) -> bool:
    """True when ``visual_size`` is a widescreen panel that supports
    the Dynamic Island split overlay.  Gates ``_composite_split_overlay``
    so non-widescreen devices skip the load+composite entirely.
    """
    return visual_size in _WIDESCREEN_SPLIT_RESOLUTIONS


def _cutout_is_right_side(
    info: ProductInfo, visual_size: tuple[int, int],
) -> bool:
    """True when the device's PanelCutout sits past the canvas midline.

    Mirrors legacy ``RenderPipeline._cutout_is_right_side`` — the
    Levita SKU has its camera cutout on the right side of the panel,
    so the left-side split assets need a horizontal flip.  Devices
    without a PanelCutout (or whose cutout is left-of-midline) keep
    the assets as-authored.
    """
    cutout = info.panel_cutout
    if cutout is None:
        return False
    w, _ = visual_size
    return cutout.x + cutout.w // 2 > w // 2


# =========================================================================
# SceneCache — per-device layered cache
# =========================================================================


@dataclass
class SceneCache:
    """Two surfaces + the invalidation keys that govern them.

    ``frame_key`` and ``frame_bytes`` cache the final wire-encoded
    frame so a tick where nothing changed (cache HIT on bg+overlay
    AND identical brightness/orientation/split/rotate) can return the
    last frame directly — skipping composite + brightness + rotate +
    encode entirely.
    """

    # bg_mask layer
    bg_mask_surface: Any
    bg_mask_key: tuple[Any, ...]       # (theme_path, visual_size, video_cursor)

    # overlay layer
    overlay_surface: Any
    overlay_key: tuple[Any, ...]       # (config_id, visual_size, sensor_tuple)

    # Final wire-bytes cache — keyed on the full pipeline inputs so a
    # tick with no changes returns identical bytes without re-encoding.
    frame_key: tuple[Any, ...] | None = None
    frame_bytes: bytes | None = None

    # The final composited + rotated surface, captured just before the
    # wire encode.  The GUI preview reuses THIS instead of re-running the
    # whole pipeline a second time per tick (see ``rendered_surface``) —
    # it's byte-for-byte what the device received.
    preview_surface: Any = None


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
        # Per-device pre-composited animation-frame cache (bg+mask for
        # every video frame, built once).  Decouples the animation loop
        # from the per-frame ``_build_bg_mask`` rebuild — a tick after
        # the first build is a list lookup, not a decode+fit+composite.
        # Built lazily, dropped by ``invalidate`` alongside the scene
        # cache so any Command that changes the bg+mask layer rebuilds.
        self._video_caches: dict[str, VideoFrameCache] = {}
        # Cache of loaded split-overlay surfaces keyed by
        # (style, rotation, mirrored).  Loaded lazily on first
        # widescreen render so non-Levita devices pay nothing.
        self._split_cache: dict[tuple[int, int, bool], Any] = {}
        # Per-device scene-cache hit/miss state — used to log INFO on
        # TRANSITION only (matches Phase-0's ``_log_tick_skip``
        # shape).  Per-tick HIT/MISS stays at DEBUG so 15 fps doesn't
        # flood the log; transitions surface "froze on first frame"
        # regressions in one grep.
        self._cache_state: dict[str, tuple[bool, bool]] = {}

    # ── Top-level pipeline ────────────────────────────────────────────

    def _compose_geometry(
        self, profile: DeviceProfile, theme: Theme,
    ) -> tuple[tuple[int, int], bool]:
        """Return ``(base compose size, is_portrait_compose)`` for this frame.

        A non-square ``rotate=True`` panel is physically portrait.  When the
        active theme is portrait-authored (``theme.resolution`` taller than
        wide) the canvas composes at portrait dims so the content isn't
        stretched into the landscape canvas, and the device 90° rotate is
        skipped (the portrait composition already matches the portrait wire
        buffer).  Landscape themes (and themes with no declared size) keep
        composing landscape + rotate — content-matched, so the working
        widescreen panels are unchanged. (#136)
        """
        w, h = profile.resolution
        tw, th = theme.resolution
        portrait = bool(profile.rotate and w != h and tw > 0 and th > tw)
        return ((h, w) if portrait else (w, h)), portrait

    def composed_canvas_size(
        self, info: ProductInfo, theme: Theme,
        profile: DeviceProfile | None, orientation: int,
    ) -> tuple[int, int]:
        """The render canvas size for the active theme, incl. portrait
        composition + user orientation.  The GUI sizes its preview bezel from
        this so the frame asset + label match what the panel shows (#136).
        """
        resolved = self._resolve_profile(info, profile)
        base_size, _ = self._compose_geometry(resolved, theme)
        return self._visual_size(base_size, orientation)

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
        base_size, portrait = self._compose_geometry(resolved_profile, theme)

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
        # State-transition log at INFO — a "frozen on frame N" bug
        # surfaces as cache flipping to all-HIT and staying there
        # while a video is supposedly playing.  Per-tick stays DEBUG
        # above; this only fires when the state actually changes.
        self._log_cache_transition(info.key, bg_hit, ovl_hit)

        # Full-pipeline cache key: when every input that affects the
        # final wire bytes matches the last tick, return the cached
        # bytes directly.  Lifts the legacy ``OverlayService.would_change``
        # optimisation (skip on no-op tick) up to the byte level.
        frame_key = (
            bg_key, overlay_key,
            s.brightness, s.orientation, s.split_mode,
            resolved_profile.rotate,
            id(resolved_profile),
        )
        if (
            scene is not None
            and scene.frame_key == frame_key
            and scene.frame_bytes is not None
        ):
            log.debug("build_frame %s: full-pipeline cache HIT (%d bytes)",
                      info.key, len(scene.frame_bytes))
            return scene.frame_bytes

        bg_surface, overlay_surface = self._resolve_bg_overlay(
            info, theme, sensors, visual_size, clock,
            scene, bg_key, overlay_key,
        )

        # Compose: bg+mask below, overlay on top
        surface = self._r.composite(bg_surface, overlay_surface, position=(0, 0))

        # Split-mode overlay (Dynamic Island) — Levita / 1600x720
        # widescreen only.  Picks an asset by (split_mode, rotation),
        # mirrors it horizontally when the panel's cutout sits on the
        # right side (PanelCutout from the variant override).  No-op
        # when split_mode==0 or the LCD isn't widescreen.
        if s.split_mode and _is_widescreen_split(visual_size):
            surface = self._composite_split_overlay(
                info, s.split_mode, s.orientation, visual_size, surface,
            )

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
        # ("RGB565-LE rotated" in legacy report output).  Skipped when we
        # already composed the canvas at portrait dims for a portrait theme
        # (the composition matches the portrait wire buffer directly). (#136)
        if resolved_profile.rotate and not portrait:
            log.debug("build_frame %s: device rotate 90° (portrait panel)",
                      info.key)
            surface = self._r.rotate(surface, 90)

        encoded = self._encode_for_wire(surface, resolved_profile)
        self._scenes[info.key] = SceneCache(
            bg_mask_surface=bg_surface, bg_mask_key=bg_key,
            overlay_surface=overlay_surface, overlay_key=overlay_key,
            frame_key=frame_key, frame_bytes=encoded,
            preview_surface=surface,
        )
        return encoded

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
        log.debug("build_preview_surface: key=%s theme=%s",
                  info.key, theme.name)
        resolved_profile = self._resolve_profile(info, profile)
        base_size, portrait = self._compose_geometry(resolved_profile, theme)

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

        # Shared resolve — same video-cache fast path build_frame uses, so a
        # video theme's preview is a cache lookup, not a fresh per-tick decode.
        bg_surface, overlay_surface = self._resolve_bg_overlay(
            info, theme, sensors, visual_size, clock,
            scene, bg_key, overlay_key,
        )
        self._scenes[info.key] = SceneCache(
            bg_mask_surface=bg_surface, bg_mask_key=bg_key,
            overlay_surface=overlay_surface, overlay_key=overlay_key,
        )

        surface = self._r.composite(bg_surface, overlay_surface, position=(0, 0))
        return self._apply_post_processing(
            surface, s, resolved_profile, compose_portrait=portrait,
        )

    def _resolve_bg_overlay(
        self,
        info: ProductInfo,
        theme: Theme,
        sensors: dict[str, float],
        visual_size: tuple[int, int],
        clock: dict[str, str],
        scene: SceneCache | None,
        bg_key: tuple[Any, ...],
        overlay_key: tuple[Any, ...],
    ) -> tuple[Any, Any]:
        """Resolve the (bg+mask, overlay) surfaces for a tick.

        Shared by ``build_frame`` (wire) and ``build_preview_surface``
        (GUI) so both go through the same caches — most importantly the
        VideoFrameCache: a multi-frame video draws a new bg every tick
        (the cursor is in ``bg_key`` → always a single-surface scene
        MISS), so without the frame cache every tick re-ran the full
        decode+fit+composite.  ``get_surface`` returns exactly what
        ``_build_bg_mask`` produced for that cursor (the parity gate).
        """
        if scene is not None and scene.bg_mask_key == bg_key:
            bg_surface = scene.bg_mask_surface
        else:
            video_cache = self._video_cache(info, theme, visual_size)
            if video_cache is not None:
                pb = self._media.playback(info.key)
                cursor = pb.cursor if pb is not None else 0
                bg_surface = video_cache.get_surface(cursor)
                if bg_surface is None:
                    log.warning(
                        "resolve_bg_overlay %s: video cache miss at cursor "
                        "%d (frames=%d) — rebuilding bg directly",
                        info.key, cursor, video_cache.frame_count,
                    )
                    bg_surface = self._build_bg_mask(info, theme, visual_size)
                else:
                    log.debug("resolve_bg_overlay %s: bg from video cache "
                              "(cursor=%d/%d)", info.key, cursor,
                              video_cache.frame_count)
            else:
                bg_surface = self._build_bg_mask(info, theme, visual_size)

        if scene is not None and scene.overlay_key == overlay_key:
            overlay_surface = scene.overlay_surface
        else:
            overlay_surface = self._build_overlay(
                info, theme, sensors, visual_size, clock,
            )
        return bg_surface, overlay_surface

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
        log.info("build_solid_color_frame: key=%s color=%s", info.key, color)
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
        log.debug("build_screencast_frame: key=%s", info.key)
        resolved = self._resolve_profile(info, profile)
        target_w, target_h = resolved.resolution

        surface = self._r.from_raw_rgb24(frame)
        if (
            self._r.surface_size(surface) != (target_w, target_h)
        ):
            surface = self._r.resize(surface, target_w, target_h)

        s = self._settings.for_device(info.key)
        surface = self._apply_post_processing(surface, s, resolved)
        return self._encode_for_wire(surface, resolved)

    def build_image_frame(
        self,
        *,
        info: ProductInfo,
        path: Path,
        profile: DeviceProfile | None = None,
    ) -> bytes:
        """Encode an arbitrary image file for the device wire — no persistence.

        Used by :class:`SendImage` Command + ``trcc display send-image``
        CLI to push a one-off image without staging a theme (no
        ``user_content_dir/single-image/`` directory created; no
        ``DeviceSettings.background_path`` mutation).  Honors per-device
        brightness + orientation + device-side rotation so the displayed
        image matches the rest of the LCD's state.

        Raises ``TrccError`` if the image can't be opened — caller
        catches and returns a structured Result.
        """
        log.info("build_image_frame: key=%s path=%s", info.key, path)
        resolved = self._resolve_profile(info, profile)
        target_w, target_h = resolved.resolution

        surface = self._r.open_image(path)
        if self._r.surface_size(surface) != (target_w, target_h):
            surface = self._r.resize(surface, target_w, target_h)

        s = self._settings.for_device(info.key)
        surface = self._apply_post_processing(surface, s, resolved)
        return self._encode_for_wire(surface, resolved)

    def _apply_post_processing(
        self,
        surface: Any,
        s: DeviceSettings,
        resolved: DeviceProfile,
        *,
        compose_portrait: bool = False,
    ) -> Any:
        """Apply user brightness, user orientation, and device-side rotation.

        Shared tail of every frame build that respects per-device
        settings (build_frame, build_screencast_frame, build_image_frame).
        ``build_solid_color_frame`` intentionally calls only the
        brightness step because user-orientation on a uniform fill is a
        no-op and the helper's extra rotate calls would burn cycles for
        no visible change.  ``compose_portrait`` skips the device 90° rotate
        when the canvas was already composed at portrait dims (#136).
        """
        log.debug("_apply_post_processing: brightness=%d orientation=%d rotate=%s "
                  "compose_portrait=%s",
                  s.brightness, s.orientation, resolved.rotate, compose_portrait)
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)
        if s.orientation:
            surface = self._r.rotate(surface, 360 - s.orientation)
        if resolved.rotate and not compose_portrait:
            surface = self._r.rotate(surface, 90)
        return surface

    def rendered_surface(self, key: str) -> Any | None:
        """The last frame's pre-encode surface for *key*, or None.

        The GUI preview reuses this instead of re-rendering the whole
        pipeline a second time per tick — it's exactly what ``build_frame``
        composited + rotated and handed to the wire encode.  None before
        the first frame is built (pre-load) or after ``invalidate``.
        """
        scene = self._scenes.get(key)
        surface = scene.preview_surface if scene is not None else None
        log.debug("rendered_surface: key=%s available=%s",
                  key, surface is not None)
        return surface

    def invalidate(self, key: str) -> None:
        """Drop the scene cache for *key* (called on disconnect / theme change)."""
        log.info("invalidate: key=%s", key)
        self._scenes.pop(key, None)
        # Drop the animation-frame cache too — every Command that mutates
        # the bg+mask layer (PlayVideo / ApplyMask / SetFitMode /
        # SetBackgroundMode / LoadTheme …) already calls this, so the
        # cache rebuilds from the new layer on the next video tick.
        self._video_caches.pop(key, None)
        # Reset the transition tracker too, so the next build_frame for
        # this key logs INFO when the cache state first appears
        # post-invalidation (instead of comparing against stale state).
        self._cache_state.pop(key, None)

    def invalidate_all(self) -> None:
        log.info("invalidate_all: scenes=%d video_caches=%d",
                 len(self._scenes), len(self._video_caches))
        self._scenes.clear()
        self._video_caches.clear()
        self._cache_state.clear()

    def _log_cache_transition(self, key: str, bg_hit: bool,
                              ovl_hit: bool) -> None:
        """Log INFO on the first call AND every state flip per device.

        Per-tick HIT/MISS already logs at DEBUG in ``build_frame``; this
        is the load-bearing diagnostic: "video should be animating but
        cache is steady-HIT" is the shape of every "frozen on frame N"
        regression, and surfaces as a missing flip in this log.
        """
        new_state = (bg_hit, ovl_hit)
        prev_state = self._cache_state.get(key)
        if prev_state == new_state:
            return
        log.info(
            "build_frame %s: cache state %s → bg=%s overlay=%s",
            key,
            "(first)" if prev_state is None
            else f"bg={prev_state[0]} overlay={prev_state[1]}",
            "HIT" if bg_hit else "MISS",
            "HIT" if ovl_hit else "MISS",
        )
        self._cache_state[key] = new_state

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
        log.info("encode_boot_anim_frame: path=%s resolution=%dx%d",
                 image_path, *resolution)
        surface = self._r.open_image(image_path)
        if self._r.surface_size(surface) != resolution:
            surface = self._r.resize(surface, *resolution)
        return self._r.encode_rgb565(surface)

    def encode_png(self, surface: Any) -> bytes:
        """PNG-encode a preview surface (lossless — API preview snapshot).

        A public encode seam over the Renderer so callers (the preview
        routes) don't reach the private ``_r``.
        """
        log.debug("encode_png: encoding preview surface")
        return self._r.encode_png(surface)

    def encode_jpeg(self, surface: Any, quality: int = 95) -> bytes:
        """JPEG-encode a preview surface (the WebSocket preview stream)."""
        log.debug("encode_jpeg: quality=%d", quality)
        return self._r.encode_jpeg(surface, quality)

    # ── Layer 1: background + mask ────────────────────────────────────

    def _video_cache(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> VideoFrameCache | None:
        """Lazily build (and return) the animation-frame cache for *info*.

        Gated on a multi-frame video playback rendered as the theme
        background.  For ``color`` / ``transparent`` / static-image
        themes there's nothing per-frame to cache, so this returns None
        and the caller keeps the single-surface scene cache.

        The cache holds, per cursor, exactly what ``_build_bg_mask``
        produces for that frame: built by seeking the playback to each
        cursor and reusing ``_build_bg_mask`` itself — so a cached
        surface is byte-identical to the live path (the parity gate).
        Brightness stays at 100 (passthrough): ``build_frame`` keeps
        applying brightness on the composited surface, so a brightness
        change needs no rebuild — matching ``_bg_mask_key`` which omits
        brightness.  Invalidation is explicit (``invalidate``), so once
        an active cache exists it's valid until a layer Command drops it.
        """
        s = self._settings.for_device(info.key)
        if s.background_mode != "theme":
            return None
        playback = self._media.playback(info.key)
        if playback is None or len(playback.frames) <= 1:
            return None

        cache = self._video_caches.get(info.key)
        if (
            cache is not None
            and cache.active
            and cache.frame_count == len(playback.frames)
        ):
            return cache

        log.info(
            "_video_cache %s: building %d-frame cache (theme=%r, %dx%d)",
            info.key, len(playback.frames), theme.name,
            visual_size[0], visual_size[1],
        )
        cache = VideoFrameCache()
        masked: list[Any] = []
        saved_cursor = playback.cursor
        try:
            for index in range(len(playback.frames)):
                playback.cursor = index
                masked.append(self._build_bg_mask(info, theme, visual_size))
        finally:
            playback.cursor = saved_cursor
        # ``_build_bg_mask`` already produced finished bg+mask surfaces per
        # cursor — the cache just holds them for per-tick lookup.
        cache.build(masked)
        self._video_caches[info.key] = cache
        return cache

    def _build_bg_mask(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> Any:
        """Compose fitted background + mask at visual size.

        Honors ``DeviceSettings.background_mode``:

          * ``'theme'`` (default) — paint the active theme's
            background (image / video frame / cloud override) onto
            the canvas, then composite the mask on top.
          * ``'color'`` — fill canvas with ``overlay_background``
            solid color, SKIP theme-bg paint, then composite mask.
            Used when the user wants a flat colored backdrop behind
            the overlay metrics.
          * ``'transparent'`` — SKIP both theme-bg paint AND the
            canvas pre-fill; canvas stays at its solid black init
            (RGB565 has no alpha; "transparent" effectively means
            "black, with the overlay drawn on top").  Used by the
            screencast pipeline where the captured frame is the
            background.
        """
        s = self._settings.for_device(info.key)
        mode = s.background_mode
        log.debug(
            "_build_bg_mask: key=%s mode=%s mask_visible=%s mask_path=%s "
            "fit=%s playback=%s",
            info.key, mode, s.mask_visible, s.mask_path,
            getattr(s.fit_mode, "value", s.fit_mode),
            (self._media.playback(info.key) is not None),
        )

        # Initial canvas — 'color' mode fills with the user's chosen
        # colour; 'theme' / 'transparent' start solid black.  RGB565
        # has no alpha on the wire so the alpha channel is moot
        # post-encode, but we keep 255 to avoid renderer quirks where
        # alpha=0 composite-blends to white (per
        # render-dc-divergence-audit).
        if mode == "color":
            r, g, b = s.overlay_background
            canvas = self._r.create_surface(
                *visual_size, color=(r, g, b, 255),
            )
            log.debug(
                "build_bg_mask %s: mode=color fill=%s — skipping theme bg",
                info.key, s.overlay_background,
            )
        else:
            canvas = self._r.create_surface(
                *visual_size, color=(0, 0, 0, 255),
            )

        # Paint the fitted theme background only in 'theme' mode.
        # 'color' has already painted; 'transparent' is intentionally
        # left at solid black so the overlay draws on a clean canvas.
        if mode == "theme":
            source = self._resolve_background(info, theme, visual_size)
            if source is not None:
                src_w, src_h = self._r.surface_size(source)
                dst_w, dst_h = visual_size
                fit_w, fit_h, off_x, off_y = _fit(
                    s.fit_mode, src_w, src_h, dst_w, dst_h,
                )
                log.debug(
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
        elif mode == "transparent":
            log.debug(
                "build_bg_mask %s: mode=transparent — skipping theme bg "
                "(canvas stays solid black; overlay draws on top)",
                info.key,
            )

        # Mask layer: per-device override (ApplyMask Command) takes
        # precedence over the theme's bundled mask; mask_visible=False
        # skips the layer entirely. Position defaults to (0, 0).
        mask_source = self._resolve_mask_source(s, theme)
        if mask_source is not None:
            mask = self._r.open_image(mask_source)
            mw, mh = self._r.surface_size(mask)
            position = s.mask_position or (0, 0)
            log.debug(
                "build_bg_mask %s: mask %s (%dx%d) at top-left (%d, %d) "
                "[visible=%s]",
                info.key, mask_source, mw, mh, position[0], position[1],
                s.mask_visible,
            )
            canvas = self._r.composite(canvas, mask, position=position)
        else:
            log.debug(
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
        log.debug(
            "_resolve_mask_source: mask_visible=%s mask_path=%s",
            device_settings.mask_visible, device_settings.mask_path,
        )
        if not device_settings.mask_visible:
            log.debug("_resolve_mask_source: mask_visible=False → None")
            return None
        if device_settings.mask_path is not None:
            override = Path(device_settings.mask_path)
            if override.exists():
                log.debug("_resolve_mask_source: using override %s", override)
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

        # Cloud-background override (DeviceSettings.background_path) —
        # takes precedence over the active theme's own bg.  Set by
        # LoadCloudTheme; cleared by LoadTheme on local-theme select.
        s = self._settings.for_device(info.key)
        if s.background_path:
            override = Path(s.background_path)
            if override.exists():
                log.debug(
                    "resolve_background %s: cloud background override → %s",
                    info.key, override,
                )
                path = override
                ext = path.suffix.lower()
                if ext in _VIDEO_EXTS:
                    try:
                        playback = self._media.load_video(
                            device_key=info.key, path=path, size=visual_size,
                        )
                    except Exception as e:
                        log.warning(
                            "resolve_background %s: override video decode "
                            "failed for %s: %s: %s",
                            info.key, path.name, type(e).__name__, e,
                        )
                        return None
                    log.debug(
                        "resolve_background %s: override video loaded "
                        "(%d frames)", info.key, len(playback.frames),
                    )
                    frame = playback.current
                    return (
                        self._r.from_raw_rgb24(frame) if frame else None
                    )
                if ext in _IMAGE_EXTS:
                    return self._r.open_image(path)
            else:
                log.warning(
                    "resolve_background %s: override %s does not exist — "
                    "falling back to theme background",
                    info.key, override,
                )

        path = self._themes.background_path(theme)
        if path is None:
            log.warning(
                "resolve_background %s: theme %r has no background "
                "(no 00.png or Theme.{mp4,mov,webm,zt} in %s)",
                info.key, theme.name, theme.path,
            )
            return None
        ext = path.suffix.lower()
        log.debug("resolve_background %s: theme %r → %s",
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
            log.debug(
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
        # ONE effective overlay layout (legacy's single ``self.config``),
        # resolved by precedence user > mask > theme — each REPLACES, never
        # stacks.  The result becomes the render config's ``elements`` and
        # NO separate user layer is passed, so every element draws exactly
        # once (the cutover's additive theme+user path drew each twice).
        elements = resolve_overlay_elements(
            theme.config, s.mask_overlay_elements, s.user_overlay_elements,
        )
        config_for_render = {**theme.config, "elements": elements}
        layout = (
            "user" if s.user_overlay_elements
            else "mask" if s.mask_overlay_elements is not None
            else "theme"
        )
        log.debug(
            "build_overlay %s: theme=%r layout=%s (%d element(s)) "
            "[theme=%d mask=%s user=%d] overlay_enabled=%s",
            info.key, theme.name, layout, len(elements),
            len(theme.config.get("elements") or []),
            (len(s.mask_overlay_elements)
             if s.mask_overlay_elements is not None else None),
            len(s.user_overlay_elements),
            theme.config.get("overlay_enabled", True),
        )
        # ``temp_unit`` flows from per-device settings — kept in sync
        # with AppSettings.temp_unit by SetTempUnit Command.  The
        # renderer is the single conversion site (sensor sources always
        # deliver °C; rendering converts to °F when requested).
        return self._overlay.render(
            overlay_canvas, config_for_render, sensors,
            clock=clock, temp_unit=s.temp_unit,
        )

    # ── Cache keys ────────────────────────────────────────────────────

    def _bg_mask_key(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> tuple[Any, ...]:
        # Cursor inclusion mirrors ``_resolve_background``'s precedence:
        # if a live playback exists for this device, the rendered bg is
        # ``playback.current`` (per-frame), regardless of whether the
        # active theme's bundled background is static or video.  Asking
        # ``themes.background_path(theme)`` alone misses the cloud-bg
        # override case (active theme = static 00.png, but a cloud
        # video overrides on top via ``DeviceSettings.background_path``)
        # — the cache key would stay constant across ticks, every tick
        # would HIT, and the LCD would freeze on the first-rendered
        # frame.  Consulting MediaService directly fixes that.
        cursor: int | None = None
        pb = self._media.playback(info.key)
        if pb is not None and pb.frames:
            cursor = pb.cursor
        else:
            path = self._themes.background_path(theme)
            if path is not None and path.suffix.lower() in _VIDEO_EXTS:
                # Defensive: video-backed theme without a loaded
                # playback shouldn't happen post-Phase-1 (LoadTheme
                # auto-dispatches PlayVideo), but pin cursor=0 so the
                # key stays distinct from static-theme keys.
                cursor = 0
        # Mask state belongs in this key so the bg+mask layer rebuilds when
        # ApplyMask / SetMaskPosition / SetMaskVisible run. The Commands
        # already explicitly invalidate, but including it defends against
        # any path that mutates Settings without going through Commands.
        s = self._settings.for_device(info.key)
        mask_sig = (s.mask_path, s.mask_position, s.mask_visible, s.fit_mode)
        # ``background_path`` participates in the key so two cloud
        # videos played back-to-back (each starting at cursor=0) don't
        # share a cache entry — PlayVideo already calls
        # _invalidate_scene, but keying on the override path is the
        # explicit contract.
        bg_override = s.background_path
        # ``background_mode`` + ``overlay_background`` colour also
        # affect what _build_bg_mask paints — SetBackgroundMode and
        # SetOverlayBackground invalidate explicitly, but keying on
        # them here is the explicit contract (same defence-in-depth
        # the mask_sig provides).
        bg_mode_sig = (s.background_mode, s.overlay_background)
        return (
            str(theme.path), bg_override, visual_size,
            cursor, mask_sig, bg_mode_sig,
        )

    def _overlay_key(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
        sensors: dict[str, float],
        clock: dict[str, str],
    ) -> tuple[Any, ...]:
        # Sensors turn into a sorted tuple of (id, raw value).  Earlier
        # versions rounded to 1 decimal as a perf optimization — but
        # CPU temps that hover within a 0.1 °C band then NEVER rebuilt
        # the overlay between minute boundaries (clock element was the
        # only thing busting the cache), so users saw "frozen" metric
        # readouts.  Raw values cost ~1 overlay rebuild per metrics
        # tick (every refresh_interval_s, default 2 s) — cheap on a
        # 320×320 panel, and the user-visible "yes, it's reading my
        # sensors" feedback is worth it.
        sensor_tuple = tuple(sorted(sensors.items()))
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
        # Mask overlay elements participate in the cache key so the
        # overlay layer rebuilds when ApplyMask / SetMaskPath(None)
        # change the layout — keeps the mask layout surviving theme
        # swaps without leaking the previous mask's state.
        mask_overlay_sig: tuple[Any, ...] = (
            tuple(
                (e.id, e.type, e.x, e.y, e.color, e.size,
                 e.bold, e.italic, e.text, e.metric, e.format, e.source)
                for e in s.mask_overlay_elements
            )
            if s.mask_overlay_elements is not None
            else ()
        )
        # Temp unit participates in the key so toggling °C ↔ °F via
        # SetTempUnit busts the overlay cache and the next render
        # picks up the new format-string + value-conversion path.
        return (id(theme.config), visual_size, sensor_tuple, clock_tuple,
                user_sig, mask_overlay_sig, s.temp_unit)

    # ── Split-mode overlay (Dynamic Island / Levita widescreen) ───────

    def _composite_split_overlay(
        self,
        info: ProductInfo,
        split_mode: int,
        rotation: int,
        visual_size: tuple[int, int],
        surface: Any,
    ) -> Any:
        """Composite the Dynamic Island PNG over ``surface`` (in place).

        Picks the asset by ``(split_mode, rotation)`` from
        ``SPLIT_OVERLAY_MAP``.  Mirrors the asset horizontally when the
        device's PanelCutout sits past the canvas midline (Levita's
        cutout is on the right side; the assets are authored for the
        left side).  Cached after first load so non-Levita devices
        pay nothing and Levita devices only pay once per (style,
        rotation, mirrored) tuple.
        """
        asset_name = SPLIT_OVERLAY_MAP.get((split_mode, rotation))
        if asset_name is None:
            log.warning(
                "split overlay %s: no asset for (style=%d, rotation=%d)",
                info.key, split_mode, rotation,
            )
            return surface
        mirrored = _cutout_is_right_side(info, visual_size)
        cache_key = (split_mode, rotation, mirrored)
        overlay = self._split_cache.get(cache_key)
        if overlay is None:
            overlay = self._load_split_asset(asset_name)
            if overlay is None:
                return surface
            if mirrored:
                overlay = self._r.flip_horizontal(overlay)
            self._split_cache[cache_key] = overlay
            log.info(
                "split overlay %s: cached (%s, mirrored=%s)",
                info.key, asset_name, mirrored,
            )
        try:
            return self._r.composite(surface, overlay, position=(0, 0))
        except (OSError, ValueError, RuntimeError) as e:
            log.warning("split overlay %s: composite failed: %s: %s",
                        info.key, type(e).__name__, e)
            return surface

    def _load_split_asset(self, asset_name: str) -> Any | None:
        """Load a split-overlay PNG from ``ui/gui/assets/``.

        Returns None when the asset is missing or the renderer can't
        open it.  Renderer.open_image already raises a tolerant
        exception; we log + drop the overlay rather than fail the
        whole frame build.
        """
        from pathlib import Path
        asset_dir = (
            Path(__file__).resolve().parents[1] / "ui" / "gui" / "assets"
        )
        path = asset_dir / asset_name
        if not path.is_file():
            log.warning("split overlay asset missing: %s", path)
            return None
        try:
            return self._r.open_image(path)
        except (OSError, ValueError, RuntimeError) as e:
            log.warning("split overlay asset load failed for %s: %s: %s",
                        path, type(e).__name__, e)
            return None

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
        # Device-only encode baseline: panels with a fixed hardware-mount
        # rotation (FW360 PM=6 → 180°) need their WIRE frame pre-rotated so the
        # glass reads upright.  This is the single chokepoint every send path
        # funnels through; the preview path never calls it, so the GUI preview
        # stays upright — exactly the reporter's ask.  rotate() returns a new
        # surface, so the caller's stored preview_surface is untouched. (#137)
        if profile.encode_baseline:
            surface = self._r.rotate(surface, profile.encode_baseline)
        if profile.jpeg:
            return self._r.encode_jpeg(surface)
        return self._r.encode_rgb565(surface, profile.byte_order)


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
