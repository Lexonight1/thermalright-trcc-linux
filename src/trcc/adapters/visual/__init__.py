"""Audio visuals: what the computer is playing, turned into a panel's
background.  Importing the package registers every visual in ``VISUALS``."""
from . import cavalier, karmaviz, pipes, projectm  # noqa: F401 -- each class registers itself
from ._base import VISUALS, BaseVisual, GlVisual, catalog, drawn_on
from ._gl import CglSurface, EglSurface, WglSurface

__all__ = ["VISUALS", "BaseVisual", "CglSurface", "EglSurface", "GlVisual",
           "WglSurface", "catalog", "drawn_on"]
