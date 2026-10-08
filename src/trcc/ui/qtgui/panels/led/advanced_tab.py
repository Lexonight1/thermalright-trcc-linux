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

Memory ratio + the disk-sensor pin live on this tab too because they're
"set once and forget" — power users who want them changed will find them
here.

The disk control is APP-WIDE, unlike everything else here: it pins which
``DiskSource`` supplies ``disk_temp`` everywhere, so its state comes from
``ListDiskSensors().active`` rather than from this device's LED snapshot.
It replaced a per-LED-device ``disk_index`` spin box that nothing ever
applied — the index addressed a psutil partition list while the metric came
from the thermal list, three lists with no shared key.
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
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
)

from .....core.commands import (
    EnableLedTestMode,
    ListDiskSensors,
    OpenRgbSync,
    SetClockFormat,
    SetDiskDevice,
    SetLedLoadSource,
    SetLedTempSource,
    SetMemoryRatio,
    SetOpenRgbSync,
    SetWeekStart,
)
from .....core.results import LedSnapshotResult
from ....presentation.led_panel import LedPanelModel
from ....presentation.openrgb_address import (
    format_openrgb_address,
    parse_openrgb_address,
)
from ._base import LedTabBase

log = logging.getLogger(__name__)


class AdvancedTab(LedTabBase):
    """Sensor source + test mode + clock options."""

    def __init__(self, app, key_provider, parent=None) -> None:
        log.debug("__init__: app=%s key_provider=%s", app, key_provider)
        super().__init__(app, key_provider, parent)
        self._build_ui()

    def _build_ui(self) -> None:
        log.debug("_build_ui")
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
        self._disk_selector = QComboBox(self)
        self._disk_selector.setToolTip(
            "Which drive supplies the disk temperature.  Sourced from the "
            "THERMAL sensor list the reading actually comes from, so what "
            "you pick and what is shown are the same list.  Applies "
            "app-wide, not just to this device.",
        )
        self._disk_selector.currentIndexChanged.connect(self._on_disk_selected)
        self._memory_ratio = QComboBox(self)
        for mult in (1, 2, 4):
            self._memory_ratio.addItem(f"×{mult}", userData=mult)
        self._memory_ratio.currentIndexChanged.connect(
            self._on_memory_ratio_changed,
        )
        misc_form.addRow("Disk sensor:", self._disk_selector)
        misc_form.addRow("DDR multiplier:", self._memory_ratio)
        root.addWidget(misc_box)

        # ── OpenRGB follows the cooler (#160) -- app-wide ────────────
        openrgb_box = QGroupBox("OpenRGB", self)
        openrgb_layout = QVBoxLayout(openrgb_box)
        self._openrgb_check = QCheckBox(
            "OpenRGB's devices follow the cooler's colours", self)
        self._openrgb_check.setToolTip(
            "Sends this cooler's colours to every device of OpenRGB's SDK "
            "server (start it in OpenRGB's SDK Server tab) and puts them in "
            "direct mode.  Applies app-wide, not just to this device.")
        self._openrgb_check.toggled.connect(self._on_openrgb_toggled)
        self._openrgb_addr = QLineEdit("127.0.0.1:6742", self)
        self._openrgb_addr.setToolTip("OpenRGB's SDK server, host:port")
        self._openrgb_status = QLabel("", self)
        self._openrgb_status.setWordWrap(True)
        openrgb_layout.addWidget(self._openrgb_check)
        openrgb_layout.addWidget(self._openrgb_addr)
        openrgb_layout.addWidget(self._openrgb_status)
        root.addWidget(openrgb_box)

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
        # Once per device switch, and only when the style shows it — the same
        # trigger gui uses (``uc_led_control:1018``).  The list is hardware
        # identity, not per-tick state, so it does not belong in refresh_from.
        if panel.show_disk_panel:
            self.show_disk_sensors()

    # ── Internals ─────────────────────────────────────────────────────

    def show_disk_sensors(self) -> None:
        """Fill the picker from ``ListDiskSensors`` — the list the metric comes from.

        Deliberately NOT ``Platform.disk_info()`` (physical drives) nor
        ``ListDisks`` (mounted partitions): those are three lists with three
        cardinalities and no shared key, and a picker fed by either of the
        others cannot address what is displayed.  Going through the Query also
        keeps this tab off every port but the bus.

        Falls safe on any exception: the probe surface underneath is wide
        (hwmon / WMI / SMC), and this runs on every device switch.
        """
        log.info("show_disk_sensors")
        try:
            result = self._dispatch(ListDiskSensors())
            self._disk_selector.blockSignals(True)
            self._disk_selector.clear()
            for disk in result.disks:
                name = disk.name or disk.key
                # C# shows the name up to '(' — keep that trim.
                if "(" in name:
                    name = name[:name.index("(") - 1]
                self._disk_selector.addItem(name, disk.key)
            if result.active:
                idx = self._disk_selector.findData(result.active)
                if idx >= 0:
                    self._disk_selector.setCurrentIndex(idx)
            self._disk_selector.blockSignals(False)
            log.info("show_disk_sensors: %d sensor(s), active=%s",
                     len(result.disks), result.active or "(hottest)")
        except Exception as e:
            log.warning("show_disk_sensors: failed (%s) — picker empty", e)
            self._disk_selector.blockSignals(False)

    def _block_sources(self, blocked: bool) -> None:
        log.debug("_block_sources: blocked=%s", blocked)
        for w in (
            self._temp_cpu, self._temp_gpu,
            self._load_cpu, self._load_gpu,
        ):
            w.blockSignals(blocked)

    # ── Command dispatch ──────────────────────────────────────────────

    # Radio buttons emit ``toggled`` for both the newly-checked and
    # newly-unchecked button — only act on the checked one.
    def _on_temp_cpu_toggled(self, checked: bool) -> None:
        log.debug("_on_temp_cpu_toggled: checked=%s", checked)
        if checked:
            self._on_temp_source("cpu")

    def _on_temp_gpu_toggled(self, checked: bool) -> None:
        log.debug("_on_temp_gpu_toggled: checked=%s", checked)
        if checked:
            self._on_temp_source("gpu")

    def _on_load_cpu_toggled(self, checked: bool) -> None:
        log.debug("_on_load_cpu_toggled: checked=%s", checked)
        if checked:
            self._on_load_source("cpu")

    def _on_load_gpu_toggled(self, checked: bool) -> None:
        log.debug("_on_load_gpu_toggled: checked=%s", checked)
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

    def show_openrgb(self) -> None:
        """Show the App's OpenRGB setting and what it is doing -- sends nothing."""
        result = self._dispatch(OpenRgbSync())
        log.debug("show_openrgb: enabled=%s connected=%s", result.enabled,
                  result.connected)
        self._openrgb_check.blockSignals(True)
        self._openrgb_check.setChecked(result.enabled)
        self._openrgb_check.blockSignals(False)
        if result.host:
            self._openrgb_addr.setText(
                format_openrgb_address(result.host, result.port))
        if not result.enabled:
            text = ""
        elif result.error:
            text = f"Not reachable at {result.host}:{result.port} — {result.error}"
        elif result.connected:
            text = f"Following on: {', '.join(result.devices) or 'no devices'}"
        else:
            text = f"Waiting for the cooler's colours ({result.host}:{result.port})"
        self._openrgb_status.setText(text)

    def show_openrgb_switch_only(self) -> None:
        """Put the switch back to the App's state, keeping the status line."""
        enabled = self._dispatch(OpenRgbSync()).enabled
        log.debug("show_openrgb_switch_only: enabled=%s", enabled)
        self._openrgb_check.blockSignals(True)
        self._openrgb_check.setChecked(enabled)
        self._openrgb_check.blockSignals(False)

    def _on_openrgb_toggled(self, checked: bool) -> None:
        address = parse_openrgb_address(self._openrgb_addr.text())
        log.info("_on_openrgb_toggled: checked=%s address=%s", checked, address)
        if address is None:
            self._openrgb_status.setText(
                "Address must be host:port, e.g. 127.0.0.1:6742")
            self.show_openrgb_switch_only()
            return
        host, port = address
        result = self._dispatch(SetOpenRgbSync(enabled=checked, host=host,
                                               port=port))
        if not result.ok:
            log.warning("_on_openrgb_toggled: refused — %s", result.message)
        self.show_openrgb()

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

    def _on_disk_selected(self, index: int) -> None:
        """Emit the chosen sensor KEY, not a positional index.

        A key survives re-enumeration; an index into a discovered list does
        not — which is why ``DiskSource.key`` was made stable and unique
        before this control existed.  ``SetDiskDevice`` is app-wide, so it
        takes no device key.
        """
        disk_key = self._disk_selector.itemData(index) or ""
        log.info("_on_disk_selected: index=%s key=%s",
                 index, disk_key or "(hottest)")
        self._dispatch(SetDiskDevice(disk_key=disk_key))

    def _on_memory_ratio_changed(self, index: int) -> None:
        ratio = self._memory_ratio.itemData(index)
        log.info("_on_memory_ratio_changed: ratio=%s", ratio)
        key = self.current_key()
        if key and isinstance(ratio, int):
            self._dispatch(SetMemoryRatio(key=key, ratio=ratio))
