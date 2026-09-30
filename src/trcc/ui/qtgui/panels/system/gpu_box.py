"""GpuBox — which GPU the metric sources read.

Degrades to a disabled "No GPU detected" rather than an error: a box with
no discrete or integrated GPU is a supported state, not a fault.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel

from .....core.commands import ControlCenterSnapshot, ListGpus, SetGpuDevice
from ._base import SystemBox

log = logging.getLogger(__name__)


class GpuBox(SystemBox):
    """Pick the GPU whose readings feed the overlay."""

    TITLE = "Metric source"

    def _build_ui(self) -> None:
        log.debug("_build_ui")
        form = QFormLayout(self)
        # Shows the ACTIVE GPU and switches when the user picks another.  It
        # never selected the active one and sent its current row on a button
        # press, so an untouched press switched to the first GPU listed.
        self._combo = QComboBox(self)
        self._combo.activated.connect(self._on_pick)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        form.addRow("GPU:", self._combo)
        form.addRow("", self._status)
        self._bus.app_settings_changed.connect(
            self._on_app_settings_changed,
            type=Qt.ConnectionType.QueuedConnection)
        self._populate()

    def _populate(self) -> None:
        """Fill the combo from ListGpus and show the active one."""
        log.debug("_populate")
        result = self.dispatch(ListGpus())
        self._combo.clear()
        if not result.ok or not result.gpus:
            log.info("_populate: no GPU reported — control disabled")
            self._combo.addItem("No GPU detected", userData=None)
            self._combo.setEnabled(False)
            return
        log.info("_populate: %d GPU(s)", len(result.gpus))
        self._combo.setEnabled(True)
        for gpu in result.gpus:
            tag = "discrete" if gpu.is_discrete else "integrated"
            self._combo.addItem(f"{gpu.name} ({tag})", userData=gpu.key)
        self._show_active()

    def _show_active(self) -> None:
        """Select the GPU the App uses.  Sends nothing (``activated`` only)."""
        active = self.dispatch(ControlCenterSnapshot()).active_gpu
        index = self._combo.findData(active) if active else -1
        log.info("_show_active: %s (row %d)", active or "auto", index)
        if index >= 0:
            self._combo.setCurrentIndex(index)

    def _on_app_settings_changed(self, event: object) -> None:
        log.debug("_on_app_settings_changed: %s", type(event).__name__)
        self._show_active()

    def _on_pick(self, _index: int) -> None:
        gpu_key = self._combo.currentData()
        if gpu_key is None:
            log.debug("_on_pick: nothing selectable")
            return
        log.info("_on_pick: gpu_key=%s", gpu_key)
        self._status.setText(self.dispatch(SetGpuDevice(gpu_key=str(gpu_key))).message)
