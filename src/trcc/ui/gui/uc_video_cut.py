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
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QProgressBar, QWidget

from ...core import toolchain
from ...core.logs import per_frame
from ...core.models import (
    CUTTER_DEFAULT_FPS,
    FitMode,
    VideoExportRequest,
    panel_asset_dims,
)
from ...core.models import SUBPROCESS_NO_WINDOW as _NO_WINDOW
from ...core.models import (
    ZT_MAX_DURATION_MS as MAX_DURATION_MS,
)
from ..presentation.clip_preview import ClipPreview
from .assets import Assets
from .base import make_icon_button

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

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

# How often a pending still is checked for -- the C#'s 15 ms Form1 timer.
STILL_POLL_MS = 15

# Frame-rate choice: the C#'s button1 / button2 (UCVideoCut.cs:2936-2962),
# either side of the low-to-high wedge the background draws beside "FPS".
BTN_FPS = {15: (40, 661, 14, 14), 24: (110, 661, 14, 14)}

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
        export_requested(int, int, int, object, int): (start_ms, end_ms,
            rotation, fit_mode, fps) — the window turns this into
            ``ExportVideoClip`` for the active device.  The panel does not
            know the device key or the canvas size, and does not need to:
            the Command resolves both.  ``fit_mode`` is a
            :class:`~trcc.core.models.FitMode` or ``None`` for auto, which
            is why the signal carries ``object`` — a ``str`` slot could not
            express "the user never pressed a fit button".
        video_cut_done(str): Emitted with Theme.zt path on export, or '' on cancel.
    """

    export_requested = Signal(int, int, int, object, int)
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
        # Which edge the user forced, or None while they have not pressed
        # W or H.  TRI-state on purpose: this was a bool defaulting to True,
        # so wiring it straight through would have silently switched every
        # export to forced-width.  None is the auto arm — fit inside, never
        # crop — which is what an untouched export has always done (#291).
        self._fit_mode: FitMode | None = None
        # The clip's frame rate, 15 or 24 -- the C#'s originalImageHz.
        self._clip_fps = CUTTER_DEFAULT_FPS

        # Timeline handles (pixel x positions)
        self._start_x = TIMELINE_X
        self._end_x = TIMELINE_X + TIMELINE_W
        self._start_ms = 0
        self._end_ms = 0
        self._dragging = None  # 'start' or 'end'

        # Preview state.  ClipPreview owns the ffmpeg; the timer only picks
        # up what it has written -- frames while playing, else one still.
        self._preview_pixmap = None
        self._clip = ClipPreview()
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._preview_tick)
        self._previewing = False

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

        # Frame-rate choice (15 / 24), one checked at a time
        self._fps_btns = {
            fps: make_icon_button(self, rect, 'shared_checkbox_off.png',
                                  str(fps), partial(self._on_fps, fps))
            for fps, rect in BTN_FPS.items()
        }
        self._show_fps()

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
        self._video_path = str(path)

        # Get metadata with ffprobe
        try:
            result = subprocess.run([
                toolchain.executable('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
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

        self._clip.load(Path(self._video_path))

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

        # Load resolution-specific background (C# scaled dims, not raw LCD dims)
        pw, ph = panel_asset_dims(w, h)
        bg_name = f'video_cut_{pw}x{ph}.png'
        log.debug("set_resolution: %dx%d → panel %dx%d asset=%s", w, h, pw, ph, bg_name)
        bg_pix = Assets.load_pixmap(bg_name, PANEL_W, PANEL_H)
        if not bg_pix.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(bg_pix))
            self.setPalette(palette)

    def _request(self, start_ms, end_ms):
        """The clip as Export would encode it -- what the preview shows."""
        frame_log.debug("_request: %s-%s ms", start_ms, end_ms)
        return VideoExportRequest(
            source=Path(self._video_path or ""), start_ms=int(start_ms),
            end_ms=int(end_ms), target_w=self._target_w,
            target_h=self._target_h, rotation=self._rotation,
            fit_mode=self._fit_mode, fps=self._clip_fps)

    def _seek_and_show(self, ms):
        """Show the frame at *ms*, composed exactly as Export will encode it.

        Grabbed in the background by :class:`ClipPreview`, which keeps only
        the newest request while one is in flight.  This used to run ffmpeg
        here, on the GUI thread, for every mouse-move and every 41 ms tick.
        """
        if not self._video_path:
            return
        log.debug("_seek_and_show: ms=%s", ms)
        if self._previewing:
            self._stop_preview()
        self._lbl_current.setText(_format_time(ms))
        self._clip.still(self._request(ms, ms + 1), (PREVIEW_W, PREVIEW_H))
        if not self._preview_timer.isActive():
            self._preview_timer.start(STILL_POLL_MS)

    def _show_jpeg(self, data):
        """Put one preview frame (already box-sized by ffmpeg) on screen."""
        img = QImage.fromData(data)
        if img.isNull():
            log.debug("_show_jpeg: %d bytes did not decode", len(data))
            return
        self._preview_pixmap = QPixmap.fromImage(img)
        self.update()

    # =========================================================================
    # Frame rate, fit mode and rotation
    # =========================================================================

    def _on_fps(self, fps, _checked=False):
        """A frame-rate button: the C#'s button1_Click / button2_Click."""
        log.info("_on_fps: %s -> %s", self._clip_fps, fps)
        self._clip_fps = fps
        self._show_fps()

    def _show_fps(self):
        """Check the chosen frame rate's box, clear the other."""
        log.debug("_show_fps: %s", self._clip_fps)
        for fps, btn in self._fps_btns.items():
            name = ('shared_checkbox_on.png' if fps == self._clip_fps
                    else 'shared_checkbox_off.png')
            pix = Assets.load_pixmap(name, *BTN_FPS[fps][2:])
            if not pix.isNull():
                btn.setIcon(QIcon(pix))

    def _on_width_fit(self):
        log.info("_on_width_fit: fit_mode %s -> WIDTH", self._fit_mode)
        self._fit_mode = FitMode.WIDTH
        self._seek_and_show(self._start_ms)

    def _on_height_fit(self):
        log.info("_on_height_fit: fit_mode %s -> HEIGHT", self._fit_mode)
        self._fit_mode = FitMode.HEIGHT
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
        """Decode the trimmed range once, in the background, and play it.

        The C#'s buttonYulan_Click: one ffmpeg at the chosen rate; the timer
        shows what it has written so far, then loops.
        """
        if not self._video_path:
            return
        log.info("_start_preview: %s-%s ms at %d fps", self._start_ms,
                 self._end_ms, self._clip_fps)
        self._previewing = True
        self._clip.play(self._request(self._start_ms, self._end_ms),
                        (PREVIEW_W, PREVIEW_H))
        self._preview_timer.start(int(1000 / self._clip_fps))

    def _stop_preview(self):
        log.debug("_stop_preview: previewing=%s", self._previewing)
        self._previewing = False
        self._preview_timer.stop()
        self._clip.stop()

    def _preview_tick(self):
        """Show what ClipPreview has ready: a playing frame or a still."""
        data = self._clip.poll()
        frame_log.debug("_preview_tick: got=%s previewing=%s",
                        data is not None, self._previewing)
        if data is not None:
            self._show_jpeg(data)
            if self._previewing:
                self._lbl_current.setText(_format_time(
                    self._start_ms + self._clip.shown * 1000 / self._clip_fps))
        if not self._previewing and not self._clip.active:
            self._preview_timer.stop()

    # =========================================================================
    # Export
    # =========================================================================

    def _on_export(self):
        """Ask the window to encode the current clip.  Does not encode."""
        log.info("_on_export: video_path=%s start=%s end=%s rotation=%s "
                 "fps=%s", self._video_path, self._start_ms, self._end_ms,
                 self._rotation, self._clip_fps)
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
            self._start_ms, self._end_ms, self._rotation, self._fit_mode,
            self._clip_fps)

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
        log.debug("_cleanup_video: path=%s", self._video_path)
        self._preview_timer.stop()
        self._clip.stop()
        self._video_path = None

    def closeEvent(self, event):
        self._stop_preview()
        self._cleanup_video()
        # A running export is NOT killed: it belongs to the app, not to
        # this panel, and under TRCC_DAEMON=1 it is not even this
        # process's to terminate.
        event.accept()
