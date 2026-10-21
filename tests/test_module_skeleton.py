"""The ``hello`` skeleton, ``new_module.py``, and the guide that explains them.

The skeleton is loaded through the real catalog and the real import path a
built-in module uses (``trcc.modules.<key>``), so a change to the module API
breaks here instead of leaving a stale template.  It carries one of each
thing a module may have -- a Query, words, a CLI command, an API route, a
gui page and a qtgui panel -- so every hook a module plugs into is driven.
"""
from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from trcc import ipc
from trcc.adapters.modules import read_manifest, specs
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import Command, SetModuleEnabled
from trcc.core.i18n import tr
from trcc.core.models import MODULE_CAPABILITIES, ModuleManifest

from .conftest import FakePlatform, loopback_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import new_module

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATE = _REPO / "dev" / "module_template"


@pytest.fixture
def hello(module_root: Callable[[Path], Path]) -> Path:
    """The skeleton, installed as a built-in is."""
    return module_root(_TEMPLATE)


def _greet(**kwargs: Any) -> Command[Any]:
    """Hello's own Query -- reached by its wire name, as any UI reaches it."""
    return ipc.decode_command({"command": "Greet", "kwargs": kwargs})


def _app(tmp_home: Path, on: bool = False) -> App:
    app = App(platform=FakePlatform(tmp_home), renderer=QtRenderer(),
              modules=specs(_TEMPLATE))
    if on:
        assert app.dispatch(SetModuleEnabled(key="hello", enabled=True)).ok
    return app


def test_the_skeleton_runs_every_hook_through_the_app(
        hello: Path, tmp_home: Path, caplog: pytest.LogCaptureFixture) -> None:
    app = App(platform=FakePlatform(tmp_home), modules=specs(hello))
    caplog.set_level(logging.INFO, logger="trcc.modules.hello.app")
    try:
        app.start_session()
        assert app.dispatch(SetModuleEnabled(key="hello", enabled=True)).ok
        app.dispatch(SetModuleEnabled(key="hello", enabled=False))
    finally:
        app.close()
    said = [r.getMessage() for r in caplog.records
            if r.name == "trcc.modules.hello.app"]
    assert said == ["hello: pre_enable", "hello: enable", "hello: start",
                    "hello: hello was switched on",     # it heard its own switch
                    "hello: stop", "hello: disable"]


def test_the_skeletons_manifest_is_valid() -> None:
    manifest = read_manifest(_TEMPLATE / "hello" / "module.json")
    assert (manifest.key, manifest.default_on, manifest.capabilities,
            Path(manifest.icon).is_file()) == ("hello", False, ("events",), True)


def test_a_modules_query_runs_in_the_module_and_only_while_it_is_on(
        hello: Path, tmp_home: Path) -> None:
    """``Greet`` is answered by Hello's own root, never the App; while Hello
    is off every UI is told so.  MUTATION CHECK: drop ``MODULE`` from
    ``Greet`` and the off case reaches a module that is not loaded."""
    app = _app(tmp_home)
    off = app.dispatch(_greet(name="Ada"))
    app.dispatch(SetModuleEnabled(key="hello", enabled=True))
    on = app.dispatch(_greet(name="Ada"))
    assert (off.ok, off.message) == (False, "Hello is off -- turn it on in Modules")
    assert (on.ok, on.message, type(on).__name__) == (True, "Hello, Ada!", "HelloResult")


def test_its_types_cross_the_wire_like_the_apps_own(hello: Path) -> None:
    """Reading the catalog teaches IPC a module's Commands and Results, so a
    window talking to the shared App sends and reads them."""
    specs(hello)
    command = _greet(name="Ada")
    hello_result: Any = ipc.RESULT_TYPES["HelloResult"]
    result: Any = ipc.decode_result(ipc.encode_result(
        hello_result(ok=True, greeting="hi", message="hi")))
    assert ipc.decode_command(ipc.encode_command(command)) == command
    assert (type(result).__name__, result.greeting) == ("HelloResult", "hi")


