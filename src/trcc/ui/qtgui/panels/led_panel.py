"""LedPanel — host for the LED sub-tabs.

Owns the device-key picker + connection status header and hosts a
:class:`QTabWidget` that swaps between focused sub-panels:

* :class:`ColorTab`    — global colour + brightness + presets
* :class:`ModeTab`     — pick the animation mode
* :class:`ZoneTab`     — per-zone colour + sync (hidden for single-zone)
* :class:`SegmentTab`  — per-segment toggles (hidden if no segments)
* :class:`AdvancedTab` — sensor source, test mode, clock options

The header watches the ``LedColorsChanged`` event so an external
mutation (another UI, an API client) refreshes every tab without the
user having to click Refresh.  Zone + Segment tabs decide for
themselves whether they have content to show, so the panel asks each
on every refresh and hides the irrelevant ones.

Replaces the legacy 1390-line ``uc_led_control.py`` monolith.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
)

from ....core.led_models import LEGACY_STYLE_ID
from ...presentation.led_panel import led_panel_for
from ..base import BasePanel
from ..device_picker import DevicePickerWidget
from .led import AdvancedTab, ColorTab, ModeTab, SegmentTab, ZoneTab

log = logging.getLogger(__name__)


class LedPanel(BasePanel):
    """Tabbed LED control surface for a single LED device key."""

    def _setup_ui(self) -> None:
        # ── Header: device key + status ───────────────────────────────
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="led", parent=self,
        )
        self._picker.key_changed.connect(self._on_key_changed)

        header_form = QFormLayout()
        header_form.addRow("Device key:", self._picker)

        header_row = QHBoxLayout()
        header_row.addLayout(header_form, stretch=1)

        self._status_label = QLabel(
            "Enter an LED device key (e.g. 0416:8001) and pick a tab.",
            self,
        )
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #aaa;")

        # ── Tabs ─────────────────────────────────────────────────────
        self._tabs = QTabWidget(self)

        self._color_tab = ColorTab(self.app, self._current_key, self._tabs)
        self._mode_tab = ModeTab(self.app, self._current_key, self._tabs)
        self._zone_tab = ZoneTab(self.app, self._current_key, self._tabs)
        self._segment_tab = SegmentTab(self.app, self._current_key, self._tabs)
        self._advanced_tab = AdvancedTab(
            self.app, self._current_key, self._tabs,
        )

        self._tabs.addTab(self._color_tab, "Colour")
        self._tabs.addTab(self._mode_tab, "Mode")
        # Indices used for show/hide.  Qt re-numbers when tabs are
        # added/removed, so we always look up by widget reference.
        self._tabs.addTab(self._zone_tab, "Zones")
        self._tabs.addTab(self._segment_tab, "Segments")
        self._tabs.addTab(self._advanced_tab, "Advanced")

        # ── Compose ──────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addLayout(header_row)
        root.addWidget(self._status_label)
        root.addWidget(self._tabs, stretch=1)

        # Refresh whenever the LED settings change from elsewhere (CLI,
        # API, another GUI).  QueuedConnection is the safe default —
        # the event might fire from a background thread.
        self._bus.led_colors_changed.connect(
            self._on_led_settings_changed,
            type=Qt.ConnectionType.QueuedConnection,
        )

    # ── Key plumbing ─────────────────────────────────────────────────

    def _current_key(self) -> str:
        return self._picker.current_key()

    def _on_key_changed(self, _key: str = "") -> None:
        log.info("_on_key_changed: _key=%s", _key)
        self._refresh_all_tabs()

    def _refresh_all_tabs(self) -> None:
        log.debug("_refresh_all_tabs")
        key = self._current_key()
        if not key:
            self._status_label.setText(
                "Pick an LED device to load its state.  Open the "
                "Devices panel to scan if no devices are listed.",
            )
            self._set_optional_tabs_visible(zones=False, segments=False)
            self._advanced_tab.apply_panel(led_panel_for(1))   # plain default
            return
        settings = self.app.settings.for_led(key)
        for tab in (
            self._color_tab,
            self._mode_tab,
            self._zone_tab,
            self._segment_tab,
            self._advanced_tab,
        ):
            tab.refresh_from(settings)
        # Zone + Segment tabs may have nothing to show — hide them
        # rather than confusing the user with empty editors.
        self._set_optional_tabs_visible(
            zones=self._zone_tab.has_visible_content(),
            segments=self._segment_tab.has_visible_content(),
        )

        device = self.app.devices.get(key)
        style = getattr(device.info, "led_style", None) if device else None
        # Gate the Advanced tab's sub-sections to this device's LED style —
        # the same C#-sourced composition the gui renders from.  Unknown
        # style falls back to a plain panel (gauges only).
        sid = LEGACY_STYLE_ID.get(style) if style else None
        self._advanced_tab.apply_panel(led_panel_for(sid if sid is not None else 1))

        if device is None:
            self._status_label.setText(
                f"{key} isn't connected yet — settings will apply when "
                "the device shows up.  Open the Device tab to scan.",
            )
        else:
            style_name = style.name if style else "no style detected"
            self._status_label.setText(
                f"{key} • {device.info.vendor} {device.info.product} • "
                f"style {style_name}",
            )

    def _set_optional_tabs_visible(
        self, *, zones: bool, segments: bool,
    ) -> None:
        # The two optional tabs are added late so they always sit at
        # the right of the bar.  Use widget lookups, not stored
        # indices, since indices shift when other tabs are removed.
        for widget, show in (
            (self._zone_tab, zones),
            (self._segment_tab, segments),
        ):
            idx = self._tabs.indexOf(widget)
            if show and idx == -1:
                # Re-add at a stable position — after Mode, before Advanced.
                insert_at = self._tabs.indexOf(self._advanced_tab)
                label = "Zones" if widget is self._zone_tab else "Segments"
                self._tabs.insertTab(insert_at, widget, label)
            elif not show and idx != -1:
                self._tabs.removeTab(idx)

    # ── Bus subscription ─────────────────────────────────────────────

    def _on_led_settings_changed(self, event) -> None:
        log.info("_on_led_settings_changed")
        if event.key != self._current_key():
            return
        self._refresh_all_tabs()
