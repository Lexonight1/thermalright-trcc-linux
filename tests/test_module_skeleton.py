"""The ``hello`` skeleton, ``new_module.py``, and the guide that explains them.

The skeleton is loaded through the real catalog and the real import path a
built-in module uses (``trcc.adapters.modules.<key>``), so a change to the
module API breaks here instead of leaving a stale template.
"""
from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path

import pytest

import trcc.adapters.modules as modules_package
from trcc.adapters.modules import MODULES, read_manifest, specs
from trcc.app import App
from trcc.core.commands import SetModuleEnabled
from trcc.core.models import MODULE_CAPABILITIES, ModuleManifest

from .conftest import FakePlatform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import new_module

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATE = _REPO / "dev" / "module_template"


@pytest.fixture
def importable(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Code folders added to ``trcc.adapters.modules``'s search path, so a
    module's ``entry`` imports exactly as a built-in's does."""
    added: list[str] = []
    monkeypatch.setattr(modules_package, "__path__", list(modules_package.__path__))
    yield added
    for key in added:
        sys.modules.pop(f"trcc.adapters.modules.{key}", None)
        MODULES._table.pop(key, None)


def test_the_skeleton_runs_every_hook_through_the_app(
        importable: list[str], tmp_home: Path, caplog: pytest.LogCaptureFixture) -> None:
    modules_package.__path__.append(str(_TEMPLATE / "code"))
    importable.append("hello")
    app = App(platform=FakePlatform(tmp_home), modules=specs(_TEMPLATE / "manifest"))
    caplog.set_level(logging.INFO, logger="trcc.adapters.modules.hello")
    try:
        app.start_session()
        assert app.dispatch(SetModuleEnabled(key="hello", enabled=True)).ok
        app.dispatch(SetModuleEnabled(key="hello", enabled=False))
    finally:
        app.close()
    said = [r.getMessage() for r in caplog.records
            if r.name == "trcc.adapters.modules.hello"]
    assert said == ["hello: pre_enable", "hello: enable", "hello: start",
                    "hello: hello was switched on",     # it heard its own switch
                    "hello: stop", "hello: disable"]


def test_the_skeletons_manifest_is_valid() -> None:
    manifest = read_manifest(_TEMPLATE / "manifest" / "hello" / "module.json")
    assert (manifest.key, manifest.entry, manifest.default_on, manifest.capabilities,
            Path(manifest.icon).is_file()) == (
        "hello", "trcc.adapters.modules.hello", False, ("events",), True)


def test_new_module_makes_a_module_that_loads(
        tmp_path: Path, importable: list[str], tmp_home: Path) -> None:
    manifest_dir, code_dir = new_module.create(
        "weather", "Weather", "Today's forecast as a panel background.", repo=tmp_path)
    manifest = read_manifest(manifest_dir / "module.json")
    assert (manifest.title, manifest.entry, manifest.default_on, manifest.guide) == (
        "Weather", "trcc.adapters.modules.weather", False,
        "https://github.com/Lexonight1/thermalright-trcc-linux/blob/main/"
        "doc/GUIDE_MODULES.md#weather")
    modules_package.__path__.append(str(code_dir.parent))
    importable.append("weather")
    app = App(platform=FakePlatform(tmp_home), modules=specs(manifest_dir.parent))
    assert app.dispatch(SetModuleEnabled(key="weather", enabled=True)).ok
    assert type(app.modules._modules["weather"]).__name__ == "WeatherModule"


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
