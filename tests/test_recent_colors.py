"""The overlay colour editor's recent row -- the C#'s button1..11.

``UCXiTongXianShiColor`` keeps 11 swatches per device in ``Color.dc``: when
the user moves to another element having changed the colour, that colour goes
to the front and the oldest drops off (``UCXiTongXianShiBackupColorSave``).
Ours were 11 transparent buttons, never filled or connected.  The App keeps
the row so every UI shows the same one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import RecentColors, RememberColor
from trcc.core.models import RECENT_COLOR_DEFAULT, RECENT_COLOR_SLOTS
from trcc.services.settings import Settings

from .conftest import FakePaths

KEY = "0402:3922"
SILVER = RECENT_COLOR_DEFAULT


def test_an_untouched_row_is_eleven_silver(fake_platform) -> None:
    result = App(fake_platform).dispatch(RecentColors(key=KEY))
    assert result.ok
    assert result.colors == (SILVER,) * RECENT_COLOR_SLOTS


def test_remembering_shifts_the_row_and_drops_the_oldest(fake_platform) -> None:
    app = App(fake_platform)
    for i in range(13):
        result = app.dispatch(RememberColor(key=KEY, color=(i, i, i)))
    assert result.ok
    assert result.colors == tuple((i, i, i) for i in range(12, 1, -1))
    assert app.dispatch(RecentColors(key=KEY)).colors == result.colors
    # What is STORED stops at 11 too, or trcc.json grows by one per edit.
    assert len(app.settings.for_device(KEY).recent_colors) == RECENT_COLOR_SLOTS


def test_a_repeat_is_kept_as_the_csharp_keeps_it(fake_platform) -> None:
    app = App(fake_platform)
    app.dispatch(RememberColor(key=KEY, color=(1, 2, 3)))
    colors = app.dispatch(RememberColor(key=KEY, color=(1, 2, 3))).colors
    assert colors[:3] == ((1, 2, 3), (1, 2, 3), SILVER)


def test_a_channel_out_of_range_is_refused(fake_platform) -> None:
    result = App(fake_platform).dispatch(RememberColor(key=KEY,
                                                       color=(0, 256, 0)))
    assert not result.ok
    assert result.message == "g channel out of range (0-255): 256"


def test_the_row_survives_a_restart(tmp_path: Path) -> None:
    Settings(FakePaths(tmp_path)).remember_color(KEY, (10, 20, 30))
    reloaded = Settings(FakePaths(tmp_path))
    assert reloaded.recent_colors(KEY)[0] == (10, 20, 30)
    assert reloaded.recent_colors(KEY)[1] == SILVER


@pytest.mark.parametrize("stored", [
    [[1, 2]], [[1, 2, 300]], [["a", 2, 3]], "not a list",
])
def test_a_malformed_stored_row_falls_back_to_silver(tmp_path: Path,
                                                      stored) -> None:
    Settings(FakePaths(tmp_path)).remember_color(KEY, (5, 5, 5))
    path = tmp_path / "trcc.json"
    data = json.loads(path.read_text())
    data["devices"][KEY]["recent_colors"] = stored
    path.write_text(json.dumps(data))
    assert Settings(FakePaths(tmp_path)).recent_colors(KEY) == (
        [SILVER] * RECENT_COLOR_SLOTS)


# ── The classic window: one report per edit, swatches do not count ──────────

def test_an_edit_is_reported_once_when_the_user_moves_on(qtbot) -> None:
    from PySide6.QtCore import QPoint, Qt

    from trcc.ui.gui.color_and_add_panels import ColorPickerPanel

    panel = ColorPickerPanel()
    qtbot.addWidget(panel)
    ended: list = []
    panel.edit_finished.connect(lambda r, g, b: ended.append((r, g, b)))
    for x in (20, 50, 79):                      # a drag: three hues
        qtbot.mouseClick(panel.hue_strip, Qt.MouseButton.LeftButton,
                         pos=QPoint(x, 9))
    panel.end_edit()
    panel.end_edit()
    assert ended == [(0, 255, 255)]


def test_a_swatch_is_not_an_edit(qtbot) -> None:
    from trcc.ui.gui.color_and_add_panels import ColorPickerPanel

    panel = ColorPickerPanel()
    qtbot.addWidget(panel)
    ended: list = []
    panel.edit_finished.connect(lambda r, g, b: ended.append((r, g, b)))
    panel.set_recent_colors([(9, 8, 7)] + [SILVER] * 10)
    panel._history_btns[0].click()
    assert (panel.r_input.text(), panel.g_input.text(),
            panel.b_input.text()) == ("9", "8", "7")
    panel.end_edit()
    assert ended == []


def test_qtgui_remembers_a_colour_chosen_in_the_element_dialog(qtbot) -> None:
    from types import SimpleNamespace

    from PySide6.QtWidgets import QWidget

    from trcc.ui.qtgui.panels.overlay_editor import _ElementDialog

    sent: list = []

    class _Panel(QWidget):
        """The editor panel as the dialog uses it: key, dispatch, app."""

        def __init__(self) -> None:
            super().__init__()
            self.app = self

        def _key(self) -> str:
            return KEY

        def dispatch(self, cmd):
            sent.append(cmd)
            return SimpleNamespace(ok=True, colors=(), fonts=[])

    panel = _Panel()
    qtbot.addWidget(panel)
    dialog = _ElementDialog(panel)  # type: ignore[arg-type]
    sent.clear()
    dialog.accept()
    assert sent == [], "an untouched colour was remembered"
    dialog._on_eyedropper_picked(1, 2, 3)
    dialog.accept()
    assert [c.color for c in sent if isinstance(c, RememberColor)] == [(1, 2, 3)]


def test_moving_to_another_element_asks_the_app_to_remember(qtbot) -> None:
    """UCThemeSetting.cs:179 saves the colour before showing the next element."""
    from PySide6.QtCore import QPoint, Qt

    from trcc.core.models import OverlayElementConfig
    from trcc.ui.gui.uc_theme_setting import UCThemeSetting

    setting = UCThemeSetting()
    qtbot.addWidget(setting)
    asked: list = []
    setting.delegate.connect(lambda cmd, info, data: asked.append((cmd, info)))
    qtbot.mouseClick(setting.color_panel.hue_strip, Qt.MouseButton.LeftButton,
                     pos=QPoint(79, 9))
    asked.clear()
    setting._show_element(OverlayElementConfig())
    assert asked == [(UCThemeSetting.CMD_COLOR_REMEMBER, (0, 255, 255))]
