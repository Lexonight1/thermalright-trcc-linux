"""
PyQt6 UCVideoCut - Video trimmer panel.

Matches Windows TRCC UCVideoCut functionality (500x702).
Provides timeline scrubber with in/out handles, fit modes, rotation,
and Theme.zt export.
"""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QLabel, QProgressBar, QWidget

from .assets import load_pixmap
from .base import make_icon_button

# Try OpenCV for preview
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# Try PIL for JPEG conversion
try:
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ============================================================================
# Constants
# ============================================================================

PANEL_W, PANEL_H = 500, 702
PREVIEW_X, PREVIEW_Y = 10, 10
PREVIEW_W, PREVIEW_H = 480, 500

TIMELINE_X, TIMELINE_Y = 9, 564
TIMELINE_W, TIMELINE_H = 480, 20

HANDLE_W, HANDLE_H = 15, 20

MAX_DURATION_MS = 300000  # 5 minutes
EXPORT_FPS = 24
FRAME_INTERVAL_MS = 1000.0 / EXPORT_FPS  # ~41.67ms

# Button positions (y=656 row)
BTN_HEIGHT_FIT = (169, 656, 34, 26)
BTN_WIDTH_FIT = (233, 656, 34, 26)
BTN_ROTATE = (297, 656, 34, 26)
BTN_EXPORT = (446, 656, 34, 26)

# Preview / Close buttons
BTN_PREVIEW = (233, 513, 34, 20)
BTN_CLOSE = (474, 510, 16, 16)

# Time labels
LABEL_CURRENT = (32, 531, 150, 16)
LABEL_DURATION = (370, 531, 120, 16)
LABEL_START = (32, 597, 150, 16)
LABEL_END = (370, 597, 120, 16)
LABEL_INFO = (106, 582, 280, 16)

# Progress bar
PROGRESS_RECT = (8, 565, 480, 10)


