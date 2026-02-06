"""
Screen Capture Overlay — frozen-screen region selection tool.

Matches Windows FormScreenshot functionality but adapted for Linux:
- Captures full screen (X11 + Wayland compatible)
- Shows frozen screenshot with dimmed overlay
- User draws selection rectangle
- Cropped region emitted as PIL Image

Works on both X11 and Wayland via fallback chain.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from functools import lru_cache

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget


@lru_cache(maxsize=1)
def is_wayland() -> bool:
    """Detect if the current session is running on Wayland.

    Checks XDG_SESSION_TYPE and WAYLAND_DISPLAY environment variables.
    Result is cached since the session type doesn't change at runtime.
    """
    return (os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
            or bool(os.environ.get('WAYLAND_DISPLAY')))


def grab_full_screen() -> QPixmap:
    """Capture the full screen, X11 + Wayland compatible.

    Tries QScreen.grabWindow() first (works on X11).
    Falls back to grim/gnome-screenshot/scrot for Wayland.

    Returns:
        QPixmap of the full screen, or null pixmap on failure.
    """
    # Try Qt native capture first (works on X11, may be blank on Wayland)
    screen = QApplication.primaryScreen()
    if screen:
        pixmap = screen.grabWindow(0)  # type: ignore[arg-type]
        if not pixmap.isNull() and pixmap.width() > 1:
            return pixmap

    # Wayland fallback: try external tools
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        tmp_path = f.name

    try:
        for cmd in [
            ['grim', tmp_path],                          # Wayland (wlroots)
            ['gnome-screenshot', '-f', tmp_path],        # GNOME
            ['scrot', tmp_path],                         # X11 fallback
        ]:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=5)
                if result.returncode == 0 and os.path.getsize(tmp_path) > 0:
                    pixmap = QPixmap(tmp_path)
                    if not pixmap.isNull():
                        return pixmap
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return QPixmap()


def grab_screen_region(x: int, y: int, w: int, h: int) -> QPixmap:
    """Capture a specific screen region. X11 + Wayland compatible.

    Called repeatedly by the screencast timer (~150ms interval), so this
    needs to be reasonably efficient.

    X11: QScreen.grabWindow(0, x, y, w, h) captures the region directly.
    Wayland: grim with -g geometry flag, or full capture + crop fallback.

    Returns:
        QPixmap of the region, or null pixmap on failure.
    """
    # Try Qt native capture with region (works on X11)
    screen = QApplication.primaryScreen()
    if screen:
        pixmap = screen.grabWindow(0, x, y, w, h)  # type: ignore[arg-type]
        if not pixmap.isNull() and pixmap.width() > 1:
            return pixmap

    # Wayland fallback: grim with -g region flag
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        tmp_path = f.name

    try:
        geometry = f"{x},{y} {w}x{h}"
        for cmd in [
            ['grim', '-g', geometry, tmp_path],       # Wayland (wlroots)
            ['scrot', '-a', f'{x},{y},{w},{h}', tmp_path],  # X11 fallback
        ]:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=2)
                if result.returncode == 0 and os.path.getsize(tmp_path) > 0:
                    pixmap = QPixmap(tmp_path)
                    if not pixmap.isNull():
                        return pixmap
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        # Last resort: full screen capture + crop
        full = grab_full_screen()
        if not full.isNull():
            return full.copy(x, y, w, h)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return QPixmap()


class ScreenCaptureOverlay(QWidget):
    """Full-screen overlay for selecting a screen region.

    Shows a frozen screenshot. User draws a selection rectangle.
    The selected region is emitted as a PIL Image.

    Usage:
        overlay = ScreenCaptureOverlay()
        overlay.captured.connect(on_captured)
        overlay.show()
    """

    captured = pyqtSignal(object)  # PIL Image or None

    # Visual constants
    _DIM_COLOR = QColor(0, 0, 0, 120)
    _BORDER_COLOR = QColor(200, 200, 200)
    _BORDER_WIDTH = 2
    _SIZE_FONT = QFont("sans-serif", 11)
    _SIZE_BG = QColor(0, 0, 0, 180)
    _SIZE_TEXT = QColor(255, 255, 255)
    _MIN_SELECTION = 10  # Minimum selection size in pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._screenshot: QPixmap = QPixmap()
        self._selecting = False
        self._start = QPoint()
        self._end = QPoint()

    def show(self):
        """Capture screen then show fullscreen overlay."""
        self._screenshot = grab_full_screen()
        if self._screenshot.isNull():
            self.captured.emit(None)
            return

        # Go fullscreen on the primary screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.setGeometry(geo)

        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def paintEvent(self, event):
        if self._screenshot.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw frozen screenshot
        painter.drawPixmap(0, 0, self._screenshot)

        # Dim the entire screen
        painter.fillRect(self.rect(), self._DIM_COLOR)

        if self._selecting and self._start != self._end:
            sel = self._selection_rect()

            # Undim selected region (draw screenshot region over dim)
            painter.drawPixmap(sel, self._screenshot, sel)

            # Draw selection border
            pen = QPen(self._BORDER_COLOR, self._BORDER_WIDTH)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(sel)

            # Draw size label
            w, h = sel.width(), sel.height()
            label = f"{w} × {h}"
            painter.setFont(self._SIZE_FONT)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(label) + 12
            th = fm.height() + 6

            lx = sel.center().x() - tw // 2
            ly = sel.bottom() + 8
            if ly + th > self.height():
                ly = sel.top() - th - 8

            painter.fillRect(lx, ly, tw, th, self._SIZE_BG)
            painter.setPen(self._SIZE_TEXT)
            painter.drawText(lx + 6, ly + fm.ascent() + 3, label)

        else:
            # Draw hint text when not selecting
            painter.setPen(self._SIZE_TEXT)
            painter.setFont(QFont("sans-serif", 14))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "Click and drag to select a region\nPress ESC to cancel"
            )

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._selecting = True
            self._start = event.pos()
            self._end = event.pos()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._end = event.pos()
            self._selecting = False

            sel = self._selection_rect()
            if sel.width() >= self._MIN_SELECTION and sel.height() >= self._MIN_SELECTION:
                self._confirm(sel)
            else:
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def _selection_rect(self) -> QRect:
        """Normalized rectangle from start/end points."""
        return QRect(self._start, self._end).normalized()

    def _confirm(self, rect: QRect):
        """Crop the selected region and emit as PIL Image."""
        self.hide()
        try:
            cropped = self._screenshot.copy(rect)
            # Convert QPixmap → PIL Image
            from .base import pixmap_to_pil
            pil_img = pixmap_to_pil(cropped)
            self.captured.emit(pil_img)
        except Exception:
            self.captured.emit(None)
        self.deleteLater()

    def _cancel(self):
        """Close without capturing."""
        self.hide()
        self.captured.emit(None)
        self.deleteLater()
