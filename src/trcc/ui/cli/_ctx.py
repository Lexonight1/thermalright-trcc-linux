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


def ensure_connected(app: App, key: str) -> None:
    """Attach + handshake *key* if this stateless CLI process hasn't yet.

    Every CLI invocation is a fresh, non-daemon App holding no attached
    devices, so a wire command (``color`` / ``play`` / ``load-theme`` / LED
    ``render`` …) dispatched straight away would fail with "not connected".
    ``EnsureConnected`` is idempotent — a no-op when a daemon/GUI already holds
    the device — so this is safe before a single wire command and once before a
    render/play loop.  Exits with the connect error on failure (a wire command
    against an unattached device can do nothing useful).
    """
    from ...core.commands import EnsureConnected
    result = app.dispatch(EnsureConnected(key=key))
    if not result.ok:
        typer.echo(result.message, err=True)
        raise typer.Exit(code=1)


def dispatch_echo(cmd: Any) -> Any:
    """Dispatch *cmd*, echo its ``message``, exit(1) on failure; return the Result.

    Collapses the `result = get_app().dispatch(...); typer.echo(result.message);
    if not result.ok: raise typer.Exit(1)` tail that every non-interactive CLI
    command repeats.  Commands that read fields off the Result keep the returned
    value; the rest just call it.
    """
    result = get_app().dispatch(cmd)
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    return result


def parse_on_off(state: str) -> bool:
    """Parse an ``on``/``off`` CLI argument to bool, or raise ``BadParameter``."""
    lowered = state.lower()
    if lowered not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    return lowered == "on"


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
