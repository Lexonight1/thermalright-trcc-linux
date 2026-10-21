"""Where the visuals' GLSL lives: the module's own ``assets/shaders``."""
from __future__ import annotations

import functools
import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Every visual's GLSL, one ``.glsl`` file per shader -- the one home, so a
#: shader is read, packaged and reviewed the same way whichever visual draws
#: it.  Bodies only: ``program`` puts the ``#version`` header on.
SHADERS = Path(__file__).resolve().parents[1] / "assets" / "shaders"


@functools.cache
def shader(name: str) -> str:
    """The GLSL in ``SHADERS/name`` -- read once, then kept."""
    text = (SHADERS / name).read_text(encoding="utf-8")
    log.debug("shader: %s, %d characters", name, len(text))
    return text
