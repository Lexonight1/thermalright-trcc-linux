"""BasePanel -- the panel base every Qt GUI's panels share.

A ``QFrame`` with the delegate signal (a panel hands an action to its window),
a fixed size, a periodic-update timer, and the ``_setup_ui`` contract.  It
lived in ``gui/base.py``; the device Settings panel (``ui/settings_panel``),
which every GUI shows, is one, so the base is shared.  ``gui/base.py``
re-exports it, so the gui's own panels name it as before.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame

from .qt_periodic import PeriodicUpdater

log = logging.getLogger(__name__)


class BasePanel(QFrame):
    """Base class for TRCC panels.

    Enforces lifecycle:
        _setup_ui() — abstract, must be implemented by every subclass.
        get_state() / set_state() — virtual hooks, default no-op/empty.

    Provides:
        invoke_delegate() — delegate signal emission (MVC pattern).
        start_periodic_updates() — timer management.
    """

    # Signal for delegate pattern (replaces Tkinter invoke_delegate)
    delegate = Signal(int, object, object)  # cmd, info, data

    def __init__(self, parent=None, width=None, height=None):
        super().__init__(parent)

        # No default stylesheet - let each panel set its own background
        # either via setStyleSheet() or QPalette for image backgrounds

        # Fixed size if specified (matches Windows component sizes)
        if width and height:
            self.setFixedSize(width, height)
        elif width:
            self.setFixedWidth(width)
        elif height:
            self.setFixedHeight(height)

        # Resource directory (legacy)
        self._resource_dir = None
        # Periodic update timer
        self._updates = PeriodicUpdater(self)

    def __init_subclass__(cls, **kwargs):
        """Enforce that concrete subclasses implement _setup_ui()."""
        super().__init_subclass__(**kwargs)
        # Skip enforcement on intermediate abstract classes (e.g. BaseThemeBrowser)
        # by checking if _setup_ui is still the base stub or a real override.
        if '_setup_ui' not in cls.__dict__ and not any(
            '_setup_ui' in base.__dict__ for base in cls.__mro__[1:]
            if base is not BasePanel
        ):
            raise TypeError(
                f"{cls.__name__} must implement _setup_ui()"
            )

    def _setup_ui(self) -> None:
        """Create child widgets and lay out the panel.

        Called by subclass __init__ (not called automatically by BasePanel).
        Must be overridden by every concrete subclass.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement _setup_ui()"
        )

    # === Virtual hooks (default no-op) ===

    def get_state(self) -> dict:
        """Serialize panel state for save/restore."""
        return {}

    def set_state(self, state: dict) -> None:
        """Restore panel state from a previously saved dict."""
        log.debug(
            "%s.set_state: keys=%s (base no-op)",
            type(self).__name__, sorted(state) if isinstance(state, dict) else state,
        )

    # === Concrete helpers ===

    def start_periodic_updates(
        self, interval_ms: int, callback: Callable[[], None],
    ) -> None:
        """Start a periodic timer calling `callback` every `interval_ms`.

        Creates the timer on first call. Subsequent calls restart with
        the new interval and callback.
        """
        self._updates.start(interval_ms, callback)

    # === Legacy ===

    def set_resource_dir(self, path):
        """Set the resource directory for loading images."""
        log.debug("%s.set_resource_dir: %s",
                  type(self).__name__, path)
        self._resource_dir = Path(path) if path else None

    def load_pixmap(self, name):
        """Load a pixmap from the resource directory."""
        if not self._resource_dir:
            log.debug("%s.load_pixmap(%r): no resource_dir bound",
                      type(self).__name__, name)
            return None

        path = self._resource_dir / name
        if path.exists():
            return QPixmap(path.as_posix())
        log.debug("%s.load_pixmap(%r): missing at %s",
                  type(self).__name__, name, path)
        return None

    def invoke_delegate(self, cmd, info=None, data=None):
        """Emit delegate signal (replaces Tkinter invoke_delegate)."""
        log.info("%s.invoke_delegate: cmd=%s info=%r",
                 type(self).__name__, cmd,
                 getattr(info, 'name', info))
        self.delegate.emit(cmd, info, data)
