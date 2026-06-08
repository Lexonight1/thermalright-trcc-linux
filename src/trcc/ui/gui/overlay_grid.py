"""Overlay grid panel — 7x6 grid of overlay elements.

Matches Windows UCXiTongXianShi (472x430). Manages element configs,
selection, add/delete, and serialization to overlay config format.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QPushButton

from ...core.models import OverlayElementConfig
from ..presentation.overlay_model import OverlayModel
from ..presentation.overlay_serialization import (
    configs_to_next_elements,
    configs_to_overlay_config,
    overlay_config_to_configs,
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

        # Interaction state lives in the toolkit-free Presentation Model;
        # this panel is a thin View that delegates + renders + emits signals.
        self._model = OverlayModel()
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
        log.debug("_on_toggle: overlay_enabled=%s→%s", self._model.enabled, checked)
        self._model.set_enabled(checked)
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
        """Sync cell widgets with the model's element list."""
        log.debug("_refresh_cells")
        configs = self._model.all_configs()
        selected = self._model.selected_index
        for i, cell in enumerate(self._cells):
            cell.config = configs[i] if i < len(configs) else None
            cell.set_selected(i == selected)
            cell.update()

    def _on_cell_clicked(self, index):
        count = len(self._model)
        log.debug("_on_cell_clicked: index=%s (configs=%s)", index, count)
        # Deselect previous
        previous = self._model.selected_index
        if 0 <= previous < len(self._cells):
            self._cells[previous].set_selected(False)

        if index < count:
            # Clicked an existing element — select it
            config = self._model.select(index)
            self._cells[index].set_selected(True)
            self.element_selected.emit(index, config)
        elif index == count and count < self.MAX_ELEMENTS:
            # Clicked the "+" slot — request add
            self._model.clear_selection()
            self.add_requested.emit()
        else:
            self._model.clear_selection()

    def _on_cell_double_clicked(self, index):
        log.debug("_on_cell_double_clicked: index=%s", index)
        if index < len(self._model):
            self.delete_element(index)

    def select_element(self, index: int) -> None:
        """Programmatically select an element by index."""
        if index < 0 or index >= len(self._model):
            return
        self._on_cell_clicked(index)

    def find_nearest_element(self, x: int, y: int) -> int:
        """Find index of element nearest to (x, y). Returns -1 if none."""
        return self._model.find_nearest(x, y)

    # --- Public API ---

    @property
    def overlay_enabled(self):
        return self._model.enabled

    def set_overlay_enabled(self, enabled: bool):
        """Programmatically set overlay enabled state (no signal emitted)."""
        self._model.set_enabled(enabled)
        self._toggle_btn.blockSignals(True)
        self._toggle_btn.setChecked(enabled)
        self._toggle_btn.blockSignals(False)

    def add_element(self, config):
        """Add an element to the grid."""
        if self._model.add(config):
            self._refresh_cells()
            self.elements_changed.emit()

    def delete_element(self, index):
        """Delete element at index."""
        if self._model.delete(index):
            self._refresh_cells()
            self.element_deleted.emit(index)
            self.elements_changed.emit()

    def update_element(self, index, config):
        """Update config for element at index."""
        log.debug("update_element: index=%s", index)
        if self._model.update(index, config):
            self._cells[index].set_config(config)
            self._cells[index].update()

    def get_selected_index(self):
        return self._model.selected_index

    def get_selected_config(self):
        return self._model.selected_config

    def get_all_configs(self) -> list[OverlayElementConfig]:
        """Get all element configs."""
        return self._model.all_configs()

    def load_configs(self, configs: list[OverlayElementConfig]):
        """Load element configs from list."""
        self._model.load(configs)
        self._refresh_cells()

    def clear_all(self):
        self._model.clear()
        self._refresh_cells()

    def to_overlay_config(self):
        """Convert to OverlayRenderer config format."""
        return configs_to_overlay_config(self._model.all_configs(), self._model.enabled)

    def to_next_elements(self) -> list[dict]:
        """Grid → next/ ``OverlayElement`` dicts for the Command bus.

        The shape ``SetOverlayConfig`` accepts (id + flat font + type).  This
        is what edits dispatch; ``to_overlay_config`` (legacy keyed shape)
        stays for any local-state consumers.
        """
        if not self._model.enabled:
            return []
        return configs_to_next_elements(self._model.all_configs())

    def load_from_overlay_config(self, overlay_config):
        """Load from OverlayRenderer config format."""
        self.load_configs(overlay_config_to_configs(overlay_config))
