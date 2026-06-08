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
- Config persistence via system_config.json
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QWidget

if TYPE_CHECKING:
    from ...adapters.infra.sysinfo_config import SysInfoConfig
    from ...core.models import HardwareMetrics

from ...core.models import (
    CATEGORY_COLORS,
    CATEGORY_IMAGES,
    PanelConfig,
    SensorBinding,
)
from ...core.ports import SensorEnumerator
from ..presentation.sensor_display import format_sensor_value
from .assets import Assets
from .base import set_background_pixmap
from .constants import Layout

log = logging.getLogger(__name__)

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
PAGE_PREV_POS = (570, 655, 64, 24)
PAGE_NEXT_POS = (650, 655, 64, 24)


class SystemInfoPanel(QWidget):
    """Single hardware monitoring panel (matches Windows UCSystemInfoOptionsOne).

    Background is a pre-rendered PNG (sysinfo_cpu.png etc.).
    Values are overlaid as QLabels at correct positions.
    Selector buttons (↓) open the sensor picker per row.
    """

    clicked = Signal(object)                     # self
    sensor_select_requested = Signal(object, int)  # (self, row_index)
    delete_requested = Signal(object)              # self
    name_changed = Signal(object, str)             # (self, new_name)

    def __init__(self, config: PanelConfig, parent=None):
        super().__init__(parent)
        self.setFixedSize(PANEL_W, PANEL_H)

        self.config = config
        self._selected = False
        self._temp_unit = 0  # 0=Celsius, 1=Fahrenheit
        self._color = CATEGORY_COLORS.get(config.category_id, '#888888')
        self._value_labels: list[QLabel] = []
        self._selector_btns: list[QPushButton] = []
        # First-populate flag — INFO on the first ``update_values``
        # call shows which panel got data first and what readings
        # bound, then DEBUG so 2 s cadence doesn't drown the log.
        self._first_populate_logged: bool = False
        log.info(
            "SystemInfoPanel.__init__: name=%r category=%d rows=%d color=%s",
            config.name, config.category_id, len(config.sensors), self._color,
        )

        # Load background image (no tiling — matches Windows ImageLayout.None)
        img_name = CATEGORY_IMAGES.get(config.category_id, 'sysinfo_custom.png')
        self._bg_pixmap = Assets.load_pixmap(img_name, PANEL_W, PANEL_H)
        if not self._bg_pixmap.isNull():
            set_background_pixmap(self, self._bg_pixmap)

        # Load selector button image
        self._sel_pixmap = Assets.load_pixmap('sysinfo_select.png', 16, 30)

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
            sel.setProperty('row_idx', row_idx)
            sel.clicked.connect(self._on_selector_clicked)
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
            self._del_btn.setToolTip("Delete panel")
            self._del_btn.clicked.connect(self._on_delete_clicked)

            # Editable name for custom panels
            self._name_edit = QLineEdit(config.name, self)
            self._name_edit.setGeometry(49, 24, 190, 16)
            self._name_edit.setToolTip("Panel name")
            self._name_edit.setStyleSheet(
                "QLineEdit { background: transparent; border: none; "
                "border-bottom: 1px solid #444; color: #C0C0C0; font-size: 10px; }"
                "QLineEdit:focus { border-bottom: 1px solid #9375FF; }"
            )
            self._name_edit.editingFinished.connect(self._on_name_edited)

    def _on_selector_clicked(self, *_qt_args) -> None:
        """Selector-button slot — reads row_idx from sender's property."""
        sender = self.sender()
        if sender is None:
            log.debug("SystemInfoPanel._on_selector_clicked: sender is None")
            return
        row_idx = sender.property('row_idx')
        log.info("SystemInfoPanel._on_selector_clicked: panel=%r row=%s",
                 self.config.name, row_idx)
        if row_idx is not None:
            self.sensor_select_requested.emit(self, row_idx)

    def _on_delete_clicked(self) -> None:
        """Delete-button slot — re-emit delete_requested with this panel."""
        log.info("SystemInfoPanel._on_delete_clicked: panel=%r", self.config.name)
        self.delete_requested.emit(self)

    def _on_name_edited(self) -> None:
        """editingFinished slot — emit name_changed with the new text."""
        new_name = self._name_edit.text()
        log.info("SystemInfoPanel._on_name_edited: %r → %r",
                 self.config.name, new_name)
        self.name_changed.emit(self, new_name)

    def update_values(self, sensor_readings: dict[str, float]):
        """Update displayed values from sensor_id → value mapping."""
        rendered: list[str] = []
        for i, binding in enumerate(self.config.sensors):
            if i >= len(self._value_labels):
                break
            if not binding.sensor_id:
                self._value_labels[i].setText('--')
                rendered.append(f"{binding.label}=<unbound>")
                continue
            value = sensor_readings.get(binding.sensor_id)
            if value is None:
                self._value_labels[i].setText('--')
                rendered.append(f"{binding.label}={binding.sensor_id}=<missing>")
            else:
                txt = format_sensor_value(value, binding.unit, self._temp_unit)
                self._value_labels[i].setText(txt)
                rendered.append(f"{binding.label}={binding.sensor_id}={txt}")
        # First populate per panel logs INFO so we can see which
        # panel got which readings first; subsequent ticks DEBUG.
        if not self._first_populate_logged:
            log.info(
                "SystemInfoPanel.update_values %r: first populate — "
                "readings=%d → %s",
                self.config.name, len(sensor_readings), "; ".join(rendered),
            )
            self._first_populate_logged = True
        else:
            log.debug(
                "SystemInfoPanel.update_values %r: readings=%d → %s",
                self.config.name, len(sensor_readings), "; ".join(rendered),
            )

    def set_temp_unit(self, unit: int):
        """Set temperature unit. 0=Celsius, 1=Fahrenheit."""
        log.info("SystemInfoPanel.set_temp_unit %r: unit=%d (%s)",
                 self.config.name, unit, "F" if unit == 1 else "C")
        self._temp_unit = unit

    def update_binding(self, row: int, binding: SensorBinding):
        """Update a row's sensor binding after picker selection."""
        log.info("SystemInfoPanel.update_binding %r: row=%d → %s (%s) unit=%s",
                 self.config.name, row, binding.label,
                 binding.sensor_id or "<unbound>", binding.unit)
        # Row labels are baked into the PNG backgrounds

    def set_selected(self, selected: bool):
        """Set selection state (white border when selected)."""
        log.debug("SystemInfoPanel.set_selected %r: %s",
                  self.config.name, selected)
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
        log.debug("SystemInfoPanel.mousePressEvent: panel=%r", self.config.name)
        self.clicked.emit(self)


