"""SystemPanel — platform info + sensor readouts + doctor + debug report.

Reads:
* ``GetPlatformInfo`` for the static identity block;
* ``ReadSensors`` on a 2-second timer for live CPU/GPU/temp values;
* ``RunHealthCheck`` on demand for the doctor row.

Writes (via Commands):
* ``GenerateDebugReport`` when the "Save bug report…" button is clicked
  → opens a save-as dialog and writes the bundle.

Demonstrates the BasePanel pattern: dispatch Commands, use the timer
helper, lay out widgets without business logic.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ....core.commands import (
    CheckForUpdate,
    DisableAutostart,
    EnableAutostart,
    GenerateDebugReport,
    GetAutostartStatus,
    GetPlatformInfo,
    ListGpus,
    ReadSensors,
    RunHealthCheck,
    SetGpuDevice,
)
from ..base import BasePanel

log = logging.getLogger(__name__)

_SENSOR_REFRESH_MS = 2000


class SystemPanel(BasePanel):
    """Live system readout + diagnostic actions."""

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(self._build_platform_box())
        outer.addWidget(self._build_gpu_box())
        outer.addWidget(self._build_maintenance_box())
        outer.addWidget(self._build_health_box())
        outer.addWidget(self._build_sensors_box(), 1)
        outer.addLayout(self._build_action_row())

        self._refresh_platform()
        self._refresh_health()
        self._refresh_sensors()
        self.start_periodic_updates(_SENSOR_REFRESH_MS, self._refresh_sensors)

    # ── Widget builders ───────────────────────────────────────────────

    def _build_platform_box(self) -> QGroupBox:
        box = QGroupBox("Platform", self)
        form = QFormLayout(box)
        self._distro_label = QLabel("…")
        self._install_label = QLabel("…")
        self._paths_label = QLabel("…")
        self._paths_label.setWordWrap(True)
        form.addRow("Distro:", self._distro_label)
        form.addRow("Install:", self._install_label)
        form.addRow("Paths:", self._paths_label)
        return box

    def _build_gpu_box(self) -> QGroupBox:
        box = QGroupBox("Metric source", self)
        form = QFormLayout(box)
        self._gpu_combo = QComboBox(box)
        self._set_gpu_btn = QPushButton("Use this GPU", box)
        self._set_gpu_btn.clicked.connect(self._on_set_gpu)
        row = QHBoxLayout()
        row.addWidget(self._gpu_combo, stretch=1)
        row.addWidget(self._set_gpu_btn)
        self._gpu_status = QLabel("", box)
        self._gpu_status.setWordWrap(True)
        form.addRow("GPU:", row)
        form.addRow("", self._gpu_status)
        self._populate_gpus()
        return box

    def _build_maintenance_box(self) -> QGroupBox:
        box = QGroupBox("Maintenance", self)
        form = QFormLayout(box)
        self._autostart_check = QCheckBox("Start TRCC on login", box)
        self._autostart_check.toggled.connect(self._on_autostart_toggled)
        self._update_btn = QPushButton("Check for updates", box)
        self._update_btn.clicked.connect(self._on_check_update)
        self._maint_status = QLabel("", box)
        self._maint_status.setWordWrap(True)
        self._maint_status.setTextFormat(Qt.TextFormat.RichText)
        self._maint_status.setOpenExternalLinks(True)
        form.addRow(self._autostart_check)
        form.addRow(self._update_btn)
        form.addRow(self._maint_status)
        self._refresh_autostart()
        return box

    def _build_health_box(self) -> QGroupBox:
        box = QGroupBox("Health", self)
        layout = QVBoxLayout(box)
        self._health_summary = QLabel("Running checks…", box)
        bold = QFont()
        bold.setBold(True)
        self._health_summary.setFont(bold)
        layout.addWidget(self._health_summary)

        self._health_details = QLabel("", box)
        self._health_details.setWordWrap(True)
        self._health_details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._health_details)
        return box

    def _build_sensors_box(self) -> QGroupBox:
        box = QGroupBox("Sensors (live)", self)
        layout = QVBoxLayout(box)
        self._sensors_list = QListWidget(box)
        self._sensors_list.setSelectionMode(
            QListWidget.SelectionMode.NoSelection,
        )
        layout.addWidget(self._sensors_list)
        return box

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        refresh = QPushButton("Re-run health check", self)
        refresh.clicked.connect(self._refresh_health)
        row.addWidget(refresh)

        report = QPushButton("Save bug report…", self)
        report.clicked.connect(self._save_debug_report)
        row.addWidget(report)

        row.addStretch(1)
        return row

    # ── Refreshers ────────────────────────────────────────────────────

    def _refresh_platform(self) -> None:
        r = self.dispatch(GetPlatformInfo())
        self._distro_label.setText(r.distro_name or "—")
        self._install_label.setText(r.install_method or "—")
        self._paths_label.setText(
            f"config: {r.config_dir}\n"
            f"data:   {r.data_dir}\n"
            f"log:    {r.log_file}",
        )

    def _refresh_health(self) -> None:
        r = self.dispatch(RunHealthCheck())
        self._health_summary.setText(r.message)
        details: list[str] = []
        for check in r.checks:
            line = f"[{check.severity:4}] {check.name:22} {check.message}"
            details.append(line)
            if check.fix_hint and check.severity != "OK":
                details.append(f"         hint: {check.fix_hint}")
        self._health_details.setText("\n".join(details))

    def _refresh_sensors(self) -> None:
        r = self.dispatch(ReadSensors())
        self._sensors_list.clear()
        for reading in r.readings:
            text = (
                f"{reading.sensor_id:30}  "
                f"{reading.value:>10.2f} {reading.unit:<6}  "
                f"({reading.category})"
            )
            item = QListWidgetItem(text)
            self._sensors_list.addItem(item)

    # ── Actions ───────────────────────────────────────────────────────

    def _populate_gpus(self) -> None:
        """Fill the GPU combo from ListGpus — self-healing, and degrades to
        a disabled 'No GPU detected' when none are present (a supported
        state, not an error)."""
        result = self.dispatch(ListGpus())
        self._gpu_combo.clear()
        if not result.ok or not result.gpus:
            self._gpu_combo.addItem("No GPU detected", userData=None)
            self._gpu_combo.setEnabled(False)
            self._set_gpu_btn.setEnabled(False)
            return
        self._gpu_combo.setEnabled(True)
        self._set_gpu_btn.setEnabled(True)
        for gpu in result.gpus:
            tag = "discrete" if gpu.is_discrete else "integrated"
            self._gpu_combo.addItem(f"{gpu.name} ({tag})", userData=gpu.key)

    def _on_set_gpu(self) -> None:
        gpu_key = self._gpu_combo.currentData()
        if gpu_key is None:
            return
        log.info("_on_set_gpu: gpu_key=%s", gpu_key)
        r = self.dispatch(SetGpuDevice(gpu_key=str(gpu_key)))
        self._gpu_status.setText(r.message)

    def _refresh_autostart(self) -> None:
        r = self.dispatch(GetAutostartStatus())
        self._autostart_check.blockSignals(True)
        self._autostart_check.setChecked(r.enabled)
        self._autostart_check.blockSignals(False)

    def _on_autostart_toggled(self, checked: bool) -> None:
        log.info("_on_autostart_toggled: checked=%s", checked)
        r = self.dispatch(EnableAutostart() if checked else DisableAutostart())
        self._maint_status.setText(r.message)

    def _on_check_update(self) -> None:
        log.info("_on_check_update")
        r = self.dispatch(CheckForUpdate())
        if not r.ok:
            self._maint_status.setText(f"Update check failed: {r.message}")
            return
        if r.latest_version and r.latest_version != r.local_version:
            self._maint_status.setText(
                f"Update available: {r.latest_version} "
                f"(you have {r.local_version}). "
                f'<a href="{r.release_url}">Release notes</a> — '
                "upgrade via your package manager.",
            )
        else:
            self._maint_status.setText(f"Up to date ({r.local_version}).")

    def _save_debug_report(self) -> None:
        default_name = "trcc-debug-report.txt"
        path_str, _filter = QFileDialog.getSaveFileName(
            self,
            "Save TRCC debug report",
            default_name,
            "Text files (*.txt);;All files (*.*)",
        )
        if not path_str:
            return
        out = Path(path_str)
        r = self.dispatch(GenerateDebugReport(output_path=out, log_tail_lines=1000))
        if r.ok:
            self._health_summary.setText(
                f"Debug report saved to {r.output_path}",
            )
        else:
            self._health_summary.setText(
                f"Debug report failed: {r.message}",
            )
