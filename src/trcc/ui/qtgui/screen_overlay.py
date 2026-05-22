"""Screen-grab + frozen-screen overlay primitives.

Two pieces shared by the eyedropper (pick a colour) and any future
region-capture tool (image crop "from screen", screencast region select):

* :func:`grab_full_screen` — best-effort full-display capture that
  works on X11 (Qt native) and Wayland (``grim`` / ``gnome-screenshot``
  / ``scrot`` fallback chain).  Returns a :class:`QPixmap`; null on
  total failure so callers can surface a friendly message instead of
  crashing.

* :class:`BaseScreenOverlay` — a frameless, always-on-top, fullscreen
  widget that paints a frozen screenshot of the desktop.  Subclasses
  override ``paintEvent`` + mouse handlers to layer their interaction
  (magnifier, selection rectangle) on top.  ESC is wired to cancel via
  ``_emit_cancel()``.

Why a frozen screenshot instead of overlaying a transparent window on
the live desktop: cursor-following capture is racy (compositor lag,
sub-pixel artifacts) and on Wayland we can't read pixel colour from an
arbitrary window anyway.  Freezing the screen once is honest about
what the user is picking.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QWidget

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def is_wayland() -> bool:
    """``True`` if we're running under a Wayland session."""
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


_FALLBACK_TOOLS: tuple[str, ...] = ("grim", "gnome-screenshot", "scrot")


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _try_external_capture(tmp_path: str) -> QPixmap:
    """Run a fallback screenshot tool, return what it wrote (or null)."""
    cmds = {
        "grim": ["grim", tmp_path],
        "gnome-screenshot": ["gnome-screenshot", "-f", tmp_path],
        "scrot": ["scrot", tmp_path],
    }
    for tool in _FALLBACK_TOOLS:
        if not _has_tool(tool):
            log.debug("screen capture: %s not installed", tool)
            continue
        try:
            result = subprocess.run(
                cmds[tool], capture_output=True, timeout=5, check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("screen capture: %s timed out", tool)
            continue
        if result.returncode != 0 or not Path(tmp_path).exists():
            log.debug("screen capture: %s exited %d", tool, result.returncode)
            continue
        pix = QPixmap(tmp_path)
        if not pix.isNull():
            log.debug("screen capture via %s", tool)
            return pix
    return QPixmap()


def grab_full_screen() -> QPixmap:
    """Capture the full primary screen, X11 + Wayland.

    Tries the Qt native path first (works on X11, sometimes blank on
    Wayland).  Falls back to ``grim`` / ``gnome-screenshot`` / ``scrot``
    in that order.  Returns a null pixmap if every option fails — the
    caller is responsible for surfacing that to the user.
    """
    screen = QApplication.primaryScreen()
    if screen is not None:
        pix = screen.grabWindow(0)  # type: ignore[arg-type]
        if not pix.isNull() and pix.width() > 1:
            return pix

    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        return _try_external_capture(tmp_path)
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


class BaseScreenOverlay(QWidget):
    """Frameless fullscreen widget that paints a frozen screenshot.

    Subclasses override ``paintEvent`` and the mouse handlers to layer
    interaction on top.  ``_emit_cancel()`` must be implemented to emit
    the subclass's cancel signal — base ESC handling calls it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._screenshot: QPixmap = QPixmap()

    def show(self) -> None:
        """Capture the screen, then show fullscreen.

        If capture fails (null pixmap), we immediately ``_emit_cancel``
        and do not present an empty window — better than showing a
        black screen that swallows clicks.
        """
        self._screenshot = grab_full_screen()
        if self._screenshot.isNull():
            log.warning("screen overlay: capture failed, cancelling")
            self._emit_cancel()
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(event)

    def _cancel(self) -> None:
        self.hide()
        self._emit_cancel()
        self.deleteLater()

    def _emit_cancel(self) -> None:
        raise NotImplementedError(
            "BaseScreenOverlay subclass must emit its own cancel signal",
        )
