"""
Eyedropper Color Picker — frozen-screen pixel color selection.

Matches Windows FormGetColor functionality:
- Captures full screen (frozen screenshot)
- Shows magnified 12×12px preview near cursor
- Left-click to accept color, ESC/right-click to cancel
- Emits picked (R, G, B) color values

Uses the same grab_full_screen() utility as ScreenCaptureOverlay.
"""

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QFont, QImage, QCursor,
)

from .screen_capture import grab_full_screen


class EyedropperOverlay(QWidget):
    """Full-screen overlay for picking a pixel color.

    Shows a frozen screenshot with a magnified preview near the cursor.
    The user moves the mouse to find the desired color, then clicks to accept.

    Signals:
        color_picked(int, int, int): Emitted with R, G, B on left-click.
        cancelled(): Emitted on ESC or right-click.
    """

    color_picked = pyqtSignal(int, int, int)
    cancelled = pyqtSignal()

    # Windows FormGetColor uses 12×12 capture area
    MAGNIFY_SIZE = 12
    MAGNIFY_SCALE = 10
    PREVIEW_OFFSET = 25

    # Visual constants
    _BORDER_COLOR = QColor(180, 180, 180)
    _CROSSHAIR_COLOR = QColor(255, 255, 255, 200)
    _BG_COLOR = QColor(30, 30, 30, 220)
    _TEXT_COLOR = QColor(255, 255, 255)
    _FONT = QFont("monospace", 10)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._screenshot: QPixmap = QPixmap()
        self._current_color = QColor(0, 0, 0)
        self._cursor_pos = QPoint()

    def show(self):
        """Capture screen then show fullscreen overlay."""
        self._screenshot = grab_full_screen()
        if self._screenshot.isNull():
            self.cancelled.emit()
            return

        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())

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

        # Draw magnifier preview near cursor
        if not self._cursor_pos.isNull():
            self._draw_magnifier(painter)

        painter.end()

    def mouseMoveEvent(self, event):
        self._cursor_pos = event.pos()
        self._update_color_at_cursor()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._accept()
        else:
            self._cancel()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def _update_color_at_cursor(self):
        """Extract the pixel color at the cursor position from the screenshot."""
        if self._screenshot.isNull():
            return
        x = max(0, min(self._cursor_pos.x(), self._screenshot.width() - 1))
        y = max(0, min(self._cursor_pos.y(), self._screenshot.height() - 1))
        img = self._screenshot.toImage()
        self._current_color = QColor(img.pixel(x, y))

    def _draw_magnifier(self, painter: QPainter):
        """Draw magnified preview box near the cursor."""
        cx, cy = self._cursor_pos.x(), self._cursor_pos.y()
        half = self.MAGNIFY_SIZE // 2
        mag_w = self.MAGNIFY_SIZE * self.MAGNIFY_SCALE
        mag_h = self.MAGNIFY_SIZE * self.MAGNIFY_SCALE

        # Position: offset from cursor, flip if near edges
        mx = cx + self.PREVIEW_OFFSET
        my = cy + self.PREVIEW_OFFSET
        if mx + mag_w + 60 > self.width():
            mx = cx - self.PREVIEW_OFFSET - mag_w
        if my + mag_h + 40 > self.height():
            my = cy - self.PREVIEW_OFFSET - mag_h - 40

        # Extract region from screenshot
        src_x = max(0, cx - half)
        src_y = max(0, cy - half)
        src_w = min(self.MAGNIFY_SIZE, self._screenshot.width() - src_x)
        src_h = min(self.MAGNIFY_SIZE, self._screenshot.height() - src_y)

        region = self._screenshot.copy(src_x, src_y, src_w, src_h)
        scaled = region.scaled(
            mag_w, mag_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation  # Pixelated look
        )

        # Draw magnifier background
        total_h = mag_h + 36
        painter.fillRect(mx - 2, my - 2, mag_w + 4, total_h + 4, self._BG_COLOR)

        # Draw magnified pixels
        painter.drawPixmap(mx, my, scaled)

        # Draw border
        pen = QPen(self._BORDER_COLOR, 1)
        painter.setPen(pen)
        painter.drawRect(mx - 1, my - 1, mag_w + 1, mag_h + 1)

        # Draw crosshair in center of magnifier
        center_x = mx + mag_w // 2
        center_y = my + mag_h // 2
        cell = self.MAGNIFY_SCALE
        pen = QPen(self._CROSSHAIR_COLOR, 1)
        painter.setPen(pen)
        painter.drawRect(
            center_x - cell // 2, center_y - cell // 2,
            cell, cell
        )

        # Draw color info below magnifier
        r, g, b = (self._current_color.red(),
                    self._current_color.green(),
                    self._current_color.blue())
        hex_str = f"#{r:02X}{g:02X}{b:02X}"
        rgb_str = f"RGB({r}, {g}, {b})"

        painter.setFont(self._FONT)
        painter.setPen(self._TEXT_COLOR)

        # Color swatch + hex
        swatch_y = my + mag_h + 6
        painter.fillRect(mx, swatch_y, 20, 20,
                         QColor(r, g, b))
        painter.setPen(QPen(self._BORDER_COLOR, 1))
        painter.drawRect(mx, swatch_y, 20, 20)

        painter.setPen(self._TEXT_COLOR)
        painter.drawText(mx + 26, swatch_y + 14, f"{hex_str}  {rgb_str}")

    def _accept(self):
        """Accept the current color and close."""
        self.hide()
        self.color_picked.emit(
            self._current_color.red(),
            self._current_color.green(),
            self._current_color.blue(),
        )
        self.deleteLater()

    def _cancel(self):
        """Cancel and close."""
        self.hide()
        self.cancelled.emit()
        self.deleteLater()