class UCSystemInfo(QWidget):
    """System monitoring dashboard with configurable panels.

    Windows UCSystemInfoOptions layout:
    - Grid: 4 columns, starting at (44, 36), spacing (300, 199)
    - Panels: loaded from system_config.json
    - "+" button to add custom panels
    - Page navigation for >12 panels

    Matches 1274x800 content area (same as FormCZTV).
    """

    panel_clicked = Signal(object)  # SystemInfoPanel

    def __init__(self, enumerator: SensorEnumerator,
                 sysinfo_config: SysInfoConfig,
                 parent=None):
        super().__init__(parent)
        _, _, w, h = Layout.SYSINFO_PANEL
        self.setFixedSize(w, h)

        self._enumerator = enumerator
        self._config = sysinfo_config
        self._page = 0
        self._temp_unit = 0  # 0=Celsius, 1=Fahrenheit
        self._panels_list: list[SystemInfoPanel] = []
        self._add_btn: QLabel | None = None
        self._page_prev: QPushButton | None = None
        self._page_next: QPushButton | None = None
        self._page_label: QLabel | None = None
        self._slot_widgets: list[QWidget] = []
        self._selected_panel: SystemInfoPanel | None = None
        # First-populate diagnostic flag — INFO on the first
        # ``update_from_metrics`` call proves the panel is receiving
        # data; subsequent ticks DEBUG to avoid flooding.
        self._first_populate_logged: bool = False
        log.info("UCSystemInfo.__init__: size=%dx%d", w, h)

        # No local timer — observers of Topic.METRICS dispatch updates via
        # update_from_metrics().  Trcc's PollingMetricsLoop is the single
        # publisher; this panel reads from the same broadcast as the LCD
        # overlay and the LED engine, so all three always agree.
        self._setup_ui()

    def _setup_ui(self):
        """Build from config, auto-map empty bindings."""
        log.info("UCSystemInfo._setup_ui: loading sysinfo_config")
        # Background image (sidebar_sysinfo_bg.png — Windows UCSystemInfoOptions)
        # ImageLayout.None in Windows — draw once, no tiling
        _, _, w, h = Layout.SYSINFO_PANEL
        set_background_pixmap(self, Assets.SYSINFO_BG, width=w, height=h)

        # Load config and auto-map any empty bindings
        self._config.load()
        self._config.auto_map(self._enumerator)
        self._config.save()
        log.info(
            "UCSystemInfo._setup_ui: configured panels=%d",
            len(self._config.panels),
        )

        self._rebuild_grid()

    def _rebuild_grid(self):
        """Clear and rebuild all panels from the current config."""
        log.info(
            "UCSystemInfo._rebuild_grid: page=%d configured=%d",
            self._page, len(self._config.panels),
        )
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

            add_pixmap = Assets.load_pixmap('sysinfo_add_group.png', PANEL_W, PANEL_H)
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
            self._add_btn.mousePressEvent = self._handle_add_btn_press  # type: ignore[assignment]
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

        log.info(
            "UCSystemInfo._rebuild_grid: rendered page=%d visible=%d total=%d",
            self._page, len(visible_panels), total_panels,
        )

        # Select first panel by default
        if self._panels_list:
            self._on_panel_clicked(self._panels_list[0])

    def _setup_page_nav(self, total_panels: int):
        """Create/update page navigation buttons."""
        total_pages = max(1, (total_panels + PANELS_PER_PAGE) // PANELS_PER_PAGE)
        show_nav = total_pages > 1
        log.info(
            "UCSystemInfo._setup_page_nav: total_panels=%d total_pages=%d show_nav=%s",
            total_panels, total_pages, show_nav,
        )

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
        prev_px = Assets.load_pixmap('sysinfo_prev_page.png', 64, 24)
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
        self._page_prev.setToolTip("Previous page")
        self._page_prev.clicked.connect(self._on_prev_page)
        self._page_prev.setEnabled(self._page > 0)
        self._page_prev.show()

        # Next page button
        next_px = Assets.load_pixmap('sysinfo_next_page.png', 64, 24)
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
        self._page_next.setToolTip("Next page")
        self._page_next.clicked.connect(self._on_next_page)
        self._page_next.setEnabled(self._page < total_pages - 1)
        self._page_next.show()

        # Page indicator
        self._page_label = QLabel(f"{self._page + 1}/{total_pages}", self)
        self._page_label.setGeometry(640, 655, 40, 24)
        self._page_label.setStyleSheet("color: #888; font-size: 10px; background: transparent;")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.show()

    def _change_page(self, direction: int):
        """Navigate pages (-1 = prev, +1 = next)."""
        old = self._page
        self._page += direction
        log.info("UCSystemInfo._change_page: %d → %d (dir=%+d)",
                 old, self._page, direction)
        self._rebuild_grid()

    def _on_prev_page(self) -> None:
        log.info("UCSystemInfo._on_prev_page: page=%d", self._page)
        self._change_page(-1)

    def _on_next_page(self) -> None:
        log.info("UCSystemInfo._on_next_page: page=%d", self._page)
        self._change_page(1)

    def _on_panel_clicked(self, panel: SystemInfoPanel):
        """Select a panel (highlight with white border)."""
        log.info("UCSystemInfo._on_panel_clicked: %r", panel.config.name)
        if self._selected_panel:
            self._selected_panel.set_selected(False)
        panel.set_selected(True)
        self._selected_panel = panel
        self.panel_clicked.emit(panel)

    def _on_selector_clicked(self, panel: SystemInfoPanel, row: int):
        """Open sensor picker for a specific row."""
        from PySide6.QtWidgets import QDialog

        from .uc_sensor_picker import SensorPickerDialog

        current_id = ''
        if row < len(panel.config.sensors):
            current_id = panel.config.sensors[row].sensor_id
        log.info(
            "UCSystemInfo._on_selector_clicked: panel=%r row=%d current=%r",
            panel.config.name, row, current_id or "<unbound>",
        )

        dialog = SensorPickerDialog(self._enumerator, self)
        if current_id:
            dialog.set_current_sensor(current_id)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            if (sensor := dialog.get_selected_sensor()):
                # Update the binding
                if row < len(panel.config.sensors):
                    panel.config.sensors[row] = SensorBinding(
                        label=panel.config.sensors[row].label,
                        sensor_id=sensor.id,
                        unit=sensor.unit,
                    )
                    log.info(
                        "UCSystemInfo._on_selector_clicked: bound %r row=%d → %s",
                        panel.config.name, row, sensor.id,
                    )
                    panel.update_binding(row, panel.config.sensors[row])
                    self._config.save()
            else:
                log.info("UCSystemInfo._on_selector_clicked: dialog accepted but no sensor selected")
        else:
            log.info("UCSystemInfo._on_selector_clicked: dialog cancelled")

    def _handle_add_btn_press(self, _event) -> None:
        """mousePressEvent handler for the add-panel button — drops the event arg."""
        log.info("UCSystemInfo._handle_add_btn_press: add-btn pressed")
        self._on_add_clicked()

    def _on_add_clicked(self):
        """Add a new custom panel."""
        log.info("UCSystemInfo._on_add_clicked: existing=%d",
                 len(self._config.panels))
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
        log.info("UCSystemInfo._on_delete_clicked: %r", panel.config.name)
        if panel.config in self._config.panels:
            self._config.panels.remove(panel.config)
            self._config.save()
            self._rebuild_grid()
        else:
            log.warning(
                "UCSystemInfo._on_delete_clicked: %r not in config — stale signal?",
                panel.config.name,
            )

    def _on_name_changed(self, panel: SystemInfoPanel, new_name: str):
        """Update custom panel name."""
        log.info("UCSystemInfo._on_name_changed: %r → %r",
                 panel.config.name, new_name)
        panel.config.name = new_name
        self._config.save()

    def start_updates(self) -> None:
        """No-op — retained for caller compatibility.

        The panel is a Topic.METRICS observer; updates arrive via
        ``update_from_metrics`` dispatched by trcc_app's main-thread
        signal.  The single publisher owns the cadence — no local timer.
        """
        log.info("UCSystemInfo.start_updates: no-op (Topic.METRICS observer)")

    def stop_updates(self) -> None:
        """No-op — retained for cleanup-call compatibility.

        The panel is a Topic.METRICS observer now; the single publisher
        owns the cadence and lifecycle, so there's nothing to stop here.
        """
        log.info("UCSystemInfo.stop_updates: no-op (Topic.METRICS observer)")

    def set_temp_unit(self, unit: int):
        """Set temperature unit on all panels. 0=Celsius, 1=Fahrenheit."""
        log.info("UCSystemInfo.set_temp_unit: %d → %d (panels=%d)",
                 self._temp_unit, unit, len(self._panels_list))
        self._temp_unit = unit
        for panel in self._panels_list:
            panel.set_temp_unit(unit)

    def update_from_metrics(self, metrics: HardwareMetrics) -> None:
        """Render panels from the unified metrics broadcast.

        Pulls the raw ``readings`` dict carried on the broadcast so we
        render the exact sensors the OS exposed — no field-by-field
        filtering through the typed DTO.  Same source as the LCD overlay,
        same source as the LED engine.
        """
        readings = metrics.readings
        # Per-tick — DEBUG; warn loudly if either side is empty.
        if not self._panels_list:
            log.warning(
                "UCSystemInfo.update_from_metrics: _panels_list empty — "
                "no panels configured; readings=%d available", len(readings),
            )
            return
        if not readings:
            log.warning(
                "UCSystemInfo.update_from_metrics: readings empty — "
                "publisher delivered no sensor data; panels=%d",
                len(self._panels_list),
            )
        # First call proves data is reaching the widget; subsequent
        # ticks stay DEBUG.  Same shape Phase 0 used.
        if not self._first_populate_logged:
            log.info(
                "UCSystemInfo.update_from_metrics: first populate — "
                "panels=%d readings=%d keys=%s",
                len(self._panels_list), len(readings), sorted(readings)[:10],
            )
            self._first_populate_logged = True
        else:
            log.debug(
                "UCSystemInfo.update_from_metrics: panels=%d readings=%d keys=%s",
                len(self._panels_list), len(readings), sorted(readings)[:10],
            )
        for panel in self._panels_list:
            panel.update_values(readings)
