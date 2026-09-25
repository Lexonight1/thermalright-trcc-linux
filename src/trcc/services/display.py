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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.geometry import content_is_portrait, fit_rect_for_mode, plan_orientation
from ..core.logs import per_frame
from ..core.models import (
    MEDIA,
    RENDER_CACHE_MAX_BYTES,
    SPLIT_OVERLAY_MAP,
    DeviceSettings,
    FitMode,
    MediaKind,
    ProductInfo,
    RawFrame,
    RenderContent,
    Theme,
)
from ..core.ports import ContentStore, Paths, Renderer
from ..core.protocol import (
    DEFAULT_PROFILE,
    DeviceProfile,
    get_profile,
    wire_angle,
)
from ._clock import compute_clock
from .background import Background, BackgroundSlot
from .bg_cache import BgMaskCache
from .media import MediaService
from .overlay import OverlayService, overlay_source, resolve_overlay_elements
from .settings import Settings

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)




# Devices whose canvas is widescreen split-eligible.  Currently just
# Levita (1600x720); listed as a set so future widescreen panels are
# a one-line addition.
_WIDESCREEN_SPLIT_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset({
    (1600, 720),
    (720, 1600),    # rotated portrait of the same panel
})


#: What :meth:`DisplayService._resolve_background` returns when nothing
#: resolved.  A frozen value rather than ``None`` so every caller asks one
#: question -- ``content.background is None`` -- instead of two.
_NO_BACKGROUND = RenderContent(None, None)


def _is_widescreen_split(visual_size: tuple[int, int]) -> bool:
    """True when ``visual_size`` is a widescreen panel that supports
    the Dynamic Island split overlay.  Gates ``_composite_split_overlay``
    so non-widescreen devices skip the load+composite entirely.
    """
    log.debug("_is_widescreen_split: visual_size=%s", visual_size)
    return visual_size in _WIDESCREEN_SPLIT_RESOLUTIONS


# =========================================================================
# SceneCache — per-device layered cache
# =========================================================================


