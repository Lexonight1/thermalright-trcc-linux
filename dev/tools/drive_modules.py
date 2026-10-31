#!/usr/bin/env python3
"""Drive the Modules feature through all four UIs on ONE App, and check that
every UI shows what another did.

The vision (CLAUDE.md "one App, thin UIs"): any UI can change a module, and
every UI follows -- "every UI shows the same truth".  This builds the real
gui window, the real qtgui window, the real CLI and the real API on one
in-process App (a stand-in platform: one scripted panel, no hardware), then
acts through one UI per step and reads the result back from all four:

1. both built-in modules on, everywhere;
2. the CLI switches the Visualizer off; the API switches it on again;
3. the gui's switch turns RGB off; the qtgui's turns it on again;
4. "Get modules" in every UI (``--online``: the live index);
5. developer mode on from the qtgui; the CLI installs ``hello`` from a folder;
   every UI lists it as unsigned and off; the API switches it on; the
   qtgui's Remove takes it away; the CLI switches developer mode off.

Each check prints PASS or FAIL; the exit code is the number that failed.
Run it on a private display and session bus (memory:
reference_drive_windows_on_xvfb)::

    env -u WAYLAND_DISPLAY -u QT_QPA_PLATFORM xvfb-run -a dbus-run-session -- \\
        env PYTHONPATH=src python3.12 dev/tools/drive_modules.py --online
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

log = logging.getLogger(__name__)

_KEY = "0402:3922"
BUILT_IN = ("visualizer", "rgb")


class Drive:
    """One App, four UIs, and the checks run against them."""

    def __init__(self, online: bool) -> None:
        from PySide6.QtWidgets import QApplication

        from tests.conftest import loopback_client
        from trcc.adapters.render.qt import QtRenderer
        from trcc.core.commands import ConnectDevice
        from trcc.ui.api.main import build_app
        from trcc.ui.cli import _ctx
        from trcc.ui.cli.main import app as cli
        from trcc.ui.gui.trcc_app import TRCCApp
        from trcc.ui.qtgui.app import MainWindow
        from typer.testing import CliRunner
        from window_shot import mock_platform

        log.info("Drive: online=%s", online)
        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.root = Path(tempfile.mkdtemp(prefix="trcc-drive-modules-"))
        _ctx.set_platform(mock_platform(self.root, online))
        _ctx.set_renderer(QtRenderer())
        self.app = _ctx.compose_app()           # the CLI's App IS the one App
        self.app.start_session()
        self.app.dispatch(ConnectDevice(key=_KEY))
        self._cli, self._runner = cli, CliRunner()
        self.api = loopback_client(build_app(trcc=self.app, serve=False))
        self.gui = TRCCApp(app=self.app)
        self.gui.replay_initial_devices()
        self.gui.show()
        self.qtgui = MainWindow(self.app)
        self.qtgui.show()
        self.settle()
        self.gui._show_view("modules")
        self.qtgui._on_navigate("modules")
        self.settle()
        self.failed: list[str] = []

    # ── plumbing ──────────────────────────────────────────────────────────

    def settle(self, seconds: float = 0.4) -> None:
        """Let queued signals and background answers land."""
        import time

        from PySide6.QtCore import QCoreApplication

        log.debug("settle: %.1fs", seconds)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)

    def cli(self, *args: str) -> str:
        """Run ``trcc <args>`` on the one App; its output.

        The CLI sets logging up as its own process would, on the runner's
        captured output -- closed once the command returns, so a later line
        from a background thread would hit a closed stream.  A real CLI is
        its own process; here the drive's logging is put back after each."""
        log.info("cli: %s", args)
        root = logging.getLogger()
        handlers, level = list(root.handlers), root.level
        try:
            result = self._runner.invoke(self._cli, list(args))
        finally:
            root.handlers[:] = handlers
            root.setLevel(level)
        self.settle()
        return result.output

    def views(self) -> dict[str, Any]:
        """Each window's Modules page."""
        from trcc.ui.qt_modules import ModulesView

        found = {}
        for face, window in (("gui", self.gui), ("qtgui", self.qtgui)):
            pages = window.findChildren(ModulesView)
            if pages:
                found[face] = pages[0]
        log.debug("views: %s", sorted(found))
        return found

    def check(self, what: str, got: object, want: object) -> None:
        """One check: PASS when *got* is *want*."""
        ok = got == want
        log.info("check: %s -> %s", what, ok)
        print(f"{'PASS' if ok else 'FAIL'}  {what}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))
        if not ok:
            self.failed.append(what)

    # ── what each UI says about a module ─────────────────────────────────

    def states(self, key: str) -> dict[str, object]:
        """*key* as each UI shows it: on, off, or absent."""
        log.debug("states: %s", key)
        out: dict[str, object] = {}
        for face, view in self.views().items():
            switch = view.switches.get(key)
            out[f"{face} page"] = None if switch is None else switch.text().lower()
        listed = {line.split("]")[1].split()[0]: line[3:6].strip()
                  for line in self.cli("system", "modules", "list").splitlines()
                  if line.startswith("  [")}
        out["cli"] = listed.get(key)
        modules = self.api.get("/system/modules").json()["modules"]
        api = {m["key"]: ("on" if m["enabled"] else "off") for m in modules}
        out["api"] = api.get(key)
        return out

    def entries(self, key: str) -> dict[str, bool]:
        """Whether *key*'s way in is shown: its mode tab or sidebar button in
        the gui (the Visualizer has a tab, RGB a button), its sidebar entry in
        the qtgui."""
        log.debug("entries: %s", key)
        gui = self.gui._module_tabs.get(key) or self.gui.uc_device.view_buttons[key]
        return {"gui tab": not gui.isHidden(),
                "qtgui entry": not self.qtgui._sidebar._buttons[key].isHidden()}

    def everywhere(self, value: object) -> dict[str, object]:
        log.debug("everywhere: %r", value)
        return dict.fromkeys(("gui page", "qtgui page", "cli", "api"), value)

    # ── the steps ─────────────────────────────────────────────────────────

    def built_in_start_on(self) -> None:
        print("\n== 1. the built-in modules start on, in every UI")
        for key in BUILT_IN:
            self.check(f"{key} on everywhere", self.states(key), self.everywhere("on"))
            self.check(f"{key} tab + entry shown", self.entries(key),
                       {"gui tab": True, "qtgui entry": True})

    def cli_off_api_on(self) -> None:
        print("\n== 2. CLI switches the Visualizer off, the API on")
        self.cli("system", "modules", "off", "visualizer")
        self.check("off from the CLI: every UI says off", self.states("visualizer"),
                   self.everywhere("off"))
        self.check("off: tab + entry hidden", self.entries("visualizer"),
                   {"gui tab": False, "qtgui entry": False})
        self.api.post("/system/modules/visualizer", json={"enabled": True})
        self.settle()
        self.check("on from the API: every UI says on", self.states("visualizer"),
                   self.everywhere("on"))
        self.check("on: tab + entry back", self.entries("visualizer"),
                   {"gui tab": True, "qtgui entry": True})

    def window_switches(self) -> None:
        print("\n== 3. the gui's switch turns RGB off, the qtgui's on")
        self.views()["gui"].switches["rgb"].click()
        self.settle()
        self.check("off from the gui: every UI says off", self.states("rgb"),
                   self.everywhere("off"))
        self.views()["qtgui"].switches["rgb"].click()
        self.settle()
        self.check("on from the qtgui: every UI says on", self.states("rgb"),
                   self.everywhere("on"))

    def get_modules(self, online: bool) -> None:
        print(f"\n== 4. Get modules in every UI ({'the live index' if online else 'offline'})")
        from trcc.ui.presentation.module_catalog import NONE_LISTED

        cli = said(self.cli("system", "modules", "available"))
        api = self.api.get("/system/modules/available")
        pages = {}
        for face, view in self.views().items():
            view.open_catalog()
        self.settle(6.0 if online else 1.0)
        for face, view in self.views().items():
            pages[face] = view.catalog_note or (NONE_LISTED if not view.catalog else "listed")
            view.close_catalog()
        self.settle()
        if online:
            self.check("CLI says the list is empty", cli, NONE_LISTED)
            self.check("API lists no module", (api.status_code, api.json().get("modules")),
                       (200, []))
            self.check("both pages say the list is empty", pages,
                       {"gui": NONE_LISTED, "qtgui": NONE_LISTED})
        else:
            self.check("offline: every UI says it could not be read",
                       {"cli": "could not be read" in cli,
                        **{face: "could not be read" in note for face, note in pages.items()}},
                       {"cli": True, "gui": True, "qtgui": True})

    def developer_round_trip(self) -> None:
        print("\n== 5. developer mode: install from a folder, switch on, remove")
        views = self.views()
        views["qtgui"].developer.switch.click()
        self.settle()
        views = self.views()
        self.check("developer mode on from the qtgui: both pages show the folder button",
                   {face: v.developer.button is not None for face, v in views.items()},
                   {"gui": True, "qtgui": True})
        folder = self.root / "hello"
        shutil.copytree(_REPO / "dev/module_template/hello", folder,
                        ignore=shutil.ignore_patterns("__pycache__"))
        said = self.cli("system", "modules", "install-folder", str(folder), "--yes")
        self.check("the CLI installs hello from its folder", "installed unsigned" in said, True)
        self.check("hello listed, off, in every UI", self.states("hello"), self.everywhere("off"))
        origin = {m["key"]: m.get("origin") for m in
                  self.api.get("/system/modules").json()["modules"]}.get("hello")
        self.check("the API says hello is unsigned", origin, "unsigned")
        self.api.post("/system/modules/hello", json={"enabled": True})
        self.settle()
        self.check("on from the API: every UI says on", self.states("hello"),
                   self.everywhere("on"))
        page = self.views()["qtgui"]
        page.ask = _yes
        page.removes["hello"].click()
        self.settle(1.5)
        self.check("removed from the qtgui: gone from every UI", self.states("hello"),
                   self.everywhere(None))
        self.cli("system", "modules", "developer", "off")
        self.check("developer mode off from the CLI: no folder button anywhere",
                   {face: v.developer.button is not None for face, v in self.views().items()},
                   {"gui": False, "qtgui": False})

    def close(self) -> None:
        log.info("close")
        self.gui.close()
        self.qtgui.close()
        self.app.close()


#: A log record the runner mixes into a command's output (warnings go to the
#: terminal): ``2026-10-31T12:19:19.908 WARNING [pid] ...``.
_LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d")


def said(output: str) -> str:
    """What a CLI command printed for the user: its first line that is not a
    log record (an in-process CLI adds a note about the panel after it)."""
    lines = [line for line in output.splitlines() if line and not _LOG_LINE.match(line)]
    log.debug("said: %d line(s) of %d", len(lines), len(output.splitlines()))
    return lines[0] if lines else ""


def _yes(question: str, details: str, confirm: str) -> bool:
    log.info("_yes: %s (%s)", question, confirm)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--online", action="store_true",
                        help="read the live module index (else the index is offline)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    drive = Drive(args.online)
    steps: list[Callable[[], None]] = [
        drive.built_in_start_on, drive.cli_off_api_on, drive.window_switches]
    for step in steps:
        step()
    drive.get_modules(args.online)
    drive.developer_round_trip()
    drive.close()
    print(f"\n{len(drive.failed)} failed" + (": " + json.dumps(drive.failed) if drive.failed else ""))
    return len(drive.failed)


if __name__ == "__main__":
    sys.exit(main())
