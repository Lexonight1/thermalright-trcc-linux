"""VideoCropDialog — choose start/end, preview, export to Theme.zt.

Wraps :class:`VideoExporter` in a modal :class:`QDialog`:

* Frame preview (single ffmpeg seek per scrub).
* Timeline with in/out handles + click-to-seek.
* Time labels (current / duration / clip start / clip end).
* Fit-width / fit-height / rotate / preview-play / export buttons.
* Progress bar while the export runs in a background QThread.

The dialog never blocks the GUI: ffmpeg work happens in
:class:`_ExportThread`, progress is wired to the bar via a Qt signal.

Successful exports leave the produced ``Theme.zt`` on disk; the caller
reads :meth:`output_path` to find it.  Cancel returns ``None``.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ...services.video_export import (
    EXPORT_FPS,
    MAX_DURATION_MS,
    VideoExporter,
    VideoExportError,
    VideoExportRequest,
    probe_duration_ms,
)

log = logging.getLogger(__name__)


_PREVIEW_W = 480
_PREVIEW_H = 300
_TIMELINE_W = 480
_TIMELINE_H = 22
_HANDLE_W = 12
_FRAME_INTERVAL_MS = int(1000 / EXPORT_FPS)


def _format_ms(ms: int) -> str:
    """``hh:mm:ss`` (we never display milliseconds — too fiddly to read)."""
    s = max(0, int(ms / 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class _TimelineBar(QWidget):
    """Painted timeline with two draggable handles + click-to-seek."""

    start_changed = Signal(int)  # ms
    end_changed = Signal(int)    # ms
    seek_requested = Signal(int)  # ms

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_TIMELINE_W, _TIMELINE_H)
        self.setMouseTracking(True)
        self._duration_ms = 0
        self._start_ms = 0
        self._end_ms = 0
        self._dragging: str | None = None

    # ── Public API ───────────────────────────────────────────────────

    def set_range(self, duration_ms: int) -> None:
        self._duration_ms = max(0, duration_ms)
        self._start_ms = 0
        self._end_ms = min(self._duration_ms, MAX_DURATION_MS)
        self.update()

    def set_start(self, start_ms: int) -> None:
        self._start_ms = max(0, min(start_ms, self._end_ms - 1))
        self.update()

    def set_end(self, end_ms: int) -> None:
        self._end_ms = max(self._start_ms + 1, min(end_ms, self._duration_ms))
        self.update()

    def clip_ms(self) -> tuple[int, int]:
        return self._start_ms, self._end_ms

    # ── Geometry ─────────────────────────────────────────────────────

    def _ms_to_x(self, ms: int) -> int:
        if self._duration_ms <= 0:
            return 0
        return int(ms / self._duration_ms * _TIMELINE_W)

    def _x_to_ms(self, x: int) -> int:
        if self._duration_ms <= 0:
            return 0
        x = max(0, min(_TIMELINE_W, x))
        return int(x / _TIMELINE_W * self._duration_ms)

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#555"), 1))
        p.setBrush(QBrush(QColor("#2a2a2a")))
        p.drawRect(0, 0, _TIMELINE_W - 1, _TIMELINE_H - 1)

        if self._duration_ms > 0:
            sx = self._ms_to_x(self._start_ms)
            ex = self._ms_to_x(self._end_ms)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(50, 130, 70, 160)))
            p.drawRect(sx, 1, max(1, ex - sx), _TIMELINE_H - 2)

            # Start handle
            p.setPen(QPen(QColor("#88ff88"), 1))
            p.setBrush(QBrush(QColor("#226633")))
            p.drawRect(sx, 0, _HANDLE_W, _TIMELINE_H - 1)
            # End handle
            p.setPen(QPen(QColor("#ff8888"), 1))
            p.setBrush(QBrush(QColor("#662222")))
            p.drawRect(ex - _HANDLE_W, 0, _HANDLE_W, _TIMELINE_H - 1)
        p.end()

    # ── Mouse ────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._duration_ms <= 0:
            return
        x = int(event.position().x())
        sx = self._ms_to_x(self._start_ms)
        ex = self._ms_to_x(self._end_ms)
        if sx <= x <= sx + _HANDLE_W:
            self._dragging = "start"
        elif ex - _HANDLE_W <= x <= ex:
            self._dragging = "end"
        else:
            self.seek_requested.emit(self._x_to_ms(x))

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging or self._duration_ms <= 0:
            return
        ms = self._x_to_ms(int(event.position().x()))
        if self._dragging == "start":
            self._start_ms = max(0, min(ms, self._end_ms - 1))
            if self._end_ms - self._start_ms > MAX_DURATION_MS:
                self._end_ms = self._start_ms + MAX_DURATION_MS
            self.start_changed.emit(self._start_ms)
            self.end_changed.emit(self._end_ms)
            self.seek_requested.emit(self._start_ms)
        else:
            self._end_ms = max(self._start_ms + 1, min(ms, self._duration_ms))
            if self._end_ms - self._start_ms > MAX_DURATION_MS:
                self._start_ms = self._end_ms - MAX_DURATION_MS
            self.start_changed.emit(self._start_ms)
            self.end_changed.emit(self._end_ms)
            self.seek_requested.emit(self._end_ms)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = None


class _ExportThread(QThread):
    """Run :class:`VideoExporter` off the main thread."""

    progress = Signal(int, str)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, request: VideoExportRequest) -> None:
        super().__init__()
        self._request = request

    def run(self) -> None:
        try:
            output = VideoExporter().export_zt(
                self._request, progress=self._on_progress,
            )
        except VideoExportError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:  # last-ditch — never let a worker crash the UI
            log.exception("VideoExporter raised unexpected exception")
            self.failed.emit(f"Unexpected export failure: {e}")
            return
        self.succeeded.emit(str(output))

    def _on_progress(self, percent: int, message: str) -> None:
        log.info("_on_progress: percent=%s message=%s", percent, message)
        self.progress.emit(percent, message)


class VideoCropDialog(QDialog):
    """Modal: load a video, trim it, export a ``Theme.zt``.

    Usage::

        dialog = VideoCropDialog(parent)
        dialog.load_video(Path("clip.mp4"), target_w=480, target_h=480)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            zt_path = dialog.output_path()
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crop video → Theme.zt")
        self.setModal(True)

        self._video_path: Path | None = None
        self._duration_ms = 0
        self._target_w = 0
        self._target_h = 0
        self._rotation = 0
        self._preview_pix: QPixmap | None = None
        self._output: Path | None = None
        self._exporting = False
        self._export_thread: _ExportThread | None = None
        self._playing = False
        self._play_pos_ms = 0
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_play_tick)
        self._build()

    # ── Public API ───────────────────────────────────────────────────

    def load_video(
        self, path: Path, target_w: int, target_h: int,
    ) -> bool:
        """Load *path* for trimming.  Returns ``False`` on failure."""
        self._video_path = path
        self._target_w = target_w
        self._target_h = target_h
        self._rotation = 0
        self._duration_ms = probe_duration_ms(path)
        if self._duration_ms <= 0:
            self._info.setText(
                f"Couldn't read duration of {path.name}.  "
                "Is ffprobe installed and the file a real video?",
            )
            return False
        self._target_label.setText(
            f"Target: {target_w}×{target_h}px • Source: {path.name}",
        )
        self._duration_label.setText(_format_ms(self._duration_ms))
        self._timeline.set_range(self._duration_ms)
        self._start_label.setText(_format_ms(0))
        self._end_label.setText(_format_ms(
            min(self._duration_ms, MAX_DURATION_MS),
        ))
        self._seek_preview(0)
        return True

    def output_path(self) -> Path | None:
        """Path to the produced Theme.zt after Accept, else ``None``."""
        return self._output

    # ── UI build ─────────────────────────────────────────────────────

    def _build(self) -> None:
        toolbar = QToolBar(self)
        act_rotate = QAction("Rotate 90°", self)
        act_rotate.triggered.connect(self._on_rotate)
        toolbar.addAction(act_rotate)
        toolbar.addSeparator()
        self._play_action = QAction("Play", self)
        self._play_action.triggered.connect(self._toggle_play)
        toolbar.addAction(self._play_action)

        self._target_label = QLabel("Target: (load a video)", self)
        self._target_label.setStyleSheet("color: #aaa;")

        self._preview = QLabel(self)
        self._preview.setFixedSize(_PREVIEW_W, _PREVIEW_H)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            "background-color: #000; border: 1px solid #333;",
        )
        self._preview.setText("Preview")

        self._timeline = _TimelineBar(self)
        self._timeline.start_changed.connect(self._on_start_changed)
        self._timeline.end_changed.connect(self._on_end_changed)
        self._timeline.seek_requested.connect(self._seek_preview)

        self._current_label = QLabel("00:00:00", self)
        self._current_label.setStyleSheet("color: #6c6;")
        self._duration_label = QLabel("00:00:00", self)
        self._duration_label.setStyleSheet("color: #ccc;")
        self._start_label = QLabel("00:00:00", self)
        self._start_label.setStyleSheet("color: #6c6;")
        self._end_label = QLabel("00:00:00", self)
        self._end_label.setStyleSheet("color: #c66;")

        clock_row = QHBoxLayout()
        clock_row.addWidget(QLabel("Cursor:", self))
        clock_row.addWidget(self._current_label)
        clock_row.addStretch(1)
        clock_row.addWidget(QLabel("Duration:", self))
        clock_row.addWidget(self._duration_label)

        clip_row = QHBoxLayout()
        clip_row.addWidget(QLabel("Clip in:", self))
        clip_row.addWidget(self._start_label)
        clip_row.addStretch(1)
        clip_row.addWidget(QLabel("Clip out:", self))
        clip_row.addWidget(self._end_label)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)

        self._info = QLabel("", self)
        self._info.setStyleSheet("color: #aaa;")
        self._info.setWordWrap(True)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.button(
            QDialogButtonBox.StandardButton.Ok,
        ).setText("Export")
        self._buttons.accepted.connect(self._on_export_clicked)
        self._buttons.rejected.connect(self._on_cancel_clicked)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addWidget(toolbar)
        root.addWidget(self._target_label)
        root.addWidget(self._preview, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._timeline, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addLayout(clock_row)
        root.addLayout(clip_row)
        root.addWidget(self._progress)
        root.addWidget(self._info)
        root.addWidget(self._buttons)

    # ── Frame preview (ffmpeg seek) ──────────────────────────────────

    def _seek_preview(self, ms: int) -> None:
        if self._video_path is None:
            return
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-ss", f"{ms / 1000.0}",
                    "-i", str(self._video_path),
                    "-vframes", "1",
                    "-f", "image2pipe", "-vcodec", "bmp",
                    "-v", "error", "-y", "-",
                ],
                capture_output=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            log.debug("video preview seek failed: %s", e)
            return
        if result.returncode != 0 or not result.stdout:
            return
        img = QImage.fromData(result.stdout)
        if img.isNull():
            return
        if self._rotation:
            img = img.transformed(QTransform().rotate(self._rotation))
        scaled = img.scaled(
            _PREVIEW_W, _PREVIEW_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_pix = QPixmap.fromImage(scaled)
        self._preview.setPixmap(self._preview_pix)
        self._current_label.setText(_format_ms(ms))

    # ── Signal handlers ──────────────────────────────────────────────

    def _on_start_changed(self, ms: int) -> None:
        log.info("_on_start_changed: ms=%s", ms)
        self._start_label.setText(_format_ms(ms))

    def _on_end_changed(self, ms: int) -> None:
        log.info("_on_end_changed: ms=%s", ms)
        self._end_label.setText(_format_ms(ms))

    def _on_rotate(self) -> None:
        log.info("_on_rotate")
        self._rotation = (self._rotation + 90) % 360
        start, _ = self._timeline.clip_ms()
        self._seek_preview(start)

    def _toggle_play(self) -> None:
        if self._playing:
            self._stop_play()
        else:
            self._start_play()

    def _start_play(self) -> None:
        if self._video_path is None:
            return
        self._playing = True
        self._play_action.setText("Pause")
        start, _ = self._timeline.clip_ms()
        self._play_pos_ms = start
        self._play_timer.start(_FRAME_INTERVAL_MS)

    def _stop_play(self) -> None:
        self._playing = False
        self._play_action.setText("Play")
        self._play_timer.stop()

    def _on_play_tick(self) -> None:
        log.debug("_on_play_tick")
        start, end = self._timeline.clip_ms()
        if self._play_pos_ms >= end:
            self._play_pos_ms = start
        self._seek_preview(self._play_pos_ms)
        self._play_pos_ms += _FRAME_INTERVAL_MS

    # ── Export ───────────────────────────────────────────────────────

    def _on_export_clicked(self) -> None:
        log.info("_on_export_clicked")
        if self._video_path is None:
            self._info.setText("Load a video first.")
            return
        if self._exporting:
            return
        self._stop_play()
        start, end = self._timeline.clip_ms()
        request = VideoExportRequest(
            source=self._video_path,
            start_ms=start,
            end_ms=end,
            target_w=self._target_w,
            target_h=self._target_h,
            rotation=self._rotation,
        )
        self._exporting = True
        self._buttons.button(
            QDialogButtonBox.StandardButton.Ok,
        ).setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._info.setText("Exporting…")
        self._export_thread = _ExportThread(request)
        self._export_thread.progress.connect(self._on_export_progress)
        self._export_thread.succeeded.connect(self._on_export_succeeded)
        self._export_thread.failed.connect(self._on_export_failed)
        self._export_thread.finished.connect(self._cleanup_thread)
        self._export_thread.start()

    def _on_cancel_clicked(self) -> None:
        log.info("_on_cancel_clicked")
        if self._exporting and self._export_thread is not None:
            # Terminate is heavy-handed but ffmpeg can run for minutes;
            # the temp dir is cleaned up by VideoExporter's own except.
            self._export_thread.requestInterruption()
            self._export_thread.terminate()
        self._stop_play()
        self.reject()

    def _on_export_progress(self, percent: int, message: str) -> None:
        log.info("_on_export_progress: percent=%s message=%s", percent, message)
        self._progress.setValue(percent)
        self._info.setText(message)

    def _on_export_succeeded(self, output_path_str: str) -> None:
        log.info("_on_export_succeeded: output_path_str=%s", output_path_str)
        self._output = Path(output_path_str)
        self._info.setText(f"Exported to {output_path_str}")
        self._buttons.button(
            QDialogButtonBox.StandardButton.Ok,
        ).setEnabled(True)
        self._exporting = False
        self.accept()

    def _on_export_failed(self, message: str) -> None:
        log.info("_on_export_failed: message=%s", message)
        self._info.setText(f"Export failed: {message}")
        self._progress.setVisible(False)
        self._buttons.button(
            QDialogButtonBox.StandardButton.Ok,
        ).setEnabled(True)
        self._exporting = False

    def _cleanup_thread(self) -> None:
        self._export_thread = None
