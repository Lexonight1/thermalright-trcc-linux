"""The screencast viewfinder -- a frame round each device's cast region.

The C#'s ``FormScreenshot`` (``formJP``): a frameless always-on-top window
drawn round the region being cast, shown while screencast mode is on unless
its hide flag (``myYcbk``) is set (``FormCZTV.cs:4629-4637``).  Dragging it
moves the region -- the release writes the new position
(``FormScreenshot_MouseUp`` -> ``UpDateFormScreenshot``) -- and a double-click
hides it and sets the flag (``FormScreenshot_DoubleClick``).

One difference, on purpose.  The C# draws its 5 px pen INSIDE the window, on
the captured edge, and gets away with it because ``CopyFromScreen`` skips
layered windows.  Qt's grab does not, so this ring sits OUTSIDE the region:
the window is the region plus 5 px all round, masked to the ring alone, so
its middle is click-through, never painted, and never captured.

Shared by both skins.  All state is the App's: which device is casting, where,
and whether the frame is hidden come from ``LcdSnapshot`` and the screencast
events, and an edit is sent as ``SetScreencastRegion`` -- so a frame follows
an edit made in any UI.  Wayland has no frame: a window there cannot place
itself on screen, so the UIs offer the drag picker instead.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from ..core.commands import (
    GetPlatformInfo,
    LcdSnapshot,
    ListDevices,
    SetScreencastRegion,
)
from ..core.events import Event, ScreencastRegionChanged
from ..core.geometry import lock_region_to_panel
from ..core.logs import per_frame
from ..core.ports import CommandBus
from .bus_bridge import BusBridge

if TYPE_CHECKING:
    from ..core.results import ScreencastResult
    from .screen_overlay import RegionSelectOverlay

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The C#'s pen: 5 px, RGB(200, 200, 200) (``FormScreenshot.cs:41``).
RING = 5
_RING_COLOUR = QColor(200, 200, 200)
#: The App's display servers on which a window can place itself.
_PLACEABLE = frozenset({"x11", "native"})


def frames_supported(app: CommandBus) -> bool:
    """Whether a frame can be shown: the App's session lets a window place
    itself, and this UI's Qt is not a Wayland client.  The one rule -- the
    fleet and qtgui's hide checkbox both read it."""
    server = app.dispatch(GetPlatformInfo()).display_server
    qt_platform = QGuiApplication.platformName()
    supported = server in _PLACEABLE and qt_platform != "wayland"
    log.info("frames_supported: display_server=%s qt=%s -> %s", server,
             qt_platform, supported)
    return supported


def open_picker(parent: QWidget | None = None) -> RegionSelectOverlay:
    """Drag a new region over a frozen screen.  The caller connects
    ``region_selected`` -- normally to :func:`store_picked_region`."""
    from .screen_overlay import RegionSelectOverlay

    log.info("open_picker: parent=%s", type(parent).__name__)
    overlay = RegionSelectOverlay(parent)
    overlay.show()
    return overlay


def store_picked_region(app: CommandBus, key: str, x: int, y: int, w: int,
                        h: int) -> ScreencastResult:
    """Fit a picked rectangle to the canvas the cast fills, and store it.

    The one place both skins' pickers end: the C#'s viewfinder is PRE-SIZED
    from the panel, so what the user framed is what appears; a free-form
    drag keeps that guarantee only once its shape is the canvas's.
    """
    snap = app.dispatch(LcdSnapshot(key=key))
    canvas = snap.screencast_canvas if snap.ok else None
    x, y, w, h = lock_region_to_panel(canvas, x, y, w, h)
    log.info("store_picked_region: %s -> (%d,%d %dx%d) on %s", key, x, y, w, h,
             canvas)
    return app.dispatch(SetScreencastRegion(key=key, x=x, y=y, w=w, h=h))


