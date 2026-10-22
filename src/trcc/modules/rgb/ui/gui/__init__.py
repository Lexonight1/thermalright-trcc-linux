"""RGB Lighting in the classic window: a whole page behind its sidebar
button (``VIEW``)."""
from .uc_rgb import UCRgb

#: Its view (``ui/gui/``'s fixed name).
VIEW = UCRgb

__all__ = ["VIEW", "UCRgb"]
