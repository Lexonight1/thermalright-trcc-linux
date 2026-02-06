"""
PyQt6 UCSystemInfo - System monitoring dashboard with sensor customization.

Displays configurable hardware monitoring panels (CPU, GPU, Memory, HDD, Network,
Fan, Custom) with live system metrics and sensor selection.

Matches Windows TRCC UCSystemInfoOptions:
- Grid: 4 columns, startX=44, startY=36, addX=300, addY=199
- Each UCSystemInfoOptionsOne: 266x189 panel with 4 metric rows
- Selector buttons (↓) to open sensor picker per row
- Add (+) button to add custom panels
- Page navigation for >12 panels
- Config persistence via sysinfo_config.json
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPalette
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton, QWidget

from ..sensor_enumerator import SensorEnumerator
from ..sysinfo_config import (
    CATEGORY_COLORS,
    CATEGORY_IMAGES,
    PanelConfig,
    SensorBinding,
    SysInfoConfig,
)
from .assets import load_pixmap
from .constants import Colors

# Grid layout (from Windows UCSystemInfoOptions.cs)
PANEL_W = 266
PANEL_H = 189
START_X = 44
START_Y = 36
SPACING_X = 300
SPACING_Y = 199
COLUMNS = 4
ROWS_PER_PAGE = 3
PANELS_PER_PAGE = COLUMNS * ROWS_PER_PAGE  # 12

# Row positions within each panel (value label x, selector button)
VALUE_POSITIONS = [
    (240, 52),   # Row 0
    (240, 86),   # Row 1
    (240, 121),  # Row 2
    (240, 156),  # Row 3
]

SELECTOR_POSITIONS = [
    (245, 49, 16, 30),
    (245, 84, 16, 30),
    (245, 119, 16, 30),
    (245, 154, 16, 30),
]

# Page nav button positions (bottom center)
PAGE_PREV_POS = (570, 650, 64, 24)
PAGE_NEXT_POS = (650, 650, 64, 24)


class SystemInfoPanel(QWidget):
    """Single hardware monitoring panel (matches Windows UCSystemInfoOptionsOne).

    Background is a pre-rendered PNG (Acpu.png etc.).
    Values are overlaid as QLabels at correct positions.
    Selector buttons (↓) open the sensor picker per row.
    """

    clicked = pyqtSignal(object)                     # self
    sensor_select_requested = pyqtSignal(object, int)  # (self, row_index)
    delete_requested = pyqtSignal(object)              # self
    name_changed = pyqtSignal(object, str)             # (self, new_name)

    def __init__(self, config: PanelConfig, parent=None):
        super().__init__(parent)
        self.setFixedSize(PANEL_W, PANEL_H)

        self.config = config
        self._selected = False
        self._temp_unit = 0  # 0=Celsius, 1=Fahrenheit
        self._color = CATEGORY_COLORS.get(config.category_id, '#888888')
        self._value_labels: list[QLabel] = []
        self._selector_btns: list[QPushButton] = []

        # Load background image
        img_name = CATEGORY_IMAGES.get(config.category_id, 'A自定义.png')
        self._bg_pixmap = load_pixmap(img_name, PANEL_W, PANEL_H)
        if not self._bg_pixmap.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(self._bg_pixmap))
            self.setPalette(palette)
            self.setAutoFillBackground(True)

        # Load selector button image
        self._sel_pixmap = load_pixmap('A数据选择.png', 16, 30)

        # Value labels and selector buttons (row labels are baked into the PNG)
        value_font = QFont('Arial', 10)

        for i in range(4):
            # Value label (right-aligned, shows live readings)
            vx, vy = VALUE_POSITIONS[i]
            vlbl = QLabel('--', self)
            vlbl.setFont(value_font)
            vlbl.setStyleSheet(f"color: {self._color}; background: transparent;")
            vlbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vlbl.setGeometry(vx - 100, vy, 100, 20)
            self._value_labels.append(vlbl)

            # Selector button (↓)
            sx, sy, sw, sh = SELECTOR_POSITIONS[i]
            sel = QPushButton(self)
            sel.setGeometry(sx, sy, sw, sh)
            sel.setFlat(True)
            sel.setStyleSheet(
                "QPushButton { background: transparent; border: none; }"
                "QPushButton:hover { background: rgba(255,255,255,20); }"
            )
            sel.setCursor(Qt.CursorShape.PointingHandCursor)
            if not self._sel_pixmap.isNull():
                sel.setIcon(QIcon(self._sel_pixmap))
                sel.setIconSize(QSize(16, 30))
            else:
                sel.setText("↓")
                sel.setStyleSheet(
                    "QPushButton { background: transparent; border: none; "
                    "color: #888; font-size: 16px; }"
                    "QPushButton:hover { color: white; }"
                )
            row_idx = i
            sel.clicked.connect(lambda checked, r=row_idx: self.sensor_select_requested.emit(self, r))
            self._selector_btns.append(sel)

        # Delete button for custom panels (category_id=0)
        if config.category_id == 0:
            self._del_btn = QPushButton("✕", self)
            self._del_btn.setGeometry(245, 5, 16, 16)
            self._del_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; "
                "color: #666; font-size: 12px; }"
                "QPushButton:hover { color: #FF4444; }"
            )
            self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._del_btn.clicked.connect(lambda: self.delete_requested.emit(self))

            # Editable name for custom panels
            self._name_edit = QLineEdit(config.name, self)
            self._name_edit.setGeometry(49, 24, 190, 16)
            self._name_edit.setStyleSheet(
                "QLineEdit { background: transparent; border: none; "
                "border-bottom: 1px solid #444; color: #C0C0C0; font-size: 10px; }"
                "QLineEdit:focus { border-bottom: 1px solid #9375FF; }"
            )
            self._name_edit.editingFinished.connect(
                lambda: self.name_changed.emit(self, self._name_edit.text())
            )

    def update_values(self, sensor_readings: dict[str, float]):
        """Update displayed values from sensor_id → value mapping."""
        for i, binding in enumerate(self.config.sensors):
            if i >= len(self._value_labels):
                break
            if not binding.sensor_id:
                self._value_labels[i].setText('--')
                continue
            value = sensor_readings.get(binding.sensor_id)
            if value is None:
                self._value_labels[i].setText('--')
            else:
                self._value_labels[i].setText(self._format_value(value, binding.unit))

    def set_temp_unit(self, unit: int):
        """Set temperature unit. 0=Celsius, 1=Fahrenheit."""
        self._temp_unit = unit

    def update_binding(self, row: int, binding: SensorBinding):
        """Update a row's sensor binding after picker selection."""
        pass  # Row labels are baked into the PNG backgrounds

    def set_selected(self, selected: bool):
        """Set selection state (white border when selected)."""
        self._selected = selected
        self.update()

    def paintEvent(self, event):
        """Draw selection border over background."""
        super().paintEvent(event)
        if self._selected:
            painter = QPainter(self)
            painter.setPen(QColor('white'))
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
            painter.end()

    def mousePressEvent(self, event):
        self.clicked.emit(self)

    def _format_value(self, value: float, unit: str) -> str:
        """Format a sensor value with its unit."""
        if unit == '°C':
            if self._temp_unit == 1:
                return f"{value * 9 / 5 + 32:.0f}°F"
            return f"{value:.0f}°C"
        elif unit in ('%', 'RPM', 'W'):
            return f"{value:.0f}{unit}"
        elif unit == 'V':
            return f"{value:.2f}V"
        elif unit in ('MHz',):
            return f"{value:.0f}MHz"
        elif unit in ('MB', 'MB/s', 'KB/s'):
            return f"{value:.1f}{unit}"
        else:
            return f"{value:.1f}"


