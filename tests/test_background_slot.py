"""BackgroundSlot — the current picture, and the token that proves it is current.

Two halves, and the second is the one that matters.

**The value**: a slot hit means ``_resolve_background`` does not run, so the
background file is not re-opened.  That is narrower than it sounds and worth
stating exactly: when the bg+mask cache HITS, ``_build_bg_mask`` is never
called and the slot is not consulted at all.  The slot earns its keep on the
misses where the SOURCE did not change but the way it is drawn did — rotate
the panel, toggle the mask, change the fit — which today re-opened and
re-decoded the same file every time.

**The guard**: the slot is read BY TOKEN, and a mismatch resolves rather than
serving what is there.  Without that check a per-device slot is a trap: render
theme X while the slot holds theme Y's background and you get Y's picture
under X's mask and X's metrics, silently, with a cache entry to keep it.
``SceneCache`` and ``BgMaskCache`` already store a value beside the key that
produced it and check on the way out; this does the same.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.adapters.theme.filesystem import FileContentStore
from trcc.core.models import FitMode, Kind, ProductInfo, Theme, Wire
from trcc.core.protocol import DeviceProfile
from trcc.services.background import Background, BackgroundSlot
from trcc.services.display import DisplayService
from trcc.services.media import MediaService, Playback
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings

from .conftest import FakePaths

SIZE = (320, 320)
PROFILE = DeviceProfile(320, 320, big_endian=True)
KEY = "0402:3922"


def _info() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922, vendor="ALi", product="320x320",
        wire=Wire.SCSI, kind=Kind.LCD, device_type=1, fbl=100,
        native_resolution=SIZE, orientations=(0, 90, 180, 270),
    )


# ── the slot itself, no renderer ──────────────────────────────────────


def test_a_pushed_background_comes_back_under_its_token() -> None:
    slot = BackgroundSlot()
    bg = Background("surface", is_user_content=True)

    slot.push(KEY, ("theme", "/a"), bg)

    assert slot.current(KEY, ("theme", "/a")) is bg


def test_a_different_token_reads_as_absent() -> None:
    """THE GUARD.  A stale slot must answer "nothing", never "this"."""
    slot = BackgroundSlot()
    slot.push(KEY, ("theme", "/a"), Background("theme-a-surface"))

    assert slot.current(KEY, ("theme", "/b")) is None, (
        "the slot served theme A's background to a caller asking for B — "
        "that renders A's picture under B's mask and B's metrics, silently"
    )


def test_an_untouched_device_reads_as_absent() -> None:
    assert BackgroundSlot().current(KEY, ("theme", "/a")) is None


def test_clear_drops_only_that_device() -> None:
    slot = BackgroundSlot()
    slot.push("a:1", ("t",), Background("a"))
    slot.push("b:2", ("t",), Background("b"))

    slot.clear("a:1")

    assert slot.current("a:1", ("t",)) is None
    assert slot.current("b:2", ("t",)) is not None


# ── the token, against the real service ───────────────────────────────


@pytest.fixture
def display(tmp_home: Path) -> DisplayService:
    renderer = QtRenderer()
    paths = FakePaths(tmp_home)
    return DisplayService(
        renderer=renderer, themes=FileContentStore(paths),
        overlay=OverlayService(renderer), settings=Settings(paths),
        media=MediaService(), backgrounds=BackgroundSlot(), paths=paths,
    )


def _theme(tmp_home: Path, name: str) -> Theme:
    from PySide6.QtGui import QColor, QImage

    d = tmp_home / "themes" / name
    d.mkdir(parents=True, exist_ok=True)
    img = QImage(*SIZE, QImage.Format.Format_ARGB32)
    img.fill(QColor(40, 80, 120))
    img.save(str(d / "00.png"))
    (d / "trcc.json").write_text(json.dumps({
        "name": name, "width": SIZE[0], "height": SIZE[1], "elements": [],
    }), encoding="utf-8")
    return FileContentStore(FakePaths(tmp_home)).load(d)


def test_two_themes_get_two_tokens(
    display: DisplayService, tmp_home: Path,
) -> None:
    """The identity the slot is read by actually distinguishes sources."""
    a = _theme(tmp_home, "a")
    b = _theme(tmp_home, "b")

    assert (display._background_token(_info(), a)
            != display._background_token(_info(), b))


def test_the_token_moves_with_the_video_cursor(
    display: DisplayService, tmp_home: Path,
) -> None:
    """Identity includes WHICH FRAME — the cursor is part of the source.

    The bg+mask cache key is built from this token, so a token that ignored
    the cursor is the "frozen on frame 0" bug: every tick HITs while the
    picture underneath keeps changing.
    """
    theme = _theme(tmp_home, "vid")
    playback = Playback(frames=[b"a", b"b"], fps=15)
    display._media._playbacks[KEY] = playback

    first = display._background_token(_info(), theme)
    playback.advance()

    assert display._background_token(_info(), theme) != first


def test_the_token_ignores_the_mask_and_the_canvas(
    display: DisplayService, tmp_home: Path,
) -> None:
    """Identity is the SOURCE, not how it is drawn.

    This is what makes the slot reusable across a mask toggle or a rotation:
    those change the composite, not which background it is.  Were they in the
    token, the slot would miss exactly when it is meant to help and the file
    would be re-opened for a rotation.
    """
    theme = _theme(tmp_home, "t")
    s = display._settings.for_device(KEY)
    before = display._background_token(_info(), theme)

    s.mask_visible = not s.mask_visible
    s.mask_position = (11, 13)
    s.orientation = 90

    assert display._background_token(_info(), theme) == before


def test_a_mask_change_reuses_the_background_instead_of_reopening_it(
    display: DisplayService, tmp_home: Path,
) -> None:
    """WHERE THE SLOT EARNS ITS KEEP.

    Toggling the mask misses the bg+mask cache (mask state is in that key)
    but keeps the token, so the background is reused rather than re-opened.
    Counted on ``Renderer.open_image`` because that is the cost being
    avoided — a file read and a decode, per tick, for a picture already held.
    """
    theme = _theme(tmp_home, "t")
    s = display._settings.for_device(KEY)
    s.mask_visible = False

    display.build_frame(info=_info(), theme=theme, sensors={}, profile=PROFILE)

    opened: list[Path] = []
    real_open = display._r.open_image

    def counting_open(path: Path):
        opened.append(path)
        return real_open(path)

    display._r.open_image = counting_open
    s.fit_mode = FitMode.STRETCH                   # moves the bg cache key
    display.build_frame(info=_info(), theme=theme, sensors={}, profile=PROFILE)

    assert opened == [], (
        f"the background was re-opened {len(opened)}x for a change that did "
        f"not alter WHICH background it is: {opened}"
    )


def test_the_token_follows_the_RESOLVED_background_not_the_theme_dir(
    display: DisplayService, tmp_home: Path,
) -> None:
    """A reference theme's background can live in either root — identity
    must name the file that RESOLVED, not the directory that asked for it.

    ``background_path`` follows ``theme.config["background"]`` through
    ``_resolve_asset_ref``, which tries the USER root first and the default
    root second.  So one unchanged theme at one unchanged path can resolve to
    two different files, and a key naming only ``theme.path`` cannot tell them
    apart: the user's asset appears, the key does not move, and the cache goes
    on serving the shipped picture.

    This is the same fault that shipped a black panel once already — the
    origin read off the active theme's directory while the background came
    from somewhere else.  That was fixed in the compositor; the cache key kept
    it until identity moved into ``_background_token``.
    """
    from PySide6.QtGui import QColor, QImage

    paths = FakePaths(tmp_home)
    ref = "web/320320/shared.png"

    def _asset(root: Path, colour: QColor) -> None:
        dest = root / ref
        dest.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(*SIZE, QImage.Format.Format_ARGB32)
        img.fill(colour)
        img.save(str(dest))

    _asset(paths.data_dir(), QColor(10, 20, 30))          # shipped only

    d = tmp_home / "themes" / "ref"
    d.mkdir(parents=True, exist_ok=True)
    (d / "trcc.json").write_text(json.dumps({
        "name": "ref", "width": SIZE[0], "height": SIZE[1],
        "background": ref, "elements": [],
    }), encoding="utf-8")
    theme = FileContentStore(paths).load(d)

    shipped = display._background_token(_info(), theme)

    _asset(paths.user_data_dir(), QColor(200, 30, 30))    # user asset shadows it

    assert display._background_token(_info(), theme) != shipped, (
        "the token did not move when the SAME theme started resolving to a "
        "different file — the user's background would never be painted"
    )
