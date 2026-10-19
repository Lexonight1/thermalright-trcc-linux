"""GUI composition root for next/ — wires Qt adapter.

Single entry point for the graphical interface.  Builds the windowed
``QApplication`` (which Qt requires before any QWidget), constructs an
``App`` via ``trcc._boot.trcc()``, then hands the app handle
to ``MainWindow``.  ``discover`` runs in a background ``BootstrapWorker``
so the splash shows immediate feedback.

Composition root — this is the ONE place that imports concrete adapters
(``Platform``, ``QtRenderer``, ``SingleInstance``).  Every other file under
``ui/gui/`` holds an ``App`` handle and dispatches Commands.

This UI does NOT host an IPC server.  It used to construct one, but only
``IPCServer.start()`` (bind + listen) was ever called and nothing served it,
so it squatted on the daemon's socket answering nothing — ``daemon_running()``
reported a daemon that did not exist, ``trcc daemon`` refused to start while
the GUI was open, and a live ``trccd`` was orphaned by the unconditional
unlink.  The daemon is the only process that may own that socket; a UI
reaches it as a client, and only when ``TRCC_DAEMON=1``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .base import BasePanel, ImageLabel
from .trcc_app import TRCCApp
from .uc_device import UCDevice
from .uc_preview import UCPreview
from .uc_theme_local import UCThemeLocal
from .uc_theme_mask import UCThemeMask
from .uc_theme_setting import UCThemeSetting
from .uc_theme_web import UCThemeWeb

__all__ = [
    'BasePanel',
    'ImageLabel',
    'TRCCApp',
    'UCDevice',
    'UCPreview',
    'UCThemeLocal',
    'UCThemeMask',
    'UCThemeSetting',
    'UCThemeWeb',
    'launch',
    'run_gui',
]

log = logging.getLogger(__name__)


def launch(verbosity: int = 0, decorated: bool = False,
           start_hidden: bool = False) -> int:
    """Bootstrap and run the shipping GUI.  Returns the Qt exit code.

    Thin wrapper over :func:`run_gui` — builds the real host platform and
    runs the full composition with the production seams on (single-instance
    lock, IPC server, ``Platform.hard_exit`` reap).

    ``verbosity`` is unused: the CLI root callback (``ui.cli.main:_root``)
    ALWAYS runs first and has already configured logging at the requested
    level.  Re-configuring here would silently downgrade DEBUG back to INFO.
    """
    del verbosity
    from ...adapters.system import current_platform
    platform = current_platform()
    return run(platform, decorated=decorated, start_hidden=start_hidden)


def run(platform: Any, *, decorated: bool = False,
        start_hidden: bool = False, single_instance: bool = True,
        force_exit: bool = True,
        on_ready: Callable[[Any], None] | None = None) -> int:
    """Run the GUI composition from an injected ``platform``.  Returns exit code.

    A thin alias over the UI bus (``ui/_base.py``): the launch sequence —
    single-instance guard, compose, bring up, run, close — is
    ``UserInterface.start`` and is shared with every other face.  What stays
    the GUI's own is ``GuiUI``: its lock, its Qt-first composition, its splash
    bootstrap and its window.

    Two things stay HERE rather than on the face, because they are process
    concerns rather than UI behaviour:

    * ``platform.configure_stdout()`` — must precede the single-instance check
      and everything that writes output (a Windows console defaults to cp1252
      and crashes on non-ASCII).  It takes the INJECTED platform, which is what
      the dev mock wants.
    * ``force_exit`` — the process ends via ``Platform.hard_exit`` AFTER the
      bus's ``finally`` has closed the App, so it cannot live inside
      ``GuiUI.run`` (``reap_after``).  Qt's metrics/sensor/render threads
      occasionally outlive ``qapp.exec()`` when native libraries (pynvml,
      psutil, pyusb) hold the GIL on shutdown; cleanup has already happened by
      here, so forcing the reap is safe.  The dev mock passes
      ``force_exit=False`` and returns normally.
    """
    log.info("run: delegating to the UI bus (decorated=%s start_hidden=%s "
             "single_instance=%s)", decorated, start_hidden, single_instance)
    platform.configure_stdout()
    from functools import partial

    from .._base import reap_after
    from .._uis import GuiUI
    face = GuiUI(decorated=decorated, start_hidden=start_hidden,
                 single_instance=single_instance, on_ready=on_ready)
    return reap_after(platform, partial(face.start, platform),
                      force_exit=force_exit)


# Back-compat alias — ``dev/mock_gui`` and existing tests call ``run_gui``.
# ``run`` is the canonical name in the unified UI-launch contract.
run_gui = run
