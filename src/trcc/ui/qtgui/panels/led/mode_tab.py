"""ModeTab — pick the LED animation mode.

Six options, one Command:

* STATIC      — solid colour, no animation
* BREATHING   — sinusoidal brightness pulse
* COLORFUL    — six-phase gradient cycle per segment
* RAINBOW     — table-driven hue scroll across all segments
* TEMP_LINKED — colour derived from CPU/GPU temperature
* LOAD_LINKED — colour derived from CPU/GPU load

The dependent sensor-source pickers (which CPU? which GPU?) live in
:class:`AdvancedTab` so this tab stays a clean one-click choice.
Picking a mode dispatches :class:`SetLedMode`; settings persist so the
selection survives across launches.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from .....core.commands import SetLedMode
from .....core.led_models import LedDeviceSettings, LEDMode
from ._base import LedTabBase

_MODE_LABELS: tuple[tuple[LEDMode, str, str], ...] = (
    (LEDMode.STATIC,
     "Static",
     "Solid colour, no animation."),
    (LEDMode.BREATHING,
     "Breathing",
     "Brightness pulses up and down at a steady rate."),
    (LEDMode.COLORFUL,
     "Colourful",
     "Six-phase gradient cycle running across each segment."),
    (LEDMode.RAINBOW,
     "Rainbow",
     "Hue scrolls smoothly through the spectrum on every tick."),
    (LEDMode.TEMP_LINKED,
     "Temperature-linked",
     "Colour follows CPU or GPU temperature (pick the source on the "
     "Advanced tab)."),
    (LEDMode.LOAD_LINKED,
     "Load-linked",
     "Colour follows CPU or GPU load (pick the source on the Advanced tab)."),
)


class ModeTab(LedTabBase):
    """Radio-button mode picker.  Each click dispatches SetLedMode."""

    def __init__(self, app, key_provider, parent=None) -> None:
        super().__init__(app, key_provider, parent)
        self._radios: dict[LEDMode, QRadioButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("LED animation mode", self)
        title.setStyleSheet("font-weight: bold; font-size: 12pt;")
        root.addWidget(title)

        for mode, label, helper in _MODE_LABELS:
            radio = QRadioButton(label, self)
            radio.setToolTip(helper)
            radio.toggled.connect(
                lambda checked, m=mode: self._on_radio_toggled(m, checked),
            )
            self._radios[mode] = radio
            self._group.addButton(radio)
            root.addWidget(radio)

            helper_label = QLabel(helper, self)
            helper_label.setWordWrap(True)
            helper_label.setStyleSheet("color: #888; padding-left: 22px;")
            helper_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction,
            )
            root.addWidget(helper_label)

        root.addStretch(1)

    # ── Public ────────────────────────────────────────────────────────

    def refresh_from(self, settings: LedDeviceSettings | None) -> None:
        if settings is None:
            return
        radio = self._radios.get(settings.mode)
        if radio is not None:
            radio.blockSignals(True)
            radio.setChecked(True)
            radio.blockSignals(False)

    # ── Handlers ──────────────────────────────────────────────────────

    def _on_radio_toggled(self, mode: LEDMode, checked: bool) -> None:
        if not checked:
            return
        key = self.current_key()
        if not key:
            return
        self._dispatch(SetLedMode(key=key, mode=mode))
