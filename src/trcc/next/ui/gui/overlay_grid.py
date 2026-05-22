"""Overlay grid panel — 7x6 grid of overlay elements.

Matches Windows UCXiTongXianShi (472x430). Manages element configs,
selection, add/delete, and serialization to overlay config format.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QPushButton

from ...core.models import (
    HARDWARE_METRICS,
    METRIC_TO_IDS,
    OverlayElementConfig,
    OverlayMode,
)
from .assets import Assets
from .base import set_background_pixmap
from .constants import Colors, Sizes, Styles
from .overlay_element import OverlayElementWidget

log = logging.getLogger(__name__)


class OverlayGridPanel(QFrame):
    """7x6 grid of overlay elements (matches UCXiTongXianShi 472x430).

    Manages a list of element configs. Empty cells show "+".
    Has on/off toggle and "add" button at next available slot.
    """

    element_selected = Signal(int, object)  # index, OverlayElementConfig
    element_deleted = Signal(int)           # index
    add_requested = Signal()
    elements_changed = Signal()             # any add/delete/reorder
    toggle_changed = Signal(bool)           # overlay on/off

    MAX_ELEMENTS = 42

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.OVERLAY_GRID_W, Sizes.OVERLAY_GRID_H)

        set_background_pixmap(self, 'settings_overlay_grid_bg.png',
            Sizes.OVERLAY_GRID_W, Sizes.OVERLAY_GRID_H,
            fallback_style=f"background-color: {Colors.BASE_BG}; border-radius: 5px;")

        self._configs: list[OverlayElementConfig] = []
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

        on_px = Assets.load_pixmap(Assets.TOGGLE_ON, 36, 18)
        off_px = Assets.load_pixmap(Assets.TOGGLE_OFF, 36, 18)
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

        self._toggle_btn.setToolTip("Toggle overlay display")
        self._toggle_btn.clicked.connect(self._on_toggle)

    def _on_toggle(self, checked):
        log.debug("_on_toggle: overlay_enabled=%s→%s", self._overlay_enabled, checked)
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
        log.debug("_on_cell_clicked: index=%s (configs=%s)", index, len(self._configs))
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
        log.debug("_on_cell_double_clicked: index=%s", index)
        if index < len(self._configs):
            self.delete_element(index)

    def select_element(self, index: int) -> None:
        """Programmatically select an element by index."""
        if index < 0 or index >= len(self._configs):
            return
        self._on_cell_clicked(index)

    def find_nearest_element(self, x: int, y: int) -> int:
        """Find index of element nearest to (x, y). Returns -1 if none."""
        if not self._configs:
            return -1
        best_idx = -1
        best_dist = float('inf')
        for i, cfg in enumerate(self._configs):
            d = (cfg.x - x) ** 2 + (cfg.y - y) ** 2
            if d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx

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

    def get_all_configs(self) -> list[OverlayElementConfig]:
        """Get all element configs."""
        return list(self._configs)

    def load_configs(self, configs: list[OverlayElementConfig]):
        """Load element configs from list."""
        from dataclasses import replace
        self._configs = [replace(c) for c in configs[:self.MAX_ELEMENTS]]
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

        for i, cfg in enumerate(self._configs):
            entry = {
                'x': cfg.x,
                'y': cfg.y,
                'color': cfg.color,
                'font': {
                    'size': cfg.font_size,
                    'style': 'bold' if cfg.font_style == 1 else 'regular',
                    'name': cfg.font_name,
                },
                'enabled': True,
            }

            if cfg.mode == OverlayMode.TIME:
                entry['metric'] = 'time'
                entry['time_format'] = cfg.mode_sub
                key = f'time_{i}'
            elif cfg.mode == OverlayMode.DATE:
                entry['metric'] = 'date'
                entry['date_format'] = cfg.mode_sub
                key = f'date_{i}'
            elif cfg.mode == OverlayMode.WEEKDAY:
                entry['metric'] = 'weekday'
                key = f'weekday_{i}'
            elif cfg.mode == OverlayMode.CUSTOM:
                entry['text'] = cfg.text
                key = f'custom_{i}'
            elif cfg.mode == OverlayMode.HARDWARE:
                entry['metric'] = HARDWARE_METRICS.get(
                    (cfg.main_count, cfg.sub_count),
                    f'hw_{cfg.main_count}_{cfg.sub_count}')
                entry['temp_unit'] = cfg.mode_sub
                key = f'hw_{cfg.main_count}_{cfg.sub_count}_{i}'
            else:
                continue

            overlay_config[key] = entry

        return overlay_config

    def load_from_overlay_config(self, overlay_config):
        """Load from OverlayRenderer config format."""
        configs: list[OverlayElementConfig] = []
        for _key, cfg in overlay_config.items():
            if not isinstance(cfg, dict) or not cfg.get('enabled', True):
                continue

            font = cfg.get('font', {})
            font_size = font.get('size', 36) if isinstance(font, dict) else 36
            font_style = (1 if font.get('style') == 'bold' else 0) if isinstance(font, dict) else 0
            font_name = font.get('name', 'Microsoft YaHei') if isinstance(font, dict) else 'Microsoft YaHei'

            elem = OverlayElementConfig(
                x=cfg.get('x', 100),
                y=cfg.get('y', 100),
                color=cfg.get('color', '#FFFFFF'),
                font_size=font_size,
                font_style=font_style,
                font_name=font_name,
            )

            metric = cfg.get('metric', '')
            if metric == 'time':
                elem.mode = OverlayMode.TIME
                elem.mode_sub = cfg.get('time_format', 0)
            elif metric == 'date':
                elem.mode = OverlayMode.DATE
                elem.mode_sub = cfg.get('date_format', 0)
            elif metric == 'weekday':
                elem.mode = OverlayMode.WEEKDAY
            elif 'text' in cfg:
                elem.mode = OverlayMode.CUSTOM
                elem.text = cfg['text']
            elif metric in METRIC_TO_IDS:
                mc, sc = METRIC_TO_IDS[metric]
                elem.mode = OverlayMode.HARDWARE
                elem.main_count = mc
                elem.sub_count = sc
                elem.mode_sub = cfg.get('temp_unit', 0)
            else:
                continue
            configs.append(elem)

        self.load_configs(configs)
