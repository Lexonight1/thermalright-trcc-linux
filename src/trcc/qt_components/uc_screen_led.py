#!/usr/bin/env python3
"""
LED segment visualization widget (UCScreenLED equivalent).

Displays the LED device preview with colored circles at segment positions,
matching Windows UCScreenLED.cs. Background shows the device image
(e.g., DAX120_DIGITAL.png), colored circles represent each LED segment.
"""

from typing import List, Optional, Tuple

try:
    from PyQt6.QtCore import QRectF, Qt, pyqtSignal
    from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QWidget
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False


# Segment layout for Style 1 (AX120_DIGITAL, 30 LEDs, 10 segments)
# Approximate positions on the 536x536 device preview image.
# Each segment maps to 3 physical LEDs (30 LEDs / 10 segments).
STYLE1_SEGMENTS = [
    {"name": "Logo1", "x": 200, "y": 100, "r": 20},
    {"name": "Logo2", "x": 260, "y": 100, "r": 20},
    {"name": "Cpu1",  "x": 140, "y": 200, "r": 18},
    {"name": "Cpu2",  "x": 180, "y": 200, "r": 18},
    {"name": "Gpu1",  "x": 300, "y": 200, "r": 18},
    {"name": "Gpu2",  "x": 340, "y": 200, "r": 18},
    {"name": "SSD",   "x": 140, "y": 300, "r": 15},
    {"name": "HSD",   "x": 200, "y": 300, "r": 15},
    {"name": "BFB",   "x": 300, "y": 300, "r": 15},
    {"name": "Ring",  "x": 240, "y": 400, "r": 25},
]

# Segment layouts for other styles (positions are approximate)
STYLE_SEGMENTS = {
    1: STYLE1_SEGMENTS,
    # Other styles use evenly distributed circles as placeholder
}


def _generate_circle_layout(count: int, width: int = 536, height: int = 536):
    """Generate evenly distributed circle positions for styles without layout data."""
    segments = []
    cols = max(1, int(count ** 0.5))
    rows = (count + cols - 1) // cols
    margin = 40
    cell_w = (width - 2 * margin) / max(cols, 1)
    cell_h = (height - 2 * margin) / max(rows, 1)
    radius = min(cell_w, cell_h) / 3

    for i in range(count):
        row = i // cols
        col = i % cols
        x = int(margin + col * cell_w + cell_w / 2)
        y = int(margin + row * cell_h + cell_h / 2)
        segments.append({"name": f"LED{i}", "x": x, "y": y, "r": int(radius)})

    return segments


def get_segment_layout(style_id: int, segment_count: int):
    """Get segment positions for a device style."""
    if style_id in STYLE_SEGMENTS:
        return STYLE_SEGMENTS[style_id]
    return _generate_circle_layout(segment_count)


if PYQT6_AVAILABLE:

    class UCScreenLED(QWidget):
        """LED device preview with colored segment circles.

        Matches Windows UCScreenLED.cs functionality:
        - Background: device-specific image (set via QPalette)
        - Colored circles at LED segment positions
        - Click to toggle individual segments
        """

        segment_clicked = pyqtSignal(int)  # segment index

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setFixedSize(536, 536)

            self._style_id = 1
            self._segment_count = 10
            self._segments = STYLE1_SEGMENTS
            self._colors: List[Tuple[int, int, int]] = [(0, 0, 0)] * 10
            self._is_on: List[bool] = [True] * 10
            self._background: Optional[QPixmap] = None

        def set_style(self, style_id: int, segment_count: int) -> None:
            """Configure for a specific LED device style."""
            self._style_id = style_id
            self._segment_count = segment_count
            self._segments = get_segment_layout(style_id, segment_count)
            self._colors = [(0, 0, 0)] * segment_count
            self._is_on = [True] * segment_count
            self.update()

        def set_background(self, pixmap: Optional[QPixmap]) -> None:
            """Set device preview background image."""
            self._background = pixmap
            self.update()

        def set_colors(self, colors: List[Tuple[int, int, int]]) -> None:
            """Update LED segment colors from controller tick."""
            self._colors = colors[:self._segment_count]
            # Pad if needed
            while len(self._colors) < self._segment_count:
                self._colors.append((0, 0, 0))
            self.update()

        def set_segment_on(self, index: int, on: bool) -> None:
            """Toggle an individual segment."""
            if 0 <= index < len(self._is_on):
                self._is_on[index] = on
                self.update()

        def paintEvent(self, event):
            """Paint background image and colored LED circles."""
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Background
            if self._background:
                painter.drawPixmap(0, 0, self._background.scaled(
                    self.width(), self.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                ))
            else:
                painter.fillRect(self.rect(), QColor(30, 30, 30))

            # Draw LED segments as colored circles
            for i, seg in enumerate(self._segments):
                if i >= len(self._colors):
                    break

                r, g, b = self._colors[i]
                is_on = self._is_on[i] if i < len(self._is_on) else True

                if is_on and (r > 0 or g > 0 or b > 0):
                    # Active LED: filled circle with glow
                    color = QColor(r, g, b)
                    glow = QColor(r, g, b, 80)

                    # Glow
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(glow))
                    painter.drawEllipse(
                        QRectF(
                            seg["x"] - seg["r"] * 1.5,
                            seg["y"] - seg["r"] * 1.5,
                            seg["r"] * 3,
                            seg["r"] * 3,
                        )
                    )

                    # Core
                    painter.setBrush(QBrush(color))
                    painter.drawEllipse(
                        QRectF(
                            seg["x"] - seg["r"],
                            seg["y"] - seg["r"],
                            seg["r"] * 2,
                            seg["r"] * 2,
                        )
                    )
                else:
                    # Off LED: dim circle outline
                    painter.setPen(QPen(QColor(60, 60, 60), 1))
                    painter.setBrush(QBrush(QColor(20, 20, 20)))
                    painter.drawEllipse(
                        QRectF(
                            seg["x"] - seg["r"],
                            seg["y"] - seg["r"],
                            seg["r"] * 2,
                            seg["r"] * 2,
                        )
                    )

            painter.end()

        def set_timer(self, month: int, day: int, hour: int, minute: int,
                      day_of_week: int) -> None:
            """Set LC2 clock display data for preview overlay.

            From FormLED.cs SetMyTimer(): paints date/time text on the
            LC2 device preview image.

            Args:
                month: 1-12
                day: 1-31
                hour: 0-23
                minute: 0-59
                day_of_week: 0=Mon..6=Sun (or 0=Sun if week starts Sunday)
            """
            self._timer_data = (month, day, hour, minute, day_of_week)
            self.update()

        def mousePressEvent(self, event):
            """Handle click to toggle segments."""
            if event.button() != Qt.MouseButton.LeftButton:
                return

            pos = event.position()
            for i, seg in enumerate(self._segments):
                dx = pos.x() - seg["x"]
                dy = pos.y() - seg["y"]
                if dx * dx + dy * dy <= seg["r"] * seg["r"] * 2.25:
                    self.segment_clicked.emit(i)
                    return
