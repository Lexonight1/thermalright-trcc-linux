"""The RGB page in the classic window: the shared view, this skin's Commands.

The page is drawn in the window's own colours -- the gold title bar of a
device page and the dark framed body of the settings page -- since no bitmap
of the original carries an RGB page to sit on.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from PySide6.QtGui import QPaintEvent
from PySide6.QtWidgets import QWidget

from trcc.core.commands import Command, ListDevices
from trcc.core.logs import per_frame
from trcc.core.ports import CommandBus
from trcc.core.results import DeviceEntry
from trcc.modules.rgb.core.commands import (
    BlinkAuraHeader,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetAuraEffect,
    SetAuraLeds,
    SetLinkedColor,
    SetRamEffect,
    SetRgbFollow,
)
from trcc.modules.rgb.core.models import RgbFollowMode
from trcc.modules.rgb.core.results import RgbFollowResult, RgbLightsResult
from trcc.modules.rgb.ui.gui.uc_ram_access import UCRamAccess
from trcc.modules.rgb.ui.presentation.rgb_page import (
    ApplyPlan,
    FindLights,
    FollowDevice,
    HeaderLeds,
    LeaveLights,
    LinkColor,
    RunBoardEffect,
    SaveEffect,
)
from trcc.modules.rgb.ui.qt_ram_access import RamAccessRow
from trcc.modules.rgb.ui.qt_rgb_page import RgbPageView, hear
from trcc.ui.bus_bridge import BusBridge
from trcc.ui.gui.base import paint_page
from trcc.ui.gui.constants import Colors, Sizes
from trcc.ui.gui.module_view import ModuleView

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

GOLD = Colors.PAGE_GOLD
ACCENT = "#d9b45a"
FRAME = Colors.PAGE_FRAME
CARD = "#2b2b31"
CARD_BORDER = "#4a4a52"
MUTED = "#9a9aa2"
TEXT = "#eeeeee"

_QSS = f"""
#rgb-page, #rgb-page QWidget {{ background: transparent; color: {TEXT};
    font-size: 11pt; }}
#rgb-page QLabel#rgb-heading {{ color: #cfcfd4; font-size: 13pt;
    font-weight: bold; }}
#rgb-page QLabel#rgb-title, #rgb-page QCheckBox#rgb-light {{
    font-weight: bold; }}
#rgb-page QLabel#rgb-muted {{ color: {MUTED}; font-size: 9pt; }}
#rgb-page QLabel#rgb-problem {{ color: {ACCENT}; font-size: 10pt; }}
#rgb-page QFrame#rgb-card {{ background: {CARD}; border: 1px solid {ACCENT};
    border-radius: 6px; }}
#rgb-page QFrame#rgb-openrgb {{ border: 1px dashed {CARD_BORDER};
    border-radius: 6px; }}
#rgb-page QLineEdit#rgb-address {{ background: black; color: {GOLD};
    border: none; font-size: 9pt; padding: 2px 4px; }}
#rgb-page QScrollBar:vertical {{ background: transparent; width: 8px;
    margin: 0; }}
#rgb-page QScrollBar::handle:vertical {{ background: {CARD_BORDER};
    border-radius: 4px; min-height: 24px; }}
#rgb-page QScrollBar::add-line:vertical, #rgb-page QScrollBar::sub-line:vertical {{
    height: 0; }}
#rgb-page QSpinBox#rgb-leds {{ background: black; color: {GOLD};
    border: 1px solid {CARD_BORDER}; font-size: 10pt; padding: 2px 4px;
    min-width: 56px; min-height: 22px; }}
#rgb-page QComboBox {{ background: #1b1b20; border: 1px solid #5a5a62;
    padding: 4px 10px; }}
#rgb-page QComboBox QAbstractItemView {{ background: #1b1b20;
    selection-background-color: {GOLD}; }}
#rgb-page QPushButton#rgb-choice {{ background: {CARD};
    border: 1px solid {CARD_BORDER}; padding: 6px 22px; font-size: 10pt; }}
#rgb-page QPushButton#rgb-choice:checked {{ background: #4a3f22;
    border: 1px solid {ACCENT}; }}
#rgb-page QPushButton#rgb-device {{ background: {CARD};
    border: 1px solid {CARD_BORDER}; border-radius: 6px; padding: 10px 14px;
    text-align: left; font-size: 11pt; }}
#rgb-page QPushButton#rgb-device:checked {{ background: #4a3f22;
    border: 1px solid {ACCENT}; font-weight: bold; }}
