"""DisplayPanel — orientation, brightness, theme load, video transport."""
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
    SetBrightness,
    SetOrientation,
    StopVideo,
    ToggleVideo,
)
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

log = logging.getLogger(__name__)


class DisplayPanel(BasePanel):
    """Per-device display controls (orientation / brightness / theme)."""

    def _setup_ui(self) -> None:
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
        )

        self._orientation = QComboBox(self)
        for deg in (0, 90, 180, 270):
            self._orientation.addItem(f"{deg}°", userData=deg)

        self._brightness = QSlider(Qt.Orientation.Horizontal, self)
        self._brightness.setRange(0, 100)
        self._brightness.setValue(100)
        self._brightness_label = QLabel("100%", self)
        self._brightness.valueChanged.connect(
            lambda v: self._brightness_label.setText(f"{v}%")
        )

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

        # Video transport — immediate actions (not part of the batch Apply).
        self._play_video_btn = QPushButton("Play video…", self)
        self._play_video_btn.clicked.connect(self._on_play_video)
        self._pause_video_btn = QPushButton("Pause/Resume", self)
        self._pause_video_btn.clicked.connect(self._on_toggle_video)
        self._stop_video_btn = QPushButton("Stop", self)
        self._stop_video_btn.clicked.connect(self._on_stop_video)

        video_row = QHBoxLayout()
        video_row.addWidget(self._play_video_btn)
        video_row.addWidget(self._pause_video_btn)
        video_row.addWidget(self._stop_video_btn)
        video_row.addStretch(1)

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
        root.addWidget(QLabel("Video:", self))
        root.addLayout(video_row)
        root.addWidget(self._status)
        root.addStretch(1)

    # ── Actions ───────────────────────────────────────────────────────

    def _require_key(self) -> str | None:
        """Return the picked device key, or set a prompt + return None."""
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

    def _on_play_video(self) -> None:
        key = self._require_key()
        if key is None:
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Pick a video to play", "",
            "Videos (*.mp4 *.mov *.webm *.mkv *.avi *.zt);;All files (*)",
        )
        if not source:
            return
        log.info("_on_play_video: key=%s path=%s", key, source)
        result = self.dispatch(PlayVideo(key=key, path=Path(source)))
        self._status.setText(result.message)

    def _on_toggle_video(self) -> None:
        key = self._require_key()
        if key is None:
            return
        log.info("_on_toggle_video: key=%s", key)
        result = self.dispatch(ToggleVideo(key=key))
        self._status.setText(result.message)

    def _on_stop_video(self) -> None:
        key = self._require_key()
        if key is None:
            return
        log.info("_on_stop_video: key=%s", key)
        result = self.dispatch(StopVideo(key=key))
        self._status.setText(result.message)

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