class ScreencastViewfinder(QWidget):
    """One device's frame: a ring round its region, dragged to move it."""

    #: The drag ended: the device key and the region's new top-left.
    released = Signal(str, int, int)
    #: A double-click: hide the frame (the App's flag, as the C# sets it).
    hide_requested = Signal(str)

    def __init__(self, key: str) -> None:
        super().__init__(None, Qt.WindowType.Window
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        log.debug("ScreencastViewfinder.__init__: %s", key)
        self._key = key
        self._grab: QPoint | None = None
        self._moved = False
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setWindowTitle(f"TRCC screencast {key}")

    def frame(self, x: int, y: int, w: int, h: int) -> None:
        """Ring the region ``(x, y, w, h)`` on screen."""
        log.info("ScreencastViewfinder.frame: %s (%d,%d %dx%d)", self._key,
                 x, y, w, h)
        outer_w, outer_h = w + 2 * RING, h + 2 * RING
        self.setGeometry(x - RING, y - RING, outer_w, outer_h)
        self.setMask(QRegion(0, 0, outer_w, outer_h).subtracted(
            QRegion(RING, RING, w, h)))

    def region_origin(self) -> tuple[int, int]:
        """The region's top-left, read off where the window is now."""
        origin = (self.x() + RING, self.y() + RING)
        log.debug("ScreencastViewfinder.region_origin: %s %s", self._key, origin)
        return origin

    def paintEvent(self, event) -> None:
        """Fill the window; the mask leaves only the ring to be seen."""
        frame_log.debug("ScreencastViewfinder.paintEvent: %s", self._key)
        QPainter(self).fillRect(self.rect(), _RING_COLOUR)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = event.position().toPoint()
            self._moved = False
        log.debug("ScreencastViewfinder.mousePressEvent: %s grab=%s",
                  self._key, self._grab)

    def mouseMoveEvent(self, event) -> None:
        frame_log.debug("ScreencastViewfinder.mouseMoveEvent: dragging=%s",
                        self._grab is not None)
        if self._grab is not None:
            self.move(event.globalPosition().toPoint() - self._grab)
            self._moved = True

    def mouseReleaseEvent(self, event) -> None:
        """The end of a drag is the edit -- a window-manager move never is."""
        moved = self._grab is not None and self._moved
        log.info("ScreencastViewfinder.mouseReleaseEvent: %s moved=%s at %s",
                 self._key, moved, self.pos())
        self._grab = None
        if moved:
            self.released.emit(self._key, *self.region_origin())

    def mouseDoubleClickEvent(self, event) -> None:
        log.info("ScreencastViewfinder.mouseDoubleClickEvent: %s", self._key)
        self.hide_requested.emit(self._key)

    def closeEvent(self, event) -> None:
        """The C# cancels its close (``FormScreenshot_FormClosing``): the frame
        goes when the App says -- the cast stops or the flag hides it."""
        log.info("ScreencastViewfinder.closeEvent: %s ignored", self._key)
        event.ignore()


class ViewfinderFleet:
    """Every device's frame, shown iff that device casts and the flag allows."""

    def __init__(self, app: CommandBus, bus: BusBridge) -> None:
        self._app = app
        #: Held, not borrowed: the bridge's signals are this fleet's only
        #: input, and a bridge nothing references is collected with them.
        self._bus = bus
        self._frames: dict[str, ScreencastViewfinder] = {}
        #: Off (Wayland), there is no frame at all; the UIs pick instead.
        self.enabled = frames_supported(app)
        log.info("ViewfinderFleet.__init__: frames %s",
                 "on" if self.enabled else "off")
        if not self.enabled:
            return
        queued = Qt.ConnectionType.QueuedConnection
        bus.screencast_started.connect(self._on_cast_event, type=queued)
        bus.screencast_stopped.connect(self._on_cast_event, type=queued)
        bus.settings_changed.connect(self._on_settings_changed, type=queued)
        for entry in app.dispatch(ListDevices()).devices:
            self._follow(entry.key)

    def frame_for(self, key: str) -> ScreencastViewfinder | None:
        """*key*'s frame, if one has ever been shown."""
        log.debug("ViewfinderFleet.frame_for: %s", key)
        return self._frames.get(key)

    def close(self) -> None:
        """The UI is going: take every frame down with it."""
        log.info("ViewfinderFleet.close: %d frame(s)", len(self._frames))
        for frame in self._frames.values():
            frame.hide()
            frame.deleteLater()
        self._frames.clear()

    def _on_cast_event(self, event: Event) -> None:
        log.debug("ViewfinderFleet._on_cast_event: %s", type(event).__name__)
        self._follow(getattr(event, "key", ""))

    def _on_settings_changed(self, event: Event) -> None:
        """Only a region or flag change moves a frame; the rest is not ours."""
        if isinstance(event, ScreencastRegionChanged):
            log.debug("ViewfinderFleet._on_settings_changed: %s", event.key)
            self._follow(event.key)

    def _follow(self, key: str) -> None:
        """Show, move or hide *key*'s frame to match the App."""
        snap = self._app.dispatch(LcdSnapshot(key=key)) if key else None
        rect = snap.screencast_rect if snap is not None and snap.ok else None
        show = (rect is not None and snap is not None
                and snap.screencast_region is not None
                and not snap.screencast_hide_border)
        log.info("ViewfinderFleet._follow: %s rect=%s show=%s", key, rect, show)
        frame = self._frames.get(key)
        if not show or rect is None:
            if frame is not None:
                frame.hide()
            return
        if frame is None:
            frame = self._frames[key] = ScreencastViewfinder(key)
            frame.released.connect(self._on_released)
            frame.hide_requested.connect(self._on_hide_requested)
        frame.frame(*rect)
        frame.show()

    def _on_released(self, key: str, x: int, y: int) -> None:
        """A drag ended: the region moves there, its size unchanged."""
        snap = self._app.dispatch(LcdSnapshot(key=key))
        log.info("ViewfinderFleet._on_released: %s -> (%d,%d) rect=%s", key,
                 x, y, snap.screencast_rect)
        if snap.ok and snap.screencast_rect is not None:
            _, _, w, h = snap.screencast_rect
            self._app.dispatch(SetScreencastRegion(key=key, x=x, y=y, w=w, h=h))

    def _on_hide_requested(self, key: str) -> None:
        """A double-click: the App's flag hides the frame, in every UI."""
        snap = self._app.dispatch(LcdSnapshot(key=key))
        log.info("ViewfinderFleet._on_hide_requested: %s rect=%s", key,
                 snap.screencast_rect)
        if snap.ok and snap.screencast_rect is not None:
            self._app.dispatch(SetScreencastRegion(
                key=key, x=snap.screencast_rect[0], y=snap.screencast_rect[1],
                w=snap.screencast_rect[2], h=snap.screencast_rect[3],
                hide_border=True))
