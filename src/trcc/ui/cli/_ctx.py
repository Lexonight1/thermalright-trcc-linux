"""Shared CLI context — App singleton + lightweight helpers.

In daemon mode (``TRCC_NEXT_DAEMON=1``) the App is actually an
``AppProxy`` — same ``dispatch(cmd) -> Result`` surface, calls travel
over the Unix socket to the running daemon.  Resolved via the canonical
``_boot.trcc_next()`` factory.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from ..._boot import trcc_next
from ...app import App
from ...core.ports import Platform, Renderer

log = logging.getLogger(__name__)


_platform_override: Platform | None = None
_renderer_override: Renderer | None = None


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
    """Lazy App singleton used by every CLI command handler.

    Returns an in-process ``App`` (default) or an ``AppProxy`` when
    ``TRCC_NEXT_DAEMON=1`` is set — UIs don't distinguish, both expose
    ``dispatch(cmd) -> Result``.
    """
    return trcc_next(platform=_platform_override, renderer=_renderer_override)
