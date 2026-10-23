#!/usr/bin/env python3
"""Every installed module, driven end to end on the mock -- zero hardware.

The modules' half of "thoroughly test it": what the unit tests check one
piece at a time, driven together through the real App, every UI and a real
daemon, for EVERY module in ``src/trcc/modules`` -- so a module added later is
covered without touching this file.  Per module:

  Manifest      it loads; its capabilities are ones TRCC knows; its icon is
                there; its ``i18n.json`` is complete in every language
  Switching     on -> off -> on through the App: ``ModuleChanged`` heard each
                time; its own Query refused while it is off, answered when on
  Letting go    switched off, it holds nothing: no event handler, no frame
                watcher, no thread it started
  Faces         its CLI commands registered; its API routes served; its gui
                tab or view button and its qtgui entry shown while on, hidden
                while off
  Capabilities  each one it declares is the one its switch-on asks for; one it
                does not declare is refused

And once, for the fleet:

  Daemon        a real daemon on the mock, reached from this process as every
                UI reaches it: the modules listed, each switched off and on,
                ``ModuleChanged`` streamed back, its Query refused and answered

Run it as it is; it starts itself again on a private display and session bus
with a throwaway home, so no window, tray icon or saved setting reaches your
desktop or ``dev/.trcc`` (memory: reference_drive_windows_on_xvfb)::

    PYTHONPATH=src python3.12 dev/smoke_modules.py [--template]

``--template`` adds ``dev/module_template/hello`` -- every face a module can
have -- to what is driven.  Without ``xvfb-run`` and ``dbus-run-session`` the
window probes are skipped, saying so.  Exit 1 on any FAIL.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import site
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from operator import attrgetter
from pathlib import Path
from typing import Any

_DEV = Path(__file__).resolve().parent
_REPO = _DEV.parent
sys.path[:0] = [str(_DEV), str(_REPO / "src"), str(_REPO)]

from _smoke_runtime import Section, print_section, print_summary_and_exit

#: Marks the private re-run, so it does not start itself again.
_PRIVATE = "TRCC_SMOKE_MODULES_PRIVATE"
#: One scripted 320x320 panel -- the one the tests use.
_SPECS: list[dict[str, Any]] = [{"vid": "0402", "pid": "3922", "fbl": 100}]
_TEMPLATE = _REPO / "dev" / "module_template"


# ── the private run ──────────────────────────────────────────────────────────

def _relaunch(argv: list[str]) -> int | None:
    """This smoke again, on a private display and session bus with a
    throwaway home -- or None to run here, with the window probes off."""
    if os.environ.get(_PRIVATE) == "1":
        return None
    tools = [shutil.which("xvfb-run"), shutil.which("dbus-run-session")]
    if None in tools:
        print("  xvfb-run / dbus-run-session not found -- the window probes "
              "are skipped")
        os.environ[_PRIVATE] = "0"
        return None
    home = Path(tempfile.mkdtemp(prefix="trcc-smoke-modules-"))
    (home / "run").mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAYLAND_DISPLAY", "DISPLAY", "QT_QPA_PLATFORM")}
    env.update({_PRIVATE: "1", "HOME": str(home), "XDG_RUNTIME_DIR": str(home / "run"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local/share"),
                # PySide6 may be a per-user install; the throwaway home hides it.
                "PYTHONUSERBASE": site.getuserbase(), "TRCC_DAEMON": "0"})
    command = [*tools, "--", sys.executable, str(Path(__file__).resolve()), *argv]
    command.insert(1, "-a")                      # xvfb-run -a: a free display
    try:
        return subprocess.run(command, env=env, check=False).returncode
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _add_template() -> None:
    """``hello`` becomes ``trcc.modules.hello``, as a built-in is."""
    import trcc.modules as package

    package.__path__.append(str(_TEMPLATE))


# ── what a module holds, and its own Query ──────────────────────────────────

def _held(app: Any) -> tuple[Counter[str], Counter[str]]:
    """The App's event handlers by event, and its threads by name -- a
    module's frame watcher is a ``FrameSent`` handler the App holds for it."""
    from trcc import ipc

    handlers = Counter({name: app.events.subscriber_count(event)
                        for name, event in ipc.EVENT_TYPES.items()})
    threads = Counter(t.name for t in threading.enumerate())
    return +handlers, threads


