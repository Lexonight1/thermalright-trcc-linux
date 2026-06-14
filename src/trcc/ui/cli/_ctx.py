"""Shared CLI context — App singleton + lightweight helpers.

In daemon mode (``TRCC_DAEMON=1``) the App is actually an
``AppProxy`` — same ``dispatch(cmd) -> Result`` surface, calls travel
over the Unix socket to the running daemon.  Resolved via the canonical
``_boot.trcc()`` factory.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from functools import lru_cache
from typing import Any

import typer

from ..._boot import trcc
from ...app import App
from ...core.ports import Platform, Renderer

log = logging.getLogger(__name__)


def dumps_json(payload: Any) -> str:
    """JSON for CLI ``--json`` output — indented, ``default=str`` so enums /
    Paths / tuples serialise without a custom encoder.  One shape for every
    ``--json`` flag (per-result via :func:`emit_json`, composite via the
    top-level ``status``)."""
    return json.dumps(payload, default=str, indent=2)


def emit_json(result: Any) -> None:
    """Print a dataclass Result/Snapshot as indented JSON for scripts."""
    typer.echo(dumps_json(dataclasses.asdict(result)))


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
    ``TRCC_DAEMON=1`` is set — UIs don't distinguish, both expose
    ``dispatch(cmd) -> Result``.
    """
    return trcc(platform=_platform_override, renderer=_renderer_override)
