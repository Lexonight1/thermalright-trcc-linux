"""hudgui (``ui/hudgui``) -- the gui redesigned as a HUD (his design, 2026-11-05).

His goal: "so gamers can have a thin bar above their gaming".  At rest the
window is only the chrome bar, always on top; a click drops a panel down,
clearing the last one lit; a second click on the same item pulls it up and the
window is the bar again.  Devices drops down the found devices as the gui's
symbols; a picked one opens its side panel: its own tab bar and the shared
live preview.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.conftest import show_a_theme
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice

_KEY = "0402:3922"


@pytest.fixture
def window(tmp_path: Path, qtbot: Any) -> Iterator[Any]:
    from trcc.ui.hudgui.window import HudWindow

    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    win = HudWindow(app)
    qtbot.addWidget(win)
    win.show()
    yield win
    app.close()


def _lit(win: Any) -> list[str]:
    return [item for item, button in win.bar.buttons.items() if button.isChecked()]


def test_at_rest_it_is_only_the_bar_on_top_of_everything(window: Any) -> None:
    from trcc.ui.hudgui.chrome import CHROME_HEIGHT

    assert list(window.bar.buttons) == ["Devices", "Sensors", "Modules",
                                        "Control Center", "About"]
    flags = window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert window.height() == CHROME_HEIGHT and not window.drop.isVisible()


def test_devices_lists_the_symbols_and_a_pick_opens_its_side_panel(
        window: Any, qtbot: Any) -> None:
    """MUTATION CHECK: drop the ``set_key`` in ``DevicesView._on_picked`` and
    the panel's preview shows no device."""
    from trcc.ui.qt_preview import PreviewSurface

    window.bar.buttons["Devices"].click()
    devices = window.panel("Devices")
    button = devices.menu.buttons[_KEY]
    assert not button.icon().isNull() and button.text() == ""   # the symbol, as gui
    assert not devices.panel.isVisible()                        # until one is picked
    button.click()
    qtbot.waitUntil(devices.panel.isVisible)
    assert window._selection.key == _KEY and button.isChecked()
    assert list(devices.panel.tabs.tabs) == ["Local Themes", "Cloud Masks",
                                             "Cloud Backgrounds", "Settings"]
    assert devices.panel.tabs.current == "Local Themes"
    assert isinstance(devices.panel.preview, PreviewSurface)


def test_a_menu_item_drops_its_panel_and_a_second_click_pulls_it_up(
        window: Any, qtbot: Any) -> None:
    """MUTATION CHECK: drop the ``item == self.active`` branch in
    ``HudWindow._on_menu`` and the second click leaves the panel down."""
    from trcc.ui.hudgui.chrome import CHROME_HEIGHT

    window.bar.buttons["Modules"].click()
    assert (window.active, _lit(window), window.drop.title) == ("Modules", ["Modules"], "Modules")
    qtbot.waitUntil(window.drop.isVisible)
    assert window.height() > CHROME_HEIGHT
    window.bar.buttons["Sensors"].click()                  # clears the last one lit
    assert (window.active, _lit(window)) == ("Sensors", ["Sensors"])
    window.bar.buttons["Sensors"].click()                  # the same one again: up
    assert (window.active, _lit(window)) == (None, [])
    qtbot.waitUntil(lambda: not window.drop.isVisible(), timeout=2000)
    assert window.height() == CHROME_HEIGHT              # back to just the bar


def test_escape_pulls_the_open_panel_up(window: Any, qtbot: Any) -> None:
    window.bar.buttons["About"].click()
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert (window.active, _lit(window)) == (None, [])


def test_the_bar_reads_out_the_cpu_and_gpu_temperature(window: Any) -> None:
    """In the user's unit, from the broadcast every UI hears."""
    from trcc.core.events import SensorsUpdated
    from trcc.core.models import HardwareMetrics

    window._on_sensors(SensorsUpdated(readings={}, temp_unit="F", reading_count=0,
                                      metrics=HardwareMetrics(cpu_temp=122.0,
                                                              gpu_temp=140.0)))
    assert window.bar.readout.text() == "CPU 122°F   GPU 140°F"


# ── Local Themes ─────────────────────────────────────────────────────────


def _themes(app: App, *names: str) -> list[Path]:
    """Installed 320x320 themes, each a picture of its own."""
    from PySide6.QtGui import QColor, QImage

    folder = app.platform.paths().theme_dir(320, 320)
    made = []
    for index, name in enumerate(names):
        theme = folder / name
        theme.mkdir(parents=True)
        (theme / "trcc.json").write_text(
            '{"name": "%s", "width": 320, "height": 320, "elements": []}' % name,
            encoding="utf-8")
        img = QImage(320, 320, QImage.Format.Format_RGB888)
        img.fill(QColor(40 * index, 80, 160))
        assert img.save(str(theme / "00.png"))
        made.append(theme)
    return made


