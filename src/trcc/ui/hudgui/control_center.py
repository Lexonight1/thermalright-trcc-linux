"""hudgui's Control Center: the App's own settings, as the gui's page has
them -- start at login, the temperature unit, disk readings, how often the
readings refresh, which GPU they come from, and the language.

Every control shows what the App holds (``ControlCenterSnapshot``,
``GetAutostartStatus``) and is shown again when any UI changes a setting, so
a change in the gui or the CLI moves it here too.  Showing sends nothing.
The gui's "running mode" pair is not here: there it is drawn and wired to
nothing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from ...core.commands import (
    ControlCenterSnapshot,
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    ListGpus,
    ListLanguages,
    SetGpuDevice,
    SetHddEnabled,
    SetLanguage,
    SetRefreshInterval,
    SetTempUnit,
)
from ...core.models import MAX_REFRESH_INTERVAL_S, MIN_REFRESH_INTERVAL_S

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)

_STYLE = ("QLabel, QCheckBox { color: #dfe5ea; background: transparent; font-size: 11pt; }"
          "QLabel#note { color: #ffcf6e; }"
          "QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; "
          "background: rgba(255, 255, 255, 25); border: 1px solid rgba(255, 255, 255, 90); }"
          "QCheckBox::indicator:checked { background: #a9e6ff; }")
#: The temperature units, as the picker lists them.
UNITS = (("°C", "C"), ("°F", "F"))


class ControlCenter(QWidget):
    """The App's settings, one row each."""

    def __init__(self, app: CommandBus, bus: BusBridge,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("ControlCenter: built")
        self._app = app
        self.setStyleSheet(_STYLE)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(16)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self.autostart_box = QCheckBox("Start TRCC when I log in", self)
        self.autostart_box.toggled.connect(self._on_autostart)
        form.addRow("Startup", self.autostart_box)

        self.unit_box = QComboBox(self)
        for text, unit in UNITS:
            self.unit_box.addItem(text, unit)
        self.unit_box.currentIndexChanged.connect(self._on_unit)
        form.addRow("Temperature", self.unit_box)

        self.hdd_box = QCheckBox("Read the disks' temperature and use", self)
        self.hdd_box.toggled.connect(self._on_hdd)
        form.addRow("Disks", self.hdd_box)

        self.refresh_box = QSpinBox(self)
        self.refresh_box.setRange(int(MIN_REFRESH_INTERVAL_S), int(MAX_REFRESH_INTERVAL_S))
        self.refresh_box.setSuffix(" s")
        self.refresh_box.editingFinished.connect(self._on_refresh)
        form.addRow("Refresh every", self.refresh_box)

        self.gpu_box = QComboBox(self)
        for gpu in app.dispatch(ListGpus()).gpus:
            self.gpu_box.addItem(gpu.name, gpu.key)
        if not self.gpu_box.count():
            self.gpu_box.addItem("No GPU detected", "")
        self.gpu_box.setEnabled(self.gpu_box.count() > 1)
        self.gpu_box.currentIndexChanged.connect(self._on_gpu)
        form.addRow("GPU", self.gpu_box)

        self.language_box = QComboBox(self)
        for language in app.dispatch(ListLanguages()).languages:
            self.language_box.addItem(f"{language.name} ({language.code})", language.code)
        self.language_box.currentIndexChanged.connect(self._on_language)
        form.addRow("Language", self.language_box)

        #: Why the last change was refused, or "".
        self.note = QLabel("", self)
        self.note.setObjectName("note")
        form.addRow("", self.note)

        bus.app_settings_changed.connect(self.show_state,
                                         type=Qt.ConnectionType.QueuedConnection)
        self.show_state()

    def show_state(self, *_event: object) -> None:
        """Every control from the App.  Sends nothing."""
        settings = self._app.dispatch(ControlCenterSnapshot())
        autostart = self._app.dispatch(GetAutostartStatus()).enabled
        log.info("ControlCenter.show_state: autostart=%s unit=%s hdd=%s every=%ss "
                 "gpu=%s language=%s", autostart, settings.temp_unit, settings.hdd_enabled,
                 settings.refresh_interval_s, settings.active_gpu, settings.language)
        controls = (self.autostart_box, self.unit_box, self.hdd_box, self.refresh_box,
                    self.gpu_box, self.language_box)
        blockers = [QSignalBlocker(control) for control in controls]
        self.autostart_box.setChecked(autostart)
        self.unit_box.setCurrentIndex(max(0, self.unit_box.findData(settings.temp_unit)))
        self.hdd_box.setChecked(settings.hdd_enabled)
        self.refresh_box.setValue(round(settings.refresh_interval_s))
        if (gpu := self.gpu_box.findData(settings.active_gpu or "")) >= 0:
            self.gpu_box.setCurrentIndex(gpu)
        if (language := self.language_box.findData(settings.language)) >= 0:
            self.language_box.setCurrentIndex(language)
        del blockers

    def _send(self, command: Command[Any]) -> None:
        """Dispatch *command*; say why if it was refused, and show the App's
        values either way."""
        result = self._app.dispatch(command)
        log.info("ControlCenter._send: %s -> ok=%s %s", command, result.ok, result.message)
        self.note.setText("" if result.ok else result.message)
        self.show_state()

    # ── One per control ────────────────────────────────────────────────

    def _on_autostart(self, on: bool) -> None:
        log.info("ControlCenter._on_autostart: %s", on)
        self._send(EnableAutostart() if on else DisableAutostart())

    def _on_unit(self) -> None:
        unit = str(self.unit_box.currentData())
        log.info("ControlCenter._on_unit: %s", unit)
        self._send(SetTempUnit(unit=unit))

    def _on_hdd(self, on: bool) -> None:
        log.info("ControlCenter._on_hdd: %s", on)
        self._send(SetHddEnabled(enabled=on))

    def _on_refresh(self) -> None:
        seconds = float(self.refresh_box.value())
        log.info("ControlCenter._on_refresh: %ss", seconds)
        self._send(SetRefreshInterval(seconds=seconds))

    def _on_gpu(self) -> None:
        gpu = str(self.gpu_box.currentData() or "")
        log.info("ControlCenter._on_gpu: %r", gpu)
        if gpu:
            self._send(SetGpuDevice(gpu_key=gpu))

    def _on_language(self) -> None:
        language = str(self.language_box.currentData())
        log.info("ControlCenter._on_language: %s", language)
        self._send(SetLanguage(language=language))
