"""Shared base class for LED sub-tabs.

Every tab needs:
* a back-reference to the App for ``dispatch``;
* a getter for "what device key am I editing right now?" — the
  outer :class:`LedPanel` owns that input, sub-tabs read it on
  demand via :meth:`_key_provider`;
* a hook to refresh from the current ``LedDeviceSettings`` when the
  user switches keys or another UI mutates the same device.

Putting that in one place keeps the tabs themselves under ~150 lines.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from .....core.results import LedSnapshotResult

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .....core.commands import Command
    from .....core.ports import CommandBus


KeyProvider = Callable[[], str]
"""Returns the current device key, or empty string if none picked."""


class LedTabBase(QWidget):
    """Abstract base — owns the App reference + key provider plumbing.

    Sub-tabs implement :meth:`_build_ui` (call from their own ``__init__``)
    and :meth:`refresh_from` to re-render when settings change.  All
    Command dispatching goes through :meth:`_dispatch` so subclasses
    don't have to reach through ``self._app``.
    """

    def __init__(
        self,
        app: CommandBus,
        key_provider: KeyProvider,
        parent: QWidget | None = None,
    ) -> None:
        log.debug("__init__: app=%s key_provider=%s", app, key_provider)
        super().__init__(parent)
        self._app = app
        self._key_provider = key_provider

    # ── For subclasses ───────────────────────────────────────────────

    #: Set by the tabs that can be EMPTY for a given device (zone, segment).
    #: ``LedPanel`` asks only those two whether to surface their tab, so this
    #: is a contract shared by that pair rather than a default for every tab —
    #: a base default would answer for tabs that never manage a placeholder.
    _placeholder_visible: bool = False

    def has_visible_content(self) -> bool:
        """Whether ``LedPanel`` should surface this tab for the current device.

        Hiding beats showing an empty editor: a device with one zone has
        nothing for the zone tab to edit, and an empty grid reads as broken.
        """
        log.debug("has_visible_content")
        return not self._placeholder_visible

    def current_key(self) -> str:
        log.debug("current_key")
        return self._key_provider()

    def _dispatch(self, command: Command):
        log.debug("_dispatch: command=%s", command)
        return self._app.dispatch(command)

    # ── Hook for refresh ─────────────────────────────────────────────

    def refresh_from(self, snapshot: LedSnapshotResult | None) -> None:
        """Re-render from a fresh settings snapshot.

        Default is a no-op; subclasses override to update widgets from
        persisted state (e.g. when the user switches device keys).
        """
        log.debug("refresh_from")
        del snapshot
