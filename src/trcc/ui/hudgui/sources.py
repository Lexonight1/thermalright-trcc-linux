"""hudgui's device Settings tab (sources) and the controls under the preview.

The gui's Settings tab, layout first (his order, 2026-11-05): the four display
sources -- Mask, Background, Screencast, Video player -- each a switch and its
buttons; brightness and rotation sit UNDER THE PREVIEW, as in the gui
(``DeviceControls``).  The overlay editor is the next step.

Every control dispatches a Command (``doc/REFERENCE_COMMANDS.md``) and every
switch SHOWS what the App holds (``LcdSnapshot``), re-read on every device
event from any UI -- never what this window last sent.  The background switch
is ``ShowBackground``, the one Command every UI's switch now sends.

Picking a picture or a video here puts it on as it is; the gui's crop and trim
editors come later.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.commands import (
    LcdSnapshot,
    SetBrightness,
    SetOrientation,
)
from ..presentation.theme_settings import SettingsView, ThemeSettings
from ..qt_words import say
from .words import words

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...core.ports import CommandBus
    from ...core.results import LcdSnapshotResult
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection

log = logging.getLogger(__name__)

_GROUP_STYLE = ("QGroupBox { color: #dfe5ea; font-weight: bold; border: 1px solid "
                "rgba(200, 205, 212, 90); border-radius: 8px; margin-top: 12px; "
                "padding: 10px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }"
                "QCheckBox, QLabel { color: #dfe5ea; }")
_IMAGES = "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
_VIDEOS = "Videos (*.mp4 *.mov *.webm *.mkv *.avi *.zt *.gif)"


class _DeviceTab(QWidget):
    """What the device tabs here share: the picked device, the App, and
    showing its state again on every device event from any UI."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("%s: built", type(self).__name__)
        self._app = app
        self._selection = selection
        #: What a Settings control sends -- the gui's, shared by every GUI.
        self._settings = ThemeSettings(app)
        selection.changed.connect(self.show_state)
        self._follow(bus)

    def _follow(self, bus: BusBridge) -> None:
        """Show the device again when one of its settings or its theme changes."""
        log.debug("%s._follow: settings + theme", type(self).__name__)
        bus.settings_changed.connect(self._on_device_event,
                                     type=Qt.ConnectionType.QueuedConnection)
        bus.theme_loaded.connect(self._on_device_event,
                                 type=Qt.ConnectionType.QueuedConnection)

    def _on_device_event(self, event: Any) -> None:
        """A device changed somewhere -- if it is this one, show it again."""
        log.debug("%s._on_device_event: %s", type(self).__name__, type(event).__name__)
        if getattr(event, "key", None) == self._selection.key:
            self.show_state()

    def show_state(self, *_args: object) -> None:
        """Put the App's values on the controls.  Sends nothing."""
        key = self._selection.key
        log.debug("%s.show_state: %s", type(self).__name__, key)
        if key:
            self._show(self._app.dispatch(LcdSnapshot(key=key)))

    def _show(self, snapshot: LcdSnapshotResult) -> None:
        """The controls from *snapshot* -- each tab its own."""
        log.debug("%s._show: nothing to show", type(self).__name__)

    def _send(self, command: Command[Any]) -> Any:
        """Dispatch *command* for the picked device; say if it failed."""
        log.info("%s._send: %s", type(self).__name__, command)
        result = self._app.dispatch(command)
        if not result.ok:
            log.warning("%s._send: %s", type(self).__name__, result.message)
        return result

    @staticmethod
    def _quiet(box: QCheckBox | QSlider, value: Any) -> None:
        """Set a control without it sending anything."""
        log.debug("_DeviceTab._quiet: %s", value)
        box.blockSignals(True)
        if isinstance(box, QCheckBox):
            box.setChecked(bool(value))
        else:
            box.setValue(int(value))
        box.blockSignals(False)


