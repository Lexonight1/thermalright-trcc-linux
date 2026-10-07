"""BasePanel — common contract for every next/ GUI panel.

Architectural role: ports/adapters analogue of legacy ``gui/base.py``,
shrunk to the parts panels actually need.

Every panel:
* holds a reference to the ``App`` (so it can dispatch Commands);
* holds the ``BusBridge`` (so it can subscribe to typed Qt signals
  forwarded from the EventBus);
* implements ``_setup_ui()`` to build its widget tree;
* may override ``get_state()`` / ``set_state(dict)`` for save / restore.

Why a metaclass-style enforcement: legacy hit ``TypeError`` when QFrame
+ ABC mixed (PySide6 uses ``sip.wrappertype`` as metaclass).
``__init_subclass__`` gives us the same "must implement _setup_ui"
guarantee without the metaclass conflict — works on every Qt version.

The ``dispatch`` helper threads each Command through the App so panels
don't need to ``self._app.dispatch(...)`` every line — looks like
``self.dispatch(SetBrightness(...))`` instead.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QWidget

from ...core.results import Result
from ..qt_periodic import PeriodicUpdater
from .device_selection import DeviceSelection

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)


R = TypeVar("R", bound=Result)


class TicksWhileShown:
    """Periodic updates that run only while the widget is on screen.

    qtgui stacks its panels (``app.py:102``), so all but one are hidden at
    any moment, and the window itself hides to the tray on close.  A timer
    that kept going there dispatched a full ``BuildPreview`` every second for
    nobody: measured 2026-09-30 on the preview, 3 renders in 3 s after the
    window closed, the same as while shown.

    This lives on the WIDGET, not on ``PeriodicUpdater``, which the gui skin
    shares without show/hide hooks.  No window-owned updater drives the
    device (the core's loops do, #249), so pausing one never freezes a panel.
    A widget that is not on screen when it starts waits for its first show.
    """

    _updates: PeriodicUpdater

    if TYPE_CHECKING:
        # Declared for the type checker only: defined for real it would come
        # FIRST in the MRO and shadow the QWidget's own isVisible.
        def isVisible(self) -> bool: ...

    def _start_updates(self, interval_ms: int,
                       callback: Callable[[], None]) -> None:
        self._updates.start(interval_ms, callback)
        if not self.isVisible():
            log.debug("_start_updates: %s not on screen yet — waiting",
                      type(self).__name__)
            self._updates.suspend()

    def showEvent(self, event: object) -> None:
        log.debug("showEvent: %s back on screen", type(self).__name__)
        super().showEvent(event)      # type: ignore[misc]
        self._updates.resume()

    def hideEvent(self, event: object) -> None:
        log.debug("hideEvent: %s left the screen", type(self).__name__)
        super().hideEvent(event)      # type: ignore[misc]
        self._updates.suspend()


class BasePanel(TicksWhileShown, QFrame):
    """Common QFrame substrate for every TRCC GUI panel.

    Subclasses receive ``app`` + ``bus`` via __init__, build their UI in
    ``_setup_ui()``, and dispatch Commands via ``self.dispatch``.  The
    panel-changed signal lets MainWindow listen for navigation events
    (e.g. "user selected the Theme tab").
    """

    # Fired when the panel wants the parent to navigate elsewhere.
    # Payload is a panel name (free-form string; MainWindow knows the set).
    navigate = Signal(str)

    def __init__(
        self,
        app: CommandBus,
        bus: BusBridge,
        parent: QWidget | None = None,
        *,
        selection: DeviceSelection | None = None,
    ) -> None:
        log.debug("__init__: app=%s bus=%s selection=%s", app, bus, selection)
        super().__init__(parent)
        self._app = app
        self._bus = bus
        # Assigned BEFORE _setup_ui(): panels build their picker in there and
        # hand it this selection.  A panel built without one (every test that
        # constructs a panel bare) gets a private selection and behaves as it
        # always did -- MainWindow is what makes them share.
        self._selection = selection or DeviceSelection(self)
        self._updates = PeriodicUpdater(self)
        self._setup_ui()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Reject concrete subclasses that forget to implement _setup_ui."""
        log.debug("__init_subclass__")
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("_abstract", False):
            return
        # Allow intermediate abstract panels (e.g. ``BaseThemeBrowser``)
        # to declare ``_abstract = True``.
        has_impl = any(
            "_setup_ui" in klass.__dict__
            for klass in cls.__mro__
            if klass is not BasePanel
        )
        if not has_impl:
            raise TypeError(
                f"{cls.__name__} must implement _setup_ui()"
            )

    # ── Subclass hooks ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Build widgets + lay out the panel.  Called by ``__init__``."""
        log.debug("_setup_ui")
        raise NotImplementedError(
            f"{type(self).__name__} must implement _setup_ui()"
        )

    def get_state(self) -> dict:
        """Serialize panel state for save / restore.  Default empty."""
        log.debug("get_state")
        return {}

    def set_state(self, state: dict) -> None:
        """Restore panel state from a previously saved dict.  Default no-op."""
        log.debug("set_state: state=%s", state)
        del state

    # ── Concrete helpers ───────────────────────────────────────────────

    def dispatch(self, command: Command[R]) -> R:
        """Run *command* on the App.  Convenience over ``self._app.dispatch``."""
        log.debug("dispatch: command=%s", command)
        return self._app.dispatch(command)

    @property
    def device_key(self) -> str:
        """The device this panel edits — one per window, not per widget.

        Read this rather than a panel's own picker: the picker is a VIEW of
        the selection, and step 2 of the workspace rebuild deletes most of
        them in favour of the device rail.
        """
        log.debug("device_key -> %r", self._selection.key)
        return self._selection.key

    @property
    def selection(self) -> DeviceSelection:
        """The shared selection, for panels that build their own picker."""
        log.debug("selection")
        return self._selection

    @property
    def app(self) -> CommandBus:
        log.debug("app")
        return self._app

    @property
    def bus(self) -> BusBridge:
        log.debug("bus")
        return self._bus

    def start_periodic_updates(
        self,
        interval_ms: int,
        callback: Callable[[], None],
    ) -> None:
        """Run *callback* every *interval_ms* on the Qt main thread."""
        log.debug("start_periodic_updates: interval_ms=%s callback=%s", interval_ms, callback)
        self._start_updates(interval_ms, callback)
