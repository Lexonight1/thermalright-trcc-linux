"""Cache HEALTH — what the real key derivation does to the real cache.

``test_bg_mask_cache`` covers the data structure: the byte budget, eviction
order, the working-set decline.  It hands the cache its own hand-built keys,
so it cannot see the thing that actually breaks — what
``DisplayService._bg_mask_key`` derives for a real workload, and what the
cache then does with it.

Three workloads, three shapes, and each fails differently:

* **static** — one entry, and every tick after the first is a HIT.  Getting
  this wrong costs a full compose per tick forever; nothing looks broken.
* **cyclic** (a video) — the cursor is in the key, so N frames make N entries
  and the second lap HITs.  Getting it wrong the other way is the "LCD frozen
  on the first frame" bug: the key stops moving, every tick HITs, and the
  panel shows frame 0 while the cursor advances underneath.
* **live** (a screen capture) — a key that never repeats.  It must not reach
  the cache at all.

**Why the live case is pinned now, while nothing renders that way.**  A live
source through the cached path is 100% miss forever AND evicts the entries
that would have hit, so a screencast running on one device degrades every
other source on it.  That surfaces as "the panel got slower after the
refactor" and **no other gate in this repo would see it** —
``dev/tools/wire_baseline.py`` is provably blind to it (a cache-key mutation
moves 0 of its 228 rows, measured).  This is the tripwire for the background
slot unification, so it is mutation-checked rather than assumed: caching every
capture under a never-repeating key fails both live pins.

That mutation had to be fixed before it proved anything -- the first version
keyed on ``id(frame)``, and CPython recycles the id of a collected object, so
the "unique" keys collided, no eviction pressure built, and the eviction pin
passed while the bug was present.  A mutation that does not do what it claims
is indistinguishable from a guard that works.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.adapters.theme.filesystem import FileContentStore
from trcc.core.models import Kind, ProductInfo, RawFrame, Theme, Wire
from trcc.core.protocol import DeviceProfile
from trcc.services.background import BackgroundSlot
from trcc.services.display import DisplayService
from trcc.services.media import MediaService, Playback
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings

from .conftest import FakePaths

SIZE = (320, 320)
PROFILE = DeviceProfile(320, 320, big_endian=True)
SENSORS = {"cpu:temp": 55.0}
KEY = "0402:3922"


def _info() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922, vendor="ALi", product="320x320",
        wire=Wire.SCSI, kind=Kind.LCD, device_type=1, fbl=100,
        native_resolution=SIZE, orientations=(0, 90, 180, 270),
    )


@pytest.fixture
def display(tmp_home: Path) -> DisplayService:
    renderer = QtRenderer()
    paths = FakePaths(tmp_home)
    return DisplayService(
        renderer=renderer, themes=FileContentStore(paths),
        overlay=OverlayService(renderer), settings=Settings(paths),
        media=MediaService(), backgrounds=BackgroundSlot(), paths=paths,
    )


@pytest.fixture
def theme(tmp_home: Path) -> Theme:
    """A static theme with a real background on disk."""
    from PySide6.QtGui import QColor, QImage

    d = tmp_home / "themes" / "static"
    d.mkdir(parents=True, exist_ok=True)
    img = QImage(*SIZE, QImage.Format.Format_ARGB32)
    img.fill(QColor(30, 60, 90))
    img.save(str(d / "00.png"))
    (d / "trcc.json").write_text(json.dumps({
        "name": "static", "width": SIZE[0], "height": SIZE[1],
        "elements": [],
    }), encoding="utf-8")
    return FileContentStore(FakePaths(tmp_home)).load(d)


def _jpeg(value: int) -> bytes:
    from PySide6.QtGui import QColor, QImage

    img = QImage(*SIZE, QImage.Format.Format_RGB888)
    img.fill(QColor(value, value, value))
    return QtRenderer().encode_jpeg(img, 90)


def _cache(display: DisplayService):
    return display._bg_cache(KEY)


def _render(display: DisplayService, theme: Theme, n: int = 1) -> None:
    for _ in range(n):
        display.build_frame(info=_info(), theme=theme, sensors=SENSORS,
                            profile=PROFILE)


# ── static ────────────────────────────────────────────────────────────


def test_a_static_theme_settles_on_one_entry(
    display: DisplayService, theme: Theme,
) -> None:
    """Ten ticks of an unchanging theme compose ONCE."""
    _render(display, theme, 10)

    assert _cache(display).count == 1, (
        "a static theme made more than one bg+mask entry — its key is moving "
        "when nothing about the background is"
    )


def test_a_static_theme_hits_after_the_first_tick(
    display: DisplayService, theme: Theme,
) -> None:
    """The second tick finds its key already there.

    Asserted through ``__contains__`` rather than a hit counter because that
    is the question the render path asks, and it does not promote.
    """
    _render(display, theme, 1)
    key = display._bg_mask_key(_info(), theme, SIZE)

    assert key in _cache(display), (
        "the second tick would MISS — a static theme recomposes every frame"
    )


# ── cyclic ────────────────────────────────────────────────────────────


def test_a_video_cycle_is_held_and_the_second_lap_hits(
    display: DisplayService, theme: Theme,
) -> None:
    """N frames → N entries, and coming back around HITS.

    This is the workload ``BgMaskCache`` exists for.  It also pins the
    opposite failure: were the cursor to leave the key, count would stay at 1
    and every tick would hit while the panel froze on frame 0.
    """
    frames = [_jpeg(v) for v in (20, 90, 160)]
    playback = Playback(frames=frames, fps=15)
    display._media._playbacks[KEY] = playback

    for _ in range(len(frames)):                          # first lap
        _render(display, theme, 1)
        playback.advance()

    assert _cache(display).count == len(frames), (
        f"expected one entry per frame, got {_cache(display).count} — the "
        f"cursor is not reaching the cache key"
    )

    for lap_frame in range(len(frames)):                   # second lap
        key = display._bg_mask_key(_info(), theme, SIZE)
        assert key in _cache(display), (
            f"the second lap MISSED at frame {lap_frame} — the cycle is not "
            f"being reused, so a video composes every tick forever"
        )
        playback.advance()


def test_a_video_key_moves_with_the_cursor(
    display: DisplayService, theme: Theme,
) -> None:
    """Two cursors, two keys.  The 'frozen on frame 0' bug is this inverted."""
    playback = Playback(frames=[_jpeg(20), _jpeg(200)], fps=15)
    display._media._playbacks[KEY] = playback

    first = display._bg_mask_key(_info(), theme, SIZE)
    playback.advance()
    second = display._bg_mask_key(_info(), theme, SIZE)

    assert first != second, (
        "the bg cache key did not move when the video cursor did — every "
        "tick will HIT and the panel will freeze on one frame"
    )


# ── live ──────────────────────────────────────────────────────────────


def test_a_live_capture_does_not_grow_the_cache(
    display: DisplayService, theme: Theme,
) -> None:
    """A screen capture is a key that never repeats — it must not be cached.

    The cache is primed with a real theme entry first, so "unchanged" is a
    statement about a populated cache rather than an empty one.
    """
    _render(display, theme, 1)
    before = _cache(display).count
    assert before == 1, "the cache was not primed — this pin proves nothing"

    for value in range(0, 120, 10):
        display.build_screencast_frame(
            info=_info(), theme=theme, sensors=SENSORS, profile=PROFILE,
            frame=RawFrame(data=bytes([value, value, value]) * (SIZE[0] * SIZE[1]),
                           width=SIZE[0], height=SIZE[1]),
        )

    assert _cache(display).count == before, (
        f"a live capture added {_cache(display).count - before} cache "
        f"entries — it will never hit them and they evict what would have"
    )


def test_a_live_capture_does_not_evict_the_theme_entry(
    display: DisplayService, theme: Theme,
) -> None:
    """The entry that WOULD hit survives the capture.

    Counting entries is not enough: a cache already at its byte cap keeps its
    count flat while live frames push the useful entry out.  So this asks for
    the theme's own key back.

    **The budget is shrunk to three surfaces on purpose.**  At the shipped
    128 MB cap a dozen 320x320 frames evict nothing, so this pin could not
    fail — measured: under a mutation that caches every capture, the
    entry-count pin failed and this one still passed.  A guard that cannot
    fail is not a guard, so the cache is sized to the pressure being tested.
    """
    from trcc.services.bg_cache import BgMaskCache

    one_surface = SIZE[0] * SIZE[1] * 4
    display._bg_caches[KEY] = BgMaskCache(3 * one_surface)

    _render(display, theme, 1)
    theme_key = display._bg_mask_key(_info(), theme, SIZE)
    assert theme_key in _cache(display), (
        "the theme entry never landed — this pin proves nothing"
    )

    for value in range(0, 120, 10):
        display.build_screencast_frame(
            info=_info(), theme=theme, sensors=SENSORS, profile=PROFILE,
            frame=RawFrame(data=bytes([value, value, value]) * (SIZE[0] * SIZE[1]),
                           width=SIZE[0], height=SIZE[1]),
        )

    assert theme_key in _cache(display), (
        "the theme's composed background was evicted by live frames that can "
        "never be reused — the live source degraded every other source"
    )