class _SettingsTab(_DeviceTab):
    """A tab of the device Settings panel: it draws the one ``SettingsView``
    every GUI's panel draws, and follows every event that changes it -- the
    gui's list, not its own pick of two."""

    def _follow(self, bus: BusBridge) -> None:
        log.debug("%s._follow: the Settings panel's events", type(self).__name__)
        bus.follow_settings(self._on_settings_event)

    def _on_settings_event(self, event: object) -> None:
        """Something the panel shows changed -- here, if it concerns this device."""
        if ThemeSettings.concerns(event, self._selection.key):
            log.debug("%s._on_settings_event: %s", type(self).__name__,
                      type(event).__name__)
            self.show_state()

    def show_state(self, *_args: object) -> None:
        """Put the App's values on the controls.  Sends nothing."""
        key = self._selection.key
        view = self._settings.read(key) if key else None
        log.debug("%s.show_state: %s -> %s", type(self).__name__, key, view is not None)
        if view is not None:
            self._show_view(view)

    def _show_view(self, view: SettingsView) -> None:
        """The controls from *view* -- each tab its own."""
        log.debug("%s._show_view: nothing to show", type(self).__name__)


class SourcesPanel(_SettingsTab):
    """The four display sources, as the gui's Settings tab has them."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(app, bus, selection, parent)
        log.debug("SourcesPanel: mask, background, screencast, video player")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.mask_switch = say(QCheckBox(), "Show the mask")
        self.background_switch = say(QCheckBox(), "Draw a background")
        self.screencast_switch = say(QCheckBox(), "Cast a screen region")
        self.audio_switch = say(QCheckBox(), "with sound bars")
        self.video_switch = say(QCheckBox(), "Video player playing")
        self.mask_switch.toggled.connect(self._on_mask)
        self.background_switch.toggled.connect(self._on_background)
        self.screencast_switch.toggled.connect(self._on_screencast)
        self.video_switch.toggled.connect(self._on_video)
        grid.addWidget(self._group("Layer Mask", self.mask_switch,
                                   ("Upload", self._on_mask_upload)), 0, 0)
        grid.addWidget(self._group("Background", self.background_switch,
                                   ("Load Image", self._on_picture),
                                   ("Load Video", self._on_background_video)), 0, 1)
        grid.addWidget(self._group("Screencast", self.screencast_switch, self.audio_switch), 1, 0)
        grid.addWidget(self._group("Media Player", self.video_switch,
                                   ("Load Video", self._on_player_file),
                                   ("Web Video", self._on_player_url)), 1, 1)
        grid.setRowStretch(2, 1)
        self.show_state()

    def _group(self, title: str, switch: QCheckBox, *extras: Any) -> QGroupBox:
        """One source: its switch, then its buttons (or a second checkbox)."""
        log.debug("SourcesPanel._group: %s", title)
        group = QGroupBox()
        group.setStyleSheet(_GROUP_STYLE)
        column = QVBoxLayout(group)
        # The title is a label, not the box's own: ``say`` keeps a label's
        # English, so the language can change under it.
        heading = say(QLabel(group), title)
        heading.setStyleSheet("font-weight: bold;")
        column.addWidget(heading)
        column.addWidget(switch)
        row = QHBoxLayout()
        for extra in extras:
            if isinstance(extra, QWidget):
                row.addWidget(extra)
                continue
            text, slot = extra
            button = say(QPushButton(group), text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        column.addLayout(row)
        return group

    def _show_view(self, view: SettingsView) -> None:
        """The switches as the App holds them."""
        log.info("SourcesPanel._show_view: source=%s drawn=%s mask=%s cast audio=%s",
                 view.source, view.background_drawn, view.mask_visible, view.cast_audio)
        self._quiet(self.mask_switch, view.mask_visible)
        self._quiet(self.background_switch,
                    view.source == "background" and view.background_drawn)
        self._quiet(self.screencast_switch, view.source == "screencast")
        self._quiet(self.audio_switch, bool(view.cast_audio))
        self._quiet(self.video_switch, view.source == "media")

    # ── The switches ───────────────────────────────────────────────────

    def _on_mask(self, on: bool) -> None:
        log.info("SourcesPanel._on_mask: %s", on)
        self._settings.set_mask_visible(self._selection.key, on)
        self.show_state()

    def _on_background(self, on: bool) -> None:
        log.info("SourcesPanel._on_background: %s", on)
        self._settings.show_background(self._selection.key, on)
        self.show_state()

    def _on_screencast(self, on: bool) -> None:
        """On: the panel's own region, as the App holds it, and the sound
        choice.  Off: the cast ends."""
        key = self._selection.key
        log.info("SourcesPanel._on_screencast: %s", on)
        if on:
            box = self._app.dispatch(LcdSnapshot(key=key)).screencast_rect
            self._settings.start_cast(key, box if box is not None else (0, 0, 0, 0),
                                      self.audio_switch.isChecked())
        else:
            self._settings.stop_cast(key)
        self.show_state()

    def _on_video(self, on: bool) -> None:
        """Off: the media player stops and the theme returns.  On needs a
        file or an address -- the buttons."""
        log.info("SourcesPanel._on_video: %s", on)
        if not on:
            self._settings.stop_media(self._selection.key)
        self.show_state()

    # ── The buttons ────────────────────────────────────────────────────

    def choose_file(self, title: str, kinds: str) -> str:
        """A file the user picks, or "" -- a method, so a test picks.
        *title* is English; the dialog says it in the App's language."""
        log.debug("SourcesPanel.choose_file: %s", title)
        path, _ = QFileDialog.getOpenFileName(self, words(self._app)(title), "", kinds)
        return path

    def ask_url(self) -> str:
        """A web address the user types, or "" -- a method, so a test types."""
        log.debug("SourcesPanel.ask_url")
        say_now = words(self._app)
        url, ok = QInputDialog.getText(self, say_now("Web Video"),
                                       say_now("An http, https or rtsp address:"))
        return url.strip() if ok else ""

    def _on_mask_upload(self) -> None:
        log.info("SourcesPanel._on_mask_upload")
        if path := self.choose_file("Upload", _IMAGES):
            self._settings.apply_mask(self._selection.key, Path(path), custom=True)

    def _on_picture(self) -> None:
        log.info("SourcesPanel._on_picture")
        if path := self.choose_file("Load Image", _IMAGES):
            self._settings.set_background(self._selection.key, Path(path))

    def _on_background_video(self) -> None:
        log.info("SourcesPanel._on_background_video")
        if path := self.choose_file("Load Video", _VIDEOS):
            self._settings.set_background(self._selection.key, Path(path))

    def _on_player_file(self) -> None:
        log.info("SourcesPanel._on_player_file")
        if path := self.choose_file("Media Player", _VIDEOS):
            self._settings.play_media(self._selection.key, path)
            self.show_state()

    def _on_player_url(self) -> None:
        log.info("SourcesPanel._on_player_url")
        if url := self.ask_url():
            self._settings.play_media(self._selection.key, url)
            self.show_state()


