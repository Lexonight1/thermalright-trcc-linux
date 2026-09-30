"""A rotation shows the rotated theme's own overlay layout, as 2.1.8 does.

2.1.8 ``UpDateUCComboBox1`` (FormCZTV.cs:1927-1960) recomputes the theme
folder (``ThemeML``: resolution AND orientation) and reloads that folder's
theme, config1.dc included.  Layouts are keyed by folder and nothing
transforms coordinates between them.

We kept the landscape layer across a rotation (June, read from the wrong
release), which was harmless while the layer held only a user's edits.  Once
every theme load filled it (#276), a rotated 854x480 panel drew the landscape
layout -- x up to 660 -- on a 480-wide canvas, and kept doing so after a
restart.  The layer now records the folder it was laid out in.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    LoadTheme,
    RestoreDeviceState,
    SetOrientation,
    UpdateOverlayElement,
)
from trcc.core.models import OverlayElement

from .mock_platform import MockPlatform

_KEY = "87ad:70db"
_WIDE = {"type": "lcd", "vid": "87ad", "pid": "70db", "resolution": "854x480",
         "pm": 11, "sub": 0}
_LANDSCAPE = [{"id": "cpu", "type": "text", "text": "CPU", "x": 600, "y": 100, "size": 16}]
_PORTRAIT = [{"id": "cpu", "type": "text", "text": "CPU", "x": 100, "y": 700, "size": 16}]


def _theme(directory: Path, elements: list[dict]) -> Path:
    from PySide6.QtGui import QImage

    directory.mkdir(parents=True, exist_ok=True)
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0x0A141E)
    image.save(str(directory / "00.png"))
    (directory / "trcc.json").write_text(
        json.dumps({"name": directory.name, "elements": elements}), encoding="utf-8")
    return directory


def _app(root: Path) -> App:
    app = App(MockPlatform([_WIDE], root), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    return app


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Theme1 in both orientations, each with its own layout."""
    paths = MockPlatform([_WIDE], tmp_path).paths()
    _theme(paths.theme_dir(854, 480) / "Theme1", _LANDSCAPE)
    _theme(paths.theme_dir(480, 854) / "Theme1", _PORTRAIT)
    return tmp_path


def _landscape(app: App) -> None:
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    theme = app.platform.paths().theme_dir(854, 480) / "Theme1"
    assert app.dispatch(LoadTheme(key=_KEY, path=theme)).ok


def _layout(app: App) -> list[tuple[int, int]]:
    return [(e.x, e.y) for e in app.settings.for_device(_KEY).user_overlay_elements or ()]


def test_a_rotation_shows_the_rotated_themes_own_layout(root: Path) -> None:
    app = _app(root)
    _landscape(app)

    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    assert _layout(app) == [(100, 700)]

    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    assert _layout(app) == [(600, 100)]


def test_an_edit_is_dropped_by_a_rotation_as_in_the_windows_app(root: Path) -> None:
    """FormCZTV keeps a layout per folder and writes it only on Save; an
    unsaved edit is replaced by the rotated theme's layout."""
    app = _app(root)
    _landscape(app)
    app.dispatch(UpdateOverlayElement(key=_KEY, element_id="cpu", x=300))

    app.dispatch(SetOrientation(key=_KEY, degrees=90))

    assert _layout(app) == [(100, 700)]


def test_an_edit_survives_a_restart_at_the_same_orientation(root: Path) -> None:
    """The reason the layer is kept at all: a reconnect shows the last preview
    with its changes."""
    app = _app(root)
    _landscape(app)
    app.dispatch(UpdateOverlayElement(key=_KEY, element_id="cpu", x=300))
    app.close()

    again = _app(root)
    assert again.dispatch(RestoreDeviceState(key=_KEY)).ok

    assert _layout(again) == [(300, 100)]


def _saved_before_the_fix(root: Path, orientation: int, layout: list[dict]) -> App:
    """A config written before the layer recorded its folder."""
    app = _app(root)
    folder = (854, 480) if orientation == 0 else (480, 854)
    app.settings.set_orientation(_KEY, orientation)
    app.settings.set_current_theme(
        _KEY, str(app.platform.paths().theme_dir(*folder) / "Theme1"))
    app.settings.set_user_overlay_elements(
        _KEY, [OverlayElement.from_dict(e) for e in layout])
    app.settings.for_device(_KEY).user_overlay_catalog = None
    app.close()
    return _app(root)


def test_a_config_broken_by_the_old_rotation_heals_on_the_next_start(root: Path) -> None:
    app = _saved_before_the_fix(root, 90, _LANDSCAPE)       # x=600 on a 480-wide canvas

    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok

    dev = app.settings.for_device(_KEY)
    assert (_layout(app), dev.user_overlay_catalog) == ([(100, 700)], "theme480854")


def test_an_old_config_that_fits_is_kept_and_learns_its_folder(root: Path) -> None:
    edited = [dict(_LANDSCAPE[0], x=300)]
    app = _saved_before_the_fix(root, 0, edited)

    assert app.dispatch(RestoreDeviceState(key=_KEY)).ok

    dev = app.settings.for_device(_KEY)
    assert (_layout(app), dev.user_overlay_catalog) == ([(300, 100)], "theme854480")