def test_a_name_the_app_already_uses_is_refused() -> None:
    """The wire knows a class by its name alone, so a module may not reuse
    one.  MUTATION CHECK: drop the clash check and SendColor is replaced."""
    from dataclasses import dataclass
    from types import SimpleNamespace

    from trcc.core.commands import SendColor

    @dataclass(frozen=True, slots=True)
    class SendColor2(Command[Any]):   # renamed below: same name, other class
        def execute(self, app: Any) -> Any: ...
    SendColor2.__name__ = "SendColor"
    with pytest.raises(ValueError, match="the app already has a SendColor"):
        ipc.register_types(SimpleNamespace(SendColor=SendColor2))
    assert ipc.COMMAND_TYPES["SendColor"] is SendColor


def test_its_words_join_the_translations(hello: Path) -> None:
    specs(hello)
    assert (tr("Hello", "de"), tr("Hello", "fr")) == ("Hallo", "Hello")


def test_the_cli_runs_its_command_on_or_off(
        hello: Path, cli_runner: Any, cli_app: App) -> None:
    """An installed module's command is in the CLI whether it is on or off;
    off, the App says so.  MUTATION CHECK: add only on modules and the first
    call is "No such command"."""
    from trcc.ui.cli import main

    main.add_module_commands()
    off = cli_runner.invoke(main.app, ["hello", "Ada"])
    cli_app.dispatch(SetModuleEnabled(key="hello", enabled=True))
    on = cli_runner.invoke(main.app, ["hello", "Ada"])
    assert (off.exit_code, off.output.strip().splitlines()[-1]) == (
        1, "Hello is off -- turn it on in Modules")
    assert (on.exit_code, on.output.strip()) == (0, "Hello, Ada!")


