"""The RGB page in both windows: the Commands each sends, and a follow
preview that shows the colours the App sends."""
from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage

from trcc.core.commands import (
    ListDevices,
    RamLighting,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetRamEffect,
    SetRgbFollow,
)
from trcc.core.events import FrameSent
from trcc.core.models import (
    FollowMapping,
    LightKind,
    RamAccessState,
    RamEffect,
    RgbFollowMode,
    RgbLight,
)
from trcc.core.results import (
    DeviceEntry,
    DevicesListResult,
    RamLightingResult,
    RgbFollowResult,
    RgbLightsResult,
)
from trcc.ui.presentation.rgb_page import RgbSource
from trcc.ui.qt_rgb_page import sample_grid

LCD = "0402:3922"
STICKS = (RgbLight("i2c-3/0x19", "Stick A", 10, LightKind.RAM),
          RgbLight("i2c-3/0x1b", "Stick B", 10, LightKind.RAM))


class _Bus:
    """Answers the page's reads; records every Command it is sent."""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    def dispatch(self, cmd: Any) -> Any:
        self.sent.append(cmd)
        match cmd:
            case RamLighting():
                return RamLightingResult(state=RamAccessState.ON, message="on")
            case RgbLights() | ScanRgbLights() | SetRamEffect():
                return RgbLightsResult(lights=STICKS, scanned=True,
                                       ram_access=RamAccessState.ON,
                                       message="done")
            case RgbFollow() | SetRgbFollow():
                return RgbFollowResult(host="127.0.0.1", port=6742,
                                       message="done")
            case ListDevices():
                return DevicesListResult(devices=[DeviceEntry(
                    key=LCD, product="Frozen", kind="lcd", connected=True)])
        raise AssertionError(f"unexpected {cmd!r}")

    def commands(self, kind: type) -> list[Any]:
        return [c for c in self.sent if isinstance(c, kind)]


def _skins() -> list[Any]:
    from trcc.ui.gui.uc_rgb import UCRgbPage
    from trcc.ui.qtgui.panels.rgb_panel import QtRgbPage
    return [UCRgbPage, QtRgbPage]


@pytest.fixture(params=_skins(), ids=["gui", "qtgui"])
def view(request: Any, qtbot: Any) -> Any:
    page = request.param(_Bus())
    qtbot.addWidget(page)
    page.resize(1170, 630)
    page.show()
    qtbot.waitExposed(page)
    return page


def _answered(view: Any, qtbot: Any) -> None:
    qtbot.waitUntil(lambda: not view._busy, timeout=3000)


def test_find_sends_the_address_in_the_box(view: Any, qtbot: Any) -> None:
    view._lights_column._address.setText("10.0.0.7:6800")
    view._lights_column._on_address_edited("10.0.0.7:6800")
    view._on_find()
    _answered(view, qtbot)
    assert view._app.commands(ScanRgbLights) == [
        ScanRgbLights(host="10.0.0.7", port=6800)]
    assert view._message.text() == "done"


def test_apply_saves_the_effect_with_only_what_it_takes(view: Any,
                                                        qtbot: Any) -> None:
    view._source_buttons[RgbSource.EFFECT].click()
    view._effect._on_effect(view._effect._effect.findData(RamEffect.RAINBOW))
    view._apply.click()
    _answered(view, qtbot)
    (sent,) = view._app.commands(SetRamEffect)
    assert (sent.effect, sent.refs, sent.colors) == (RamEffect.RAINBOW, (), ())


def test_apply_follows_with_the_source_the_mapping_and_the_lights(
        view: Any, qtbot: Any) -> None:
    view._source_buttons[RgbSource.FOLLOW].click()
    view._follow._on_source(view._follow._source.findData(LCD))
    view._follow._mapping._buttons[FollowMapping.SINGLE].click()
    view._lights_column._on_light_toggled(STICKS[0].ref, False)
    view._apply.click()
    _answered(view, qtbot)
    assert view._app.commands(SetRgbFollow) == [SetRgbFollow(
        mode=RgbFollowMode.RAM, host="127.0.0.1", port=6742, source=LCD,
        mapping=FollowMapping.SINGLE, targets=(STICKS[1].ref,))]


def test_leave_alone_turns_following_off(view: Any, qtbot: Any) -> None:
    view._source_buttons[RgbSource.LEAVE].click()
    view._apply.click()
    _answered(view, qtbot)
    assert view._app.commands(SetRgbFollow) == [
        SetRgbFollow(mode=RgbFollowMode.OFF)]


def test_only_the_followed_lcd_reaches_the_preview(view: Any) -> None:
    view._source_buttons[RgbSource.FOLLOW].click()
    view._follow._on_source(view._follow._source.findData(LCD))
    picture = QImage(32, 32, QImage.Format.Format_RGB32)
    view.on_frame(FrameSent(key="0402:9999", bytes_sent=0, surface=picture))
    assert view._follow.preview._image is None
    view.on_frame(FrameSent(key=LCD, bytes_sent=0, surface=picture))
    assert view._follow.preview._image is picture


@pytest.mark.parametrize("columns", [1, 2, 3])
def test_the_preview_samples_as_the_app_does(columns: int) -> None:
    from trcc.adapters.render.qt import QtRenderer
    image = QImage(320, 240, QImage.Format.Format_ARGB32)
    for x in range(320):
        for y in range(240):
            image.setPixelColor(x, y, QColor(x * 255 // 319, y, (x + y) % 256))
    assert sample_grid(image, columns, 10) == QtRenderer().get_pixels_rgb(
        image, columns, 10)


def test_a_followed_cooler_reaches_the_preview_as_the_follower_gets_it(
        view: Any) -> None:
    """The first cooler heard leads, as in the App; the strips are its
    colours stretched over each light's LEDs, the drivers' own rule."""
    from trcc.core.events import LedColorsChanged
    from trcc.core.led_models import stretch

    view._source_buttons[RgbSource.FOLLOW].click()
    view._follow._on_source(view._follow._source.findData(""))
    colors = tuple((i * 8, 0, 255 - i * 8) for i in range(30))
    preview = view._follow.preview
    view.on_led_colors(LedColorsChanged(key="0416:8001", color_count=30,
                                        colors=colors))
    assert preview._colors == colors
    view.on_led_colors(LedColorsChanged(key="0416:8002", color_count=1,
                                        colors=((1, 1, 1),)))
    assert preview._colors == colors          # not the cooler that leads
    assert stretch(colors, 10) == [colors[i * 3] for i in range(10)]
    # An LCD followed: a cooler's colours are not its picture.
    view._follow._on_source(view._follow._source.findData(LCD))
    view.on_led_colors(LedColorsChanged(key="0416:8001", color_count=1,
                                        colors=((9, 9, 9),)))
    assert preview._colors == ()
