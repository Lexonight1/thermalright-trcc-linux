"""DisplayPanel — orientation, brightness, theme load, background media."""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
    QWidget,
)

from ....core.commands import (
    LcdSnapshot,
    LoadTheme,
    PlayVideo,
    RestoreDeviceState,
    SeekVideo,
    SetBackground,
    SetBrightness,
    SetMediaPlayer,
    SetOrientation,
    StopVideo,
    ToggleVideo,
    VideoStatus,
)
from ....core.events import (
    VideoAdvanced,
    VideoPauseChanged,
    VideoStarted,
    VideoStopped,
)
from ....core.models import MEDIA, MediaKind
from ...presentation.display_source import describe_source
from ...presentation.video_clock import playback_clock
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

        # Each control sends its one Command when the USER changes it, and
        # shows the App's value otherwise.  A batch "Apply" re-sent both
        # without ever loading them: 30% -> 100% and 180° -> 0° on a press.
        self._orientation = QComboBox(self)
        for deg in (0, 90, 180, 270):
            self._orientation.addItem(f"{deg}°", userData=deg)
        self._orientation.activated.connect(self._on_orientation_chosen)

        self._brightness = QSlider(Qt.Orientation.Horizontal, self)
        self._brightness.setRange(0, 100)
        # valueChanged on release / key step only, so a drag sends once.
        self._brightness.setTracking(False)
        self._brightness_label = QLabel("", self)
        self._brightness.sliderMoved.connect(self._on_brightness_slid)
        self._brightness.valueChanged.connect(self._on_brightness_chosen)

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

        self._load_btn = QPushButton("Load theme", self)
        self._load_btn.clicked.connect(self._on_load_theme)

        self._restore_btn = QPushButton("Restore last theme", self)
        self._restore_btn.clicked.connect(self._on_restore_last)

        # Background media — immediate actions.
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
        self._pause_video_btn = QPushButton("Pause", self)
        self._pause_video_btn.clicked.connect(self._on_toggle_video)
        self._stop_video_btn = QPushButton("Stop", self)
        self._stop_video_btn.clicked.connect(self._on_stop_video)

        # Position.  Play/Pause/Stop could start and halt a video and never say
        # WHERE it was, so there was no way to jump -- the CLI and API both
        # have ``SeekVideo`` and this skin had no surface for it.
        self._seek = QSlider(Qt.Orientation.Horizontal, self)
        self._seek.setEnabled(False)
        self._seek.sliderReleased.connect(self._on_seek_released)
        self._seek.sliderMoved.connect(self._on_seek_moved)
        # A groove click or an arrow/page key: neither signal above fires.
        self._seek.valueChanged.connect(self._on_seek_released)
        #: ``(frame_count, fps)`` of the video shown, for the drag label.
        self._clock_basis = (0, 0)
        # The core ticks the video (#249) and announces each frame, so the
        # slider can follow it live instead of only on a manual refresh.
        self._bus.video_advanced.connect(self._on_video_advanced)
        # ...and its start and stop from ANY UI, so a stopped video does not
        # leave the slider live at its last frame.
        for signal in (self._bus.video_started, self._bus.video_stopped):
            signal.connect(self._on_video_state,
                           type=Qt.ConnectionType.QueuedConnection)
        # ...and a pause or resume from any UI, which neither of those carries.
        self._bus.video_pause_changed.connect(
            self._on_video_pause_changed,
            type=Qt.ConnectionType.QueuedConnection)
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

        #: What is on the panel, from any UI -- ``describe_source``.
        self._showing = QLabel("", self)
        for signal in (self._bus.screencast_started, self._bus.screencast_stopped,
                       self._bus.video_started, self._bus.video_stopped):
            signal.connect(self._on_source_event,
                           type=Qt.ConnectionType.QueuedConnection)

        self._status = QLabel("", self)
        self._media = MediaPlayerControls(self, self._status.setText)

        form = QFormLayout()
        form.addRow("Device key:", self._picker)
        form.addRow("Orientation:", self._orientation)
        form.addRow("Brightness:", brightness_row)
        form.addRow("Theme:", theme_row)
        form.addRow("Showing:", self._showing)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self._load_btn)
        root.addWidget(self._restore_btn)
        root.addWidget(QLabel("Background:", self))
        root.addLayout(video_row)
        root.addLayout(seek_row)
        root.addWidget(QLabel("Media player:", self))
        root.addWidget(self._media)
        root.addWidget(self._status)
        root.addStretch(1)

        self._picker.key_changed.connect(self._on_key_changed)
        self._bus.settings_changed.connect(
            self._on_settings_changed, type=Qt.ConnectionType.QueuedConnection)
        self._show_state()

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
        result = self.dispatch(RestoreDeviceState(key=key))
        self._status.setText(result.message)

    def _on_brightness_slid(self, value: int) -> None:
        """Echo the slider position beside it while dragging."""
        log.debug("_on_brightness_slid: value=%s", value)
        self._brightness_label.setText(f"{value}%")

    def _on_brightness_chosen(self, value: int) -> None:
        self._on_brightness_slid(value)
        key = self._require_key()
        if key is None:
            return
        log.info("_on_brightness_chosen: key=%s %d%%", key, value)
        self._status.setText(self.dispatch(
            SetBrightness(key=key, percent=value)).message)

    def _on_orientation_chosen(self, _index: int) -> None:
        """No theme reload here: SetOrientation publishes OrientationChanged,
        and ``App._on_orientation_changed`` re-roots the active theme (plus the
        cloud background and mask) inside that dispatch, for every face."""
        key = self._require_key()
        if key is None:
            return
        degrees = int(self._orientation.currentData())
        log.info("_on_orientation_chosen: key=%s %d°", key, degrees)
        self._status.setText(self.dispatch(
            SetOrientation(key=key, degrees=degrees)).message)

    def _on_key_changed(self, key: str) -> None:
        log.info("_on_key_changed: %s", key)
        self._show_state()

    def _on_settings_changed(self, event: Any) -> None:
        if event.key == self._picker.current_key():
            log.debug("_on_settings_changed: %s for %s",
                      type(event).__name__, event.key)
            self._show_state()

    def _show_state(self) -> None:
        """The device's orientation and brightness, as the App holds them.
        Sends nothing: the combo fires on ``activated`` only, and the slider
        is set under blocked signals."""
        key = self._picker.current_key()
        snap = self.dispatch(LcdSnapshot(key=key)) if key else None
        if snap is None or not snap.ok:
            log.debug("_show_state: nothing to show for %r", key)
            return
        log.info("_show_state: %s %d° %d%%", key, snap.orientation,
                 snap.brightness)
        index = self._orientation.findData(snap.orientation)
        if index >= 0:
            self._orientation.setCurrentIndex(index)
        self._brightness.blockSignals(True)
        self._brightness.setValue(snap.brightness)
        self._brightness.blockSignals(False)
        self._brightness_label.setText(f"{snap.brightness}%")
        self._showing.setText(describe_source(
            snap.display_source, snap.background_mode, snap.media_player_uri))
        self._media.show_snapshot(snap)

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
        self._status.setText(result.message)   # the button follows the event

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
        log.debug("_refresh_video_status: key=%s playing=%s paused=%s "
                  "cursor=%s/%s", key, r.playing, r.paused, r.cursor,
                  r.frame_count)
        self._show_position(r.cursor or 0, r.frame_count or 0, r.fps or 0)
        self._show_paused(bool(r.paused))

    def _on_video_pause_changed(self, event: VideoPauseChanged) -> None:
        """Paused or resumed in any UI -- this one included."""
        log.info("_on_video_pause_changed: %s paused=%s (showing %s)",
                 event.key, event.paused, self._picker.current_key())
        if event.key == self._picker.current_key():
            self._show_paused(event.paused)

    def _show_paused(self, paused: bool) -> None:
        """The button names what a click will do."""
        log.debug("_show_paused: %s", paused)
        self._pause_video_btn.setText("Resume" if paused else "Pause")

    def _on_video_advanced(self, event: VideoAdvanced) -> None:
        """Follow the selected device's video frame by frame (per-frame: DEBUG).

        Left alone while the user drags, or the thumb would be torn out of
        their hand thirty times a second.
        """
        if event.key != self._picker.current_key() or self._seek.isSliderDown():
            return
        log.debug("_on_video_advanced: %s %d/%d",
                  event.key, event.cursor, event.frame_count)
        self._show_position(event.cursor, event.frame_count, event.fps)

    def _on_source_event(self, event: Any) -> None:
        """A cast or video started or stopped in any UI -- not a settings
        event, so ``_on_settings_changed`` never saw it."""
        if event.key == self._picker.current_key():
            log.info("_on_source_event: %s for %s", type(event).__name__, event.key)
            self._show_state()

    def _on_video_state(self, event: VideoStarted | VideoStopped) -> None:
        """A video started or stopped somewhere — show the selected device's."""
        log.info("_on_video_state: %s %s (showing %s)", type(event).__name__,
                 event.key, self._picker.current_key())
        if event.key == self._picker.current_key():
            self._refresh_video_status()

    def _show_position(self, cursor: int, total: int, fps: int) -> None:
        """Put a playback position on the slider; ``total == 0`` is no video."""
        log.debug("_show_position: %d/%d @ %d fps", cursor, total, fps)
        if not total:
            self._seek.setEnabled(False)
            self._seek_label.setText("no video")
            return
        self._seek.setEnabled(True)
        self._seek.blockSignals(True)
        self._seek.setRange(0, max(0, total - 1))
        self._seek.setValue(cursor)
        self._seek.blockSignals(False)
        self._clock_basis = (total, fps)
        self._seek_label.setText(playback_clock(cursor, total, fps))

    def _on_seek_moved(self, frame: int) -> None:
        """Dragging: show where the release will land, seek nothing yet."""
        log.debug("_on_seek_moved: frame=%d", frame)
        self._seek_label.setText(playback_clock(frame, *self._clock_basis))

    def _on_seek_released(self, _value: int | None = None) -> None:
        """Seek -- on RELEASE of a drag, or on a groove click / key step.

        Not on every drag step: ``SeekVideo`` moves the playback cursor, which
        the render tick reads, so seeking per pixel of drag would queue a
        rebuild per pixel.  ``valueChanged`` lands here too, for the click and
        the key that move the value without a press on the handle; while the
        handle is held it is ignored, and ``_show_position`` blocks signals,
        so following playback never lands here.
        """
        if self._seek.isSliderDown() or (key := self._require_key()) is None:
            return
        frame = int(self._seek.value())
        log.info("_on_seek_released: key=%s frame=%d", key, frame)
        result = self.dispatch(SeekVideo(key=key, frame=frame))
        self._status.setText(result.message)
        self._refresh_video_status()

    def _on_load_theme(self) -> None:
        """Load the chosen theme folder -- when the user asks, once."""
        key = self._require_key()
        theme_path = self._theme_path.text().strip()
        if key is None or not theme_path:
            log.info("_on_load_theme: nothing to load (key=%s path=%r)",
                     key, theme_path)
            return
        log.info("_on_load_theme: key=%s path=%s", key, theme_path)
        self._status.setText(self.dispatch(
            LoadTheme(key=key, path=Path(theme_path))).message)