def _tab(window: Any, qtbot: Any, name: str) -> Any:
    """The picked device's tab *name*, shown."""
    window.bar.buttons["Devices"].click()
    devices = window.panel("Devices")
    devices.menu.buttons[_KEY].click()
    qtbot.waitUntil(devices.panel.isVisible)
    devices.panel.tabs.tabs[name].click()
    return devices.panel.pages[name]


def _local_themes(window: Any, qtbot: Any) -> Any:
    return _tab(window, qtbot, "Local Themes")


def _lit_names(grid: Any) -> list[str]:
    return [Path(tile_id).name for tile_id, t in grid.tiles.items() if t.isChecked()]


def test_local_themes_lists_the_devices_themes_and_a_click_loads_one(
        window: Any, qtbot: Any) -> None:
    """The gui's way: one click loads it onto the panel, and it lights.
    MUTATION CHECK: return a query from ``LocalThemes._pick`` instead of
    ``LoadTheme`` and nothing loads."""
    one, two, three = _themes(window._app, "One", "Two", "Three")
    grid = _local_themes(window, qtbot)
    grid.refresh()
    assert {Path(p).name for p in grid.tiles} >= {"One", "Two", "Three"}
    grid.tiles[str(two.resolve())].click()
    qtbot.waitUntil(lambda: _lit_names(grid) == ["Two"], timeout=3000)


def test_the_light_follows_a_theme_loaded_from_another_ui(
        window: Any, qtbot: Any) -> None:
    """The light is the App's, never this window's last click.  MUTATION
    CHECK: drop the ``theme_loaded`` connection in ``ThemeGrid.__init__``."""
    from trcc.core.commands import LoadTheme

    one, two = _themes(window._app, "One", "Two")
    grid = _local_themes(window, qtbot)
    grid.refresh()
    assert window._app.dispatch(LoadTheme(key=_KEY, path=one)).ok   # e.g. the CLI
    qtbot.waitUntil(lambda: grid.tiles[str(one.resolve())].isChecked(), timeout=2000)


def test_cloud_masks_lays_one_over_the_theme_and_lights_it(
        window: Any, qtbot: Any) -> None:
    """The App names the mask's FILE; the tile is its folder -- it lights.
    MUTATION CHECK: compare ``mask_path`` itself in ``CloudMasks._current``."""
    from PySide6.QtGui import QColor, QImage

    folder = window._app.platform.paths().cloud_mask_dir(320, 320)
    for name in ("001a", "002a"):
        (folder / name).mkdir(parents=True)
        img = QImage(320, 320, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 200, 0, 120))
        assert img.save(str(folder / name / "01.png"))
    grid = _tab(window, qtbot, "Cloud Masks")
    grid.refresh()
    assert sorted(Path(t).name for t in grid.tiles) == ["001a", "002a"]
    grid.tiles[str((folder / "002a").resolve())].click()
    qtbot.waitUntil(lambda: _lit_names(grid) == ["002a"], timeout=3000)


def test_cloud_backgrounds_by_category_and_a_failed_download_says_why(
        window: Any, qtbot: Any) -> None:
    """The catalog's categories, All first; one shows its own only.  A click
    downloads -- offline here, so the tab SAYS it failed, never silently."""
    grid = _tab(window, qtbot, "Cloud Backgrounds")
    every = len(grid.tiles)
    assert list(grid._pills)[0] == "all" and len(grid._pills) > 1 and every
    other = list(grid._pills)[1]
    grid._pills[other].click()
    assert 0 < len(grid.tiles) < every
    assert all(tile_id.startswith(other) for tile_id in grid.tiles)
    next(iter(grid.tiles.values())).click()
    qtbot.waitUntil(lambda: grid.note.text() != "", timeout=5000)


def test_the_snapshot_says_which_background_file_the_user_chose(window: Any) -> None:
    """What lights a cloud background in every UI: the file the user chose
    (a cloud one is ``<id>.mp4``) -- None for the theme's own."""
    from trcc.core.commands import LcdSnapshot
    from trcc.core.models import VideoBackground

    app = window._app
    assert app.dispatch(LcdSnapshot(key=_KEY)).background_file is None
    app.settings.set_background(_KEY, VideoBackground("/data/web/320320/a017.mp4"))
    assert app.dispatch(LcdSnapshot(key=_KEY)).background_file == "/data/web/320320/a017.mp4"


