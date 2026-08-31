"""AdvancedTab — sensor source, test mode, clock + week prefs.

Settings here apply only when the relevant mode is active:

* Temp source — only meaningful in TEMP_LINKED mode (CPU vs GPU
  temperature drives colour).
* Load source — only meaningful in LOAD_LINKED mode.
* Test mode — overrides everything else, cycles through four
  reference colours.  Use to confirm the device responds.
* Clock format / week start — only LC2-style segment devices render
  these, but persisting them per-device means future LED styles that
  add a clock get the right prefs out of the box.

Memory ratio + disk index live on this tab too because they're "set
once and forget" — power users who want them changed will find them
here.
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from .....core.commands import (
    EnableLedTestMode,
    SetClockFormat,
    SetDiskIndex,
    SetLedLoadSource,
    SetLedTempSource,
    SetMemoryRatio,
    SetWeekStart,
)
from .....core.results import LedSnapshotResult
from ....presentation.led_panel import LedPanelModel
from ._base import LedTabBase

log = logging.getLogger(__name__)


class AdvancedTab(LedTabBase):
    """Sensor source + test mode + clock options."""

    def __init__(self, app, key_provider, parent=None) -> None:
        super().__init__(app, key_provider, parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Sensor-linked sources ────────────────────────────────────
        self._sources_box = sources_box = QGroupBox("Sensor linkage", self)
        sources_form = QFormLayout(sources_box)

        self._temp_cpu = QRadioButton("CPU", self)
        self._temp_gpu = QRadioButton("GPU", self)
        self._temp_group = QButtonGroup(self)
        self._temp_group.setExclusive(True)
        self._temp_group.addButton(self._temp_cpu)
        self._temp_group.addButton(self._temp_gpu)
        self._temp_cpu.toggled.connect(self._on_temp_cpu_toggled)
        self._temp_gpu.toggled.connect(self._on_temp_gpu_toggled)
        temp_row = QHBoxLayout()
        temp_row.addWidget(self._temp_cpu)
        temp_row.addWidget(self._temp_gpu)
        temp_row.addStretch(1)
        sources_form.addRow("Temperature follows:", temp_row)

        self._load_cpu = QRadioButton("CPU", self)
        self._load_gpu = QRadioButton("GPU", self)
        self._load_group = QButtonGroup(self)
        self._load_group.setExclusive(True)
        self._load_group.addButton(self._load_cpu)
        self._load_group.addButton(self._load_gpu)
        self._load_cpu.toggled.connect(self._on_load_cpu_toggled)
        self._load_gpu.toggled.connect(self._on_load_gpu_toggled)
        load_row = QHBoxLayout()
        load_row.addWidget(self._load_cpu)
        load_row.addWidget(self._load_gpu)
        load_row.addStretch(1)
        sources_form.addRow("Load follows:", load_row)

        root.addWidget(sources_box)

        # ── Test mode ────────────────────────────────────────────────
        test_box = QGroupBox("Diagnostic", self)
        test_layout = QVBoxLayout(test_box)
        self._test_check = QCheckBox(
            "Cycle through 4 reference colours (test mode)", self,
        )
        self._test_check.setToolTip(
            "Overrides the current mode + colour.  Useful for confirming "
            "the device responds at all.",
        )
        self._test_check.toggled.connect(self._on_test_mode_toggled)
        test_layout.addWidget(self._test_check)
        root.addWidget(test_box)

        # ── Clock options ────────────────────────────────────────────
        self._clock_box = clock_box = QGroupBox("Clock + calendar (LC2)", self)
        clock_form = QFormLayout(clock_box)
        self._clock_24h = QCheckBox("24-hour clock", self)
        self._clock_24h.toggled.connect(self._on_clock_format)
        self._week_sunday = QCheckBox("Week starts on Sunday", self)
        self._week_sunday.toggled.connect(self._on_week_start)
        clock_form.addRow(self._clock_24h)
        clock_form.addRow(self._week_sunday)
        root.addWidget(clock_box)

        # ── Memory + disk (segment devices) ──────────────────────────
        self._misc_box = misc_box = QGroupBox("Memory + disk (segment devices)", self)
        misc_form = QFormLayout(misc_box)
        self._disk_index = QSpinBox(self)
        self._disk_index.setRange(0, 31)
        self._disk_index.setToolTip(
            "Which physical disk to surface for read/write stats.  0 is "
            "usually the system / primary drive.",
        )
        self._disk_index.editingFinished.connect(self._on_disk_index_changed)
        self._memory_ratio = QComboBox(self)
        for mult in (1, 2, 4):
            self._memory_ratio.addItem(f"×{mult}", userData=mult)
        self._memory_ratio.currentIndexChanged.connect(
            self._on_memory_ratio_changed,
        )
        misc_form.addRow("Disk index:", self._disk_index)
        misc_form.addRow("DDR multiplier:", self._memory_ratio)
        root.addWidget(misc_box)

        root.addStretch(1)

    # ── Public ────────────────────────────────────────────────────────

    def refresh_from(self, snapshot: LedSnapshotResult | None) -> None:
        log.debug("refresh_from")
        if snapshot is None:
            return

        self._block_sources(True)
        if snapshot.temp_source == "cpu":
            self._temp_cpu.setChecked(True)
        else:
            self._temp_gpu.setChecked(True)
        if snapshot.load_source == "cpu":
            self._load_cpu.setChecked(True)
        else:
            self._load_gpu.setChecked(True)
        self._block_sources(False)

        self._test_check.blockSignals(True)
        self._test_check.setChecked(snapshot.test_mode)
        self._test_check.blockSignals(False)

        self._clock_24h.blockSignals(True)
        self._clock_24h.setChecked(snapshot.clock_24h)
        self._clock_24h.blockSignals(False)

        self._week_sunday.blockSignals(True)
        self._week_sunday.setChecked(snapshot.week_sunday)
        self._week_sunday.blockSignals(False)

        self._disk_index.blockSignals(True)
        self._disk_index.setValue(snapshot.disk_index)
        self._disk_index.blockSignals(False)

        self._memory_ratio.blockSignals(True)
        idx = self._memory_ratio.findData(snapshot.memory_ratio)
        self._memory_ratio.setCurrentIndex(idx if idx >= 0 else 1)   # default ×2
        self._memory_ratio.blockSignals(False)

    def apply_panel(self, panel: LedPanelModel) -> None:
        """Show only the sub-sections this device's LED style actually uses.

        Mirrors the gui's ``led_panel_for`` gating (the shared C#-sourced
        composition model): sensor linkage rides with the M1-M6 gauges, the
        clock box is LC2-only, the memory/disk box is for the LC1/LF11 segment
        devices.  The diagnostic/test box stays for every device.
        """
        log.info(
            "apply_panel: style=%s sensors=%s clock=%s memory=%s disk=%s",
            panel.style_id, panel.show_sensor_gauges, panel.show_clock_panel,
            panel.show_memory_panel, panel.show_disk_panel,
        )
        self._sources_box.setVisible(panel.show_sensor_gauges)
        self._clock_box.setVisible(panel.show_clock_panel)
        self._misc_box.setVisible(
            panel.show_memory_panel or panel.show_disk_panel,
        )

    # ── Internals ─────────────────────────────────────────────────────

    def _block_sources(self, blocked: bool) -> None:
        for w in (
            self._temp_cpu, self._temp_gpu,
            self._load_cpu, self._load_gpu,
        ):
            w.blockSignals(blocked)

    # ── Command dispatch ──────────────────────────────────────────────

    # Radio buttons emit ``toggled`` for both the newly-checked and
    # newly-unchecked button — only act on the checked one.
    def _on_temp_cpu_toggled(self, checked: bool) -> None:
        if checked:
            self._on_temp_source("cpu")

    def _on_temp_gpu_toggled(self, checked: bool) -> None:
        if checked:
            self._on_temp_source("gpu")

    def _on_load_cpu_toggled(self, checked: bool) -> None:
        if checked:
            self._on_load_source("cpu")

    def _on_load_gpu_toggled(self, checked: bool) -> None:
        if checked:
            self._on_load_source("gpu")

    def _on_temp_source(self, source: str) -> None:
        log.info("_on_temp_source: source=%s", source)
        key = self.current_key()
        if key:
            self._dispatch(SetLedTempSource(key=key, source=source))

    def _on_load_source(self, source: str) -> None:
        log.info("_on_load_source: source=%s", source)
        key = self.current_key()
        if key:
            self._dispatch(SetLedLoadSource(key=key, source=source))

    def _on_test_mode_toggled(self, checked: bool) -> None:
        log.info("_on_test_mode_toggled: checked=%s", checked)
        key = self.current_key()
        if key:
            self._dispatch(EnableLedTestMode(key=key, enabled=checked))

    def _on_clock_format(self, is_24h: bool) -> None:
        log.info("_on_clock_format: is_24h=%s", is_24h)
        key = self.current_key()
        if key:
            self._dispatch(SetClockFormat(key=key, is_24h=is_24h))

    def _on_week_start(self, sunday_first: bool) -> None:
        log.info("_on_week_start: sunday_first=%s", sunday_first)
        key = self.current_key()
        if key:
            self._dispatch(SetWeekStart(key=key, sunday_first=sunday_first))

    def _on_disk_index_changed(self) -> None:
        log.info("_on_disk_index_changed")
        key = self.current_key()
        if key:
            self._dispatch(SetDiskIndex(
                key=key, index=self._disk_index.value(),
            ))

    def _on_memory_ratio_changed(self, index: int) -> None:
        ratio = self._memory_ratio.itemData(index)
        log.info("_on_memory_ratio_changed: ratio=%s", ratio)
        key = self.current_key()
        if key and isinstance(ratio, int):
            self._dispatch(SetMemoryRatio(key=key, ratio=ratio))
