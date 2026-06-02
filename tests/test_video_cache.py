"""VideoFrameCache — pre-composited frame cache + refresh-rate overlay slot.

Uses a counting renderer stand-in so lazy-fill is directly observable: the
mask composite happens once per frame at build, and the per-frame
brightness dim happens once per frame on first access (then it's a cached
lookup) — the whole point of decoupling the animation loop from the
metric overlay.
"""
from __future__ import annotations

from typing import Any

from trcc.core.models import RawFrame
from trcc.services.video_cache import VideoFrameCache


class _CountingRenderer:
    """Minimal Renderer stand-in — counts the calls the cache makes and
    returns identifiable opaque surfaces."""

    def __init__(self) -> None:
        self.from_raw = 0
        self.composites = 0
        self.brightness = 0

    def from_raw_rgb24(self, frame: RawFrame) -> Any:
        self.from_raw += 1
        return ("raw", bytes(frame.data[:2]))

    def composite(self, base: Any, overlay: Any, position: Any,
                  mask: Any | None = None) -> Any:
        self.composites += 1
        return ["composite", base, overlay, position]

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        self.brightness += 1
        return ["dim", surface, percent]


def _frames(n: int) -> list[RawFrame]:
    return [RawFrame(data=bytes([i, i]), width=1, height=1) for i in range(n)]


def _cache(renderer: _CountingRenderer) -> VideoFrameCache:
    return VideoFrameCache(renderer)  # type: ignore[arg-type]


# ── build ────────────────────────────────────────────────────────────


def test_build_composites_mask_once_per_frame_and_defers_brightness() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(3), mask="MASK", mask_position=(0, 0), brightness=80)

    assert c.active is True
    assert c.frame_count == 3
    assert r.from_raw == 3          # one raw→surface per frame
    assert r.composites == 3        # one mask composite per frame (L2)
    assert r.brightness == 0        # L3 deferred — nothing accessed yet


def test_build_without_mask_skips_composite() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(2), mask=None, mask_position=(0, 0), brightness=100)
    assert c.frame_count == 2
    assert r.composites == 0


def test_build_empty_invalidates() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build([], mask="MASK", mask_position=(0, 0), brightness=100)
    assert c.active is False
    assert c.frame_count == 0


# ── get_surface — lazy L3 + caching ──────────────────────────────────


def test_get_surface_lazy_dim_then_cached() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(3), mask="MASK", mask_position=(0, 0), brightness=80)

    s0a = c.get_surface(0)
    assert r.brightness == 1        # dimmed once on first access
    s0b = c.get_surface(0)
    assert s0a is s0b               # cached — same surface object
    assert r.brightness == 1        # not re-dimmed

    c.get_surface(1)
    assert r.brightness == 2        # frame 1 dimmed on its first access


def test_get_surface_passthrough_at_full_brightness() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(2), mask="MASK", mask_position=(0, 0), brightness=100)
    surface = c.get_surface(0)
    assert surface is not None
    assert r.brightness == 0        # brightness >= 100 → no dim, passthrough


def test_get_surface_out_of_range_returns_none() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(2), mask="MASK", mask_position=(0, 0), brightness=100)
    assert c.get_surface(-1) is None
    assert c.get_surface(2) is None


def test_set_brightness_resets_l3() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(2), mask="MASK", mask_position=(0, 0), brightness=80)
    first = c.get_surface(0)
    assert r.brightness == 1
    c.set_brightness(50)
    second = c.get_surface(0)
    assert r.brightness == 2        # re-dimmed at the new brightness
    assert second is not first


# ── overlay slot ─────────────────────────────────────────────────────


def test_update_overlay_keyed() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    assert c.has_overlay is False
    assert c.update_overlay("O1", key=("a",)) is True
    assert c.has_overlay is True
    assert c.update_overlay("O1", key=("a",)) is False   # unchanged key → no-op
    assert c.update_overlay("O2", key=("b",)) is True     # new key → updated
    assert c.overlay == "O2"


def test_composited_with_and_without_overlay() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(2), mask="MASK", mask_position=(0, 0), brightness=100)

    # No overlay → returns the bare frame surface (no extra composite).
    composites_after_build = r.composites
    base = c.composited(0)
    assert base is c.get_surface(0)
    assert r.composites == composites_after_build

    # With overlay → one composite of frame + overlay.
    c.update_overlay("OVL", key=("k",))
    out = c.composited(0)
    assert out is not None
    assert r.composites == composites_after_build + 1
    assert out[0] == "composite"     # ["composite", base, overlay, pos]
    assert out[2] == "OVL"


def test_composited_out_of_range_returns_none() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(1), mask="MASK", mask_position=(0, 0), brightness=100)
    c.update_overlay("OVL", key=("k",))
    assert c.composited(5) is None


# ── invalidate ───────────────────────────────────────────────────────


def test_invalidate_clears_everything() -> None:
    r = _CountingRenderer()
    c = _cache(r)
    c.build(_frames(2), mask="MASK", mask_position=(0, 0), brightness=80)
    c.update_overlay("OVL", key=("k",))
    c.invalidate()
    assert c.active is False
    assert c.frame_count == 0
    assert c.has_overlay is False
    assert c.get_surface(0) is None