# ── The device Settings tab, and the controls under the preview ──────────


def test_the_settings_switches_change_the_app_and_show_what_it_holds(
        window: Any, qtbot: Any) -> None:
    """Each switch is a Command; each shows the App's state afterwards."""
    from trcc.core.commands import LcdSnapshot

    snap = lambda: window._app.dispatch(LcdSnapshot(key=_KEY))  # noqa: E731
    page = _tab(window, qtbot, "Settings").sources
    assert page.mask_switch.isChecked() == snap().mask_visible
    page.mask_switch.click()
    assert snap().mask_visible is page.mask_switch.isChecked() is False
    page.background_switch.click()                      # off
    assert (snap().background_mode, page.background_switch.isChecked()) == (
        "transparent", False)
    page.background_switch.click()                      # on again
    assert (snap().background_mode, page.background_switch.isChecked()) == ("theme", True)


def test_brightness_and_rotation_sit_under_the_preview(window: Any, qtbot: Any) -> None:
    from trcc.core.commands import LcdSnapshot

    _tab(window, qtbot, "Local Themes")
    controls = window.panel("Devices").panel.controls
    controls.brightness.setValue(40)
    controls.brightness.sliderReleased.emit()
    controls.rotate.click()
    snap = window._app.dispatch(LcdSnapshot(key=_KEY))
    assert (snap.brightness, snap.orientation) == (40, 90)


def test_show_background_on_ends_the_media_player_and_restores_the_mode(
        window: Any) -> None:
    """The one switch every UI sends.  MUTATION CHECK: drop the media
    player's stop in ``ShowBackground``."""
    from trcc.core.commands import LcdSnapshot, SetBackgroundMode, ShowBackground
    from trcc.core.models import MediaPlayerBackground

    app = window._app
    app.settings.set_background(_KEY, MediaPlayerBackground("https://example.test/a.m3u8"))
    app.dispatch(SetBackgroundMode(key=_KEY, mode="transparent"))
    assert app.dispatch(ShowBackground(key=_KEY, shown=True)).ok
    snap = app.dispatch(LcdSnapshot(key=_KEY))
    assert (snap.display_source, snap.background_mode) == ("background", "theme")


# ── The overlay editor (Settings tab) ────────────────────────────────────


def _layout(window: Any) -> list[tuple[str, str, int, int]]:
    from trcc.core.commands import ResolveOverlay

    return [(e.type, e.text or e.metric or e.source, e.x, e.y)
            for e in window._app.dispatch(ResolveOverlay(key=_KEY)).elements]


def _add(editor: Any, label: str) -> None:
    from PySide6.QtWidgets import QPushButton

    next(b for b in editor.findChildren(QPushButton) if b.text() == f"+ {label}").click()


def test_the_overlay_editor_adds_edits_and_deletes_through_the_app(
        window: Any, qtbot: Any) -> None:
    """Add selects the new element; an edit sends ONE field; the list and form
    are the App's layout afterwards.  MUTATION CHECK: send ``x`` from
    ``_on_text`` and the text never changes."""
    editor = _tab(window, qtbot, "Settings").overlay
    _add(editor, "Text")
    assert _layout(window) == [("text", "Text", 20, 20)] and editor._picked_id()
    editor.text_box.setText("Hello HUD")
    editor.text_box.editingFinished.emit()
    editor.x_box.setValue(120)
    editor.x_box.editingFinished.emit()
    assert _layout(window) == [("text", "Hello HUD", 120, 20)]
    _add(editor, "Metric")
    editor.metric_box.setCurrentIndex(editor.metric_box.findData("cpu:temp"))
    editor.metric_box.activated.emit(editor.metric_box.currentIndex())
    _add(editor, "Date")
    assert [e[:2] for e in _layout(window)] == [("text", "Hello HUD"),
                                               ("metric", "cpu:temp"), ("clock", "date")]
    editor.list.setCurrentRow(0)
    editor.delete.click()
    assert [e[0] for e in _layout(window)] == ["metric", "clock"]
    assert [editor.list.item(i).text() for i in range(editor.list.count())] == [
        "Metric: cpu:temp", "Clock: date"]


def test_the_overlay_switch_is_the_apps(window: Any, qtbot: Any) -> None:
    from trcc.core.commands import ResolveOverlay

    editor = _tab(window, qtbot, "Settings").overlay
    assert window._app.dispatch(ResolveOverlay(key=_KEY)).enabled is True
    editor.on_switch.click()
    assert window._app.dispatch(ResolveOverlay(key=_KEY)).enabled is False
    assert editor.on_switch.isChecked() is False
