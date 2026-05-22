"""Bootstrap splash screen shown during initialization.

Displayed before the main window while TrccApp.bootstrap() runs in a
background QThread. Gives the user immediate feedback that the application
is starting — especially visible on first install when theme data is being
downloaded and extracted.

Usage (gui/__init__.py)::

    splash = TrccSplash()
    splash.show()

    worker = BootstrapWorker(app, QtRenderer)
    worker.progress.connect(splash.update_message)
    worker.start()

    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    loop.exec()

    splash.close()
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEventLoop, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

from trcc.__version__ import __version__

log = logging.getLogger(__name__)

_SPLASH_W = 400
_SPLASH_H = 170

_STYLE = """
QWidget {
    background-color: #1e1e2e;
}
QLabel#title {
    color: #cdd6f4;
    font-size: 18px;
    font-weight: bold;
}
QLabel#version {
    color: #585b70;
    font-size: 10px;
}
QLabel#status {
    color: #a6adc8;
    font-size: 10px;
}
QProgressBar {
    background-color: #313244;
    border: none;
    border-radius: 3px;
    max-height: 5px;
}
QProgressBar::chunk {
    background-color: #89b4fa;
    border-radius: 3px;
}
"""


class TrccSplash(QWidget):
    """Small frameless loading window shown while bootstrap runs.

    Thread-safe: update_message() is a Slot so it can be connected to a
    Signal emitted from a QThread — Qt routes it through the event loop.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TRCC Linux")
        self.setFixedSize(_SPLASH_W, _SPLASH_H)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 26)
        layout.setSpacing(4)

        title = QLabel("TRCC Linux")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        ver = QLabel(f"v{__version__}")
        ver.setObjectName("version")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status = QLabel("Starting…")
        self._status.setObjectName("status")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)   # indeterminate / marquee
        self._bar.setTextVisible(False)

        layout.addWidget(title)
        layout.addWidget(ver)
        layout.addSpacing(10)
        layout.addWidget(self._status)
        layout.addSpacing(6)
        layout.addWidget(self._bar)

        self._center()

    def _center(self) -> None:
        if (screen := QApplication.primaryScreen()) is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - _SPLASH_W // 2,
                geo.center().y() - _SPLASH_H // 2,
            )

    @Slot(str)
    def update_message(self, message: str) -> None:
        """Update status label. Connected to BootstrapWorker.progress signal."""
        self._status.setText(message)


class BootstrapWorker(QThread):
    """Runs ``Trcc.discover()`` in a background QThread.

    Subscribes to ``Topic.BOOTSTRAP_PROGRESS`` on the Trcc's EventBus so
    download/extraction progress strings are forwarded as the
    ``progress(str)`` Qt signal — safely crossing from the worker thread
    to the main thread. ``QThread.finished`` fires automatically when
    ``run()`` returns.
    """

    progress: Signal = Signal(str)
    failed: Signal = Signal(str)

    def __init__(self, trcc_handle: Any) -> None:
        super().__init__()
        self._trcc = trcc_handle

    def run(self) -> None:
        from trcc.core.events import Topic

        sub_id = self._trcc.events.subscribe(
            Topic.BOOTSTRAP_PROGRESS,
            lambda msg: self.progress.emit(str(msg)),
        )
        try:
            self._trcc.discover()
        except Exception as exc:
            log.exception("Bootstrap error")
            self.failed.emit(str(exc))
        finally:
            self._trcc.events.unsubscribe(sub_id)


def run_bootstrap_with_splash(trcc_handle: Any) -> bool:
    """Show splash, run discover() in background, close splash when done.

    Returns True on success, False if bootstrap raised an exception.
    Caller must have a live QApplication before calling this.
    """
    splash = TrccSplash()
    splash.show()
    QApplication.processEvents()

    worker = BootstrapWorker(trcc_handle)

    error: list[str] = []
    worker.failed.connect(lambda msg: error.append(msg))  # type: ignore[arg-type]
    worker.progress.connect(splash.update_message)

    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    worker.start()
    loop.exec()

    splash.close()
    splash.deleteLater()

    if error:
        log.error("Bootstrap failed: %s", error[0])
        return False
    return True
