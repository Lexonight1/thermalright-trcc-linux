"""Overlay grid panel — 7x6 grid of overlay elements.

Matches Windows UCXiTongXianShi (472x430). Manages element configs,
selection and add/delete; each edit names its element by id.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QPushButton

from ...core.logs import per_frame
from ...core.models import OverlayElementConfig, OverlayMode
from ..presentation.overlay_model import OverlayModel
from ..presentation.overlay_serialization import tile_reading
from ..qt_words import say_tip
from .overlay_element import OverlayElementWidget
from .settings_layout import (
    GRID_TOGGLE,
    OVERLAY_CELL,
    OVERLAY_COLS,
    OVERLAY_DX,
    OVERLAY_DY,
    OVERLAY_GRID_H,
    OVERLAY_GRID_W,
    OVERLAY_ROWS,
    OVERLAY_X0,
    OVERLAY_Y0,
)
from .settings_skin import SettingsSkin

log = logging.getLogger(__name__)
#: The metrics tick reaches every cell, once a second.
frame_log = per_frame(__name__)


class OverlayGridPanel(QFrame):
    """7x6 grid of overlay elements (matches UCXiTongXianShi 472x430).

    Manages a list of element configs. Empty cells show "+".
    Has on/off toggle and "add" button at next available slot.
    """

    element_selected = Signal(int, object)  # index, OverlayElementConfig
    element_added = Signal(object)          # the new cell, id minted
    element_deleted = Signal(object)        # the removed cell
    add_requested = Signal()
    toggle_changed = Signal(bool)           # overlay on/off

    MAX_ELEMENTS = 42

    def __init__(self, parent=None, *, skin: SettingsSkin):
        super().__init__(parent)
        self._skin = skin
        self.setFixedSize(OVERLAY_GRID_W, OVERLAY_GRID_H)

        skin.background(self, skin.grid_bg, OVERLAY_GRID_W, OVERLAY_GRID_H,
                        fallback_style=f"background-color: {skin.base_bg}; border-radius: 5px;")

        # Interaction state lives in the toolkit-free Presentation Model;
        # this panel is a thin View that delegates + renders + emits signals.
        self._model = OverlayModel()
        self._cells = []           # OverlayElementWidget instances (always 42)
        # The last metrics tick, re-applied whenever cells change occupant.
        self._readings: Mapping[str, float] = {}
        self._temp_unit = "C"

        self._setup_toggle()
        self._setup_cells()

    def _setup_toggle(self):
        """On/Off toggle at (5, 5) using slide switch images."""
        self._toggle_btn = QPushButton(self)
        self._toggle_btn.setGeometry(*GRID_TOGGLE)
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(True)

        on_px = self._skin.pixmap(self._skin.toggle_on, 36, 18)
        off_px = self._skin.pixmap(self._skin.toggle_off, 36, 18)
        if not on_px.isNull() and not off_px.isNull():
            icon = QIcon()
            icon.addPixmap(on_px, QIcon.Mode.Normal, QIcon.State.On)
            icon.addPixmap(off_px, QIcon.Mode.Normal, QIcon.State.Off)
            self._toggle_btn.setIcon(icon)
            self._toggle_btn.setIconSize(self._toggle_btn.size())
            self._toggle_btn.setStyleSheet(self._skin.flat_button)
        else:
            self._toggle_btn.setText("ON")
            self._toggle_btn.setStyleSheet(self._skin.grid_toggle_fallback)

        say_tip(self._toggle_btn, "Toggle overlay display")
        self._toggle_btn.clicked.connect(self._on_toggle)

    def _on_toggle(self, checked):
        """The switch sends the switch (``EnableOverlay``) and nothing else.

        It used to re-send the whole layout too, so flipping it overwrote
        whatever another UI had edited with this grid's copy.
        """
        log.debug("_on_toggle: overlay_enabled=%s→%s", self._model.enabled, checked)
        self._model.set_enabled(checked)
        self.toggle_changed.emit(checked)

    def _setup_cells(self):
        """Create 42 cell widgets in the 7x6 grid."""
        for row in range(OVERLAY_ROWS):
            for col in range(OVERLAY_COLS):
                index = row * OVERLAY_COLS + col
                x = OVERLAY_X0 + col * OVERLAY_DX
                y = OVERLAY_Y0 + row * OVERLAY_DY

                cell = OverlayElementWidget(index, self, skin=self._skin)
                cell.setGeometry(x, y, OVERLAY_CELL, OVERLAY_CELL)
                cell.clicked.connect(self._on_cell_clicked)
                cell.double_clicked.connect(self._on_cell_double_clicked)
                self._cells.append(cell)

    def set_language(self, lang: str) -> None:
        """The window's language, for every cell -- a weekday is drawn in it."""
        log.info("OverlayGridPanel.set_language: %s", lang)
        for cell in self._cells:
            cell.set_language(lang)

    def _refresh_cells(self):
        """Sync cell widgets with the model's element list."""
        log.debug("_refresh_cells")
        configs = self._model.all_configs()
        selected = self._model.selected_index
        for i, cell in enumerate(self._cells):
            cell.config = configs[i] if i < len(configs) else None
            cell.set_selected(i == selected)
            cell.update()
        self._apply_readings()

    def update_metrics(self, readings: Mapping[str, float],
                       temp_unit: str) -> None:
        """A metrics tick: every hardware cell shows its live value (#301).

        The C# refreshes each tile about once a second
        (``UCXiTongXianShiTimer``); clock cells repaint so their time moves.
        """
        frame_log.debug("update_metrics: %d reading(s) temp_unit=%s",
                        len(readings), temp_unit)
        self._readings, self._temp_unit = readings, temp_unit
        self._apply_readings()

    def _apply_readings(self) -> None:
        """Each occupied cell shows its own reading from the last tick."""
        frame_log.debug("_apply_readings: %d cell(s)", len(self._cells))
        for cell in self._cells:
            if cell.config is None:
                cell.show_reading(None)
            elif cell.config.mode == OverlayMode.HARDWARE:
                cell.show_reading(tile_reading(cell.config, self._readings,
                                               self._temp_unit))
            else:
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
        """Add a copy of *config* as a new element, with its own id."""
        log.debug("add_element: %s", config.mode.name)
        if self._model.add(config):
            self._refresh_cells()
            self.element_added.emit(self._model.selected_config)

    def delete_element(self, index):
        """Delete element at index."""
        log.debug("delete_element: index=%s", index)
        removed = self._model.config_at(index)
        if self._model.delete(index):
            self._refresh_cells()
            self.element_deleted.emit(removed)

    def update_element(self, index, config):
        """Update config for element at index."""
        log.debug("update_element: index=%s", index)
        if self._model.update(index, config):
            self._cells[index].set_config(config)
            self._apply_readings()

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
