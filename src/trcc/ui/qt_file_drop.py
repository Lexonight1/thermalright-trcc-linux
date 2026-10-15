"""A file dropped on a window's preview, for either window (#265).

One local file whose kind the media table knows -- a video or GIF plays, an
image becomes the background -- reaches the window as ``dropped(path, kind)``;
anything else as ``refused(reason)`` the moment it is dragged over.  The window does with it exactly what
its own file picker does, so a drop is never a second, different way in.

``MEDIA.kind_of`` decides what a file is: the one list of extensions the
render path also reads.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtGui import QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QWidget

from ..core.logs import per_frame
from ..core.models import MEDIA, MediaKind

log = logging.getLogger(__name__)
#: Every event a widget gets passes the filter -- never built at default.
frame_log = per_frame(__name__)

#: What a refused drop says -- one video or image, and nothing else.
REFUSED = "Drop one video or image"


def dropped_file(urls: list) -> tuple[Path, MediaKind] | str:
    """The one usable file in a drop and its kind, or why there is none."""
    files = [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]
    if len(urls) != 1 or len(files) != 1:
        log.info("dropped_file: %d url(s), %d local -- refused", len(urls),
                 len(files))
        return REFUSED
    kind = MEDIA.kind_of(files[0])
    log.info("dropped_file: %s -> %s", files[0], kind)
    return (files[0], kind) if kind is not None else REFUSED


class FileDropTarget(QObject):
    """Makes *widget* take a dropped file; installs itself as its filter."""

    dropped = Signal(Path, object)      # (path, MediaKind)
    refused = Signal(str)

    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        log.info("FileDropTarget: on %s", type(widget).__name__)
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Accept a drag that carries one usable file; act on its drop."""
        kind = event.type()
        frame_log.debug("FileDropTarget.eventFilter: %s", kind)
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            # A QDragEnterEvent IS a QDragMoveEvent: one test covers both.
            if not isinstance(event, QDragMoveEvent):
                return False
            found = dropped_file(event.mimeData().urls())
            if not isinstance(found, str):
                event.acceptProposedAction()
                return True
            # Qt delivers no drop to a widget that refused the drag, so the
            # user hears why as the drag arrives, beside the "no" cursor.
            if kind == QEvent.Type.DragEnter:
                self.refused.emit(found)
            return False
        if kind == QEvent.Type.Drop and isinstance(event, QDropEvent):
            found = dropped_file(event.mimeData().urls())
            if isinstance(found, str):
                self.refused.emit(found)
            else:
                event.acceptProposedAction()
                self.dropped.emit(*found)
            return True
        return super().eventFilter(watched, event)
