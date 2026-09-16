"""
PyQt6 UCVideoCut - Video trimmer panel.

Matches Windows TRCC UCVideoCut functionality (500x702).
Provides timeline scrubber with in/out handles, fit modes, rotation,
and Theme.zt export.

The encode is NOT done here.  This panel used to carry an
``ExportWorker`` QThread that hand-rolled the ffmpeg invocation and the
``.zt`` container writer — a second implementation of
``services/video_export.py``, which had already drifted from it (the
service passed no ``creationflags``, this one did) and which the
contract audit could not even see, because a reimplementation imports
nothing.  The panel now emits :attr:`export_requested` and the window
dispatches ``ExportVideoClip``; progress arrives back through
:meth:`set_export_progress`.
"""

from __future__ import annotations

import logging
import subprocess

from PySide6.QtCore import Qt, QTimer, Signal
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
from PySide6.QtWidgets import QLabel, QProgressBar, QWidget

from ...core.models import SUBPROCESS_NO_WINDOW as _NO_WINDOW
from ...core.models import (
    ZT_FRAME_INTERVAL_MS as FRAME_INTERVAL_MS,
)
from ...core.models import (
    ZT_MAX_DURATION_MS as MAX_DURATION_MS,
)
from ...core.models import panel_asset_dims
from .assets import Assets
from .base import make_icon_button

log = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

PANEL_W, PANEL_H = 500, 702
PREVIEW_X, PREVIEW_Y = 10, 10
PREVIEW_W, PREVIEW_H = 480, 500

TIMELINE_X, TIMELINE_Y = 9, 564
TIMELINE_W, TIMELINE_H = 480, 20

HANDLE_W, HANDLE_H = 15, 20

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
# Main video cut widget
# ============================================================================

