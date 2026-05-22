"""SplashScreen — shown briefly while the App + MainWindow construct.

Honest scope:

* No animation, no progress bar — App construction is fast enough on
  modern hardware that a real progress UI would feel artificial.
* The splash fades out as soon as MainWindow.show() is called.
* Single image (asset) or a styled QFrame fallback if the asset is
  missing — so a fresh install with no bundled assets still works.

This isn't loading-screen theatre.  It's a 200ms visual marker so the
user knows "yes, my click registered, something is happening" instead
of staring at an empty desktop.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSplashScreen,
    QVBoxLayout,
    QWidget,
)

from .assets import Assets

log = logging.getLogger(__name__)


def make_splash() -> QSplashScreen | QFrame:
    """Build a splash widget.

    Returns ``QSplashScreen`` (Qt's built-in) if the splash asset is
    present, otherwise a ``QFrame`` fallback that paints the project
    name + version on a dark background.  Both honor ``.show()`` /
    ``.close()``.
    """
    if Assets.exists(Assets.SPLASH_BG):
        pix = Assets.pixmap(Assets.SPLASH_BG)
        splash = QSplashScreen(pix, Qt.WindowType.WindowStaysOnTopHint)
        splash.setMask(pix.mask())
        return splash
    # Fallback — paint our own splash.
    return _FrameSplash()


class _FrameSplash(QFrame):
    """Minimal splash used when no SPLASH_BG asset is bundled."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(420, 220)
        self.setStyleSheet(
            "background-color: #1a1a1a; color: #e0e0e0;"
            "border: 1px solid #444;",
        )
        title = QLabel("TRCC Linux", self)
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        from ... import __version__
        version = QLabel(f"version {__version__}", self)
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet("color: #888;")

        tagline = QLabel(
            "Thermalright LCD/LED cooler control",
            self,
        )
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setStyleSheet("color: #aaa;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 30, 20, 30)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(version)
        layout.addSpacing(12)
        layout.addWidget(tagline)
        layout.addStretch(1)


def show_splash(parent: QWidget | None = None) -> QWidget:
    """Show a splash, return the handle so callers can ``.close()`` later.

    ``parent`` is unused for now (splashes are always top-level) but
    kept in the signature for the future per-window-positioning case.
    """
    del parent
    splash = make_splash()
    splash.show()
    return splash


def auto_close(splash: QWidget, after_ms: int = 250) -> None:
    """Schedule ``splash.close()`` after *after_ms*.

    Used by the launcher when MainWindow is ready immediately — keeps
    the splash visible just long enough to register visually.
    """
    QTimer.singleShot(after_ms, splash.close)
