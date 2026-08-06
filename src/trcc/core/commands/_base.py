"""Command base class + Result-typed generic."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar

from ..results import (
    Result,
)

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


R_co = TypeVar("R_co", bound=Result, covariant=True)


log = logging.getLogger(__name__)


class Command(ABC, Generic[R_co]):
    """A user action.  Exactly one execute method; returns one Result.

    Parameterised on the concrete Result subclass so that
    ``app.dispatch(DiscoverDevices())`` is typed as ``DiscoverResult``,
    not the Result base — callers get the subclass's fields (products,
    readings, etc.) without casting.

    ``LOG_LEVEL`` controls how App.dispatch logs the command's entry +
    successful outcome.  Default INFO — a Command changes something, and a
    user-visible change is worth a line.  Per-tick commands (``RenderAndSend``,
    ``SendFrame``, ``RenderLed``, ``TickDisplay``) override to DEBUG: they fire
    dozens of times per second and would drown the log.

    Reads do not belong here — see :class:`Query`, which carries the DEBUG
    default in its type instead of asking each author to remember it.  This
    docstring used to hand-list which classes were DEBUG and drifted six names
    out of date before the split existed.
    """

    LOG_LEVEL: ClassVar[int] = logging.INFO

    @abstractmethod
    def execute(self, app: App) -> R_co: ...


class Query(Command[R_co]):
    """A question.  Answers, and changes nothing.

    A Query is a Command in every mechanical sense — same ``execute``, same
    dispatch, same Result, same IPC envelope — so nothing at the seam needs to
    know the difference.  What it adds is a *contract*:

    * **It must not mutate.**  No event published, no setting written, no file
      created, no device commanded.  Enforced by
      ``tests/test_architecture_boundaries.py``, not by review.
    * **It logs at DEBUG.**  UIs poll reads — a preview panel asks every
      second, an overlay editor on every click — so a read at INFO buries the
      user's actual actions.  The level now follows from the *kind* rather
      than from each author remembering, which is what let ``LcdSnapshot`` sit
      at DEBUG while ``ListSensors`` sat at INFO with no one able to say why.

    Why it exists at all: 122 Commands were built and reads were never a
    first-class idea, so the ones that appeared were retrofitted one at a time.
    A UI that needed to *ask* something often found nothing to call and reached
    past the bus instead — which is how ``DiscoverDevices`` ended up being the
    only device-listing verb despite scanning USB and triggering data installs.
    Naming the kind makes a missing read obvious instead of archaeological.
    """

    LOG_LEVEL: ClassVar[int] = logging.DEBUG