#rgb-page QPushButton#rgb-button, #rgb-page QPushButton#rgb-apply {{
    background: #2d2d33; border: 2px solid {ACCENT}; border-radius: 6px;
    padding: 6px 16px; font-weight: bold; }}
#rgb-page QPushButton:disabled {{ color: #666; border-color: #555; }}
#rgb-page QRadioButton:disabled, #rgb-page QCheckBox:disabled {{
    color: #666; }}
#rgb-page QRadioButton::indicator {{ width: 12px; height: 12px;
    border: 2px solid #cfcfd4; border-radius: 8px; }}
#rgb-page QRadioButton::indicator:checked {{ background: {ACCENT};
    border-color: {ACCENT}; }}
#rgb-page QRadioButton::indicator:disabled {{ border-color: #555; }}
#rgb-page QCheckBox::indicator {{ width: 12px; height: 12px;
    border: 2px solid #cfcfd4; border-radius: 3px; }}
#rgb-page QCheckBox::indicator:checked {{ background: {ACCENT};
    border-color: {ACCENT}; }}
#rgb-page QSlider::groove:horizontal {{ height: 6px; background: #45454d;
    border-radius: 3px; }}
#rgb-page QSlider::sub-page:horizontal {{ background: {ACCENT};
    border-radius: 3px; }}
#rgb-page QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px;
    margin: -5px 0; border-radius: 8px; }}
"""


class UCRgbPage(RgbPageView):
    """The page body, sending this skin's Commands."""

    def _access_row(self) -> RamAccessRow:
        log.debug("UCRgbPage._access_row")
        return UCRamAccess(self._app)

    def _lights(self) -> RgbLightsResult:
        log.debug("UCRgbPage._lights")
        return self._app.dispatch(RgbLights())

    def _following(self) -> RgbFollowResult:
        log.debug("UCRgbPage._following")
        return self._app.dispatch(RgbFollow())

    def _devices(self) -> Sequence[DeviceEntry]:
        log.debug("UCRgbPage._devices")
        return self._app.dispatch(ListDevices()).devices

    def _find(self, plan: FindLights) -> Command[Any]:
        log.info("UCRgbPage._find: %s", plan)
        return ScanRgbLights(host=plan.host, port=plan.port)

    def _leds(self, plan: HeaderLeds) -> Command[Any]:
        log.info("UCRgbPage._leds: %s", plan)
        return SetAuraLeds(header=plan.header, leds=plan.leds)

    def _blink(self, header: int) -> Command[Any]:
        log.info("UCRgbPage._blink: header %d", header)
        return BlinkAuraHeader(header=header)

    def _send(self, plan: ApplyPlan) -> Command[Any]:
        log.info("UCRgbPage._send: %s", plan)
        match plan:
            case LeaveLights():
                return SetRgbFollow(mode=RgbFollowMode.OFF)
            case SaveEffect(refs=refs, settings=s):
                return SetRamEffect(
                    effect=s.effect, refs=refs, speed=s.speed,
                    direction=s.direction, colors=s.colors,
                    random_colors=s.random_colors, brightness=s.brightness)
            case LinkColor(refs=refs, effect=e):
                return SetLinkedColor(metric=e.metric, refs=refs, source=e.source)
            case RunBoardEffect(refs=refs, settings=s):
                return SetAuraEffect(
                    effect=s.effect, refs=refs, speed=s.speed,
                    direction=s.direction, colors=s.colors,
                    random_colors=s.random_colors, brightness=s.brightness)
            case FollowDevice():
                return SetRgbFollow(
                    mode=plan.mode, host=plan.host, port=plan.port,
                    source=plan.source, mapping=plan.mapping,
                    colors=plan.colors, targets=plan.targets)


class UCRgb(QWidget, ModuleView):
    """The RGB Lighting view: title bar, framed body, the page inside --
    opened by its button in the sidebar."""

    BUTTON_TEXT = "RGB"
    TOOLTIP = "RGB Lighting -- memory and OpenRGB devices"

    #: The title bar and the framed body, in this view's coordinates -- where
    #: a device page's gold bar and the settings page's frame sit.
    BODY = (50, 120, 1170, 630)

    def __init__(self, app: CommandBus, bridge: BusBridge,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("UCRgb.__init__")
        self.setFixedSize(Sizes.FORM_W, Sizes.FORM_H)
        self.page = UCRgbPage(app, self)
        self.page.setGeometry(*self.BODY)
        self.page.setStyleSheet(_QSS)
        hear(self.page, bridge)

    def paintEvent(self, event: QPaintEvent) -> None:
        frame_log.debug("UCRgb.paintEvent")
        paint_page(self, "RGB Lighting")

