"""Command base class + Result-typed generic."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import fields
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar, cast, get_args

from ..models import Capability
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
    #: The Command acts on ``self.key``'s device, so ``App.dispatch`` connects
    #: it first -- for every UI.  Each UI used to decide that for itself (the
    #: CLI before 21 commands, the API before 9 routes, 6 routes not at all),
    #: which is how a verb that worked in the CLI failed over the API.
    #: One-shot Commands only: a per-tick one would retry a USB connect at
    #: frame rate while a panel is unplugged; hotplug and the play loops own
    #: reconnecting those.
    USES_DEVICE: ClassVar[bool] = False
    #: Runs in the CALLER's process, never the shared App's -- for every UI.
    #: The App is a detached process with no terminal and its own working
    #: directory, so a Command that is ABOUT that process (stop it, report
    #: whether it runs), needs the user's terminal (``sudo``), or must work
    #: while the App is hung (the debug report) cannot run inside it.  Measured
    #: through the App: ``daemon-status`` STARTED one to answer, ``sudo`` could
    #: not prompt, and ``report`` failed after 37.6 s against a hung App.
    RUNS_IN_CALLER: ClassVar[bool] = False
    #: What ``self.key``'s device must be able to do, or None.  ``App.dispatch``
    #: refuses the Command, before it runs, for a device that provably lacks it.
    #: Declared per Command rather than "is it an LCD": an LED took ``LoadTheme``
    #: and ``SetBackground`` and wrote LCD settings under its key, and a device
    #: that is neither (TR-VISION) has its own set.
    REQUIRES: ClassVar[Capability | None] = None
    #: The Command waits on the PERSON, not the machine -- a password prompt
    #: -- so a client waits for its answer as long as the App does, instead of
    #: the transport's usual limit.  The App's own wait always ends: the
    #: person answers, closes the prompt, or there is no prompt to show.
    WAITS_ON_USER: ClassVar[bool] = False

    @abstractmethod
    def execute(self, app: App) -> R_co: ...

    def refusal(self, message: str) -> R_co:
        """This Command's own Result, ``ok=False`` with *message*."""
        result_cls = result_type(type(self))
        names = {f.name for f in fields(result_cls)}
        extra = {"key": getattr(self, "key", "")} if "key" in names else {}
        log.info("%s.refusal: %s", type(self).__name__, message)
        return cast("R_co", result_cls(ok=False, message=message, **extra))


def result_type(cls: type) -> type[Result]:
    """The Result a Command class is parameterised on -- ``Command[XResult]``."""
    for klass in cls.__mro__:
        for base in getattr(klass, "__orig_bases__", ()):
            for arg in get_args(base):
                if isinstance(arg, type) and issubclass(arg, Result):
                    return arg
    log.debug("result_type: %s declares none — the base Result", cls.__name__)
    return Result


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
