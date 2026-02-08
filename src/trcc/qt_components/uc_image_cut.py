"""
PyQt6 UCImageCut - Image cropper panel.

Matches Windows TRCC UCImageCut functionality (500x702).
Provides pan, zoom, rotation, and fit-mode controls for cropping
images to LCD target resolution.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget

from .assets import load_pixmap
from .base import make_icon_button

try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ============================================================================
# Constants
# ============================================================================

PANEL_W, PANEL_H = 500, 702
PREVIEW_X, PREVIEW_Y = 0, 0
PREVIEW_W, PREVIEW_H = 500, 540

# Zoom slider
SLIDER_Y = 546
SLIDER_H = 46
SLIDER_X_MIN = 12
SLIDER_X_MAX = 484
SLIDER_CENTER = 248
SLIDER_HANDLE_R = 8  # radius

# Buttons (y=656 row)
BTN_HEIGHT_FIT = (169, 656, 34, 26)
BTN_WIDTH_FIT = (233, 656, 34, 26)
BTN_ROTATE = (297, 656, 34, 26)
BTN_OK = (446, 656, 34, 26)
BTN_CLOSE = (474, 510, 16, 16)

# Pan multipliers per resolution
_PAN_MULTIPLIERS = {
    (240, 240): 1, (320, 320): 1, (360, 360): 1,
    (480, 480): 2, (640, 480): 2, (800, 480): 3,
    (854, 480): 3, (960, 540): 3, (1280, 480): 4,
    (1600, 720): 4, (1920, 462): 4,
}


class UCImageCut(QWidget):
    """Image cropper panel (500x702).

    Provides zoom slider, pan via drag, rotation, fit modes.
    Returns cropped PIL Image at target resolution on OK, or None on cancel.

    Signals:
        image_cut_done(object): PIL Image on OK, None on cancel.
    """

    image_cut_done = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PANEL_W, PANEL_H)
        self.setMouseTracking(True)

        # Image state
        self._source_image = None   # Original PIL Image (never modified)
        self._target_w = 320
        self._target_h = 320

        # View state
        self._zoom = 1.0          # bili factor
        self._pan_x = 0           # offset in source pixels
        self._pan_y = 0
        self._rotation = 0        # degrees (0, 90, 180, 270)

        # Interaction state
        self._slider_x = SLIDER_CENTER   # zoom slider handle position
        self._dragging_slider = False
        self._dragging_image = False
        self._drag_start = QPoint()
        self._pan_multiplier = 1

        # Cached display pixmap
        self._display_pixmap = None

        # Dark background
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#232227'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self._setup_ui()

    def _setup_ui(self):
        self._btn_height_fit = make_icon_button(
            self, BTN_HEIGHT_FIT, 'P高度适应.png', "H", self._on_height_fit)
        self._btn_width_fit = make_icon_button(
            self, BTN_WIDTH_FIT, 'P宽度适应.png', "W", self._on_width_fit)
        self._btn_rotate = make_icon_button(
            self, BTN_ROTATE, 'P旋转.png', "R", self._on_rotate)
        self._btn_ok = make_icon_button(
            self, BTN_OK, 'P裁减.png', "OK", self._on_ok)
        self._btn_close = make_icon_button(
            self, BTN_CLOSE, 'P关闭按钮.png', "\u2715", self._on_close)

    # =========================================================================
    # Public API
    # =========================================================================

    def load_image(self, pil_image, target_w, target_h):
        """Load a PIL Image for cropping.

        Args:
            pil_image: Source PIL Image
            target_w: Target width for LCD
            target_h: Target height for LCD
        """
        if not PIL_AVAILABLE or pil_image is None:
            return

        self._source_image = pil_image.copy()
        self._target_w = target_w
        self._target_h = target_h
        self._rotation = 0
        self._pan_x = 0
        self._pan_y = 0
        self._pan_multiplier = _PAN_MULTIPLIERS.get((target_w, target_h), 1)

        # Auto-fit: portrait → height fit, landscape → width fit
        src_w, src_h = pil_image.size
        if src_h > src_w:
            self._fit_height()
        else:
            self._fit_width()

        # Load resolution-specific background
        bg_name = f'P0图片裁减{target_w}{target_h}.png'
        bg_pix = load_pixmap(bg_name, PANEL_W, PANEL_H)
        if not bg_pix.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(bg_pix))
            self.setPalette(palette)

    def set_resolution(self, w, h):
        """Set target resolution."""
        self._target_w = w
        self._target_h = h
        self._pan_multiplier = _PAN_MULTIPLIERS.get((w, h), 1)

    # =========================================================================
    # Internal: zoom / fit
    # =========================================================================

    def _calc_zoom_from_slider(self, cx):
        """Windows zoom formula: bili = f(slider_x)."""
        if cx > SLIDER_CENTER:
            return 1.0 + (cx - SLIDER_CENTER) * 0.03
        else:
            denom = 1.0 + (SLIDER_CENTER - cx) * 0.03
            return 1.0 / denom if denom > 0 else 1.0

    def _slider_x_from_zoom(self, zoom):
        """Inverse of zoom formula → slider position."""
        if zoom >= 1.0:
            return int(SLIDER_CENTER + (zoom - 1.0) / 0.03)
        else:
            # zoom = 1 / (1 + d*0.03) → d = (1/zoom - 1)/0.03
            d = (1.0 / zoom - 1.0) / 0.03 if zoom > 0 else 0
            return int(SLIDER_CENTER - d)

    def _fit_width(self):
        """Fit image to target width."""
        if not self._source_image:
            return
        img = self._get_rotated_source()
        if img is None:
            return
        src_w, src_h = img.size
        self._zoom = self._target_w / src_w if src_w > 0 else 1.0
        self._slider_x = max(SLIDER_X_MIN, min(SLIDER_X_MAX,
                             self._slider_x_from_zoom(self._zoom)))
        self._pan_x = 0
        self._pan_y = 0
        self._rebuild_display()

    def _fit_height(self):
        """Fit image to target height."""
        if not self._source_image:
            return
        img = self._get_rotated_source()
        if img is None:
            return
        src_w, src_h = img.size
        self._zoom = self._target_h / src_h if src_h > 0 else 1.0
        self._slider_x = max(SLIDER_X_MIN, min(SLIDER_X_MAX,
                             self._slider_x_from_zoom(self._zoom)))
        self._pan_x = 0
        self._pan_y = 0
        self._rebuild_display()

    def _get_rotated_source(self):
        """Get source image with rotation applied."""
        if not self._source_image:
            return None
        img = self._source_image
        if self._rotation == 90:
            img = img.transpose(PILImage.Transpose.ROTATE_270)
        elif self._rotation == 180:
            img = img.transpose(PILImage.Transpose.ROTATE_180)
        elif self._rotation == 270:
            img = img.transpose(PILImage.Transpose.ROTATE_90)
        return img

    def _get_cropped_output(self):
        """Get the final cropped PIL Image at target resolution."""
        img = self._get_rotated_source()
        if img is None:
            return None

        src_w, src_h = img.size
        new_w = int(src_w * self._zoom)
        new_h = int(src_h * self._zoom)
        if new_w < 1 or new_h < 1:
            return None

        scaled = img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)

        # Create black canvas at target resolution
        output = PILImage.new('RGB', (self._target_w, self._target_h), (0, 0, 0))

        # Paste scaled image with pan offset centered
        cx = (self._target_w - new_w) // 2 + self._pan_x
        cy = (self._target_h - new_h) // 2 + self._pan_y
        output.paste(scaled, (cx, cy))

        return output

    def _rebuild_display(self):
        """Rebuild the display pixmap for the preview area."""
        output = self._get_cropped_output()
        if output is None:
            self._display_pixmap = None
            self.update()
            return

        # Scale to fit preview area
        pw, ph = output.size
        scale = min(PREVIEW_W / pw, PREVIEW_H / ph)
        disp_w, disp_h = int(pw * scale), int(ph * scale)
        display = output.resize((disp_w, disp_h), PILImage.Resampling.LANCZOS)

        # Convert PIL → QPixmap
        if display.mode != 'RGB':
            display = display.convert('RGB')
        data = display.tobytes('raw', 'RGB')
        qimg = QImage(data, disp_w, disp_h, disp_w * 3, QImage.Format.Format_RGB888)
        self._display_pixmap = QPixmap.fromImage(qimg)
        self.update()

    # =========================================================================
    # Painting
    # =========================================================================

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Preview area background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor('#000000')))
        p.drawRect(PREVIEW_X, PREVIEW_Y, PREVIEW_W, PREVIEW_H)

        # Display cropped image
        if self._display_pixmap and not self._display_pixmap.isNull():
            px = self._display_pixmap
            x = PREVIEW_X + (PREVIEW_W - px.width()) // 2
            y = PREVIEW_Y + (PREVIEW_H - px.height()) // 2
            p.drawPixmap(x, y, px)

        # Zoom slider track
        track_y = SLIDER_Y + SLIDER_H // 2
        p.setPen(QPen(QColor('#555'), 2))
        p.drawLine(SLIDER_X_MIN, track_y, SLIDER_X_MAX, track_y)

        # Zoom slider handle
        p.setPen(QPen(QColor('#AAA'), 1))
        p.setBrush(QBrush(QColor('#FFF')))
        p.drawEllipse(
            int(self._slider_x - SLIDER_HANDLE_R),
            int(track_y - SLIDER_HANDLE_R),
            SLIDER_HANDLE_R * 2, SLIDER_HANDLE_R * 2
        )

        p.end()

    # =========================================================================
    # Mouse interaction
    # =========================================================================

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = int(event.position().x()), int(event.position().y())

        # Check slider area
        if SLIDER_Y <= y <= SLIDER_Y + SLIDER_H:
            self._dragging_slider = True
            self._update_slider(x)
            return

        # Check preview area → start pan
        if PREVIEW_Y <= y <= PREVIEW_Y + PREVIEW_H:
            self._dragging_image = True
            self._drag_start = QPoint(x, y)

    def mouseMoveEvent(self, event):
        x, y = int(event.position().x()), int(event.position().y())

        if self._dragging_slider:
            self._update_slider(x)
        elif self._dragging_image:
            dx = x - self._drag_start.x()
            dy = y - self._drag_start.y()
            self._pan_x += int(dx * self._pan_multiplier)
            self._pan_y += int(dy * self._pan_multiplier)
            self._drag_start = QPoint(x, y)
            self._rebuild_display()

    def mouseReleaseEvent(self, event):
        self._dragging_slider = False
        self._dragging_image = False

    def wheelEvent(self, event):
        """Zoom via mouse wheel."""
        delta = event.angleDelta().y()
        step = 20 if delta > 0 else -20
        self._update_slider(self._slider_x + step)

    def _update_slider(self, x):
        """Update zoom slider position and recalculate zoom."""
        old_zoom = self._zoom
        self._slider_x = max(SLIDER_X_MIN, min(SLIDER_X_MAX, x))
        self._zoom = self._calc_zoom_from_slider(self._slider_x)

        # Compensate pan to keep center stable
        if self._source_image and old_zoom > 0:
            img = self._get_rotated_source()
            if img:
                sw, sh = img.size
                old_w, old_h = int(sw * old_zoom), int(sh * old_zoom)
                new_w, new_h = int(sw * self._zoom), int(sh * self._zoom)
                self._pan_x -= (new_w - old_w) // 2
                self._pan_y -= (new_h - old_h) // 2

        self._rebuild_display()

    # =========================================================================
    # Button handlers
    # =========================================================================

    def _on_width_fit(self):
        self._fit_width()

    def _on_height_fit(self):
        self._fit_height()

    def _on_rotate(self):
        self._rotation = (self._rotation + 90) % 360
        self._pan_x = 0
        self._pan_y = 0
        # Re-fit after rotation
        img = self._get_rotated_source()
        if img:
            src_w, src_h = img.size
            if src_h > src_w:
                self._fit_height()
            else:
                self._fit_width()

    def _on_ok(self):
        output = self._get_cropped_output()
        self.image_cut_done.emit(output)

    def _on_close(self):
        self.image_cut_done.emit(None)
