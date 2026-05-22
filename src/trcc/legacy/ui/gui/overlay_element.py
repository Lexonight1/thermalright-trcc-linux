"""Overlay element widget — single 60x60 grid cell.

Matches Windows UCXiTongXianShiSub: displays mode-specific styling,
live hardware metrics, and selection overlay.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from ...core.models import (
    CATEGORY_NAMES,
    HARDWARE_METRICS,
    OVERLAY_MODE_IMAGES,
    OVERLAY_SELECT_IMAGE,
    SUB_METRICS,
    OverlayElementConfig,
    OverlayMode,
    format_metric,
)
from .assets import Assets
from .constants import Colors, Sizes

log = logging.getLogger(__name__)

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

    # Single source of truth: HARDWARE_METRICS in core/models.py

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.config: OverlayElementConfig | None = None
        self._selected = False
        self._live_value = ''   # label2: formatted value
        self._live_unit = ''    # label3: unit suffix

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
        log.debug("element[%d] set_config mode=%s", self.index,
                  config.mode if config else None)
        self.config = config
        self._live_value = ''
        self._live_unit = ''
        self.update()

    def set_selected(self, selected):
        self._selected = selected
        self.update()

    def update_metrics(self, metrics):
        """Update card with live system metrics (Windows UCXiTongXianShiSubTimer)."""
        if not self.config or self.config.mode != OverlayMode.HARDWARE:
            return
        metric_key = HARDWARE_METRICS.get((self.config.main_count, self.config.sub_count))
        if not metric_key:
            return
        raw = getattr(metrics, metric_key, None)
        if raw is None:
            return
        # Split formatted value into number + unit (Windows regex pattern)
        import re

        formatted = format_metric(metric_key, raw,
                                  temp_unit=self.config.mode_sub)
        # Separate number from unit: "52°C" → "52" + "°C"
        m = re.match(r'([\d.]+)(.*)', formatted)
        if m:
            self._live_value = m.group(1)
            self._live_unit = m.group(2).strip()
        else:
            self._live_value = formatted
            self._live_unit = ''
        self.update()

    # Card UI font matching Windows 微软雅黑 10.5pt
    _CARD_FONT = QFont('Microsoft YaHei', 10)

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
                cat_name = CATEGORY_NAMES.get(mc, '???')

                # label1: category name in category color (Windows: label1.ForeColor)
                painter.setPen(QColor(cat_color))
                painter.drawText(2, 1, 56, 18, Qt.AlignmentFlag.AlignCenter, cat_name)
                # label2: live value or sub-metric name (Windows: label2.ForeColor = myColor)
                painter.setPen(QColor(color_str))
                if self._live_value:
                    painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                     self._live_value)
                else:
                    sub_name = SUB_METRICS.get(mc, {}).get(self.config.sub_count, '--')
                    painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, sub_name)
                # label3: unit suffix (Windows: label3.ForeColor = myColor)
                painter.drawText(2, 41, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                 self._live_unit or '--')
            elif mode == OverlayMode.TIME:
                # Windows: label1+label3 hidden, only label2 visible
                from datetime import datetime
                if self.config.mode_sub == 1:
                    text = datetime.now().strftime('%I:%M').lstrip('0')
                else:
                    text = datetime.now().strftime('%H:%M')
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, text)
            elif mode == OverlayMode.DATE:
                # Windows: label1+label3 hidden, only label2 visible
                from datetime import datetime
                fmts = {0: '%Y/%m/%d', 1: '%Y/%m/%d', 2: '%d/%m/%Y', 3: '%m/%d', 4: '%d/%m'}
                text = datetime.now().strftime(fmts.get(self.config.mode_sub, '%Y/%m/%d'))
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, text)
            elif mode == OverlayMode.WEEKDAY:
                # Windows: label1+label3 hidden, only label2 visible
                # Windows uses SUN=0..SAT=6 array indexed by DayOfWeek
                from datetime import datetime
                days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                 days[datetime.now().weekday()])
            elif mode == OverlayMode.CUSTOM:
                # Windows: label1+label3 hidden, label2.Text = myText
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                 self.config.text)

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
            self.clicked.emit(self.index)

    def mouseDoubleClickEvent(self, event):
        if self.config:
            self.double_clicked.emit(self.index)

    def contextMenuEvent(self, event):
        if self.config:
            menu = QMenu(self)
            delete_action = menu.addAction("Delete")
            action = menu.exec(event.globalPos())
            if action == delete_action:
                self.double_clicked.emit(self.index)
