"""DisplayPanel — orientation, brightness, theme load, background media."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from ....core.commands import (
    LoadTheme,
    PlayVideo,
    RestoreLastTheme,
    SeekVideo,
    SetBackground,
    SetBrightness,
    SetOrientation,
    StopVideo,
    ToggleVideo,
    VideoStatus,
)
from ....core.models import MEDIA, MediaKind
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

log = logging.getLogger(__name__)


class DisplayPanel(BasePanel):
    """Per-device display controls (orientation / brightness / theme)."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd",
            parent=self, selection=self._selection,
        )

        self._orientation = QComboBox(self)
        for deg in (0, 90, 180, 270):
            self._orientation.addItem(f"{deg}°", userData=deg)

        self._brightness = QSlider(Qt.Orientation.Horizontal, self)
        self._brightness.setRange(0, 100)
        self._brightness.setValue(100)
        self._brightness_label = QLabel("100%", self)
        self._brightness.valueChanged.connect(self._on_brightness_slid)

        brightness_row = QHBoxLayout()
        brightness_row.addWidget(self._brightness, stretch=1)
        brightness_row.addWidget(self._brightness_label)

        self._theme_path = QLineEdit(self)
        self._theme_path.setReadOnly(True)
        self._theme_browse = QPushButton("Browse…", self)
        self._theme_browse.clicked.connect(self._on_browse_theme)

        theme_row = QHBoxLayout()
        theme_row.addWidget(self._theme_path, stretch=1)
        theme_row.addWidget(self._theme_browse)

        self._apply_btn = QPushButton("Apply", self)
        self._apply_btn.clicked.connect(self._on_apply)

        self._restore_btn = QPushButton("Restore last theme", self)
        self._restore_btn.clicked.connect(self._on_restore_last)

        # Background media — immediate actions (not part of the batch Apply).
        # ``SetBackground`` and ``PlayVideo`` are sisters: both write
        # ``DeviceSettings.background_path``, which the renderer consults
        # BEFORE the theme's own background, so they swap the picture and
        # KEEP the theme's overlays.  ``Stop`` clears the override for
        # either — it tests ``had_override`` independently of whether any
        # video was playing — which is why one button serves both.
        self._background_btn = QPushButton("Set background…", self)
        self._background_btn.clicked.connect(self._on_set_background)
        self._play_video_btn = QPushButton("Play video…", self)
        self._play_video_btn.clicked.connect(self._on_play_video)
        self._pause_video_btn = QPushButton("Pause/Resume", self)
        self._pause_video_btn.clicked.connect(self._on_toggle_video)
        self._stop_video_btn = QPushButton("Stop", self)
        self._stop_video_btn.clicked.connect(self._on_stop_video)

        # Position.  Play/Pause/Stop could start and halt a video and never say
        # WHERE it was, so there was no way to jump -- the CLI and API both
        # have ``SeekVideo`` and this skin had no surface for it.
        self._seek = QSlider(Qt.Orientation.Horizontal, self)
        self._seek.setEnabled(False)
        self._seek.sliderReleased.connect(self._on_seek_released)
        self._seek_label = QLabel("no video", self)
        self._refresh_video_btn = QPushButton("↻", self)
        self._refresh_video_btn.setToolTip("Refresh playback position")
        self._refresh_video_btn.setMaximumWidth(32)
        self._refresh_video_btn.clicked.connect(self._refresh_video_status)

        video_row = QHBoxLayout()
        video_row.addWidget(self._background_btn)
        video_row.addWidget(self._play_video_btn)
        video_row.addWidget(self._pause_video_btn)
        video_row.addWidget(self._stop_video_btn)
        video_row.addStretch(1)

        seek_row = QHBoxLayout()
        seek_row.addWidget(self._seek, 1)
        seek_row.addWidget(self._seek_label)
        seek_row.addWidget(self._refresh_video_btn)

        self._status = QLabel("", self)

        form = QFormLayout()
        form.addRow("Device key:", self._picker)
        form.addRow("Orientation:", self._orientation)
        form.addRow("Brightness:", brightness_row)
        form.addRow("Theme:", theme_row)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self._apply_btn)
        root.addWidget(self._restore_btn)
        root.addWidget(QLabel("Background:", self))
        root.addLayout(video_row)
        root.addLayout(seek_row)
        root.addWidget(self._status)
        root.addStretch(1)

    # ── Actions ───────────────────────────────────────────────────────

    def _require_key(self) -> str | None:
        """Return the picked device key, or set a prompt + return None."""
        log.debug("_require_key")
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first.  Open the Devices panel to scan "
                "if no devices are listed.",
            )
            return None
        return key

    def _on_browse_theme(self) -> None:
        log.info("_on_browse_theme")
        path = QFileDialog.getExistingDirectory(
            self, "Select theme directory", "",
        )
        if path:
            self._theme_path.setText(path)

    def _on_restore_last(self) -> None:
        key = self._require_key()
        if key is None:
            return
        log.info("_on_restore_last: key=%s", key)
        result = self.dispatch(RestoreLastTheme(key=key))
        self._status.setText(result.message)

    def _on_brightness_slid(self, value: int) -> None:
        """Echo the slider position beside it.  Not the apply path."""
        log.debug("_on_brightness_slid: value=%s", value)
        self._brightness_label.setText(f"{value}%")

    def _on_set_background(self) -> None:
        """Override the background with a STILL IMAGE, keeping the theme.

        The still sister of :meth:`_on_play_video`.  ``LoadImage`` is not a
        substitute — that one REPLACES the theme with a synthesised one-file
        theme, losing its overlays; this writes the override the renderer
        consults first.  Cleared by ``Stop``, same as a video.
        """
        key = self._require_key()
        if key is None:
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Pick a background image", "",
            f"Images ({MEDIA.patterns(MediaKind.IMAGE)});;All files (*)",
        )
        if not source:
            log.info("_on_set_background: cancelled by the user")
            return
        log.info("_on_set_background: key=%s path=%s", key, source)
        result = self.dispatch(SetBackground(key=key, path=Path(source)))
        self._status.setText(result.message)

    def _on_play_video(self) -> None:
        key = self._require_key()
        if key is None:
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Pick a video to play", "",
            f"Videos ({MEDIA.patterns(MediaKind.ANIMATED)});;All files (*)",
        )
        if not source:
            return
        log.info("_on_play_video: key=%s path=%s", key, source)
        result = self.dispatch(PlayVideo(key=key, path=Path(source)))
        self._status.setText(result.message)
        self._refresh_video_status()

    def _on_toggle_video(self) -> None:
        key = self._require_key()
        if key is None:
            return
        log.info("_on_toggle_video: key=%s", key)
        result = self.dispatch(ToggleVideo(key=key))
        self._status.setText(result.message)
        self._refresh_video_status()

    def _on_stop_video(self) -> None:
        key = self._require_key()
        if key is None:
            return
        log.info("_on_stop_video: key=%s", key)
        result = self.dispatch(StopVideo(key=key))
        self._status.setText(result.message)
        self._refresh_video_status()

    def _refresh_video_status(self) -> None:
        """Ask what playback is doing and show it.

        ``VideoStatus`` is the read: a device with no playback answers
        ``ok=True, playing=False`` with the optional fields ``None`` -- absence
        is a normal answer here, not a failure, so an empty slider is disabled
        rather than shown at zero as if it were parked at frame 0.
        """
        key = self._require_key()
        if key is None:
            return
        r = self.dispatch(VideoStatus(key=key))
        log.debug("_refresh_video_status: key=%s playing=%s cursor=%s/%s",
                  key, r.playing, r.cursor, r.frame_count)
        total = r.frame_count or 0
        if not total:
            self._seek.setEnabled(False)
            self._seek_label.setText("no video")
            return
        self._seek.setEnabled(True)
        self._seek.blockSignals(True)
        self._seek.setRange(0, max(0, total - 1))
        self._seek.setValue(r.cursor or 0)
        self._seek.blockSignals(False)
        self._seek_label.setText(f"{(r.cursor or 0) + 1} / {total}")

    def _on_seek_released(self) -> None:
        """Jump on RELEASE, not on every drag step.

        ``SeekVideo`` moves the playback cursor, which the render tick reads --
        seeking per pixel of drag would queue a rebuild per pixel.
        """
        key = self._require_key()
        if key is None:
            return
        frame = int(self._seek.value())
        log.info("_on_seek_released: key=%s frame=%d", key, frame)
        result = self.dispatch(SeekVideo(key=key, frame=frame))
        self._status.setText(result.message)
        self._refresh_video_status()

    def _on_apply(self) -> None:
        log.info("_on_apply")
        key = self._require_key()
        if key is None:
            return

        messages = []

        r_orient = self.dispatch(SetOrientation(
            key=key,
            degrees=int(self._orientation.currentData()),
        ))
        messages.append(r_orient.message)
        # No theme reload here: SetOrientation publishes OrientationChanged,
        # and App._on_orientation_changed re-roots the active theme (plus the
        # cloud background and mask) to the new orientation's catalog inside
        # that dispatch, for every face.  This panel used to re-decide it and
        # dispatch a second LoadTheme, which took the default
        # reset_overrides=True and persist-cleared the user's overlay edits.

        r_bright = self.dispatch(SetBrightness(
            key=key, percent=self._brightness.value(),
        ))
        messages.append(r_bright.message)

        theme_path = self._theme_path.text().strip()
        if theme_path:
            r_theme = self.dispatch(LoadTheme(
                key=key, path=Path(theme_path),
            ))
            messages.append(r_theme.message)

        self._status.setText("  |  ".join(messages))
