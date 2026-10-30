#!/usr/bin/env python3
"""A picture of a real TRCC window on the mock -- to SEE a change, not only
test it.

Builds the real gui (``TRCCApp``) or qtgui (``MainWindow``) on a stand-in
platform with one scripted 320x320 panel and a private data folder, applies
what it is told, and saves the window as a PNG.  Run it on a private display
and a private session bus, or the window and its tray icon land on your
desktop (memory: reference_drive_windows_on_xvfb)::

    env -u WAYLAND_DISPLAY -u QT_QPA_PLATFORM xvfb-run -a dbus-run-session -- \\
        env PYTHONPATH=src python3.12 dev/tools/window_shot.py gui shot.png \\
        --module visualizer=off --open visualizer

``--module KEY=on|off`` switches a module first (repeatable); ``--open KEY``
opens that module's tab or view (gui) or sidebar page (qtgui) before the
picture -- or, in the gui, a full-window view by name (``about``, ``rgb``, ``thanks``,
``modules``, ``sysinfo``, ``led``);
``--lang CODE`` sets the window's language.
``--developer`` switches developer mode on through the App AFTER the window is
up -- as another UI would -- so the picture shows the Modules page following it.
``--browse`` presses "Get modules" on each Modules page and prints what the
page then says; with ``--online`` the App reads the REAL network (the live
module index) while USB stays scripted -- without it, the index is offline.
Prints what it saw: each module's tab or entry and whether it is visible.
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

#: One panel, the one the tests use.
_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"


def _settle(app: object, seconds: float = 0.2) -> None:
    """Let queued signals land, and background answers (a gallery's tiles)
    arrive -- the windows follow both through the event loop."""
    import time

    from PySide6.QtCore import QCoreApplication

    del app
    log.debug("_settle: %.1fs", seconds)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)


def _platform(root: Path, online: bool) -> Any:
    """One scripted panel; the real network only when *online*."""
    from tests.mock_platform import MockPlatform

    log.info("_platform: %s online=%s", root, online)
    if not online:
        return MockPlatform([_SPEC], root)
    from trcc.adapters.repo.http import UrllibHttpFetcher

    class _Online(MockPlatform):
        """Scripted USB, the real network -- "Get modules" reads the live index."""

        def http_fetcher(self) -> UrllibHttpFetcher:
            log.info("_Online.http_fetcher: the real network")
            return UrllibHttpFetcher()

    return _Online([_SPEC], root)


def shoot(face: str, out: Path, modules: list[str], open_key: str,
          wait: float = 3.0, lang: str = "", developer: bool = False,
          browse: bool = False, online: bool = False) -> list[str]:
    """Build *face*, switch *modules*, open *open_key*, save the window to
    *out*; what each module's tab or entry looked like."""
    from PySide6.QtWidgets import QApplication

    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, SetLanguage, SetModuleEnabled

    log.info("shoot: %s -> %s modules=%s open=%r developer=%s", face, out, modules,
             open_key, developer)
    qapp = QApplication.instance() or QApplication(sys.argv)
    root = Path(tempfile.mkdtemp(prefix="trcc-shot-"))
    app = App(_platform(root, online), renderer=QtRenderer())
    app.start_session()
    app.dispatch(ConnectDevice(key=_KEY))
    if lang:
        print(f"language {lang}: {app.dispatch(SetLanguage(language=lang)).message}")
    for switch in modules:
        key, _, state = switch.partition("=")
        result = app.dispatch(SetModuleEnabled(key=key, enabled=state == "on"))
        print(f"switch {key} {state}: {result.message}")
    seen: list[str] = []
    if face == "gui":
        from trcc.ui.gui.trcc_app import TRCCApp

        window = TRCCApp(app=app)
        # As GuiUI.run does (ui/_uis.py): the sidebar lists the App's panels.
        window.replay_initial_devices()
        window.show()
        _settle(qapp)
        if open_key in window._module_tabs:
            window._module_tabs[open_key].click()
        elif open_key in window.uc_device.view_buttons:
            window.uc_device.view_buttons[open_key].click()
        elif open_key:
            window._show_view(open_key)
        for key, tab in window._module_tabs.items():
            seen.append(f"gui tab {key}: at {tab.geometry().getRect()} "
                        f"visible={tab.isVisibleTo(window)}")
    else:
        from trcc.ui.qtgui.app import MainWindow

        window = MainWindow(app)
        window.show()
        _settle(qapp)
        if open_key:
            window._on_navigate(open_key)
        for key in sorted(window._module_panels):
            entry = window._sidebar._buttons[key]
            seen.append(f"qtgui entry {key}: {entry.text()!r} "
                        f"visible={entry.isVisibleTo(window)}")
    if developer:
        seen += _developer_mode(app, window, qapp)
    if browse:
        seen += _browse(window, qapp, wait)
    _settle(qapp, wait)
    window.grab().save(str(out))
    window.close()
    app.close()
    return seen


def _developer_mode(app: Any, window: Any, qapp: Any) -> list[str]:
    """Developer mode on, as another UI would switch it; what each Modules
    page in *window* then shows."""
    from trcc.core.commands import SetDeveloperMode
    from trcc.ui.qt_modules import ModulesView

    result = app.dispatch(SetDeveloperMode(on=True))
    print(f"developer mode: {result.message}")
    _settle(qapp)
    lines = []
    for view in window.findChildren(ModulesView):
        dev = view.developer
        lines.append(f"modules page: switch checked="
                     f"{dev.switch is not None and dev.switch.isChecked()} "
                     f"folder button={dev.button.text() if dev.button else None!r} "
                     f"visible={dev.button is not None and dev.button.isVisibleTo(window)}")
    log.info("_developer_mode: %s", lines)
    return lines or ["modules page: none in this window"]


def _browse(window: Any, qapp: Any, wait: float) -> list[str]:
    """Press "Get modules" on each Modules page in *window*; what each says
    once the index has answered."""
    from PySide6.QtWidgets import QLabel

    from trcc.ui.qt_modules import ModulesView

    views = window.findChildren(ModulesView)
    log.info("_browse: %d Modules page(s)", len(views))
    for view in views:
        view.open_catalog()
    _settle(qapp, wait)
    lines = []
    for view in views:
        says = [label.text() for label in view.findChildren(QLabel)
                if label.objectName() in ("modulesEmpty", "moduleProblem")
                and label.isVisibleTo(window)]
        lines.append(f"get modules: {len(view.catalog)} listed, note={view.catalog_note!r}, "
                     f"says={says}")
    return lines or ["get modules: no Modules page in this window"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("face", choices=("gui", "qtgui"))
    ap.add_argument("out", type=Path, help="where to save the PNG")
    ap.add_argument("--module", action="append", default=[], metavar="KEY=on|off")
    ap.add_argument("--open", default="", metavar="KEY",
                    help="open this module's tab or page first (gui: or a view)")
    ap.add_argument("--lang", default="", help="the window's language, e.g. de")
    ap.add_argument("--developer", action="store_true",
                    help="switch developer mode on after the window is up")
    ap.add_argument("--browse", action="store_true",
                    help='press "Get modules" and print what the page says')
    ap.add_argument("--online", action="store_true",
                    help="let the App reach the real network (the live module index)")
    ap.add_argument("--wait", type=float, default=3.0,
                    help="seconds to let background answers arrive first")
    args = ap.parse_args(argv)
    for line in shoot(args.face, args.out, args.module, args.open, args.wait,
                      args.lang, args.developer, args.browse, args.online):
        print(line)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
