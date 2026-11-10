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
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from ...core.commands import (
    LcdSnapshot,
    SetBrightness,
    SetOrientation,
)
from ..presentation.theme_settings import ThemeSettings
from ..qt_words import say

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
