"""hudgui's About: which TRCC this is and whether a newer one is out, the
diagnostic report, then who TRCC is inspired by and every work it uses --
``core.credits``, the About every UI shows.

The update line is the App's answer (``UpdateStatus``, then each
``UpdateChecked``): the App checks at start and hourly, and no window asks for
itself.  "How to upgrade" says the command for this install
(``RunUpgrade`` runs nothing).
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...core.commands import GenerateDebugReport, RunUpgrade, UpdateStatus
from ...core.credits import GROUPS, INSPIRATIONS, WE_LOVE, WHAT_TRCC_DOES
from ..presentation.credit_line import credit_html

if TYPE_CHECKING:
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)

#: The people's pictures -- package assets, as the gui's About has them.
PICTURES = Path(__file__).resolve().parents[2] / "assets" / "inspired"
_PICTURE = 72
#: The project's page.
PROJECT_PAGE = "https://github.com/Lexonight1/thermalright-trcc-linux"
_ACCENT = "#7fd4ff"
_STYLE = ("QLabel { color: #dfe5ea; background: transparent; font-size: 10pt; }"
          "QLabel#heading { color: #a9e6ff; font-size: 13pt; font-weight: bold; }"
          "QLabel#version { color: white; font-size: 16pt; font-weight: bold; }"
          "QLabel#love { color: white; font-size: 18pt; font-weight: bold; }"
          "QWidget#card { background: rgba(255, 255, 255, 14); "
          "border: 1px solid rgba(255, 255, 255, 30); border-radius: 8px; }"
          "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")


def _text(words: str, name: str = "", rich: bool = False) -> QLabel:
    """A wrapping label, named for the style sheet."""
    log.debug("_text: %.40s", words)
    label = QLabel(words)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    label.setOpenExternalLinks(rich)
    if name:
        label.setObjectName(name)
    return label


class About(QScrollArea):
    """The version and its actions, then the credits, scrolling."""

    def __init__(self, app: CommandBus, bus: BusBridge,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("About: built")
        self._app = app
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setStyleSheet(_STYLE)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setSpacing(12)
        column.addLayout(self._top())
        column.addLayout(self._people())
        column.addWidget(_text(WHAT_TRCC_DOES))
        love = _text(WE_LOVE, "love")
        love.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(love)
        for title, credits in GROUPS:
            column.addWidget(_text(title, "heading"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(30)
            for index, credit in enumerate(credits):
                grid.addWidget(_text(credit_html(credit, _ACCENT), rich=True),
                               index // 2, index % 2)
            column.addLayout(grid)
        column.addStretch(1)
        self.setWidget(body)
        bus.update_checked.connect(self._show_update,
                                   type=Qt.ConnectionType.QueuedConnection)
        self._show_update(self._app.dispatch(UpdateStatus()))

    def _top(self) -> QHBoxLayout:
        """The version, the update line, and the three actions."""
        log.debug("About._top")
        row = QHBoxLayout()
        #: Which TRCC this window is.
        self.version = _text(f"TRCC Linux {__version__}", "version")
        #: The App's last word on updates.
        self.update_line = _text("")
        row.addWidget(self.version)
        row.addWidget(self.update_line, 1)
        #: Shown only when a newer TRCC is out.
        self.upgrade_button = QPushButton("How to upgrade")
        self.upgrade_button.clicked.connect(self._on_upgrade)
        self.upgrade_button.hide()
        report = QPushButton("Save diagnostic report…")
        report.clicked.connect(self._on_report)
        page = QPushButton("Project page")
        page.clicked.connect(self._on_page)
        for button in (self.upgrade_button, report, page):
            row.addWidget(button)
        return row

    def _people(self) -> QGridLayout:
        """Who TRCC is inspired by: a picture and a name, and what each did."""
        log.debug("About._people: %d", len(INSPIRATIONS))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        for row, person in enumerate(INSPIRATIONS):
            picture = QLabel()
            picture.setPixmap(QPixmap(str(PICTURES / person.picture)).scaled(
                _PICTURE, _PICTURE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            grid.addWidget(picture, row, 0)
            words = _text(f"<b style='color:white'>{html.escape(person.name)}</b>"
                          f"<br>{html.escape(person.did)}", rich=True)
            grid.addWidget(words, row, 1)
        grid.setColumnStretch(1, 1)
        return grid

    # ── The App's answers ──────────────────────────────────────────────

    def _show_update(self, answer: Any) -> None:
        """The App's last word on updates (an ``UpdateStatus`` result or an
        ``UpdateChecked`` event -- the same fields)."""
        newer = bool(answer.ok and answer.update_available)
        log.info("About._show_update: ok=%s newer=%s %s", answer.ok, newer,
                 answer.latest_version)
        self.update_line.setText(f"Version {answer.latest_version} is out" if newer
                                 else answer.message)
        self.upgrade_button.setVisible(newer)

    # ── The actions ────────────────────────────────────────────────────

    def _on_upgrade(self) -> None:
        """The command that upgrades this install -- shown, never run."""
        result = self._app.dispatch(RunUpgrade())
        log.info("About._on_upgrade: %s", result.message)
        self.tell("Upgrade TRCC", result.message)

    def _on_report(self) -> None:
        """The diagnostic report, to a file the user picks."""
        path = self.choose_report_file()
        log.info("About._on_report: %r", path)
        if not path:
            return
        result = self._app.dispatch(GenerateDebugReport(output_path=Path(path),
                                                        log_tail_lines=1000))
        self.tell("Diagnostic report", f"Saved to {result.output_path}" if result.ok
                  else f"The report could not be saved: {result.message}")

    def _on_page(self) -> None:
        log.info("About._on_page: %s", PROJECT_PAGE)
        QDesktopServices.openUrl(QUrl(PROJECT_PAGE))

    # ── What a person sees and picks -- methods, so a test answers ─────

    def choose_report_file(self) -> str:
        """Where to save the report, or ""."""
        log.debug("About.choose_report_file")
        path, _ = QFileDialog.getSaveFileName(self, "Save diagnostic report",
                                              "trcc-debug-report.txt",
                                              "Text files (*.txt);;All files (*)")
        return path

    def tell(self, title: str, message: str) -> None:
        """Say *message*; the text can be selected and copied."""
        log.info("About.tell: %s: %s", title, message)
        box = QMessageBox(QMessageBox.Icon.Information, title, message, parent=self)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()