def _format_time(ms):
    """Format milliseconds as HH:MM:SS."""
    s = max(0, int(ms / 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ============================================================================
# Export worker thread
# ============================================================================

class ExportWorker(QThread):
    """Background thread for FFmpeg frame extraction + Theme.zt assembly."""

    progress = pyqtSignal(int, str)  # percent, message
    finished = pyqtSignal(str)       # output path (empty on error)
    error = pyqtSignal(str)

    def __init__(self, video_path, start_ms, end_ms, target_w, target_h,
                 rotation, width_fit):
        super().__init__()
        self.video_path = str(video_path)
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.target_w = target_w
        self.target_h = target_h
        self.rotation = rotation
        self.width_fit = width_fit

    def run(self):
        try:
            self._do_export()
        except Exception as e:
            self.error.emit(str(e))

    def _do_export(self):
        temp_dir = tempfile.mkdtemp(prefix='trcc_videocut_')
        frames_dir = os.path.join(temp_dir, 'frames')
        os.makedirs(frames_dir, exist_ok=True)

        duration_ms = self.end_ms - self.start_ms
        start_s = self.start_ms / 1000.0
        duration_s = duration_ms / 1000.0

        # Build FFmpeg command
        vf_filters = []
        if self.rotation == 90:
            vf_filters.append('transpose=1')
        elif self.rotation == 180:
            vf_filters.append('transpose=1,transpose=1')
        elif self.rotation == 270:
            vf_filters.append('transpose=2')

        cmd = [
            'ffmpeg', '-ss', str(start_s), '-t', str(duration_s),
            '-i', self.video_path, '-y',
            '-r', str(EXPORT_FPS),
            '-s', f'{self.target_w}x{self.target_h}',
        ]
        if vf_filters:
            cmd.extend(['-vf', ','.join(vf_filters)])
        cmd.extend(['-f', 'image2', os.path.join(frames_dir, '%04d.bmp')])

        self.progress.emit(5, "Extracting frames...")
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            self.error.emit(f"FFmpeg error: {result.stderr.decode()[:200]}")
            return

        # Convert BMPs to JPEG
        bmp_files = sorted(
            f for f in os.listdir(frames_dir) if f.endswith('.bmp')
        )
        if not bmp_files:
            self.error.emit("No frames extracted")
            return

        total = len(bmp_files)
        self.progress.emit(20, f"Converting {total} frames...")
        jpeg_data_list = []

        for i, bmp_name in enumerate(bmp_files):
            bmp_path = os.path.join(frames_dir, bmp_name)
            if PIL_AVAILABLE:
                img = PILImage.open(bmp_path)
                from io import BytesIO
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=85)
                jpeg_data_list.append(buf.getvalue())
            else:
                # Fallback: read raw BMP (less efficient)
                with open(bmp_path, 'rb') as f:
                    jpeg_data_list.append(f.read())

            os.remove(bmp_path)
            pct = 20 + int(60 * (i + 1) / total)
            if i % 10 == 0:
                self.progress.emit(pct, f"Converting {i+1}/{total}...")

        # Write Theme.zt
        self.progress.emit(85, "Writing Theme.zt...")
        output_path = os.path.join(temp_dir, 'Theme.zt')
        frame_count = len(jpeg_data_list)

        with open(output_path, 'wb') as f:
            # Magic byte
            f.write(struct.pack('B', 0xDC))
            # Frame count
            f.write(struct.pack('<i', frame_count))
            # Timestamps (41.67ms intervals)
            for i in range(frame_count):
                ts = int(i * FRAME_INTERVAL_MS)
                f.write(struct.pack('<i', ts))
            # Frame data
            for jpeg_bytes in jpeg_data_list:
                f.write(struct.pack('<i', len(jpeg_bytes)))
                f.write(jpeg_bytes)

        self.progress.emit(100, "Done!")
        self.finished.emit(output_path)

        # Clean up frames dir (Theme.zt stays)
        try:
            os.rmdir(frames_dir)
        except OSError:
            pass


# ============================================================================
# Main video cut widget
# ============================================================================

class UCVideoCut(QWidget):
    """Video trimmer panel (500x702).

    Shows video preview, timeline with in/out handles,
    fit mode buttons, rotation, and Theme.zt export.

    Signals:
        video_cut_done(str): Emitted with Theme.zt path on export, or '' on cancel.
    """

    video_cut_done = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PANEL_W, PANEL_H)

        # State
        self._video_path = None
        self._video_cap = None
        self._total_frames = 0
        self._fps = 30.0
        self._duration_ms = 0
        self._target_w = 320
        self._target_h = 320
        self._rotation = 0
        self._width_fit = True

        # Timeline handles (pixel x positions)
        self._start_x = TIMELINE_X
        self._end_x = TIMELINE_X + TIMELINE_W
        self._start_ms = 0
        self._end_ms = 0
        self._dragging = None  # 'start' or 'end'

        # Preview state
        self._preview_pixmap = None
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._preview_tick)
        self._previewing = False
        self._preview_pos_ms = 0

        # Export state
        self._export_worker = None
        self._is_processing = False

        # Dark background via palette
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#232227'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self._setup_ui()

    def _setup_ui(self):
        """Build the video cut UI."""
        # Time labels
        self._lbl_current = QLabel("00:00:00", self)
        self._lbl_current.setGeometry(*LABEL_CURRENT)
        self._lbl_current.setStyleSheet("color: #00FF00; font-size: 9pt; background: transparent;")

        self._lbl_duration = QLabel("00:00:00", self)
        self._lbl_duration.setGeometry(*LABEL_DURATION)
        self._lbl_duration.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lbl_duration.setStyleSheet("color: #CCCCCC; font-size: 9pt; background: transparent;")

        self._lbl_start = QLabel("00:00:00", self)
        self._lbl_start.setGeometry(*LABEL_START)
        self._lbl_start.setStyleSheet("color: #00AA00; font-size: 9pt; background: transparent;")

        self._lbl_end = QLabel("00:00:00", self)
        self._lbl_end.setGeometry(*LABEL_END)
        self._lbl_end.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lbl_end.setStyleSheet("color: #AA0000; font-size: 9pt; background: transparent;")

        self._lbl_info = QLabel("", self)
        self._lbl_info.setGeometry(*LABEL_INFO)
        self._lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_info.setStyleSheet("color: #888; font-size: 9pt; background: transparent;")
        self._lbl_info.setVisible(False)

        # Progress bar
        self._progress = QProgressBar(self)
        self._progress.setGeometry(*PROGRESS_RECT)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { background: #333; border: none; }"
            "QProgressBar::chunk { background: #4488FF; }"
        )
        self._progress.setVisible(False)

        # Fit mode buttons
        self._btn_height_fit = make_icon_button(
            self, BTN_HEIGHT_FIT, 'P高度适应.png', "H", self._on_height_fit)
        self._btn_width_fit = make_icon_button(
            self, BTN_WIDTH_FIT, 'P宽度适应.png', "W", self._on_width_fit)
        self._btn_rotate = make_icon_button(
            self, BTN_ROTATE, 'P旋转.png', "R", self._on_rotate)
        self._btn_export = make_icon_button(
            self, BTN_EXPORT, 'P裁减.png', "OK", self._on_export)

        # Preview button
        self._btn_preview = make_icon_button(
            self, BTN_PREVIEW, 'P0预览.png', "\u25b6", self._on_preview_toggle)

        # Close button
        self._btn_close = make_icon_button(
            self, BTN_CLOSE, 'P关闭按钮.png', "\u2715", self._on_close)

    # =========================================================================
    # Painting
    # =========================================================================

    def paintEvent(self, event):
        """Custom paint: preview area, timeline, handles."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Preview area background
        p.setPen(QPen(QColor('#444'), 1))
        p.setBrush(QBrush(QColor('#000000')))
        p.drawRect(PREVIEW_X, PREVIEW_Y, PREVIEW_W, PREVIEW_H)

        # Draw preview frame
        if self._preview_pixmap and not self._preview_pixmap.isNull():
            px = self._preview_pixmap
            # Center in preview area
            x = PREVIEW_X + (PREVIEW_W - px.width()) // 2
            y = PREVIEW_Y + (PREVIEW_H - px.height()) // 2
            p.drawPixmap(x, y, px)

        # Timeline background
        p.setPen(QPen(QColor('#555'), 1))
        p.setBrush(QBrush(QColor('#333')))
        p.drawRect(TIMELINE_X, TIMELINE_Y, TIMELINE_W, TIMELINE_H)

        # Selected range (green bar between handles)
        if self._duration_ms > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor('#004400')))
            p.drawRect(
                self._start_x, TIMELINE_Y,
                self._end_x - self._start_x, TIMELINE_H
            )

        # Start handle (green)
        p.setPen(QPen(QColor('#00FF00'), 1))
        p.setBrush(QBrush(QColor('#00AA00')))
        p.drawRect(self._start_x, TIMELINE_Y, HANDLE_W, HANDLE_H)

        # End handle (red)
        p.setPen(QPen(QColor('#FF0000'), 1))
        p.setBrush(QBrush(QColor('#AA0000')))
        p.drawRect(self._end_x - HANDLE_W, TIMELINE_Y, HANDLE_W, HANDLE_H)

        p.end()

    # =========================================================================
    # Mouse interaction (timeline handles)
    # =========================================================================

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = event.position().x(), event.position().y()

        # Check if click is on timeline area
        if not (TIMELINE_Y <= y <= TIMELINE_Y + TIMELINE_H):
            return

        # Check start handle
        if self._start_x <= x <= self._start_x + HANDLE_W:
            self._dragging = 'start'
        # Check end handle
        elif self._end_x - HANDLE_W <= x <= self._end_x:
            self._dragging = 'end'
        # Click on timeline — seek
        elif TIMELINE_X <= x <= TIMELINE_X + TIMELINE_W:
            ms = self._x_to_ms(x)
            self._seek_and_show(ms)

    def mouseMoveEvent(self, event):
        if not self._dragging or self._duration_ms <= 0:
            return
        x = event.position().x()
        x = max(TIMELINE_X, min(TIMELINE_X + TIMELINE_W, x))

        if self._dragging == 'start':
            self._start_x = min(x, self._end_x - HANDLE_W * 2)
            self._start_ms = self._x_to_ms(self._start_x)
            # Enforce max duration
            if self._end_ms - self._start_ms > MAX_DURATION_MS:
                self._end_ms = self._start_ms + MAX_DURATION_MS
                self._end_x = self._ms_to_x(self._end_ms)
            self._lbl_start.setText(_format_time(self._start_ms))
            self._seek_and_show(self._start_ms)

        elif self._dragging == 'end':
            self._end_x = max(x, self._start_x + HANDLE_W * 2)
            self._end_ms = self._x_to_ms(self._end_x)
            # Enforce max duration
            if self._end_ms - self._start_ms > MAX_DURATION_MS:
                self._start_ms = self._end_ms - MAX_DURATION_MS
                self._start_x = self._ms_to_x(self._start_ms)
            self._lbl_end.setText(_format_time(self._end_ms))
            self._seek_and_show(self._end_ms)

        self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = None

    def _x_to_ms(self, x):
        """Convert pixel x to milliseconds."""
        if TIMELINE_W <= 0:
            return 0
        frac = (x - TIMELINE_X) / TIMELINE_W
        return max(0, min(self._duration_ms, frac * self._duration_ms))

    def _ms_to_x(self, ms):
        """Convert milliseconds to pixel x."""
        if self._duration_ms <= 0:
            return TIMELINE_X
        frac = ms / self._duration_ms
        return TIMELINE_X + frac * TIMELINE_W

    # =========================================================================
    # Video loading and preview
    # =========================================================================

    def load_video(self, path):
        """Load a video file for trimming."""
        if not CV2_AVAILABLE:
            self._lbl_info.setText("OpenCV not available")
            self._lbl_info.setVisible(True)
            return

        self._video_path = str(path)
        self._video_cap = cv2.VideoCapture(self._video_path)
        if not self._video_cap.isOpened():
            self._lbl_info.setText("Failed to open video")
            self._lbl_info.setVisible(True)
            return

        self._total_frames = int(self._video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._fps = self._video_cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._duration_ms = (self._total_frames / self._fps) * 1000

        # Reset handles
        self._start_x = TIMELINE_X
        self._end_x = TIMELINE_X + TIMELINE_W
        self._start_ms = 0
        self._end_ms = min(self._duration_ms, MAX_DURATION_MS)
        if self._end_ms < self._duration_ms:
            self._end_x = self._ms_to_x(self._end_ms)

        # Update labels
        self._lbl_duration.setText(_format_time(self._duration_ms))
        self._lbl_start.setText(_format_time(self._start_ms))
        self._lbl_end.setText(_format_time(self._end_ms))

        # Show first frame
        self._seek_and_show(0)
        self.update()

    def set_resolution(self, w, h):
        """Set target LCD resolution for export."""
        self._target_w = w
        self._target_h = h

        # Try to load resolution-specific background
        bg_name = f'P0裁减{w}{h}.png'
        bg_pix = load_pixmap(bg_name, PANEL_W, PANEL_H)
        if not bg_pix.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(bg_pix))
            self.setPalette(palette)

    def _seek_and_show(self, ms):
        """Seek to time and display frame."""
        if not self._video_cap or not self._video_cap.isOpened():
            return
        frame_num = int(ms / 1000.0 * self._fps)
        frame_num = max(0, min(self._total_frames - 1, frame_num))
        self._video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = self._video_cap.read()
        if not ret:
            return

        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Apply rotation
        if self._rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self._rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        # Scale to fit preview
        h, w = frame.shape[:2]
        scale = min(PREVIEW_W / w, PREVIEW_H / h)
        new_w, new_h = int(w * scale), int(h * scale)
        if new_w > 0 and new_h > 0:
            frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Convert to QPixmap
        from PyQt6.QtGui import QImage
        h, w, ch = frame.shape
        bytes_per_line = ch * w
        qimg = QImage(bytes(frame.data), w, h, bytes_per_line, QImage.Format.Format_RGB888)
        self._preview_pixmap = QPixmap.fromImage(qimg)

        self._lbl_current.setText(_format_time(ms))
        self.update()

    # =========================================================================
    # Fit mode and rotation
    # =========================================================================

    def _on_width_fit(self):
        self._width_fit = True
        self._seek_and_show(self._start_ms)

    def _on_height_fit(self):
        self._width_fit = False
        self._seek_and_show(self._start_ms)

    def _on_rotate(self):
        self._rotation = (self._rotation + 90) % 360
        self._seek_and_show(self._start_ms)

    # =========================================================================
    # Preview playback
    # =========================================================================

    def _on_preview_toggle(self):
        if self._previewing:
            self._stop_preview()
        else:
            self._start_preview()

    def _start_preview(self):
        self._previewing = True
        self._preview_pos_ms = self._start_ms
        self._preview_timer.start(int(FRAME_INTERVAL_MS))

    def _stop_preview(self):
        self._previewing = False
        self._preview_timer.stop()

    def _preview_tick(self):
        if self._preview_pos_ms >= self._end_ms:
            self._preview_pos_ms = self._start_ms
        self._seek_and_show(self._preview_pos_ms)
        self._preview_pos_ms += FRAME_INTERVAL_MS

    # =========================================================================
    # Export
    # =========================================================================

    def _on_export(self):
        if self._is_processing or not self._video_path:
            return

        self._stop_preview()
        self._is_processing = True
        self._btn_export.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._lbl_info.setText("Starting export...")
        self._lbl_info.setVisible(True)

        self._export_worker = ExportWorker(
            self._video_path, self._start_ms, self._end_ms,
            self._target_w, self._target_h,
            self._rotation, self._width_fit
        )
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_progress(self, percent, message):
        self._progress.setValue(percent)
        self._lbl_info.setText(message)

    def _on_export_finished(self, output_path):
        self._is_processing = False
        self._btn_export.setEnabled(True)
        self._progress.setVisible(False)
        self._lbl_info.setVisible(False)
        self.video_cut_done.emit(output_path)

    def _on_export_error(self, message):
        self._is_processing = False
        self._btn_export.setEnabled(True)
        self._progress.setVisible(False)
        self._lbl_info.setText(f"Error: {message[:80]}")
        self._lbl_info.setVisible(True)

    def _on_close(self):
        self._stop_preview()
        self._cleanup_video()
        self.video_cut_done.emit('')

    # =========================================================================
    # Cleanup
    # =========================================================================

    def _cleanup_video(self):
        if self._video_cap:
            self._video_cap.release()
            self._video_cap = None

    def closeEvent(self, event):
        self._stop_preview()
        self._cleanup_video()
        if self._export_worker and self._export_worker.isRunning():
            self._export_worker.terminate()
        event.accept()
