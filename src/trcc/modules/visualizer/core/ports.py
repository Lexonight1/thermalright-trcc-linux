"""The Audio Visualizer's port: one visual engine."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from operator import attrgetter
from pathlib import Path
from typing import ClassVar

from trcc.core.models import RawFrame

from .models import VisualInfo, VisualSpec

log = logging.getLogger(__name__)


def ranked_visuals(classes: Iterable[type[VisualEngine]]) -> tuple[VisualInfo, ...]:
    """The gallery: each visual this machine can draw, by ``RANK`` -- never
    by the order its module happened to load (an import sorter reorders
    those, and TRCC Pipes is to come first)."""
    infos = tuple(cls.INFO for cls in sorted(classes, key=attrgetter("RANK"))
                  if cls.available())
    log.debug("ranked_visuals: %s", [i.kind for i in infos])
    return infos


class VisualEngine(ABC):
    """Turns sound into pictures: one audio visual.

    Concrete: the ``VISUALS`` registry (``adapters/visual/_base.py``) --
    ``TrccPipes``, ``MilkdropVisual``.  Every call comes from ONE thread: a
    visual may draw with OpenGL, whose context belongs to the thread that
    made it.

    The class answers what the gallery asks -- ``INFO``, whether this
    machine can draw it, its preset files, whether a picture will do -- so
    the Commands never touch a file: a visual that reads files does so here,
    in its adapter.
    """

    #: What the visual is, for the gallery; every concrete visual sets it.
    INFO: ClassVar[VisualInfo]
    #: Where it sits in the gallery, lowest first -- TRCC Pipes is 0, the
    #: first thing the user sees (their call).  Equal ranks keep the order
    #: their classes registered in.  See :func:`ranked_visuals`.
    RANK: ClassVar[int] = 50

    @classmethod
    def on_surface(cls, surface: type | None) -> type[VisualEngine]:
        """This visual as it draws on the OS's kind of OpenGL context --
        itself, for one that draws without it."""
        log.debug("%s.on_surface: draws without OpenGL", cls.__name__)
        return cls

    @classmethod
    def available(cls) -> bool:
        """Whether this machine has what the visual needs."""
        log.debug("%s.available: yes", cls.__name__)
        return True

    @classmethod
    def problem(cls) -> str:
        """Why this machine cannot draw it -- "" when it can.  Unlike
        ``available``, the gallery still shows it, with this reason: a
        visual every user expects (TRCC Pipes) says why it is missing."""
        log.debug("%s.problem: none", cls.__name__)
        return ""

    @classmethod
    def presets(cls, folder: Path) -> tuple[str, ...]:
        """The preset files under *folder* this visual draws, relative to it;
        none for a visual without presets."""
        log.debug("%s.presets: none", cls.__name__)
        return ()

    @classmethod
    def preset(cls, folder: Path, name: str) -> Path | None:
        """*name* as one of this visual's presets under *folder*, or None."""
        log.debug("%s.preset: %r -- none", cls.__name__, name)
        return None

    @classmethod
    def picture_problem(cls, path: Path) -> str:
        """Why *path* cannot be the centre picture; "" when it can."""
        log.debug("%s.picture_problem: %s -- fine", cls.__name__, path)
        return ""

    @abstractmethod
    def open(self, spec: VisualSpec) -> None:
        """Start drawing *spec*; ``OSError`` when the engine or the graphics
        it needs are missing."""

    @abstractmethod
    def feed(self, pcm: bytes) -> None:
        """Sound for the next picture (``SoundInput.read``'s shape)."""

    @abstractmethod
    def adjust(self, settings: Mapping[str, float]) -> None:
        """New slider values while it plays -- called on its own thread,
        between pictures; it keeps playing."""

    @abstractmethod
    def render(self) -> RawFrame:
        """The next picture, RGB24, top row first."""

    @abstractmethod
    def close(self) -> None:
        """Free the engine and its graphics (idempotent)."""
