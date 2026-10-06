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


# ── Per-orientation memory (the C#'s per-folder Theme.dc) ────────────────────

_COLOURS = {"Theme1": 0xC80000, "Theme2": 0x00C800, "Theme3": 0x0000C8,
            "Mine": 0xC8C800}


def _painted(directory: Path, width: int, height: int) -> Path:
    """A full-canvas theme in one colour, so a frame on the wire says which."""
    from PySide6.QtGui import QImage

    directory.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_RGB888)
    image.fill(_COLOURS[directory.name])
    image.save(str(directory / "00.png"))
    (directory / "trcc.json").write_text(
        json.dumps({"name": directory.name, "elements": []}), encoding="utf-8")
    return directory


@pytest.fixture
def folders(tmp_path: Path) -> Path:
    """Theme1-3 in both orientations, painted; "Mine" saved in landscape only."""
    paths = MockPlatform([_WIDE], tmp_path).paths()
    for name in ("Theme1", "Theme2", "Theme3"):
        _painted(paths.theme_dir(854, 480) / name, 854, 480)
        _painted(paths.theme_dir(480, 854) / name, 480, 854)
    _painted(paths.user_theme_dir(854, 480) / "Mine", 854, 480)
    return tmp_path


def _pick(app: App, folder: tuple[int, int], name: str, brightness: int) -> None:
    from trcc.core.commands import SetBrightness

    path = app.platform.paths().theme_dir(*folder) / name
    assert app.dispatch(LoadTheme(key=_KEY, path=path)).ok
    assert app.dispatch(SetBrightness(key=_KEY, percent=brightness)).ok


def _showing(app: App) -> tuple[str, str, int]:
    theme = app.active_themes[_KEY]
    return (theme.path.parent.name, theme.path.name,
            app.settings.for_device(_KEY).brightness)


def test_each_orientation_keeps_its_own_theme_and_brightness(folders: Path) -> None:
    """The C# reads the folder's own ``Theme.dc`` on a rotation: back in a
    folder, its theme and brightness come back; a folder never visited carries
    over what was playing (an empty file keeps the old values)."""
    app = _app(folders)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    _pick(app, (854, 480), "Theme3", 40)

    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    assert _showing(app) == ("theme480854", "Theme3", 40)       # carried over
    _pick(app, (480, 854), "Theme2", 80)

    from trcc.core.events import BrightnessChanged
    heard: list[int] = []
    app.events.subscribe(BrightnessChanged, lambda e: heard.append(e.percent))

    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    assert _showing(app) == ("theme854480", "Theme3", 40)
    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    assert _showing(app) == ("theme480854", "Theme2", 80)
    assert heard == [40, 80]            # every UI shows the folder's own level


def test_a_folder_left_untouched_is_not_remembered(folders: Path) -> None:
    """The C# writes a folder's ``Theme.dc`` only on a user action, so a folder
    merely passed through still reads empty and carries over the next time."""
    app = _app(folders)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    _pick(app, (854, 480), "Theme3", 40)
    app.dispatch(SetOrientation(key=_KEY, degrees=90))           # untouched
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    _pick(app, (854, 480), "Theme2", 60)

    app.dispatch(SetOrientation(key=_KEY, degrees=90))

    assert _showing(app) == ("theme480854", "Theme2", 60)


def test_a_theme_without_a_portrait_twin_shows_theme1_not_black(
    folders: Path,
) -> None:
    """A theme saved only in landscape used to stay on after a rotation; on a
    widescreen panel's portrait canvas its background drew solid black."""
    from PySide6.QtGui import QImage

    from trcc.core.commands import RenderAndSend

    app = _app(folders)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    assert app.dispatch(LoadTheme(
        key=_KEY,
        path=app.platform.paths().user_theme_dir(854, 480) / "Mine")).ok
    sent: list[bytes] = []
    send = app.send
    app.send = lambda key, payload, **kw: (  # type: ignore[method-assign]
        sent.append(bytes(payload)), send(key, payload, **kw))[1]

    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    assert app.dispatch(RenderAndSend(key=_KEY)).ok

    assert _showing(app)[:2] == ("theme480854", "Theme1")
    frame = QImage.fromData(sent[-1])
    assert frame.pixelColor(frame.width() // 2, frame.height() // 2).red() > 150


def test_a_rotation_does_not_switch_the_slideshow_off(folders: Path) -> None:
    """The rotation's theme load looked like a theme picked by hand to the
    running slideshow, which then switched itself off."""
    import time

    from trcc.core.commands import ConfigureSlideshow, SetSlideshow

    app = _app(folders)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    _pick(app, (854, 480), "Theme1", 100)
    assert app.dispatch(ConfigureSlideshow(
        key=_KEY, interval_s=1.0, themes=("Theme1", "Theme2"))).ok
    assert app.dispatch(SetSlideshow(key=_KEY, enabled=True)).ok
    first = app.settings.for_device(_KEY).current_theme
    deadline = time.monotonic() + 5
    while app.settings.for_device(_KEY).current_theme == first:
        assert time.monotonic() < deadline, "the slideshow never advanced"
        time.sleep(0.05)

    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    time.sleep(2.5)                     # the driver ticks once a second

    assert app.settings.for_device(_KEY).slideshow_enabled is True


def test_a_rotation_made_while_unplugged_applies_at_connect(folders: Path) -> None:
    """``SetOrientation`` works with the panel unplugged; the next connect
    must show that folder's own state.  A restart also proves the per-folder
    state survives the settings file."""
    app = _app(folders)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    _pick(app, (854, 480), "Theme3", 40)
    app.dispatch(SetOrientation(key=_KEY, degrees=90))
    _pick(app, (480, 854), "Theme2", 80)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    app.close()

    again = App(MockPlatform([_WIDE], folders), renderer=QtRenderer())
    again.settings.set_orientation(_KEY, 90)        # turned while unplugged
    assert again.dispatch(ConnectDevice(key=_KEY)).ok
    assert again.dispatch(RestoreDeviceState(key=_KEY)).ok

    assert _showing(again) == ("theme480854", "Theme2", 80)


def test_0_and_180_share_one_folder(folders: Path) -> None:
    """The C# picks the folder by ``themeDirection % 180``."""
    app = _app(folders)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))
    _pick(app, (854, 480), "Theme3", 40)

    app.dispatch(SetOrientation(key=_KEY, degrees=180))

    assert _showing(app) == ("theme854480", "Theme3", 40)
    assert app.settings.for_device(_KEY).orientation_slots == {}
