"""ZoneTab — per-zone colour + zone-sync carousel.

For multi-zone LED devices (PA120, LF8, LF10, AK120, CZ1, LF11, …)
each zone can hold its own colour.  The carousel optionally rotates
which zone is lit on a fixed tick interval so users get a "scanner"
effect.

Hidden by :class:`LedPanel` when the active device has ≤1 zones — most
users will never see this tab.

Dispatches:
* :class:`SetLedZoneColor`           — one zone's colour
* :class:`SelectZone`                — UI's notion of "active zone"
* :class:`SetLedZoneSync`            — carousel on/off
* :class:`SetLedZoneSyncInterval`    — carousel tick interval
* :class:`ToggleLed` (zone=N)        — mute one zone without disturbing
                                       the rest
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .....core.commands import (
    SelectZone,
    SetLedZoneBrightness,
    SetLedZoneColor,
    SetLedZoneMode,
    SetLedZoneSync,
    SetLedZoneSyncInterval,
    ToggleLed,
)
from .....core.led_models import LEDMode
from .....core.results import LedSnapshotResult
from ._base import LedTabBase

log = logging.getLogger(__name__)


class _ZoneRow(QWidget):
    """One row: radio (active), label, colour swatch, on/off, pick button."""

    def __init__(
        self,
        index: int,
        on_pick,
        on_radio,
        on_toggle,
        on_mode,
        on_brightness,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._color = QColor(255, 0, 0)
        self._on_radio = on_radio
        self._on_mode = on_mode
        self._on_brightness = on_brightness

        self._radio = QRadioButton(f"Zone {index + 1}", self)
        self._radio.toggled.connect(self._on_radio_toggled)

        self._mode = QComboBox(self)
        for member in LEDMode:
            self._mode.addItem(
                member.name.replace("_", " ").title(), userData=int(member),
            )
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        self._brightness = QSpinBox(self)
        self._brightness.setRange(0, 100)
        self._brightness.setValue(65)
        self._brightness.setSuffix("%")
        self._brightness.editingFinished.connect(self._on_brightness_edited)

        self._swatch = QLabel(self)
        self._swatch.setFixedSize(40, 22)
        self._update_swatch()

        self._enabled = QCheckBox("On", self)
        self._enabled.setChecked(True)
        self._enabled.toggled.connect(
            lambda checked: on_toggle(self._index, checked),
        )

        self._pick_btn = QPushButton("Pick…", self)
        self._pick_btn.clicked.connect(
            lambda: on_pick(self._index, self._color),
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._radio)
        row.addStretch(1)
        row.addWidget(self._mode)
        row.addWidget(self._brightness)
        row.addWidget(self._swatch)
        row.addWidget(self._enabled)
        row.addWidget(self._pick_btn)

    def set_color(self, r: int, g: int, b: int) -> None:
        self._color = QColor(r, g, b)
        self._update_swatch()

    def set_enabled(self, on: bool, *, emit_signals: bool = True) -> None:
        if not emit_signals:
            self._enabled.blockSignals(True)
        self._enabled.setChecked(on)
        if not emit_signals:
            self._enabled.blockSignals(False)

    def set_active(self, active: bool) -> None:
        self._radio.blockSignals(True)
        self._radio.setChecked(active)
        self._radio.blockSignals(False)

    def _on_radio_toggled(self, checked: bool) -> None:
        log.info("_on_radio_toggled: checked=%s", checked)
        if checked:
            self._on_radio(self._index)

    def _update_swatch(self) -> None:
        self._swatch.setStyleSheet(
            f"background-color: {self._color.name()}; "
            "border: 1px solid #333;",
        )

    def set_mode(self, mode: LEDMode) -> None:
        self._mode.blockSignals(True)
        idx = self._mode.findData(int(mode))
        if idx >= 0:
            self._mode.setCurrentIndex(idx)
        self._mode.blockSignals(False)

    def set_brightness(self, percent: int) -> None:
        self._brightness.blockSignals(True)
        self._brightness.setValue(percent)
        self._brightness.blockSignals(False)

    def _on_mode_changed(self, _index: int) -> None:
        log.info("_on_mode_changed: _index=%s", _index)
        self._on_mode(self._index, LEDMode(int(self._mode.currentData())))

    def _on_brightness_edited(self) -> None:
        log.info("_on_brightness_edited")
        self._on_brightness(self._index, self._brightness.value())


class ZoneTab(LedTabBase):
    """Per-zone colour + sync carousel."""

    def __init__(self, app, key_provider, parent=None) -> None:
        super().__init__(app, key_provider, parent)
        self._zone_widgets: list[_ZoneRow] = []
        self._placeholder_visible = True
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "Each zone holds its own colour.  Pick the active zone "
            "with the radio button on the left; the global colour tab "
            "still drives the default for STATIC mode.",
            self,
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa;")
        root.addWidget(intro)

        self._zones_box = QGroupBox("Zones", self)
        self._zones_layout = QVBoxLayout(self._zones_box)
        root.addWidget(self._zones_box)

        sync_box = QGroupBox("Carousel", self)
        sync_form = QFormLayout(sync_box)
        self._sync_check = QCheckBox(
            "Enable zone-sync carousel", self,
        )
        self._sync_check.toggled.connect(self._on_sync_toggled)
        self._interval_spin = QSpinBox(self)
        self._interval_spin.setRange(1, 600)
        self._interval_spin.setValue(13)
        self._interval_spin.setSuffix(" ticks")
        self._interval_spin.editingFinished.connect(self._on_interval_changed)
        sync_form.addRow(self._sync_check)
        sync_form.addRow("Rotation interval:", self._interval_spin)
        root.addWidget(sync_box)

        self._placeholder = QLabel(
            "This device has no separately-addressable zones.",
            self,
        )
        self._placeholder.setStyleSheet("color: #aaa;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setVisible(False)
        root.addWidget(self._placeholder)

        root.addStretch(1)

    # ── Public ────────────────────────────────────────────────────────

    def refresh_from(self, snapshot: LedSnapshotResult | None) -> None:
        log.debug("refresh_from")
        if snapshot is None:
            self._show_placeholder(True)
            return
        zone_count = len(snapshot.zones)
        if zone_count <= 1:
            self._show_placeholder(True)
            self._rebuild_zone_rows(0)
            return
        self._show_placeholder(False)
        self._rebuild_zone_rows(zone_count)
        for i, zone in enumerate(snapshot.zones):
            row = self._zone_widgets[i]
            row.set_color(*zone.color)
            # ``LedZoneEntry.mode`` is the LEDMode NAME, and ``set_mode`` does
            # ``findData(int(mode))`` — ``int("STATIC")`` is a ValueError.
            row.set_mode(LEDMode[zone.mode])
            row.set_brightness(zone.brightness)
            row.set_enabled(zone.on, emit_signals=False)
            row.set_active(i == snapshot.selected_zone)

        self._sync_check.blockSignals(True)
        self._sync_check.setChecked(snapshot.zone_sync)
        self._sync_check.blockSignals(False)

        self._interval_spin.blockSignals(True)
        self._interval_spin.setValue(snapshot.zone_sync_interval_ticks)
        self._interval_spin.blockSignals(False)

    def has_visible_content(self) -> bool:
        """LedPanel uses this to decide whether to surface the tab."""
        return not self._placeholder_visible

    # ── Internals ─────────────────────────────────────────────────────

    def _show_placeholder(self, show: bool) -> None:
        self._placeholder_visible = show
        self._placeholder.setVisible(show)
        self._zones_box.setVisible(not show)

    def _rebuild_zone_rows(self, count: int) -> None:
        if count == len(self._zone_widgets):
            return
        # Wipe + recreate so we never carry stale widgets.
        while self._zones_layout.count():
            item = self._zones_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._zone_widgets = []
        for i in range(count):
            row = _ZoneRow(
                i,
                on_pick=self._on_pick_zone_color,
                on_radio=self._on_zone_radio,
                on_toggle=self._on_zone_toggle,
                on_mode=self._on_zone_mode,
                on_brightness=self._on_zone_brightness,
                parent=self._zones_box,
            )
            self._zones_layout.addWidget(row)
            self._zone_widgets.append(row)

    # ── Command dispatch ──────────────────────────────────────────────

    def _on_pick_zone_color(self, zone: int, current: QColor) -> None:
        log.info("_on_pick_zone_color: zone=%s", zone)
        from PySide6.QtWidgets import QColorDialog

        picked = QColorDialog.getColor(current, self, f"Pick zone {zone + 1} colour")
        if not picked.isValid():
            return
        key = self.current_key()
        if not key:
            return
        rgb = (picked.red(), picked.green(), picked.blue())
        result = self._dispatch(SetLedZoneColor(key=key, zone=zone, color=rgb))
        if result.ok and zone < len(self._zone_widgets):
            self._zone_widgets[zone].set_color(*rgb)

    def _on_zone_radio(self, zone: int) -> None:
        log.info("_on_zone_radio: zone=%s", zone)
        key = self.current_key()
        if key:
            self._dispatch(SelectZone(key=key, zone=zone))

    def _on_zone_toggle(self, zone: int, on: bool) -> None:
        log.info("_on_zone_toggle: zone=%s on=%s", zone, on)
        key = self.current_key()
        if key:
            self._dispatch(ToggleLed(key=key, on=on, zone=zone))

    def _on_zone_mode(self, zone: int, mode: LEDMode) -> None:
        key = self.current_key()
        if not key:
            return
        log.info("_on_zone_mode: zone=%d mode=%s", zone, mode.name)
        self._dispatch(SetLedZoneMode(key=key, zone=zone, mode=mode))

    def _on_zone_brightness(self, zone: int, percent: int) -> None:
        key = self.current_key()
        if not key:
            return
        log.info("_on_zone_brightness: zone=%d percent=%d", zone, percent)
        self._dispatch(SetLedZoneBrightness(key=key, zone=zone, percent=percent))

    def _on_sync_toggled(self, checked: bool) -> None:
        log.info("_on_sync_toggled: checked=%s", checked)
        key = self.current_key()
        if key:
            self._dispatch(SetLedZoneSync(key=key, enabled=checked))

    def _on_interval_changed(self) -> None:
        log.info("_on_interval_changed")
        key = self.current_key()
        if not key:
            return
        self._dispatch(SetLedZoneSyncInterval(
            key=key, ticks=self._interval_spin.value(),
        ))
