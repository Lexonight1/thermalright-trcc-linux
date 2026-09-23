"""Shared substrate for the qtgui asset browsers.

The local-theme browser and the mask browser are the same panel wearing two
labels: pick a device, show a thumbnail grid of what's installed for it, apply
the selection.  Only the *asset kind* differs.  What they genuinely share lives
here — the grid widget's configuration and the "what canvas is this device?"
lookup — so a fix to either lands in both.

The gui skin has had a ``BaseThemeBrowser`` since it was written; this is qtgui
catching up to a pattern that skin already established, not a new invention.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QListWidget

from ....core.commands import DeviceCanvas
from ..base import BasePanel
from ..device_picker import DevicePickerWidget
from ..device_selection import DeviceSelection

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from ....app import App
    from ...bus_bridge import BusBridge

log = logging.getLogger(__name__)

# Thumbnail-grid geometry — parity with the gui skin's browser.
_ICON_SIZE = QSize(96, 96)
_GRID_SIZE = QSize(124, 140)
_GRID_SPACING = 6


class AssetBrowserPanel(BasePanel):
    """A device picker + a thumbnail grid of that device's assets.

    Subclasses build their own layout in ``_setup_ui`` and call
    :meth:`_build_asset_list` for the grid; they must define ``_on_apply``
    (what a double-click does) and own a ``self._status`` label, which
    :meth:`_target_resolution` writes to when it can't resolve a canvas.
    """

    #: Intermediate panel — ``BasePanel.__init_subclass__`` enforces
    #: ``_setup_ui`` on concrete panels only, and this one has no layout
    #: of its own to build.
    _abstract: ClassVar[bool] = True

    _status: QLabel
    _picker: DevicePickerWidget

    def _device_key(self) -> str | None:
        """The picked device, or ``None`` having told the user what to do.

        Lived verbatim in two browsers, INCLUDING the sentence the user reads.
        A duplicated user-facing string is the worst kind: improving the
        wording in one place leaves the other saying something else, and
        nothing fails.
        """
        key = self._picker.current_key()
        if not key:
            log.debug("_device_key: no device picked")
            self._status.setText(
                "Pick a device first.  Open the Devices panel to scan "
                "if no devices are listed.",
            )
            return None
        return key

    def __init__(
        self,
        app: App,
        bus: BusBridge,
        parent: QWidget | None = None,
        *,
        selection: DeviceSelection | None = None,
    ) -> None:
        super().__init__(app, bus, parent, selection=selection)
        # The first-run archives download in the background now, so this grid
        # is built BEFORE its assets exist and would otherwise stay empty for
        # the whole session.  The gui skin re-lists via ``notify_data_ready``;
        # qtgui had no equivalent at all until #275.  Queued: the event is
        # published from the install worker thread.
        log.info("%s: subscribing to DataInstalled", type(self).__name__)
        bus.data_installed.connect(
            self._on_data_installed,
            type=Qt.ConnectionType.QueuedConnection,
        )

    def _on_data_installed(self, event: object) -> None:
        """First-run archives landed — re-list the grid.  (#275)"""
        log.info("_on_data_installed: %s → refreshing %s",
                 getattr(event, "resolution", None), type(self).__name__)
        self.refresh()

    def refresh(self) -> None:
        """Re-list the grid from disk.  Every asset browser implements it."""
        log.error("%s does not implement refresh() — its grid cannot "
                  "re-list when the first-run data lands", type(self).__name__)
        raise NotImplementedError(
            f"{type(self).__name__} must implement refresh()"
        )

    def _build_asset_list(self) -> QListWidget:
        """The thumbnail grid, configured identically for every asset kind.

        Each entry shows its preview image instead of a bare text row, which
        is what makes the qtgui browsers legible at a glance the way the gui
        skin's are.
        """
        log.debug("_build_asset_list")
        widget = QListWidget(self)
        widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        widget.setViewMode(QListWidget.ViewMode.IconMode)
        widget.setIconSize(_ICON_SIZE)
        widget.setGridSize(_GRID_SIZE)
        widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        widget.setMovement(QListWidget.Movement.Static)
        widget.setSpacing(_GRID_SPACING)
        widget.setWordWrap(True)
        return widget

    def _on_item_double_clicked(self, _item: object) -> None:
        """Double-click applies the selection.

        A named slot rather than a lambda so the signal's item argument is
        absorbed in one obvious place instead of at every connect site.
        """
        log.debug("_on_item_double_clicked: _item=%s", _item)
        self._on_apply()

    def _on_apply(self) -> None:
        """Apply the current selection — every browser defines its own."""
        log.debug("_on_apply")
        raise NotImplementedError(
            f"{type(self).__name__} must implement _on_apply()",
        )

    def _target_resolution(self, key: str) -> tuple[int, int] | None:
        """The canvas to author an asset for, or None with a status message.

        One dispatch.  This used to walk the ladder itself — handshake, then
        the scanned ``native_resolution``, then ``find_product`` — which was
        a SECOND copy of the one in the Command layer (``native_canvas``,
        behind :class:`DeviceCanvas`) and it gated differently: this required
        a CONNECTED device where the Command accepts an ATTACHED one, so a
        panel that had answered a handshake and since dropped authored at its
        registry size here and its real size there.  A widget reaching into
        ``core.registry`` was also the import CLAUDE.md forbids outright.
        """
        result = self.dispatch(DeviceCanvas(key=key))
        if not result.ok:
            log.warning("_target_resolution: no canvas for %s", key)
            self._status.setText(
                f"No canvas known for {key} — connect the device first so we "
                "know the target resolution.",
            )
            return None
        log.debug("_target_resolution: %s → %dx%d (from %s)",
                  key, result.width, result.height, result.source)
        return (result.width, result.height)
