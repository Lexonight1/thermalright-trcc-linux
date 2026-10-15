"""The RGB page in both windows: the Commands each sends, and a follow
preview that shows the colours the App sends."""
from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtGui import QImage

from trcc.core.commands import (
    BlinkAuraHeader,
    ListDevices,
    RamLighting,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetAuraLeds,
    SetRamEffect,
    SetRgbFollow,
)
from trcc.core.events import FrameSent
from trcc.core.models import (
    FollowColors,
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
from trcc.ui.presentation.rgb_page import RgbSource, strip_legend

LCD = "0402:3922"
STICKS = (RgbLight("i2c-3/0x19", "Stick A", 10, LightKind.RAM),
          RgbLight("i2c-3/0x1b", "Stick B", 10, LightKind.RAM))
#: A board header no count was given for -- listed beside the sticks.
HEADER = RgbLight("aura/argb1", "ASUS Board -- ARGB header 1", 0,
                  LightKind.BOARD)


class _Bus:
    """Answers the page's reads; records every Command it is sent."""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    def dispatch(self, cmd: Any) -> Any:
        self.sent.append(cmd)
        match cmd:
            case RamLighting():
                return RamLightingResult(state=RamAccessState.ON, message="on")
            case (RgbLights() | ScanRgbLights() | SetRamEffect() | SetAuraLeds()
                  | BlinkAuraHeader()):
                return RgbLightsResult(lights=(*STICKS, HEADER), scanned=True,
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
    view.follow._on_source(view.follow._source.findData(LCD))
    view.follow._mapping._buttons[FollowMapping.SINGLE].click()
    view.follow._colors._buttons[FollowColors.SMOOTH].click()
    view._lights_column._on_light_toggled(STICKS[0].ref, False)
    view._apply.click()
    _answered(view, qtbot)
    assert view._app.commands(SetRgbFollow) == [SetRgbFollow(
        mode=RgbFollowMode.RAM, host="127.0.0.1", port=6742, source=LCD,
        mapping=FollowMapping.SINGLE, targets=(STICKS[1].ref,),
        colors=FollowColors.SMOOTH)]


def test_blink_sends_the_headers_number(view: Any, qtbot: Any) -> None:
    from PySide6.QtWidgets import QPushButton
    (button,) = view.findChildren(QPushButton, "rgb-blink")
    button.click()
    _answered(view, qtbot)
    assert view._app.commands(BlinkAuraHeader) == [BlinkAuraHeader(header=1)]


def test_leave_alone_turns_following_off(view: Any, qtbot: Any) -> None:
    view._source_buttons[RgbSource.LEAVE].click()
    view._apply.click()
    _answered(view, qtbot)
    assert view._app.commands(SetRgbFollow) == [
        SetRgbFollow(mode=RgbFollowMode.OFF)]


def test_only_the_followed_lcd_reaches_the_preview(view: Any) -> None:
    view._source_buttons[RgbSource.FOLLOW].click()
    view.follow._on_source(view.follow._source.findData(LCD))
    picture = QImage(32, 32, QImage.Format.Format_RGB32)
    view.follow.on_frame(FrameSent(key="0402:9999", bytes_sent=0, surface=picture))
    assert view.follow.preview._image is None
    view.follow.on_frame(FrameSent(key=LCD, bytes_sent=0, surface=picture))
    assert view.follow.preview._image is picture


def test_the_strips_show_what_the_app_sent_from_what_is_followed(
        view: Any) -> None:
    """The window never samples: the App samples the picture under the
    overlay, which no window sees, and reports what it sent."""
    from trcc.core.events import RgbFollowChanged, RgbFollowSent
    from trcc.core.models import RgbFollowMode

    view._source_buttons[RgbSource.FOLLOW].click()
    view.follow._on_source(view.follow._source.findData(LCD))
    preview = view.follow.preview
    red, blue = ((200, 0, 0),) * 10, ((0, 0, 200),) * 10
    view.follow.on_follow_sent(RgbFollowSent(source="0402:9999", columns=(blue,)))
    assert preview._sent == ()                 # not what this page follows
    view.follow.on_follow_sent(RgbFollowSent(source=LCD, columns=(red, blue)))
    assert preview._sent == (red, blue)
    view.on_app_event(RgbFollowChanged(mode=RgbFollowMode.OFF))
    assert preview._sent == ()                 # following changed: stale


def test_a_followed_cooler_reaches_the_preview_as_the_follower_gets_it(
        view: Any) -> None:
    """The first cooler heard leads, as in the App; the strips are its
    colours stretched over each light's LEDs, the drivers' own rule."""
    from trcc.core.events import LedColorsChanged
    from trcc.core.led_models import stretch

    view._source_buttons[RgbSource.FOLLOW].click()
    view.follow._on_source(view.follow._source.findData(""))
    colors = tuple((i * 8, 0, 255 - i * 8) for i in range(30))
    preview = view.follow.preview
    view.follow.on_led_colors(LedColorsChanged(key="0416:8001", color_count=30,
                                        colors=colors))
    assert preview._colors == colors
    view.follow.on_led_colors(LedColorsChanged(key="0416:8002", color_count=1,
                                        colors=((1, 1, 1),)))
    assert preview._colors == colors          # not the cooler that leads
    assert stretch(colors, 10) == [colors[i * 3] for i in range(10)]
    # An LCD followed: a cooler's colours are not its picture.
    view.follow._on_source(view.follow._source.findData(LCD))
    view.follow.on_led_colors(LedColorsChanged(key="0416:8001", color_count=1,
                                        colors=((9, 9, 9),)))
    assert preview._colors == ()


# ── The effect preview ──────────────────────────────────────────────

def _strips(view: Any, source: RgbSource) -> Any:
    return view._previews[list(RgbSource).index(source)].strips


def test_the_effect_preview_animates_only_while_it_is_on_screen(
        view: Any, qtbot: Any) -> None:
    view._source_buttons[RgbSource.EFFECT].click()
    strips = _strips(view, RgbSource.EFFECT)
    qtbot.waitUntil(lambda: strips.animating, timeout=1000)
    view._source_buttons[RgbSource.FOLLOW].click()
    assert not strips.animating
    view._source_buttons[RgbSource.EFFECT].click()
    assert strips.animating
    view.hide()
    assert not strips.animating


def test_sticks_with_no_known_effect_are_drawn_still(view: Any) -> None:
    # The fake App's sticks carry no saved effect.
    view._source_buttons[RgbSource.LEAVE].click()
    strips = _strips(view, RgbSource.LEAVE)
    assert strips.isVisible() and not strips.animating
    assert view._previews[0].legend.text() == strip_legend(
        tuple((stick.name, None) for stick in STICKS))


def test_no_sticks_no_preview(view: Any) -> None:
    view._source_buttons[RgbSource.EFFECT].click()
    for row in view.page.rows(LightKind.RAM):
        view._lights_column._on_light_toggled(row.ref, False)
    preview = view._previews[1]
    assert not preview.isVisible() and not preview.strips.animating


def test_a_headers_led_count_is_sent_when_the_edit_ends(view: Any,
                                                        qtbot: Any) -> None:
    """The box beside an ARGB header sends SetAuraLeds -- once, when the
    edit ends; an unchanged count sends nothing.  The note says what the
    list means, to anyone.

    MUTATION CHECK: send on every value change instead."""
    from PySide6.QtWidgets import QLabel, QSpinBox

    from trcc.ui.presentation.rgb_page import BOARD_NOTE, LEDS_TIP

    (box,) = [b for b in view.findChildren(QSpinBox)
              if b.objectName() == "rgb-leds"]
    assert box.toolTip() == LEDS_TIP
    assert BOARD_NOTE in [label.text() for label in view.findChildren(QLabel)]
    box.editingFinished.emit()                       # unchanged: nothing
    box.setValue(7)
    box.setValue(8)                                  # typing: still nothing
    assert view._app.commands(SetAuraLeds) == []
    box.editingFinished.emit()
    _answered(view, qtbot)
    assert view._app.commands(SetAuraLeds) == [SetAuraLeds(header=1, leds=8)]
