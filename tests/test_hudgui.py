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
from PySide6.QtWidgets import QLabel

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
    assert list(devices.panel.tabs.tabs) == ["Local Theme", "Cloud Masks",
                                             "Cloud Backgrounds", "Settings"]
    assert devices.panel.tabs.current == "Local Theme"
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
    return _tab(window, qtbot, "Local Theme")


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

    _tab(window, qtbot, "Local Theme")
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

    next(b for b in editor.findChildren(QPushButton) if b.text() == label).click()


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
        "Metric: cpu:temp", "Clock: Date"]


def test_the_overlay_switch_is_the_apps(window: Any, qtbot: Any) -> None:
    from trcc.core.commands import ResolveOverlay

    editor = _tab(window, qtbot, "Settings").overlay
    assert window._app.dispatch(ResolveOverlay(key=_KEY)).enabled is True
    editor.on_switch.click()
    assert window._app.dispatch(ResolveOverlay(key=_KEY)).enabled is False
    assert editor.on_switch.isChecked() is False


# ── Moving an element by hand on the preview (S3) ────────────────────────


def test_widget_to_lcd_scales_and_keeps_the_point_on_the_panel() -> None:
    from trcc.ui.presentation.preview_geometry import widget_to_lcd

    assert widget_to_lcd((100, 50), (200, 100), (320, 160)) == (160, 80)
    assert widget_to_lcd((999, -5), (200, 100), (320, 160)) == (320, 0)
    assert widget_to_lcd((10, 10), (0, 0), (320, 160)) == (0, 0)


