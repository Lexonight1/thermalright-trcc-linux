"""ImageCropDialog — pan/zoom/rotate an image to a target resolution.

Translates the legacy ``UCImageCut`` pattern into a clean QDialog:

* The crop region is shown live, scaled to fit a 480×480 preview.
* A real :class:`QSlider` controls zoom (no hand-painted pixel handles
  from the C# port).
* :class:`QToolBar` exposes fit-to-width, fit-to-height, rotate-90,
  reset.  Layout is QVBoxLayout, not absolute coordinates.
* OK returns the cropped :class:`QImage` at exactly the requested
  target resolution; Cancel returns ``None``.

The maths (pan multiplier per resolution, fit-width vs fit-height,
zoom curve) are the same as legacy so users get identical behaviour
across both apps.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QImage,
    QPainter,
    QPalette,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

_PREVIEW_W = 480
_PREVIEW_H = 480


_PAN_MULTIPLIERS: dict[tuple[int, int], int] = {
    (240, 240): 1, (320, 320): 1, (360, 360): 1,
    (480, 480): 2, (640, 480): 2, (800, 480): 3,
    (854, 480): 3, (960, 540): 3, (1280, 480): 4,
    (1600, 720): 4, (1920, 462): 4,
}


class _CropCanvas(QWidget):
    """The painted preview area + drag-to-pan interaction."""

    _BG = QColor("#000000")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_PREVIEW_W, _PREVIEW_H)
        self.setMouseTracking(True)
        self._display_pixmap: QPixmap | None = None
        self._drag_start = QPoint()
        self._dragging = False
        self._on_pan: Callable[[int, int], None] | None = None

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self._BG)
        self.setPalette(palette)
        self.setAutoFillBackground(True)

    def set_pan_callback(self, cb: Callable[[int, int], None]) -> None:
        self._on_pan = cb

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._display_pixmap = pix
        self.update()

    def paintEvent(self, event) -> None:
        if not self._display_pixmap or self._display_pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        px = self._display_pixmap
        x = (self.width() - px.width()) // 2
        y = (self.height() - px.height()) // 2
        painter.drawPixmap(x, y, px)
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = QPoint(
                int(event.position().x()), int(event.position().y()),
            )

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging or self._on_pan is None:
            return
        x = int(event.position().x())
        y = int(event.position().y())
        dx = x - self._drag_start.x()
        dy = y - self._drag_start.y()
        self._drag_start = QPoint(x, y)
        self._on_pan(dx, dy)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False


class ImageCropDialog(QDialog):
    """Modal dialog: pan / zoom / rotate an image to a fixed target.

    Usage::

        dialog = ImageCropDialog(parent)
        dialog.load_image(qimage_or_path, target_w=480, target_h=480)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            cropped = dialog.cropped()  # QImage at exactly target_w×target_h
    """

    _SLIDER_MIN: ClassVar[int] = 0
    _SLIDER_MAX: ClassVar[int] = 200
    _SLIDER_CENTER: ClassVar[int] = 100

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crop image")
        self.setModal(True)
        self._source: QImage | None = None
        self._target_w = 0
        self._target_h = 0
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self._rotation = 0
        self._pan_multiplier = 1
        self._build()

    # ── Public API ───────────────────────────────────────────────────

    def load_image(
        self, image: QImage | str, target_w: int, target_h: int,
    ) -> None:
        """Stage *image* for cropping at exactly *target_w×target_h*."""
        if isinstance(image, str):
            loaded = QImage(image)
            if loaded.isNull():
                log.warning("ImageCropDialog: cannot load %s", image)
                return
            self._source = loaded
        else:
            self._source = image.copy()

        self._target_w = target_w
        self._target_h = target_h
        self._rotation = 0
        self._pan_x = 0
        self._pan_y = 0
        self._pan_multiplier = _PAN_MULTIPLIERS.get((target_w, target_h), 1)
        self._target_label.setText(
            f"Target: {target_w}×{target_h}px",
        )

        # Auto-fit: portrait → height, landscape → width.  Most users
        # want the long edge filling the screen.
        if self._source.height() > self._source.width():
            self._fit_height()
        else:
            self._fit_width()

    def cropped(self) -> QImage | None:
        """Return the cropped result at target resolution (or None)."""
        return self._render_output()

    # ── UI build ─────────────────────────────────────────────────────

    def _build(self) -> None:
        toolbar = QToolBar(self)
        act_fit_w = QAction("Fit width", self)
        act_fit_h = QAction("Fit height", self)
        act_rotate = QAction("Rotate 90°", self)
        act_reset = QAction("Reset", self)
        act_fit_w.triggered.connect(self._fit_width)
        act_fit_h.triggered.connect(self._fit_height)
        act_rotate.triggered.connect(self._rotate_90)
        act_reset.triggered.connect(self._reset_view)
        toolbar.addAction(act_fit_w)
        toolbar.addAction(act_fit_h)
        toolbar.addSeparator()
        toolbar.addAction(act_rotate)
        toolbar.addSeparator()
        toolbar.addAction(act_reset)

        self._canvas = _CropCanvas(self)
        self._canvas.set_pan_callback(self._on_pan)

        self._zoom_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._zoom_slider.setRange(self._SLIDER_MIN, self._SLIDER_MAX)
        self._zoom_slider.setValue(self._SLIDER_CENTER)
        self._zoom_slider.valueChanged.connect(self._on_zoom_changed)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom:", self))
        zoom_row.addWidget(self._zoom_slider, stretch=1)

        self._target_label = QLabel("Target: (load an image)", self)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addWidget(toolbar)
        root.addWidget(self._target_label)
        root.addWidget(self._canvas)
        root.addLayout(zoom_row)
        root.addWidget(buttons)

    # ── Internals: maths ─────────────────────────────────────────────

    def _slider_to_zoom(self, v: int) -> float:
        # Match legacy: linear in slider → multiplicative around 1.0.
        if v >= self._SLIDER_CENTER:
            return 1.0 + (v - self._SLIDER_CENTER) * 0.03
        denom = 1.0 + (self._SLIDER_CENTER - v) * 0.03
        return 1.0 / denom if denom > 0 else 1.0

    def _zoom_to_slider(self, zoom: float) -> int:
        if zoom >= 1.0:
            return self._SLIDER_CENTER + round((zoom - 1.0) / 0.03)
        if zoom <= 0:
            return self._SLIDER_CENTER
        return self._SLIDER_CENTER - round((1.0 / zoom - 1.0) / 0.03)

    def _rotated_source(self) -> QImage | None:
        if self._source is None or self._source.isNull():
            return None
        if self._rotation == 0:
            return self._source
        t = QTransform().rotate(float(self._rotation))
        return self._source.transformed(
            t, Qt.TransformationMode.SmoothTransformation,
        )

    def _fit_width(self) -> None:
        img = self._rotated_source()
        if img is None:
            return
        src_w = img.width()
        self._zoom = self._target_w / src_w if src_w > 0 else 1.0
        self._pan_x = 0
        self._pan_y = 0
        self._sync_slider_silently()
        self._rebuild_display()

    def _fit_height(self) -> None:
        img = self._rotated_source()
        if img is None:
            return
        src_h = img.height()
        self._zoom = self._target_h / src_h if src_h > 0 else 1.0
        self._pan_x = 0
        self._pan_y = 0
        self._sync_slider_silently()
        self._rebuild_display()

    def _rotate_90(self) -> None:
        self._rotation = (self._rotation + 90) % 360
        self._pan_x = 0
        self._pan_y = 0
        img = self._rotated_source()
        if img is None:
            return
        if img.height() > img.width():
            self._fit_height()
        else:
            self._fit_width()

    def _reset_view(self) -> None:
        self._rotation = 0
        self._pan_x = 0
        self._pan_y = 0
        img = self._rotated_source()
        if img is None:
            return
        if img.height() > img.width():
            self._fit_height()
        else:
            self._fit_width()

    def _sync_slider_silently(self) -> None:
        v = max(self._SLIDER_MIN, min(self._SLIDER_MAX,
                                       self._zoom_to_slider(self._zoom)))
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(v)
        self._zoom_slider.blockSignals(False)

    def _on_zoom_changed(self, value: int) -> None:
        img = self._rotated_source()
        if img is None:
            return
        old_zoom = self._zoom
        self._zoom = self._slider_to_zoom(value)
        # Keep the panned-out point anchored as the zoom changes — pan
        # offset shifts by half the size delta.
        sw, sh = img.width(), img.height()
        old_w, old_h = int(sw * old_zoom), int(sh * old_zoom)
        new_w, new_h = int(sw * self._zoom), int(sh * self._zoom)
        self._pan_x -= (new_w - old_w) // 2
        self._pan_y -= (new_h - old_h) // 2
        self._rebuild_display()

    def _on_pan(self, dx: int, dy: int) -> None:
        self._pan_x += dx * self._pan_multiplier
        self._pan_y += dy * self._pan_multiplier
        self._rebuild_display()

    def _render_output(self) -> QImage | None:
        img = self._rotated_source()
        if img is None or self._target_w == 0 or self._target_h == 0:
            return None

        sw, sh = img.width(), img.height()
        new_w = int(sw * self._zoom)
        new_h = int(sh * self._zoom)
        if new_w < 1 or new_h < 1:
            return None
        scaled = img.scaled(
            new_w, new_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        output = QImage(
            self._target_w, self._target_h, QImage.Format.Format_RGB32,
        )
        output.fill(QColor(0, 0, 0))
        cx = (self._target_w - new_w) // 2 + self._pan_x
        cy = (self._target_h - new_h) // 2 + self._pan_y
        painter = QPainter(output)
        painter.drawImage(cx, cy, scaled)
        painter.end()
        return output

    def _rebuild_display(self) -> None:
        output = self._render_output()
        if output is None:
            self._canvas.set_pixmap(None)
            return
        pw, ph = output.width(), output.height()
        scale = min(_PREVIEW_W / pw, _PREVIEW_H / ph)
        disp_w, disp_h = int(pw * scale), int(ph * scale)
        display = output.scaled(
            disp_w, disp_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._canvas.set_pixmap(QPixmap.fromImage(display))
