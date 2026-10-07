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
    SetLedZoneSyncZones,
    ToggleLed,
)
from .....core.led_models import LEDMode
from .....core.results import LedSnapshotResult
from ....presentation.led_display import LedSelector, led_display_for
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
        log.debug("__init__: index=%s on_pick=%s", index, on_pick)
        super().__init__(parent)
        self._index = index
        self._color = QColor(255, 0, 0)
        self._on_radio = on_radio
        self._on_mode = on_mode
        self._on_brightness = on_brightness
        # Held like the three above rather than captured in a closure, so the
        # slots below can be named methods — ``feedback_no_lambdas``.
        self._on_pick = on_pick
        self._on_toggle = on_toggle

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
        # Finished AND changed only -- editingFinished fired on focus alone.
        self._brightness.setKeyboardTracking(False)
        self._brightness.valueChanged.connect(self._on_brightness_edited)

        self._swatch = QLabel(self)
        self._swatch.setFixedSize(40, 22)
        self._update_swatch()

        self._enabled = QCheckBox("On", self)
        self._enabled.setChecked(True)
        self._enabled.toggled.connect(self._on_enabled_toggled)

        self._pick_btn = QPushButton("Pick…", self)
        self._pick_btn.clicked.connect(self._on_pick_clicked)

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
        log.debug("set_color: r=%s g=%s", r, g)
        self._color = QColor(r, g, b)
        self._update_swatch()

    def set_enabled(self, on: bool, *, emit_signals: bool = True) -> None:
        log.debug("set_enabled: on=%s", on)
        if not emit_signals:
            self._enabled.blockSignals(True)
        self._enabled.setChecked(on)
        if not emit_signals:
            self._enabled.blockSignals(False)

    def set_active(self, active: bool) -> None:
        log.debug("set_active: active=%s", active)
        self._radio.blockSignals(True)
        self._radio.setChecked(active)
        self._radio.blockSignals(False)

    def _on_enabled_toggled(self, checked: bool) -> None:
        """This zone's On box changed — tell the tab which zone."""
        log.debug("_on_enabled_toggled: index=%s checked=%s",
                  self._index, checked)
        self._on_toggle(self._index, checked)

    def _on_pick_clicked(self) -> None:
        """Pick… pressed — hand the tab this zone and its current colour."""
        log.info("_on_pick_clicked: index=%s", self._index)
        self._on_pick(self._index, self._color)

    def _on_radio_toggled(self, checked: bool) -> None:
        log.info("_on_radio_toggled: checked=%s", checked)
        if checked:
            self._on_radio(self._index)

    def _update_swatch(self) -> None:
        log.debug("_update_swatch")
        self._swatch.setStyleSheet(
            f"background-color: {self._color.name()}; "
            "border: 1px solid #333;",
        )

    def set_mode(self, mode: LEDMode) -> None:
        log.debug("set_mode: mode=%s", mode)
        self._mode.blockSignals(True)
        idx = self._mode.findData(int(mode))
        if idx >= 0:
            self._mode.setCurrentIndex(idx)
        self._mode.blockSignals(False)

    def set_brightness(self, percent: int) -> None:
        log.debug("set_brightness: percent=%s", percent)
        self._brightness.blockSignals(True)
        self._brightness.setValue(percent)
        self._brightness.blockSignals(False)

    def _on_mode_changed(self, _index: int) -> None:
        log.info("_on_mode_changed: _index=%s", _index)
        self._on_mode(self._index, LEDMode(int(self._mode.currentData())))

    def _on_brightness_edited(self) -> None:
        log.info("_on_brightness_edited")
        self._on_brightness(self._index, self._brightness.value())


_ZONES_INTRO = ("Each zone holds its own colour.  Pick the active zone "
                "with the radio button on the left; the global colour tab "
                "still drives the default for STATIC mode.")
_PAGES_INTRO = ("This display shows one reading at a time.  Pick the one it "
                "shows, or turn the carousel on and tick the ones it cycles "
                "through.")


