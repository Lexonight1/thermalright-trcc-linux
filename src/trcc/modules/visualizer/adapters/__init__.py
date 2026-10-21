"""The Audio Visualizer's visuals: importing this package registers every one
in ``VISUALS`` -- only when the module is switched on (``app.py`` imports it)."""
from . import (  # noqa: F401 -- each class registers itself
    cavalier,
    karmaviz,
    pipes,
    projectm,
    spikes,
)
from ._base import VISUALS, BaseVisual, GlVisual, catalog, drawn_on

__all__ = ["VISUALS", "BaseVisual", "GlVisual", "catalog", "drawn_on"]
