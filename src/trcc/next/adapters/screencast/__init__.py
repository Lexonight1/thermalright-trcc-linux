"""Screen-capture adapters.

One port (:class:`ScreenCapture`) with backend(s) that grab a region
of the user's desktop on demand.  Today we ship the Qt-backed
adapter; PipeWire / wlroots-native backends can land here later
behind the same port.
"""
from .qt import QtScreenCapture

__all__ = ("QtScreenCapture",)