@dataclass
class SceneCache:
    """The overlay surface + the wire bytes, and the keys that govern them.

    ``frame_key`` and ``frame_bytes`` cache the final wire-encoded
    frame so a tick where nothing changed (cache HIT on bg+overlay
    AND identical brightness/orientation/split/rotate) can return the
    last frame directly — skipping composite + brightness + rotate +
    encode entirely.

    The background+mask layer is NOT here.  It used to be, as a single
    surface plus its key — which is a cache of one, and a video theme
    misses a cache of one on every tick because its cursor is part of
    the key.  It lives in ``BgMaskCache`` instead, which holds a bounded
    cycle of them.  One layer, one owner: a second copy here would be a
    second answer to "what background is current".
    """

    # overlay layer
    overlay_surface: Any
    overlay_key: tuple[Any, ...]       # (config_id, visual_size, sensor_tuple)

    # Final wire-bytes cache — keyed on the full pipeline inputs so a
    # tick with no changes returns identical bytes without re-encoding.
    frame_key: tuple[Any, ...] | None = None
    frame_bytes: bytes | None = field(default=None, repr=False)

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
        backgrounds: BackgroundSlot,
        paths: Paths,
    ) -> None:
        log.debug("__init__: renderer=%s themes=%s", renderer, themes)
        self._r = renderer
        self._themes = themes
        self._overlay = overlay
        self._settings = settings
        self._media = media
        # What this device is currently showing, behind mask and metrics.
        # Owned by App so it survives a renderer swap -- see BackgroundSlot.
        self._backgrounds = backgrounds
        # Content origin (program/cloud vs user upload) drives the theme-bg
        # fill rule: program content is authored-for-canvas → the C# native-
        # or-black width test (no letterbox); user content keeps fit_mode
        # scaling.  Same axis as the PlayVideo decode-size gate.
        self._paths = paths
        self._scenes: dict[str, SceneCache] = {}
        # Per-device byte-capped cache of composed background+mask
        # surfaces.  A video theme's bg layer is a repeating cycle, so it
        # is worth keeping — but only within a budget, because the same
        # cycle costs 59 MB on a 320x320 panel and 475 MB on a 1600x720
        # one.  Filled lazily (one composite per asking tick), so nothing
        # is paid up front the way the unbounded pre-composed cache did.
        self._bg_caches: dict[str, BgMaskCache] = {}
        # Cache of loaded split-overlay surfaces keyed by
        # (style, rotation, mirrored).  Loaded lazily on first
        # widescreen render so non-Levita devices pay nothing.
        self._split_cache: dict[str, Any] = {}
        # Per-device scene-cache hit/miss state — used to log INFO on
        # TRANSITION only (matches Phase-0's ``_log_tick_skip``
        # shape).  Per-tick HIT/MISS stays at DEBUG so 15 fps doesn't
        # flood the log; transitions surface "froze on first frame"
        # regressions in one grep.
        self._cache_state: dict[str, tuple[bool, bool]] = {}
        #: Devices whose profile came from a FALLBACK, so ``_resolve_profile``
        #: announces each one once instead of once per frame.
        self._profile_fallbacks: set[str] = set()

    # ── Top-level pipeline ────────────────────────────────────────────

    @staticmethod
    def _content_is_portrait(
        theme: Theme, profile: DeviceProfile, s: DeviceSettings,
    ) -> bool:
        """Portrait decision for the render path — see
        :func:`trcc.core.geometry.content_is_portrait` (the shared source, also
        used by ``SaveTheme`` so save + reload agree on orientation)."""
        portrait = content_is_portrait(theme, profile, s.mask_path,
                                       s.mask_visible)
        frame_log.debug("_content_is_portrait: %s (theme=%s mask=%s visible=%s)",
                        portrait, theme.name, s.mask_path, s.mask_visible)
        return portrait

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
        frame_log.debug("_compose_geometry: %dx%d rotate=%s widescreen=%s @ %d° "
                        "-> canvas=%s portrait=%s post_rotate=%d",
                        profile.width, profile.height, profile.rotate,
                        profile.widescreen, orientation, plan.canvas,
                        plan.is_portrait_content, plan.post_rotate)
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
        frame_log.debug(
            "build_frame %s: theme=%r visual=%dx%d orientation=%d brightness=%d",
            info.key, theme.name, visual_size[0], visual_size[1],
            s.orientation, s.brightness,
        )

        clock = compute_clock(
            time_format=s.time_format,
            date_format=s.date_format,
            language=self._settings.app.language,
        )
        frame_log.debug(
            "build_frame %s: clock=%s (time_format=%s date_format=%s lang=%s)",
            info.key, sorted(clock.keys()),
            s.time_format, s.date_format,
            self._settings.app.language,
        )

        scene = self._scenes.get(info.key)
        bg_key = self._bg_mask_key(info, theme, visual_size)
        overlay_key = self._overlay_key(info, theme, visual_size, sensors, clock)

        bg_hit = bg_key in self._bg_cache(info.key)
        ovl_hit = scene is not None and scene.overlay_key == overlay_key
        frame_log.debug(
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
            frame_log.debug("build_frame %s: full-pipeline cache HIT (%d bytes)",
                            info.key, len(scene.frame_bytes))
            return scene.frame_bytes

        bg_surface, overlay_surface = self._resolve_bg_overlay(
            info, theme, sensors, visual_size, clock,
            scene, bg_key, overlay_key,
        )

        # Compose: bg+mask below, overlay on top
        surface = self._r.composite(bg_surface, overlay_surface, position=(0, 0))

        # Split-mode overlay (Dynamic Island) — 1600x720 widescreen only.
        # Picks an asset by (split_mode, rotation, SUB byte), as the C# does.
        # No-op when split_mode==0 or the LCD isn't widescreen.
        if s.split_mode and _is_widescreen_split(visual_size):
            surface = self._composite_split_overlay(
                info, s.split_mode, s.orientation,
                profile.sub if profile is not None else 0, surface,
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
            frame_log.debug("build_frame %s: wire rotate %d°", info.key, angle)
            surface = self._r.rotate(composite, angle)
        else:
            surface = composite

        encoded = self._encode_for_wire(surface, resolved_profile)
        self._scenes[info.key] = SceneCache(
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
        (GUI) so both go through the same caches.

        The two layers cache differently because they repeat
        differently.  The overlay carries live sensor readings and the
        clock, so its key moves whenever the numbers do and there is
        nothing to reuse — one entry is the right number.  The
        background of a video theme cycles: N frames, then the same N
        again, forever.  ``BgMaskCache`` keeps as much of that cycle as
        its byte budget allows, so a tick that comes back around to a
        frame it has already composed pays a dict lookup instead of a
        JPEG decode, a fit and a mask composite.

        A miss composes fresh, which is what the C# does unconditionally
        — ``GenerateImage`` (UCScreenImage.cs:634) allocates a bitmap and
        draws background → mask → text for every frame it sends.  So the
        budget being exhausted is not a failure mode; it is the C#'s own
        behaviour, and it is what a panel too large to fit its animation
        in the budget falls back to.

        What is NOT done here is pre-composing the whole video up front,
        which is how this cache existed before ``da4be2e9`` deleted it:
        unbounded, and paid in one 4.38s freeze on apply for 3,964 MB
        retained at 1600x720 (#264, #256).  Entries appear one per asking
        tick.
        """
        bg_cache = self._bg_cache(info.key)
        bg_surface = bg_cache.get(bg_key)
        bg_hit = bg_surface is not None
        if bg_surface is None:
            bg_surface = self._build_bg_mask(info, theme, visual_size)
            nbytes = self._r.surface_nbytes(bg_surface)
            # The cycle this device will come back around through: one
            # composed surface per video frame, or just this one for a
            # static theme.  The cache needs it to tell a workload it can
            # serve from one it would only thrash on.
            playback = self._media.playback(info.key)
            frames = len(playback.frames) if playback is not None else 1
            bg_cache.put(bg_key, bg_surface, nbytes,
                         working_set_bytes=frames * nbytes)

        overlay_hit = scene is not None and scene.overlay_key == overlay_key
        if overlay_hit:
            overlay_surface = scene.overlay_surface   # type: ignore[union-attr]
        else:
            overlay_surface = self._build_overlay(
                info, theme, sensors, visual_size, clock,
            )
        # The two-layer decision the whole cache design exists for: a video
        # theme's bg cycles and its overlay moves with the sensors, so the pair
        # is what says whether a tick paid a dict lookup or a JPEG decode plus
        # a fit plus a mask composite.
        frame_log.debug("_resolve_bg_overlay %s: bg=%s overlay=%s (%dx%d)",
                        info.key, "HIT" if bg_hit else "MISS",
                        "HIT" if overlay_hit else "REBUILD", *visual_size)
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
        theme: Theme | None = None,
        sensors: dict[str, float] | None = None,
        spectrum: Sequence[float] = (),
        profile: DeviceProfile | None = None,
    ) -> bytes:
        """Encode one captured screen region, with mask + metrics on top.

        **The capture is the BACKGROUND, not a replacement for the frame.**
        That is the C# model exactly: ``CopyFromScreen`` writes the captured
        region into ``bitmapBGK`` (FormCZTV.cs:3550) — the same slot a GIF
        (:3007) and the video player (:3057) write — and ``GenerateImage``
        then draws background -> ``bitmapMB`` (the mask) -> a ``DrawString``
        per metric element, every frame.  It holds in the threaded path too:
        ``StartPipeline``'s first stage assigns the same ``bitmapBGK`` and
        calls the same compositor.

        Until 2026-09-14 this method skipped both layers ("once we layer them
        in", it said), so a screencast showed the bare desktop while
        ``RenderAndSend`` — which nothing gated — overwrote it with a full
        theme frame on every ``SensorsUpdated``.  At ~7 fps capture against a
        2 s sensor tick the panel showed ~13 bare frames, then one frame of
        mask+metrics, forever: the reported "blinking metrics mask".

        ``theme`` is optional because a caller mid-teardown may have none; the
        capture is then sent bare, which is the old behaviour and still beats
        dropping the frame.

        **Geometry is deliberately untouched.**  The canvas stays
        ``plan_orientation(..., False).canvas`` and rotation stays
        ``_orient_for_wire``, so the wire bytes are unchanged on every panel
        at every orientation.  The oracle composes on the ORIENTED canvas
        instead (``GIFSize`` transposes at 90/270 for non-square panels,
        FormCZTV.cs:3430-3512), which is a real divergence on 6 FBLs — but it
        is coupled to the capture aspect, which the region picker does not yet
        transpose, and changing both belongs in its own verified step.
        """
        log.debug("build_screencast_frame: key=%s theme=%s",
                  info.key, theme.name if theme else None)
        resolved = self._resolve_profile(info, profile)
        s = self._settings.for_device(info.key)
        target_w, target_h = plan_orientation(
            resolved, s.orientation, False).canvas

        surface = self._r.from_raw_rgb24(frame)
        if (
            self._r.surface_size(surface) != (target_w, target_h)
        ):
            surface = self._r.resize(surface, target_w, target_h)

        if theme is not None:
            surface = self._composite_mask(info, s, theme, surface)
            clock = compute_clock(
                time_format=s.time_format,
                date_format=s.date_format,
                language=self._settings.app.language,
            )
            overlay = self._build_overlay(
                info, theme, sensors or {}, (target_w, target_h), clock)
            surface = self._r.composite(surface, overlay, position=(0, 0))

        # Audio spectrum LAST, over everything, because it is a live meter
        # rather than part of the picture — the same order the gui's tick drew
        # it in.  Empty when the session asked for no audio or ``sounddevice``
        # is absent, and then this is a no-op.
        if len(spectrum):
            frame_log.debug("build_screencast_frame: %d spectrum bands",
                            len(spectrum))
            surface = self._r.draw_spectrum(surface, spectrum)

        # PREVIEW = the composite, captured BEFORE the wire rotation, which is
        # the same point and the same rule ``build_frame`` uses: the display
        # angle is a MOUNT CORRECTION, so an upright preview is what lets an
        # owner turn the dial until the glass reads right, and it is what keeps
        # the surface editable (overlay drag maps widget->LCD by scale alone,
        # with no angle term).
        #
        # It has to be published or the preview shows a DIFFERENT PICTURE from
        # the panel: the gui painted its preview straight from the raw grab
        # (``lcd_handler.on_screencast_frame``), so once the wire frame gained
        # the mask and the metrics, the LCD had them and the preview did not.
        self._remember_preview(info.key, surface)

        surface = self._apply_post_processing(surface, s, resolved)
        surface = self._orient_for_wire(surface, s, resolved, info)
        return self._encode_for_wire(surface, resolved)

    def _remember_preview(self, key: str, surface: Any) -> None:
        """Park *surface* where :meth:`rendered_surface` will find it.

        Updates the existing scene rather than replacing it: the scene also
        holds ``build_frame``'s overlay and wire-byte caches, and dropping
        those would make the next theme render recompose from nothing.  When
        there is no scene yet, the entry carries ``frame_key=None``, which the
        full-pipeline cache treats as a miss — a preview must never be able to
        satisfy a request for wire bytes.
        """
        frame_log.debug("_remember_preview: key=%s", key)
        scene = self._scenes.get(key)
        self._scenes[key] = (
            replace(scene, preview_surface=surface) if scene is not None
            else SceneCache(overlay_surface=None, overlay_key=(),
                            preview_surface=surface)
        )

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
        frame_log.debug("rendered_surface: key=%s available=%s",
                  key, surface is not None)
        return surface

    def _bg_cache(self, key: str) -> BgMaskCache:
        """The background+mask cache for *key*, created on first ask.

        Per device rather than shared, so one large panel's animation
        cannot evict a small one's — the budget in ``RENDER_CACHE_MAX_BYTES``
        is what a single device may retain.
        """
        cache = self._bg_caches.get(key)
        if cache is None:
            log.debug("_bg_cache: opening a %d-byte budget for %s",
                     RENDER_CACHE_MAX_BYTES, key)
            cache = BgMaskCache(RENDER_CACHE_MAX_BYTES)
            self._bg_caches[key] = cache
        return cache

    def invalidate(self, key: str) -> None:
        """Drop the scene cache for *key* (called on disconnect / theme change)."""
        log.debug("invalidate: key=%s", key)
        self._scenes.pop(key, None)
        # The background cache goes too.  Its keys already carry theme,
        # mask and mode, so stale entries would simply never be asked for
        # again — but "never asked for" still occupies the budget until it
        # ages out, and a device that just changed theme should not be
        # spending its allowance on the previous one.
        self._bg_caches.pop(key, None)
        # Reset the transition tracker too, so the next build_frame for
        # this key logs INFO when the cache state first appears
        # post-invalidation (instead of comparing against stale state).
        self._cache_state.pop(key, None)

    def invalidate_all(self) -> None:
        log.info("invalidate_all: scenes=%d bg_caches=%d",
                 len(self._scenes), len(self._bg_caches))
        self._scenes.clear()
        self._bg_caches.clear()
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
        frame_log.debug(
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
            # The slot answers first.  On a match nothing is resolved and no
            # file is re-opened; on a miss -- a new theme, a new override, the
            # next video frame -- the theme source resolves itself and pushes,
            # which is what makes ``_resolve_background`` a producer rather
            # than something the renderer asks every tick.
            token = self._background_token(info, theme)
            held = self._backgrounds.current(info.key, token)
            if held is None:
                resolved = self._resolve_background(info, theme, visual_size)
                held = Background(resolved.background,
                                  resolved.background_is_user)
                self._backgrounds.push(info.key, token, held)
            content = RenderContent(held.surface, None,
                                    background_is_user=held.is_user_content)
            if content.background is not None:
                src_w, src_h = self._r.surface_size(content.background)
                dst_w, dst_h = visual_size
                if content.background_is_user:
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
                    fitted = self._r.resize(content.background, fit_w, fit_h)
                    canvas = self._r.composite(
                        canvas, fitted, position=(off_x, off_y),
                    )
                else:
                    # Program/cloud content — the C# native-or-black width test,
                    # shared with Renderer.build_frame (increment 2c): native at
                    # (0,0) when it fits the canvas width, else solid black.
                    # bg_fit logs the native/black branch (incl. the drop warn).
                    canvas = self._r.bg_fit(canvas, content)
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
        return self._composite_mask(info, s, theme, canvas)

    def _composite_mask(
        self,
        info: ProductInfo,
        s: DeviceSettings,
        theme: Theme,
        canvas: Any,
    ) -> Any:
        """Lay the mask over *canvas*, or return it untouched.

        Extracted so the SCREENCAST path composites the identical layer: the
        C# has exactly ONE mask draw per compose half and both are gated the
        same way (``UCScreenImage.cs:1118`` and ``:1511``,
        ``if (isDrawMbImage && bitmapMB != null)``), so a screencast that
        merely skipped the mask would not be a different mode — it would be
        a missing layer.

        A user-picked mask (``DeviceSettings.mask_path``) takes precedence
        over the theme's bundled one; ``mask_visible=False`` skips the layer
        entirely.  Position defaults to (0, 0).
        """
        mask_source = self._resolve_mask_source(s, theme)
        if mask_source is not None:
            mask = self._r.open_image(mask_source)
            mw, mh = self._r.surface_size(mask)
            position = s.mask_position or (0, 0)
            frame_log.debug(
                "composite_mask %s: mask %s (%dx%d) at top-left (%d, %d) "
                "[visible=%s]",
                info.key, mask_source, mw, mh, position[0], position[1],
                s.mask_visible,
            )
            return self._r.composite(canvas, mask, position=position)
        # NB: no ``self._themes.mask_path(theme)`` here.  Arguments are
        # evaluated eagerly, so naming it in a DEBUG call did real
        # filesystem work on EVERY frame to build a string that is
        # usually discarded — and when mask_visible is False it did the
        # very lookup ``_resolve_mask_source`` had just decided to skip.
        # It adds nothing either: reaching this branch with a visible
        # mask means the theme mask resolved to None. (#264)
        frame_log.debug(
            "composite_mask %s: no mask composited (visible=%s, override=%r)",
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
        frame_log.debug(
            "_resolve_mask_source: mask_visible=%s mask_path=%s",
            device_settings.mask_visible, device_settings.mask_path,
        )
        if not device_settings.mask_visible:
            frame_log.debug("_resolve_mask_source: mask_visible=False → None")
            return None
        if device_settings.mask_path is not None:
            override = Path(device_settings.mask_path)
            if override.exists():
                frame_log.debug("_resolve_mask_source: using override %s",
                                override)
                return override
            log.warning(
                "resolve_mask_source: override %s does not exist — "
                "falling back to theme bundled mask",
                override,
            )
        theme_mask = self._themes.mask_path(theme)
        frame_log.debug(
            "resolve_mask_source: using theme bundled mask %s",
            theme_mask,
        )
        return theme_mask

    def _resolve_background(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> RenderContent:
        """Return the current background frame AND where it came from.

        Playback (set by ``PlayVideo`` or by a prior video-theme render)
        takes precedence — lets users play arbitrary videos without
        replacing the active theme. When no playback exists, fall back
        to the theme's bundled background image or video.

        **The origin rides with the surface.**  Every branch below reaches a
        DIFFERENT tree — a playback decoded at native, a ``background_path``
        override, a reference theme's library asset resolved user-root-first,
        the theme's own ``00.png`` — and only the branch that resolved it
        knows which.  The caller used to ask the active THEME's directory
        instead, which answers for none of the first three: a user upload
        under a program theme took the canvas-sized native-or-black rule and
        the panel went black, while the same file under a user theme
        rendered.  ``Paths.is_user_content`` decides; nobody re-derives it.
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
            frame_log.debug(
                "resolve_background %s: video playback active "
                "(%d frames, cursor=%d, %d encoded bytes)",
                info.key, len(playback.frames), playback.cursor,
                len(payload) if payload else 0,
            )
            # Frames are held ENCODED and exactly one is decoded per tick —
            # the C#'s ByteToBitmap(imageArray[gifCount]) (FormCZTV.cs:2176).
            # Holding them raw cost 3.1 GB on a 897-frame 1600x720 video.
            # The playback carries its own origin: ``PlayVideo`` decided it
            # once when it picked a decode size, and that answer travels
            # rather than being guessed again from the active theme.
            return RenderContent(
                self._r.decode_image(payload) if payload else None, None,
                background_is_user=playback.is_user_content,
            )

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
                if MEDIA.kind_of(ext) is MediaKind.ANIMATED:
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
                    return _NO_BACKGROUND
                if MEDIA.kind_of(ext) is MediaKind.IMAGE:
                    return RenderContent(
                        self._r.open_image(path), None,
                        background_is_user=self._paths.is_user_content(path),
                    )
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
            return _NO_BACKGROUND
        ext = path.suffix.lower()
        log.debug("resolve_background %s: theme %r → %s",
                  info.key, theme.name, path)

        if MEDIA.kind_of(ext) is MediaKind.ANIMATED:
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
            return _NO_BACKGROUND

        if MEDIA.kind_of(ext) is MediaKind.IMAGE:
            # A reference theme's ``background`` key resolves user-root FIRST
            # (``FileContentStore._resolve_asset_ref``), so even the theme's
            # own background can live outside its directory, in either tree.
            return RenderContent(
                self._r.open_image(path), None,
                background_is_user=self._paths.is_user_content(path),
            )

        log.warning(
            "resolve_background %s: unrecognised background extension %r "
            "at %s — skipping",
            info.key, ext, path,
        )
        return _NO_BACKGROUND

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

    def _background_token(
        self, info: ProductInfo, theme: Theme,
    ) -> tuple[Any, ...]:
        """WHICH background this device should be showing, and which frame.

        The one derivation of background IDENTITY.  It answers the same
        question ``_resolve_background`` answers with pixels, in the same
        precedence -- playback, then the ``background_path`` override, then
        the theme -- and both the slot and the bg+mask cache key are built
        from it, so identity is stated once.

        It used to be stated twice.  ``_bg_mask_key`` carried its own copy of
        the precedence with a comment saying it "mirrors
        ``_resolve_background``", which is a rule kept by hand across two
        functions: get them out of step and the key stops moving while the
        picture does, every tick HITs, and the panel freezes on one frame
        while the cursor runs on underneath.

        What is deliberately NOT here: the canvas size and the mask.  Those
        are the CONSUMER's context -- they change how the background is
        drawn, not which background it is -- so they stay with the cache key
        and the slot holds one surface per source rather than one per canvas.
        """
        s = self._settings.for_device(info.key)
        # A live playback IS the background, whatever the theme bundles --
        # ``PlayVideo`` loads it and the cursor names the frame.
        pb = self._media.playback(info.key)
        if pb is not None and pb.frames:
            source: tuple[Any, ...] = ("playback", s.background_path, pb.cursor)
        elif s.background_path:
            # A cloud / user override beats the theme's own background.
            source = ("override", s.background_path)
        else:
            path = self._themes.background_path(theme)
            animated = (path is not None
                        and MEDIA.kind_of(path) is MediaKind.ANIMATED)
            # A video-backed theme with no playback loaded should not happen
            # (LoadTheme dispatches PlayVideo), but it must not share a token
            # with a static theme if it does.
            source = ("theme", str(theme.path), str(path), animated)
        # ``background_mode`` decides whether the source is painted at all:
        # 'color' paints a fill instead and 'transparent' paints nothing, so
        # they are different backgrounds, not different renderings of one.
        token = (*source, s.background_mode, s.overlay_background)
        frame_log.debug("_background_token: key=%s → %s", info.key, token)
        return token

    def _bg_mask_key(
        self,
        info: ProductInfo,
        theme: Theme,
        visual_size: tuple[int, int],
    ) -> tuple[Any, ...]:
        # How the composite is DRAWN, as against WHICH background it is: the
        # mask layer, and the rule that fits the background to this canvas.
        # ``fit_mode`` belongs here and not in the token -- it has no mask use
        # at all (its only reader is the user-content branch of
        # ``_build_bg_mask``), and it changes how the picture is drawn rather
        # than which picture it is, which is exactly what lets the slot be
        # reused across a fit change.  It sat in a tuple called ``mask_sig``
        # until the key was split into identity + context, where the name
        # stopped being merely odd and started being wrong.
        #
        # Mask state is here so the layer rebuilds when ApplyMask /
        # SetMaskPosition / SetMaskVisible run.  Those Commands already
        # invalidate explicitly; keying on it defends against any path that
        # mutates Settings without going through a Command.
        s = self._settings.for_device(info.key)
        draw_sig = (s.mask_path, s.mask_position, s.mask_visible, s.fit_mode)
        token = self._background_token(info, theme)
        frame_log.debug("_bg_mask_key: token=%s size=%s draw=%s",
                        token, visual_size, draw_sig)
        return (token, visual_size, draw_sig)

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
        frame_log.debug("_overlay_key: %d sensor(s) %d clock field(s) "
                        "%d user element(s) unit=%s size=%s",
                        len(sensor_tuple), len(clock_tuple), len(user_sig),
                        s.temp_unit, visual_size)
        return (id(theme.config), visual_size, sensor_tuple, clock_tuple,
                user_sig, s.temp_unit)

    # ── Split-mode overlay (Dynamic Island / Levita widescreen) ───────

    def _composite_split_overlay(
        self,
        info: ProductInfo,
        split_mode: int,
        rotation: int,
        sub: int,
        surface: Any,
    ) -> Any:
        """Composite the Dynamic Island PNG over ``surface`` (in place).

        Picks the asset by ``(split_mode, rotation)`` from
        ``SPLIT_OVERLAY_MAP`` — with the C#'s one exception: style 2 on a
        SUB 3 panel takes the asset 180° round (``UCScreenImage.cs``
        ``myLddVal == 2 && myLddValSub == 3``; ``myLddValSub = pmSub``,
        ``FormCZTV.cs:893``).  That panel's wire angle is 180° off its
        siblings', and the C# compensates by choosing the island, never by
        flipping it.  This tree used to MIRROR the asset instead, on a cutout
        position we invented, through a ``QImage.mirrored`` keyword PySide6
        rejects — so every split-mode frame on that panel raised and the
        screen froze (#149).  Cached per asset, so each costs one load.
        """
        if split_mode == 2 and sub == 3:
            rotation = (rotation + 180) % 360
        asset_name = SPLIT_OVERLAY_MAP.get((split_mode, rotation))
        if asset_name is None:
            log.warning(
                "split overlay %s: no asset for (style=%d, rotation=%d)",
                info.key, split_mode, rotation,
            )
            return surface
        overlay = self._split_cache.get(asset_name)
        if overlay is None:
            overlay = self._load_split_asset(asset_name)
            if overlay is None:
                return surface
            self._split_cache[asset_name] = overlay
            log.info("split overlay %s: cached %s (style=%d sub=%d)",
                     info.key, asset_name, split_mode, sub)
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

    def _resolve_profile(
        self, info: ProductInfo, override: DeviceProfile | None,
    ) -> DeviceProfile:
        """Pick the profile to drive frame building.

        Preference: caller-supplied (from a live handshake) → registry
        FBL lookup → synthesized fallback matching the pre-profile
        behavior (native_resolution, RGB565, no rotation).
        """
        if override is not None:
            frame_log.debug("_resolve_profile: %s -> handshake profile %dx%d",
                            info.key, override.width, override.height)
            return override
        # Both fallbacks sit on the frame path, so they announce themselves ONCE
        # per device — the shape ``_log_cache_transition`` uses.  Per frame they
        # would fill the 1 MB rotating file and scroll the handshake line out of
        # the tail of every ``trcc report``, which is the one artifact we read
        # for hardware we do not own.  Once each, they are diagnostic gold: a
        # panel rendering off GUESSED geometry says so in the report.
        first = info.key not in self._profile_fallbacks
        self._profile_fallbacks.add(info.key)
        if info.fbl is not None:
            profile = get_profile(info.fbl)
            if first:
                log.info("_resolve_profile: %s has no handshake profile — "
                         "falling back to registry FBL %s (%dx%d)",
                         info.key, info.fbl, profile.width, profile.height)
            return profile
        w, h = info.native_resolution
        if (w, h) == (0, 0):
            # The catalog declares NOTHING for this row, on purpose: one USB
            # id covers several panels and only the handshake separates them
            # (0416:5302, #300).  Honest about the resolution, but a 0x0
            # surface is not a guess — it is a broken frame, and every
            # consumer downstream divides by it.  Fall through to the ONE
            # answer the catalog gives for "unknown", the same one an
            # unrecognised FBL already gets.
            if first:
                log.warning(
                    "_resolve_profile: %s is not connected and its catalog row "
                    "declares no resolution (this id covers several panels) — "
                    "assuming %dx%d.  Connect the device for its real geometry.",
                    info.key, DEFAULT_PROFILE.width, DEFAULT_PROFILE.height,
                )
            return DEFAULT_PROFILE
        if first:
            log.warning("_resolve_profile: %s has neither a handshake profile "
                        "nor an FBL — synthesizing %dx%d RGB565, no rotation; "
                        "wire geometry is a GUESS for this panel",
                        info.key, w, h)
        return DeviceProfile(width=w, height=h, jpeg=False, rotate=False)

    def _encode_for_wire(self, surface: Any, profile: DeviceProfile) -> bytes:
        # The single wire-encode chokepoint every send path funnels through;
        # the preview path never calls it, so the GUI preview stays upright
        # (#137).  Delegates to the shared Renderer.encode_payload (increment
        # 2c): a fixed hardware-mount baseline (FW360 PM=6 → 180°) pre-rotates
        # the wire frame, then JPEG or RGB565 per the profile.
        frame_log.debug("_encode_for_wire: %dx%d jpeg=%s baseline=%d°",
                        profile.width, profile.height, profile.jpeg,
                        profile.encode_baseline)
        return self._r.encode_payload(surface, profile)


# =========================================================================
# Pure-Python fit algorithm
# =========================================================================


def _fit(
    mode: FitMode,
    src_w: int, src_h: int,
    dst_w: int, dst_h: int,
) -> tuple[int, int, int, int]:
    """(fit_w, fit_h, x_offset, y_offset).

    A thin adapter over :func:`~trcc.core.geometry.fit_rect_for_mode`, which
    the ``Theme.zt`` exporter uses too.  The arithmetic lived here AND in the
    exporter, so the trimmer's W/H buttons could mean one thing on screen and
    another on the panel; one implementation makes that unrepresentable.
    Parity with the previous inline version was measured over 1740 cases.
    """
    log.debug("_fit: mode=%s src_w=%s", mode, src_w)
    rect = fit_rect_for_mode((src_w, src_h), (dst_w, dst_h), mode)
    return rect.width, rect.height, rect.x, rect.y


# Re-exported for unit tests
fit = _fit
