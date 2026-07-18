"""Shared async-dispatch helper for the Qt UIs (gui + qtgui).

Some Commands (``PlayVideo``, ``LoadTheme``, ``LoadCloudTheme``, ``SaveTheme``,
``LoadVideo``) split their ``execute()`` into a slow ``_prepare()`` (ffmpeg /
network / file I/O — reads App state but is safe off the GUI thread) and a
fast ``_apply()`` (dict/settings mutation + event publish — must run on the
GUI thread).  ``PrepareThread`` runs the former in a background ``QThread``
and marshals the result back via a Qt signal, the same shape
``ui/qtgui/video_crop.py::_ExportThread`` already uses for the video-export
step — this generalizes it to any zero-arg callable.

Usage::

    cmd = SaveTheme(key=key, name=name)
    thread = PrepareThread(lambda: cmd._prepare(self.app))
    thread.succeeded.connect(lambda prepared: self._finish_save(cmd, prepared))
    thread.failed.connect(self._on_prepare_failed)
    thread.start()
    self._thread = thread   # keep a reference — Qt won't GC a running QThread
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..core.errors import TrccError

log = logging.getLogger(__name__)


class PrepareThread(QThread):
    """Run a zero-arg callable off the GUI thread; emit its result.

    ``fn`` is typically a Command's ``_prepare(app)`` bound via a lambda —
    see module docstring.  Any ``TrccError`` raised is caught and reported
    via ``failed``; anything else is a programming error and is allowed to
    propagate (surfaces as a Qt "exception in slot" log, same as an
    unguarded bug anywhere else — never let a worker crash the whole app).
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        log.debug("PrepareThread.run: starting")
        try:
            result = self._fn()
        except TrccError as e:
            log.warning("PrepareThread.run: %s", e)
            self.failed.emit(str(e))
            return
        log.debug("PrepareThread.run: done")
        self.succeeded.emit(result)
