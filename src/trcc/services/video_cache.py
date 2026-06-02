"""VideoFrameCache — per-device cache of pre-composited animation frames.

Decouples the background animation loop (frame rate) from the metric
overlay (refresh rate).  The expensive per-frame work — raw→surface, mask
composite, brightness dim — is done once and cached, so a tick after the
first loop is a lookup + one overlay composite + encode, instead of the
full pipeline 15×/sec.

Two layers + one overlay slot (translated from legacy ``VideoFrameCache``
onto the current ``Renderer`` port — no copy):

  L2  ``_masked``    — each frame composited with the theme mask.  Built
                       once; immutable until rebuilt.
  L3  ``_adjusted``  — brightness-dimmed surfaces, filled LAZILY on first
                       access.  After one full playback loop every
                       ``get_surface`` is a list lookup.
  overlay            — the metric overlay surface, stored once per refresh
                       interval via :meth:`update_overlay` and composited
                       on top of every frame at tick time.

The caller (DisplayService) owns fit/scale + device rotation + encoding —
this cache only holds canvas-sized surfaces and the cheap per-frame
brightness layer, matching the legacy split (rotation/encode stay at the
device boundary so a rotation change needs no rebuild).
"""
from __future__ import annotations

import logging
from typing import Any

from ..core.models import RawFrame
from ..core.ports import Renderer

log = logging.getLogger(__name__)


class VideoFrameCache:
    """Cache of bg+mask+brightness frame surfaces + one metric overlay."""

    __slots__ = (
        "_active",
        "_adjusted",
        "_brightness",
        "_masked",
        "_overlay",
        "_overlay_key",
        "_r",
    )

    def __init__(self, renderer: Renderer) -> None:
        self._r = renderer
        # L2: bg-frame composited with the mask (immutable after build).
        self._masked: list[Any] = []
        # L3: brightness-dimmed surfaces, filled lazily on first access.
        self._adjusted: list[Any | None] = []
        self._brightness: int = 100
        # The metric overlay surface — rendered once per refresh interval.
        self._overlay: Any | None = None
        self._overlay_key: tuple[Any, ...] | None = None
        self._active: bool = False

    # ── State ──────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True once :meth:`build` has stored at least one frame."""
        return self._active and bool(self._masked)

    @property
    def frame_count(self) -> int:
        return len(self._masked)

    @property
    def overlay(self) -> Any | None:
        return self._overlay

    @property
    def has_overlay(self) -> bool:
        return self._overlay is not None

    # ── Build (once per video load / theme / fit change) ───────────────

    def build(
        self,
        frames: list[Any],
        mask: Any | None,
        mask_position: tuple[int, int],
        brightness: int,
    ) -> None:
        """Build L2 — each frame composited with the mask.  L3 fills lazily.

        ``frames`` may be :class:`RawFrame` (from a Playback) or renderer
        surfaces, and must already be at the device canvas size (the caller
        owns fit/scale).  An empty list invalidates the cache.
        """
        if not frames:
            self.invalidate()
            return

        surfaces = (
            [self._r.from_raw_rgb24(f) for f in frames]
            if isinstance(frames[0], RawFrame)
            else list(frames)
        )
        if mask is not None:
            self._masked = [
                self._r.composite(s, mask, position=mask_position)
                for s in surfaces
            ]
        else:
            self._masked = list(surfaces)

        self._brightness = brightness
        self._adjusted = [None] * len(self._masked)
        self._active = True
        log.info(
            "VideoFrameCache.build: %d frame(s), mask=%s, brightness=%d",
            len(self._masked), mask is not None, brightness,
        )

    def set_brightness(self, brightness: int) -> None:
        """Change brightness — L3 refills lazily on next access."""
        if brightness == self._brightness:
            return
        log.info("VideoFrameCache.set_brightness: %d → %d (L3 reset)",
                 self._brightness, brightness)
        self._brightness = brightness
        self._adjusted = [None] * len(self._masked)

    # ── Overlay (≤ once per refresh interval) ──────────────────────────

    def update_overlay(
        self, surface: Any | None, key: tuple[Any, ...] | None,
    ) -> bool:
        """Store the metric overlay surface.  Returns True if it changed.

        Keyed so an unchanged metrics tick is a no-op — the same surface
        is reused across every frame until the readings actually change.
        """
        if key == self._overlay_key:
            return False
        self._overlay = surface
        self._overlay_key = key
        log.info("VideoFrameCache.update_overlay: changed (key=%s)", key)
        return True

    # ── Per-tick access ────────────────────────────────────────────────

    def get_surface(self, index: int) -> Any | None:
        """Brightness-adjusted bg+mask surface for ``index``.

        O(1) after the first loop: the dim is applied once per frame and
        cached.  Returns None for an out-of-range index or unbuilt cache.
        Passes through (no dim) at brightness ≥ 100 — the common case.
        """
        if not (0 <= index < len(self._masked)):
            return None
        if self._adjusted[index] is None:
            base = self._masked[index]
            self._adjusted[index] = (
                base if self._brightness >= 100
                else self._r.apply_brightness(base, self._brightness)
            )
        return self._adjusted[index]

    def composited(self, index: int) -> Any | None:
        """Final pre-encode surface: the cached bg+mask+brightness frame with
        the stored overlay composited on top.  ``composite`` returns a fresh
        surface, so the cached frame is never mutated.  None if unbuilt.
        """
        base = self.get_surface(index)
        if base is None:
            return None
        if self._overlay is None:
            return base
        return self._r.composite(base, self._overlay, position=(0, 0))

    def invalidate(self) -> None:
        """Drop every layer — next build rebuilds from scratch."""
        log.debug("VideoFrameCache.invalidate")
        self._masked = []
        self._adjusted = []
        self._overlay = None
        self._overlay_key = None
        self._active = False
