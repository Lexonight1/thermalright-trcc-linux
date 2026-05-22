"""GUI adapter — PySide6.

Thin widgets over the Command API.  Widgets dispatch Commands and
subscribe to EventBus events via a bridge (bus_bridge.py) that converts
events into Qt signals for thread-safe UI updates.

Entry point: `python -m trcc.next gui` → launches MainWindow.
"""

from .app import launch

__all__ = ["launch"]
