"""VideoFrameCache — a per-cursor store of finished animation-frame surfaces.

DisplayService builds one bg+mask surface per video cursor and hands the
list here; build_frame pulls get_surface(cursor) each tick.  The store
just holds the surfaces and returns them by index — the DisplayService
integration (the surfaces actually being byte-identical to the live path)
is covered in test_video_playback.py.
"""
from __future__ import annotations

from trcc.services.video_cache import VideoFrameCache


def test_build_stores_frames() -> None:
    c = VideoFrameCache()
    c.build(["a", "b", "c"])
    assert c.active is True
    assert c.frame_count == 3


def test_build_empty_invalidates() -> None:
    c = VideoFrameCache()
    c.build([])
    assert c.active is False
    assert c.frame_count == 0


def test_get_surface_returns_by_index() -> None:
    c = VideoFrameCache()
    c.build(["a", "b"])
    assert c.get_surface(0) == "a"
    assert c.get_surface(1) == "b"


def test_get_surface_out_of_range_returns_none() -> None:
    c = VideoFrameCache()
    c.build(["a"])
    assert c.get_surface(-1) is None
    assert c.get_surface(5) is None


def test_get_surface_unbuilt_returns_none() -> None:
    c = VideoFrameCache()
    assert c.get_surface(0) is None
    assert c.active is False


def test_invalidate_clears() -> None:
    c = VideoFrameCache()
    c.build(["a", "b"])
    c.invalidate()
    assert c.active is False
    assert c.frame_count == 0
    assert c.get_surface(0) is None
