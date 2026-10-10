"""The RAM-lighting row both windows show -- the view, not the Commands.

A status line and one button.  Enabling first shows the warning, then asks
for the switch on a background thread: the answer comes when the password is
typed or the prompt is closed, and the window stays usable meanwhile.

Template Method: each skin's subclass names the two Commands
(``_status`` / ``_switch``), so the Commands are each UI's own -- which is how
a UI turns input into Commands, and how the UI-parity gate sees that both
windows reach them.
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from ..core.models import RamAccessState
from ..core.results import RamLightingResult
from .presentation.ram_access import CONFIRM_TEXT, CONFIRM_TITLE, ram_access_view

log = logging.getLogger(__name__)


class RamAccessRow(QWidget):
    """Status + one button; a skin's subclass supplies the two Commands."""

    _answered = Signal(object)          # RamLightingResult, from the worker

    def __init__(self, parent: QWidget | None = None, *,
                 text_style: str = "", button_style: str = "") -> None:
        super().__init__(parent)
        log.debug("RamAccessRow.__init__: %s", type(self).__name__)
        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._button = QPushButton("", self)
        if text_style:
            self._status_label.setStyleSheet(text_style)
        if button_style:
            self._button.setStyleSheet(button_style)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._status_label, 1)
        layout.addWidget(self._button)
        self._enables: bool | None = None
        self._button.clicked.connect(self._on_clicked)
        self._answered.connect(self._show)

    # ── What a skin supplies ─────────────────────────────────────────

    def _status(self) -> RamLightingResult:
        """The ``RamLighting`` Query's answer."""
        log.error("RamAccessRow._status: %s names no Command", type(self).__name__)
        raise NotImplementedError

    def _switch(self, enabled: bool) -> RamLightingResult:
        """The ``SetRamLighting`` Command's answer."""
        log.error("RamAccessRow._switch: %s names no Command (%s)",
                  type(self).__name__, enabled)
        raise NotImplementedError

    # ── The row ──────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Show where things stand now -- a file read in the App, no bus."""
        log.debug("RamAccessRow.refresh")
        self._show(self._status())

    def _show(self, result: RamLightingResult, *, busy: bool = False) -> None:
        view = ram_access_view(result, busy=busy)
        log.info("RamAccessRow: %s / %r", view.status, view.button)
        self._status_label.setText(view.status)
        self._button.setText(view.button)
        self._button.setVisible(bool(view.button))
        self._enables = view.enables

    def _on_clicked(self) -> None:
        enable = self._enables
        log.info("RamAccessRow._on_clicked: enable=%s", enable)
        if enable is None:
            return
        if enable and QMessageBox.question(
                self, CONFIRM_TITLE, CONFIRM_TEXT) != QMessageBox.StandardButton.Yes:
            log.info("RamAccessRow: the warning was declined")
            return
        self._show(RamLightingResult(), busy=True)
        threading.Thread(target=self._switch_in_background, args=(enable,),
                         daemon=True, name="trcc-ram-access").start()

    def _switch_in_background(self, enable: bool) -> None:
        """Wait for the person's answer off the UI thread, then hand it back."""
        log.info("RamAccessRow: switching %s", "on" if enable else "off")
        try:
            result = self._switch(enable)
        except Exception as e:          # the App went away mid-prompt
            log.warning("RamAccessRow: the switch failed -- %s: %s",
                        type(e).__name__, e)
            result = RamLightingResult(ok=False, state=RamAccessState.OFF,
                                       message=f"Could not reach TRCC: {e}")
        try:
            self._answered.emit(result)
        except RuntimeError:            # the window closed while waiting
            log.info("RamAccessRow: answered after the window closed")
