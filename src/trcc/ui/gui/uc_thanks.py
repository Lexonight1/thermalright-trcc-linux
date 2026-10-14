"""The About page: who TRCC is inspired by -- a small picture and what each
did -- what TRCC does, our love to the open-source community, then the
licensing: every work TRCC uses, who made it and its licence.  All of it is
``core.credits``, the About every UI shows.  Opened by About, beside Control Center
on the Control Center page's top bar; it scrolls, so everything fits."""
from __future__ import annotations

import html
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.credits import GROUPS, INSPIRATIONS, WE_LOVE, WHAT_TRCC_DOES, Credit
from .constants import Sizes
from .uc_rgb import GOLD, paint_page

log = logging.getLogger(__name__)

#: The people's pictures -- package assets, like the renderer's fonts.
PICTURES = Path(__file__).resolve().parents[2] / "assets" / "inspired"
_PICTURE = 96
#: Inside the framed body, in this page's coordinates.
_BODY = (40, 110, 1190, 660)
_SCROLLBAR = ("QScrollArea { background: transparent; border: none; }"
              "QScrollBar:vertical { background: transparent; width: 8px; }"
              "QScrollBar::handle:vertical { background: #555; border-radius: 4px;"
              " min-height: 20px; }"
              "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }")


def _label(text: str, style: str, rich: bool = False) -> QLabel:
    log.debug("_label: %.40s", text)
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    label.setStyleSheet(f"background: transparent; {style}")
    if rich:
        label.setOpenExternalLinks(True)
    return label


def _cell() -> QFrame:
    """One table cell: a thin border, the page's colour inside."""
    log.debug("_cell")
    cell = QFrame()
    cell.setObjectName("cell")
    cell.setStyleSheet("QFrame#cell { background: #2a2a30; border: 1px solid #434343; }")
    return cell


def _credit(credit: Credit) -> QLabel:
    """Name, who made it, its licence (a link to its text), what it gave."""
    log.debug("_credit: %s (%s)", credit.name, credit.licence_url or "no link")
    licence = html.escape(credit.licence)
    if credit.licence_url and licence:
        licence = (f"<a href='{html.escape(credit.licence_url, quote=True)}'"
                   f" style='color:{GOLD}'>{licence}</a>")
    who = ", ".join(part for part in (html.escape(credit.author), licence) if part)
    used = (f"<br><span style='color:#b3b3b3'>{html.escape(credit.used_for)}</span>"
            if credit.used_for else "")
    return _label(f"<b style='color:white; font-size:11pt'>{html.escape(credit.name)}</b>"
                  f"<span style='color:{GOLD}'>&nbsp; {who}</span>{used}",
                  "font-size: 9pt;", rich=True)


class UCThanks(QWidget):
    """About: the people, what TRCC does, our love, the licensing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("UCThanks: %d inspiration(s), %d group(s)", len(INSPIRATIONS), len(GROUPS))
        self.setFixedSize(Sizes.FORM_W, Sizes.FORM_H)
        self._title = "About"
        scroll = QScrollArea(self)
        scroll.setGeometry(*_BODY)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLLBAR)
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        column = QVBoxLayout(body)
        column.setSpacing(14)

        # The people, top to bottom, as a table of two cells: the picture with
        # the name under it, centred to its width -- and their three sentences.
        people = QGridLayout()
        people.setHorizontalSpacing(0)
        people.setVerticalSpacing(0)
        self.names: list[QLabel] = []
        self.people: list[QLabel] = []
        for row, person in enumerate(INSPIRATIONS):
            picture = QLabel()
            picture.setPixmap(QPixmap(str(PICTURES / person.picture)).scaled(
                _PICTURE, _PICTURE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            picture.setFixedSize(_PICTURE, _PICTURE)
            picture.setStyleSheet("background: transparent;")
            name = _label(person.name, "color: white; font-size: 10pt; font-weight: bold;")
            name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            name.setFixedWidth(_PICTURE)
            left = _cell()
            face = QVBoxLayout(left)
            face.setContentsMargins(12, 10, 12, 10)
            face.setSpacing(4)
            face.addWidget(picture, 0, Qt.AlignmentFlag.AlignHCenter)
            face.addWidget(name, 0, Qt.AlignmentFlag.AlignHCenter)
            right = _cell()
            beside = QVBoxLayout(right)
            beside.setContentsMargins(18, 10, 18, 10)
            words = _label(person.did, "color: #d0d0d0; font-size: 11pt;")
            words.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            beside.addWidget(words)
            people.addWidget(left, row, 0)
            people.addWidget(right, row, 1)
            self.names.append(name)
            self.people.append(words)
        people.setColumnStretch(1, 1)
        column.addLayout(people)

        self.what = _label(WHAT_TRCC_DOES, "color: white; font-size: 14pt;")
        column.addWidget(self.what)
        self.love = _label(WE_LOVE, "color: white; font-size: 22pt; font-weight: bold;")
        self.love.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.love)

        self.entries: list[QLabel] = []
        for title, credits in GROUPS:
            column.addWidget(_label(title, f"color: {GOLD}; font-size: 15pt;"
                                           " font-weight: bold;"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(30)
            grid.setVerticalSpacing(8)
            for n, credit in enumerate(credits):
                entry = _credit(credit)
                grid.addWidget(entry, n // 2, n % 2)
                self.entries.append(entry)
            column.addLayout(grid)
        column.addStretch(1)
        scroll.setWidget(body)

    def set_title(self, title: str) -> None:
        """The title bar's text -- retranslated with the window's language."""
        log.info("UCThanks.set_title: %r", title)
        self._title = title
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        log.debug("UCThanks.paintEvent: %s", self._title)
        paint_page(self, self._title)
