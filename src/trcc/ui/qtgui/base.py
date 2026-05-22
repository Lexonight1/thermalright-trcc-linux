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

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QWidget

from ...core.results import Result

if TYPE_CHECKING:
    from ...app import App
    from ...core.commands import Command
    from .bus_bridge import BusBridge

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
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._bus = bus
        self._update_timer: QTimer | None = None
        self._setup_ui()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Reject concrete subclasses that forget to implement _setup_ui."""
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
        raise NotImplementedError(
            f"{type(self).__name__} must implement _setup_ui()"
        )

    def apply_language(self, lang: str) -> None:
        """Re-render localized strings.  Default no-op."""
        del lang

    def get_state(self) -> dict:
        """Serialize panel state for save / restore.  Default empty."""
        return {}

    def set_state(self, state: dict) -> None:
        """Restore panel state from a previously saved dict.  Default no-op."""
        del state

    # ── Concrete helpers ───────────────────────────────────────────────

    def dispatch(self, command: Command[R]) -> R:
        """Run *command* on the App.  Convenience over ``self._app.dispatch``."""
        return self._app.dispatch(command)

    @property
    def app(self) -> App:
        return self._app

    @property
    def bus(self) -> BusBridge:
        return self._bus

    def start_periodic_updates(
        self,
        interval_ms: int,
        callback: Callable[[], None],
    ) -> None:
        """Run *callback* every *interval_ms* on the Qt main thread.

        Stopping + restarting cleans up the prior timer + connection so
        subscribers can repeat-call with a new cadence.
        """
        if self._update_timer is None:
            self._update_timer = QTimer(self)
        else:
            self._update_timer.stop()
            try:
                self._update_timer.timeout.disconnect()
            except RuntimeError:
                # No previous connection — fresh timer, ignore.
                pass
        self._update_timer.timeout.connect(callback)
        self._update_timer.start(interval_ms)

    def stop_periodic_updates(self) -> None:
        if self._update_timer is not None:
            self._update_timer.stop()
