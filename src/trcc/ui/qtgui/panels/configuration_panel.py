"""ConfigurationPanel — per-device knobs that don't fit elsewhere.

Bundles the smaller settings into one screen so users find them
together instead of hunting tabs:

* Fit / split mode, the overlay switch, the device's clock and date format
* Background mode (theme / color / transparent) + the color picker
* Slideshow controls (themes + interval + on/off)
* Game mode (on/off + the CPU threshold)
* Application settings (temperature unit, language, refresh interval)

Every control SHOWS what the App holds — on open, when the device changes,
and when any UI changes it — and sends its one Command when the user changes
it, like the gui and the Windows app.  There used to be two "Apply" buttons
that re-sent every control: the controls were never loaded, so one press
reset up to 11 settings nobody touched (measured 2026-09-30: German ->
Arabic, °F -> °C, 180° -> 0°, the slideshow wiped...).  Only the slideshow's
theme list keeps a button, because a list is edited before it is sent.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from functools import partial
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ....core._colors import parse_hex
from ....core.commands import (
    Command,
    ConfigureSlideshow,
    ControlCenterSnapshot,
    EnableOverlay,
    LcdSnapshot,
    ListLanguages,
    SetBackgroundMode,
    SetDateFormat,
    SetFitMode,
    SetGameMode,
    SetLanguage,
    SetOverlayBackground,
    SetRefreshInterval,
    SetSlideshow,
    SetSplitMode,
    SetTempUnit,
    SetTimeFormat,
)
from ....core.models import MAX_REFRESH_INTERVAL_S, MIN_REFRESH_INTERVAL_S
from ....core.results import Result
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

if TYPE_CHECKING:
    from ....core.events import SlideshowChanged

log = logging.getLogger(__name__)


def _combo(parent: Any, items: Sequence[tuple[Any, str]]) -> QComboBox:
    """A combo of ``(value, label)`` items."""
    log.debug("_combo: %d item(s)", len(items))
    combo = QComboBox(parent)
    for value, label in items:
        combo.addItem(label, userData=value)
    return combo


class ConfigurationPanel(BasePanel):
    """Bundled device-config knobs (split / fit / background / slideshow)."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd",
            parent=self, selection=self._selection,
        )
        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)

        # ── Display group ──
        display_box = QGroupBox("Display", self)
        display_form = QFormLayout(display_box)
        self._fit = _combo(display_box, (
            ("width", "Width (letterbox top/bottom)"),
            ("height", "Height (pillarbox left/right)"),
            ("stretch", "Stretch (fill both, distort)"),
        ))
        self._split = _combo(display_box, (
            (0, "Off"), (1, "Style A"), (2, "Style B"), (3, "Style C"),
        ))
        self._overlay = _combo(display_box, ((True, "On"), (False, "Off")))
        self._clock = _combo(display_box, (("24h", "24-hour"),
                                           ("12h", "12-hour")))
        # EDITABLE, because the pattern language is open: four tokens
        # (``yyyy`` ``yy`` ``MM`` ``dd``, see ``services/_clock._PATTERN_RULES``)
        # with any separator passed through, so a fixed list would cap what the
        # CLI already accepts.  The presets are the two the C# oracle ships
        # plus the common European and ISO orders.
        self._date = QComboBox(display_box)
        self._date.setEditable(True)
        for pattern in ("yyyy/MM/dd", "dd/MM/yyyy", "MM/dd/yyyy",
                        "dd.MM.yyyy", "yyyy-MM-dd"):
            self._date.addItem(pattern, userData=pattern)
        self._shown_date = ""
        display_form.addRow("Fit mode:", self._fit)
        display_form.addRow("Split mode:", self._split)
        display_form.addRow("Metric overlay:", self._overlay)
        display_form.addRow("Clock format (every clock):", self._clock)
        display_form.addRow("Date format (every date):", self._date)

        # ── Background group ──
        bg_box = QGroupBox("Background", self)
        bg_form = QFormLayout(bg_box)
        self._bg_mode = _combo(bg_box, (
            ("theme", "Theme background"),
            ("color", "Solid color"),
            ("transparent", "Transparent (for screencast)"),
        ))
        self._bg_color = "#000000"
        self._bg_color_btn = QPushButton("Pick color…", bg_box)
        self._bg_color_btn.clicked.connect(self._pick_bg_color)
        self._bg_color_label = QLabel("#000000", bg_box)
        bg_color_row = QHBoxLayout()
        bg_color_row.addWidget(self._bg_color_btn)
        bg_color_row.addWidget(self._bg_color_label)
        bg_color_row.addStretch(1)
        bg_form.addRow("Mode:", self._bg_mode)
        bg_form.addRow("Color (when mode = Solid):", bg_color_row)

        # ── Slideshow group ──
        sl_box = QGroupBox("Slideshow", self)
        sl_form = QFormLayout(sl_box)
        self._slideshow_enabled = _combo(sl_box, ((False, "Off"), (True, "On")))
        self._slideshow_interval = QDoubleSpinBox(sl_box)
        self._slideshow_interval.setRange(1.0, 3600.0)
        self._slideshow_interval.setSuffix(" s")
        self._slideshow_themes = QPlainTextEdit(sl_box)
        self._slideshow_themes.setPlaceholderText(
            "One theme name per line, in rotation order.\n"
            "e.g.\n  My-Theme-A\n  My-Theme-B",
        )
        self._slideshow_save = QPushButton("Save slideshow", sl_box)
        self._slideshow_save.clicked.connect(self._save_slideshow)
        sl_form.addRow("State:", self._slideshow_enabled)
        sl_form.addRow("Interval:", self._slideshow_interval)
        sl_form.addRow("Themes:", self._slideshow_themes)
        sl_form.addRow("", self._slideshow_save)

        # ── Game mode group ──
        game_box = QGroupBox("Game mode", self)
        game_form = QFormLayout(game_box)
        self._game = _combo(game_box, ((False, "Off"), (True, "On")))
        self._game_threshold = QSpinBox(game_box)
        self._game_threshold.setRange(0, 99)      # the C#'s two-digit box
        self._game_threshold.setSuffix(" %")
        self._game_threshold.setKeyboardTracking(False)
        game_form.addRow("State:", self._game)
        game_form.addRow("Takes the panel above CPU:", self._game_threshold)
        game_form.addRow("", QLabel(
            "After 11 seconds above it the panel shows only its overlay, on "
            "black, until 11 seconds at or below it.", game_box))

        # ── Application group (all devices — no device key needed) ──
        app_box = QGroupBox("Application (all devices)", self)
        app_form = QFormLayout(app_box)
        self._temp_unit = _combo(app_box, (("C", "Celsius (°C)"),
                                           ("F", "Fahrenheit (°F)")))
        self._language = QComboBox(app_box)
        self._populate_languages()
        self._refresh = QDoubleSpinBox(app_box)
        self._refresh.setRange(MIN_REFRESH_INTERVAL_S, MAX_REFRESH_INTERVAL_S)
        self._refresh.setSingleStep(0.5)
        self._refresh.setSuffix(" s")
        # A typed value is sent once, when it is finished -- not per keystroke.
        self._refresh.setKeyboardTracking(False)
        app_form.addRow("Temperature unit:", self._temp_unit)
        app_form.addRow("Language:", self._language)
        app_form.addRow("Refresh interval:", self._refresh)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addLayout(key_form)
        root.addWidget(display_box)
        root.addWidget(bg_box)
        root.addWidget(sl_box, stretch=1)
        root.addWidget(game_box)
        root.addWidget(app_box)
        root.addWidget(self._status)

        self._wire_controls()
        self._follow_the_app()
        self._show_state()

    # ── One control, one Command ──────────────────────────────────────

    def _wire_controls(self) -> None:
        """Each control sends its Command when the USER changes it.

        Combos fire on ``activated``, which a programmatic update never
        emits; the spinbox is updated under blocked signals.  So showing the
        App's state can never write it back.
        """
        log.debug("_wire_controls")
        # (control, its Command, the field its value goes in) -- the key is
        # the selected device's.
        device: tuple[tuple[QComboBox, Callable[..., Command], str], ...] = (
            (self._fit, SetFitMode, "mode"),
            (self._split, SetSplitMode, "mode"),
            (self._overlay, EnableOverlay, "enabled"),
            (self._clock, SetTimeFormat, "fmt"),
            (self._bg_mode, SetBackgroundMode, "mode"),
            (self._slideshow_enabled, SetSlideshow, "enabled"),
        )
        for combo, command, field in device:
            combo.activated.connect(
                partial(self._send_device, combo, command, field))
        self._temp_unit.activated.connect(self._send_temp_unit)
        self._language.activated.connect(self._send_language)
        self._refresh.valueChanged.connect(self._send_refresh)
        self._game.activated.connect(self._send_game_enabled)
        self._game_threshold.valueChanged.connect(self._send_game_threshold)
        self._date.activated.connect(self._send_date)
        if (line_edit := self._date.lineEdit()) is not None:
            line_edit.editingFinished.connect(self._send_date)

    def _send_device(self, combo: QComboBox, command: Callable[..., Command],
                     field: str, _index: int) -> None:
        log.debug("_send_device: %s.%s=%r", getattr(command, "__name__", command),
                  field, combo.currentData())
        key = self._key()
        if key is not None:
            self._send(command(key=key, **{field: combo.currentData()}))

    def _send_temp_unit(self, _index: int) -> None:
        log.info("_send_temp_unit: %s", self._temp_unit.currentData())
        self._send(SetTempUnit(unit=str(self._temp_unit.currentData())))

    def _send_language(self, _index: int) -> None:
        log.info("_send_language: %s", self._language.currentData())
        self._send(SetLanguage(language=str(self._language.currentData())))

    def _send_refresh(self, seconds: float) -> None:
        log.info("_send_refresh: %.1fs", seconds)
        self._send(SetRefreshInterval(seconds=float(seconds)))

    def _send_game_enabled(self, _index: int) -> None:
        log.info("_send_game_enabled: %s", self._game.currentData())
        key = self._key()
        if key is not None:
            self._send(SetGameMode(key=key, enabled=bool(self._game.currentData())))

    def _send_game_threshold(self, percent: int) -> None:
        log.info("_send_game_threshold: %d%%", percent)
        key = self._key()
        if key is not None:
            self._send(SetGameMode(key=key, threshold=percent))

    def _send_date(self, _index: int = -1) -> None:
        """A typed pattern is sent when it is finished, and only if it
        differs from what is shown -- a focus change alone sends nothing."""
        text = self._date.currentText().strip()
        key = self._key()
        if not text or text == self._shown_date or key is None:
            log.debug("_send_date: %r unchanged or empty — nothing sent", text)
            return
        self._shown_date = text
        self._send(SetDateFormat(fmt=text, key=key))

    def _save_slideshow(self) -> None:
        log.info("_save_slideshow")
        key = self._key()
        if key is None:
            return
        themes = tuple(
            line.strip()
            for line in self._slideshow_themes.toPlainText().splitlines()
            if line.strip()
        )
        self._send(ConfigureSlideshow(
            key=key, themes=themes,
            interval_s=float(self._slideshow_interval.value()),
        ))

    def _pick_bg_color(self) -> None:
        log.debug("_pick_bg_color")
        key = self._key()
        if key is None:
            return
        picked = QColorDialog.getColor(
            QColor(self._bg_color), self, "Pick background color",
        )
        if picked.isValid():
            self._send(SetOverlayBackground(key=key,
                                            color=_hex_to_rgb(picked.name())))

    def _send(self, command: Command) -> Result:
        log.info("_send: %s", command)
        result = self.dispatch(command)
        self._status.setText(result.message)
        return result

    # ── Showing what the App holds ────────────────────────────────────

    def _follow_the_app(self) -> None:
        """Re-show on a device pick and whenever any UI changes a setting."""
        log.debug("_follow_the_app")
        qconn = Qt.ConnectionType.QueuedConnection
        self._picker.key_changed.connect(self._on_key_changed)
        for signal in (self._bus.settings_changed, self._bus.theme_loaded):
            signal.connect(self._on_device_event, type=qconn)
        self._bus.app_settings_changed.connect(self._on_app_event, type=qconn)
        # The slideshow's controls follow its saved state, whoever changed it
        # -- including the App switching it off when another source took over.
        self._bus.slideshow_changed.connect(self._on_slideshow_changed,
                                            type=qconn)

    def _on_key_changed(self, key: str) -> None:
        log.info("_on_key_changed: %s", key)
        self._show_state()

    def _on_device_event(self, event: Any) -> None:
        if event.key == self._picker.current_key():
            log.debug("_on_device_event: %s for %s", type(event).__name__,
                      event.key)
            self._show_state()

    def _on_app_event(self, event: Any) -> None:
        log.debug("_on_app_event: %s", type(event).__name__)
        self._show_state()

    def _show_state(self) -> None:
        """Put the App's values on every control.  Sends nothing."""
        cc = self.dispatch(ControlCenterSnapshot())
        self._select_combo_by_data(self._temp_unit, cc.temp_unit)
        self._select_combo_by_data(self._language, cc.language)
        self._refresh.blockSignals(True)
        self._refresh.setValue(float(cc.refresh_interval_s))
        self._refresh.blockSignals(False)
        key = self._picker.current_key()
        snap = self.dispatch(LcdSnapshot(key=key)) if key else None
        if snap is None or not snap.ok:
            log.info("_show_state: app settings only (device %r: %s)", key,
                     snap.message if snap else "none selected")
            self._status.setText(
                "Pick a device to see and change its settings." if snap is None
                else snap.message)
            return
        log.info("_show_state: %s fit=%s split=%s overlay=%s bg=%s clock=%s "
                 "date=%s game=%s/%d%%", key, snap.fit_mode, snap.split_mode,
                 snap.overlay_enabled, snap.background_mode, snap.time_format,
                 snap.date_format, snap.game_enabled, snap.game_threshold)
        self._select_combo_by_data(self._fit, snap.fit_mode)
        self._select_combo_by_data(self._split, snap.split_mode)
        self._select_combo_by_data(self._overlay, snap.overlay_enabled)
        self._select_combo_by_data(self._bg_mode, snap.background_mode)
        self._select_combo_by_data(self._clock, snap.time_format)
        self._shown_date = snap.date_format
        self._date.setEditText(snap.date_format)
        r, g, b = snap.overlay_background
        self._bg_color = f"#{r:02x}{g:02x}{b:02x}"
        self._bg_color_label.setText(self._bg_color)
        self._show_slideshow(snap.slideshow_enabled, snap.slideshow_interval_s,
                             snap.slideshow_themes)
        self._select_combo_by_data(self._game, snap.game_enabled)
        self._game_threshold.blockSignals(True)
        self._game_threshold.setValue(snap.game_threshold)
        self._game_threshold.blockSignals(False)

    def _show_slideshow(self, enabled: bool, interval_s: float,
                        themes: Sequence[str]) -> None:
        """Put a saved slideshow state into the three slideshow controls."""
        log.info("_show_slideshow: enabled=%s interval=%ss themes=%d",
                 enabled, interval_s, len(themes))
        self._select_combo_by_data(self._slideshow_enabled, enabled)
        self._slideshow_interval.setValue(float(interval_s))
        self._slideshow_themes.setPlainText("\n".join(themes))

    def _on_slideshow_changed(self, event: SlideshowChanged) -> None:
        showing = self._picker.current_key()
        log.info("_on_slideshow_changed: key=%s enabled=%s (showing %s)",
                 event.key, event.enabled, showing)
        if event.key == showing:
            self._show_slideshow(event.enabled, event.interval_s, event.themes)

    # ── Helpers ───────────────────────────────────────────────────────

    def _key(self) -> str | None:
        log.debug("_key")
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first.  Use the Devices panel to scan "
                "if no devices are listed.",
            )
            return None
        return key

    def _populate_languages(self) -> None:
        """Fill the language combo from the live i18n table (self-healing —
        a newly-translated language shows up automatically)."""
        log.debug("_populate_languages")
        result = self.dispatch(ListLanguages())
        self._language.clear()
        for entry in result.languages:
            self._language.addItem(
                f"{entry.name} ({entry.code})", userData=entry.code,
            )

    @staticmethod
    def _select_combo_by_data(combo: QComboBox, value: Any) -> None:
        log.debug("_select_combo_by_data: combo=%s value=%s", combo, value)
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse #rrggbb → (r, g, b); fall back to black on bad input."""
    log.debug("_hex_to_rgb: hex_color=%s", hex_color)
    try:
        r, g, b, _a = parse_hex(hex_color)
    except ValueError:
        return (0, 0, 0)
    return (r, g, b)
