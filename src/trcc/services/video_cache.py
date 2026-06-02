"""VideoFrameCache — per-device store of pre-composited animation frames.

Decouples the background animation loop from the per-tick bg rebuild: a
multi-frame video draws a new bg every tick (the video cursor is in the
scene-cache key → always a MISS), so without this every tick re-ran the
full decode + fit + mask composite.  DisplayService builds one finished
bg+mask surface per cursor (reusing ``_build_bg_mask``) and hands the
list here; ``build_frame`` then pulls ``get_surface(cursor)`` instead of
recomposing.

This is a plain indexed surface store — the renderer-side compositing,
fit/scale, brightness, rotation, and encode all live in DisplayService
(``_build_bg_mask`` produces the surfaces; brightness/rotate/encode run
per tick on the composited result).  Keeping the cache to just "hold the
finished surfaces, return by cursor" is the whole job.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class VideoFrameCache:
    """A per-cursor store of finished bg+mask animation-frame surfaces."""

    __slots__ = ("_frames",)

    def __init__(self) -> None:
        self._frames: list[Any] = []

    # ── State ──────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True once :meth:`build` has stored at least one frame."""
        return bool(self._frames)

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    # ── Build (once per video load / theme / fit change) ───────────────

    def build(self, frames: list[Any]) -> None:
        """Store the finished per-cursor surfaces; an empty list invalidates.

        ``frames`` are renderer surfaces already at the device canvas size
        with bg + mask composited (DisplayService owns that via
        ``_build_bg_mask``).
        """
        self._frames = list(frames)
        log.info("VideoFrameCache.build: %d frame(s)", len(self._frames))

    # ── Per-tick access ────────────────────────────────────────────────

    def get_surface(self, index: int) -> Any | None:
        """The cached surface for ``index``, or None (out of range / unbuilt)."""
        if not (0 <= index < len(self._frames)):
            return None
        return self._frames[index]

    def invalidate(self) -> None:
        """Drop the stored frames — next build rebuilds from scratch."""
        log.debug("VideoFrameCache.invalidate")
        self._frames = []
