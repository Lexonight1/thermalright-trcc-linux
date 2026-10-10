"""The gui shows what the App holds, on open and after any UI changes it.

With one App behind every UI, a CLI ``trcc brightness`` while the gui is open
must move the gui's brightness too, and a gui opened after it must show it.
Measured before this was wired: the gui followed 2 of 9 displayed settings
(theme, slideshow), and on open showed the mask as visible at (0, 0) and no
theme highlighted, whatever the App held.

Drives the real window offscreen against the mock platform, the scaffold
``test_gui_video_cut_persistence`` uses.  A change is dispatched on the App,
which is exactly what another UI's Command does; the window only ever hears
about it through the bus.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import renderable_theme
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    ControlCenterSnapshot,
    DeleteTheme,
    DisableAutostart,
    EnableOverlay,
    LcdSnapshot,
    LoadTheme,
    SaveTheme,
    SetBrightness,
    SetGameMode,
    SetGpuDevice,
    SetHddEnabled,
    SetLanguage,
    SetMaskPosition,
    SetMaskVisible,
    SetOrientation,
    SetRefreshInterval,
    SetSensorDashboard,
    SetSplitMode,
    SetTempUnit,
)
from trcc.core.commands._base import Query
from trcc.core.i18n import tr
from trcc.core.models import PanelConfig

_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"
_OTHER_SPEC = {"vid": "87ad", "pid": "70db", "fbl": 100}
_OTHER_KEY = "87ad:70db"


def _translated(window: Any, lang: str) -> bool:
    """Every translatable label reads in *lang*."""
    return all(label.text() == tr(key, lang)
               for label, key in window._i18n_labels if key is not None)


def _local_names(window: Any) -> set[str]:
    """The names the gui's local theme grid holds."""
    return {t.name for t in window.uc_theme_local._all_themes}


# setting: (what another UI sends, how the gui shows it, what it must show)
_ROWS: dict[str, tuple[Callable[[], Any], Callable[[Any], Any], Any]] = {
    "brightness": (lambda: SetBrightness(key=_KEY, percent=37),
                   lambda w: (w._handlers[_KEY]._pm.brightness_level,
                              w.uc_brightness.value), (37, 37)),
    "orientation": (lambda: SetOrientation(key=_KEY, degrees=90),
                    lambda w: w.rotation_combo.currentIndex() * 90, 90),
    "split mode": (lambda: SetSplitMode(key=_KEY, mode=3),
                   lambda w: w._handlers[_KEY]._pm.split_mode, 3),
    "game mode": (lambda: SetGameMode(key=_KEY, enabled=True, threshold=60),
                  lambda w: (w.uc_theme_local.game.enabled,
                             w.uc_theme_local.game.cpu_input.text()),
                  (True, "60")),
    "overlay": (lambda: EnableOverlay(key=_KEY, enabled=False),
                lambda w: (w._handlers[_KEY]._pm.state.overlay_enabled,
                           w.uc_theme_setting.overlay_grid._toggle_btn.isChecked()),
                (False, False)),
    "mask visible": (lambda: SetMaskVisible(key=_KEY, visible=False),
                     lambda w: w.uc_theme_setting.mask_panel._mask_visible, False),
    "mask position": (lambda: SetMaskPosition(key=_KEY, x=40, y=60),
                      lambda w: (w.uc_theme_setting.mask_panel.entry_x.text(),
                                 w.uc_theme_setting.mask_panel.entry_y.text()),
                      ("40", "60")),
    "temp unit": (lambda: SetTempUnit(unit="F"),
                  lambda w: (w.uc_about.fahrenheit_btn.isChecked(),
                             w.uc_about.celsius_btn.isChecked(),
                             w.uc_system_info._temp_unit),
                  (True, False, 1)),
    "hdd": (lambda: SetHddEnabled(enabled=False),     # on by default
            lambda w: (w.uc_about.hdd_btn.isChecked(), w.uc_about.read_hdd),
            (False, False)),
    "refresh interval": (lambda: SetRefreshInterval(seconds=7),
                         lambda w: (w.uc_about.refresh_input.text(),
                                    w.uc_about.refresh_interval),
                         ("7", 7)),
    # Off: opening the gui ENABLES autostart on a first launch
    # (``ensure_autostart``), so "on" is already what it shows.
    "autostart": (lambda: DisableAutostart(),
                  lambda w: (w.uc_about.startup_btn.isChecked(),
                             w.uc_about._autostart), (False, False)),
    "sensor dashboard": (lambda: SetSensorDashboard(panels=(PanelConfig.custom("Mine"),)),
                         lambda w: [c.name for c in w.uc_system_info._dashboard],
                         ["Mine"]),
    "saved theme": (lambda: SaveTheme(key=_KEY, name="Mine"),
                    lambda w: "Mine" in _local_names(w), True),
    "language": (lambda: SetLanguage(language="de"),
                 lambda w: (w._lang_combo.currentData(), _translated(w, "de")),
                 ("de", True)),
}


