"""FlowArea — hudgui's one way to lay out a panel's tiles or cards.

Items of one width, as many to a row as fit the VISIBLE width, scrolling down
only -- never sideways.  The theme tiles and the Sensors cards both sit in
one, so a panel never grows a bottom scrollbar (his ask, 2026-11-10: the
Sensors cards ran off the right in a single row).  Items in a row share its
height, so a row reads as one even band.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QGridLayout, QScrollArea, QWidget

log = logging.getLogger(__name__)


class FlowArea(QScrollArea):
    """A scrolling area that flows its items into rows of the visible width."""

    def __init__(self, item_width: int, spacing: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("FlowArea: items %d wide, %d apart", item_width, spacing)
        self._item_width, self._spacing = item_width, spacing
        self._items: list[QWidget] = []
        #: How many items sit in a row now.
        self.columns = 1
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget "
                           "{ background: transparent; }")
        #: What the items are made in.
        self.body = QWidget()
        self._grid = QGridLayout(self.body)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(self.body)
        # The VISIBLE width, which can change without the panel resizing (a
        # scrollbar appears; the panel is shown after it was laid out hidden).
        self.viewport().installEventFilter(self)

    def place(self, items: Sequence[QWidget]) -> None:
        """*items*, in order, flowed into rows -- made in :attr:`body`."""
        log.debug("FlowArea.place: %d item(s)", len(items))
        self._items = list(items)
        self._lay_out()

    def fit(self) -> int:
        """How many items fit across the visible width now (at least one)."""
        columns = max(1, (self.viewport().width() + self._spacing)
                      // (self._item_width + self._spacing))
        log.debug("FlowArea.fit: %d", columns)
        return columns

    def _lay_out(self) -> None:
        """Place the items, in order, as many to a row as fit."""
        self.columns = self.fit()
        log.debug("FlowArea._lay_out: %d item(s), %d to a row",
                  len(self._items), self.columns)
        for index, item in enumerate(self._items):
            self._grid.addWidget(item, index // self.columns, index % self.columns)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """The visible width changed: flow again, if a different number fit.
        His run (2026-11-05): laid out while hidden -- one to a row -- and the
        viewport took its real width after the panel's own resize, so on a
        1920-wide bar the themes stayed in a single column."""
        if (watched is self.viewport() and event.type() == QEvent.Type.Resize
                and self.fit() != self.columns):
            log.debug("FlowArea.eventFilter: viewport %d wide -- re-flow",
                      self.viewport().width())
            self._lay_out()
        return super().eventFilter(watched, event)
