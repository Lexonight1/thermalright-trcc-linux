"""The audio visuals registry: every visual, by the key in its class line.

The same idiom as ``DEVICES`` and ``PLATFORMS``: one shared ``Registry``,
and a visual registers itself by naming its key where it is defined::

    class TrccPipes(BaseVisual, key="trcc-pipes"): ...

A new visual is one new class.  Nothing that plays visuals names one: the
Commands take a key, the module binds each class to the OS's OpenGL context
(:meth:`GlVisual.on_surface`), and the Audio Visuals page draws its gallery
and sliders from each class's ``INFO``.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar, TypeVar, cast

from trcc.core.factory import Registry, Reject
from trcc.core.ports import GlSurface

from ..core.models import VisualInfo
from ..core.ports import VisualEngine

log = logging.getLogger(__name__)

VISUALS: Registry[str, type[BaseVisual]] = Registry(
    "audio visual", on_missing=Reject(OSError),
)


class BaseVisual(VisualEngine):
    """An audio visual: what it is (``INFO``), and whether this machine can
    draw it.  ``key=None`` in a class line means an intermediate base."""

    INFO: ClassVar[VisualInfo]

    def __init_subclass__(cls, key: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if key is None:
            log.debug("%s: intermediate visual base, not registered",
                      cls.__name__)
            return
        log.debug("%s: registering as visual %s", cls.__name__, key)
        VISUALS.register(key)(cls)


class GlVisual(BaseVisual):
    """A visual drawn with OpenGL, on the context its platform names
    (:func:`drawn_on`).  Listed wherever it is registered; where no context
    opens -- or none is named -- the gallery shows it with the reason."""

    #: The platform's kind of context; ``None`` until :func:`drawn_on` binds one.
    SURFACE: ClassVar[type[GlSurface] | None] = None

    @classmethod
    def problem(cls) -> str:
        """Why it cannot draw here -- a context is opened once to find out,
        and the answer kept."""
        if "_gl_problem" not in cls.__dict__:
            cls._gl_problem = cls._try_surface()
        problem: str = cls.__dict__["_gl_problem"]
        log.debug("%s.problem: %r", cls.__name__, problem)
        return problem

    @classmethod
    def _try_surface(cls) -> str:
        """Open a context and put it away: "" if it opened, else why not."""
        if cls.SURFACE is None:
            log.info("%s: no OpenGL context named on this OS", cls.__name__)
            return f"{cls.INFO.title} cannot draw on this computer yet"
        try:
            cls.SURFACE.open().close()
        except OSError as e:
            log.warning("%s: cannot draw here -- %s", cls.__name__, e)
            return f"{cls.INFO.title} needs OpenGL 3: {e}"
        log.info("%s: OpenGL draws here", cls.__name__)
        return ""

    @classmethod
    def on_surface(cls, surface: type | None) -> type[VisualEngine]:
        """This visual drawing on *surface*, the OS's kind of context."""
        log.debug("%s.on_surface: %s", cls.__name__,
                  getattr(surface, "__name__", None))
        return _bound(cls, surface)

    def _open_surface(self) -> GlSurface:
        """A context to draw on, current on this thread; ``OSError`` saying
        why there is none."""
        surface = type(self).SURFACE
        log.info("%s._open_surface: %s", type(self).__name__,
                 surface.__name__ if surface else None)
        if surface is None:
            raise OSError(type(self).problem())
        return surface.open()


_V = TypeVar("_V", bound=BaseVisual)


@functools.cache
def _bound(cls: type[_V], surface: type[GlSurface] | None) -> type[_V]:
    """*cls* bound to *surface* -- a subclass, made once, so each binding
    keeps its own answer to :meth:`GlVisual.problem` (and MilkDrop its own
    :meth:`~VisualEngine.available`)."""
    log.debug("_bound: %s on %s", cls.__name__,
              surface.__name__ if surface else None)
    made = type(cls.__name__, (cls,), {"SURFACE": surface,
                                       "__module__": cls.__module__,
                                       "__doc__": cls.__doc__})
    return cast("type[_V]", made)


def drawn_on(surface: type[GlSurface]) -> Mapping[str, type[VisualEngine]]:
    """The ``VISUALS`` registry as one OS draws it: every visual on that OS's
    *surface* (:meth:`VisualEngine.on_surface`)."""
    log.info("drawn_on: %s", surface.__name__)
    return MappingProxyType({key: cls.on_surface(surface)
                             for key, cls in VISUALS.items()})

