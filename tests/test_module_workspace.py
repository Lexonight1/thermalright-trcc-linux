"""The module workspace: an author's modules, run live from their own folder
while developer mode is on -- never as TRCC's own, never without the switch.

A module there was not installed and nobody reviewed it, so it needs no
install record and gets none: developer mode is its only gate.  Off, it is
listed and refused, saying how to run it; on, it runs from the folder as
written -- switching the mode again reads its files afresh.
"""
from __future__ import annotations

import logging
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trcc import modules as module_folders
from trcc.adapters.modules._catalog import WORKSPACE_OFF
from trcc.app import App
from trcc.core.commands import ListModules, SetDeveloperMode, SetModuleEnabled
from trcc.core.events import DeveloperModeChanged, ModuleChanged
from trcc.core.models import ModuleInfo
from trcc.ui.presentation.module_catalog import removable

from .conftest import FakePlatform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import new_module

_KEY = "probe"


def _workspace(tmp_home: Path) -> Path:
    return FakePlatform(tmp_home).paths().module_workspace_dir()


def _write(tmp_home: Path, key: str = _KEY) -> Path:
    """An author's new module, made in the workspace the way a scaffold makes it."""
    return new_module.create(key, key.title(), "A module being written.",
                             author="Ada Author", into=_workspace(tmp_home) / key)


def _write_broken(tmp_home: Path) -> Path:
    """Module "broken", whose author's ``_disable`` raises."""
    folder = _write(tmp_home, key="broken")
    app_py = folder / "app.py"
    app_py.write_text(app_py.read_text(encoding="utf-8").replace(
        'log.info("broken: disable")', 'raise RuntimeError("half-written")'),
        encoding="utf-8")
    return folder


def _info(app: App, key: str = _KEY) -> ModuleInfo:
    return next(m for m in app.dispatch(ListModules()).modules if m.key == key)


@pytest.fixture(autouse=True)
def _undone(module_root: Callable[[Path], Path], monkeypatch: pytest.MonkeyPatch,
            tmp_home: Path) -> None:
    """Every trace of a test's workspace undone afterwards: the modules search
    path is the process's, so a folder left on it would be found by the next
    test -- and anything on it that is not the user's reads as TRCC's own."""
    monkeypatch.setattr(module_folders, "_USERS", set(module_folders._USERS))
    module_root(_workspace(tmp_home))


