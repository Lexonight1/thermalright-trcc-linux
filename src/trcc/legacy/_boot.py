"""TRCC composition root — canonical Trcc factory.

One module, one function, used uniformly by CLI / API / GUI / daemon.
Every UI calls :func:`trcc` to obtain its per-process Trcc handle and
gets the same answer based on environment + state. Honors
``TRCC_DAEMON`` to switch between in-process and daemon-proxy modes.

Pure DI (Mark Seemann): all wiring happens here. Trcc takes its
dependencies through the constructor; nothing mutates a built Trcc
later. Cosmic Python's bootstrap function shape — defaults make
production easy, kwargs let tests inject fakes.

Composition pattern (the only one in this codebase)::

    from trcc.legacy._boot import trcc
    result = trcc().lcd.set_brightness(0, 50)
    typer.echo(result.format())          # CLI
    return _to_response(result)          # API
    self._status_label.setText(...)      # GUI

Modes:

  ``TRCC_DAEMON`` unset (default)
      Builds the in-process `Trcc` once — single instance per process,
      cached. Construction wires platform, renderer, system service,
      data extraction callable, and theme download callables; then
      runs ``Trcc.discover()`` so the first command sees devices.

  ``TRCC_DAEMON=1`` (and AF_UNIX available)
      Returns a `TrccProxy` connected to a running daemon, auto-spawning
      one via :func:`trcc.daemon.ensure_daemon` if necessary. The proxy
      is a structural drop-in for `Trcc` — call sites are statically
      typed against the same surface either way.

  Windows < build 17063
      ``AF_UNIX`` is unavailable; daemon mode silently falls back to
      in-process. The flag is safe to set on any OS.

Dependency overrides (tests + GUI)::

    from trcc.legacy._boot import trcc
    from tests.mock_platform import MockPlatform
    from trcc.legacy.adapters.render.qt import QtRenderer

    # Tests: pass a MockPlatform to skip real USB.
    trcc(MockPlatform(specs))

    # GUI: pass its own windowed renderer + defer discover until splash.
    trcc(platform, renderer=QtRenderer(), discover_now=False)
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from typing import TYPE_CHECKING, cast

from trcc.legacy.core.trcc import Trcc

if TYPE_CHECKING:
    from trcc.legacy.core.ports import Platform, Renderer

log = logging.getLogger(__name__)

# Cached per-process Trcc — built on first call. In daemon mode this
# is actually a `TrccProxy`, but it satisfies the `Trcc` contract via
# structural typing so call sites never need to distinguish.
_cached: Trcc | None = None
_lock = threading.Lock()

# Strong reference to the offscreen QApplication we may create. Without
# this, PySide6 collects the Python wrapper between calls and the next
# QtRenderer construction segfaults. Module-level ref keeps it alive for
# the process lifetime.
_qapp_strong_ref: object | None = None

# Env flag values that count as "on". Mirrors usage elsewhere in the
# codebase (CLI flags, conf parsing).
_TRUTHY_FLAG = frozenset({'1', 'true', 'yes', 'on'})


def daemon_mode_enabled() -> bool:
    """True when ``TRCC_DAEMON`` is set truthy AND the platform supports it.

    Returns False on Windows builds older than 17063 where ``AF_UNIX``
    is unavailable — the user gets in-process mode silently rather than
    a hard error, so the flag is safe to set on every OS.
    """
    if os.environ.get('TRCC_DAEMON', '').lower() not in _TRUTHY_FLAG:
        return False
    return hasattr(socket, 'AF_UNIX')


def trcc(
    platform: Platform | None = None,
    *,
    renderer: Renderer | None = None,
    discover_now: bool = True,
    verbosity: int = 0,
) -> Trcc:
    """Return the per-process `Trcc` (or `TrccProxy` in daemon mode).

    Cached, thread-safe. Identical contract for every UI — they all
    see the same handle, no UI-specific composition logic anywhere.

    Parameters:
        platform: DI override. ``None`` triggers OS detection via
            ``PlatformFactory.current()``.  Tests pass ``MockPlatform`` to skip USB.
        renderer: DI override. ``None`` builds an offscreen ``QtRenderer``.
            The GUI passes its own windowed renderer.
        discover_now: When True (default), runs ``Trcc.discover()`` so
            devices are connected before the first command. The GUI passes
            False so discover happens behind the splash screen.
        verbosity: Forwarded to the logging configurator (``-v`` / ``-vv``).
    """
    global _cached
    with _lock:
        if _cached is not None:
            return _cached

        # 1. Daemon-mode short circuit — proxy, no Qt or USB needed
        # in this process. Every facade call travels over the socket.
        if platform is None and daemon_mode_enabled():
            from trcc.legacy.core.trcc_proxy import TrccProxy
            from trcc.legacy.daemon import ensure_daemon
            if not ensure_daemon():
                raise RuntimeError(
                    "TRCC_DAEMON=1 but no daemon is reachable. "
                    "Start one explicitly with `trcc daemon`.")
            # TrccProxy is a structural drop-in for Trcc — same .lcd /
            # .led / .control_center / .events surface so call sites
            # stay typed against Trcc and just work at runtime.
            # The renderer (caller-supplied or a built offscreen one)
            # is forwarded to the proxy so its EventBus can reconstruct
            # ``Topic.FRAME`` surface envelopes received from the daemon.
            if renderer is None:
                _ensure_qapp()
                from trcc.legacy.adapters.render.qt import QtRenderer
                renderer = QtRenderer()
            _cached = cast(Trcc, TrccProxy(renderer=renderer))
            return _cached

        # 2. Resolve / accept the Platform.
        if platform is None:
            from trcc.legacy.adapters.system import PlatformFactory
            platform = PlatformFactory.current()

        # 3. Bootstrap the process: logging, OS-specific stdout config,
        # settings file, first-run setup hook. All idempotent — safe
        # to call again from the same process.
        from trcc.legacy.adapters.infra.logging_setup import StandardLoggingConfigurator
        from trcc.legacy.conf import init_settings
        StandardLoggingConfigurator().configure(verbosity=verbosity)
        platform.configure_stdout()
        init_settings(platform)
        if platform.needs_setup():
            platform.auto_setup()

        # 4. Resolve / build the renderer. When the caller didn't
        # provide one we create an offscreen QtRenderer — sufficient
        # for tests, ``python -m trcc.daemon``, and CLI commands that
        # don't want a graphical session.
        if renderer is None:
            _ensure_qapp()
            from trcc.legacy.adapters.render.qt import QtRenderer
            renderer = QtRenderer()

        # 5. Build infra services. Composition happens once, here.
        from trcc.legacy.adapters.infra.data_repository import DataManager
        from trcc.legacy.adapters.infra.theme_downloader import (
            download_pack as download_pack_fn,
        )
        from trcc.legacy.adapters.infra.theme_downloader import (
            list_available as list_available_fn,
        )
        from trcc.legacy.conf import settings as _settings
        from trcc.legacy.core.builder import ControllerBuilder
        from trcc.legacy.services.image import ImageService
        from trcc.legacy.services.system import set_instance
        builder = ControllerBuilder(platform).with_renderer(renderer)
        ImageService.set_renderer(renderer)
        system_svc = builder.build_system(settings=_settings)
        set_instance(system_svc)
        ensure_data_fn = DataManager.ensure_all

        # 6. Construct the singleton Trcc with every dependency injected.
        # No ``set_X`` mutation later — Pure DI means construction is final.
        # ``settings`` is the global instance ``init_settings(platform)``
        # populated above; passing it here makes Trcc's settings access go
        # through DI (Phase 10A.3 partial). The full version (every reader
        # takes settings explicitly, global goes away) is its own session.
        t = Trcc(
            platform,
            renderer=renderer,
            system_svc=system_svc,
            ensure_data_fn=ensure_data_fn,
            settings=_settings,
            download_pack_fn=download_pack_fn,
            list_available_fn=list_available_fn,
        )

        # 7. Optional: run discovery now. CLI / API / daemon want devices
        # ready before the first command; GUI defers so the splash can
        # show progress while data extraction and USB connect happen.
        if discover_now:
            try:
                t.discover()
            except Exception:
                log.exception("trcc(): discover raised — continuing with no devices")
            # Seed each connected device with one fresh metrics snapshot
            # so sensor-linked LED modes (temp_linked, load_linked) and
            # overlay sensors render real values when this is the only
            # UI running on a headless box (issue #130).
            try:
                metrics = platform.metrics
                for device in t:
                    device.update_metrics(metrics)
            except Exception:
                log.exception("trcc(): metrics seeding raised — continuing")

        _cached = t
        return _cached


def cleanup() -> None:
    """Release the cached Trcc. Idempotent + thread-safe.

    For an in-process `Trcc`: stops the metrics loop, releases device
    handles, clears event subscribers. For a `TrccProxy`: closes
    long-lived event subscription sockets. Either way, the next
    :func:`trcc` call rebuilds.
    """
    global _cached
    with _lock:
        if _cached is None:
            return
        cleanup_fn = getattr(_cached, 'cleanup', None)
        if callable(cleanup_fn):
            try:
                cleanup_fn()
            except Exception:
                log.exception("trcc cleanup failed")
        _cached = None


def _ensure_qapp() -> None:
    """Make sure SOME QApplication is alive for QtRenderer to use.

    No-op if one already exists (e.g. the GUI created a display app
    earlier in the process). Otherwise creates an offscreen
    QApplication — sufficient for the renderer's image work without
    requiring a graphical session.
    """
    global _qapp_strong_ref
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is not None:
        return
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    _qapp_strong_ref = QApplication([])
    log.debug("created offscreen QApplication for QtRenderer")
