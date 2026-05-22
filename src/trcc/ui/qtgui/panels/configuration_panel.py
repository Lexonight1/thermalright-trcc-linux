"""ConfigurationPanel — per-device knobs that don't fit elsewhere.

Bundles the smaller settings into one screen so users find them
together instead of hunting tabs:

* Split mode (widescreen Dynamic Island)
* Fit mode (width / height / stretch)
* Background mode (theme / color / transparent) + the color picker
* Slideshow controls (themes + interval + on/off)
* Overlay master toggle

The pattern: one widget per setting, "Apply" button at the bottom
dispatches every Command in order, status line lists results.  Users
who change only one thing still hit Apply — fine, the no-op Commands
return ``ok=True`` with "no change" messages.
"""
from __future__ import annotations

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
    QVBoxLayout,
)

from ....core.commands import (
    ConfigureSlideshow,
    EnableOverlay,
    LcdSnapshot,
    SetBackgroundMode,
    SetFitMode,
    SetOverlayBackground,
    SetSlideshow,
    SetSplitMode,
)
from ..base import BasePanel
from ..device_picker import DevicePickerWidget


class ConfigurationPanel(BasePanel):
    """Bundled device-config knobs (split / fit / background / slideshow)."""

    def _setup_ui(self) -> None:
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
        )

        load_btn = QPushButton("Load current settings", self)
        load_btn.clicked.connect(self._load_from_snapshot)

        key_row = QHBoxLayout()
        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)
        key_row.addLayout(key_form, stretch=1)
        key_row.addWidget(load_btn)

        # ── Display group ──
        display_box = QGroupBox("Display", self)
        display_form = QFormLayout(display_box)

        self._fit = QComboBox(display_box)
        for value, label in (
            ("width",   "Width (letterbox top/bottom)"),
            ("height",  "Height (pillarbox left/right)"),
            ("stretch", "Stretch (fill both, distort)"),
        ):
            self._fit.addItem(label, userData=value)

        self._split = QComboBox(display_box)
        for value, label in (
            (0, "Off"),
            (1, "Style A"),
            (2, "Style B"),
            (3, "Style C"),
        ):
            self._split.addItem(label, userData=value)

        self._overlay = QComboBox(display_box)
        self._overlay.addItem("On",  userData=True)
        self._overlay.addItem("Off", userData=False)

        display_form.addRow("Fit mode:", self._fit)
        display_form.addRow("Split mode:", self._split)
        display_form.addRow("Metric overlay:", self._overlay)

        # ── Background group ──
        bg_box = QGroupBox("Background", self)
        bg_form = QFormLayout(bg_box)
        self._bg_mode = QComboBox(bg_box)
        for value, label in (
            ("theme",       "Theme background"),
            ("color",       "Solid color"),
            ("transparent", "Transparent (for screencast)"),
        ):
            self._bg_mode.addItem(label, userData=value)
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
        self._slideshow_enabled = QComboBox(sl_box)
        self._slideshow_enabled.addItem("Off", userData=False)
        self._slideshow_enabled.addItem("On",  userData=True)
        self._slideshow_interval = QDoubleSpinBox(sl_box)
        self._slideshow_interval.setRange(1.0, 3600.0)
        self._slideshow_interval.setSuffix(" s")
        self._slideshow_interval.setValue(60.0)
        self._slideshow_themes = QPlainTextEdit(sl_box)
        self._slideshow_themes.setPlaceholderText(
            "One theme name per line, in rotation order.\n"
            "e.g.\n  My-Theme-A\n  My-Theme-B",
        )
        sl_form.addRow("State:", self._slideshow_enabled)
        sl_form.addRow("Interval:", self._slideshow_interval)
        sl_form.addRow("Themes:", self._slideshow_themes)

        # ── Apply ──
        self._apply_btn = QPushButton("Apply all settings", self)
        self._apply_btn.clicked.connect(self._apply)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addLayout(key_row)
        root.addWidget(display_box)
        root.addWidget(bg_box)
        root.addWidget(sl_box, stretch=1)
        root.addWidget(self._apply_btn)
        root.addWidget(self._status)

    # ── Helpers ───────────────────────────────────────────────────────

    def _key(self) -> str | None:
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first.  Use the Devices panel to scan "
                "if no devices are listed.",
            )
            return None
        return key

    def _pick_bg_color(self) -> None:
        picked = QColorDialog.getColor(
            QColor(self._bg_color), self, "Pick background color",
        )
        if picked.isValid():
            self._bg_color = picked.name()
            self._bg_color_label.setText(self._bg_color)

    def _load_from_snapshot(self) -> None:
        key = self._key()
        if key is None:
            return
        snap = self.dispatch(LcdSnapshot(key=key))
        if not snap.ok:
            self._status.setText(snap.message)
            return
        self._select_combo_by_data(self._fit, snap.fit_mode)
        self._select_combo_by_data(self._split, snap.split_mode)
        self._select_combo_by_data(self._overlay, snap.overlay_enabled)
        # Background fields come from Settings since the snapshot doesn't
        # carry overlay_background; read directly.
        settings = self.app.settings.for_device(key)
        self._select_combo_by_data(self._bg_mode, settings.background_mode)
        r, g, b = settings.overlay_background
        self._bg_color = f"#{r:02x}{g:02x}{b:02x}"
        self._bg_color_label.setText(self._bg_color)
        # Slideshow fields.
        self._select_combo_by_data(
            self._slideshow_enabled, settings.slideshow_enabled,
        )
        self._slideshow_interval.setValue(float(settings.slideshow_interval_s))
        self._slideshow_themes.setPlainText(
            "\n".join(settings.slideshow_themes),
        )
        self._status.setText(f"Loaded settings for {key}.")

    @staticmethod
    def _select_combo_by_data(combo: QComboBox, value) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _apply(self) -> None:
        key = self._key()
        if key is None:
            return
        messages: list[str] = []

        # Fit mode
        r = self.dispatch(SetFitMode(
            key=key, mode=str(self._fit.currentData()),
        ))
        messages.append(r.message)

        # Split mode
        r2 = self.dispatch(SetSplitMode(
            key=key, mode=int(self._split.currentData()),
        ))
        messages.append(r2.message)

        # Overlay
        r3 = self.dispatch(EnableOverlay(
            key=key, enabled=bool(self._overlay.currentData()),
        ))
        messages.append(r3.message)

        # Background mode
        r4 = self.dispatch(SetBackgroundMode(
            key=key, mode=str(self._bg_mode.currentData()),
        ))
        messages.append(r4.message)

        # Background color
        rgb = _hex_to_rgb(self._bg_color)
        r5 = self.dispatch(SetOverlayBackground(key=key, color=rgb))
        messages.append(r5.message)

        # Slideshow themes + interval
        themes = tuple(
            line.strip()
            for line in self._slideshow_themes.toPlainText().splitlines()
            if line.strip()
        )
        r6 = self.dispatch(ConfigureSlideshow(
            key=key, themes=themes,
            interval_s=float(self._slideshow_interval.value()),
        ))
        messages.append(r6.message)

        # Slideshow on/off
        r7 = self.dispatch(SetSlideshow(
            key=key, enabled=bool(self._slideshow_enabled.currentData()),
        ))
        messages.append(r7.message)

        self._status.setText("  |  ".join(messages))


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse #rrggbb → (r, g, b); fall back to black on bad input."""
    s = hex_color.lstrip("#").strip()
    if len(s) != 6:
        return (0, 0, 0)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (0, 0, 0)
