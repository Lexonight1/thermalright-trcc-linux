"""BasePanel — common contract for every next/ GUI panel.

Architectural role: ports/adapters analogue of legacy ``gui/base.py``,
shrunk to the parts panels actually need.

Every panel:
* holds a reference to the ``App`` (so it can dispatch Commands);
* holds the ``BusBridge`` (so it can subscribe to typed Qt signals
  forwarded from the EventBus);
* implements ``_setup_ui()`` to build its widget tree;
* may override ``apply_language(code)`` to re-render localized strings
  (default no-op);
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
    from ...app import App
    from ...core.commands import Command
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)


R = TypeVar("R", bound=Result)


class BasePanel(QFrame):
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
        app: App,
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

    # ── Visibility ────────────────────────────────────────────────────
    #
    # qtgui stacks its panels (``app.py:102``), so all but one are hidden at
    # any moment — and nothing stopped them.  ``stop_periodic_updates`` had
    # ZERO callers and there were no show/hide hooks in the skin, while
    # ``ui/gui`` has gated on visibility all along (``trcc_app.py:342``).
    #
    # This lives on the PANEL, not on ``PeriodicUpdater``: an updater may
    # drive the PHYSICAL DEVICE, and gating it on visibility would freeze a
    # panel whenever the window is hidden — worse than the waste it fixes.
    # (Video was the case in point until 2026-09-25; the core's VideoLoop
    # drives it now, #249, so no window-owned updater touches the wire.)

    def showEvent(self, event: object) -> None:
        log.debug("showEvent: %s back on screen", type(self).__name__)
        super().showEvent(event)      # type: ignore[arg-type]
        self._updates.resume()

    def hideEvent(self, event: object) -> None:
        log.debug("hideEvent: %s left the screen", type(self).__name__)
        super().hideEvent(event)      # type: ignore[arg-type]
        self._updates.suspend()

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

    def apply_language(self, lang: str) -> None:
        """Re-render localized strings.  Default no-op."""
        log.debug("apply_language: lang=%s", lang)
        del lang

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
    def app(self) -> App:
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
        self._updates.start(interval_ms, callback)

    def stop_periodic_updates(self) -> None:
        """Stop the periodic update timer if running."""
        log.debug("stop_periodic_updates")
        self._updates.stop()
