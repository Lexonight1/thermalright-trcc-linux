"""ActivitySidebar — left-rail navigation that swaps the main content pane.

Architectural role: replaces legacy ``gui/uc_activity_sidebar.py`` with
a Qt-native button rail.  Emits ``selected`` with a panel key when the
user clicks an entry; ``MainWindow`` listens and calls
``content.setCurrentWidget(...)`` on a ``QStackedWidget``.

Buttons are data-driven (``_ENTRIES``) so adding a panel = adding a row
to the tuple.  No hardcoded layouts per panel.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..base import BasePanel


@dataclass(frozen=True, slots=True)
class _Entry:
    key: str         # MainWindow uses this as the stacked-widget id
    label: str       # display text


# Add panels here as they land — each one corresponds to a setCurrentWidget()
# target on MainWindow.  Order = vertical order in the sidebar.
_ENTRIES: tuple[_Entry, ...] = (
    _Entry("devices",    "Devices"),
    _Entry("display",    "Display"),
    _Entry("preview",    "Preview"),
    _Entry("themes",     "Themes"),
    _Entry("cloud",      "Cloud themes"),
    _Entry("masks",      "Masks"),
    _Entry("overlay",    "Overlay editor"),
    _Entry("screencast", "Screencast"),
    _Entry("config",     "Configuration"),
    _Entry("led",        "LED"),
    _Entry("status",     "Status"),
    _Entry("system",     "System"),
    _Entry("about",      "About"),
)


class ActivitySidebar(BasePanel):
    """Left-rail navigation — emits the panel key on click."""

    selected = Signal(str)

    def _setup_ui(self) -> None:
        self.setFixedWidth(200)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(4)

        title = QLabel("TRCC", self)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(16)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._buttons: dict[str, QPushButton] = {}
        for entry in _ENTRIES:
            button = QPushButton(entry.label, self)
            button.setCheckable(True)
            button.setMinimumHeight(36)
            button.clicked.connect(
                lambda _checked=False, key=entry.key: self._on_clicked(key),
            )
            self._buttons[entry.key] = button
            self._group.addButton(button)
            layout.addWidget(button)

        layout.addStretch(1)

        # Default selection — first entry checked.
        first = _ENTRIES[0]
        self._buttons[first.key].setChecked(True)

    def select(self, key: str) -> None:
        """Programmatically check the button for *key* (no signal emitted)."""
        button = self._buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def _on_clicked(self, key: str) -> None:
        self.selected.emit(key)

    def apply_language(self, lang: str) -> None:
        # Translation keys come back when tr() wiring lands; the sidebar
        # is one of the smallest places to start localizing.
        del lang
