#!/usr/bin/env python3
"""
7-segment digital display renderer for HR10 panel preview.

Custom QPainter widget that renders numeric values as 7-segment digits,
matching the physical HR10_2280_PRO_DIGITAL NVMe heatsink display.
The digit color follows the current LED color setting.
"""

from typing import List, Optional, Tuple

try:
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
    from PyQt6.QtWidgets import QWidget
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False


# 7-segment encoding: each digit maps to which segments are ON.
# Segment layout:
#    _a_
#   |   |
#   f   b
#   |_g_|
#   |   |
#   e   c
#   |_d_|
#
# Bits: a=0, b=1, c=2, d=3, e=4, f=5, g=6
SEGMENT_MAP = {
    '0': (True,  True,  True,  True,  True,  True,  False),
    '1': (False, True,  True,  False, False, False, False),
    '2': (True,  True,  False, True,  True,  False, True),
    '3': (True,  True,  True,  True,  False, False, True),
    '4': (False, True,  True,  False, False, True,  True),
    '5': (True,  False, True,  True,  False, True,  True),
    '6': (True,  False, True,  True,  True,  True,  True),
    '7': (True,  True,  True,  False, False, False, False),
    '8': (True,  True,  True,  True,  True,  True,  True),
    '9': (True,  True,  True,  True,  False, True,  True),
    '-': (False, False, False, False, False, False, True),
    ' ': (False, False, False, False, False, False, False),
    # Letters for unit display (approximations on 7-segment)
    'A': (True,  True,  True,  False, True,  True,  True),
    'b': (False, False, True,  True,  True,  True,  True),
    'C': (True,  False, False, True,  True,  True,  False),
    'c': (False, False, False, True,  True,  False, True),
    'd': (False, True,  True,  True,  True,  False, True),
    'E': (True,  False, False, True,  True,  True,  True),
    'F': (True,  False, False, False, True,  True,  True),
    'H': (False, True,  True,  False, True,  True,  True),
    'L': (False, False, False, True,  True,  True,  False),
    'n': (False, False, True,  False, True,  False, True),
    'o': (False, False, True,  True,  True,  False, True),
    'P': (True,  True,  False, False, True,  True,  True),
    'r': (False, False, False, False, True,  False, True),
    'S': (True,  False, True,  True,  False, True,  True),  # same as 5
    't': (False, False, False, True,  True,  True,  True),
    'U': (False, True,  True,  True,  True,  True,  False),
    'u': (False, False, True,  True,  True,  False, False),
    # 'B' shown as 'b' (uppercase B = 8 which is confusing)
    'B': (False, False, True,  True,  True,  True,  True),
}