def test_the_cli_adds_late_modules_before_it_runs(
        hello: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A module the import-time pass missed (its own ``ui/cli`` was mid-import)
    is added when ``main()`` runs.  MUTATION CHECK: drop the call in ``main``
    and hello is not added."""
    from trcc.ui.cli import main

    seen: list[bool] = []

    def run() -> None:
        seen.append("hello" in main._ADDED)

    monkeypatch.setattr(main, "app", run)
    main.main()
    assert seen == [True]


@pytest.mark.parametrize("loading, level", [(True, None), (False, logging.ERROR)])
def test_a_face_still_loading_is_not_a_fault(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        loading: bool, level: int | None) -> None:
    """A face asked while it is mid-import (it imported the UI first) adds
    nothing yet and is no ERROR; a loaded face missing the name is.
    MUTATION CHECK: drop the ``_initializing`` branch and the first case
    logs an ERROR."""
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    from trcc import modules
    from trcc.ui import _module_faces

    part = ModuleType("trcc.modules.x.ui.cli")
    part.__spec__ = ModuleSpec(part.__name__, None)
    part.__spec__.__dict__["_initializing"] = loading

    def load_face(key: str, face: str) -> ModuleType:
        return part

    monkeypatch.setattr(modules, "load_face", load_face)
    with caplog.at_level(logging.DEBUG, logger=_module_faces.__name__):
        found = _module_faces.contribution("x", "cli", "COMMANDS")
    worst = max((r.levelno for r in caplog.records
                 if "exports no" in r.getMessage()), default=None)
    assert (found, worst) == (None, level)


def test_the_api_serves_its_route(hello: Path, tmp_home: Path) -> None:
    """Asked for when the API is built: a module added since its file was
    imported is served.  MUTATION CHECK: fold module_routers back into the
    import-time ROUTERS and Hello's route is missing."""
    from trcc.ui.api import main

    with loopback_client(main.build_app(trcc=_app(tmp_home, on=True))) as client:
        answer = client.get("/modules/hello", params={"name": "Ada"})
    assert (answer.status_code, answer.json()["greeting"]) == (200, "Hello, Ada!")


def test_the_gui_shows_its_tab_while_it_is_on(
        hello: Path, qtbot: Any, tmp_home: Path) -> None:
    """On: a tab in the first module slot opening Hello's page.  Off: the tab
    hides and the window leaves the page.  MUTATION CHECK: skip the hiding in
    ``_sync_module_tabs`` and the tab stays."""
    from trcc.ui.gui.constants import Layout
    from trcc.ui.gui.trcc_app import TRCCApp

    app = _app(tmp_home, on=True)
    window = TRCCApp(app=app)
    qtbot.addWidget(window)
    tab = window._module_tabs["hello"]
    tab.click()
    shown: Any = window.panel_stack.currentWidget()
    app.dispatch(SetModuleEnabled(key="hello", enabled=False))
    window._sync_module_tabs()
    assert (tab.geometry().getRect(), shown is window._module_pages["hello"],
            shown.label.text()) == (Layout.TAB_MODULE_SLOTS[0], True, "Hello, world!")
    assert (tab.isVisibleTo(window), window.panel_stack.currentIndex()) == (False, 0)
    window.close()


def test_the_qtgui_shows_its_entry_while_it_is_on(
        hello: Path, qtbot: Any, tmp_home: Path) -> None:
    """On: an entry above Modules opening Hello's panel.  Off: the entry hides
    and the window goes back to Devices."""
    from trcc.ui.qtgui.app import MainWindow

    app = _app(tmp_home, on=True)
    window = MainWindow(app)
    qtbot.addWidget(window)
    window._on_navigate("hello")
    shown: Any = window._content.currentWidget()
    app.dispatch(SetModuleEnabled(key="hello", enabled=False))
    window._sync_module_panels()
    entry = window._sidebar._buttons["hello"]
    assert (shown is window._panels["hello"], shown.label.text()) == (
        True, "Hello, world!")
    assert (entry.isVisibleTo(window),
            window._content.currentWidget() is window._panels["devices"]) == (
        False, True)
    window.close()


def test_new_module_makes_a_module_that_loads(
        tmp_path: Path, module_root: Callable[[Path], Path], tmp_home: Path) -> None:
    folder = new_module.create(
        "weather", "Weather", "Today's forecast as a panel background.", repo=tmp_path)
    manifest = read_manifest(folder / "module.json")
    assert (manifest.title, manifest.default_on, manifest.guide) == (
        "Weather", False,
        "https://github.com/Lexonight1/thermalright-trcc-linux/blob/main/"
        "doc/GUIDE_MODULES.md#weather")
    module_root(folder.parent)
    app = App(platform=FakePlatform(tmp_home), modules=specs(folder.parent))
    assert app.dispatch(SetModuleEnabled(key="weather", enabled=True)).ok
    assert type(app.modules._modules["weather"]).__name__ == "WeatherModule"
    answer = app.dispatch(ipc.decode_command({"command": "Greet", "kwargs": {}}))
    assert answer.message == "Weather, world!"


@pytest.mark.parametrize("key", ["Weather", "w", "2fast", "rgb-lights", "a" * 40])
def test_new_module_refuses_a_key_that_is_not_a_package_name(
        tmp_path: Path, key: str) -> None:
    with pytest.raises(SystemExit):
        new_module.create(key, "X", "y", repo=tmp_path)


def _table(heading: str) -> dict[str, str]:
    """The first table under *heading* in GUIDE_MODULES.md: first column
    (back-ticks removed) -> second column."""
    text = (_REPO / "doc" / "GUIDE_MODULES.md").read_text(encoding="utf-8")
    section = text.split(f"### {heading}\n", 1)[1]
    rows = re.findall(r"^\| `([^`]+)` \| (.+?) \|$", section.split("\n### ", 1)[0], re.M)
    return dict(rows)


def test_the_guide_lists_every_capability_as_the_code_names_it() -> None:
    """The guide's table IS the list: a capability added in code without a
    line here, or a line left after one is gone, fails."""
    assert _table("Capabilities") == MODULE_CAPABILITIES


def test_the_guide_lists_every_manifest_field() -> None:
    assert list(_table("module.json")) == [f.name for f in fields(ModuleManifest)]


def test_every_folder_of_module_code_is_a_package() -> None:
    """A module's folders of Python code each carry an ``__init__.py``.
    Without one Python still imports it, as a namespace package, so every
    test passed -- but CI's ``stale_refs --check`` reported the visualizer's
    services "does not exist" (2026-10-21), and anything that walks packages
    may skip it the same way.  MUTATION CHECK: delete the visualizer's
    ``services/__init__.py``."""
    roots = [_REPO / "src" / "trcc" / "modules", _TEMPLATE / "hello"]
    bare = sorted(str(folder.relative_to(_REPO))
                  for root in roots for folder in [root, *root.rglob("*")]
                  if folder.is_dir() and folder.name != "__pycache__"
                  and "assets" not in folder.relative_to(root).parts
                  and any(folder.glob("*.py")) and not (folder / "__init__.py").is_file())
    assert bare == []
