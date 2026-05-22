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

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from .....core.led_models import LedDeviceSettings

if TYPE_CHECKING:
    from .....app import App
    from .....core.commands import Command


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
        app: App,
        key_provider: KeyProvider,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._key_provider = key_provider

    # ── For subclasses ───────────────────────────────────────────────

    def current_settings(self) -> LedDeviceSettings | None:
        """Return the LedDeviceSettings for the current key, or None."""
        key = self._key_provider()
        if not key:
            return None
        return self._app.settings.for_led(key)

    def current_key(self) -> str:
        return self._key_provider()

    def _dispatch(self, command: Command):
        return self._app.dispatch(command)

    # ── Hook for refresh ─────────────────────────────────────────────

    def refresh_from(self, settings: LedDeviceSettings | None) -> None:
        """Re-render from a fresh settings snapshot.

        Default is a no-op; subclasses override to update widgets from
        persisted state (e.g. when the user switches device keys).
        """
        del settings