def test_the_preview_reports_a_press_in_panel_pixels(window: Any, qtbot: Any) -> None:
    """Through the real mouse, on the real picture."""
    from PySide6.QtCore import QPoint

    _tab(window, qtbot, "Settings")
    preview = window.panel("Devices").panel.preview
    preview._lcd = (320, 320)
    label = preview._label
    with qtbot.waitSignal(preview.pressed) as pressed:
        qtbot.mousePress(label, Qt.MouseButton.LeftButton,
                         pos=QPoint(label.width() // 2, label.height() // 2))
    assert pressed.args == [160, 160]


def test_dragging_on_the_preview_moves_the_element_and_keeps_it_on(
        window: Any, qtbot: Any) -> None:
    """The gui's way: it moves as far as the pointer, never off the panel;
    the form shows where it ended.  MUTATION CHECK: send the pointer's own
    position from ``drag_to`` instead of the element's moved one."""
    page = _tab(window, qtbot, "Settings")
    editor = page.overlay
    _add(editor, "Text")                                   # at (20, 20), selected
    preview = window.panel("Devices").panel.preview
    preview.pressed.emit(30, 30)
    preview.dragged.emit(80, 60)                           # +50, +30
    assert _layout(window) == [("text", "Text", 70, 50)]
    preview.dragged.emit(2000, 2000)                       # far off the panel
    preview.released.emit()
    _type, _what, x, y = _layout(window)[0]
    assert (x, y) == (editor.x_box.maximum(), editor.y_box.maximum())
    assert (editor.x_box.value(), editor.y_box.value()) == (x, y)


def test_a_press_with_nothing_selected_picks_the_nearest(window: Any, qtbot: Any) -> None:
    editor = _tab(window, qtbot, "Settings").overlay
    _add(editor, "Text")
    _add(editor, "Time")
    editor.x_box.setValue(250)
    editor.x_box.editingFinished.emit()                    # Time at (250, 20)
    editor.list.clearSelection()
    editor.list.setCurrentRow(-1)
    window.panel("Devices").panel.preview.pressed.emit(240, 25)
    assert editor.list.currentItem().text() == "Clock: Time"


def test_a_drag_outside_the_settings_tab_moves_nothing(window: Any, qtbot: Any) -> None:
    editor = _tab(window, qtbot, "Settings").overlay
    _add(editor, "Text")
    devices = window.panel("Devices")
    devices.panel.tabs.tabs["Local Theme"].click()
    devices.panel.preview.pressed.emit(20, 20)
    devices.panel.preview.dragged.emit(120, 120)
    assert _layout(window) == [("text", "Text", 20, 20)]


def _cross(grid: Any, name: str) -> Any:
    """The ✕ on theme *name*'s tile, or None."""
    from PySide6.QtWidgets import QToolButton

    tile = next(t for tile_id, t in grid.tiles.items() if Path(tile_id).name == name)
    return tile.findChild(QToolButton)


def test_save_as_saves_what_the_panel_shows_and_asks_before_replacing(
        window: Any, qtbot: Any) -> None:
    """The gui's Save: a name, then the theme is listed as the user's own --
    with a ✕.  A name taken asks first; no means the old one stays.
    MUTATION CHECK: drop the ``overwrite=True`` re-send in ``_on_save``."""
    grid = _local_themes(window, qtbot)
    asked: list[str] = []
    grid.ask_name = lambda: "Mine"
    grid.confirm = lambda question: asked.append(question) or True
    grid._on_save()
    qtbot.waitUntil(lambda: any(Path(p).name == "Mine" for p in grid.tiles), timeout=3000)
    assert _cross(grid, "Mine") is not None
    assert grid.note.text() == ""
    grid._on_save()                         # the name is taken now
    assert asked == ["A theme named 'Mine' already exists.  Replace it?"]
    assert grid.note.text() == ""


def test_saying_no_to_replacing_keeps_the_old_one_and_says_why(
        window: Any, qtbot: Any) -> None:
    grid = _local_themes(window, qtbot)
    grid.ask_name = lambda: "Mine"
    grid.confirm = lambda question: False
    grid._on_save()
    qtbot.waitUntil(lambda: any(Path(p).name == "Mine" for p in grid.tiles), timeout=3000)
    grid._on_save()
    assert grid.note.text() != ""


def test_only_the_users_own_themes_carry_a_cross_and_it_asks_first(
        window: Any, qtbot: Any) -> None:
    """A shipped theme has no ✕; the user's own has one, and a no keeps it.
    MUTATION CHECK: make every tile ``removable`` in ``LocalThemes._tiles``."""
    _themes(window._app, "Shipped")
    grid = _local_themes(window, qtbot)
    grid.ask_name = lambda: "Mine"
    grid._on_save()
    qtbot.waitUntil(lambda: any(Path(p).name == "Mine" for p in grid.tiles), timeout=3000)
    assert _cross(grid, "Shipped") is None
    grid.confirm = lambda question: False
    _cross(grid, "Mine").click()
    assert any(Path(p).name == "Mine" for p in grid.tiles)
    grid.confirm = lambda question: True
    _cross(grid, "Mine").click()
    qtbot.waitUntil(lambda: not any(Path(p).name == "Mine" for p in grid.tiles),
                    timeout=3000)


def test_export_writes_what_the_panel_shows_and_import_brings_it_back(
        window: Any, qtbot: Any, tmp_path: Path) -> None:
    """Export what is shown to a ``.tr``; Import lists it as the user's own.
    MUTATION CHECK: dispatch nothing in ``_on_import``."""
    from trcc.core.commands import LoadTheme

    shown, = _themes(window._app, "Pictured")
    assert window._app.dispatch(LoadTheme(key=_KEY, path=shown)).ok
    grid = _local_themes(window, qtbot)
    archive = tmp_path / "out" / "Shown.tr"
    archive.parent.mkdir()
    grid.choose_archive = lambda saving: str(archive)
    grid._on_export()
    assert archive.is_file()
    grid._on_import()
    qtbot.waitUntil(lambda: any(Path(p).name == "Shown" for p in grid.tiles), timeout=3000)
    assert _cross(grid, "Shown") is not None


# ── Sensors ──────────────────────────────────────────────────────────────


def _sensors(window: Any, qtbot: Any) -> Any:
    window.bar.buttons["Sensors"].click()
    view = window.panel("Sensors")
    qtbot.waitUntil(view.isVisible)
    return view


def test_sensors_lists_every_reading_by_group_and_leaves_the_clock_out(
        window: Any, qtbot: Any) -> None:
    """A card per source, the known ones first; every reading of the App's
    on one -- the time and date on none.  MUTATION CHECK: drop the
    ``_NOT_SENSORS`` filter in ``grouped``."""
    from trcc.core.commands import ReadSensors

    view = _sensors(window, qtbot)
    assert list(view.cards)[:2] == ["CPU", "GPU"]
    ids = {r.sensor_id for r in window._app.dispatch(ReadSensors()).readings}
    assert set(view.values) == {i for i in ids if not i.startswith(("time:", "date:"))}


def test_sensors_follow_the_broadcast_in_the_users_unit(window: Any, qtbot: Any) -> None:
    """The values are the broadcast's, the temperature unit its unit.
    MUTATION CHECK: drop the ``sensors_updated`` connection."""
    from trcc.core.events import SensorsUpdated
    from trcc.core.models import HardwareMetrics

    view = _sensors(window, qtbot)
    window._bus.sensors_updated.emit(SensorsUpdated(
        readings={"cpu:temp": 150.0, "cpu:usage": 7.5}, temp_unit="F",
        reading_count=2, metrics=HardwareMetrics()))
    qtbot.waitUntil(lambda: view.values["cpu:temp"].text() == "150°F", timeout=2000)
    assert view.values["cpu:usage"].text() == "8%"


def test_a_reading_not_seen_before_lists_the_sensors_again(window: Any, qtbot: Any) -> None:
    from trcc.core.events import SensorsUpdated
    from trcc.core.models import HardwareMetrics

    view = _sensors(window, qtbot)
    before = view.cards["CPU"]
    view._on_sensors(SensorsUpdated(readings={"module:new": 1.0}, temp_unit="C",
                                    reading_count=1, metrics=HardwareMetrics()))
    assert view.cards["CPU"] is not before


def test_sensors_write_a_value_as_the_guis_picker_and_follow_the_unit_back(
        window: Any, qtbot: Any) -> None:
    """One format for both windows (``sensor_display.format_sensor_value``);
    a temperature read under °F reads °C again when the unit goes back.
    MUTATION CHECK: keep the reading's own unit in ``_card``."""
    from trcc.core.commands import SetTempUnit
    from trcc.core.events import SensorsUpdated
    from trcc.core.models import HardwareMetrics
    from trcc.ui.presentation.sensor_display import format_sensor_value

    assert window._app.dispatch(SetTempUnit(unit="F")).ok
    view = _sensors(window, qtbot)
    assert window._app.dispatch(SetTempUnit(unit="C")).ok
    view._on_sensors(SensorsUpdated(readings={"cpu:temp": 41.0, "memory:used": 8192.0},
                                    temp_unit="C", reading_count=2,
                                    metrics=HardwareMetrics()))
    assert view.values["cpu:temp"].text() == "41°C"
    assert view.values["memory:used"].text() == format_sensor_value(8192.0, "MB")


# ── Modules ──────────────────────────────────────────────────────────────


def test_modules_is_the_shared_list_and_follows_a_switch_from_another_ui(
        window: Any, qtbot: Any) -> None:
    """The gui's and qtgui's list (``qt_modules.ModulesView``): every module
    the App has, each with its switch -- and a module switched in another UI
    turns here too.  MUTATION CHECK: drop the ``app_settings_changed``
    connection in ``hudgui.modules.modules_panel``."""
    from trcc.core.commands import ListModules, SetModuleEnabled
    from trcc.ui.qt_modules import ModulesView

    window.bar.buttons["Modules"].click()
    view = window.panel("Modules")
    assert isinstance(view, ModulesView)
    listed = window._app.dispatch(ListModules()).modules
    qtbot.waitUntil(lambda: set(view.switches) == {m.key for m in listed}, timeout=3000)
    module = next(m for m in listed if not m.problem)
    assert window._app.dispatch(SetModuleEnabled(key=module.key,
                                                 enabled=not module.enabled)).ok
    qtbot.waitUntil(lambda: view.switches[module.key].isChecked() is not module.enabled,
                    timeout=3000)


# ── Control Center ───────────────────────────────────────────────────────


def _control_center(window: Any) -> Any:
    window.bar.buttons["Control Center"].click()
    return window.panel("Control Center")


def test_control_center_changes_the_apps_settings(window: Any) -> None:
    """Each control sends its Command, and the App holds the change.
    MUTATION CHECK: send ``SetTempUnit(unit="C")`` from ``_on_unit``."""
    from trcc.core.commands import ControlCenterSnapshot, GetAutostartStatus

    view = _control_center(window)
    view.unit_box.setCurrentIndex(view.unit_box.findData("F"))
    view.hdd_box.setChecked(True)
    view.refresh_box.setValue(7)
    view.refresh_box.editingFinished.emit()
    view.autostart_box.setChecked(True)
    view.language_box.setCurrentIndex(view.language_box.findData("de"))
    held = window._app.dispatch(ControlCenterSnapshot())
    assert (held.temp_unit, held.hdd_enabled, held.refresh_interval_s, held.language) == (
        "F", True, 7.0, "de")
    assert window._app.dispatch(GetAutostartStatus()).enabled
    assert view.note.text() == ""


def test_control_center_follows_a_change_from_another_ui(window: Any, qtbot: Any) -> None:
    """MUTATION CHECK: drop the ``app_settings_changed`` connection."""
    from trcc.core.commands import SetHddEnabled, SetRefreshInterval, SetTempUnit

    view = _control_center(window)
    for command in (SetTempUnit(unit="F"), SetHddEnabled(enabled=True),
                    SetRefreshInterval(seconds=9.0)):
        assert window._app.dispatch(command).ok      # e.g. the CLI
    qtbot.waitUntil(lambda: (view.unit_box.currentData(), view.hdd_box.isChecked(),
                             view.refresh_box.value()) == ("F", True, 9), timeout=2000)


def test_showing_the_settings_sends_nothing(window: Any, monkeypatch: Any) -> None:
    """Putting the App's values on the controls must not echo them back.
    MUTATION CHECK: drop the signal blockers in ``show_state``."""
    from trcc.core.commands import SetHddEnabled, SetTempUnit

    view = _control_center(window)
    sent: list[Any] = []
    monkeypatch.setattr(view, "_send", sent.append)
    assert window._app.dispatch(SetTempUnit(unit="F")).ok
    assert window._app.dispatch(SetHddEnabled(enabled=True)).ok
    view.show_state()                       # new values on every control
    assert (view.unit_box.currentData(), view.hdd_box.isChecked()) == ("F", True)
    assert sent == []


# ── About ────────────────────────────────────────────────────────────────


def _about(window: Any) -> Any:
    window.bar.buttons["About"].click()
    return window.panel("About")


def test_about_shows_the_apps_word_on_updates_and_how_to_upgrade(
        window: Any, qtbot: Any) -> None:
    """The App's answer, as every window hears it; "How to upgrade" only
    when a newer TRCC is out, and it says the command, never runs one.
    MUTATION CHECK: drop the ``update_checked`` connection."""
    from trcc.core.events import UpdateChecked

    view = _about(window)
    assert view.upgrade_button.isHidden()
    window._bus.update_checked.emit(UpdateChecked(ok=True, latest_version="99.0.0",
                                                  update_available=True))
    qtbot.waitUntil(lambda: view.update_line.text() == "Version 99.0.0 is out",
                    timeout=2000)
    assert not view.upgrade_button.isHidden()
    told: list[tuple[str, str]] = []
    view.tell = lambda title, message: told.append((title, message))
    view.upgrade_button.click()
    assert told and told[0][0] == "Upgrade TRCC" and told[0][1]


def test_about_saves_the_diagnostic_report(window: Any, tmp_path: Path) -> None:
    """MUTATION CHECK: drop ``output_path`` from the GenerateDebugReport."""
    view = _about(window)
    target = tmp_path / "report.txt"
    view.choose_report_file = lambda: str(target)
    told: list[str] = []
    view.tell = lambda title, message: told.append(message)
    view._on_report()
    assert target.is_file() and told == [f"Saved to {target}"]


def test_about_lists_every_credit(window: Any) -> None:
    from PySide6.QtWidgets import QLabel

    from trcc.core.credits import GROUPS

    view = _about(window)
    text = " ".join(label.text() for label in view.findChildren(QLabel))
    assert all(credit.name in text for _, credits in GROUPS for credit in credits)


# ── The language ─────────────────────────────────────────────────────────


def test_a_language_set_in_any_ui_retranslates_the_window(window: Any, qtbot: Any) -> None:
    """The bar, a panel already made, the open panel's title, and a panel
    made later -- all in the App's language, set here by another UI.
    MUTATION CHECK: drop the ``LanguageChanged`` branch in ``_on_app_settings``."""
    from trcc.core.commands import SetLanguage

    window.bar.buttons["Control Center"].click()
    center = window.panel("Control Center")
    assert window._app.dispatch(SetLanguage(language="de")).ok     # e.g. the gui
    qtbot.waitUntil(lambda: window.bar.buttons["Devices"].text() == "Geräte", timeout=2000)
    assert window.drop.title == "Kontrollzentrum"
    labels = {label.text() for label in center.findChildren(QLabel)}
    assert "Grafikkarte" in labels
    window.bar.buttons["Sensors"].click()
    sensors = window.panel("Sensors")
    sensors.refresh()                                   # rebuilt: still German
    assert any(label.text() == "Lüfter" for label in sensors.findChildren(QLabel)
               if label.isVisibleTo(sensors))


def test_every_word_hudgui_says_is_in_every_language() -> None:
    """A label hudgui ``say``s, and every word it translates at the moment,
    is a key of the shared table -- so no language shows it in English."""
    from trcc.core.i18n import TRANSLATIONS
    from trcc.ui.hudgui.chrome import DEVICE_SETTINGS_TAB, DEVICE_TABS, MENU
    from trcc.ui.hudgui.overlay import ADDS

    english = TRANSLATIONS["en"]
    words = {*MENU, *DEVICE_TABS, DEVICE_SETTINGS_TAB, *(a[0] for a in ADDS)}
    assert words <= english.keys()


def test_tiles_flow_to_the_visible_width_not_the_tabs(window: Any, qtbot: Any) -> None:
    """His run (2026-11-05): the tiles were laid out while the side panel was
    hidden -- one to a row -- and the VIEWPORT took its real width after the
    tab's own resize, so nothing re-flowed: themes in a single column on a
    1920-wide bar (reproduced on Xvfb: viewport 1158 wide, 1 column, 8 fit).
    The offscreen platform resizes in an order that hides it, so the test
    resizes the viewport alone.  MUTATION CHECK: drop the viewport's event
    filter in ``TileGrid``."""
    _themes(window._app, "One", "Two", "Three", "Four", "Five", "Six")
    grid = _local_themes(window, qtbot)
    viewport = grid._scroll.viewport()
    viewport.resize(130, viewport.height())
    assert grid._columns == 1
    viewport.resize(1000, viewport.height())
    assert grid._columns == grid._fit() == (1000 + 10) // 130
