"""Canonical App factory — the only constructor UIs call.

Routes to one of two backends based on the environment::

    TRCC_DAEMON unset     → real App, in-process
    TRCC_DAEMON=1         → AppProxy, talks to daemon over Unix socket
                                 (auto-spawns the daemon if not running)

On Windows < build 17063 ``AF_UNIX`` is not available; the factory
silently falls back to in-process so the env var is safe to leave set
on every OS.

UIs hold the return value as ``App``; the proxy is structurally
compatible (same ``dispatch(cmd) -> Result`` surface).
"""
from __future__ import annotations

import logging
import os
import socket
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .app import App
    from .core.ports import Platform, Renderer

log = logging.getLogger(__name__)


_ENV_FLAG = "TRCC_DAEMON"


def trcc(
    *,
    platform: Platform | None = None,
    renderer: Renderer | None = None,
) -> App:
    """Return the App every UI should call ``dispatch`` on.

    Three modes:

      * ``TRCC_DAEMON=1`` + ``AF_UNIX`` available → ``AppProxy``
        (auto-spawn daemon on first request).
      * ``TRCC_DAEMON=1`` on a platform without ``AF_UNIX`` →
        warn once and fall back to in-process.
      * Default → in-process ``App`` built from ``platform`` /
        ``renderer`` (auto-detected when omitted).

    ``platform`` / ``renderer`` are ignored in proxy mode — the daemon
    owns both.  Pass them when forcing in-process construction (tests,
    composition roots).
    """
    log.info("trcc: env_flag=%s platform=%s renderer=%s",
             os.environ.get(_ENV_FLAG), platform is not None,
             renderer is not None)
    if os.environ.get(_ENV_FLAG) == "1":
        if not hasattr(socket, "AF_UNIX"):
            log.warning(
                "%s=1 ignored — AF_UNIX is unavailable on this platform; "
                "falling back to in-process App", _ENV_FLAG,
            )
        else:
            from . import daemon as _daemon_module
            from .proxy import AppProxy

            if not _daemon_module.ensure_daemon():
                log.warning(
                    "%s=1 set but daemon failed to start; falling back to "
                    "in-process App", _ENV_FLAG,
                )
            else:
                return cast("App", AppProxy())

    return _build_local_app(platform=platform, renderer=renderer)


def _build_local_app(
    *,
    platform: Platform | None = None,
    renderer: Renderer | None = None,
) -> App:
    """Construct an in-process App.  Used by ``trcc`` and the daemon."""
    log.info("_build_local_app: platform=%s renderer=%s",
             platform is not None, renderer is not None)
    from .adapters.system import current_platform
    from .app import App

    real_platform = platform if platform is not None else current_platform()
    real_renderer = renderer
    if real_renderer is None:
        try:
            from .adapters.render.qt import QtRenderer
            real_renderer = QtRenderer()
        except Exception as e:
            log.warning(
                "QtRenderer unavailable (%s); display commands will fail "
                "until a renderer is attached", e,
            )
    return App(platform=real_platform, renderer=real_renderer)