class ZoneTab(LedTabBase):
    """Per-zone colour + sync carousel."""

    def __init__(self, app, key_provider, parent=None) -> None:
        log.debug("__init__: app=%s key_provider=%s", app, key_provider)
        super().__init__(app, key_provider, parent)
        self._zone_widgets: list[_ZoneRow] = []
        self._placeholder_visible = True
        #: The metric pages of a PAGE-style display (AX120, AK120, LF8, ...),
        #: from ``apply_style``; empty on zone and no-selector styles.
        self._pages: tuple[str, ...] = ()
        self._participation_labels: tuple[str, ...] = ()
        self._build_ui()

    def _build_ui(self) -> None:
        log.debug("_build_ui")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._intro = QLabel(_ZONES_INTRO, self)
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet("color: #aaa;")
        root.addWidget(self._intro)

        self._zones_box = QGroupBox("Zones", self)
        self._zones_layout = QVBoxLayout(self._zones_box)
        root.addWidget(self._zones_box)

        self._sync_box = sync_box = QGroupBox("Carousel", self)
        sync_form = QFormLayout(sync_box)
        self._sync_check = QCheckBox(
            "Enable zone-sync carousel", self,
        )
        self._sync_check.toggled.connect(self._on_sync_toggled)
        self._interval_spin = QSpinBox(self)
        self._interval_spin.setRange(1, 600)
        self._interval_spin.setValue(13)
        self._interval_spin.setSuffix(" ticks")
        self._interval_spin.setKeyboardTracking(False)
        self._interval_spin.valueChanged.connect(self._on_interval_changed)
        sync_form.addRow(self._sync_check)
        self._interval_label = QLabel("Rotation interval:", sync_box)
        sync_form.addRow(self._interval_label, self._interval_spin)
        # WHICH zones the carousel visits.  Without this mask it stays empty
        # and ``next_sync_zone`` is stuck on page 0 -- the carousel never
        # advances however the user sets the switch above, so the feature
        # looks present and does nothing.
        self._participation = QWidget(sync_box)
        self._participation_layout = QHBoxLayout(self._participation)
        self._participation_layout.setContentsMargins(0, 0, 0, 0)
        self._participation_checks: list[QCheckBox] = []
        self._participation_label = QLabel("Rotate through:", sync_box)
        sync_form.addRow(self._participation_label, self._participation)
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

    def apply_style(self, style_id: int | None) -> None:
        """Know the device's LED style -- a PAGE style has no zones, but its
        pages are picked here, as the gui's selector buttons pick them."""
        display = led_display_for(style_id or 0)
        self._pages = (display.page_labels
                       if display.selector is LedSelector.PAGE else ())
        # PA120 / LF10: the switch selects EVERY zone for an edit and nothing
        # rotates -- the C#'s panel reads "Select all" there and hides its
        # timer (FormLED.cs:1669, :1724).  "Carousel" said otherwise.
        select_all = display.selector is LedSelector.ZONE
        self._sync_box.setTitle("Select all" if select_all else "Carousel")
        self._sync_check.setText("Edit every zone at once" if select_all
                                 else "Enable zone-sync carousel")
        for widget in (self._interval_label, self._interval_spin,
                       self._participation_label, self._participation):
            widget.setVisible(not select_all)
        log.info("apply_style: style=%s pages=%s select_all=%s",
                 style_id, self._pages, select_all)

    def refresh_from(self, snapshot: LedSnapshotResult | None) -> None:
        log.debug("refresh_from")
        if snapshot is None:
            self._show_placeholder(True)
            return
        zone_count = len(snapshot.zones)
        pages = self._pages if zone_count <= 1 else ()
        if zone_count <= 1 and not pages:
            self._show_placeholder(True)
            self._rebuild_zone_rows(0)
            return
        self._show_placeholder(False)
        self._zones_box.setVisible(not pages)
        self._intro.setText(_PAGES_INTRO if pages else _ZONES_INTRO)
        self._rebuild_zone_rows(0 if pages else zone_count)
        if pages:
            self._rebuild_participation(pages)
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

        # The zones the App actually uses: a missing entry is off, and with
        # none on it falls back to zone 0 -- ``_edit_zones`` for edits,
        # ``next_sync_zone`` for the carousel, and the C#'s default
        # (``LunBo1`` only).  This showed a missing entry as ON, so a fresh
        # device read "every zone" while the App drove zone 0 alone.
        mask = snapshot.zone_sync_zones or ()
        used = {i for i, on in enumerate(mask) if on} or {0}
        log.debug("refresh_from: zone mask %s -> showing %s", list(mask), sorted(used))
        for i, box in enumerate(self._participation_checks):
            box.blockSignals(True)
            box.setChecked(i in used)
            box.blockSignals(False)

    # ── Internals ─────────────────────────────────────────────────────

    def _show_placeholder(self, show: bool) -> None:
        log.debug("_show_placeholder: show=%s", show)
        self._placeholder_visible = show
        self._placeholder.setVisible(show)
        self._zones_box.setVisible(not show)

    def _rebuild_zone_rows(self, count: int) -> None:
        log.debug("_rebuild_zone_rows: count=%s", count)
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
        self._rebuild_participation(tuple(str(i + 1) for i in range(count)))

    def _rebuild_participation(self, labels: tuple[str, ...]) -> None:
        """One checkbox per zone or page -- the carousel's mask."""
        log.debug("_rebuild_participation: %s", labels)
        if labels == self._participation_labels:
            return
        self._participation_labels = labels
        while self._participation_layout.count():
            item = self._participation_layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._participation_checks = []
        for label in labels:
            box = QCheckBox(label, self._participation)
            box.toggled.connect(self._on_participation_changed)
            self._participation_layout.addWidget(box)
            self._participation_checks.append(box)
        self._participation_layout.addStretch(1)

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

    def _on_participation_changed(self, checked: bool) -> None:
        """Send the WHOLE mask — ``SetLedZoneSyncZones`` replaces it wholesale.

        Except on a page display with the carousel off: there a click picks
        exactly that page (FormLED :2812, ``SelectZone``), and the shown page
        cannot be un-picked -- the C#'s last selection cannot be deselected.
        """
        mask = tuple(b.isChecked() for b in self._participation_checks)
        key = self.current_key()
        picking = bool(self._pages) and not self._sync_check.isChecked()
        log.info("_on_participation_changed: mask=%s picking=%s", mask, picking)
        if not key:
            return
        if not picking:
            self._dispatch(SetLedZoneSyncZones(key=key, zones=mask))
            return
        box = self.sender()
        if not isinstance(box, QCheckBox) or box not in self._participation_checks:
            log.warning("_on_participation_changed: unknown sender %r", box)
            return
        if not checked:                      # the page shown stays shown
            box.blockSignals(True)
            box.setChecked(True)
            box.blockSignals(False)
            return
        self._dispatch(SelectZone(key=key,
                                  zone=self._participation_checks.index(box)))

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
