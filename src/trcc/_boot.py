"""Canonical App factory — the only constructor UIs call.

Every UI is a remote control for ONE App process that owns the panels (see
"The vision" in CLAUDE.md), so the default finds that App — or starts it —
and hands back an ``AppProxy``.  A local, in-process ``App`` is built only for
a stated reason:

    TRCC_DAEMON=0            the caller asked (tests, dev mocks, profilers)
    no AF_UNIX               CPython has no AF_UNIX on Windows, at any build
    elevated from a user     sudo / run0 / pkexec / doas: the shared App is
                             that user's, and a root App started here would
                             outlive ``sudo trcc system setup`` holding USB.
                             A root LOGIN or a root service is not elevated:
                             it shares one App like any user (#150 #246 #267)
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

#: What each elevator leaves in the environment of the root process it starts:
#: sudo and run0 ``SUDO_UID``, pkexec ``PKEXEC_UID``, doas ``DOAS_USER``.
_ELEVATED_BY = ("SUDO_UID", "PKEXEC_UID", "DOAS_USER")


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
              else _elevated() or _stand_in(platform))
    log.debug("_local_reason: %s", reason)
    return reason


def _elevated() -> str | None:
    """Name the elevator when this is root raised from a user's session.

    That user's App is the shared one, so this process stays in-process.  A
    root login or a root service has no elevator variable: it is a user in
    its own right and shares an App like any other.  Every root process was
    sent in-process from 50b557eb, which left a root-only box (Proxmox, a
    headless Pi) with no App to keep a panel lit and a root service with
    clients that could not reach it (#150 #246 #267).
    """
    if os.geteuid() != 0:
        return None
    by = next((var for var in _ELEVATED_BY if os.environ.get(var)), None)
    log.debug("_elevated: root, elevator variable %s", by)
    return (f"elevated by {by} — the shared App is the invoking user's"
            if by else None)


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
    draws: bool = True,
) -> App:
    """Construct an in-process App.  Used by ``trcc`` and the daemon.

    ``draws=False`` builds it with no renderer, for the caller-side Commands
    (daemon status and stop, setup, upgrade, report) that never draw a frame:
    a QtRenderer and its display wiring were ~100 ms of every ``trcc kill``.
    """
    log.info("_build_local_app: platform=%s renderer=%s draws=%s",
             platform is not None, renderer is not None, draws)
    from .adapters.system import current_platform
    from .app import App

    real_platform = platform if platform is not None else current_platform()
    real_renderer = renderer
    if real_renderer is None and draws and _led_coolers_only(real_platform):
        # An LED cooler only shows its segment display -- the App sends the
        # UIs a list of colours, never a picture -- so Qt here was ~30 MB
        # held for nothing (#299).  Decided now, on the main thread, because
        # Qt started from any other thread crashes the process at exit.
        # Coolers sit inside the case, so the scan at start sees them all.
        log.info("_build_local_app: only LED coolers found — no renderer")
    elif real_renderer is None and draws:
        try:
            from .adapters.render.qt import QtRenderer
            real_renderer = QtRenderer()
        except Exception as e:
            log.warning(
                "QtRenderer unavailable (%s); display commands will fail "
                "until a renderer is attached", e,
            )
    return App(platform=real_platform, renderer=real_renderer)


def _led_coolers_only(platform: Platform) -> bool:
    """True when the scan finds coolers and every one is an LED cooler.

    Nothing found, an unknown product or a failed scan all answer False:
    the renderer is then built as it always was.
    """
    from .core.models import Kind
    from .core.registry import find_product
    try:
        scan = platform.scan_devices()
    except Exception as e:
        log.warning("_led_coolers_only: scan failed (%s) — building the "
                    "renderer", e)
        return False
    kinds = {product.kind if (product := find_product(i.vid, i.pid)) else None
             for i in scan}
    log.info("_led_coolers_only: %d device(s), kinds=%s", len(scan),
             sorted(str(k) for k in kinds))
    return bool(scan) and kinds == {Kind.LED}
