"""Canonical App factory — the only constructor UIs call.

Every UI is a remote control for ONE App process that owns the panels (see
"The vision" in CLAUDE.md), so the default finds that App — or starts it —
and hands back an ``AppProxy``.  A local, in-process ``App`` is built only for
a stated reason:

    TRCC_DAEMON=0            the caller asked (tests, dev mocks, profilers)
    no AF_UNIX               CPython has no AF_UNIX on Windows, at any build
    running as root          the shared App lives in userland: a root App
                             would outlive ``sudo trcc system setup`` holding USB
    a stand-in platform      a mock, a fake, a dev subclass: the shared App runs
                             on this host's own platform, so asking it would
                             silently drop the stand-in and drive real USB
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


def _local_reason(platform: Platform | None = None) -> str | None:
    """Why this process must build its own App, or None to use the shared one.

    *platform* is what the caller would build on.  ``None`` and this host's
    own class (what ``trcc gui`` passes) are the shared App's to serve; any
    other class is a stand-in.  "A platform was injected" is NOT the test --
    that rule (2026-10-02) put the real gui on its own App and blanked a panel.
    """
    flag = os.environ.get(_ENV_FLAG, "1")
    reason = (f"{_ENV_FLAG}={flag}" if flag != "1"
              else "no AF_UNIX on this platform" if not hasattr(socket, "AF_UNIX")
              else "running as root — the shared App lives in userland"
              if os.geteuid() == 0 else _stand_in(platform))
    log.debug("_local_reason: %s", reason)
    return reason


def _stand_in(platform: Platform | None) -> str | None:
    """Name *platform* a stand-in if it is not this host's own class."""
    if platform is None:
        log.debug("_stand_in: no platform given")
        return None
    from .adapters.system import host_platform_class

    host = host_platform_class()
    if type(platform) is host:
        log.debug("_stand_in: %s is this host's own", host.__name__)
        return None
    return (f"{type(platform).__name__} is a stand-in, not this host's "
            f"{host.__name__}")


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
    reason = _local_reason(platform)
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
