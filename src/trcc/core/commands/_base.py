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
    successful outcome.  Default INFO.  Per-tick commands (RenderAndSend,
    SendFrame, RenderLed, ReadSensors, *Snapshot) override to DEBUG so
    they show only under ``-vv``; they fire dozens of times per second
    and would drown the log at INFO.
    """

    LOG_LEVEL: ClassVar[int] = logging.INFO

    @abstractmethod
    def execute(self, app: App) -> R_co: ...
