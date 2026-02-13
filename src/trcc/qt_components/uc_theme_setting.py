"""
PyQt6 UCThemeSetting - Settings container with sub-panels.

Matches Windows TRCC.DCUserControl.UCThemeSetting (732x661)
Contains overlay editor, color picker, and display mode panels.

Windows layout (from UCThemeSetting.resx):
- ucXiTongXianShi1:     (10, 1)   472x430  - Overlay grid
- ucXiTongXianShiColor1: (492, 1)  230x374  - Color picker
- ucXiTongXianShiAdd1:   (492, 1)  230x430  - Add element (stacked)
- ucXiTongXianShiTable1: (492, 376) 230x54  - Data table
- ucMengBanXianShi1:     (10, 441) 351x100  - Mask display toggle
- ucBeiJingXianShi1:     (371, 441) 351x100 - Background display toggle
- ucTouPingXianShi1:     (10, 551) 351x100  - Screen cast toggle
- ucShiPingBoFangQi1:    (371, 551) 351x100 - Video player toggle
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..system_info import format_metric
from .assets import load_pixmap
from .base import BasePanel, set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles

# ============================================================================
# Overlay element constants (matching Tkinter UCXiTongXianShiSub)
# ============================================================================

# Mode constants
MODE_HARDWARE = 0
MODE_TIME = 1
MODE_WEEKDAY = 2
MODE_DATE = 3
MODE_CUSTOM = 4

# Hardware categories
HW_CPU = 0
HW_GPU = 1
HW_MEM = 2
HW_HDD = 3
HW_NET = 4
HW_FAN = 5

CATEGORY_NAMES = {0: 'CPU', 1: 'GPU', 2: 'MEM', 3: 'HDD', 4: 'NET', 5: 'FAN'}

CATEGORY_COLORS = {
    0: '#32C5FF',   # CPU - cyan
    1: '#44D7B6',   # GPU - teal
    2: '#6DD401',   # MEM - lime
    3: '#F7B501',   # HDD - amber
    4: '#FA6401',   # NET - orange
    5: '#E02020',   # FAN - red
}

SUB_METRICS = {
    0: {1: 'Temp', 2: 'Usage', 3: 'Freq', 4: 'Power'},
    1: {1: 'Temp', 2: 'Usage', 3: 'Clock', 4: 'Power'},
    2: {1: 'Used%', 2: 'Clock', 3: 'Used', 4: 'Free'},
    3: {1: 'Read', 2: 'Write', 3: 'Activity', 4: 'Temp'},
    4: {1: 'Down', 2: 'Up', 3: 'Total', 4: 'Ping'},
    5: {1: 'RPM', 2: 'PWM%', 3: 'Temp', 4: 'Speed'},
}

TIME_FORMATS = {0: 'HH:mm', 1: 'hh:mm AM/PM'}
DATE_FORMATS = {1: 'yyyy/MM/dd', 2: 'dd/MM/yyyy', 3: 'MM/dd', 4: 'dd/MM'}

# Background images per mode (60x60 icons)
MODE_IMAGES = {
    MODE_HARDWARE: 'P数据.png',
    MODE_TIME: 'P时间.png',
    MODE_WEEKDAY: 'P星期.png',
    MODE_DATE: 'P日期.png',
    MODE_CUSTOM: 'P文本.png',
}

SELECT_IMAGE = 'P选中.png'


def _default_element_config(mode=MODE_TIME):
    """Create a default element config dict."""
    return {
        'mode': mode,
        'mode_sub': 0,
        'x': 100,
        'y': 100,
        'main_count': 0,
        'sub_count': 1,
        'color': '#FFFFFF',
        'font_name': 'Microsoft YaHei',
        'font_size': 36,
        'font_style': 0,
        'text': '',
    }


# ============================================================================
# Overlay element widget
# ============================================================================

class OverlayElementWidget(QWidget):
    """Single overlay element cell in the 7x6 grid.

    60x60 with background image per mode type, 3 text labels,
    and selection overlay. Matches Windows UCXiTongXianShiSub.

    Windows UCXiTongXianShiSubTimer() updates cards with live data:
    - label1: category name (CPU/GPU/etc.)
    - label2: numeric value (52, 14:35, etc.)
    - label3: unit (°C, %, MHz, etc.)
    """

    clicked = pyqtSignal(int)           # index
    double_clicked = pyqtSignal(int)    # index (delete)

    # Metric key map: (main_count, sub_count) → system_info metric name
    _METRIC_MAP = {
        (0, 1): 'cpu_temp', (0, 2): 'cpu_percent', (0, 3): 'cpu_freq', (0, 4): 'cpu_power',
        (1, 1): 'gpu_temp', (1, 2): 'gpu_usage', (1, 3): 'gpu_clock', (1, 4): 'gpu_power',
        (2, 1): 'mem_percent', (2, 2): 'mem_clock',
        (3, 1): 'disk_read', (3, 2): 'disk_write',
        (4, 1): 'net_down', (4, 2): 'net_up',
        (5, 1): 'fan_rpm',
    }

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.config = None  # element config dict or None (empty slot)
        self._selected = False
        self._live_value = ''   # label2: formatted value
        self._live_unit = ''    # label3: unit suffix

        self.setFixedSize(Sizes.OVERLAY_CELL, Sizes.OVERLAY_CELL)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Preload mode images (shared via lru_cache)
        self._mode_pixmaps = {}
        for mode, img_name in MODE_IMAGES.items():
            px = load_pixmap(img_name, Sizes.OVERLAY_CELL, Sizes.OVERLAY_CELL)
            if not px.isNull():
                self._mode_pixmaps[mode] = px

        self._select_pixmap = load_pixmap(SELECT_IMAGE, Sizes.OVERLAY_CELL, Sizes.OVERLAY_CELL)

    def set_config(self, config):
        """Set element config or None to clear."""
        self.config = config
        self._live_value = ''
        self._live_unit = ''
        self.update()

    def set_selected(self, selected):
        self._selected = selected
        self.update()

    def update_metrics(self, metrics):
        """Update card with live system metrics (Windows UCXiTongXianShiSubTimer)."""
        if not self.config or self.config.get('mode') != MODE_HARDWARE:
            return
        mc = self.config.get('main_count', 0)
        sc = self.config.get('sub_count', 1)
        metric_key = self._METRIC_MAP.get((mc, sc))
        if not metric_key or metric_key not in metrics:
            return
        raw = metrics[metric_key]
        # Split formatted value into number + unit (Windows regex pattern)
        import re

        formatted = format_metric(metric_key, raw,
                                  temp_unit=self.config.get('mode_sub', 0))
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
            mode = self.config.get('mode', MODE_TIME)
            # Draw mode background
            px = self._mode_pixmaps.get(mode)
            if px:
                painter.drawPixmap(0, 0, px)
            else:
                painter.fillRect(self.rect(), QColor('#2D2D2D'))

            color_str = self.config.get('color', '#FFFFFF')

            if mode == MODE_HARDWARE:
                main_count = self.config.get('main_count', 0)
                cat_color = CATEGORY_COLORS.get(main_count, '#9375FF')
                cat_name = CATEGORY_NAMES.get(main_count, '???')

                # label1: category name in category color (Windows: label1.ForeColor)
                painter.setPen(QColor(cat_color))
                painter.drawText(2, 1, 56, 18, Qt.AlignmentFlag.AlignCenter, cat_name)
                # label2: live value or sub-metric name (Windows: label2.ForeColor = myColor)
                painter.setPen(QColor(color_str))
                if self._live_value:
                    painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                     self._live_value)
                else:
                    sub_name = SUB_METRICS.get(main_count, {}).get(
                        self.config.get('sub_count', 1), '--')
                    painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, sub_name)
                # label3: unit suffix (Windows: label3.ForeColor = myColor)
                painter.drawText(2, 41, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                 self._live_unit or '--')
            elif mode == MODE_TIME:
                # Windows: label1+label3 hidden, only label2 visible
                from datetime import datetime
                mode_sub = self.config.get('mode_sub', 0)
                if mode_sub == 1:
                    text = datetime.now().strftime('%-I:%M %p')
                else:
                    text = datetime.now().strftime('%H:%M')
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, text)
            elif mode == MODE_DATE:
                # Windows: label1+label3 hidden, only label2 visible
                from datetime import datetime
                mode_sub = self.config.get('mode_sub', 0)
                fmts = {0: '%Y/%m/%d', 1: '%Y/%m/%d', 2: '%d/%m/%Y', 3: '%m/%d', 4: '%d/%m'}
                text = datetime.now().strftime(fmts.get(mode_sub, '%Y/%m/%d'))
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, text)
            elif mode == MODE_WEEKDAY:
                # Windows: label1+label3 hidden, only label2 visible
                # Windows uses SUN=0..SAT=6 array indexed by DayOfWeek
                from datetime import datetime
                days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter,
                                 days[datetime.now().weekday()])
            elif mode == MODE_CUSTOM:
                # Windows: label1+label3 hidden, label2.Text = myText
                text = self.config.get('text', '')
                painter.setPen(QColor(color_str))
                painter.drawText(2, 21, 56, 18, Qt.AlignmentFlag.AlignCenter, text)

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


# ============================================================================
# Overlay grid panel
# ============================================================================

class OverlayGridPanel(QFrame):
    """7x6 grid of overlay elements (matches UCXiTongXianShi 472x430).

    Manages a list of element configs. Empty cells show "+".
    Has on/off toggle and "add" button at next available slot.
    """

    element_selected = pyqtSignal(int, dict)   # index, config
    element_deleted = pyqtSignal(int)           # index
    add_requested = pyqtSignal()
    elements_changed = pyqtSignal()             # any add/delete/reorder
    toggle_changed = pyqtSignal(bool)           # overlay on/off

    MAX_ELEMENTS = 42

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.OVERLAY_GRID_W, Sizes.OVERLAY_GRID_H)

        set_background_pixmap(self, 'ucXiTongXianShi1.BackgroundImage.png',
            Sizes.OVERLAY_GRID_W, Sizes.OVERLAY_GRID_H,
            fallback_style=f"background-color: {Colors.BASE_BG}; border-radius: 5px;")

        self._configs = []         # list of element config dicts
        self._selected_index = -1
        self._overlay_enabled = True
        self._cells = []           # OverlayElementWidget instances (always 42)

        self._setup_toggle()
        self._setup_cells()

    def _setup_toggle(self):
        """On/Off toggle at (5, 5) using slide switch images."""
        self._toggle_btn = QPushButton(self)
        self._toggle_btn.setGeometry(5, 5, 36, 18)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(True)

        on_px = load_pixmap('P滑动开.png', 36, 18)
        off_px = load_pixmap('P滑动关.png', 36, 18)
        if not on_px.isNull() and not off_px.isNull():
            icon = QIcon()
            icon.addPixmap(on_px, QIcon.Mode.Normal, QIcon.State.On)
            icon.addPixmap(off_px, QIcon.Mode.Normal, QIcon.State.Off)
            self._toggle_btn.setIcon(icon)
            self._toggle_btn.setIconSize(self._toggle_btn.size())
            self._toggle_btn.setStyleSheet(Styles.FLAT_BUTTON)
        else:
            self._toggle_btn.setText("ON")
            self._toggle_btn.setStyleSheet(
                "QPushButton { background: #4CAF50; color: white; font-size: 8px; }"
                "QPushButton:checked { background: #4CAF50; }"
                "QPushButton:!checked { background: #666; }"
            )

        self._toggle_btn.clicked.connect(self._on_toggle)

    def _on_toggle(self, checked):
        self._overlay_enabled = checked
        self.toggle_changed.emit(checked)
        self.elements_changed.emit()

    def _setup_cells(self):
        """Create 42 cell widgets in the 7x6 grid."""
        for row in range(Sizes.OVERLAY_ROWS):
            for col in range(Sizes.OVERLAY_COLS):
                index = row * Sizes.OVERLAY_COLS + col
                x = Sizes.OVERLAY_X0 + col * Sizes.OVERLAY_DX
                y = Sizes.OVERLAY_Y0 + row * Sizes.OVERLAY_DY

                cell = OverlayElementWidget(index, self)
                cell.setGeometry(x, y, Sizes.OVERLAY_CELL, Sizes.OVERLAY_CELL)
                cell.clicked.connect(self._on_cell_clicked)
                cell.double_clicked.connect(self._on_cell_double_clicked)
                self._cells.append(cell)

    def _refresh_cells(self):
        """Sync cell widgets with _configs list."""
        for i, cell in enumerate(self._cells):
            if i < len(self._configs):
                cell.config = self._configs[i]
            else:
                cell.config = None
            cell.set_selected(i == self._selected_index)
            cell.update()

    def _on_cell_clicked(self, index):
        # Deselect previous
        if 0 <= self._selected_index < len(self._cells):
            self._cells[self._selected_index].set_selected(False)

        if index < len(self._configs):
            # Clicked an existing element — select it
            self._selected_index = index
            self._cells[index].set_selected(True)
            self.element_selected.emit(index, self._configs[index])
        elif index == len(self._configs) and len(self._configs) < self.MAX_ELEMENTS:
            # Clicked the "+" slot — request add
            self._selected_index = -1
            self.add_requested.emit()
        else:
            self._selected_index = -1

    def _on_cell_double_clicked(self, index):
        if index < len(self._configs):
            self.delete_element(index)

    # --- Public API ---

    @property
    def overlay_enabled(self):
        return self._overlay_enabled

    def set_overlay_enabled(self, enabled: bool):
        """Programmatically set overlay enabled state (no signal emitted)."""
        self._overlay_enabled = enabled
        self._toggle_btn.blockSignals(True)
        self._toggle_btn.setChecked(enabled)
        self._toggle_btn.blockSignals(False)

    def add_element(self, config):
        """Add an element to the grid."""
        if len(self._configs) >= self.MAX_ELEMENTS:
            return
        self._configs.append(config)
        self._selected_index = len(self._configs) - 1
        self._refresh_cells()
        self.elements_changed.emit()

    def delete_element(self, index):
        """Delete element at index."""
        if 0 <= index < len(self._configs):
            self._configs.pop(index)
            if self._selected_index >= len(self._configs):
                self._selected_index = len(self._configs) - 1
            self._refresh_cells()
            self.element_deleted.emit(index)
            self.elements_changed.emit()

    def update_element(self, index, config):
        """Update config for element at index."""
        if 0 <= index < len(self._configs):
            self._configs[index] = config
            self._cells[index].set_config(config)
            self._cells[index].update()

    def get_selected_index(self):
        return self._selected_index

    def get_selected_config(self):
        if 0 <= self._selected_index < len(self._configs):
            return self._configs[self._selected_index]
        return None

    def get_all_configs(self):
        """Get all element configs as list of dicts."""
        return list(self._configs)

    def load_configs(self, configs):
        """Load element configs from list."""
        self._configs = [dict(c) for c in configs[:self.MAX_ELEMENTS]]
        self._selected_index = -1
        self._refresh_cells()

    def clear_all(self):
        self._configs.clear()
        self._selected_index = -1
        self._refresh_cells()

    def to_overlay_config(self):
        """Convert to OverlayRenderer config format."""
        if not self._overlay_enabled:
            return {}

        overlay_config = {}
        metric_map = {
            (0, 1): 'cpu_temp', (0, 2): 'cpu_percent', (0, 3): 'cpu_freq', (0, 4): 'cpu_power',
            (1, 1): 'gpu_temp', (1, 2): 'gpu_usage', (1, 3): 'gpu_clock', (1, 4): 'gpu_power',
            (2, 1): 'mem_percent', (2, 2): 'mem_clock',
            (3, 1): 'disk_read', (3, 2): 'disk_write',
            (4, 1): 'net_down', (4, 2): 'net_up',
            (5, 1): 'fan_rpm',
        }

        for i, cfg in enumerate(self._configs):
            mode = cfg.get('mode', MODE_TIME)
            entry = {
                'x': cfg.get('x', 100),
                'y': cfg.get('y', 100),
                'color': cfg.get('color', '#FFFFFF'),
                'font': {
                    'size': cfg.get('font_size', 36),
                    'style': 'bold' if cfg.get('font_style', 0) == 1 else 'regular',
                    'name': cfg.get('font_name', 'Microsoft YaHei'),
                },
                'enabled': True,
            }

            if mode == MODE_TIME:
                entry['metric'] = 'time'
                entry['time_format'] = cfg.get('mode_sub', 0)
                key = f'time_{i}'
            elif mode == MODE_DATE:
                entry['metric'] = 'date'
                entry['date_format'] = cfg.get('mode_sub', 0)
                key = f'date_{i}'
            elif mode == MODE_WEEKDAY:
                entry['metric'] = 'weekday'
                key = f'weekday_{i}'
            elif mode == MODE_CUSTOM:
                entry['text'] = cfg.get('text', '')
                key = f'custom_{i}'
            elif mode == MODE_HARDWARE:
                mc = cfg.get('main_count', 0)
                sc = cfg.get('sub_count', 1)
                entry['metric'] = metric_map.get((mc, sc), f'hw_{mc}_{sc}')
                entry['temp_unit'] = cfg.get('mode_sub', 0)
                key = f'hw_{mc}_{sc}_{i}'
            else:
                continue

            overlay_config[key] = entry

        return overlay_config

    def load_from_overlay_config(self, overlay_config):
        """Load from OverlayRenderer config format."""
        configs = []
        for key, cfg in overlay_config.items():
            if not isinstance(cfg, dict) or not cfg.get('enabled', True):
                continue
            elem = _default_element_config()
            elem['x'] = cfg.get('x', 100)
            elem['y'] = cfg.get('y', 100)
            elem['color'] = cfg.get('color', '#FFFFFF')
            font = cfg.get('font', {})
            if isinstance(font, dict):
                elem['font_size'] = font.get('size', 36)
                elem['font_style'] = 1 if font.get('style') == 'bold' else 0
                if font.get('name'):
                    elem['font_name'] = font['name']

            metric = cfg.get('metric', '')
            if metric == 'time':
                elem['mode'] = MODE_TIME
                elem['mode_sub'] = cfg.get('time_format', 0)
            elif metric == 'date':
                elem['mode'] = MODE_DATE
                elem['mode_sub'] = cfg.get('date_format', 0)
            elif metric == 'weekday':
                elem['mode'] = MODE_WEEKDAY
            elif 'text' in cfg:
                elem['mode'] = MODE_CUSTOM
                elem['text'] = cfg['text']
            elif metric.startswith('cpu'):
                elem['mode'] = MODE_HARDWARE
                elem['main_count'] = 0
                sub_map = {'cpu_temp': 1, 'cpu_percent': 2, 'cpu_freq': 3, 'cpu_power': 4}
                elem['sub_count'] = sub_map.get(metric, 1)
                elem['mode_sub'] = cfg.get('temp_unit', 0)
            elif metric.startswith('gpu'):
                elem['mode'] = MODE_HARDWARE
                elem['main_count'] = 1
                sub_map = {'gpu_temp': 1, 'gpu_usage': 2, 'gpu_clock': 3, 'gpu_power': 4}
                elem['sub_count'] = sub_map.get(metric, 1)
                elem['mode_sub'] = cfg.get('temp_unit', 0)
            elif metric.startswith('mem'):
                elem['mode'] = MODE_HARDWARE
                elem['main_count'] = 2
                elem['sub_count'] = 1
                elem['mode_sub'] = cfg.get('temp_unit', 0)
            elif metric.startswith('disk'):
                elem['mode'] = MODE_HARDWARE
                elem['main_count'] = 3
                elem['sub_count'] = 1
                elem['mode_sub'] = cfg.get('temp_unit', 0)
            elif metric.startswith('net'):
                elem['mode'] = MODE_HARDWARE
                elem['main_count'] = 4
                elem['sub_count'] = 1
                elem['mode_sub'] = cfg.get('temp_unit', 0)
            elif metric.startswith('fan'):
                elem['mode'] = MODE_HARDWARE
                elem['main_count'] = 5
                elem['sub_count'] = 1
                elem['mode_sub'] = cfg.get('temp_unit', 0)
            else:
                continue
            configs.append(elem)

        self.load_configs(configs)


# ============================================================================
# Color picker panel
# ============================================================================

class ColorPickerPanel(QFrame):
    """Color and position editor (matches UCXiTongXianShiColor 230x374)."""

    color_changed = pyqtSignal(int, int, int)
    position_changed = pyqtSignal(int, int)
    font_changed = pyqtSignal(str, int)
    eyedropper_requested = pyqtSignal()  # launch eyedropper color picker

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.COLOR_PANEL_W, Sizes.COLOR_PANEL_H)

        set_background_pixmap(self, 'ucXiTongXianShiColor1.BackgroundImage.png',
            Sizes.COLOR_PANEL_W, Sizes.COLOR_PANEL_H,
            fallback_style=f"background-color: {Colors.PANEL_FALLBACK}; border-radius: 5px;")

        self._current_color = QColor(255, 255, 255)
        self._setup_ui()

    def _setup_ui(self):
        # X coordinate input
        self.x_spin = QSpinBox(self)
        self.x_spin.setGeometry(*Layout.COLOR_X_SPIN)
        self.x_spin.setRange(0, 480)
        self.x_spin.setStyleSheet(Styles.INPUT_FIELD)
        self.x_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.x_spin.valueChanged.connect(self._on_position_changed)

        # Y coordinate input
        self.y_spin = QSpinBox(self)
        self.y_spin.setGeometry(*Layout.COLOR_Y_SPIN)
        self.y_spin.setRange(0, 480)
        self.y_spin.setStyleSheet(Styles.INPUT_FIELD)
        self.y_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.y_spin.valueChanged.connect(self._on_position_changed)

        # Font picker button
        self.font_btn = QPushButton(self)
        self.font_btn.setGeometry(*Layout.COLOR_FONT_BTN)
        self.font_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; border: none; "
            f"color: {Colors.TEXT}; font-size: 10px; text-align: left; padding-left: 27px; }}"
        )
        self.font_btn.setText("Microsoft YaHei  36")
        self.font_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.font_btn.clicked.connect(self._pick_font)
        self._current_font_name = "Microsoft YaHei"
        self._current_font_size = 36

        # Color picker area click target
        self.color_area_btn = QPushButton(self)
        self.color_area_btn.setGeometry(*Layout.COLOR_AREA)
        self.color_area_btn.setStyleSheet("background-color: transparent; border: none;")
        self.color_area_btn.setCursor(Qt.CursorShape.CrossCursor)
        self.color_area_btn.clicked.connect(self._pick_color)

        # RGB input boxes
        self.r_input = QLineEdit("255", self)
        self.r_input.setGeometry(*Layout.COLOR_R)
        self.r_input.setStyleSheet(Styles.RGB_INPUT)
        self.r_input.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.g_input = QLineEdit("255", self)
        self.g_input.setGeometry(*Layout.COLOR_G)
        self.g_input.setStyleSheet(Styles.RGB_INPUT)
        self.g_input.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.b_input = QLineEdit("255", self)
        self.b_input.setGeometry(*Layout.COLOR_B)
        self.b_input.setStyleSheet(Styles.RGB_INPUT)
        self.b_input.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for inp in (self.r_input, self.g_input, self.b_input):
            inp.editingFinished.connect(self._on_rgb_changed)

        # Preset color swatches
        for i, (r, g, b) in enumerate(Colors.PRESET_COLORS):
            btn = QPushButton(self)
            btn.setGeometry(
                Layout.COLOR_SWATCH_X0 + i * Layout.COLOR_SWATCH_DX,
                Layout.COLOR_SWATCH_PRESET_Y,
                Layout.COLOR_SWATCH_SIZE, Layout.COLOR_SWATCH_SIZE
            )
            btn.setStyleSheet(
                f"QPushButton {{ background-color: rgb({r},{g},{b}); border: none; }}"
                f"QPushButton:hover {{ border: 1px solid white; }}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, cr=r, cg=g, cb=b: self._set_color_from_swatch(cr, cg, cb))

        # History color swatches
        self._history_btns = []
        for i in range(len(Colors.PRESET_COLORS)):
            btn = QPushButton(self)
            btn.setGeometry(
                Layout.COLOR_SWATCH_X0 + i * Layout.COLOR_SWATCH_DX,
                Layout.COLOR_SWATCH_HISTORY_Y,
                Layout.COLOR_SWATCH_SIZE, Layout.COLOR_SWATCH_SIZE
            )
            btn.setStyleSheet("background-color: transparent; border: none;")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._history_btns.append(btn)

        # Eyedropper button (matches Windows buttonGetColor at (12, 276, 48, 48))
        self.eyedropper_btn = QPushButton(self)
        self.eyedropper_btn.setGeometry(*Layout.COLOR_EYEDROPPER)
        eyedrop_pixmap = load_pixmap('P吸管.png', 48, 48)
        if not eyedrop_pixmap.isNull():
            self.eyedropper_btn.setIcon(QIcon(eyedrop_pixmap))
            self.eyedropper_btn.setIconSize(self.eyedropper_btn.size())
        self.eyedropper_btn.setFlat(True)
        self.eyedropper_btn.setStyleSheet(Styles.ICON_BUTTON_HOVER)
        self.eyedropper_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eyedropper_btn.setToolTip("Pick color from screen")
        self.eyedropper_btn.clicked.connect(self.eyedropper_requested.emit)

    def _pick_color(self):
        color = QColorDialog.getColor(self._current_color, self, "Pick Color")
        if color.isValid():
            self._apply_color(color.red(), color.green(), color.blue())

    def _on_rgb_changed(self):
        try:
            r = max(0, min(255, int(self.r_input.text())))
            g = max(0, min(255, int(self.g_input.text())))
            b = max(0, min(255, int(self.b_input.text())))
            self._apply_color(r, g, b)
        except ValueError:
            pass

    def _set_color_from_swatch(self, r, g, b):
        self._apply_color(r, g, b)

    def _apply_color(self, r, g, b):
        self._current_color = QColor(r, g, b)
        self.r_input.setText(str(r))
        self.g_input.setText(str(g))
        self.b_input.setText(str(b))
        self.color_changed.emit(r, g, b)

    def _on_position_changed(self):
        self.position_changed.emit(self.x_spin.value(), self.y_spin.value())

    def set_color(self, r, g, b):
        self._current_color = QColor(r, g, b)
        self.r_input.setText(str(r))
        self.g_input.setText(str(g))
        self.b_input.setText(str(b))

    def set_color_hex(self, hex_color):
        """Set color from hex string like '#FF0000'."""
        c = QColor(hex_color)
        if c.isValid():
            self.set_color(c.red(), c.green(), c.blue())

    def set_position(self, x, y):
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.x_spin.setValue(x)
        self.y_spin.setValue(y)
        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)

    def _pick_font(self):
        """Open font dialog (matches Windows FontDialog in UCXiTongXianShiColor)."""
        from PyQt6.QtWidgets import QFontDialog
        current = QFont(self._current_font_name, self._current_font_size)
        font, ok = QFontDialog.getFont(current, self, "Pick Font")
        if ok:
            self._current_font_name = font.family()
            self._current_font_size = font.pointSize()
            self.font_btn.setText(f"{font.family()}  {font.pointSize()}")
            self.font_changed.emit(font.family(), font.pointSize())

    def set_font_display(self, font_name, font_size):
        self._current_font_name = font_name
        self._current_font_size = font_size
        self.font_btn.setText(f"{font_name}  {font_size}")


# ============================================================================
# Add element panel
# ============================================================================

class AddElementPanel(QFrame):
    """Add new overlay element panel (matches UCXiTongXianShiAdd 230x430)."""

    element_added = pyqtSignal(dict)  # full config dict

    ELEMENT_TYPES = [
        ("Hardware Data", MODE_HARDWARE),
        ("Time", MODE_TIME),
        ("Weekday", MODE_WEEKDAY),
        ("Date", MODE_DATE),
        ("Custom Text", MODE_CUSTOM),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.ADD_PANEL_W, Sizes.ADD_PANEL_H)

        set_background_pixmap(self, 'ucXiTongXianShiAdd1.BackgroundImage.png',
            Sizes.ADD_PANEL_W, Sizes.ADD_PANEL_H,
            fallback_style=f"background-color: {Colors.PANEL_FALLBACK}; border-radius: 5px;")

        self._setup_ui()

    def _setup_ui(self):
        y = Layout.ADD_BTN_Y0
        for name, mode in self.ELEMENT_TYPES:
            btn = QPushButton(name, self)
            btn.setGeometry(Layout.ADD_BTN_X, y, Layout.ADD_BTN_W, Layout.ADD_BTN_H)
            btn.setStyleSheet(Styles.ADD_ELEMENT_BTN)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, m=mode: self._on_type_clicked(m))
            y += Layout.ADD_BTN_DY

        # Hardware category combo
        self.hw_frame = QFrame(self)
        self.hw_frame.setGeometry(Layout.ADD_BTN_X, y + 10, Layout.ADD_BTN_W, 100)
        self.hw_frame.setStyleSheet("background: transparent;")

        hw_layout = QVBoxLayout(self.hw_frame)
        hw_layout.setContentsMargins(0, 0, 0, 0)

        hw_label = QLabel("Category:")
        hw_label.setStyleSheet(f"color: {Colors.STATUS_TEXT}; font-size: 10px; background: transparent;")
        hw_layout.addWidget(hw_label)

        self.hw_combo = QComboBox()
        self.hw_combo.addItems(list(CATEGORY_NAMES.values()))
        self.hw_combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(51, 51, 51, 180);
                color: white; border: 1px solid #555; padding: 5px;
            }
        """)
        hw_layout.addWidget(self.hw_combo)

        metric_label = QLabel("Metric:")
        metric_label.setStyleSheet(f"color: {Colors.STATUS_TEXT}; font-size: 10px; background: transparent;")
        hw_layout.addWidget(metric_label)

        self.metric_combo = QComboBox()
        self.metric_combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(51, 51, 51, 180);
                color: white; border: 1px solid #555; padding: 5px;
            }
        """)
        hw_layout.addWidget(self.metric_combo)

        self.hw_combo.currentIndexChanged.connect(self._on_category_changed)
        self._on_category_changed(0)

        self.hw_frame.setVisible(False)

    def _on_category_changed(self, idx):
        self.metric_combo.clear()
        metrics = SUB_METRICS.get(idx, {})
        self.metric_combo.addItems(list(metrics.values()))

    def _on_type_clicked(self, mode):
        cfg = _default_element_config(mode)

        if mode == MODE_HARDWARE:
            self.hw_frame.setVisible(True)
            cat_idx = self.hw_combo.currentIndex()
            metric_idx = self.metric_combo.currentIndex()
            cfg['main_count'] = cat_idx
            cfg['sub_count'] = metric_idx + 1
            cfg['color'] = CATEGORY_COLORS.get(cat_idx, '#FFFFFF')
        else:
            self.hw_frame.setVisible(False)

        self.element_added.emit(cfg)


# ============================================================================
# Data table panel
# ============================================================================

class DataTablePanel(QFrame):
    """Data selection table (matches UCXiTongXianShiTable 230x54).

    Windows shows different controls depending on the selected element mode:
    - Hardware (mode 0): button0 — C/F unit toggle (P单位开关.png / P单位开关a.png)
    - Time    (mode 1): button1 — 12H/24H toggle (P12H.png / P24H.png)
    - Weekday (mode 2): no controls
    - Date    (mode 3): button3 — date format cycle (PYMD→PDMY→PMD→PDM)
    - Custom  (mode 4): textBox1 — custom text input
    """

    format_changed = pyqtSignal(int, int)  # mode, mode_sub
    text_changed = pyqtSignal(str)

    # Date format images in cycle order (mode_sub 1→2→3→4→1)
    _DATE_IMAGES = {1: 'PYMD.png', 2: 'PDMY.png', 3: 'PMD.png', 4: 'PDM.png'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.DATA_TABLE_W, Sizes.DATA_TABLE_H)

        set_background_pixmap(self, 'ucXiTongXianShiTable1.BackgroundImage.png',
            Sizes.DATA_TABLE_W, Sizes.DATA_TABLE_H,
            fallback_style=f"background-color: {Colors.PANEL_FALLBACK}; border-radius: 5px;")

        # button0 — C/F unit toggle (mode 0: hardware)
        # Windows: (80, 15) 70x24
        self.unit_btn = QPushButton(self)
        self.unit_btn.setGeometry(80, 15, 70, 24)
        self.unit_btn.setFlat(True)
        self.unit_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.unit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unit_off = load_pixmap('P单位开关.png', 70, 24)   # °C
        self._unit_on = load_pixmap('P单位开关a.png', 70, 24)   # °F
        self.unit_btn.clicked.connect(self._on_unit_clicked)
        self.unit_btn.setVisible(False)

        # button1 — 12H/24H toggle (mode 1: time)
        # Windows: (88, 16) 54x22
        self.time_btn = QPushButton(self)
        self.time_btn.setGeometry(88, 16, 54, 22)
        self.time_btn.setFlat(True)
        self.time_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.time_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._time_12h = load_pixmap('P12H.png', 54, 22)
        self._time_24h = load_pixmap('P24H.png', 54, 22)
        self.time_btn.clicked.connect(self._on_time_clicked)
        self.time_btn.setVisible(False)

        # button3 — date format cycle (mode 3: date)
        # Windows: (88, 16) 54x22
        self.date_btn = QPushButton(self)
        self.date_btn.setGeometry(88, 16, 54, 22)
        self.date_btn.setFlat(True)
        self.date_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.date_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._date_pixmaps = {
            k: load_pixmap(v, 54, 22) for k, v in self._DATE_IMAGES.items()
        }
        self.date_btn.clicked.connect(self._on_date_clicked)
        self.date_btn.setVisible(False)

        # textBox1 — custom text input (mode 4: custom)
        # Windows: (15, 15) 200x22
        self.text_input = QLineEdit(self)
        self.text_input.setGeometry(15, 15, 200, 22)
        self.text_input.setStyleSheet(Styles.INPUT_FIELD)
        self.text_input.setPlaceholderText("Text...")
        self.text_input.setMaxLength(100)
        self.text_input.editingFinished.connect(
            lambda: self.text_changed.emit(self.text_input.text()))
        self.text_input.setVisible(False)

        self._current_mode = -1
        self._mode_sub = 0

    def _hide_all(self):
        self.unit_btn.setVisible(False)
        self.time_btn.setVisible(False)
        self.date_btn.setVisible(False)
        self.text_input.setVisible(False)

    def _update_unit_image(self):
        px = self._unit_on if self._mode_sub else self._unit_off
        if not px.isNull():
            self.unit_btn.setIcon(QIcon(px))
            self.unit_btn.setIconSize(self.unit_btn.size())

    def _update_time_image(self):
        # mode_sub 1 = 12H (hh:mm AM/PM), else 24H (HH:mm)
        px = self._time_12h if self._mode_sub == 1 else self._time_24h
        if not px.isNull():
            self.time_btn.setIcon(QIcon(px))
            self.time_btn.setIconSize(self.time_btn.size())

    def _update_date_image(self):
        px = self._date_pixmaps.get(self._mode_sub)
        if px and not px.isNull():
            self.date_btn.setIcon(QIcon(px))
            self.date_btn.setIconSize(self.date_btn.size())

    def set_mode(self, mode, mode_sub=0):
        """Show the appropriate control for the selected element mode."""
        self._current_mode = mode
        self._mode_sub = mode_sub
        self._hide_all()

        if mode == MODE_HARDWARE:
            self._update_unit_image()
            self.unit_btn.setVisible(True)
        elif mode == MODE_TIME:
            self._update_time_image()
            self.time_btn.setVisible(True)
        elif mode == MODE_WEEKDAY:
            pass  # No controls
        elif mode == MODE_DATE:
            if self._mode_sub == 0:
                self._mode_sub = 1  # Default to PYMD
            self._update_date_image()
            self.date_btn.setVisible(True)
        elif mode == MODE_CUSTOM:
            self.text_input.setVisible(True)

    def _on_unit_clicked(self):
        """Toggle C/F: mode_sub 0↔1."""
        self._mode_sub = 0 if self._mode_sub else 1
        self._update_unit_image()
        self.format_changed.emit(self._current_mode, self._mode_sub)

    def _on_time_clicked(self):
        """Toggle 12H/24H: mode_sub 1↔2 (Windows: 1=12H shows P12H, else P24H)."""
        self._mode_sub = 2 if self._mode_sub == 1 else 1
        self._update_time_image()
        self.format_changed.emit(self._current_mode, self._mode_sub)

    def _on_date_clicked(self):
        """Cycle date format: 1→2→3→4→1 (PYMD→PDMY→PMD→PDM)."""
        self._mode_sub = (self._mode_sub % 4) + 1
        self._update_date_image()
        self.format_changed.emit(self._current_mode, self._mode_sub)


# ============================================================================
# Display mode panel
# ============================================================================

class DisplayModePanel(QFrame):
    """Display mode toggle panel (351x100).

    Background image (localized P01) provides labels.
    Controls are invisible click targets over baked-in text.
    """

    mode_changed = pyqtSignal(str, bool)
    action_requested = pyqtSignal(str)

    def __init__(self, mode_id, actions: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.mode_id = mode_id
        self.actions: list[str] = actions or []

        self.setFixedSize(Sizes.DISPLAY_MODE_W, Sizes.DISPLAY_MODE_H)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.PANEL_FALLBACK))
        self.setPalette(palette)

        self._setup_ui()

    def _setup_ui(self):
        # Toggle button — smaller slider for mask panel, large toggle for others
        self.toggle_btn = QPushButton(self)
        if self.mode_id == 'mask':
            self.toggle_btn.setGeometry(*Layout.TOGGLE_MASK)
            on_px = load_pixmap('P滑动开.png', 36, 18)
            off_px = load_pixmap('P滑动关.png', 36, 18)
        else:
            self.toggle_btn.setGeometry(*Layout.TOGGLE_DEFAULT)
            on_px = load_pixmap('P功能选择a.png', 50, 50)
            off_px = load_pixmap('P功能选择.png', 50, 50)

        self.toggle_btn.setCheckable(True)
        if not on_px.isNull() and not off_px.isNull():
            icon = QIcon()
            icon.addPixmap(on_px, QIcon.Mode.Normal, QIcon.State.On)
            icon.addPixmap(off_px, QIcon.Mode.Normal, QIcon.State.Off)
            self.toggle_btn.setIcon(icon)
            self.toggle_btn.setIconSize(self.toggle_btn.size())
        self.toggle_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._on_toggle)

        # Action buttons with icon images
        _ICON_MAP = {
            "Image": "P图片.png", "Video": "P视频.png",
            "Load": "P蒙板.png", "VideoLoad": "P直播视频载入.png",
            "GIF": "P动画.png", "Network": "P网络.png",
        }
        action_positions = [Layout.ACTION_BTN_1, Layout.ACTION_BTN_2]
        for i, action_name in enumerate(self.actions):
            if i >= len(action_positions):
                break
            btn = QPushButton(self)
            btn.setGeometry(*action_positions[i])
            icon_name = _ICON_MAP.get(action_name)
            if icon_name:
                px = load_pixmap(icon_name, 40, 40)
                if not px.isNull():
                    btn.setIcon(QIcon(px))
                    btn.setIconSize(btn.size())
            btn.setStyleSheet(Styles.FLAT_BUTTON_HOVER)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, a=action_name: self.action_requested.emit(a))

    def _on_toggle(self, checked):
        self.mode_changed.emit(self.mode_id, checked)

    def set_enabled(self, enabled):
        self.toggle_btn.setChecked(enabled)

    def set_background_image(self, pixmap):
        """Apply P01 localized background via QPalette (not stylesheet)."""
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.width(), self.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            set_background_pixmap(self, scaled)


class ScreenCastPanel(DisplayModePanel):
    """Screen cast panel with X/Y/W/H coordinate inputs.

    Extends DisplayModePanel with coordinate entry fields, +/- buttons,
    border toggle, and aspect ratio locking.

    Matches Windows UCTouPingXianShi layout within 351x100.
    """

    screencast_params_changed = pyqtSignal(int, int, int, int)  # x, y, w, h
    border_toggled = pyqtSignal(bool)

    # Positions from Windows UCTouPingXianShi.cs
    _TEXTBOX_X = (110, 40, 56, 16)
    _TEXTBOX_Y = (110, 65, 56, 16)
    _TEXTBOX_W = (241, 40, 56, 16)
    _TEXTBOX_H = (241, 65, 56, 16)

    _BTN_ADD_X = (171, 42, 14, 14)
    _BTN_SUB_X = (189, 42, 14, 14)
    _BTN_ADD_Y = (171, 67, 14, 14)
    _BTN_SUB_Y = (189, 67, 14, 14)
    _BTN_ADD_W = (301, 42, 14, 14)
    _BTN_SUB_W = (319, 42, 14, 14)
    _BTN_ADD_H = (301, 67, 14, 14)
    _BTN_SUB_H = (319, 67, 14, 14)

    _BTN_BORDER = (309, 16, 24, 16)

    _ENTRY_STYLE = (
        "background-color: black; color: #B4964F; border: none;"
        " font-family: 'Microsoft YaHei'; font-size: 9pt;"
    )

    # Aspect ratios per resolution
    _ASPECT_RATIOS = {
        (240, 240): 1.0, (320, 320): 1.0, (360, 360): 1.0, (480, 480): 1.0,
        (640, 480): 0.75, (800, 480): 0.6, (854, 480): 0.5621,
        (960, 540): 0.5625, (1280, 480): 0.375, (1600, 720): 0.45,
        (1920, 462): 77.0 / 320.0,
    }

    capture_requested = pyqtSignal()  # launch screen capture

    def __init__(self, parent=None):
        super().__init__("screencast", [], parent)
        self._updating = False
        self._show_border = True
        self._aspect_lock = True
        self._resolution = (320, 320)
        self._setup_screencast_ui()

    def _setup_screencast_ui(self):
        """Add coordinate inputs on top of base DisplayModePanel."""
        # X/Y/W/H entries
        self.entry_x = self._make_entry(*self._TEXTBOX_X)
        self.entry_y = self._make_entry(*self._TEXTBOX_Y)
        self.entry_w = self._make_entry(*self._TEXTBOX_W)
        self.entry_h = self._make_entry(*self._TEXTBOX_H)

        self.entry_x.textChanged.connect(lambda: self._on_coord_changed('x'))
        self.entry_y.textChanged.connect(lambda: self._on_coord_changed('y'))
        self.entry_w.textChanged.connect(lambda: self._on_coord_changed('w'))
        self.entry_h.textChanged.connect(lambda: self._on_coord_changed('h'))

        # +/- buttons
        self._make_pm_btn(*self._BTN_ADD_X, +1, self.entry_x)
        self._make_pm_btn(*self._BTN_SUB_X, -1, self.entry_x)
        self._make_pm_btn(*self._BTN_ADD_Y, +1, self.entry_y)
        self._make_pm_btn(*self._BTN_SUB_Y, -1, self.entry_y)
        self._make_pm_btn(*self._BTN_ADD_W, +1, self.entry_w)
        self._make_pm_btn(*self._BTN_SUB_W, -1, self.entry_w)
        self._make_pm_btn(*self._BTN_ADD_H, +1, self.entry_h)
        self._make_pm_btn(*self._BTN_SUB_H, -1, self.entry_h)

        # Border toggle button
        self.border_btn = QPushButton(self)
        self.border_btn.setGeometry(*self._BTN_BORDER)
        self.border_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.border_btn.clicked.connect(self._on_border_toggle)
        self._update_border_icon()

    def _make_entry(self, x, y, w, h):
        """Create a coordinate entry field."""
        entry = QLineEdit(self)
        entry.setGeometry(x, y, w, h)
        entry.setText("0")
        entry.setAlignment(Qt.AlignmentFlag.AlignRight)
        entry.setStyleSheet(self._ENTRY_STYLE)
        # Numeric-only: accept 0-9999
        from PyQt6.QtGui import QIntValidator
        entry.setValidator(QIntValidator(0, 9999, entry))
        return entry

    def _make_pm_btn(self, x, y, w, h, delta, entry):
        """Create a +/- button for a coordinate."""
        btn = QPushButton(self)
        btn.setGeometry(x, y, w, h)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        img_name = 'P加.png' if delta > 0 else 'P减.png'
        pix = load_pixmap(img_name, w, h)
        if not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(btn.size())
            btn.setStyleSheet(Styles.FLAT_BUTTON)
        else:
            btn.setText("+" if delta > 0 else "-")
            btn.setStyleSheet(
                "QPushButton { background: #333; color: #888; border: none; font-size: 9px; }"
            )
        btn.clicked.connect(lambda: self._increment(entry, delta))

    def _increment(self, entry, delta):
        """Increment/decrement an entry value."""
        try:
            val = max(0, min(9999, int(entry.text() or '0') + delta))
            entry.setText(str(val))
        except ValueError:
            pass

    def _on_coord_changed(self, which):
        """Handle coordinate value change with aspect ratio locking."""
        if self._updating:
            return
        try:
            val = int(getattr(self, f'entry_{which}').text() or '0')
        except ValueError:
            return

        if self._aspect_lock and which in ('w', 'h'):
            ratio = self._get_aspect_ratio()
            self._updating = True
            if which == 'w' and ratio != 1.0:
                h = int(val / ratio)
                self.entry_h.setText(str(h))
            elif which == 'h' and ratio != 1.0:
                w = int(val * ratio)
                self.entry_w.setText(str(w))
            self._updating = False

        self._emit_params()

    def _emit_params(self):
        """Emit all four coordinate values."""
        try:
            x = int(self.entry_x.text() or '0')
            y = int(self.entry_y.text() or '0')
            w = int(self.entry_w.text() or '0')
            h = int(self.entry_h.text() or '0')
            self.screencast_params_changed.emit(x, y, w, h)
        except ValueError:
            pass

    def _get_aspect_ratio(self):
        return self._ASPECT_RATIOS.get(self._resolution, 0.75)

    def _on_border_toggle(self):
        self._show_border = not self._show_border
        self._update_border_icon()
        self.border_toggled.emit(self._show_border)

    def _update_border_icon(self):
        img = 'P显示边框A.png' if self._show_border else 'P显示边框.png'
        pix = load_pixmap(img, 24, 16)
        if not pix.isNull():
            self.border_btn.setIcon(QIcon(pix))
            self.border_btn.setIconSize(self.border_btn.size())
            self.border_btn.setStyleSheet(Styles.FLAT_BUTTON)
        else:
            self.border_btn.setText("B" if self._show_border else "b")
            self.border_btn.setStyleSheet(
                "QPushButton { background: #00CED1; color: white; border: none; font-size: 8px; }"
                if self._show_border else
                "QPushButton { background: #555; color: white; border: none; font-size: 8px; }"
            )

    def set_values(self, x=None, y=None, w=None, h=None):
        """Set coordinate values without triggering events."""
        self._updating = True
        if x is not None:
            self.entry_x.setText(str(x))
        if y is not None:
            self.entry_y.setText(str(y))
        if w is not None:
            self.entry_w.setText(str(w))
        if h is not None:
            self.entry_h.setText(str(h))
        self._updating = False

    def set_resolution(self, width, height):
        """Set LCD resolution for aspect ratio calculations."""
        self._resolution = (width, height)

    def set_aspect_lock(self, enabled):
        self._aspect_lock = enabled

    def set_border_visible(self, visible):
        self._show_border = visible
        self._update_border_icon()


# ============================================================================
# Main settings container
# ============================================================================

class UCThemeSetting(BasePanel):
    """
    Settings container with overlay editor and display mode panels.

    Windows size: 732x661
    Uses absolute positioning matching Windows UCThemeSetting layout.
    """

    CMD_BACKGROUND_TOGGLE = 1
    CMD_BACKGROUND_LOAD_IMAGE = 49
    CMD_BACKGROUND_LOAD_VIDEO = 50
    CMD_SCREENCAST_TOGGLE = 2
    CMD_VIDEO_TOGGLE = 3
    CMD_MASK_TOGGLE = 96
    CMD_MASK_LOAD = 97
    CMD_MASK_RESET = 99
    CMD_VIDEO_LOAD = 10
    CMD_OVERLAY_CHANGED = 128
    CMD_EYEDROPPER = 112  # Matches Windows cmd for FormGetColor

    overlay_changed = pyqtSignal(dict)
    background_changed = pyqtSignal(bool)
    screencast_changed = pyqtSignal(bool)
    screencast_params_changed = pyqtSignal(int, int, int, int)  # x, y, w, h
    eyedropper_requested = pyqtSignal()  # launch eyedropper color picker
    capture_requested = pyqtSignal()     # launch screen capture

    def __init__(self, parent=None):
        super().__init__(parent, width=Sizes.SETTING_W, height=Sizes.SETTING_H)
        self._setup_ui()

    def _setup_ui(self):
        """Build UI with absolute positioning matching Windows."""
        # Overlay grid
        self.overlay_grid = OverlayGridPanel(self)
        self.overlay_grid.move(*Layout.OVERLAY_GRID)
        self.overlay_grid.element_selected.connect(self._on_element_selected)
        self.overlay_grid.add_requested.connect(self._on_add_requested)
        self.overlay_grid.element_deleted.connect(self._on_element_deleted)
        self.overlay_grid.elements_changed.connect(self._on_elements_changed)

        # Right panel stack — Color picker and Add element share this spot
        self.right_stack = QStackedWidget(self)
        self.right_stack.setGeometry(*Layout.RIGHT_STACK)

        self.color_panel = ColorPickerPanel()
        self.color_panel.color_changed.connect(self._on_color_changed)
        self.color_panel.position_changed.connect(self._on_position_changed)
        self.color_panel.font_changed.connect(self._on_font_changed)
        self.color_panel.eyedropper_requested.connect(self.eyedropper_requested.emit)
        self.right_stack.addWidget(self.color_panel)

        self.add_panel = AddElementPanel()
        self.add_panel.element_added.connect(self._on_element_added)
        self.right_stack.addWidget(self.add_panel)

        self.right_stack.setCurrentWidget(self.color_panel)

        # Data table
        self.data_table = DataTablePanel(self)
        self.data_table.move(*Layout.DATA_TABLE)
        self.data_table.format_changed.connect(self._on_format_changed)
        self.data_table.text_changed.connect(self._on_text_changed)

        # Display mode panels
        self.mask_panel = DisplayModePanel("mask", ["Load", "Clear"], self)
        self.mask_panel.move(*Layout.MASK_PANEL)
        self.mask_panel.mode_changed.connect(self._on_mode_changed)
        self.mask_panel.action_requested.connect(self._on_action_requested)

        self.background_panel = DisplayModePanel("background", ["Image", "Video"], self)
        self.background_panel.move(*Layout.BG_PANEL)
        self.background_panel.mode_changed.connect(self._on_mode_changed)
        self.background_panel.action_requested.connect(self._on_action_requested)

        self.screencast_panel = ScreenCastPanel(self)
        self.screencast_panel.move(*Layout.SCREENCAST_PANEL)
        self.screencast_panel.mode_changed.connect(self._on_mode_changed)
        self.screencast_panel.screencast_params_changed.connect(self._on_screencast_params)
        self.screencast_panel.capture_requested.connect(self.capture_requested.emit)

        self.video_panel = DisplayModePanel("video", ["VideoLoad"], self)
        self.video_panel.move(*Layout.VIDEO_PANEL)
        self.video_panel.mode_changed.connect(self._on_mode_changed)
        self.video_panel.action_requested.connect(self._on_action_requested)

    # --- Element selection / editing ---

    def _on_element_selected(self, index, config):
        """Element was clicked — show its properties in color panel."""
        self.right_stack.setCurrentWidget(self.color_panel)
        self.color_panel.set_position(config.get('x', 0), config.get('y', 0))
        color_str = config.get('color', '#FFFFFF')
        self.color_panel.set_color_hex(color_str)
        self.color_panel.set_font_display(
            config.get('font_name', 'Microsoft YaHei'),
            config.get('font_size', 36))
        self.data_table.set_mode(config.get('mode', MODE_TIME), config.get('mode_sub', 0))
        if config.get('mode') == MODE_CUSTOM:
            self.data_table.text_input.setText(config.get('text', ''))

    def _on_add_requested(self):
        """Empty cell clicked — show add panel."""
        self.right_stack.setCurrentWidget(self.add_panel)

    def _on_element_added(self, config):
        """New element type selected from add panel."""
        self.right_stack.setCurrentWidget(self.color_panel)
        self.overlay_grid.add_element(config)
        # Select the newly added element
        idx = len(self.overlay_grid.get_all_configs()) - 1
        cfg = self.overlay_grid.get_selected_config()
        if cfg:
            self._on_element_selected(idx, cfg)

    def _on_element_deleted(self, index):
        """Element was deleted."""
        self.right_stack.setCurrentWidget(self.color_panel)

    def _on_elements_changed(self):
        """Any change to elements list — notify parent via delegate."""
        config = self.overlay_grid.to_overlay_config()
        self.invoke_delegate(self.CMD_OVERLAY_CHANGED, config)

    def _update_selected(self, require_mode: int | None = None, **fields):
        """Update selected overlay element config fields and propagate.

        Single entry point for all element property changes (color, position,
        font, format, text). Guards on require_mode when the update only
        applies to a specific element type.
        """
        idx = self.overlay_grid.get_selected_index()
        cfg = self.overlay_grid.get_selected_config()
        if cfg is None:
            return
        if require_mode is not None and cfg.get('mode') != require_mode:
            return
        cfg.update(fields)
        self.overlay_grid.update_element(idx, cfg)
        self._on_elements_changed()

    def _on_color_changed(self, r, g, b):
        self._update_selected(color=f'#{r:02x}{g:02x}{b:02x}')

    def _on_position_changed(self, x, y):
        self._update_selected(x=x, y=y)

    def _on_font_changed(self, font_name, font_size):
        self._update_selected(font_name=font_name, font_size=font_size)

    def _on_format_changed(self, mode, mode_sub):
        self._update_selected(require_mode=mode, mode_sub=mode_sub)
        # Persist format preference so it carries across theme changes
        from ..conf import save_format_pref
        if mode == MODE_TIME:
            save_format_pref('time_format', mode_sub)
        elif mode == MODE_DATE:
            save_format_pref('date_format', mode_sub)
        elif mode == MODE_HARDWARE:
            save_format_pref('temp_unit', mode_sub)

    def _on_text_changed(self, text):
        self._update_selected(require_mode=MODE_CUSTOM, text=text)

    # --- Display mode panels ---

    def _on_mode_changed(self, mode_id, enabled):
        if mode_id == "background":
            if enabled:
                self.screencast_panel.set_enabled(False)
                self.video_panel.set_enabled(False)
            self.background_changed.emit(enabled)
            self.invoke_delegate(self.CMD_BACKGROUND_TOGGLE, enabled)
        elif mode_id == "screencast":
            if enabled:
                self.background_panel.set_enabled(False)
                self.video_panel.set_enabled(False)
            self.screencast_changed.emit(enabled)
            self.invoke_delegate(self.CMD_SCREENCAST_TOGGLE, enabled)
        elif mode_id == "video":
            if enabled:
                self.background_panel.set_enabled(False)
                self.screencast_panel.set_enabled(False)
            self.invoke_delegate(self.CMD_VIDEO_TOGGLE, enabled)
        elif mode_id == "mask":
            self.invoke_delegate(self.CMD_MASK_TOGGLE, enabled)

    def _on_screencast_params(self, x, y, w, h):
        """Forward screencast coordinate changes."""
        self.screencast_params_changed.emit(x, y, w, h)

    def _on_action_requested(self, action_name):
        action_map = {
            "Image": self.CMD_BACKGROUND_LOAD_IMAGE,
            "Video": self.CMD_BACKGROUND_LOAD_VIDEO,
            "GIF": 51,
            "Load": self.CMD_MASK_LOAD,
            "VideoLoad": self.CMD_VIDEO_LOAD,
            "Clear": self.CMD_MASK_RESET,
            "Settings": 65,
        }
        cmd = action_map.get(action_name)
        if cmd:
            self.invoke_delegate(cmd)

    # --- Public API ---

    def get_all_configs(self):
        return self.overlay_grid.get_all_configs()

    def load_configs(self, configs):
        self.overlay_grid.load_configs(configs)

    def to_overlay_config(self):
        return self.overlay_grid.to_overlay_config()

    def load_from_overlay_config(self, overlay_config):
        self.overlay_grid.load_from_overlay_config(overlay_config)

    def set_overlay_enabled(self, enabled: bool):
        self.overlay_grid.set_overlay_enabled(enabled)

    def set_resolution(self, width: int, height: int):
        """Delegate resolution to screencast panel."""
        self.screencast_panel.set_resolution(width, height)
