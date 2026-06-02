#!/usr/bin/env python3
"""Mock GUI — run the real next/ GUI rooted at ``dev/.trcc/``.

Mirrors ``src/trcc/ui/gui/__init__.py::launch`` step for step, with two
differences:

  * ``platform`` is a ``DevPlatform`` from ``_mock_bootstrap`` so paths
    point at ``dev/.trcc/`` / ``dev/.trcc-user/`` instead of ``~/.trcc``.
  * No ``SingleInstance`` lock + no IPC server — those collide with a
    real install running in parallel.

Real USB enumeration + real sensors run unchanged, so the GUI behaves
exactly like a packaged install would, just isolated to a throwaway
data root.  Bugs found here are real bugs.

Usage:
    PYTHONPATH=src python3 dev/mock_gui.py
    PYTHONPATH=src python3 dev/mock_gui.py --decorated
    PYTHONPATH=src python3 dev/mock_gui.py -v          # DEBUG level
    PYTHONPATH=src python3 dev/mock_gui.py --report report.txt
"""
from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, cast

# Bootstrap handles sys.path + paths + logging.  Import is intentionally
# before any trcc.* import so Platform.detect picks up our paths overrides.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import (
    DEV_DATA,
    DEV_TRCC,
    DEVICES_JSON,
    bootstrap,
)

log = logging.getLogger("dev.mock_gui")


def _parse_args() -> tuple[bool, int, str | None]:
    decorated = False
    verbosity = 0
    report_path: str | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith('-v'):
            verbosity = arg.count('v')
        elif arg == '--list':
            from trcc.core.models import FBL_TO_RESOLUTION
            resolutions = sorted(set(FBL_TO_RESOLUTION.values()),
                                 key=lambda r: (r[0] * r[1], r[0]))
            print("Available resolutions:")
            for w, h in resolutions:
                print(f"  {w}x{h}")
            sys.exit(0)
        elif arg == '--report':
            i += 1
            if i < len(args):
                report_path = args[i]
            else:
                print("Error: --report requires a file path")
                sys.exit(1)
        elif arg == '--decorated':
            decorated = True
        i += 1
    return decorated, verbosity, report_path


def main() -> None:
    decorated, verbosity, report_path = _parse_args()

    # Bootstrap: dev paths + rotating log at dev/.trcc/trcc.log.
    platform = bootstrap(report_path=report_path, verbosity=verbosity)

    # ── Qt bootstrap (must precede QtRenderer construction) ──────────────
    os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.services=false")
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"

    from PySide6.QtWidgets import QApplication
    qapp = cast(QApplication, QApplication.instance() or QApplication(sys.argv))

    from trcc.ui.qapp import configure_qapplication
    configure_qapplication(qapp)

    # ── Assets dir + boot ────────────────────────────────────────────────
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    set_assets_dir(_PKG_ASSETS_DIR)

    from trcc._boot import trcc_next
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    renderer = QtRenderer()
    app = cast(App, trcc_next(platform=cast(Any, platform), renderer=renderer))

    # ── Splash + discover (same as production launch) ───────────────────
    from trcc.ui.gui.splash import run_bootstrap_with_splash
    if not run_bootstrap_with_splash(app):
        log.error("dev/mock_gui: bootstrap splash failed; aborting")
        sys.exit(1)

    # ── Live device events + metrics broadcast ──────────────────────────
    app.start_hotplug()
    app.metrics_loop.start()

    # ── Window ──────────────────────────────────────────────────────────
    from trcc.ui.gui.trcc_app import TRCCApp
    window = TRCCApp(app=app, decorated=decorated)
    window.replay_initial_devices()

    # ── Signals ─────────────────────────────────────────────────────────
    def _on_sigint(*_args: object) -> None:
        qapp.quit()
    signal.signal(signal.SIGINT, _on_sigint)

    window.show()

    print(f"\nConfig:  {DEV_TRCC / 'config.json'}")
    print(f"Data:    {DEV_DATA}")
    print(f"Devices: {DEVICES_JSON}")
    print("Close window or Ctrl+C to quit.\n")

    try:
        exit_code = qapp.exec()
    finally:
        app.close()
        log.info("dev/mock_gui: cleanup complete — exit")
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