def _settled(app: Any, before: tuple[Counter[str], Counter[str]],
             seconds: float = 3.0) -> tuple[Counter[str], Counter[str]]:
    """What the App holds once its threads have had *seconds* to end."""
    deadline = time.monotonic() + seconds
    now = _held(app)
    while now != before and time.monotonic() < deadline:
        time.sleep(0.1)
        now = _held(app)
    return now


def _query(key: str) -> type | None:
    """Module *key*'s own Query that takes no arguments, if it has one."""
    from trcc.core.commands._base import ModuleQuery

    found = []
    prefix = f"trcc.modules.{key}.core"
    for name, module in list(sys.modules.items()):
        if not (name == prefix or name.startswith(prefix + ".")):
            continue
        for obj in vars(module).values():
            if (isinstance(obj, type) and issubclass(obj, ModuleQuery)
                    and getattr(obj, "MODULE", "") == key
                    and dataclasses.is_dataclass(obj)
                    and all(f.default is not dataclasses.MISSING
                            or f.default_factory is not dataclasses.MISSING
                            for f in dataclasses.fields(obj))):
                found.append(obj)
    return min(found, key=attrgetter("__name__")) if found else None


# ── per module ───────────────────────────────────────────────────────────────

class ModuleSmoke:
    """One App on the mock, and every probe of one module against it."""

    def __init__(self, app: Any, key: str) -> None:
        self.app, self.key = app, key
        self.manifest = app.modules.manifest(key)

    def switch(self, on: bool) -> None:
        from trcc.core.commands import SetModuleEnabled

        result = self.app.dispatch(SetModuleEnabled(key=self.key, enabled=on))
        if not result.ok:
            raise AssertionError(f"switching {'on' if on else 'off'}: {result.message}")

    # Manifest
    def manifest_loads(self) -> str:
        from trcc.core.models import MODULE_CAPABILITIES

        unknown = set(self.manifest.capabilities) - set(MODULE_CAPABILITIES)
        assert not unknown, f"unknown capabilities {sorted(unknown)}"
        folder = _folder(self.key)
        if self.manifest.icon:
            assert (folder / self.manifest.icon).is_file(), f"no {self.manifest.icon}"
        return f"capabilities {list(self.manifest.capabilities) or 'none'}"

    def words(self, section: Section) -> None:
        import json

        sys.path.insert(0, str(_DEV / "tools"))
        from i18n_gaps import gaps

        table = _folder(self.key) / "i18n.json"
        if not table.is_file():
            section.warn("words", "no i18n.json -- it speaks English only")
            return
        missing = gaps(json.loads(table.read_text(encoding="utf-8")))
        if missing:
            section.warn("words", f"{len(missing)} language(s) incomplete: "
                         f"{sorted(missing)[:5]}")
        else:
            section.ok("words", "every language complete")

    # Switching
    def switching(self) -> str:
        from trcc.core.events import ModuleChanged

        heard: list[tuple[str, bool]] = []

        def hear(event: ModuleChanged) -> None:
            heard.append((event.key, event.enabled))

        query = _query(self.key)
        self.app.events.subscribe(ModuleChanged, hear)
        try:
            self.switch(False)
            refused = self.app.dispatch(query()) if query else None
            self.switch(True)
            answered = self.app.dispatch(query()) if query else None
        finally:
            self.app.events.unsubscribe(ModuleChanged, hear)
        mine = [h for h in heard if h[0] == self.key]
        assert mine == [(self.key, False), (self.key, True)], f"heard {mine}"
        if query is None:
            return "heard both switches; it has no argument-free Query to ask"
        assert refused is not None and answered is not None
        assert not refused.ok and "is off" in refused.message, \
            f"while off, {query.__name__} answered {refused.message!r}"
        assert "is off" not in answered.message, \
            f"while on, {query.__name__} said {answered.message!r}"
        return f"heard both switches; {query.__name__} refused off, answered on"

    # Letting go
    def letting_go(self) -> str:
        self.switch(False)
        before = _settled(self.app, _held(self.app), 0.5)
        self.switch(True)
        time.sleep(0.5)
        during = _held(self.app)
        self.switch(False)
        after = _settled(self.app, before)
        self.switch(True)
        kept = after[0] - before[0]
        assert not kept, f"handlers left: {dict(kept)}"
        extra = after[1] - before[1]
        assert not extra, f"threads left running: {sorted(extra.elements())}"
        took = during[0] - before[0]
        return (f"on: {dict(took) or 'no handlers'}, "
                f"{sum((during[1] - before[1]).values())} thread(s); off: none")

    # Faces
    def cli(self) -> str:
        from trcc.ui._module_faces import contribution
        from trcc.ui.cli import main as cli

        commands = contribution(self.key, "cli", "COMMANDS")
        if commands is None:
            return "adds no CLI commands"
        cli.add_module_commands()
        group = cli._GROUPS[str(contribution(self.key, "cli", "GROUP"))]
        names = {c.name for c in group.registered_commands}
        wanted = {c.name for c in commands.registered_commands}
        assert wanted <= names, f"missing {sorted(wanted - names)}"
        return f"{sorted(wanted)}"

    def api(self) -> str:
        from trcc.ui._module_faces import contribution
        from trcc.ui.api.main import build_app

        routers = contribution(self.key, "api", "ROUTERS")
        if not routers:
            return "adds no API routes"
        # What the API answers, as its schema says -- newer FastAPI keeps an
        # included router as one node in ``app.routes``, so walking those
        # finds none of a module's paths.
        served = set(build_app(trcc=self.app).openapi()["paths"])
        wanted = {r.path for router in routers for r in router.routes}
        assert wanted <= served, f"not served: {sorted(wanted - served)}"
        return f"{len(wanted)} route(s)"

    def gui(self) -> str:
        from trcc.ui.gui.trcc_app import TRCCApp

        window = TRCCApp(app=self.app)
        try:
            places = {**window._module_tabs, **window.uc_device.view_buttons}
            if self.key not in places:
                return "adds no gui tab or view"
            shown = places[self.key].isVisibleTo(window)
            self.switch(False)
            window._sync_modules()
            hidden = not places[self.key].isVisibleTo(window)
            self.switch(True)
            assert (shown, hidden) == (True, True), f"shown={shown} hidden={hidden}"
            kind = "tab" if self.key in window._module_tabs else "view button"
            return f"{kind} shown on, hidden off"
        finally:
            window.close()
            window.deleteLater()

    def gui_on_late(self) -> str:
        """His flow on real glass (2026-10-22): the module OFF when the
        window opens, switched on from the Modules page, then back to the
        panel -- its page must be told that panel, or it drops every click.
        A page built at start-up cannot meet this, which is why ``gui``
        never could."""
        from _mock_bootstrap import select_device

        from trcc.ui._module_faces import contribution
        from trcc.ui.gui.trcc_app import TRCCApp

        page_class = contribution(self.key, "gui", "PAGE", optional=True)
        if page_class is None:
            return "adds no gui tab"
        told: list[str] = []
        real = page_class.set_device_key

        def telling(page: Any, key: str) -> None:
            told.append(key)
            real(page, key)

        page_class.set_device_key = telling
        self.switch(False)
        window = TRCCApp(app=self.app)
        try:
            window.replay_initial_devices()
            panel = window._lcd_selection.key
            assert panel, "no LCD selected after start-up"
            window._show_view("modules")
            self.switch(True)
            window._sync_modules()
            select_device(window, panel)             # the sidebar click
            assert told[-1:] == [panel], f"its page was told {told}, not {panel}"
            return f"on from Modules, back to {panel}: its page has the panel"
        finally:
            page_class.set_device_key = real
            if self.key not in self.app.modules.on:
                self.switch(True)
            window.close()
            window.deleteLater()

    def qtgui(self) -> str:
        from trcc.ui.qtgui.app import MainWindow

        window = MainWindow(self.app)
        try:
            if self.key not in window._module_panels:
                return "adds no qtgui panel"
            entry = window._sidebar._buttons[self.key]
            shown = entry.isVisibleTo(window)
            self.switch(False)
            window._sync_module_panels()
            hidden = not entry.isVisibleTo(window)
            self.switch(True)
            assert (shown, hidden) == (True, True), f"shown={shown} hidden={hidden}"
            return f"entry {entry.text()!r} shown on, hidden off"
        finally:
            window.close()
            window.deleteLater()

    # Capabilities
    def capabilities(self, section: Section) -> None:
        from trcc.core.errors import ModuleUnavailable
        from trcc.core.events import ModuleChanged
        from trcc.core.models import MODULE_CAPABILITIES
        from trcc.services.modules import ModuleScope

        module_class = type(self.app.modules.root(self.key))
        self.switch(False)
        try:
            for capability in self.manifest.capabilities:
                less = dataclasses.replace(self.manifest, capabilities=tuple(
                    c for c in self.manifest.capabilities if c != capability))
                scope = ModuleScope(self.app, less)
                module = module_class()
                try:
                    module.enable(scope)
                except ModuleUnavailable as e:
                    scope.release()
                    if repr(capability) in str(e):
                        section.ok(f"needs {capability}", "refused without it")
                    else:
                        section.fail(f"needs {capability}", e)
                    continue
                module.disable(scope)
                scope.release()
                section.ok(f"needs {capability}", "not asked for at switch-on "
                           "(its session or Commands may)")
            undeclared = [c for c in MODULE_CAPABILITIES
                          if c not in self.manifest.capabilities]
            if not undeclared:
                section.skip("undeclared refused", "it declares every capability")
                return
            scope = ModuleScope(self.app, self.manifest)
            try:
                if undeclared[0] == "events":
                    scope.subscribe(ModuleChanged, _ignore)
                else:
                    getattr(scope, undeclared[0])
            except ModuleUnavailable:
                section.ok("undeclared refused", f"{undeclared[0]!r} refused")
            else:
                section.fail("undeclared refused",
                             AssertionError(f"{undeclared[0]!r} was handed over"))
            finally:
                scope.release()
        finally:
            self.switch(True)