class UCSystemInfo(QWidget):
    """System monitoring dashboard with configurable panels.

    Windows UCSystemInfoOptions layout:
    - Grid: 4 columns, starting at (44, 36), spacing (300, 199)
    - Panels: loaded from sysinfo_config.json
    - "+" button to add custom panels
    - Page navigation for >12 panels

    Matches 1274x800 content area (same as FormCZTV).
    """

    panel_clicked = pyqtSignal(object)  # SystemInfoPanel

    def __init__(self, enumerator: SensorEnumerator, lang: str = 'en', parent=None):
        super().__init__(parent)
        self.setFixedSize(1274, 800)

        self._enumerator = enumerator
        self._lang = lang
        self._config = SysInfoConfig()
        self._page = 0
        self._temp_unit = 0  # 0=Celsius, 1=Fahrenheit
        self._panels_list: list[SystemInfoPanel] = []
        self._add_btn: QLabel | None = None
        self._page_prev: QPushButton | None = None
        self._page_next: QPushButton | None = None
        self._page_label: QLabel | None = None
        self._slot_widgets: list[QWidget] = []
        self._selected_panel: SystemInfoPanel | None = None

        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_metrics)

        self._setup_ui()

    def _setup_ui(self):
        """Build from config, auto-map empty bindings."""
        # Dark background
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.WINDOW_BG))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        # Load config and auto-map any empty bindings
        self._config.load()
        self._config.auto_map(self._enumerator)
        self._config.save()

        self._rebuild_grid()

    def _rebuild_grid(self):
        """Clear and rebuild all panels from the current config."""
        # Remove all existing panel widgets
        for panel in self._panels_list:
            panel.setParent(None)
            panel.deleteLater()
        self._panels_list.clear()

        for w in self._slot_widgets:
            w.setParent(None)
            w.deleteLater()
        self._slot_widgets.clear()

        if self._add_btn:
            self._add_btn.setParent(None)
            self._add_btn.deleteLater()
            self._add_btn = None

        # Determine page range
        total_panels = len(self._config.panels)
        max_page = max(0, (total_panels) // PANELS_PER_PAGE)  # +1 slot for add button
        if self._page > max_page:
            self._page = max_page

        start_idx = self._page * PANELS_PER_PAGE
        end_idx = min(start_idx + PANELS_PER_PAGE, total_panels)
        visible_panels = self._config.panels[start_idx:end_idx]

        # Create panel widgets for this page
        for i, panel_config in enumerate(visible_panels):
            row = i // COLUMNS
            col = i % COLUMNS
            x = START_X + col * SPACING_X
            y = START_Y + row * SPACING_Y

            panel = SystemInfoPanel(panel_config, self)
            panel.set_temp_unit(self._temp_unit)
            panel.setGeometry(x, y, PANEL_W, PANEL_H)
            panel.clicked.connect(self._on_panel_clicked)
            panel.sensor_select_requested.connect(self._on_selector_clicked)
            panel.delete_requested.connect(self._on_delete_clicked)
            panel.name_changed.connect(self._on_name_changed)
            panel.show()
            self._panels_list.append(panel)

        # "+" add button in the next slot after visible panels
        slots_used = len(visible_panels)
        if slots_used < PANELS_PER_PAGE:
            add_row = slots_used // COLUMNS
            add_col = slots_used % COLUMNS
            add_x = START_X + add_col * SPACING_X
            add_y = START_Y + add_row * SPACING_Y

            add_pixmap = load_pixmap('A增加数组.png', PANEL_W, PANEL_H)
            self._add_btn = QLabel(self)
            if not add_pixmap.isNull():
                self._add_btn.setPixmap(add_pixmap)
            else:
                self._add_btn.setText("+")
                self._add_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._add_btn.setStyleSheet(
                    "color: #666; font-size: 48px; background-color: #2D2D2D;"
                )
            self._add_btn.setGeometry(add_x, add_y, PANEL_W, PANEL_H)
            self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._add_btn.mousePressEvent = lambda e: self._on_add_clicked()  # type: ignore[assignment]
            self._add_btn.show()

            # Fill remaining slots with empty placeholders
            for j in range(slots_used + 1, PANELS_PER_PAGE):
                slot_row = j // COLUMNS
                slot_col = j % COLUMNS
                slot_x = START_X + slot_col * SPACING_X
                slot_y = START_Y + slot_row * SPACING_Y
                slot = QWidget(self)
                slot.setGeometry(slot_x, slot_y, PANEL_W, PANEL_H)
                slot.setStyleSheet("background-color: #2A2A2A;")
                slot.show()
                self._slot_widgets.append(slot)

        # Page navigation
        self._setup_page_nav(total_panels)

        # Select first panel by default
        if self._panels_list:
            self._on_panel_clicked(self._panels_list[0])

    def _setup_page_nav(self, total_panels: int):
        """Create/update page navigation buttons."""
        total_pages = max(1, (total_panels + PANELS_PER_PAGE) // PANELS_PER_PAGE)
        show_nav = total_pages > 1

        # Clean up old nav
        if self._page_prev:
            self._page_prev.setParent(None)
            self._page_prev.deleteLater()
            self._page_prev = None
        if self._page_next:
            self._page_next.setParent(None)
            self._page_next.deleteLater()
            self._page_next = None
        if self._page_label:
            self._page_label.setParent(None)
            self._page_label.deleteLater()
            self._page_label = None

        if not show_nav:
            return

        # Previous page button
        prev_px = load_pixmap('A上一页a.png', 64, 24)
        self._page_prev = QPushButton(self)
        px, py, pw, ph = PAGE_PREV_POS
        self._page_prev.setGeometry(px, py, pw, ph)
        self._page_prev.setFlat(True)
        if not prev_px.isNull():
            self._page_prev.setIcon(QIcon(prev_px))
            self._page_prev.setIconSize(QSize(64, 24))
            self._page_prev.setStyleSheet("QPushButton { background: transparent; border: none; }")
        else:
            self._page_prev.setText("◄ Prev")
            self._page_prev.setStyleSheet(
                "QPushButton { background: transparent; color: #888; border: none; }"
                "QPushButton:hover { color: white; }"
            )
        self._page_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._page_prev.clicked.connect(lambda: self._change_page(-1))
        self._page_prev.setEnabled(self._page > 0)
        self._page_prev.show()

        # Next page button
        next_px = load_pixmap('A下一页a.png', 64, 24)
        self._page_next = QPushButton(self)
        nx, ny, nw, nh = PAGE_NEXT_POS
        self._page_next.setGeometry(nx, ny, nw, nh)
        self._page_next.setFlat(True)
        if not next_px.isNull():
            self._page_next.setIcon(QIcon(next_px))
            self._page_next.setIconSize(QSize(64, 24))
            self._page_next.setStyleSheet("QPushButton { background: transparent; border: none; }")
        else:
            self._page_next.setText("Next ►")
            self._page_next.setStyleSheet(
                "QPushButton { background: transparent; color: #888; border: none; }"
                "QPushButton:hover { color: white; }"
            )
        self._page_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._page_next.clicked.connect(lambda: self._change_page(1))
        self._page_next.setEnabled(self._page < total_pages - 1)
        self._page_next.show()

        # Page indicator
        self._page_label = QLabel(f"{self._page + 1}/{total_pages}", self)
        self._page_label.setGeometry(640, 650, 40, 24)
        self._page_label.setStyleSheet("color: #888; font-size: 10px; background: transparent;")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.show()

    def _change_page(self, direction: int):
        """Navigate pages (-1 = prev, +1 = next)."""
        self._page += direction
        self._rebuild_grid()

    def _on_panel_clicked(self, panel: SystemInfoPanel):
        """Select a panel (highlight with white border)."""
        if self._selected_panel:
            self._selected_panel.set_selected(False)
        panel.set_selected(True)
        self._selected_panel = panel
        self.panel_clicked.emit(panel)

    def _on_selector_clicked(self, panel: SystemInfoPanel, row: int):
        """Open sensor picker for a specific row."""
        from PyQt6.QtWidgets import QDialog

        from .uc_sensor_picker import SensorPickerDialog

        current_id = ''
        if row < len(panel.config.sensors):
            current_id = panel.config.sensors[row].sensor_id

        dialog = SensorPickerDialog(self._enumerator, self._lang, self)
        if current_id:
            dialog.set_current_sensor(current_id)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            sensor = dialog.get_selected_sensor()
            if sensor:
                # Update the binding
                if row < len(panel.config.sensors):
                    panel.config.sensors[row] = SensorBinding(
                        label=panel.config.sensors[row].label,
                        sensor_id=sensor.id,
                        unit=sensor.unit,
                    )
                    panel.update_binding(row, panel.config.sensors[row])
                    self._config.save()

    def _on_add_clicked(self):
        """Add a new custom panel."""
        new_panel = PanelConfig(
            category_id=0, name="Custom",
            sensors=[
                SensorBinding("Sensor 1", "", ""),
                SensorBinding("Sensor 2", "", ""),
                SensorBinding("Sensor 3", "", ""),
                SensorBinding("Sensor 4", "", ""),
            ],
        )
        self._config.panels.append(new_panel)
        self._config.save()

        # Navigate to the page where the new panel is
        new_idx = len(self._config.panels) - 1
        self._page = new_idx // PANELS_PER_PAGE
        self._rebuild_grid()

    def _on_delete_clicked(self, panel: SystemInfoPanel):
        """Delete a custom panel."""
        if panel.config in self._config.panels:
            self._config.panels.remove(panel.config)
            self._config.save()
            self._rebuild_grid()

    def _on_name_changed(self, panel: SystemInfoPanel, new_name: str):
        """Update custom panel name."""
        panel.config.name = new_name
        self._config.save()

    def start_updates(self):
        """Start periodic metric updates (1s interval)."""
        self._update_metrics()
        self._update_timer.start(1000)

    def stop_updates(self):
        """Stop periodic metric updates."""
        self._update_timer.stop()

    def set_temp_unit(self, unit: int):
        """Set temperature unit on all panels. 0=Celsius, 1=Fahrenheit."""
        self._temp_unit = unit
        for panel in self._panels_list:
            panel.set_temp_unit(unit)

    def _update_metrics(self):
        """Update all panels with current sensor readings."""
        try:
            readings = self._enumerator.read_all()
            for panel in self._panels_list:
                panel.update_values(readings)
        except Exception as e:
            print(f"[!] Error updating metrics: {e}")
