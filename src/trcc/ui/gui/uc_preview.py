"""
PyQt6 UCPreview - Preview panel with frame.

Matches Windows TRCC.DCUserControl.UCScreenImageBK (500x500)
Contains the LCD preview with decorative frame.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSlider, QVBoxLayout

from ..presentation.lcd_panel import lcd_panel_for
from .assets import Assets
from .base import BasePanel, ImageLabel, set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles

log = logging.getLogger(__name__)


class UCPreview(BasePanel):
    """
    Preview panel with frame image.

    Windows: UCScreenImageBK (500x500) contains UCScreenImage (varies by resolution)
    Frame image provides decorative border around the LCD preview.
    """

    # Resolution → preview placement + widescreen kind now lives in the
    # toolkit-free presentation model (``ui.presentation.lcd_panel``), shared
    # with the qtgui preview.  See ``lcd_panel_for``.

    # Commands
    CMD_ROTATION_CHANGED = 1
    CMD_BRIGHTNESS_CHANGED = 2
    CMD_SEND_TO_LCD = 3
    CMD_VIDEO_PLAY_PAUSE = 10
    CMD_VIDEO_SEEK = 11
    CMD_VIDEO_FIT_WIDTH = 12
    CMD_VIDEO_FIT_HEIGHT = 13

    # Signals
    image_clicked = Signal(int, int)
    element_drag_start = Signal(int, int)  # LCD-scaled (x, y)
    element_drag_move = Signal(int, int)   # LCD-scaled (x, y)
    element_drag_end = Signal()
    element_nudge = Signal(int, int)       # LCD-scaled (dx, dy)

    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent, width=Sizes.PREVIEW_FRAME, height=Sizes.PREVIEW_PANEL_H)

        self._lcd_width = width
        self._lcd_height = height
        self._offset_info = lcd_panel_for((width, height)).offset_info

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(5)

        # Frame container (500x500) with background image
        self.frame_container = QFrame()
        self.frame_container.setFixedSize(Sizes.PREVIEW_FRAME, Sizes.PREVIEW_FRAME)

        left, top, w, h, frame_name = self._offset_info

        set_background_pixmap(self.frame_container, frame_name,
            Sizes.PREVIEW_FRAME, Sizes.PREVIEW_FRAME,
            fallback_style=f"background-color: {Colors.BASE_BG};")

        # Preview label positioned inside frame at the LCD area
        self.preview_label = ImageLabel(w, h)
        self.preview_label.setParent(self.frame_container)
        self.preview_label.move(left, top)
        self.preview_label.clicked.connect(self._on_preview_clicked)
        self.preview_label.drag_started.connect(self._on_drag_started)
        self.preview_label.drag_moved.connect(self._on_drag_moved)
        self.preview_label.drag_ended.connect(self.element_drag_end.emit)
        self.preview_label.nudge.connect(self._on_nudge)

        layout.addWidget(self.frame_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(Styles.STATUS_LABEL)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Video progress bar container (hidden by default)
        self.progress_container = QFrame()
        self.progress_container.setFixedSize(Sizes.VIDEO_CONTROLS_W, Sizes.VIDEO_CONTROLS_H)
        self.progress_container.setVisible(False)

        set_background_pixmap(self.progress_container, Assets.VIDEO_CONTROLS_BG,
                              Sizes.VIDEO_CONTROLS_W, Sizes.VIDEO_CONTROLS_H)

        # Video control buttons
        self.play_btn = self._make_video_btn(
            Layout.PLAY_BTN, Assets.ICON_PLAY, "▶",
            "Play / Pause", self._on_play_pause)
        # Store pause icon for toggling
        pause_pix = Assets.load_pixmap(Assets.ICON_PAUSE, Layout.PLAY_BTN[2], Layout.PLAY_BTN[3])
        play_pix = Assets.load_pixmap(Assets.ICON_PLAY, Layout.PLAY_BTN[2], Layout.PLAY_BTN[3])
        self.play_btn._img_refs = [play_pix, pause_pix]  # type: ignore[attr-defined]

        self.height_fit_btn = self._make_video_btn(
            Layout.HEIGHT_FIT_BTN, 'display_mode_fit_height', "H",
            "Height fit (letterbox/crop)", self._on_height_fit)
        self.width_fit_btn = self._make_video_btn(
            Layout.WIDTH_FIT_BTN, 'display_mode_fit_width', "W",
            "Width fit (letterbox/crop)", self._on_width_fit)

        # Time label
        self.time_label = QLabel("00:00 / 00:00", self.progress_container)
        self.time_label.setGeometry(*Layout.TIME_LABEL)
        self.time_label.setStyleSheet(Styles.STATUS_LABEL_OVER_IMAGE)

        # Progress slider
        self.progress_slider = QSlider(Qt.Orientation.Horizontal, self.progress_container)
        self.progress_slider.setGeometry(*Layout.PROGRESS_SLIDER)
        self.progress_slider.setRange(0, 100)
        self.progress_slider.setStyleSheet(Styles.SLIDER)
        self.progress_slider.sliderMoved.connect(self._on_seek)

        layout.addWidget(self.progress_container)

    def _make_video_btn(self, rect, icon_name, fallback, tooltip, handler):
        """Create a flat video-control button."""
        btn = QPushButton(self.progress_container)
        btn.setGeometry(*rect)
        pix = Assets.load_pixmap(icon_name, rect[2], rect[3])
        if not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(btn.size())
        else:
            btn.setText(fallback)
        btn.setFlat(True)
        btn.setStyleSheet(Styles.FLAT_BUTTON)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.clicked.connect(handler)
        return btn

    def _widget_to_lcd(self, wx: int, wy: int) -> tuple[int, int]:
        """Translate preview widget coordinates to LCD coordinates."""
        _, _, pw, ph, _ = self._offset_info
        if pw <= 0 or ph <= 0:
            return (0, 0)
        lx = int(wx * self._lcd_width / pw)
        ly = int(wy * self._lcd_height / ph)
        return (max(0, min(lx, self._lcd_width)), max(0, min(ly, self._lcd_height)))

    def _on_drag_started(self, wx: int, wy: int):
        lx, ly = self._widget_to_lcd(wx, wy)
        log.debug("_on_drag_started: widget=(%s,%s) lcd=(%s,%s)", wx, wy, lx, ly)
        self.element_drag_start.emit(lx, ly)

    def _on_drag_moved(self, wx: int, wy: int):
        lx, ly = self._widget_to_lcd(wx, wy)
        log.debug("_on_drag_moved: widget=(%s,%s) lcd=(%s,%s)", wx, wy, lx, ly)
        self.element_drag_move.emit(lx, ly)

    def _on_nudge(self, dx: int, dy: int):
        """Forward keyboard nudge as LCD-scaled delta."""
        log.debug("_on_nudge: dx=%s dy=%s", dx, dy)
        _, _, pw, ph, _ = self._offset_info
        if pw <= 0 or ph <= 0:
            return
        lcd_dx = int(dx * self._lcd_width / pw) if dx else 0
        lcd_dy = int(dy * self._lcd_height / ph) if dy else 0
        # Ensure at least 1px nudge in LCD coords
        if dx and not lcd_dx:
            lcd_dx = 1 if dx > 0 else -1
        if dy and not lcd_dy:
            lcd_dy = 1 if dy > 0 else -1
        self.element_nudge.emit(lcd_dx, lcd_dy)

    def _on_preview_clicked(self):
        log.debug("_on_preview_clicked")
        self.image_clicked.emit(0, 0)

    def _on_play_pause(self):
        log.debug("_on_play_pause")
        self.invoke_delegate(self.CMD_VIDEO_PLAY_PAUSE)

    def _on_height_fit(self):
        log.debug("_on_height_fit")
        self.invoke_delegate(self.CMD_VIDEO_FIT_HEIGHT)

    def _on_width_fit(self):
        log.debug("_on_width_fit")
        self.invoke_delegate(self.CMD_VIDEO_FIT_WIDTH)

    def _on_seek(self, value):
        log.debug("_on_seek: value=%s", value)
        self.invoke_delegate(self.CMD_VIDEO_SEEK, value)

    def set_image(self, image, fast: bool = False):
        """Set preview image (QImage)."""
        if image is not None:
            iw = image.width() if hasattr(image, 'width') else '?'
            ih = image.height() if hasattr(image, 'height') else '?'
            log.debug("preview.set_image: %sx%s → widget %dx%d",
                      iw, ih, self.preview_label._width, self.preview_label._height)
        self.preview_label.set_image(image, fast=fast)

    def set_status(self, text):
        self.status_label.setText(text)

    def show_video_controls(self, show=True):
        self.progress_container.setVisible(show)

    def set_playing(self, playing):
        refs = getattr(self.play_btn, '_img_refs', None)
        if refs and len(refs) >= 2 and refs[0] and refs[1]:
            icon = QIcon(refs[1] if playing else refs[0])
            self.play_btn.setIcon(icon)
        else:
            self.play_btn.setText("⏸" if playing else "▶")

    def set_progress(self, percent, current_time, total_time):
        self.progress_slider.blockSignals(True)
        self.progress_slider.setValue(int(percent))
        self.progress_slider.blockSignals(False)
        self.time_label.setText(f"{current_time} / {total_time}")

    def set_frame_image(self, pixmap_or_path):
        if isinstance(pixmap_or_path, str):
            set_background_pixmap(self.frame_container, pixmap_or_path,
                                  Sizes.PREVIEW_FRAME, Sizes.PREVIEW_FRAME)
        else:
            set_background_pixmap(self.frame_container, pixmap_or_path)

    def set_resolution(self, width, height):
        self._lcd_width = width
        self._lcd_height = height
        self._offset_info = lcd_panel_for((width, height)).offset_info

        left, top, w, h, frame_name = self._offset_info
        # On-change (not per-frame) — INFO so the chosen bezel + LCD-area
        # placement is visible at the default level: this is the preview that
        # must match the device orientation.
        log.info(
            "preview.set_resolution: lcd=%dx%d → bezel=%s area=%dx%d@(%d,%d)",
            width, height, frame_name, w, h, left, top,
        )
        self.preview_label.setFixedSize(w, h)
        self.preview_label._width = w
        self.preview_label._height = h
        self.preview_label.move(left, top)
        self.set_frame_image(frame_name)

    def get_lcd_size(self):
        return (self._lcd_width, self._lcd_height)
