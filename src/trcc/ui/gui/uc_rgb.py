"""The RGB page in the classic window: the shared view, this skin's Commands.

The page is drawn in the window's own colours -- the gold title bar of a
device page and the dark framed body of the settings page -- since no bitmap
of the original carries an RGB page to sit on.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ...core.commands import (
    ListDevices,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetRamEffect,
    SetRgbFollow,
)
from ...core.models import RgbFollowMode
from ...core.ports import CommandBus
from ...core.results import DeviceEntry, Result, RgbFollowResult, RgbLightsResult
from ..presentation.rgb_page import (
    ApplyPlan,
    FindLights,
    FollowDevice,
    LeaveLights,
    SaveEffect,
)
from ..qt_ram_access import RamAccessRow
from ..qt_rgb_page import RgbPageView
from .constants import Colors, Sizes
from .uc_ram_access import UCRamAccess

log = logging.getLogger(__name__)

GOLD = "#b49653"
ACCENT = "#d9b45a"
FRAME = "#434343"
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
#rgb-page QComboBox {{ background: #1b1b20; border: 1px solid #5a5a62;
    padding: 4px 10px; }}
#rgb-page QComboBox QAbstractItemView {{ background: #1b1b20;
    selection-background-color: {GOLD}; }}
#rgb-page QPushButton#rgb-choice {{ background: {CARD};
    border: 1px solid {CARD_BORDER}; padding: 6px 22px; font-size: 10pt; }}
#rgb-page QPushButton#rgb-choice:checked {{ background: #4a3f22;
    border: 1px solid {ACCENT}; }}
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

    def _find(self, plan: FindLights) -> Result:
        log.info("UCRgbPage._find: %s", plan)
        return self._app.dispatch(ScanRgbLights(host=plan.host, port=plan.port))

    def _send(self, plan: ApplyPlan) -> Result:
        log.info("UCRgbPage._send: %s", plan)
        match plan:
            case LeaveLights():
                return self._app.dispatch(SetRgbFollow(mode=RgbFollowMode.OFF))
            case SaveEffect(refs=refs, settings=s):
                return self._app.dispatch(SetRamEffect(
                    effect=s.effect, refs=refs, speed=s.speed,
                    direction=s.direction, colors=s.colors,
                    random_colors=s.random_colors, brightness=s.brightness))
            case FollowDevice():
                return self._app.dispatch(SetRgbFollow(
                    mode=plan.mode, host=plan.host, port=plan.port,
                    source=plan.source, mapping=plan.mapping,
                    colors=plan.colors, targets=plan.targets))


class UCRgb(QWidget):
    """The RGB Lighting view: title bar, framed body, the page inside."""

    #: The title bar and the framed body, in this view's coordinates -- where
    #: a device page's gold bar and the settings page's frame sit.
    TITLE_BAR = QRect(10, 20, 1254, 48)
    BODY_FRAME = QRect(12, 98, 1250, 680)
    BODY = (50, 120, 1170, 630)

    def __init__(self, app: CommandBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("UCRgb.__init__")
        self.setFixedSize(Sizes.FORM_W, Sizes.FORM_H)
        self.page = UCRgbPage(app, self)
        self.page.setGeometry(*self.BODY)
        self.page.setStyleSheet(_QSS)

    def paintEvent(self, event: QPaintEvent) -> None:
        log.debug("UCRgb.paintEvent")
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(Colors.WINDOW_BG))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(GOLD))
        p.drawRoundedRect(self.TITLE_BAR, 6, 6)
        title = QFont()
        title.setPixelSize(30)
        title.setBold(True)
        p.setFont(title)
        p.setPen(QColor("#3a3a3a"))
        p.drawText(self.TITLE_BAR.adjusted(20, 0, 0, 0),
                   Qt.AlignmentFlag.AlignVCenter, "RGB Lighting")
        p.setPen(QPen(QColor(FRAME), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.BODY_FRAME, 6, 6)
        p.end()

