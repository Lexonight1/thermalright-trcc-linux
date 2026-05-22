"""Image cropper panel — Qt-native, no PIL.

Matches Windows TRCC UCImageCut functionality (500x702).
Provides pan, zoom, rotation, and fit-mode controls for cropping
images to LCD target resolution.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from trcc.core.models import panel_asset_dims

from .assets import Assets
from .base import make_icon_button

log = logging.getLogger(__name__)

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
    Returns cropped QImage at target resolution on OK, or None on cancel.

    Signals:
        image_cut_done(object): QImage on OK, None on cancel.
    """

    image_cut_done = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PANEL_W, PANEL_H)
        self.setMouseTracking(True)

        # Image state
        self._source_image: QImage | None = None  # Original (never modified)
        self._target_w = 0
        self._target_h = 0

        # View state
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self._rotation = 0  # degrees (0, 90, 180, 270)

        # Interaction state
        self._slider_x = SLIDER_CENTER
        self._dragging_slider = False
        self._dragging_image = False
        self._drag_start = QPoint()
        self._pan_multiplier = 1

        # Cached display pixmap
        self._display_pixmap: QPixmap | None = None

        # Dark background
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#232227'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self._setup_ui()

    def _setup_ui(self):
        self._btn_height_fit = make_icon_button(
            self, BTN_HEIGHT_FIT, 'display_mode_fit_height.png', "H", self._on_height_fit)
        self._btn_width_fit = make_icon_button(
            self, BTN_WIDTH_FIT, 'display_mode_fit_width.png', "W", self._on_width_fit)
        self._btn_rotate = make_icon_button(
            self, BTN_ROTATE, 'display_mode_rotate.png', "R", self._on_rotate)
        self._btn_ok = make_icon_button(
            self, BTN_OK, 'display_mode_crop.png', "OK", self._on_ok)
        self._btn_close = make_icon_button(
            self, BTN_CLOSE, 'shared_close.png', "\u2715", self._on_close)

    # =========================================================================
    # Public API
    # =========================================================================

    def load_image(self, image: QImage | str, target_w: int, target_h: int) -> None:
        """Load a QImage (or file path) for cropping."""
        if image is None:
            return

        if isinstance(image, str):
            loaded = QImage(image)
            if loaded.isNull():
                return
            self._source_image = loaded
        else:
            self._source_image = image.copy()

        self._target_w = target_w
        self._target_h = target_h
        self._rotation = 0
        self._pan_x = 0
        self._pan_y = 0
        self._pan_multiplier = _PAN_MULTIPLIERS.get((target_w, target_h), 1)

        # Auto-fit: portrait → height fit, landscape → width fit
        if self._source_image.height() > self._source_image.width():
            self._fit_height()
        else:
            self._fit_width()

        # Load resolution-specific background (C# scaled dims, not raw LCD dims)
        self._load_panel_background(target_w, target_h)

    def set_resolution(self, w: int, h: int) -> None:
        self._target_w = w
        self._target_h = h
        self._pan_multiplier = _PAN_MULTIPLIERS.get((w, h), 1)
        self._load_panel_background(w, h)

    def _load_panel_background(self, w: int, h: int) -> None:
        """Load the scaled panel background asset for a device resolution."""
        pw, ph = panel_asset_dims(w, h)
        bg_name = f'image_cut_{pw}x{ph}.png'
        log.debug("_load_panel_background: %dx%d → panel %dx%d asset=%s",
                  w, h, pw, ph, bg_name)
        bg_pix = Assets.load_pixmap(bg_name, PANEL_W, PANEL_H)
        if not bg_pix.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(bg_pix))
            self.setPalette(palette)

    # =========================================================================
    # Internal: zoom / fit
    # =========================================================================

    def _calc_zoom_from_slider(self, cx: int) -> float:
        if cx > SLIDER_CENTER:
            return 1.0 + (cx - SLIDER_CENTER) * 0.03
        else:
            denom = 1.0 + (SLIDER_CENTER - cx) * 0.03
            return 1.0 / denom if denom > 0 else 1.0

    def _slider_x_from_zoom(self, zoom: float) -> int:
        if zoom >= 1.0:
            return int(SLIDER_CENTER + (zoom - 1.0) / 0.03)
        else:
            d = (1.0 / zoom - 1.0) / 0.03 if zoom > 0 else 0
            return int(SLIDER_CENTER - d)

    def _fit_width(self) -> None:
        if not self._source_image:
            return
        img = self._get_rotated_source()
        if img is None:
            return
        src_w = img.width()
        self._zoom = self._target_w / src_w if src_w > 0 else 1.0
        self._slider_x = max(SLIDER_X_MIN, min(SLIDER_X_MAX,
                             self._slider_x_from_zoom(self._zoom)))
        self._pan_x = 0
        self._pan_y = 0
        self._rebuild_display()

    def _fit_height(self) -> None:
        if not self._source_image:
            return
        img = self._get_rotated_source()
        if img is None:
            return
        src_h = img.height()
        self._zoom = self._target_h / src_h if src_h > 0 else 1.0
        self._slider_x = max(SLIDER_X_MIN, min(SLIDER_X_MAX,
                             self._slider_x_from_zoom(self._zoom)))
        self._pan_x = 0
        self._pan_y = 0
        self._rebuild_display()

    def _get_rotated_source(self) -> QImage | None:
        """Return source image with rotation applied (Qt QTransform)."""
        if self._source_image is None or self._source_image.isNull():
            return None
        if self._rotation == 0:
            return self._source_image
        t = QTransform().rotate(float(self._rotation))
        return self._source_image.transformed(t, Qt.TransformationMode.SmoothTransformation)

    def _get_cropped_output(self) -> QImage | None:
        """Return the final cropped QImage at target resolution."""
        img = self._get_rotated_source()
        if img is None:
            return None

        src_w, src_h = img.width(), img.height()
        new_w = int(src_w * self._zoom)
        new_h = int(src_h * self._zoom)
        if new_w < 1 or new_h < 1:
            return None

        scaled = img.scaled(new_w, new_h,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)

        # Black canvas at target resolution
        output = QImage(self._target_w, self._target_h, QImage.Format.Format_RGB32)
        output.fill(QColor(0, 0, 0))

        # Paste scaled image with pan offset centered
        cx = (self._target_w - new_w) // 2 + self._pan_x
        cy = (self._target_h - new_h) // 2 + self._pan_y

        painter = QPainter(output)
        painter.drawImage(cx, cy, scaled)
        painter.end()

        return output

    def _rebuild_display(self) -> None:
        output = self._get_cropped_output()
        if output is None:
            self._display_pixmap = None
            self.update()
            return

        pw, ph = output.width(), output.height()
        scale = min(PREVIEW_W / pw, PREVIEW_H / ph)
        disp_w, disp_h = int(pw * scale), int(ph * scale)
        display = output.scaled(disp_w, disp_h,
                                Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
        self._display_pixmap = QPixmap.fromImage(display)
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

        if SLIDER_Y <= y <= SLIDER_Y + SLIDER_H:
            self._dragging_slider = True
            self._update_slider(x)
            return

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
        delta = event.angleDelta().y()
        step = 20 if delta > 0 else -20
        self._update_slider(self._slider_x + step)

    def _update_slider(self, x: int) -> None:
        old_zoom = self._zoom
        self._slider_x = max(SLIDER_X_MIN, min(SLIDER_X_MAX, x))
        self._zoom = self._calc_zoom_from_slider(self._slider_x)

        if self._source_image and old_zoom > 0:
            img = self._get_rotated_source()
            if img:
                sw, sh = img.width(), img.height()
                old_w = int(sw * old_zoom)
                old_h = int(sh * old_zoom)
                new_w = int(sw * self._zoom)
                new_h = int(sh * self._zoom)
                self._pan_x -= (new_w - old_w) // 2
                self._pan_y -= (new_h - old_h) // 2

        self._rebuild_display()

    # =========================================================================
    # Button handlers
    # =========================================================================

    def _on_width_fit(self):
        log.debug("_on_width_fit")
        self._fit_width()

    def _on_height_fit(self):
        log.debug("_on_height_fit")
        self._fit_height()

    def _on_rotate(self):
        log.debug("_on_rotate: rotation=%s→%s", self._rotation, (self._rotation + 90) % 360)
        self._rotation = (self._rotation + 90) % 360
        self._pan_x = 0
        self._pan_y = 0
        img = self._get_rotated_source()
        if img:
            if img.height() > img.width():
                self._fit_height()
            else:
                self._fit_width()

    def _on_ok(self):
        log.debug("_on_ok: emitting image_cut_done with cropped output")
        output = self._get_cropped_output()
        self.image_cut_done.emit(output)

    def _on_close(self):
        log.debug("_on_close: emitting image_cut_done(None)")
        self.image_cut_done.emit(None)
