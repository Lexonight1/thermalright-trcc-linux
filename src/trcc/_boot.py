"""Canonical App factory — the only constructor UIs call.

Every UI is a remote control for ONE App process that owns the panels (see
"The vision" in CLAUDE.md), so the default finds that App — or starts it —
and hands back an ``AppProxy``.  A local, in-process ``App`` is built only for
a stated reason:

    TRCC_DAEMON=0            the caller asked (tests, dev mocks, profilers)
    no AF_UNIX               CPython has no AF_UNIX on Windows, at any build
    running as root          the shared App lives in userland: a root App
                             would outlive ``sudo trcc system setup`` holding USB
    the App failed to start  degrade to in-process rather than fail the UI

UIs hold the return value as ``App``; the proxy is structurally compatible
(the same ``dispatch(cmd) -> Result`` surface).
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


def _local_reason() -> str | None:
    """Why this process must build its own App, or None to use the shared one."""
    flag = os.environ.get(_ENV_FLAG, "1")
    reason = (f"{_ENV_FLAG}={flag}" if flag != "1"
              else "no AF_UNIX on this platform" if not hasattr(socket, "AF_UNIX")
              else "running as root — the shared App lives in userland"
              if os.geteuid() == 0 else None)
    log.debug("_local_reason: %s", reason)
    return reason


def trcc(
    *,
    platform: Platform | None = None,
    renderer: Renderer | None = None,
) -> App:
    """Return the App every UI should call ``dispatch`` on.

    The shared App (an ``AppProxy``, the App found or started by
    ``daemon.ensure_daemon``) unless :func:`_local_reason` names a reason to
    build one in-process from ``platform`` / ``renderer``.  Those two are
    ignored for the proxy: the App owns its own.
    """
    reason = _local_reason()
    log.info("trcc: %s=%s platform=%s renderer=%s -> %s", _ENV_FLAG,
             os.environ.get(_ENV_FLAG), platform is not None,
             renderer is not None, reason or "the shared App")
    if reason is None:
        from . import daemon as _daemon_module
        from .proxy import AppProxy

        if _daemon_module.ensure_daemon():
            return cast("App", AppProxy())
        log.warning("trcc: the shared App failed to start — falling back to "
                    "an in-process App")
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
