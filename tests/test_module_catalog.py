"""Modules are found by their manifest, and their code loads only when on.

A module that is off must cost nothing -- not even an import -- so the
Modules page lists it from ``module.json`` alone.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.modules import ManifestError, read_manifest, specs
from trcc.app import App
from trcc.core.commands import ListModules, SetModuleEnabled

from .conftest import FakePlatform

#: Where the lazy module's code lands once imported.
_PACKAGE = "trcc.modules.lazy.app"

_CODE = '''
from trcc.adapters.modules import BaseModule

IMPORTED = True


class LazyModule(BaseModule, key="lazy"):
    title, summary = "Lazy", "Proves an off module is never imported."

    def _enable(self, host):
        pass

    def _disable(self, host):
        pass
'''


def _manifest(folder: Path, **fields: Any) -> Path:
    """A module.json in *folder* -- the lazy module's, changed by *fields*."""
    folder.mkdir(parents=True, exist_ok=True)
    data = {"key": folder.name, "title": "Lazy",
            "summary": "Proves an off module is never imported.",
            "default_on": False, "icon": "icon.svg",
            "guide": "https://example.invalid/GUIDE_MODULES.md#lazy",
            "capabilities": ["events"], **fields}
    path = folder / "module.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def lazy(tmp_path: Path, module_root: Callable[[Path], Path]) -> Path:
    """A modules folder holding the lazy module, importable as a built-in is
    but not imported; every trace undone afterwards (``module_root``)."""
    root = tmp_path / "modules"
    _manifest(root / "lazy")
    (root / "lazy" / "app.py").write_text(_CODE)
    return module_root(root)


def test_a_manifest_is_read_with_its_icon_beside_it(tmp_path: Path) -> None:
    path = _manifest(tmp_path / "lazy")
    manifest = read_manifest(path)
    assert (manifest.key, manifest.title, manifest.default_on,
            manifest.icon, manifest.capabilities) == (
        "lazy", "Lazy", False, str(tmp_path / "lazy" / "icon.svg"), ("events",))


def test_a_key_must_be_a_package_name(tmp_path: Path) -> None:
    """The key names the folder and the package ``trcc.modules.<key>``."""
    path = _manifest(tmp_path / "rgb-lights")
    with pytest.raises(ManifestError) as e:
        read_manifest(path)
    assert str(e.value) == (f"{path}: key 'rgb-lights' is not 2-31 lowercase "
                            "letters, digits or _ -- it is also a package name")


@pytest.mark.parametrize(("change", "reason"), [
    ({"title": ""}, "missing title"),
    ({"summary": None}, "missing summary"),
    ({"key": "other"}, "key 'other' is not its folder's name"),
    ({"capabilities": "events"}, "capabilities must be a list of names"),
    ({"capabilities": ["events", "root"]}, "no such capability: root"),
])
def test_a_bad_manifest_says_what_is_wrong(
        tmp_path: Path, change: dict[str, Any], reason: str) -> None:
    path = _manifest(tmp_path / "lazy", **change)
    with pytest.raises(ManifestError) as e:
        read_manifest(path)
    assert str(e.value) == f"{path}: {reason}"


def test_a_broken_manifest_never_hides_the_others(lazy: Path) -> None:
    (lazy / "broken").mkdir()
    (lazy / "broken" / "module.json").write_text("{not json")
    assert sorted(specs(lazy)) == ["lazy"]


def test_an_off_module_is_listed_but_never_imported(
        lazy: Path, tmp_home: Path) -> None:
    """MUTATION CHECK: load every spec in ``ModuleRunner.__init__`` -- the
    package is imported while the App is built."""
    app = App(platform=FakePlatform(tmp_home), modules=specs(lazy))
    [tile] = app.dispatch(ListModules()).modules
    assert (tile.key, tile.title, tile.enabled, tile.icon, tile.guide) == (
        "lazy", "Lazy", False, str(lazy / "lazy" / "icon.svg"),
        "https://example.invalid/GUIDE_MODULES.md#lazy")
    assert _PACKAGE not in sys.modules
    assert app.modules.loaded == frozenset()

    assert app.dispatch(SetModuleEnabled(key="lazy", enabled=True)).ok
    assert _PACKAGE in sys.modules                      # switched on: loaded
    assert app.modules.loaded == frozenset({"lazy"})


def test_a_module_whose_code_does_not_load_says_so(
        lazy: Path, tmp_home: Path) -> None:
    (lazy / "lazy" / "app.py").write_text("raise ImportError('broken on purpose')\n")
    app = App(platform=FakePlatform(tmp_home), modules=specs(lazy))
    result = app.dispatch(SetModuleEnabled(key="lazy", enabled=True))
    assert (result.ok, result.message.split(":")[0]) == (
        False, "Lazy could not be loaded")
    assert app.modules.on == frozenset()
