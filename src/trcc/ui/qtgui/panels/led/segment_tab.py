"""SegmentTab — toggle individual segments on/off.

Segment-display devices (LC1, LF8, LF12, LF10, CZ1, LC2, LF11)
expose a configurable number of segments — usually 8 to 16 — that
can be individually disabled.  Users hide segments they find
distracting (e.g. the second clock digit on LC2 if they only want
the temperature reading).

Hidden by :class:`LedPanel` when the device has no segments.

Dispatches :class:`ToggleSegment` per click.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
)

from .....core.commands import ToggleSegment
from .....core.led_models import LedDeviceSettings
from ._base import LedTabBase

_GRID_COLUMNS = 4


class SegmentTab(LedTabBase):
    """Per-segment visibility checkboxes."""

    def __init__(self, app, key_provider, parent=None) -> None:
        super().__init__(app, key_provider, parent)
        self._checks: list[QCheckBox] = []
        self._placeholder_visible = True
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "Toggle individual segments on or off.  Disabled segments "
            "still receive the LED packet but render as black, so the "
            "rest of the display stays in sync.",
            self,
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa;")
        root.addWidget(intro)

        self._grid = QGridLayout()
        self._grid.setSpacing(6)
        root.addLayout(self._grid)

        self._placeholder = QLabel(
            "This device has no segment display — there's nothing to toggle.",
            self,
        )
        self._placeholder.setStyleSheet("color: #aaa;")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setVisible(False)
        root.addWidget(self._placeholder)
        root.addStretch(1)

    # ── Public ────────────────────────────────────────────────────────

    def refresh_from(self, settings: LedDeviceSettings | None) -> None:
        if settings is None or not settings.segment_on:
            self._show_placeholder(True)
            return
        self._show_placeholder(False)
        self._rebuild_checks(settings.segment_on)

    def has_visible_content(self) -> bool:
        return not self._placeholder_visible

    # ── Internals ─────────────────────────────────────────────────────

    def _show_placeholder(self, show: bool) -> None:
        self._placeholder_visible = show
        self._placeholder.setVisible(show)
        # Clear the grid if showing the placeholder.
        if show:
            self._rebuild_checks([])

    def _rebuild_checks(self, segment_on: list[bool]) -> None:
        if len(self._checks) != len(segment_on):
            # Wipe + rebuild — segment counts only change when the
            # connected device changes, so this is cheap.
            while self._grid.count():
                item = self._grid.takeAt(0)
                if item is None:
                    break
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._checks = []
            for i, on in enumerate(segment_on):
                check = QCheckBox(f"Seg {i + 1}", self)
                check.setChecked(on)
                check.toggled.connect(
                    lambda checked, idx=i: self._on_toggled(idx, checked),
                )
                row, col = divmod(i, _GRID_COLUMNS)
                self._grid.addWidget(check, row, col)
                self._checks.append(check)
        else:
            for check, on in zip(self._checks, segment_on, strict=True):
                check.blockSignals(True)
                check.setChecked(on)
                check.blockSignals(False)

    def _on_toggled(self, index: int, on: bool) -> None:
        key = self.current_key()
        if key:
            self._dispatch(ToggleSegment(key=key, index=index, on=on))
