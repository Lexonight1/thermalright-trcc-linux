"""PreviewSurface — the device preview, as persistent chrome.

**Why this is a widget and not a panel.**  ``ui/gui`` keeps the preview on
screen at all times next to whatever tool the user is holding: the preview
occupies x 196-696 of a fixed 1454x800 window and the tool stack x 712-1444,
so changing a colour and seeing the result is ONE screen with no navigation.
qtgui made the preview a destination in a ``QStackedWidget`` of thirteen, so
editing an overlay colour meant leaving the preview, opening a dialog, and
coming back to find out what happened.

Two consequences fall straight out of that choice, and both were reported as
separate problems: the edit->see loop is broken for colour, position, size and
font; and drag-to-position an element has nowhere to live, because there is no
preview on the editing screen to drag ON.

So the surface is extracted from :class:`~trcc.ui.qtgui.panels.preview_panel.PreviewPanel`
and owned by the window.  It renders whatever the window's
:class:`~trcc.ui.qtgui.device_selection.DeviceSelection` points at -- it has no
picker of its own, because the rail is the one place a device is chosen.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.commands import BuildPreview
from ...core.logs import per_frame
from ..presentation.lcd_panel import lcd_panel_for
from ..qt_periodic import PeriodicUpdater
from .assets import Assets

if TYPE_CHECKING:
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge
    from .device_selection import DeviceSelection

log = logging.getLogger(__name__)
#: Fires once per frame the device sends — TRACE rung, never the -v rung.
frame_log = per_frame(__name__)

#: Re-render cadence.  Overlay metrics are live, so a static theme still
#: changes between frames.
REFRESH_MS = 1000
#: Max edge of the preview pixmap in window pixels.
PREVIEW_MAX = 480
#: Edge of the bezel container — ``ui/gui``'s ``Sizes.PREVIEW_FRAME``.  The
#: offsets in ``_PREVIEW_OFFSETS`` are expressed inside a box of this size, so
#: it is not a free choice.
FRAME_EDGE = 500


class PreviewSurface(QWidget):
    """Live render of the selected device, always on screen."""

    #: (width, height) of the render that just landed — the DEVICE's canvas,
    #: not the scaled pixmap.  The state readout used to report the pixmap's
    #: size under the label "Render size", so a 1600x720 panel read "480x216".
    rendered = Signal(int, int)

    #: "" until a Result proves the live surface cannot reach this process,
    #: then "png" for the life of the widget.  An observation, not a configured
    #: mode and not a sniffed environment.
    _encode: Literal["", "png"] = ""

    def __init__(
        self,
        app: CommandBus,
        bus: BusBridge,
        selection: DeviceSelection,
        parent: QWidget | None = None,
    ) -> None:
        log.debug("PreviewSurface.__init__: selection=%s", selection)
        super().__init__(parent)
        self._app = app
        self._bus = bus
        self._selection = selection
        self._updates = PeriodicUpdater(self)

        # The BEZEL: a fixed 500x500 container carrying that panel's frame
        # image, with the live render inset at the frame's cutout.  A bare
        # rectangle was never what ``ui/gui`` showed, and the sizes are not a
        # free choice -- ``_PREVIEW_OFFSETS`` expresses every offset inside a
        # box of exactly this edge.
        self._frame = QLabel(self)
        self._frame.setFixedSize(FRAME_EDGE, FRAME_EDGE)
        self._frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel(self._frame)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._panel: tuple[int, int] | None = None
        self._apply_panel((320, 320))    # the model's own fallback panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._frame)
        layout.addStretch(1)
        self.set_placeholder("Pick a device on the left to see its output.")

        selection.changed.connect(self._on_device_changed)
        self._updates.start(REFRESH_MS, self.refresh)
        # A frame on the wire means state changed somewhere -- another UI, the
        # API, a driver -- so re-render rather than wait for the next tick.
        self._bus.frame_sent.connect(
            self._on_frame_sent, type=Qt.ConnectionType.QueuedConnection,
        )

    # ── The bezel ──────────────────────────────────────────────────────

    def _apply_panel(self, resolution: tuple[int, int]) -> None:
        """Dress the container in *resolution*'s frame and inset the render.

        Idempotent per resolution: the render path calls this on every frame
        and only a panel CHANGE costs anything.
        """
        if resolution == self._panel:
            return
        left, top, width, height, asset = lcd_panel_for(resolution).offset_info
        log.info("_apply_panel: %dx%d — frame=%s render at (%d,%d) %dx%d",
                 *resolution, asset, left, top, width, height)
        pixmap = Assets.pixmap(asset)
        if not pixmap.isNull():
            self._frame.setPixmap(pixmap.scaled(
                FRAME_EDGE, FRAME_EDGE,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self._label.setFixedSize(width, height)
        self._label.move(left, top)
        self._panel = resolution

    # ── Reacting ───────────────────────────────────────────────────────

    def _on_device_changed(self, key: str) -> None:
        log.info("PreviewSurface: now showing %r", key)
        self.refresh()

    def _on_frame_sent(self, event: object) -> None:
        # Per-frame: logged through the ``trcc.frame`` family so it is silent
        # at the default rung and cannot drown the one-shot lines a report is
        # read for.
        mine = getattr(event, "key", None) == self._selection.key
        frame_log.debug("PreviewSurface._on_frame_sent: mine=%s", mine)
        if mine:
            self.refresh()

    # ── Render ─────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Re-render the selected device, or explain why there is nothing."""
        key = self._selection.key
        if not key:
            log.debug("PreviewSurface.refresh: no device selected")
            return
        preview = self._app.dispatch(BuildPreview(key=key, encode=self._encode))
        if (not self._encode and preview.ok and preview.width
                and preview.surface is None and not preview.image):
            # A frame WAS rendered but no carrier arrived: the live surface
            # cannot cross the daemon socket.  Ask for bytes and remember.
            # ``width`` is the discriminator -- BuildPreview sets it only on
            # the success path, so this cannot be confused with "no theme".
            log.info("PreviewSurface: %s rendered %dx%d with no surface — "
                     "switching to PNG bytes (daemon mode)",
                     key, preview.width, preview.height)
            self._encode = "png"
            preview = self._app.dispatch(BuildPreview(key=key, encode="png"))
        if not preview.ok:
            self.set_placeholder(f"No data for {key} — {preview.message}")
            return
        if preview.surface is None and not preview.image:
            self.set_placeholder(f"Load a theme for {key} to see it here.")
            return
        self._apply_panel((preview.width, preview.height))
        pixmap = surface_to_pixmap(preview.surface, self._label.width(),
                                   self._label.height(),
                                   encoded=preview.image)
        if pixmap is None:
            self.set_placeholder("Preview surface couldn't be rendered.")
            return
        self._label.setPixmap(pixmap)
        self._label.setText("")
        self.rendered.emit(preview.width, preview.height)

    def set_placeholder(self, text: str) -> None:
        """Show *text* instead of an image."""
        log.debug("PreviewSurface.set_placeholder: %s", text)
        self._label.clear()
        self._label.setText(text)
        font = QFont()
        font.setPointSize(11)
        self._label.setFont(font)
        self._label.setStyleSheet(
            "background-color: #111; color: #aaa; border: 1px solid #333; "
            "padding: 16px;",
        )
        self._label.setWordWrap(True)


def surface_to_pixmap(
    surface: object, width: int, height: int, *, encoded: bytes = b"",
) -> QPixmap | None:
    """QtRenderer surfaces are QImage — convert + scale.

    ``encoded`` is the wire carrier: over the daemon socket the live surface
    is dropped and the Result carries PNG bytes instead.  Decoding them here
    keeps the caller on ONE path rather than branching on transport, and needs
    no Renderer port -- this module IS the Qt adapter.

    Falls back to None on unexpected types so the caller surfaces a friendly
    placeholder rather than a crash.
    """
    if not isinstance(surface, QImage):
        if not encoded:
            return None
        surface = QImage.fromData(encoded)
        if surface.isNull():
            log.warning("surface_to_pixmap: %d byte(s) would not decode",
                        len(encoded))
            return None
    pixmap = QPixmap.fromImage(surface)
    if (pixmap.width(), pixmap.height()) != (width, height):
        # IgnoreAspectRatio, like ``ui/gui``'s ImageLabel: the cutout IS the
        # panel's aspect, so fitting to it is not a distortion -- and letting
        # it letterbox instead would leave bezel-coloured bars inside the
        # screen area.
        pixmap = pixmap.scaled(
            width, height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pixmap