class UCVideoCut(QWidget):
    """Video trimmer panel (500x702).

    Shows video preview, timeline with in/out handles,
    fit mode buttons, rotation, and Theme.zt export.

    Signals:
        export_requested(int, int, int, float, float, float, bool): (start_ms, end_ms, rotation, zoom, pan_x, pan_y, width_fit) — the
            window turns this into ``ExportVideoClip`` for the active
            device.  The panel does not know the device key or the canvas
            size, and does not need to: the Command resolves both.
        video_cut_done(str): Emitted with Theme.zt path on export, or '' on cancel.
    """

    export_requested = Signal(int, int, int, float, float, float, bool)
    video_cut_done = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(PANEL_W, PANEL_H)

        # State
        self._video_path = None
        self._total_frames = 0
        self._fps = 30.0
        self._duration_ms = 0
        self._target_w = 0
        self._target_h = 0
        self._rotation = 0
        self._width_fit = True
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5
        self._drag_last_x = 0
        self._drag_last_y = 0

        # Timeline handles (pixel x positions)
        self._start_x = TIMELINE_X
        self._end_x = TIMELINE_X + TIMELINE_W
        self._start_ms = 0
        self._end_ms = 0
        self._dragging = None  # 'start', 'end', or 'preview'

        # Preview state
        self._preview_pixmap = None
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._preview_tick)
        self._previewing = False
        self._preview_pos_ms = 0

        # Export state.  No worker: the encode belongs to the app, and
        # this panel only reflects its progress.
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
            self, BTN_HEIGHT_FIT, 'display_mode_fit_height.png', "H", self._on_height_fit)
        self._btn_width_fit = make_icon_button(
            self, BTN_WIDTH_FIT, 'display_mode_fit_width.png', "W", self._on_width_fit)
        self._btn_rotate = make_icon_button(
            self, BTN_ROTATE, 'display_mode_rotate.png', "R", self._on_rotate)
        self._btn_export = make_icon_button(
            self, BTN_EXPORT, 'display_mode_crop.png', "OK", self._on_export)

        # Preview button
        self._btn_preview = make_icon_button(
            self, BTN_PREVIEW, 'preview_btn.png', "\u25b6", self._on_preview_toggle)

        # Close button
        self._btn_close = make_icon_button(
            self, BTN_CLOSE, 'shared_close.png', "\u2715", self._on_close)

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
    # Mouse interaction (timeline handles, preview zoom & pan)
    # =========================================================================

    def wheelEvent(self, event):
        x, y = event.position().x(), event.position().y()
        if PREVIEW_X <= x <= PREVIEW_X + PREVIEW_W and PREVIEW_Y <= y <= PREVIEW_Y + PREVIEW_H:
            delta = event.angleDelta().y()
            if delta > 0:
                self._zoom = min(5.0, round(self._zoom + 0.15, 2))
            elif delta < 0:
                self._zoom = max(1.0, round(self._zoom - 0.15, 2))
            log.debug("wheelEvent: delta=%d zoom=%.2f", delta, self._zoom)
            self._seek_and_show(self._preview_pos_ms if self._previewing else self._start_ms)
            event.accept()
        else:
            log.debug("wheelEvent: ignoring outside preview")
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = event.position().x(), event.position().y()

        # Check if click is on preview area (pan / drag to position crop)
        if PREVIEW_X <= x <= PREVIEW_X + PREVIEW_W and PREVIEW_Y <= y <= PREVIEW_Y + PREVIEW_H:
            self._dragging = 'preview'
            self._drag_last_x = x
            self._drag_last_y = y
            return

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
        x, y = event.position().x(), event.position().y()

        if self._dragging == 'preview':
            dx = x - self._drag_last_x
            dy = y - self._drag_last_y
            self._drag_last_x = x
            self._drag_last_y = y
            sens = max(20.0, (self._zoom - 0.9) * 200.0)
            self._pan_x = max(0.0, min(1.0, self._pan_x - dx / sens))
            self._pan_y = max(0.0, min(1.0, self._pan_y - dy / sens))
            self._seek_and_show(self._preview_pos_ms if self._previewing else self._start_ms)
            return

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
        self._video_path = str(path)

        # Get metadata with ffprobe
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=r_frame_rate,nb_frames',
                '-show_entries', 'format=duration',
                '-of', 'csv=p=0',
                self._video_path,
            ], capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
            if result.returncode != 0:
                self._lbl_info.setText("Failed to open video")
                self._lbl_info.setVisible(True)
                return

            lines = result.stdout.strip().split('\n')
            # First line: r_frame_rate,nb_frames  (stream)
            # Second line: duration  (format)
            if lines:
                parts = lines[0].split(',')
                if parts:
                    fps_parts = parts[0].split('/')
                    if len(fps_parts) == 2 and fps_parts[1].strip() not in ('0', ''):
                        self._fps = float(fps_parts[0]) / float(fps_parts[1])
                    elif fps_parts[0].strip():
                        self._fps = float(fps_parts[0])
                if len(parts) >= 2:
                    try:
                        self._total_frames = int(parts[1])
                    except (ValueError, IndexError):
                        self._total_frames = 0

            # Get duration from format line (more reliable than nb_frames)
            duration_s = 0.0
            if len(lines) >= 2:
                try:
                    duration_s = float(lines[1].strip())
                except (ValueError, IndexError):
                    pass

            if duration_s > 0:
                self._duration_ms = duration_s * 1000
            elif self._total_frames > 0 and self._fps > 0:
                self._duration_ms = (self._total_frames / self._fps) * 1000
            else:
                self._lbl_info.setText("Cannot determine video duration")
                self._lbl_info.setVisible(True)
                return
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            log.debug("uc_video_cut: ffprobe duration probe failed: %s", e)
            self._lbl_info.setText("FFmpeg not available")
            self._lbl_info.setVisible(True)
            return

        # Reset handles
        self._start_x = TIMELINE_X
        self._end_x = TIMELINE_X + TIMELINE_W
        self._start_ms = 0
        self._end_ms = min(self._duration_ms, MAX_DURATION_MS)
        if self._end_ms < self._duration_ms:
            self._end_x = self._ms_to_x(self._end_ms)

        # Reset zoom and pan
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5

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

        # Load resolution-specific background (C# scaled dims, not raw LCD dims)
        pw, ph = panel_asset_dims(w, h)
        bg_name = f'video_cut_{pw}x{ph}.png'
        log.debug("set_resolution: %dx%d → panel %dx%d asset=%s", w, h, pw, ph, bg_name)
        bg_pix = Assets.load_pixmap(bg_name, PANEL_W, PANEL_H)
        if not bg_pix.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(bg_pix))
            self.setPalette(palette)

    def _seek_and_show(self, ms):
        """Seek to time and display frame using FFmpeg pipe."""
        if not self._video_path:
            return

        ss = ms / 1000.0
        try:
            result = subprocess.run([
                'ffmpeg', '-ss', str(ss), '-i', self._video_path,
                '-vframes', '1', '-f', 'image2pipe', '-vcodec', 'bmp',
                '-v', 'error', '-y', '-',
            ], capture_output=True, timeout=5, creationflags=_NO_WINDOW)
            if result.returncode != 0 or not result.stdout:
                return

            img = QImage.fromData(result.stdout)
            if img.isNull():
                return
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("uc_video_cut: frame extract via ffmpeg failed: %s", e)
            return

        # Apply rotation and scale to fit preview
        if self._rotation:
            img = img.transformed(QTransform().rotate(self._rotation))
        w, h = img.width(), img.height()
        if self._width_fit and self._zoom <= 1.0:
            scale = min(PREVIEW_W / w, PREVIEW_H / h)
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            img = img.scaled(new_w, new_h,
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        else:
            base_scale = max(PREVIEW_W / w, PREVIEW_H / h)
            scale = base_scale * self._zoom
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
            img = img.scaled(new_w, new_h,
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            max_cx = max(0, new_w - PREVIEW_W)
            max_cy = max(0, new_h - PREVIEW_H)
            cx = int(max_cx * self._pan_x)
            cy = int(max_cy * self._pan_y)
            img = img.copy(cx, cy, min(new_w, PREVIEW_W), min(new_h, PREVIEW_H))

        self._preview_pixmap = QPixmap.fromImage(img)
        self._lbl_current.setText(_format_time(ms))
        self.update()

    # =========================================================================
    # Fit mode and rotation
    # =========================================================================

    def _on_width_fit(self):
        log.debug("_on_width_fit: width_fit=True")
        self._width_fit = True
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5
        self._seek_and_show(self._start_ms)

    def _on_height_fit(self):
        log.debug("_on_height_fit: width_fit=False")
        self._width_fit = False
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5
        self._seek_and_show(self._start_ms)

    def _on_rotate(self):
        log.debug("_on_rotate: rotation=%s→%s", self._rotation, (self._rotation + 90) % 360)
        self._rotation = (self._rotation + 90) % 360
        self._seek_and_show(self._start_ms)

    # =========================================================================
    # Preview playback
    # =========================================================================

    def _on_preview_toggle(self):
        log.debug("_on_preview_toggle: previewing=%s→%s", self._previewing, not self._previewing)
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
        """Ask the window to encode the current clip.  Does not encode."""
        log.info("_on_export: video_path=%s start=%s end=%s rotation=%s zoom=%s",
                 self._video_path, self._start_ms, self._end_ms,
                 self._rotation, self._zoom)
        if self._is_processing or not self._video_path:
            log.debug("_on_export: busy=%s path=%s — ignored",
                      self._is_processing, self._video_path)
            return

        self._stop_preview()
        self._is_processing = True
        self._btn_export.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._lbl_info.setText("Starting export...")
        self._lbl_info.setVisible(True)
        self.export_requested.emit(
            self._start_ms, self._end_ms, self._rotation,
            self._zoom, self._pan_x, self._pan_y, self._width_fit,
        )

    def export_refused(self, message):
        """The window's dispatch was refused before anything was queued."""
        log.warning("export_refused: %s", message)
        self._is_processing = False
        self._btn_export.setEnabled(True)
        self._progress.setVisible(False)
        self._lbl_info.setText(message[:80])
        self._lbl_info.setVisible(True)

    def set_export_progress(self, percent, message):
        """One ``VideoExportProgress``, routed here by the window."""
        log.debug("set_export_progress: %s%% %s", percent, message)
        self._progress.setValue(percent)
        self._lbl_info.setText(message)

    def export_finished(self, ok, path, message):
        """Terminal ``VideoExportFinished``, routed here by the window.

        Emits ``video_cut_done`` on success only — the window's slot
        applies the produced ``.zt`` as the device background, and doing
        that with an empty path is how a failed export used to look
        exactly like a cancel.
        """
        log.info("export_finished: ok=%s path=%s message=%s",
                 ok, path, message)
        self._is_processing = False
        self._btn_export.setEnabled(True)
        self._progress.setVisible(False)
        if not ok:
            self._lbl_info.setText(f"Error: {message[:80]}")
            self._lbl_info.setVisible(True)
            return
        self._lbl_info.setVisible(False)
        self.video_cut_done.emit(path)

    def _on_close(self):
        log.debug("_on_close: emitting video_cut_done('')")
        self._stop_preview()
        self._cleanup_video()
        self.video_cut_done.emit('')

    # =========================================================================
    # Cleanup
    # =========================================================================

    def _cleanup_video(self):
        self._video_path = None

    def closeEvent(self, event):
        self._stop_preview()
        self._cleanup_video()
        # A running export is NOT killed: it belongs to the app, not to
        # this panel, and under TRCC_DAEMON=1 it is not even this
        # process's to terminate.
        event.accept()
