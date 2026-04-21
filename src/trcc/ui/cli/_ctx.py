"""Shared CLI context — App singleton + lightweight helpers."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from ...app import App
from ...core.ports import Platform, Renderer

log = logging.getLogger(__name__)


_platform_override: Optional[Platform] = None
_renderer_override: Optional[Renderer] = None


def set_platform(platform: Platform) -> None:
    """Override the autodetected Platform (tests, dev mock)."""
    global _platform_override
    _platform_override = platform
    get_app.cache_clear()


def set_renderer(renderer: Renderer) -> None:
    """Override the default QtRenderer.  Mostly for tests."""
    global _renderer_override
    _renderer_override = renderer
    get_app.cache_clear()


@lru_cache(maxsize=1)
def get_app() -> App:
    """Lazy App singleton used by every CLI command handler."""
    platform = _platform_override or Platform.detect()
    renderer = _renderer_override
    if renderer is None:
        try:
            from ...adapters.render.qt import QtRenderer
            renderer = QtRenderer()
        except Exception as e:
            log.warning("QtRenderer unavailable (%s); display commands will fail", e)
    return App(platform=platform, renderer=renderer)
