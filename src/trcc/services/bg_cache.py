"""BgMaskCache — the composed background+mask layer, bounded by bytes.

A video theme puts its playback cursor in the bg cache key, so a single-entry
cache MISSES on every tick and the whole background is composed again: decode
one JPEG frame, fit it to the canvas, composite the mask.  A cycle of frames
that repeats forever is exactly what a cache is for -- the reason there isn't
one is that the previous attempt (``VideoFrameCache``, deleted in da4be2e9)
pre-composed every frame of the video up front and kept all of them, with no
bound.  On a 1600x720/897-frame video that froze the UI for 4.38s and retained
3,964 MB (#264, #256).

This is that cache with the two defects removed:

* **Bounded, in bytes.**  ``RENDER_CACHE_MAX_BYTES`` governs the total, and a
  surface reports its own size through the renderer port -- because the same
  frame count costs 59 MB on one panel and 475 MB on another, so a frame limit
  protects the wrong device.  Past the cap the least-recently-used entries go,
  which degrades a large panel to composing every tick: today's behaviour,
  exactly where the bug was reported.
* **Filled lazily.**  Entries appear as ticks ask for them, one composite at a
  time.  Nothing is pre-computed, so there is no freeze on apply -- the cost
  the old cache paid all at once is the cost this one never pays.

Keying is the caller's business: ``DisplayService`` passes the same
``_bg_mask_key`` that governed the single-entry cache, which already carries
the theme, canvas size, cursor, mask signature and background mode.  So this
holds no invalidation rules of its own -- a changed mask or theme simply asks
under a different key, and the entries it no longer asks for age out.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)

CacheKey = tuple[Any, ...]


class BgMaskCache:
    """A byte-capped LRU of composed background+mask surfaces."""

    __slots__ = ("_entries", "_max_bytes", "_nbytes")

    def __init__(self, max_bytes: int) -> None:
        log.debug("BgMaskCache: max_bytes=%d", max_bytes)
        self._entries: OrderedDict[CacheKey, tuple[Any, int]] = OrderedDict()
        self._max_bytes = max_bytes
        self._nbytes = 0

    # ── State ─────────────────────────────────────────────────────────

    @property
    def nbytes(self) -> int:
        """Total pixel bytes currently retained."""
        log.debug("BgMaskCache.nbytes: %d of %d", self._nbytes, self._max_bytes)
        return self._nbytes

    @property
    def count(self) -> int:
        """How many surfaces are retained."""
        log.debug("BgMaskCache.count: %d surface(s)", len(self._entries))
        return len(self._entries)

    def __contains__(self, key: CacheKey) -> bool:
        """Whether *key* is cached, WITHOUT promoting it (hit/miss logging).

        Deliberately not a lookup: the transition log asks this before the
        resolve, and an answer that reordered the LRU would make merely
        observing the cache change what it evicts.
        """
        return key in self._entries

    # ── Per-tick access ───────────────────────────────────────────────

    def get(self, key: CacheKey) -> Any | None:
        """The surface cached under *key*, or None; a hit becomes most-recent."""
        entry = self._entries.get(key)
        if entry is None:
            log.debug("BgMaskCache.get: MISS (%d entries, %d bytes)",
                      len(self._entries), self._nbytes)
            return None
        self._entries.move_to_end(key)
        log.debug("BgMaskCache.get: HIT (%d entries, %d bytes)",
                  len(self._entries), self._nbytes)
        return entry[0]

    def put(self, key: CacheKey, surface: Any, nbytes: int,
            *, working_set_bytes: int | None = None) -> None:
        """Retain *surface* under *key*, evicting oldest entries past the cap.

        ``working_set_bytes`` is how much the caller will cycle through
        before it asks for this key again — for a video, the whole animation.
        A cyclic workload larger than the cache is LRU's worst case and
        scores a flat **zero** hit rate: every entry is evicted exactly one
        lap before it is needed.  Measured at 1600x720x897, caching bought
        0.0 ms/tick for 123 MB of retained surfaces.  So a working set that
        cannot fit is declined ENTIRELY rather than part-cached, and the
        device spends nothing instead of spending the budget to buy nothing.

        A single surface larger than the whole budget is declined for the
        same reason: keeping it would evict everything else to hold one
        frame, and the caller already holds the composite it just built.
        """
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._nbytes -= previous[1]
        needed = max(nbytes, working_set_bytes or 0)
        if needed > self._max_bytes:
            log.info(
                "BgMaskCache.put: a working set of %d byte(s) does not fit "
                "the %d-byte budget — not caching this workload at all, "
                "since a cycle larger than the cache never hits.  This "
                "device composes every tick.", needed, self._max_bytes,
            )
            return
        self._entries[key] = (surface, nbytes)
        self._nbytes += nbytes
        log.debug("BgMaskCache.put: stored %d byte(s) (%d entries, %d bytes)",
                  nbytes, len(self._entries), self._nbytes)
        self._evict_to_cap()

    def _evict_to_cap(self) -> None:
        """Drop least-recently-used entries until the total fits the cap."""
        evicted = 0
        while self._nbytes > self._max_bytes and self._entries:
            _key, (_surface, nbytes) = self._entries.popitem(last=False)
            self._nbytes -= nbytes
            evicted += 1
        if evicted:
            log.info(
                "BgMaskCache: evicted %d least-recent surface(s) to stay "
                "under %d bytes — now %d entries, %d bytes.  This panel is "
                "large enough that its animation does not fit the budget; it "
                "composes the frames it could not keep.",
                evicted, self._max_bytes, len(self._entries), self._nbytes,
            )

    def clear(self) -> None:
        """Drop every entry — the next tick composes from scratch."""
        log.debug("BgMaskCache.clear: dropping %d entries (%d bytes)",
                  len(self._entries), self._nbytes)
        self._entries.clear()
        self._nbytes = 0
