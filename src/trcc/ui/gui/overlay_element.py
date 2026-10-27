"""Overlay element widget — single 60x60 grid cell.

Matches Windows UCXiTongXianShiSub: displays mode-specific styling,
live hardware metrics, and selection overlay.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from ...core.logs import per_frame
from ...core.models import (
    CATEGORY_NAMES,
    OVERLAY_MODE_IMAGES,
    OVERLAY_SELECT_IMAGE,
    SUB_METRICS,
    OverlayElementConfig,
    OverlayMode,
    format_metric,
)
from ..presentation.sensor_display import source_label
from .assets import Assets
from .constants import Colors, Sizes

log = logging.getLogger(__name__)
#: Per metrics tick, once a second per card.
frame_log = per_frame(__name__)

# ============================================================================
# Overlay element constants (matching Tkinter UCXiTongXianShiSub)
# ============================================================================

CATEGORY_COLORS = {
    0: '#32C5FF',   # CPU - cyan
    1: '#44D7B6',   # GPU - teal
    2: '#6DD401',   # MEM - lime
    3: '#F7B501',   # HDD - amber
    4: '#FA6401',   # NET - orange
    5: '#E02020',   # FAN - red
}

TIME_FORMATS = {0: 'HH:mm', 1: 'hh:mm'}
DATE_FORMATS = {1: 'yyyy/MM/dd', 2: 'dd/MM/yyyy', 3: 'MM/dd', 4: 'dd/MM'}

# Re-export from models for backward compat (uc_theme_setting imports these)
MODE_IMAGES = OVERLAY_MODE_IMAGES
SELECT_IMAGE = OVERLAY_SELECT_IMAGE


class OverlayElementWidget(QWidget):
    """Single overlay element cell in the 7x6 grid.

    60x60 with background image per mode type, 3 text labels,
    and selection overlay. Matches Windows UCXiTongXianShiSub.

    Windows UCXiTongXianShiSubTimer() updates cards with live data:
    - label1: category name (CPU/GPU/etc.)
    - label2: numeric value (52, 14:35, etc.)
    - label3: unit (°C, %, MHz, etc.)
    """

    clicked = Signal(int)           # index
    double_clicked = Signal(int)    # index (delete)

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.config: OverlayElementConfig | None = None
        self._selected = False
        self._live_value = ''   # label2: formatted value
        self._live_unit = ''    # label3: unit suffix
        self._lang = 'en'       # the window's: a weekday is drawn in it

        self.setFixedSize(Sizes.OVERLAY_CELL, Sizes.OVERLAY_CELL)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Preload mode images (shared via lru_cache)
        self._mode_pixmaps = {}
        for mode, img_name in MODE_IMAGES.items():
            px = Assets.load_pixmap(img_name, Sizes.OVERLAY_CELL, Sizes.OVERLAY_CELL)
            if not px.isNull():
                self._mode_pixmaps[mode] = px

        self._select_pixmap = Assets.load_pixmap(SELECT_IMAGE, Sizes.OVERLAY_CELL, Sizes.OVERLAY_CELL)

    def set_config(self, config):
        """Set element config or None to clear."""
        log.info(
            "OverlayElementWidget.set_config: index=%d mode=%s",
            self.index, config.mode.name if config else None,
        )
        self.config = config
        self._live_value = ''
        self._live_unit = ''
        self.update()

    def set_language(self, lang: str) -> None:
        """The window's language -- the weekday tile is drawn in it."""
        log.debug("OverlayElementWidget.set_language: index=%d %s -> %s",
                  self.index, self._lang, lang)
        self._lang = lang
        self.update()

    def set_selected(self, selected):
        # Logged on a CHANGE only: the grid re-applies every cell's state on
        # each refill, which follows every change any UI makes, so an
        # unconditional line wrote 42 of them per drag move, nearly all
        # "index=N False" for empty cells.
        if selected != self._selected:
            log.debug("OverlayElementWidget.set_selected: index=%d %s → %s",
                      self.index, self._selected, selected)
        self._selected = selected
        self.update()

    def show_reading(self, reading: tuple[str, str] | None) -> None:
        """Show a live ``(number, unit)`` -- label2 and label3 of the C# tile.

        The text comes from ``tile_reading``, the panel's own rule, so the
        card cannot disagree with the panel (#301).  None leaves the card
        showing its sub-metric name, as before any reading arrives.
        """
        value, unit = reading or ("", "")
        if (value, unit) != (self._live_value, self._live_unit):
            frame_log.debug("show_reading: index=%d %s%s",
                            self.index, value, unit)
            self._live_value, self._live_unit = value, unit
            self.update()

    # Card UI font matching Windows 微软雅黑 10.5pt
    _CARD_FONT = QFont('Microsoft YaHei', 10)
    #: The value row -- a clock's or custom text: x, y, width, height.
    _VALUE_ROW = (2, 21, 56, 18)
    #: The smallest a value shrinks to; past it, it stays centered and its
    #: ends go past the row equally.
    _MIN_POINT = 6.0
    #: One line keeps the value down to this size ("12:59 PM" at 9.5 pt reads
    #: better than "12:59" over "PM"); a text that would need smaller goes on
    #: two lines -- the value row and the unit row under it, which the tiles
    #: that draw a value alone (clock and custom text) leave empty.
    _ONE_LINE_MIN = 8.0

    @staticmethod
    def _split(text: str, metrics: QFontMetrics) -> tuple[str, str] | None:
        """*text* broken at the space that makes its two lines most even --
        None when it has no space.  Done by hand: Qt's word wrap takes as
        many lines as it likes, and a tile has room for two."""
        cuts = [i for i, c in enumerate(text) if c == " "]
        frame_log.debug("_split: %r at %d space(s)", text, len(cuts))
        if not cuts:
            return None

        def wider_line(cut: int) -> int:
            """The wider of the two lines broken at *cut*."""
            frame_log.debug("wider_line: cut=%d", cut)
            return max(metrics.horizontalAdvance(text[:cut]),
                       metrics.horizontalAdvance(text[cut + 1:]))

        cut = min(cuts, key=wider_line)
        return text[:cut], text[cut + 1:]

    def _fit_value(self, text: str) -> tuple[QFont, tuple[str, ...]]:
        """*text*'s font for the value row, and its lines: one line, shrunk
        down to ``_ONE_LINE_MIN``; else two (broken at a space), shrunk from
        the card font down to ``_MIN_POINT``.  Past that it is what it is --
        centered, never cut with "…".  A long date ("2026/10/27") was cut off
        at both ends."""
        w = self._VALUE_ROW[2]

        def sizes(low: float) -> Iterator[QFont]:
            """The card font, then half a point smaller each time, to *low*."""
            frame_log.debug("sizes: down to %.1fpt", low)
            font = QFont(self._CARD_FONT)
            while font.pointSizeF() >= low:
                yield font
                font = QFont(font)
                font.setPointSizeF(font.pointSizeF() - 0.5)

        def fits(lines: tuple[str, ...] | None, font: QFont) -> bool:
            """Every one of *lines* fits the row at *font*."""
            frame_log.debug("fits: %s at %.1fpt", lines, font.pointSizeF())
            return bool(lines) and all(QFontMetrics(font).horizontalAdvance(line) <= w
                                       for line in lines or ())

        found = (next(((f, (text,)) for f in sizes(self._ONE_LINE_MIN)
                       if fits((text,), f)), None)
                 or next(((f, two) for f in sizes(self._MIN_POINT)
                          if (two := self._split(text, QFontMetrics(f)))
                          and fits(two, f)), None))
        if found is None:
            smallest = list(sizes(self._MIN_POINT))[-1]
            found = (smallest, self._split(text, QFontMetrics(smallest)) or (text,))
        font, lines = found
        frame_log.debug("_fit_value: %r at %.1fpt -> %s", text, font.pointSizeF(), lines)
        return font, lines

    def _draw_value(self, painter: QPainter, text: str) -> None:
        """*text* fitted (:meth:`_fit_value`), each line centered left to
        right in its row -- the value row, then the unit row under it.  A
        line wider than the row is widened around its center, never clipped
        to one side."""
        x, y, w, h = self._VALUE_ROW
        font, lines = self._fit_value(text)
        metrics = QFontMetrics(font)
        frame_log.debug("_draw_value: %s", lines)
        painter.save()
        painter.setFont(font)
        for row, line in enumerate(lines):
            spill = max(0, metrics.horizontalAdvance(line) - w)
            painter.drawText(x - spill // 2 - 1, y + row * h, w + spill + 2, h,
                             Qt.AlignmentFlag.AlignCenter, line)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setFont(self._CARD_FONT)

        if self.config:
            mode = self.config.mode
            # Draw mode background
            px = self._mode_pixmaps.get(mode)
            if px:
                painter.drawPixmap(0, 0, px)
            else:
                painter.fillRect(self.rect(), QColor('#2D2D2D'))

            color_str = self.config.color

            if mode == OverlayMode.HARDWARE:
                mc = self.config.main_count
                cat_color = CATEGORY_COLORS.get(mc, '#9375FF')
                cat_name = (source_label(self.config.metric) if self.config.metric
                            else CATEGORY_NAMES.get(mc, '???'))

                # label1: category name in category color (Windows: label1.ForeColor)
                painter.setPen(QColor(cat_color))
                painter.drawText(2, 1, 56, 18, Qt.AlignmentFlag.AlignCenter, cat_name)
                # label2: live value or sub-metric name (Windows: label2.ForeColor = myColor)
                painter.setPen(QColor(color_str))
                if self._live_value:
                    painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                     self._live_value)
                else:
                    sub_name = ('--' if self.config.metric else
                                SUB_METRICS.get(mc, {}).get(self.config.sub_count, '--'))
                    painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, sub_name)
                # label3: unit suffix (Windows: label3.ForeColor = myColor)
                painter.drawText(2, 41, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                 self._live_unit or '--')
            elif mode == OverlayMode.TIME:
                # Windows: label1+label3 hidden, only label2 visible --
                # the text the panel draws, so the two never disagree
                text = format_metric("time", 0, time_format=self.config.mode_sub)
                painter.setPen(QColor(color_str))
                self._draw_value(painter, text)
            elif mode == OverlayMode.DATE:
                # Windows: label1+label3 hidden, only label2 visible
                text = format_metric("date", 0, date_format=self.config.mode_sub)
                painter.setPen(QColor(color_str))
                self._draw_value(painter, text)
            elif mode == OverlayMode.WEEKDAY:
                # Windows: label1+label3 hidden, only label2 visible --
                # the panel's weekday, in the window's language
                painter.setPen(QColor(color_str))
                self._draw_value(painter, format_metric("weekday", 0, lang=self._lang))
            elif mode == OverlayMode.CUSTOM:
                # Windows: label1+label3 hidden, label2.Text = myText
                painter.setPen(QColor(color_str))
                self._draw_value(painter, self.config.text)

            # Selection overlay (Windows: OnPaint draws imageSelect)
            if self._selected and not self._select_pixmap.isNull():
                painter.drawPixmap(0, 0, self._select_pixmap)
            elif self._selected:
                painter.setPen(QColor('white'))
                painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        else:
            # Empty slot — draw subtle "+"
            painter.setPen(QColor(Colors.EMPTY_TEXT))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '+')

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            log.info("OverlayElementWidget.mousePressEvent: index=%d clicked",
                     self.index)
            self.clicked.emit(self.index)

    def mouseDoubleClickEvent(self, event):
        if self.config:
            log.info(
                "OverlayElementWidget.mouseDoubleClickEvent: index=%d "
                "(double_clicked / delete)", self.index,
            )
            self.double_clicked.emit(self.index)

    def contextMenuEvent(self, event):
        if self.config:
            log.info(
                "OverlayElementWidget.contextMenuEvent: index=%d "
                "(opening delete menu)", self.index,
            )
            menu = QMenu(self)
            delete_action = menu.addAction("Delete")
            action = menu.exec(event.globalPos())
            if action == delete_action:
                log.info(
                    "OverlayElementWidget.contextMenuEvent: index=%d "
                    "delete confirmed", self.index,
                )
                self.double_clicked.emit(self.index)
