"""Renderer factory — creates QtRenderer."""
from __future__ import annotations

import logging

from trcc.legacy.core.ports import Renderer

log = logging.getLogger(__name__)


def create_renderer() -> Renderer:
    """Create QtRenderer (primary renderer using C++ QPainter)."""
    log.debug("create_renderer: instantiating QtRenderer")
    from trcc.legacy.adapters.render.qt import QtRenderer
    return QtRenderer()
