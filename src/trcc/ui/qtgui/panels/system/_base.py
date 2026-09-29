"""Shared base for the System panel's group boxes.

:class:`SystemPanel` was one widget holding six unrelated concerns —
platform identity, GPU choice, maintenance, health, live sensors and the
dashboard layout — at 28 methods, a 65% outlier in its own skin.  This is
the same split ``panels/led/`` already applies to ``uc_led_control``: a
thin host plus one focused class per concern.

A box owns the App reference so it can :meth:`dispatch` and the ``BusBridge``
so it can OBSERVE — the two doors every other qtgui surface already has, and
the pair ``BasePanel`` hands its own subclasses.  It states its own ``TITLE``
and ``STRETCH`` so the host never spells them, and builds itself
in :meth:`_build_ui` — which also loads whatever it shows.  Build and load
are one step on purpose: a box that painted before it read would show "…"
to anyone who never called a second method.

**There is deliberately no ``refresh`` hook here**, and holding the bus does
not add one.  One box is live, and the host wires that one method to the
broadcast by name.  A base-level hook looped over every box would put
``ListMemorySlots`` — which shells out to ``dmidecode`` — on the live path,
and would add six DEBUG records per broadcast to the log a reporter pastes.
Keeping the subscription off the base makes both mistakes unrepresentable
rather than merely discouraged.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, TypeVar

from PySide6.QtWidgets import QGroupBox, QWidget

from .....core.results import Result

if TYPE_CHECKING:
    from .....core.commands import Command
    from .....core.ports import CommandBus
    from ....bus_bridge import BusBridge

log = logging.getLogger(__name__)

R = TypeVar("R", bound=Result)


class SystemBox(QGroupBox):
    """One concern of the System panel, as a titled group box."""

    #: Group-box caption.  Data, so the host builds every box the same way.
    TITLE: ClassVar[str] = ""

    #: Layout stretch the host gives this box.  0 = size to content.
    STRETCH: ClassVar[int] = 0

    def __init__(
        self,
        app: CommandBus,
        bus: BusBridge,
        parent: QWidget | None = None,
    ) -> None:
        log.debug("__init__: app=%s bus=%s parent=%s", app, bus, parent)
        super().__init__(self.TITLE, parent)
        self._app = app
        self._bus = bus
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the widgets AND load what they show.  Called by __init__."""
        log.debug("_build_ui")
        raise NotImplementedError(
            f"{type(self).__name__} must implement _build_ui()"
        )

    def dispatch(self, command: Command[R]) -> R:
        """Run *command* on the App — the box's only door to the core."""
        log.debug("dispatch: command=%s", command)
        return self._app.dispatch(command)
