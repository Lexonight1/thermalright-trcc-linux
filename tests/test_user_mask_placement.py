"""An uploaded mask lands at the top-left, as the C# places it.

The C# shrinks an upload to fit the oriented canvas (never enlarging it) and
centres it on itself -- ``XvalMB = W / 2``, ``YvalMB = H / 2``
(FormCZTV.cs:5786-5822, :6032).  Our DC writer never wrote a mask position,
so every user mask stored the codec's (0, 0): drawn at minus half its size
whenever it was not full-size, which a portrait upload never is against the
landscape profile.  Driven 2026-09-30 on an 854x480 panel at 90 degrees: a
full-size 480x854 mask landed at (-240, -427), a quarter of it on the panel.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    AddOverlayElement,
    ConnectDevice,
    LcdSnapshot,
    SetOrientation,
    UploadCustomMask,
)
from trcc.services import _dc as Dc
from trcc.services.overlay import OverlayService

_KEY = "87ad:70db"
_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db", "resolution": "854x480",
         "pm": 11, "sub": 5}


@pytest.fixture
def app(tmp_path: Path) -> Iterator[App]:
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    yield app
    app.close()


def _png(path: Path, size: tuple[int, int]) -> Path:
    from PySide6.QtGui import QColor, QImage

    image = QImage(*size, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 0, 0, 128))
    assert image.save(str(path))
    return path


def _upload(app: App, tmp_path: Path, orientation: int,
            size: tuple[int, int]) -> tuple[Path, object]:
    app.dispatch(SetOrientation(key=_KEY, degrees=orientation))
    source = _png(tmp_path / f"up{size[0]}x{size[1]}.png", size)
    assert app.dispatch(UploadCustomMask(key=_KEY, source=source)).ok
    snap = app.dispatch(LcdSnapshot(key=_KEY))
    return Path(snap.mask_path or ""), snap.mask_position


def _stored(mask: Path) -> tuple[int, int]:
    from PySide6.QtGui import QImageReader

    size = QImageReader(str(mask)).size()
    return size.width(), size.height()


@pytest.mark.parametrize(("orientation", "size"), [
    (90, (480, 854)),      # full-size portrait -- was (-240, -427)
    (90, (400, 700)),      # smaller portrait -- was (-200, -350)
    (0, (854, 480)),       # full-size landscape -- always worked
    (0, (300, 200)),       # smaller landscape -- was (-150, -100)
])
def test_an_upload_lands_at_the_top_left(
    app: App, tmp_path: Path, orientation: int, size: tuple[int, int],
) -> None:
    mask, position = _upload(app, tmp_path, orientation, size)

    assert position == (0, 0)
    assert Dc.File(mask.parent / "config1.dc").read()["mask_position"] == [
        size[0] // 2, size[1] // 2]


@pytest.mark.parametrize(("orientation", "size", "stored"), [
    # 480x854: width first -> 480x960, still too tall -> height -> 427x854.
    (90, (1000, 2000), (427, 854)),
    # 854x480: width first -> 854x427, which fits.  Height alone would give
    # 960x480, wider than the panel -- the order is the C#'s, not a choice.
    (0, (1200, 600), (854, 427)),
    # Only too tall -> height -> 240x480.
    (0, (300, 600), (240, 480)),
])
def test_an_oversized_upload_shrinks_to_fit_like_the_csharp(
    app: App, tmp_path: Path, orientation: int, size: tuple[int, int],
    stored: tuple[int, int],
) -> None:
    """Integer arithmetic, never enlarged (FormCZTV.cs:5786-5809)."""
    mask, position = _upload(app, tmp_path, orientation, size)

    assert _stored(mask) == stored
    assert position == (0, 0)


def test_an_overlay_edit_keeps_the_centre(app: App, tmp_path: Path) -> None:
    """``persist_user_mask_dc`` rewrites the DC on every overlay edit; it
    used to write the (0, 0) back."""
    mask, _ = _upload(app, tmp_path, 90, (400, 700))

    assert app.dispatch(AddOverlayElement(key=_KEY, type="text", text="x")).ok

    assert Dc.File(mask.parent / "config1.dc").read()["mask_position"] == [200, 350]


def test_a_mask_uploaded_before_the_fix_still_lands_at_the_top_left(
    tmp_path: Path,
) -> None:
    """Its DC stores (0, 0), which no shipped DC does (0 of 1704); read as
    unset, it is placed where the C# places an upload."""
    from trcc.services._dc import Writer

    folder = tmp_path / "custom_old"
    folder.mkdir()
    _png(folder / "01.png", (400, 700))
    (folder / "config1.dc").write_bytes(Writer().serialize(
        {"elements": [], "mask_visible": True, "mask_position": (0, 0)}))

    assert OverlayService.calculate_mask_position(
        folder, (400, 700), (854, 480)) == (0, 0)


def test_a_board_sensor_on_a_user_mask_survives_in_its_dc(
    app: App, tmp_path: Path,
) -> None:
    """S3: every overlay edit rewrites the user mask's config1.dc, and a metric
    the DC pair table cannot name was written as (0, 0) and dropped on read.
    In this session the element lives on in the device's own layer, so the
    loss shows wherever the mask's DC is read fresh -- another device, an
    export, a reset layer.  Read it back the way they do."""
    mask, _ = _upload(app, tmp_path, 0, (300, 200))
    assert app.dispatch(AddOverlayElement(
        key=_KEY, type="metric", metric="board:nct6798_auxtin1:temp")).ok

    stored = Dc.File(mask.parent / "config1.dc").read()["elements"]

    assert "board:nct6798_auxtin1:temp" in [e.get("metric") for e in stored]
