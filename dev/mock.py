#!/usr/bin/env python3
"""Unified mock driver — ONE entry to launch ANY UI against MockPlatform.

Real dev-box sensors + real src code end to end; only the USB display device
and its handshake bytes are faked (see ``tests/mock_platform.py``).  Thanks to
the uniform ``run(platform)`` launch seam (see ``METHOD_UI.md``), qtgui is
drivable here for the first time — its parity comes for free.

    PYTHONPATH=src python3 dev/mock.py --ui qtgui
    PYTHONPATH=src python3 dev/mock.py --ui api --port 9876
    PYTHONPATH=src python3 dev/mock.py --ui cli display color 0402:3922 ff0000
    PYTHONPATH=src python3 dev/mock.py --ui gui device=0402:3922

``--ui gui|cli|api`` delegate to the feature-rich per-UI harnesses
(``device=`` / ``--report`` / ``--replay`` / ``--check`` …); ``--ui qtgui``
runs the uniform ``run(platform)`` seam directly.  ``--ui`` defaults to ``gui``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Bootstrap path so ``_mock_bootstrap`` + the sibling harnesses import.
sys.path.insert(0, str(Path(__file__).resolve().parent))

_UIS = ("cli", "api", "gui", "qtgui")


def _pop_ui() -> str:
    """Pull ``--ui VALUE`` out of argv (default ``gui``); leave the rest for
    the delegated UI to parse."""
    if "--ui" not in sys.argv:
        return "gui"
    i = sys.argv.index("--ui")
    if i + 1 >= len(sys.argv):
        print(f"Error: --ui requires a value ({'|'.join(_UIS)})", file=sys.stderr)
        raise SystemExit(1)
    ui = sys.argv[i + 1]
    del sys.argv[i:i + 2]
    return ui


def _pop_report() -> str | None:
    """Pull an optional ``--report FILE`` out of argv for the qtgui path."""
    if "--report" not in sys.argv:
        return None
    i = sys.argv.index("--report")
    report = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    del sys.argv[i:i + 2]
    return report


def _run_qtgui() -> int:
    """The uniform seam: bootstrap the mock fleet, then ``run(platform)``.

    The real qtgui composes its App from the injected MockPlatform (real
    sensors, faked device) via ``build_qt_app`` — the same code path the
    shipping qtgui runs.

    Device presentation mirrors ``mock_gui`` so the qtgui mock stops booting
    blank: a simulated fleet (``devices.json`` / ``--report``) is auto-connected
    via an ``on_ready`` hook, because ``DevMockPlatform.scan_devices`` returns
    ``[]`` by dev rule so ``run``'s own coldplug finds nothing.  With NO specs
    the platform is the REAL ``DevPlatform`` and ``run``'s ``discover_and_connect``
    attaches whatever hardware is actually plugged in — "use my device".
    """
    from _mock_bootstrap import bootstrap, load_device_specs

    report = _pop_report()
    platform = bootstrap(report)
    specs = load_device_specs(report)

    def _on_ready(window: object) -> None:
        import mock_gui
        app = window._app  # type: ignore[attr-defined]
        for spec in specs:
            mock_gui._auto_connect(app, spec)

    from trcc.ui.qtgui import run
    # ``force_exit=False`` so the mock returns normally instead of ``os._exit``
    # — same contract mock_gui uses for the gui skin, and what lets a harness
    # assert on the exit code.
    return run(
        platform, on_ready=_on_ready if specs else None, force_exit=False,
    )


def main() -> None:
    ui = _pop_ui()
    if ui == "qtgui":
        raise SystemExit(_run_qtgui())
    if ui == "gui":
        import mock_gui
        mock_gui.main()
    elif ui == "cli":
        import mock_cli
        mock_cli.main()
    elif ui == "api":
        import mock_api
        mock_api.main()
    else:
        print(f"Error: --ui {ui!r} not one of {'|'.join(_UIS)}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