def test_off_it_is_listed_and_says_how_to_run_it(tmp_home: Path) -> None:
    """MUTATION CHECK: ``workspace_spec`` refusing nothing while developer
    mode is off, and it runs."""
    _write(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    info = _info(app)
    refused = app.dispatch(SetModuleEnabled(key=_KEY, enabled=True))
    assert (info.origin, info.allowed, refused.ok, refused.message) == (
        "workspace", False, False, WORKSPACE_OFF)


def test_on_it_runs_live_from_the_folder(tmp_home: Path) -> None:
    folder = _write(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    heard: list[ModuleChanged] = []
    app.events.subscribe(ModuleChanged, heard.append)
    assert app.dispatch(SetDeveloperMode(on=True)).ok
    assert app.dispatch(SetModuleEnabled(key=_KEY, enabled=True)).ok
    info = _info(app)
    assert (info.origin, info.allowed, info.enabled) == ("workspace", True, True)
    assert Path(sys.modules[f"trcc.modules.{_KEY}"].__file__ or "").parent == folder
    assert ModuleChanged(key=_KEY, enabled=False) in heard


def test_switching_the_mode_reads_the_authors_edits(tmp_home: Path) -> None:
    """Off and on again: the module's files are read afresh, so an edit runs
    without restarting TRCC.  MUTATION CHECK: drop ``_forget_module`` from
    ``rescan_workspace`` and the old summary stays."""
    folder = _write(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    app.dispatch(SetModuleEnabled(key=_KEY, enabled=True))
    app_py = folder / "app.py"
    app_py.write_text(app_py.read_text(encoding="utf-8").replace(
        '"The smallest module there is."', '"Edited by its author."'), encoding="utf-8")
    app.dispatch(SetDeveloperMode(on=False))
    app.dispatch(SetDeveloperMode(on=True))
    assert app.modules.root(_KEY).summary == "Edited by its author."


def test_off_again_stops_it_and_keeps_the_choice(tmp_home: Path) -> None:
    """The user's "on" survives the switch: on again, and after a restart."""
    _write(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    app.dispatch(SetModuleEnabled(key=_KEY, enabled=True))
    app.dispatch(SetDeveloperMode(on=False))
    assert (_info(app).enabled, _info(app).allowed) == (False, False)
    app.dispatch(SetDeveloperMode(on=True))
    assert _info(app).enabled
    module_folders.forget(_KEY)
    again = App(platform=FakePlatform(tmp_home))
    assert _info(again).enabled


def test_off_stops_every_one_even_past_one_that_fails_switching_off(
        tmp_home: Path) -> None:
    """An author's half-written ``_disable`` raises: developer mode goes off
    all the same, and so does every workspace module -- the broken one too.
    What it said reaches every UI, on its tile and in the answer.

    MUTATION CHECK: drop the ``except`` in ``rescan_workspace`` and the
    switch raises with "healthy" still running."""
    app_py = _write_broken(tmp_home) / "app.py"
    _write(tmp_home, key="healthy")
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    for key in ("broken", "healthy"):
        app.dispatch(SetModuleEnabled(key=key, enabled=True))
    heard: list[object] = []
    app.events.subscribe(ModuleChanged, heard.append)
    app.events.subscribe(DeveloperModeChanged, heard.append)
    said = "Broken did not switch off cleanly: half-written"
    result = app.dispatch(SetDeveloperMode(on=False))
    assert (result.ok, result.note) == (True, said)
    assert [(_info(app, k).enabled, _info(app, k).allowed)
            for k in ("broken", "healthy")] == [(False, False), (False, False)]
    assert app.modules.on.isdisjoint({"broken", "healthy"})
    assert heard == [ModuleChanged(key="broken", enabled=False, note=said),
                     ModuleChanged(key="healthy", enabled=False),
                     DeveloperModeChanged(on=False)]
    app_py.write_text(app_py.read_text(encoding="utf-8").replace(
        'raise RuntimeError("half-written")', 'log.info("broken: disable")'),
        encoding="utf-8")
    app.dispatch(SetDeveloperMode(on=True))
    assert _info(app, "broken").enabled
    assert app.dispatch(SetDeveloperMode(on=False)).note == ""


def test_one_that_fails_switching_off_with_its_folder_gone_is_gone(
        tmp_home: Path) -> None:
    """Its folder deleted, then developer mode off: the broken module is not
    left behind as it was -- listed, allowed, and able to switch back on
    with the mode off.  MUTATION CHECK: drop the second ``remove`` in
    ``rescan_workspace`` and it is."""
    folder = _write_broken(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    app.dispatch(SetModuleEnabled(key="broken", enabled=True))
    shutil.rmtree(folder)
    app.dispatch(SetDeveloperMode(on=False))
    assert "broken" not in {m.key for m in app.dispatch(ListModules()).modules}
    assert not app.dispatch(SetModuleEnabled(key="broken", enabled=True)).ok
    assert "broken" not in app.modules.on


def test_the_cli_says_what_a_module_said_as_it_went_off(
        tmp_home: Path, cli_runner: Any, cli_app: App) -> None:
    from trcc.ui.cli.main import app

    _write_broken(tmp_home)
    assert cli_runner.invoke(app, ["system", "modules", "developer", "on"]).exit_code == 0
    cli_app.dispatch(SetModuleEnabled(key="broken", enabled=True))
    off = cli_runner.invoke(app, ["system", "modules", "developer", "off"])
    assert (off.exit_code, off.stdout.splitlines()) == (0, [
        "developer mode off", "Broken did not switch off cleanly: half-written"])


def test_every_modules_page_shows_it_beside_why_it_is_not_run(
        qtbot: Any, tmp_home: Path) -> None:
    """Developer mode switched off in another UI: this page's tile says both
    why the module is not run and what it said going off.  MUTATION CHECK:
    the tile's old ``problem or note`` and the note is never seen."""
    from PySide6.QtWidgets import QLabel

    from trcc.core.commands import ModuleIcon
    from trcc.ui.qt_modules import ModulesView

    from .test_modules import _GET, _switch

    _write_broken(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    app.dispatch(SetModuleEnabled(key="broken", enabled=True))
    view = ModulesView(app, ListModules, _switch, ModuleIcon, **_GET)
    qtbot.addWidget(view)
    app.events.subscribe(ModuleChanged, view.on_event)
    app.dispatch(SetDeveloperMode(on=False))
    said = [label.text() for label in view.findChildren(QLabel)
            if label.objectName() == "moduleProblem" and label.isVisibleTo(view)
            and "Broken" in label.text()]
    assert said == [f"Not run: {WORKSPACE_OFF}\n"
                    "Broken did not switch off cleanly: half-written"]


def test_it_is_never_trccs_own_nor_trccs_to_remove(tmp_home: Path) -> None:
    """MUTATION CHECK: send the workspace through plain ``spec()`` and it
    reads as built-in -- removable never, but trusted as TRCC's own."""
    _write(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    info = _info(app)
    assert (info.origin, removable(info), _KEY in module_folders.shipped()) == (
        "workspace", False, False)


def test_it_never_stands_in_for_trccs_own_module(tmp_home: Path) -> None:
    """A workspace folder named like a built-in is passed over, as an import
    of the key would be."""
    _write(tmp_home, key="rgb")
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    assert _info(app, "rgb").origin == "built-in"


def test_every_start_names_a_workspace_module_that_is_on(
        tmp_home: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write(tmp_home)
    app = App(platform=FakePlatform(tmp_home))
    app.dispatch(SetDeveloperMode(on=True))
    app.dispatch(SetModuleEnabled(key=_KEY, enabled=True))
    module_folders.forget(_KEY)
    with caplog.at_level(logging.WARNING, logger="trcc.app"):
        App(platform=FakePlatform(tmp_home))
    assert [r.getMessage() for r in caplog.records if "unsigned modules ON" in r.getMessage()] == [
        "unsigned modules ON (installed from a folder in developer mode, or run "
        "from the module workspace -- never reviewed): probe"]
