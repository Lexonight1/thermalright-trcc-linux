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

from ..base import BasePanel

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

    def __init__(
        self,
        app: App,
        bus: BusBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(app, bus, parent)
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
        self._on_apply()

    def _on_apply(self) -> None:
        """Apply the current selection — every browser defines its own."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _on_apply()",
        )

    def _target_resolution(self, key: str) -> tuple[int, int] | None:
        """The canvas to author an asset for, or None with a status message.

        Three sources in descending truthfulness: what the device reported at
        handshake, its registry ``native_resolution``, and — before anything is
        attached — the product registry keyed by the ``vid:pid`` string.  A
        device that resolves to none of those can't be authored for, and the
        status label says why rather than failing silently.
        """
        device = self.app.devices.get(key)
        if device is not None:
            if device.profile is not None:
                return device.profile.resolution
            if device.info.native_resolution != (0, 0):
                return device.info.native_resolution

        try:
            vid_s, pid_s = key.split(":")
            vid = int(vid_s, 16)
            pid = int(pid_s, 16)
        except ValueError:
            log.warning("_target_resolution: %r is not vid:pid shaped", key)
            self._status.setText(
                f"Device key {key!r} isn't shaped like 'vid:pid'.",
            )
            return None

        from ....core.registry import find_product
        product = find_product(vid, pid)
        if product is None or product.native_resolution == (0, 0):
            log.warning("_target_resolution: no registry canvas for %s", key)
            self._status.setText(
                f"No registry entry for {key} — connect the device first "
                "so we know the target resolution.",
            )
            return None
        log.debug("_target_resolution: %s → %s (registry)",
                  key, product.native_resolution)
        return product.native_resolution
