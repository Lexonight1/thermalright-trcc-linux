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
from .overlay import OverlayService
from .settings import Settings
from .theme import ThemeService

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
        # ("RGB565-LE rotated" in legacy report output).
        if resolved_profile.rotate:
            log.debug("build_frame %s: device rotate 90° (portrait panel)",
                      info.key)
            surface = self._r.rotate(surface, 90)

        encoded = self._encode_for_wire(surface, resolved_profile)
        self._scenes[info.key] = SceneCache(
            bg_mask_surface=bg_surface, bg_mask_key=bg_key,
            overlay_surface=overlay_surface, overlay_key=overlay_key,
            frame_key=frame_key, frame_bytes=encoded,
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
        resolved = self._resolve_profile(info, profile)
        target_w, target_h = resolved.resolution

        surface = self._r.open_image(path)
        if self._r.surface_size(surface) != (target_w, target_h):
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
        # Reset the transition tracker too, so the next build_frame for
        # this key logs INFO when the cache state first appears
        # post-invalidation (instead of comparing against stale state).
        self._cache_state.pop(key, None)

    def invalidate_all(self) -> None:
        self._scenes.clear()
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
            log.info(
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
        elif mode == "transparent":
            log.info(
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

        # Cloud-background override (DeviceSettings.background_path) —
        # takes precedence over the active theme's own bg.  Set by
        # LoadCloudTheme; cleared by LoadTheme on local-theme select.
        s = self._settings.for_device(info.key)
        if s.background_path:
            override = Path(s.background_path)
            if override.exists():
                log.info(
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
                    log.info(
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
        # Mask-supplied overlay elements (set by ApplyMask) override the
        # active theme's elements at render time — so a mask's metric
        # layout survives a theme swap.  Persistent on DeviceSettings,
        # not on theme.config.
        mask_dicts = (
            [e.to_dict() for e in s.mask_overlay_elements]
            if s.mask_overlay_elements is not None else None
        )
        config_for_render = theme.config
        if mask_dicts is not None:
            config_for_render = {**theme.config, "elements": mask_dicts}
            log.info(
                "build_overlay %s: theme=%r theme_elements=%d "
                "mask_elements=%d (mask layout OVERRIDES) "
                "user_elements=%d overlay_enabled=%s",
                info.key, theme.name, len(theme_elements), len(mask_dicts),
                len(user_dicts), theme.config.get("overlay_enabled", True),
            )
        else:
            log.info(
                "build_overlay %s: theme=%r theme_elements=%d "
                "user_elements=%d overlay_enabled=%s",
                info.key, theme.name, len(theme_elements), len(user_dicts),
                theme.config.get("overlay_enabled", True),
            )
        # ``temp_unit`` flows from per-device settings — kept in sync
        # with AppSettings.temp_unit by SetTempUnit Command.  The
        # renderer is the single conversion site (sensor sources always
        # deliver °C; rendering converts to °F when requested).
        return self._overlay.render(
            overlay_canvas, config_for_render, sensors,
            clock=clock, user_elements=user_dicts,
            temp_unit=s.temp_unit,
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
