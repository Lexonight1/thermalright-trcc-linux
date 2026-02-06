"""
PyQt6 UCPreview - Preview panel with frame.

Matches Windows TRCC.DCUserControl.UCScreenImageBK (500x500)
Contains the LCD preview with decorative frame.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QSlider, QVBoxLayout

from .assets import Assets, load_pixmap
from .base import BasePanel, ImageLabel, set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles

try:
    from PIL import Image  # noqa: F401
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class UCPreview(BasePanel):
    """
    Preview panel with frame image.

    Windows: UCScreenImageBK (500x500) contains UCScreenImage (varies by resolution)
    Frame image provides decorative border around the LCD preview.
    """

    # Resolution offsets (left, top, width, height, frame_image)
    RESOLUTION_OFFSETS = {
        (320, 320): (90, 90, 320, 320, 'P预览320X320.png'),
        (480, 480): (10, 10, 480, 480, 'P预览480X480.png'),
        (240, 240): (130, 130, 240, 240, 'P预览240X240.png'),
        (240, 320): (130, 90, 240, 320, 'P预览240X320.png'),
        (320, 240): (90, 130, 320, 240, 'P预览320X240.png'),
        (360, 360): (70, 70, 360, 360, 'P预览360360圆.png'),
        (240, 400): (130, 50, 240, 400, 'P预览240X400.png'),
        (400, 240): (50, 130, 400, 240, 'P预览400X240.png'),
        (180, 480): (160, 10, 180, 480, 'P预览180X480.png'),
        (480, 180): (10, 160, 480, 180, 'P预览480X180.png'),
        (270, 480): (115, 10, 270, 480, 'P预览270X480.png'),
        (480, 270): (10, 115, 480, 270, 'P预览480X270.png'),
    }

    DEFAULT_OFFSET = (90, 90, 320, 320, 'P预览320X320.png')

    # Commands
    CMD_ROTATION_CHANGED = 1
    CMD_BRIGHTNESS_CHANGED = 2
    CMD_SEND_TO_LCD = 3
    CMD_VIDEO_PLAY_PAUSE = 10
    CMD_VIDEO_SEEK = 11

    # Signals
    image_clicked = pyqtSignal(int, int)

    def __init__(self, width=320, height=320, parent=None):
        super().__init__(parent, width=Sizes.PREVIEW_FRAME, height=Sizes.PREVIEW_PANEL_H)

        self._lcd_width = width
        self._lcd_height = height
        self._offset_info = self.RESOLUTION_OFFSETS.get(
            (width, height), self.DEFAULT_OFFSET
        )

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

        layout.addWidget(self.frame_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(f"color: {Colors.STATUS_TEXT}; font-size: 11px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Video progress bar container (hidden by default)
        self.progress_container = QFrame()
        self.progress_container.setFixedSize(Sizes.VIDEO_CONTROLS_W, Sizes.VIDEO_CONTROLS_H)
        self.progress_container.setVisible(False)

        set_background_pixmap(self.progress_container, Assets.VIDEO_CONTROLS_BG,
                              Sizes.VIDEO_CONTROLS_W, Sizes.VIDEO_CONTROLS_H)

        # Play/Pause button
        self.play_btn = QPushButton(self.progress_container)
        self.play_btn.setGeometry(*Layout.PLAY_BTN)
        play_pix = load_pixmap(Assets.ICON_PLAY, Layout.PLAY_BTN[2], Layout.PLAY_BTN[3])
        pause_pix = load_pixmap(Assets.ICON_PAUSE, Layout.PLAY_BTN[2], Layout.PLAY_BTN[3])
        if not play_pix.isNull():
            self.play_btn.setIcon(QIcon(play_pix))
            self.play_btn.setIconSize(self.play_btn.size())
            self.play_btn._img_refs = [play_pix, pause_pix]  # type: ignore[attr-defined]
        else:
            self.play_btn.setText("▶")
        self.play_btn.setFlat(True)
        self.play_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self._on_play_pause)

        # Time label
        self.time_label = QLabel("00:00 / 00:00", self.progress_container)
        self.time_label.setGeometry(*Layout.TIME_LABEL)
        self.time_label.setStyleSheet(
            f"color: {Colors.STATUS_TEXT}; font-size: 10px; background: transparent;"
        )

        # Progress slider
        self.progress_slider = QSlider(Qt.Orientation.Horizontal, self.progress_container)
        self.progress_slider.setGeometry(*Layout.PROGRESS_SLIDER)
        self.progress_slider.setRange(0, 100)
        self.progress_slider.setStyleSheet(Styles.SLIDER)
        self.progress_slider.sliderMoved.connect(self._on_seek)

        layout.addWidget(self.progress_container)

    def _on_preview_clicked(self):
        self.image_clicked.emit(0, 0)

    def _on_play_pause(self):
        self.invoke_delegate(self.CMD_VIDEO_PLAY_PAUSE)

    def _on_seek(self, value):
        self.invoke_delegate(self.CMD_VIDEO_SEEK, value)

    def set_image(self, pil_image):
        """Set preview from PIL Image."""
        self.preview_label.set_pil_image(pil_image)

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
        self._offset_info = self.RESOLUTION_OFFSETS.get(
            (width, height), self.DEFAULT_OFFSET
        )

        left, top, w, h, frame_name = self._offset_info
        self.preview_label.setFixedSize(w, h)
        self.preview_label.move(left, top)
        self.set_frame_image(frame_name)

    def get_lcd_size(self):
        return (self._lcd_width, self._lcd_height)
