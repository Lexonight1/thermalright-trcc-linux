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

from ..core._safe import is_under
from ..core.geometry import content_is_portrait, plan_orientation
from ..core.models import (
    SPLIT_OVERLAY_MAP,
    DeviceSettings,
    FitMode,
    ProductInfo,
    RawFrame,
    RenderContent,
    Theme,
)
from ..core.ports import ContentStore, Paths, Renderer
from ..core.protocol import (
    DeviceProfile,
    get_profile,
    wire_angle,
)
from ._clock import compute_clock
from .media import MediaService
from .overlay import OverlayService, overlay_source, resolve_overlay_elements
from .settings import Settings

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
        themes: ContentStore,
        overlay: OverlayService,
        settings: Settings,
        media: MediaService,
        paths: Paths,
    ) -> None:
        self._r = renderer
        self._themes = themes
        self._overlay = overlay
        self._settings = settings
        self._media = media
        # Content origin (program/cloud vs user upload) drives the theme-bg
        # fill rule: program content is authored-for-canvas → the C# native-
        # or-black width test (no letterbox); user content keeps fit_mode
        # scaling.  Same axis as the PlayVideo decode-size gate.
        self._paths = paths
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

    @staticmethod
    def _content_is_portrait(
        theme: Theme, profile: DeviceProfile, s: DeviceSettings,
    ) -> bool:
        """Portrait decision for the render path — see
        :func:`trcc.core.geometry.content_is_portrait` (the shared source, also
        used by ``SaveTheme`` so save + reload agree on orientation)."""
        return content_is_portrait(theme, profile, s.mask_path, s.mask_visible)

    @staticmethod
    def _compose_geometry(
        profile: DeviceProfile, orientation: int,
        content_is_portrait: bool = True,
    ) -> tuple[tuple[int, int], bool, int]:
        """Compose canvas + portrait flag + whole-composite rotation angle.

        Thin adapter over the pure :func:`trcc.core.geometry.plan_orientation`
        (the single source for the decision — shared by wire + preview + the
        GUI bezel).  Returns the legacy ``(canvas, portrait, post_rotate)``
        tuple the call sites unpack.
        """
        plan = plan_orientation(profile, orientation, content_is_portrait)
        return plan.canvas, plan.is_portrait_content, plan.post_rotate

    def composed_canvas_size(
        self, info: ProductInfo, theme: Theme,
        profile: DeviceProfile | None, orientation: int,
    ) -> tuple[int, int]:
        """The render canvas size for the active theme, incl. portrait
        composition + user orientation.  The GUI sizes its preview bezel from
        this so the frame asset + label match what the panel shows (#136).
        """
        resolved = self._resolve_profile(info, profile)
        s = self._settings.for_device(info.key)
        canvas, portrait, post_rotate = self._compose_geometry(
            resolved, orientation, self._content_is_portrait(theme, resolved, s),
        )
        # The bezel shows the DISPLAYED frame: a whole-composite rotation
        # transposes the landscape compose canvas to its portrait output size.
        if post_rotate in (90, 270):
            canvas = (canvas[1], canvas[0])
        # On-change (preview sizing), not per-frame — INFO so the orientation
        # decision is visible at the default level: panel native size + rotate
        # flag, the portrait-compose decision, the user orientation, → canvas.
        log.info(
            "composed_canvas_size %s: native=%s rotate=%s theme=%r "
            "portrait-compose=%s orientation=%d → canvas=%dx%d",
            info.key, resolved.resolution, resolved.rotate, theme.name,
            portrait, orientation, canvas[0], canvas[1],
        )
        return canvas

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
        s = self._settings.for_device(info.key)
        visual_size, portrait, post_rotate = self._compose_geometry(
            resolved_profile, s.orientation,
            self._content_is_portrait(theme, resolved_profile, s),
        )

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

        if post_rotate:
            # Landscape-only theme on a rotate panel at 90/270: everything was
            # composed aligned on the native LANDSCAPE canvas (no clip, no
            # letterbox); rotate the WHOLE composite as ONE unit into the
            # portrait buffer (legacy ``has_portrait_themes=False`` / the C#
            # oriented-output model).  bg, mask and text rotate together, so
            # they stay aligned.  The PREVIEW is this rotated result — what the
            # physically-rotated glass shows — so it is captured AFTER the
            # rotation (there is no upright portrait layout to show instead).
            log.debug("build_frame %s: rotate whole composite %d° "
                      "(landscape theme at portrait angle)", info.key, post_rotate)
            surface = self._r.rotate(surface, post_rotate)
            preview_surface = surface
            encoded = self._encode_for_wire(surface, resolved_profile)
            self._scenes[info.key] = SceneCache(
                bg_mask_surface=bg_surface, bg_mask_key=bg_key,
                overlay_surface=overlay_surface, overlay_key=overlay_key,
                frame_key=frame_key, frame_bytes=encoded,
                preview_surface=preview_surface,
            )
            return encoded

        # ── Wire rotation (0/180 for rotate panels; all angles for squares) ──
        # The composite so far is upright on the oriented canvas.  Rotation is a
        # WIRE concern and only a wire concern — both halves of it, the user's
        # display angle and the panel's physical mount.  The C# is unambiguous:
        # every RotateImg/Hei/Bu call site sits in a wire encoder (ImageToJpg,
        # ImageTo565, GifToJPG, GifTo565), the compose path contains no rotation
        # at all — no call, no Matrix — and RotateFlip appears nowhere in the
        # decompile.  ``portrait`` content is authored portrait (orientation
        # baked in) so it is never re-rotated.  (#136/#169)
        composite = surface

        # PREVIEW = the composite, exactly as composed.  The display angle does
        # NOT turn it, because in the C# it does not: SetMyUCScreenImage uses
        # the angle only to size and place the control (0 and 180 take the same
        # branch), GenerateImage uses it only to choose the canvas SHAPE and
        # draws content upright at raw coords either way, and SetUCState hands
        # that very image to the encoder, which rotates a copy for the glass.
        #
        # An upright preview is what makes the display angle usable as a mount
        # correction: an owner whose panel is bolted in rotated turns the dial
        # until the GLASS reads right, and the preview still tells the truth
        # (#224 Levita, #256).  It is also what keeps this surface EDITABLE —
        # overlay drag maps widget→LCD by scale alone with no angle term, so a
        # rotated preview inverts every drag — and SaveTheme snapshots this
        # surface into Theme.png, which must not be stored upside down.
        preview_surface = composite

        # WIRE rotation:
        #  * Every rotate panel folds mount + orientation into ONE C#-faithful
        #    angle from the encode table (``ENCODE_ROTATIONS`` →
        #    ``resolve_encode_angle``), resolved at handshake for the panel's
        #    resolution, encoder and SUB byte.  This used to be two paths — a
        #    ``wire_rotation`` for non-widescreen panels and the encode table
        #    for widescreen JPEG ones — which was the same C# switch read twice,
        #    and only one of the two copies carried the invert axis.
        #    Portrait content composes upright (post_rotate=0) and rides this
        #    SAME angle: a base-0 panel gets 270°/90° to transpose the portrait
        #    canvas onto the device's landscape buffer (the #234 640×480 squeeze
        #    fix); a base-90 panel gets 0°/180° (unchanged).  A landscape-only
        #    theme at 90/270 returned early via ``post_rotate`` above.
        #  * Squares + non-rotate panels: user orientation only.
        angle = wire_angle(resolved_profile, s.orientation, portrait)
        if angle % 360:
            log.debug("build_frame %s: wire rotate %d°", info.key, angle)
            surface = self._r.rotate(composite, angle)
        else:
            surface = composite

        encoded = self._encode_for_wire(surface, resolved_profile)
        self._scenes[info.key] = SceneCache(
            bg_mask_surface=bg_surface, bg_mask_key=bg_key,
            overlay_surface=overlay_surface, overlay_key=overlay_key,
            frame_key=frame_key, frame_bytes=encoded,
            preview_surface=preview_surface,
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
        s = self._settings.for_device(info.key)
        # ``_portrait`` is deliberately unused: the preview needs the canvas
        # size and the landscape-at-portrait-angle spin, but the portrait
        # content flag only ever gated rotations this path no longer applies.
        visual_size, _portrait, post_rotate = self._compose_geometry(
            resolved_profile, s.orientation,
            self._content_is_portrait(theme, resolved_profile, s),
        )

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
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)
        if post_rotate:
            # Landscape-only theme at a portrait angle: the whole composite was
            # deliberately composed on the native landscape canvas and is spun
            # into the portrait buffer as one unit, so the preview shows that
            # same spin — there is no upright portrait layout to show instead.
            # (The C# would draw solid black here; we do better on purpose.)
            log.debug("build_preview_surface %s: post_rotate %d° "
                      "(landscape theme at a portrait angle)",
                      info.key, post_rotate)
            return self._r.rotate(surface, post_rotate)
        # Composed upright, and returned upright.  The display angle sizes the
        # canvas but never turns the picture — see build_frame for the C# call
        # sites.  Logged with the angle that did NOT move it, so a report can
        # prove which behaviour the user was running.
        log.debug("build_preview_surface %s: composed upright, orientation=%d "
                  "not applied to the preview (wire-only)",
                  info.key, s.orientation)
        return surface

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
        (GUI) so both go through the same cache.

        A multi-frame video puts its cursor in ``bg_key``, so it MISSES
        the single-surface scene cache every tick and composes fresh.
        That is deliberate and matches the C#: ``GenerateImage``
        (UCScreenImage.cs:634) allocates a bitmap, draws background →
        mask → text, for every frame it sends, holding ONE mask bitmap
        rather than baking it into a copy per frame.

        We used to pre-composite every frame of the video up front
        instead.  On a 1600x720 panel that froze the UI for 3.96s and
        retained 4.13GB — and 1.84GB even for a stock 228-frame cloud
        theme (#264, #256).  Composing per tick costs 4.41ms more on
        that same panel, against a 41.7ms budget at the 24fps real
        themes report.  Static themes are unaffected: they still hit the
        scene cache above, because their ``bg_key`` does not move.
        """
        if scene is not None and scene.bg_mask_key == bg_key:
            bg_surface = scene.bg_mask_surface
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

        Bypasses the theme/overlay scene cache — composes a uniform surface on
        the oriented canvas and lands it on the device's wire buffer through
        the same tail every other single-image producer uses.  Used by the
        ``SendColor`` Command, ``SleepDevice`` (the shutdown blank) and the
        diagnostic CLI ``display color`` path.

        Brightness comes from per-device settings, so a user who has dimmed
        their display sees a dimmed color test instead of a bright wash.

        This method used to rotate a blanket 90° whenever ``profile.rotate``
        was set, and was deliberately exempted from the orientation tail on
        the grounds that turning a uniform fill changes no pixel.  True of the
        COLOUR and false of the DIMENSIONS — the header declares a shape, and a
        transposed buffer is painted only where the two overlap, which is what
        left part of the glass lit after ``SleepDevice`` (#262).  Gated by
        ``tests/test_solid_color_frame_shape.py``.
        """
        log.info("build_solid_color_frame: key=%s color=%s", info.key, color)
        resolved = self._resolve_profile(info, profile)
        s = self._settings.for_device(info.key)
        # The ORIENTED canvas, not the native one — the canvas and the wire
        # angle are one pair (see ``_orient_for_wire``).
        target_w, target_h = plan_orientation(
            resolved, s.orientation, False).canvas
        # Surface is opaque RGB; alpha not needed for solid fill.
        surface = self._r.create_surface(
            target_w, target_h, color=(*color, 255))
        surface = self._apply_post_processing(surface, s, resolved)
        surface = self._orient_for_wire(surface, s, resolved, info)
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
        s0 = self._settings.for_device(info.key)
        target_w, target_h = plan_orientation(
            resolved, s0.orientation, False).canvas

        surface = self._r.from_raw_rgb24(frame)
        if (
            self._r.surface_size(surface) != (target_w, target_h)
        ):
            surface = self._r.resize(surface, target_w, target_h)

        s = self._settings.for_device(info.key)
        surface = self._apply_post_processing(surface, s, resolved)
        surface = self._orient_for_wire(surface, s, resolved, info)
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
        s0 = self._settings.for_device(info.key)
        # The ORIENTED canvas, not the native one — see ``_orient_for_wire``.
        target_w, target_h = plan_orientation(
            resolved, s0.orientation, False).canvas

        surface = self._r.open_image(path)
        if self._r.surface_size(surface) != (target_w, target_h):
            surface = self._r.resize(surface, target_w, target_h)

        s = self._settings.for_device(info.key)
        surface = self._apply_post_processing(surface, s, resolved)
        surface = self._orient_for_wire(surface, s, resolved, info)
        return self._encode_for_wire(surface, resolved)

    def _apply_post_processing(
        self,
        surface: Any,
        s: DeviceSettings,
        resolved: DeviceProfile,
    ) -> Any:
        """Apply user brightness, user orientation, and device-side rotation.

        Shared tail of the two WIRE builds that own no rotation model of their
        own — ``build_screencast_frame``, ``build_image_frame`` and
        ``build_solid_color_frame`` — each encodes a single supplied or
        generated image rather than a composed theme.

        Wire only.  No preview path calls this: a preview is returned exactly
        as composed (see ``build_preview_surface``), because the C# rotates in
        its encoders and nowhere else.  The ``device_rotate`` /
        ``compose_portrait`` flags that used to carve a preview out of this
        method went with that caller.

        Brightness only.  It applied a ``360 − orientation`` + blanket 90° angle
        until ``8cc1520e`` moved wire rotation into ``_orient_for_wire``; every
        caller now pairs this with that method, so there is one wire-rotation
        authority rather than a per-caller model.
        """
        log.debug("_apply_post_processing: brightness=%d", s.brightness)
        if s.brightness != 100:
            surface = self._r.apply_brightness(surface, s.brightness)
        return surface

    def _orient_for_wire(
        self, surface: Any, s: DeviceSettings, resolved: DeviceProfile,
        info: ProductInfo,
    ) -> Any:
        """Rotate a single supplied image onto the device's wire buffer.

        The canvas and the angle are ONE pair: compose on the oriented canvas
        that ``plan_orientation`` picks, then rotate by ``wire_angle`` and the
        result lands on the device's native buffer at every angle.  Take the
        angle without the canvas and the shape transposes -- an 854x480 panel
        gets a 480x854 frame under an 854x480 header, and the firmware paints
        only the overlap (#262).

        ``portrait_content=False``: these callers scale a supplied image onto
        the device canvas rather than loading an authored portrait theme, so
        the content is native-shaped by construction.

        ``wire_angle`` ALONE, deliberately.  A ``post_rotate`` branch was
        drafted here on the reasoning that ``plan_orientation`` answers 90/270
        with post_rotate while leaving ``wire_angle`` at 0/180, so the latter
        would emit 320x240 under a 240x320 header.  MEASURED 2026-08-19: it
        does not.  On the 6 base-90 profiles (50/51/52/53/58 at 320x240, 64 at
        640x480) -- the only 12 (panel, angle) pairs where post_rotate is
        non-zero at all with ``portrait_content=False`` -- ``wire_angle``
        answers **270 at 90deg and 90 at 270deg**, not 0/180.  Both swap the
        axes, so the two paths emit the SAME shape on every live pair and the
        branch changed no dimension anywhere.

        What it did change is the rotation DIRECTION on those 12 pairs, by
        180deg, for ``build_image_frame`` and ``build_screencast_frame``.
        Whether the table's direction or post_rotate's is the one the glass
        wants is a real question, but it is a direction question on the
        base-90 family -- ``ENCODE_ROTATIONS`` territory, hardware-gated with
        #169/#203 -- and no shape gate can see it.  It does not belong in a
        shape fix, so it is not here.
        """
        angle = wire_angle(resolved, s.orientation, False)
        log.debug("_orient_for_wire %s: orientation=%d → wire %d°",
                  info.key, s.orientation, angle)
        if angle % 360:
            surface = self._r.rotate(surface, angle)
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
        # Reset the transition tracker too, so the next build_frame for
        # this key logs INFO when the cache state first appears
        # post-invalidation (instead of comparing against stale state).
        self._cache_state.pop(key, None)

    def invalidate_all(self) -> None:
        log.info("invalidate_all: scenes=%d", len(self._scenes))
        self._scenes.clear()
        self._cache_state.clear()

    def _log_cache_transition(self, key: str, bg_hit: bool,
                              ovl_hit: bool) -> None:
        """Log on the first call AND every cache state flip per device.

        DEBUG, not INFO: on animated / cloud-background content the state flips
        EVERY frame, so at INFO this floods the log and scrolls the once-per-
        connect handshake line (PM/SUB/resolution) out of the report's tail.
        The "frozen on frame N" diagnostic (a missing flip) is still here at
        ``-v``; per-tick HIT/MISS already logs at DEBUG in ``build_frame``.
        """
        new_state = (bg_hit, ovl_hit)
        prev_state = self._cache_state.get(key)
        if prev_state == new_state:
            return
        log.debug(
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
                is_user = (
                    theme.path is not None
                    and is_under(Path(theme.path), self._paths.user_content_dir())
                )
                if is_user:
                    # User upload — native resolution; honor the user's chosen
                    # fit_mode (scale/letterbox as selected).
                    fit_w, fit_h, off_x, off_y = _fit(
                        s.fit_mode, src_w, src_h, dst_w, dst_h,
                    )
                    log.debug(
                        "build_bg_mask %s: user background %dx%d → fit %s → "
                        "%dx%d at (%d, %d)",
                        info.key, src_w, src_h,
                        s.fit_mode.value if hasattr(s.fit_mode, "value")
                        else s.fit_mode,
                        fit_w, fit_h, off_x, off_y,
                    )
                    fitted = self._r.resize(source, fit_w, fit_h)
                    canvas = self._r.composite(
                        canvas, fitted, position=(off_x, off_y),
                    )
                else:
                    # Program/cloud content — the C# native-or-black width test,
                    # shared with Renderer.build_frame (increment 2c): native at
                    # (0,0) when it fits the canvas width, else solid black.
                    # bg_fit logs the native/black branch (incl. the drop warn).
                    canvas = self._r.bg_fit(
                        canvas,
                        RenderContent(source, None, background_is_user=False),
                    )
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
            # NB: no ``self._themes.mask_path(theme)`` here.  Arguments are
            # evaluated eagerly, so naming it in a DEBUG call did real
            # filesystem work on EVERY frame to build a string that is
            # usually discarded — and when mask_visible is False it did the
            # very lookup ``_resolve_mask_source`` had just decided to skip.
            # It adds nothing either: reaching this branch with a visible
            # mask means the theme mask resolved to None. (#264)
            log.debug(
                "build_bg_mask %s: no mask composited (visible=%s, "
                "override=%r)",
                info.key, s.mask_visible, s.mask_path,
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
            payload: bytes | None = playback.current
            log.debug(
                "resolve_background %s: video playback active "
                "(%d frames, cursor=%d, %d encoded bytes)",
                info.key, len(playback.frames), playback.cursor,
                len(payload) if payload else 0,
            )
            # Frames are held ENCODED and exactly one is decoded per tick —
            # the C#'s ByteToBitmap(imageArray[gifCount]) (FormCZTV.cs:2176).
            # Holding them raw cost 3.1 GB on a 897-frame 1600x720 video.
            return self._r.decode_image(payload) if payload else None

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
                    # A video background is owned by ``PlayVideo`` — the only
                    # decoder.  Reaching here means the override names a video
                    # with no playback loaded, so there is no frame to paint;
                    # decoding it HERE is what a render must never do (see the
                    # note on the theme-video branch below).
                    log.warning(
                        "resolve_background %s: override %s is a video with "
                        "no playback loaded — PlayVideo owns the decode, "
                        "skipping background this frame",
                        info.key, path.name,
                    )
                    return None
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
            # Rendering is a READ.  ``PlayVideo`` is the single decoder — it
            # owns the decode-size policy (oriented canvas, native for user
            # assets), and every path that wants a video playing dispatches it
            # (LoadTheme, SetBackground, LoadCloudTheme, RestoreLastTheme).
            #
            # This branch used to call ``load_video`` itself, which meant a
            # render could cost a full decode.  Two renders racing (the GUI
            # thread inside LoadTheme, and the metrics thread — EventBus
            # publishes on the caller's thread) both found no playback and both
            # decoded the same file, then PlayVideo decoded it a third time.
            # It also decoded at ``visual_size`` rather than PlayVideo's canvas
            # size, so which path won changed how a user upload got scaled.
            log.warning(
                "resolve_background %s: theme %r background %s is a video "
                "with no playback loaded — PlayVideo owns the decode, "
                "skipping background this frame",
                info.key, theme.name, path.name,
            )
            return None

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
        elements = resolve_overlay_elements(theme.config, s.user_overlay_elements)
        # The DEVICE'S overlay-enabled state is the single authority (the
        # user/GUI toggle ``DeviceSettings.overlay_enabled``, default True) —
        # NOT the theme's baked DC flag.  Otherwise a theme authored
        # ``overlay_enabled=False`` (e.g. many 854x480 themes) suppresses the
        # overlay forever: the device observes the metrics and the user wants
        # them, but they never render.  Any device on any OS observes the same
        # metrics and shows them when its own overlay is on.
        config_for_render = {
            **theme.config, "elements": elements,
            "overlay_enabled": s.overlay_enabled,
        }
        layout = overlay_source(s.user_overlay_elements)
        log.debug(
            "build_overlay %s: theme=%r layout=%s (%d element(s)) "
            "[theme=%d user=%s] overlay_enabled=%s",
            info.key, theme.name, layout, len(elements),
            len(theme.config.get("elements") or []),
            (len(s.user_overlay_elements)
             if s.user_overlay_elements is not None else None),
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
            for e in (s.user_overlay_elements or ())
        )
        # Temp unit participates in the key so toggling °C ↔ °F via
        # SetTempUnit busts the overlay cache and the next render
        # picks up the new format-string + value-conversion path.
        return (id(theme.config), visual_size, sensor_tuple, clock_tuple,
                user_sig, s.temp_unit)

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
        # The single wire-encode chokepoint every send path funnels through;
        # the preview path never calls it, so the GUI preview stays upright
        # (#137).  Delegates to the shared Renderer.encode_payload (increment
        # 2c): a fixed hardware-mount baseline (FW360 PM=6 → 180°) pre-rotates
        # the wire frame, then JPEG or RGB565 per the profile.
        return self._r.encode_payload(surface, profile)


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