@pytest.fixture
def app(tmp_path: Path) -> App:
    """A connected panel showing a real theme from its own library."""
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    themes = app.platform.paths().theme_dir(320, 320)
    for name in ("Theme1", "Theme2"):
        renderable_theme(themes, name)
    assert app.dispatch(LoadTheme(key=_KEY, path=themes / "Theme1")).ok
    return app


def _open(app: App, qtbot: Any, key: str = _KEY) -> Any:
    from trcc.ui.gui.trcc_app import TRCCApp

    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: key in win._handlers)
    win._activate_device(key)
    qtbot.waitUntil(lambda: win._handlers[key]._pm.ui_active)
    return win


@pytest.fixture
def window(app: App, qtbot: Any) -> Iterator[Any]:
    win = _open(app, qtbot)
    yield win
    win.close()


def _commands_sent(app: App, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Command (not Query) dispatched on *app* from now on."""
    sent: list[str] = []
    dispatch = app.dispatch

    def recording(cmd: Any) -> Any:
        if not isinstance(cmd, Query):
            sent.append(type(cmd).__name__)
        return dispatch(cmd)

    monkeypatch.setattr(app, "dispatch", recording)
    return sent


@pytest.mark.parametrize("setting", list(_ROWS))
def test_the_gui_shows_a_change_another_ui_made(
    window: Any, qtbot: Any, setting: str,
) -> None:
    send, shown, expected = _ROWS[setting]
    assert shown(window) != expected, "the row proves nothing: already showing it"

    window._app.dispatch(send())

    qtbot.waitUntil(lambda: shown(window) == expected, timeout=3000)


@pytest.mark.parametrize("setting", list(_ROWS))
def test_opening_the_gui_shows_what_the_app_holds(
    app: App, qtbot: Any, setting: str,
) -> None:
    """The value is in the App before the window exists: another UI set it
    earlier, or the App restored it."""
    send, shown, expected = _ROWS[setting]
    assert app.dispatch(send()).ok

    win = _open(app, qtbot)

    assert shown(win) == expected
    win.close()


@pytest.mark.parametrize("setting", list(_ROWS))
def test_following_a_change_sends_nothing_back(
    window: Any, qtbot: Any, setting: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Showing a change must not re-send it: an echo is an event to every
    other UI, and a stale overwrite if one of them changed it meanwhile."""
    send, shown, expected = _ROWS[setting]
    window._app.dispatch(send())
    # Recording starts AFTER the send returns: the App's own observers
    # dispatch (a re-render) inside it, and the window's follow is queued,
    # so everything recorded from here was sent by the window.
    sent = _commands_sent(window._app, monkeypatch)

    qtbot.waitUntil(lambda: shown(window) == expected, timeout=3000)
    qtbot.wait(200)            # room for an echo to arrive, had there been one

    assert sent == []


def test_picking_a_language_in_the_gui_reaches_the_app_and_the_labels(
    window: Any, qtbot: Any,
) -> None:
    """The picker no longer relabels on its own; the labels follow the App."""
    window._lang_combo.setCurrentIndex(window._lang_combo.findData("de"))

    assert window._app.dispatch(ControlCenterSnapshot()).language == "de"
    qtbot.waitUntil(lambda: _translated(window, "de"), timeout=3000)


def test_the_theme_on_the_panel_is_highlighted_and_stays_highlighted(
    window: Any, qtbot: Any,
) -> None:
    from trcc.ui.gui.base import BaseThumbnail

    browser = window.uc_theme_local
    themes = window._app.platform.paths().theme_dir(320, 320)

    def highlighted() -> list[Path]:
        """The tiles DRAWN selected — a rebuild keeps the browser's
        ``selected_item`` but draws every new tile unselected."""
        return [Path(tile.item_info.path) for tile in browser.item_widgets
                if isinstance(tile, BaseThumbnail) and tile.selected]

    assert highlighted() == [themes / "Theme1"]

    window._app.dispatch(LoadTheme(key=_KEY, path=themes / "Theme2"))   # another UI
    qtbot.waitUntil(lambda: highlighted() == [themes / "Theme2"], timeout=3000)

    browser._render_filtered()            # a filter click rebuilds every tile
    assert highlighted() == [themes / "Theme2"]


def test_a_typed_position_is_not_rewritten_under_the_cursor(
    window: Any, qtbot: Any,
) -> None:
    """Emptying the X field sends (0, y); the App echoes (0, y) back, and the
    field must stay empty rather than turn into "0" while the user types."""
    panel = window.uc_theme_setting.mask_panel
    panel.entry_y.setText("60")
    panel.entry_x.setText("")
    qtbot.waitUntil(lambda: window._app.dispatch(
        LcdSnapshot(key=_KEY)).mask_position == (0, 60))
    qtbot.wait(200)

    assert panel.entry_x.text() == ""


def test_the_gpu_picker_follows_another_ui(
    tmp_path: Path, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The picker exists only with two GPUs or more."""
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    # ``provides`` because the sensors' ``unsupported()`` asks every GPU what
    # it can read.  It used to be answered at App construction, before this
    # patch, so a fake without it passed; it is asked on first need now.
    gpus = [SimpleNamespace(key="nvidia:0", name="RTX", is_discrete=True,
                            provides=lambda quantity: True),
            SimpleNamespace(key="amd:0", name="RX", is_discrete=True,
                            provides=lambda quantity: True)]
    monkeypatch.setattr(type(app.platform.sensors()), "gpus", lambda self: gpus)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    win = _open(app, qtbot)
    picker = win.uc_about._gpu_combo
    assert picker is not None and picker.currentData() != "amd:0"
    sent = _commands_sent(app, monkeypatch)

    app.dispatch(SetGpuDevice(gpu_key="amd:0"))   # another UI

    qtbot.waitUntil(lambda: picker.currentData() == "amd:0", timeout=3000)
    assert sent == ["SetGpuDevice"]
    win.close()


def _two_panels(tmp_path: Path, qtbot: Any) -> Any:
    app = App(MockPlatform([_SPEC, _OTHER_SPEC], tmp_path), renderer=QtRenderer())
    for key in (_KEY, _OTHER_KEY):
        assert app.dispatch(ConnectDevice(key=key)).ok
    win = _open(app, qtbot, _KEY)
    qtbot.waitUntil(lambda: len(win._handlers) == 2)
    return win


def test_a_change_to_the_panel_not_shown_leaves_the_shared_widgets_alone(
    tmp_path: Path, qtbot: Any,
) -> None:
    """Every LCD shares one set of widgets; only the selected panel's handler
    may write them, or rotating panel B would show on panel A's controls."""
    win = _two_panels(tmp_path, qtbot)

    win._app.dispatch(SetOrientation(key=_OTHER_KEY, degrees=90))
    qtbot.wait(300)

    assert win.rotation_combo.currentIndex() == 0
    win.close()


def test_the_brightness_slider_shows_the_selected_panels_level(
    tmp_path: Path, qtbot: Any,
) -> None:
    """Switching back to a panel used to leave the other panel's level on the
    control."""
    win = _two_panels(tmp_path, qtbot)
    win._app.dispatch(SetBrightness(key=_KEY, percent=25))
    win._app.dispatch(SetBrightness(key=_OTHER_KEY, percent=63))

    for key, level in ((_OTHER_KEY, 63), (_KEY, 25), (_OTHER_KEY, 63)):
        win._activate_device(key)
        qtbot.waitUntil(lambda level=level: win.uc_brightness.value == level,
                        timeout=3000)
    win.close()


def test_releasing_the_slider_sets_the_shown_panel_s_brightness(
    window: Any, qtbot: Any,
) -> None:
    """Any of 101 values, as the C#'s slider -- the old button reached three.

    MUTATION CHECK -- MEASURED 2026-10-02: disconnect ``changed`` → fails.
    """
    window.uc_brightness.changed.emit(42)

    qtbot.waitUntil(lambda: window._app.dispatch(LcdSnapshot(key=_KEY)).brightness
                    == 42, timeout=3000)


def test_the_bottom_row_sits_where_formcztv_2_1_8_puts_it(window: Any) -> None:
    """Slider, theme name and save at the C#'s own coordinates
    (FormCZTV.cs:8884, :8620 and the buttonBCZT setup), so they cannot
    overlap -- the slider needs the 278-344 span the name box used to hold.
    No export / import here: 2.1.8 moved them off the form (y=880), onto the
    local-theme panel (see the test below).
    """
    def rect(w: Any) -> tuple[int, int, int, int]:
        g = w.geometry()
        return (g.x(), g.y(), g.width(), g.height())

    assert rect(window.uc_brightness) == (164, 680, 180, 24)
    assert rect(window.theme_name_input) == (378, 684, 102, 16)
    assert rect(window.save_btn) == (482, 680, 24, 24)
    assert not hasattr(window, "export_btn") and not hasattr(window, "import_btn")


def test_the_local_theme_panel_exports_and_imports_like_formcztv(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2.1.8 hid FormCZTV's export / import and kept them on UCThemeLocal at
    (441, 28) / (482, 28) (UCThemeLocal.cs:889, :903).  d9093b61 removed the
    gui's outright, reading the hidden pair as the whole story.  Export is
    what the panel shows, named after the theme-name box; a name typed
    without an extension gets the chosen format's; import shows the theme.
    """
    from PySide6.QtWidgets import QFileDialog

    local = window.uc_theme_local

    def rect(w: Any) -> tuple[int, int, int, int]:
        g = w.geometry()
        return (g.x(), g.y(), g.width(), g.height())

    assert rect(local.export_btn) == (441, 28, 40, 18)
    assert rect(local.import_btn) == (482, 28, 40, 18)
    sent = _commands_sent(window._app, monkeypatch)
    offered: list[str] = []

    def save_as(_parent: Any, _title: str, default: str, _filters: str):
        offered.append(default)
        return str(tmp_path / "Party"), "Theme files (*.tr)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_as)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *_a: (str(tmp_path / "Party.tr"), ""))
    window.theme_name_input.setText("Party")

    local.export_btn.click()
    local.import_btn.click()

    assert offered == ["Party.tr"]
    # The third is the App's, not the gui's: ImportTheme shows the theme
    # through LoadTheme on the bus.
    assert sent == ["ExportCurrentTheme", "ImportTheme", "LoadTheme"]
    assert window._app.active_themes[_KEY].path.name == "Party"


def test_the_game_mode_controls_sit_where_ucthemelocal_puts_them(
    window: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """buttonGame (228, 28) 40x18, textBoxCPU (336, 29) 24x16, two digits
    (UCThemeLocal.cs:910-935), and the ``CPU(%)>`` the C# prints beside it.
    Each control sends only its own half -- the C#'s two handlers -- so a
    click cannot overwrite a threshold another UI just set."""
    local = window.uc_theme_local
    game = local.game

    def rect(w: Any) -> tuple[int, int, int, int]:
        g = w.geometry()
        return (g.x(), g.y(), g.width(), g.height())

    assert rect(game.button) == (228, 28, 40, 18)
    assert rect(game.cpu_input) == (336, 29, 24, 16)
    assert game.cpu_input.maxLength() == 2
    assert any(label.text() == "CPU(%)>" and label.parent() is local
               for label, _key in window._i18n_labels)
    sent: list[Any] = []
    dispatch = window._app.dispatch
    monkeypatch.setattr(window._app, "dispatch",
                        lambda cmd: (sent.append(cmd), dispatch(cmd))[1])

    game.button.click()
    qtbot.waitUntil(lambda: game.enabled, timeout=3000)
    game.cpu_input.setText("60")
    game.cpu_input.editingFinished.emit()
    qtbot.waitUntil(lambda: game.threshold == 60, timeout=3000)

    asked = [c for c in sent if isinstance(c, SetGameMode)]
    assert asked == [SetGameMode(key=_KEY, enabled=True),
                     SetGameMode(key=_KEY, threshold=60)]
    s = window._app.settings.for_device(_KEY)
    assert (s.game_enabled, s.game_threshold) == (True, 60)


def test_a_panel_with_no_mask_position_does_not_show_the_previous_panels(
    tmp_path: Path, qtbot: Any,
) -> None:
    """The mask fields are shared.  A panel whose position is the default
    shows the default the render draws at, (0, 0), not what the last panel
    left there."""
    win = _two_panels(tmp_path, qtbot)
    assert win._app.dispatch(SetMaskPosition(key=_KEY, x=40, y=60)).ok
    panel = win.uc_theme_setting.mask_panel
    qtbot.waitUntil(lambda: panel.entry_x.text() == "40", timeout=3000)

    win._activate_device(_OTHER_KEY)

    qtbot.waitUntil(lambda: (panel.entry_x.text(), panel.entry_y.text())
                    == ("0", "0"), timeout=3000)
    win.close()


def test_a_theme_deleted_elsewhere_leaves_the_gui_s_grid(
    window: Any, qtbot: Any,
) -> None:
    """``DeleteTheme`` published nothing, so a theme deleted from the CLI or
    the other skin stayed in the grid until a restart -- clickable, and gone.

    MUTATION CHECK -- MEASURED 2026-10-02: drop ``ThemeDeleted`` from
    BusBridge's table → fails; drop the gui's ``themes_changed`` hookup →
    this AND the "saved theme" row fail.
    """
    app = window._app
    saved = app.dispatch(SaveTheme(key=_KEY, name="Doomed"))
    assert saved.ok, saved.message
    qtbot.waitUntil(lambda: "Doomed" in _local_names(window), timeout=3000)

    assert app.dispatch(DeleteTheme(path=Path(saved.theme_path))).ok

    qtbot.waitUntil(lambda: "Doomed" not in _local_names(window), timeout=3000)


def test_the_gui_follows_a_disk_chosen_elsewhere(window: Any, qtbot: Any) -> None:
    """``SetDiskDevice`` published nothing; the gui's disk picker showed what
    IT last picked.  Two drives, chosen from another UI.

    MUTATION CHECK -- MEASURED 2026-10-02: route DiskDeviceChanged nowhere in
    ``_on_bus_app_settings_changed`` → fails.
    """
    from trcc.adapters.sensors.aggregator import BaselineSensors
    from trcc.core.commands import SetDiskDevice

    from .conftest import FakeCpu, FakeMemory
    from .test_sensors import FakeDisk

    window._app.platform._sensors = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        disks=[FakeDisk("nvme0", 41.0), FakeDisk("nvme1", 58.0)])
    picker = window.uc_led_control._disk_selector

    window._app.dispatch(SetDiskDevice(disk_key="nvme1"))

    qtbot.waitUntil(lambda: picker.currentData() == "nvme1", timeout=3000)
    assert [picker.itemData(i) for i in range(picker.count())] == ["nvme0", "nvme1"]


# ── Two panels: the one on screen blinks ─────────────────────────────────────

_A, _B = "0402:3922", "87ad:70db"


def _two_panels_viewing_a(tmp_path: Path, qtbot: Any) -> tuple[App, Any]:
    """Two connected panels, the window showing A."""
    from trcc.core.commands import ConnectDevice as _Connect

    app = App(MockPlatform([
        {"type": "lcd", "name": "FW", "vid": "0402", "pid": "3922", "fbl": 100},
        {"type": "lcd", "name": "HR10", "vid": "87ad", "pid": "70db", "pm": 72,
         "resolution": "480x480"}], tmp_path), renderer=QtRenderer())
    assert app.dispatch(_Connect(key=_A)).ok and app.dispatch(_Connect(key=_B)).ok
    win = _open(app, qtbot, key=_B)
    win._activate_device(_A)
    qtbot.waitUntil(lambda: win._handlers[_A]._pm.ui_active)
    return app, win


def _blink_out(app: App, win: Any, qtbot: Any) -> None:
    """A VM takes A: its writes fail and it is announced lost."""
    import errno

    transport = app.devices[_A]._transport

    def gone(*_a: object, **_k: object) -> bool:
        raise OSError(errno.ENODEV, "No such device")

    transport.send_cdb = gone                  # type: ignore[attr-defined]
    transport.open = lambda: False             # type: ignore[attr-defined]
    app.send(_A, b"\x00" * 204800)
    qtbot.waitUntil(lambda: _A not in win._handlers, timeout=5000)


def _comes_back(app: App, win: Any, qtbot: Any) -> None:
    from trcc.core.events import DeviceAttached

    app._on_device_attached(DeviceAttached(key=_A, vid=0x0402, pid=0x3922))
    qtbot.waitUntil(lambda: _A in win._handlers, timeout=5000)
    qtbot.wait(200)


def test_the_panel_on_screen_comes_back_on_screen(tmp_path: Path, qtbot: Any) -> None:
    """Away, the view moves to the other panel; back, it returns.

    MUTATION CHECK: drop the re-activation from ``_on_bus_device_connected``
    and the user is left looking at the other panel after every blink.
    """
    app, win = _two_panels_viewing_a(tmp_path, qtbot)

    _blink_out(app, win, qtbot)
    assert win._active_key == _B
    _comes_back(app, win, qtbot)

    assert win._active_key == _A
    assert win._handlers[_A]._pm.ui_active and not win._handlers[_B]._pm.ui_active
    assert win.uc_device.selected_device["path"] == _A, "the sidebar follows"
    win.close()


def test_a_panel_picked_meanwhile_keeps_the_screen(tmp_path: Path, qtbot: Any) -> None:
    """The user chose while it was away; its return must not override that.

    MUTATION CHECK: drop the reset from ``_on_device_picked`` and the returning
    panel takes the screen from the one the user picked.
    """
    app, win = _two_panels_viewing_a(tmp_path, qtbot)

    _blink_out(app, win, qtbot)
    win.uc_device._on_device_clicked(                      # a real press
        next(d for d in win.uc_device.devices if d["path"] == _B))
    _comes_back(app, win, qtbot)

    assert win._active_key == _B
    win.close()


def test_a_frame_from_the_socket_is_shown_without_asking_for_another(
    window: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observed, not re-rendered: the picture on FrameSent IS the preview.

    Published on the App's bus exactly as a daemon client's proxy republishes
    it -- a JPEG and no live surface -- so it goes through the window's real
    BusBridge, which every Qt window shares.

    MUTATION CHECK: drop the decode in ``BusBridge``'s frame forwarder and
    the window asks the App for the frame again -- CurrentFrame, then a fresh
    render.
    """
    from PySide6.QtGui import QColor, QImage

    from trcc.core.events import FrameSent

    # Frames reach a window only while its preview is on screen.
    window.show()
    qtbot.waitUntil(lambda: window._bus._frames_on, timeout=3000)
    qtbot.wait(50)       # let the queued "frames resumed" redraw run first
    frame = QImage(320, 320, QImage.Format.Format_RGB888)
    frame.fill(QColor(0, 64, 128))
    jpeg = QtRenderer().encode_jpeg(frame, 95)
    shown: list[Any] = []
    monkeypatch.setattr(window._handlers[_KEY], "handle_frame", shown.append)
    asked: list[str] = []
    dispatch = window._app.dispatch

    def recording(cmd: Any) -> Any:
        asked.append(type(cmd).__name__)
        return dispatch(cmd)

    monkeypatch.setattr(window._app, "dispatch", recording)

    window._app.events.publish(FrameSent(key=_KEY, bytes_sent=1, image=jpeg))
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert [(i.width(), i.height()) for i in shown] == [(320, 320)]
    assert asked == []


def test_the_window_takes_frames_only_while_a_preview_shows(
    window: Any, qtbot: Any,
) -> None:
    """Legacy's ``is_app_visible`` gate, restored: a window hidden in the tray
    or on its settings page takes no frames, so the App encodes none for it.

    MUTATION CHECK: delete ``follow_previews`` from ``TRCCApp``."""
    assert not window._bus._frames_on, "frames before the window is shown"
    window.show()
    qtbot.waitUntil(lambda: window._bus._frames_on, timeout=3000)
    window._show_view("about")                   # the settings page
    qtbot.waitUntil(lambda: not window._bus._frames_on, timeout=3000)
    window._show_view("form")
    qtbot.waitUntil(lambda: window._bus._frames_on, timeout=3000)
    window.hide()                                # closed to the tray
    qtbot.waitUntil(lambda: not window._bus._frames_on, timeout=3000)


def test_the_new_window_takes_frames_only_while_shown(
    make_window: Any, fake_platform: Any, qtbot: Any,
) -> None:
    """qtgui's preview is permanent while its window is open, so it follows
    the window.  MUTATION CHECK: delete ``follow_previews`` from MainWindow."""
    app = App(fake_platform)
    try:
        qt_window = make_window(app)
        assert not qt_window._bus._frames_on
        qt_window.show()
        qtbot.waitUntil(lambda: qt_window._bus._frames_on, timeout=3000)
        qt_window.hide()
        qtbot.waitUntil(lambda: not qt_window._bus._frames_on, timeout=3000)
    finally:
        app.close()