if PYQT6_AVAILABLE:

    class UCSevenSegment(QWidget):
        """7-segment digital display preview widget.

        Renders numeric values as glowing 7-segment digits on a dark
        background, previewing what the HR10 heatsink display shows.
        """

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setFixedSize(500, 400)

            self._value_text = "---"   # Text to render as digits
            self._unit_text = ""        # Unit suffix (°C, %, MB/s)
            self._color = QColor(255, 120, 0)  # Digit color (amber default)
            self._dim_color = QColor(30, 25, 20)  # Off-segment color

            # Digit layout: 4 main digits + unit area
            self._digit_count = 4

        def set_value(self, text: str, unit: str = "") -> None:
            """Set the display value and unit.

            Args:
                text: Numeric string to display (e.g., "42", "1.5").
                unit: Unit suffix (e.g., "°C", "%", "MB/s").
            """
            # Pad/truncate to fit display
            self._value_text = text[:self._digit_count].rjust(self._digit_count)
            self._unit_text = unit
            self.update()

        def set_color(self, r: int, g: int, b: int) -> None:
            """Set digit color from LED color setting."""
            self._color = QColor(r, g, b)
            # Compute dim version (10% brightness for off segments)
            self._dim_color = QColor(
                max(15, r // 10),
                max(15, g // 10),
                max(15, b // 10),
            )
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            # Dark background (matching the Windows HR10 display area)
            painter.fillRect(self.rect(), QColor(15, 12, 10))

            # Draw rounded border
            painter.setPen(QPen(QColor(50, 45, 40), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(2, 2, self.width() - 4, self.height() - 4, 8, 8)

            w = self.width()
            h = self.height()

            # Digit area dimensions
            margin_x = 30
            margin_y = 40
            digit_area_w = w - 2 * margin_x
            digit_area_h = h - 2 * margin_y

            # Each digit gets equal width, with small gaps
            gap = 12
            total_digits = len(self._value_text)
            digit_w = (digit_area_w - gap * total_digits) / max(total_digits, 1)
            digit_h = digit_area_h * 0.85

            # Center vertically
            y_offset = margin_y + (digit_area_h - digit_h) / 2

            for i, ch in enumerate(self._value_text):
                x = margin_x + i * (digit_w + gap)

                if ch == '.':
                    # Draw decimal point
                    dot_size = digit_w * 0.15
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(self._color))
                    painter.drawEllipse(
                        QPointF(x + digit_w * 0.3, y_offset + digit_h - dot_size),
                        dot_size, dot_size
                    )
                elif ch in SEGMENT_MAP:
                    self._draw_digit(
                        painter, x, y_offset, digit_w, digit_h,
                        SEGMENT_MAP[ch]
                    )
                # Skip unknown chars (spaces already handled)

            # Draw unit symbols (right side) as 7-segment-style characters
            if self._unit_text:
                unit_x = margin_x + total_digits * (digit_w + gap) + gap
                # Smaller scale for unit characters
                unit_digit_w = digit_w * 0.45
                unit_digit_h = digit_h * 0.45
                unit_gap = gap * 0.5
                # Vertical center for unit characters (lower half of display)
                unit_y = y_offset + digit_h * 0.45

                self._draw_unit_string(
                    painter, unit_x, unit_y,
                    unit_digit_w, unit_digit_h, unit_gap,
                    self._unit_text
                )

            painter.end()

        def _draw_digit(self, painter: 'QPainter', x: float, y: float,
                        w: float, h: float, segments: tuple) -> None:
            """Draw a single 7-segment digit.

            Args:
                painter: Active QPainter.
                x, y: Top-left corner of digit cell.
                w, h: Digit cell dimensions.
                segments: 7-tuple of booleans (a, b, c, d, e, f, g).
            """
            # Segment thickness proportional to digit size
            t = max(4, w * 0.14)
            half_t = t / 2

            # Horizontal segment length (slightly shorter than cell width)
            seg_w = w * 0.7
            # Vertical segment length
            seg_h = (h - 3 * t) / 2

            # Center the segments within the digit cell
            cx = x + w / 2
            top = y + t
            mid = y + t + seg_h + half_t
            bot = y + t + 2 * seg_h + t

            # Segment positions: (center_x, center_y, is_horizontal)
            # a: top horizontal
            # b: top-right vertical
            # c: bottom-right vertical
            # d: bottom horizontal
            # e: bottom-left vertical
            # f: top-left vertical
            # g: middle horizontal
            seg_defs = [
                (cx, top, True),                          # a
                (cx + seg_w / 2, top + seg_h / 2 + half_t, False),   # b
                (cx + seg_w / 2, mid + seg_h / 2 + half_t, False),   # c
                (cx, bot + half_t, True),                  # d
                (cx - seg_w / 2, mid + seg_h / 2 + half_t, False),   # e
                (cx - seg_w / 2, top + seg_h / 2 + half_t, False),   # f
                (cx, mid, True),                           # g
            ]

            for idx, (sx, sy, horiz) in enumerate(seg_defs):
                is_on = segments[idx]
                color = self._color if is_on else self._dim_color

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(color))

                if horiz:
                    # Horizontal segment: hexagonal shape
                    poly = self._make_h_segment(sx, sy, seg_w, t)
                else:
                    # Vertical segment: hexagonal shape
                    poly = self._make_v_segment(sx, sy, seg_h, t)

                painter.drawPolygon(poly)

        def _draw_unit_string(self, painter: 'QPainter', x: float, y: float,
                              digit_w: float, digit_h: float, gap: float,
                              unit: str) -> None:
            """Render a unit string using 7-segment-style characters.

            Handles special symbols: ° (degree), % (percent), / (slash).
            Regular characters use SEGMENT_MAP.
            """
            cursor_x = x
            for ch in unit:
                if ch == '°':
                    # Degree symbol: small square at top-right of previous char
                    self._draw_degree(painter, cursor_x, y, digit_w, digit_h)
                    cursor_x += digit_w * 0.5 + gap * 0.5
                elif ch == '%':
                    # Percent: two small circles with diagonal slash
                    self._draw_percent(painter, cursor_x, y, digit_w, digit_h)
                    cursor_x += digit_w + gap
                elif ch == '/':
                    # Slash: diagonal line
                    self._draw_slash(painter, cursor_x, y, digit_w, digit_h)
                    cursor_x += digit_w * 0.5 + gap * 0.5
                elif ch == 'M':
                    # M can't be done on 7-segment — custom line drawing
                    self._draw_letter_M(painter, cursor_x, y, digit_w, digit_h)
                    cursor_x += digit_w + gap
                elif ch in SEGMENT_MAP:
                    self._draw_digit(painter, cursor_x, y, digit_w, digit_h,
                                     SEGMENT_MAP[ch])
                    cursor_x += digit_w + gap
                # Skip unknown chars

        def _draw_letter_M(self, painter: 'QPainter', x: float, y: float,
                           w: float, h: float) -> None:
            """Draw letter M using line segments (can't be done in 7-seg)."""
            t = max(2, w * 0.14)
            painter.setPen(QPen(self._color, t))
            # Left vertical
            painter.drawLine(
                QPointF(x + w * 0.15, y + h * 0.1),
                QPointF(x + w * 0.15, y + h * 0.9),
            )
            # Right vertical
            painter.drawLine(
                QPointF(x + w * 0.85, y + h * 0.1),
                QPointF(x + w * 0.85, y + h * 0.9),
            )
            # Left diagonal (top-left to center)
            painter.drawLine(
                QPointF(x + w * 0.15, y + h * 0.1),
                QPointF(x + w * 0.5, y + h * 0.45),
            )
            # Right diagonal (top-right to center)
            painter.drawLine(
                QPointF(x + w * 0.85, y + h * 0.1),
                QPointF(x + w * 0.5, y + h * 0.45),
            )

        def _draw_degree(self, painter: 'QPainter', x: float, y: float,
                         w: float, h: float) -> None:
            """Draw degree symbol as a small square/circle at top."""
            size = w * 0.35
            t = max(2, size * 0.25)
            cx = x + w * 0.25
            cy = y + h * 0.08
            painter.setPen(QPen(self._color, t))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(
                int(cx), int(cy), int(size), int(size)
            )

        def _draw_percent(self, painter: 'QPainter', x: float, y: float,
                          w: float, h: float) -> None:
            """Draw percent symbol: two small circles with diagonal slash."""
            t = max(2, w * 0.1)
            circle_r = w * 0.15

            # Top-left circle
            painter.setPen(QPen(self._color, t))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(
                QPointF(x + w * 0.25, y + h * 0.2), circle_r, circle_r
            )

            # Bottom-right circle
            painter.drawEllipse(
                QPointF(x + w * 0.75, y + h * 0.8), circle_r, circle_r
            )

            # Diagonal slash
            painter.setPen(QPen(self._color, t))
            painter.drawLine(
                QPointF(x + w * 0.8, y + h * 0.1),
                QPointF(x + w * 0.2, y + h * 0.9),
            )

        def _draw_slash(self, painter: 'QPainter', x: float, y: float,
                        w: float, h: float) -> None:
            """Draw a forward slash as a diagonal segment."""
            t = max(2, w * 0.14)
            painter.setPen(QPen(self._color, t))
            painter.drawLine(
                QPointF(x + w * 0.7, y + h * 0.1),
                QPointF(x + w * 0.1, y + h * 0.9),
            )

        @staticmethod
        def _make_h_segment(cx: float, cy: float, length: float,
                           thickness: float) -> 'QPolygonF':
            """Create a horizontal hexagonal segment polygon."""
            half_l = length / 2
            half_t = thickness / 2
            tip = thickness * 0.4  # Pointed ends
            return QPolygonF([
                QPointF(cx - half_l, cy),
                QPointF(cx - half_l + tip, cy - half_t),
                QPointF(cx + half_l - tip, cy - half_t),
                QPointF(cx + half_l, cy),
                QPointF(cx + half_l - tip, cy + half_t),
                QPointF(cx - half_l + tip, cy + half_t),
            ])

        @staticmethod
        def _make_v_segment(cx: float, cy: float, length: float,
                           thickness: float) -> 'QPolygonF':
            """Create a vertical hexagonal segment polygon."""
            half_l = length / 2
            half_t = thickness / 2
            tip = thickness * 0.4  # Pointed ends
            return QPolygonF([
                QPointF(cx, cy - half_l),
                QPointF(cx + half_t, cy - half_l + tip),
                QPointF(cx + half_t, cy + half_l - tip),
                QPointF(cx, cy + half_l),
                QPointF(cx - half_t, cy + half_l - tip),
                QPointF(cx - half_t, cy - half_l + tip),
            ])
