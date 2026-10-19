"""GUI adapter — PySide6.

Thin widgets over the Command API.  Widgets dispatch Commands and
subscribe to EventBus events via a bridge (bus_bridge.py) that converts
events into Qt signals for thread-safe UI updates.

Entry point: `python -m trcc gui` → launches MainWindow.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .app import run

if TYPE_CHECKING:
    from ...core.ports import Platform
    from .app import MainWindow

__all__ = ["launch", "run"]

log = logging.getLogger(__name__)


def launch(
    platform: Platform | None = None,
    on_ready: Callable[[MainWindow], None] | None = None,
    *,
    force_exit: bool = True,
    start_hidden: bool = False,
) -> int:
    """``trcc qtgui``'s entry: this host's platform, then :func:`run`.

    The composition root for the qtgui skin, as ``trcc.ui.gui.launch`` is for
    the gui: the one place it turns "this host" into a ``Platform`` -- which
    the bus treats exactly as no platform, and which ``run`` needs to end the
    process (``Platform.hard_exit``).
    """
    log.info("launch: platform=%s start_hidden=%s", platform, start_hidden)
    if platform is None:
        from ...adapters.system import current_platform
        platform = current_platform()
    return run(platform, on_ready, force_exit=force_exit,
               start_hidden=start_hidden)