class MediaPlayerControls(QWidget):
    """The media player: a video file, or a web video / live stream.

    One field for both, as the CLI's ``display media-player`` and the API take
    one ``uri``; the App plays a URL on the screencast's chain.  Its own class
    because it is its own concern -- the Display panel shows it and hands it
    each snapshot; this sends ``SetMediaPlayer`` and nothing else.
    """

    def __init__(self, panel: DisplayPanel, report: Callable[[str], None]) -> None:
        super().__init__(panel)
        log.debug("MediaPlayerControls.__init__")
        self._panel = panel
        self._report = report
        self._source = QLineEdit(self)
        self._source.setPlaceholderText("A video file, or an http / https / rtsp address")
        self._source.returnPressed.connect(self._on_play)
        self._browse = QPushButton("Browse…", self)
        self._browse.clicked.connect(self._on_browse)
        self._play = QPushButton("Play", self)
        self._play.clicked.connect(self._on_play)
        self._close = QPushButton("Close media player", self)
        self._close.clicked.connect(self._on_close)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._source, 1)
        for button in (self._browse, self._play, self._close):
            row.addWidget(button)

    def show_snapshot(self, snap: Any) -> None:
        """Another UI's source, shown -- unless the user is typing one here."""
        log.debug("show_snapshot: source=%s uri=%s",
                  snap.display_source, snap.media_player_uri)
        self._close.setEnabled(snap.display_source == "media")
        if snap.media_player_uri and not self._source.hasFocus():
            self._source.setText(snap.media_player_uri)

    def _on_browse(self) -> None:
        """Pick a local video into the field -- Play starts it."""
        source, _ = QFileDialog.getOpenFileName(
            self, "Pick a video for the media player", "",
            f"Videos ({MEDIA.patterns(MediaKind.ANIMATED)});;All files (*)",
        )
        log.info("_on_browse: %r", source)
        if source:
            self._source.setText(source)

    def _on_play(self) -> None:
        """``SetMediaPlayer`` with the field: a file, or a web address."""
        key = self._panel._require_key()
        uri = self._source.text().strip()
        log.info("_on_play: key=%s uri=%r", key, uri)
        if key is not None and uri:
            self._report(self._panel.dispatch(SetMediaPlayer(key=key, uri=uri)).message)

    def _on_close(self) -> None:
        key = self._panel._require_key()
        log.info("_on_close: key=%s", key)
        if key is not None:
            self._report(self._panel.dispatch(SetMediaPlayer(key=key, uri="")).message)