def _ignore(event: object) -> None:
    """A handler that wants nothing -- what a refused subscription offers."""


def _folder(key: str) -> Path:
    from trcc import modules

    return modules.folders()[key]


# ── the daemon ───────────────────────────────────────────────────────────────

def _serve(root: Path, template: bool) -> int:
    """``--serve``: the real daemon, on the mock, in *root*."""
    from tests.mock_platform import MockPlatform
    from trcc.adapters.infra.logging import configure_logging
    from trcc.daemon import run_daemon

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if template:
        _add_template()
    platform = MockPlatform(_SPECS, root)
    configure_logging(platform.paths().log_file())
    return run_daemon(platform=platform)


def _daemon(keys: list[str], template: bool) -> Section:
    """Every module through a real daemon, as every UI reaches it."""
    from trcc import ipc
    from trcc.core.commands import ListModules
    from trcc.core.events import ModuleChanged
    from trcc.daemon import kill_daemon
    from trcc.proxy import AppProxy

    section = Section("Daemon (a real one, on the mock)")
    if os.environ.get(_PRIVATE) != "1":
        section.skip("daemon", "only in the private run: it binds a socket")
        return section
    root = Path(tempfile.mkdtemp(prefix="trcc-smoke-daemon-"))
    command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(root)]
    server = subprocess.Popen([*command, *(["--template"] if template else [])],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not ipc.wait_for_daemon(timeout=20.0):
            server.kill()
            out, err = server.communicate()
            section.fail("starts", RuntimeError(f"never bound: {(err or out)[-400:]}"))
            return section
        proxy = AppProxy()
        listed = {m.key for m in proxy.dispatch(ListModules()).modules}
        section.run("lists every module", lambda: _same(set(keys), listed))
        heard: list[tuple[str, bool]] = []
        streamed = threading.Event()

        def hear(event: ModuleChanged) -> None:
            heard.append((event.key, event.enabled))
            streamed.set()

        proxy.events.subscribe(ModuleChanged, hear)
        # The stream opens on its own thread; a window is open long before
        # its user acts, so the harness waits for the App's acknowledgement
        # too -- an event published before it is not this client's.
        deadline = time.monotonic() + 5.0
        while not proxy._main.open and time.monotonic() < deadline:
            time.sleep(0.05)
        for key in keys:
            section.run(f"{key}: switched and streamed",
                        _switched_through(proxy, key, heard, streamed))
        proxy.close()
    finally:
        if not kill_daemon(timeout=5.0):
            server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(root, ignore_errors=True)
    return section


def _same(wanted: set[str], got: set[str]) -> str:
    assert wanted <= got, f"missing {sorted(wanted - got)}"
    return f"{sorted(got)}"


def _switched_through(proxy: Any, key: str, heard: list[tuple[str, bool]],
                   streamed: threading.Event) -> Callable[[], str]:
    """Module *key* off and on through *proxy*: each switch streamed back,
    its Query refused off and answered on."""
    from trcc.core.commands import ListModules, SetModuleEnabled

    def run() -> str:
        query = _query(key)
        listed = {m.key: m.enabled for m in proxy.dispatch(ListModules()).modules}
        if not listed[key]:                    # off by default: on first
            streamed.clear()
            assert proxy.dispatch(SetModuleEnabled(key=key, enabled=True)).ok
            assert streamed.wait(5.0), f"no ModuleChanged for {key} streamed back"
        states = []
        for on in (False, True):
            streamed.clear()
            result = proxy.dispatch(SetModuleEnabled(key=key, enabled=on))
            assert result.ok, result.message
            assert streamed.wait(5.0), f"no ModuleChanged for {key} streamed back"
            states.append(proxy.dispatch(query()) if query else None)
        assert [h for h in heard if h[0] == key][-2:] == [(key, False), (key, True)]
        if query is None:
            return "both switches streamed; no argument-free Query"
        off, on_ = states
        assert off is not None and not off.ok and "is off" in off.message, off
        assert on_ is not None and "is off" not in on_.message, on_
        return f"both switches streamed; {query.__name__} refused off, answered on"

    return run


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--template", action="store_true",
                    help="drive dev/module_template/hello too")
    ap.add_argument("--serve", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.serve is not None:
        return _serve(args.serve, args.template)
    relaunched = _relaunch(argv)
    if relaunched is not None:
        return relaunched
    windows = os.environ.get(_PRIVATE) == "1"
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb" if windows else "offscreen")
    if args.template:
        _add_template()

    from PySide6.QtWidgets import QApplication

    from tests.mock_platform import MockPlatform
    from trcc.adapters.modules import specs
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App

    qapp = QApplication.instance() or QApplication(sys.argv)
    root = Path(tempfile.mkdtemp(prefix="trcc-smoke-app-"))
    app = App(MockPlatform(_SPECS, root), renderer=QtRenderer(), modules=specs())
    app.start_session()
    keys = sorted(app.modules)
    print(f"\n  TRCC Smoke -- modules: {keys}\n")
    sections: list[Section] = []
    try:
        for key in keys:
            smoke = ModuleSmoke(app, key)
            if key not in app.modules.on:
                smoke.switch(True)
            section = Section(f"{key} -- {smoke.manifest.title}")
            section.run("manifest", smoke.manifest_loads)
            smoke.words(section)
            section.run("switching", smoke.switching)
            section.run("letting go", smoke.letting_go)
            section.run("cli", smoke.cli)
            section.run("api", smoke.api)
            if windows:
                section.run("gui", smoke.gui)
                section.run("gui, on from Modules", smoke.gui_on_late)
                section.run("qtgui", smoke.qtgui)
                qapp.processEvents()
            else:
                section.skip("gui / qtgui", "needs the private display")
            smoke.capabilities(section)
            sections.append(section)
            print_section(section)
        daemon = _daemon(keys, args.template)
        sections.append(daemon)
        print_section(daemon)
    finally:
        app.close()
        shutil.rmtree(root, ignore_errors=True)
    return print_summary_and_exit(sections)


if __name__ == "__main__":
    sys.exit(main())