class DeviceControls(_DeviceTab):
    """Under the preview, as in the gui: brightness and rotation."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(app, bus, selection, parent)
        log.debug("DeviceControls: brightness and rotation")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        label = say(QLabel(self), "Brightness")
        label.setStyleSheet("color: #dfe5ea;")
        self.brightness = QSlider(Qt.Orientation.Horizontal, self)
        self.brightness.setRange(0, 100)
        self.brightness.sliderReleased.connect(self._on_brightness)
        self.rotate = say(QPushButton(self), "Rotate 90°")
        self.rotate.clicked.connect(self._on_rotate)
        row.addWidget(label)
        row.addWidget(self.brightness, stretch=1)
        row.addWidget(self.rotate)
        self._degrees = 0
        self.show_state()

    def _show(self, snapshot: LcdSnapshotResult) -> None:
        log.debug("DeviceControls._show: %d%% %d°", snapshot.brightness,
                  snapshot.orientation)
        self._quiet(self.brightness, snapshot.brightness)
        self._degrees = snapshot.orientation

    def _on_brightness(self) -> None:
        """Sent once, when the slider is let go -- not per pixel dragged."""
        log.info("DeviceControls._on_brightness: %d", self.brightness.value())
        self._send(SetBrightness(key=self._selection.key,
                                 percent=self.brightness.value()))

    def _on_rotate(self) -> None:
        """The next quarter turn, from the App's own angle."""
        degrees = (self._degrees + 90) % 360
        log.info("DeviceControls._on_rotate: %d -> %d", self._degrees, degrees)
        self._send(SetOrientation(key=self._selection.key, degrees=degrees))
        self.show_state()
