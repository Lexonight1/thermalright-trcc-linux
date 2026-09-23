"""One rotation gesture is one theme reload, whichever face drives it.

``SetOrientation`` publishes ``OrientationChanged``; ``App._on_orientation_changed``
handles it by re-rooting the active theme -- plus the cloud background and the
mask -- into the new orientation's catalog, through the device's own artwork
library, with ``reset_overrides=False``.  Publication is synchronous, so that has
already happened by the time the dispatch returns.  A face therefore has nothing
left to decide, and ``tests/test_orientation_reload.py`` gates the decision
itself.

**What that gate cannot see is what a face does NEXT**, because it publishes
``OrientationChanged`` directly and never drives a UI.  Both Qt skins re-resolved
the target themselves and dispatched a SECOND ``LoadTheme``, and the join went
untested for eleven weeks.  Measured on a mock 854x480 panel, per rotation:

* the second load took the default ``reset_overrides=True``, which
  PERSIST-CLEARS ``user_overlay_elements``, stops video and reverts the cloud
  background -- undoing, one line later, exactly what the core passed
  ``reset_overrides=False`` to protect;
* its resolver tried ``user_theme_dir / name`` first unconditionally, so a
  shipped theme silently became the user's same-named saved theme -- the
  re-resolve ``oriented_theme_path`` documents as forbidden ("restore must NOT
  re-resolve a shipped pointer to the user one");
* gui read a STALE cached path (the core moved ``settings`` underneath it), so
  it fired on every rotation, not only when a user variant existed.

The skin step was written on 2026-07-14 for #169, one month AFTER the core
handler, because July's ``oriented_theme_path`` looked only in the generic
``theme{w}{h}`` and missed the per-SKU libraries that #169's 1600x720 cooler
uses.  ``0709ad5f`` taught the core resolver those libraries on 2026-08-23; the
workaround has been redundant since, and these drive the FACES to keep it that
way.

MUTATION CHECK -- add ``self._app.dispatch(LoadTheme(key=..., path=...))`` after
the ``SetOrientation`` in ``LCDHandler.set_rotation``, or
``self.dispatch(LoadTheme(...))`` after the one in ``DisplayPanel._on_apply``,
and that face's test must fail on the load count.  Both were confirmed to fail
before this file was committed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    AddOverlayElement,
    ConnectDevice,
    LoadTheme,
)

from .mock_platform import MockPlatform

_KEY = "87ad:70db"
_VID, _PID = 0x87AD, 0x70DB
#: Bulk panel scripted to 854x480 -- non-square, so the two orientations key
#: different catalogs and a rotation has something to re-root.
_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}
_THEME = "Theme1"


def _seed(directory: Path) -> Path:
    """A minimal loadable theme -- background + the next/-native config."""
    from PySide6.QtGui import QImage

    directory.mkdir(parents=True, exist_ok=True)
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0x0A141E)
    image.save(str(directory / "00.png"))
    (directory / "trcc.json").write_text(
        json.dumps({"name": _THEME, "elements": []}), encoding="utf-8",
    )
    return directory


@pytest.fixture
def rotatable(tmp_path: Path) -> App:
    """A connected panel at 0 degrees, on a SHIPPED theme, with a user edit.

    The portrait catalog holds a same-named USER theme as well, because that is
    the input that separates "re-root for orientation" from "re-resolve which
    tree" -- the two answers a rotation must never confuse.
    """
    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.attach(_VID, _PID)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    paths = app.platform.paths()

    landscape = _seed(paths.theme_dir(854, 480) / _THEME)
    _seed(paths.theme_dir(480, 854) / _THEME)
    _seed(paths.user_theme_dir(480, 854) / _THEME)

    # SUB 5 mounts portrait, so connect seeds 90; state the angle we rotate
    # FROM rather than inherit it (tests/test_mount_orientation_seed.py).
    app.settings.set_orientation(_KEY, 0)
    assert app.dispatch(LoadTheme(key=_KEY, path=landscape)).ok
    assert app.dispatch(AddOverlayElement(
        key=_KEY, type="text", text="MINE", element_id="keepme",
    )).ok
    return app


class _Rotation:
    """Records every ``LoadTheme`` one rotation gesture causes."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.loads: list[LoadTheme] = []

    def __enter__(self) -> _Rotation:
        real = self._app.dispatch

        def spy(command: Any) -> Any:
            if isinstance(command, LoadTheme):
                self.loads.append(command)
            return real(command)

        self._real = real
        self._app.dispatch = spy      # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        self._app.dispatch = self._real   # type: ignore[method-assign]


def _assert_one_clean_reload(app: App, rotation: _Rotation) -> None:
    """The whole invariant, stated once for all four faces."""
    paths = app.platform.paths()
    settings = app.settings.for_device(_KEY)

    assert len(rotation.loads) == 1, (
        f"a rotation caused {len(rotation.loads)} LoadTheme dispatches "
        f"({[str(c.path) for c in rotation.loads]}) — the core re-roots the "
        f"theme itself on OrientationChanged, so a face that loads again is "
        f"doing it twice"
    )
    load = rotation.loads[0]
    assert load.reset_overrides is False, (
        "the rotation reload must PRESERVE the device's overrides — "
        "reset_overrides=True persist-clears user_overlay_elements, stops "
        "video and reverts the cloud background"
    )
    assert load.path == paths.theme_dir(480, 854) / _THEME, (
        f"rotation re-roots for ORIENTATION only; it loaded {load.path}, and "
        f"the same-named theme in the user tree is a different theme"
    )
    assert settings.current_theme == str(paths.theme_dir(480, 854) / _THEME)
    assert [e.id for e in (settings.user_overlay_elements or ())] == ["keepme"], (
        "the user's overlay edit did not survive the rotation"
    )


# ── the two command-only faces ──────────────────────────────────────────


def test_cli_rotation_is_one_reload(
    rotatable: App, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app as cli

    # ``_ctx``, not the command module: ``set-orientation`` goes through
    # ``dispatch_echo``, which looks ``get_app`` up in its OWN module, so
    # patching the name bound in ``cli/display.py`` reaches nothing.
    monkeypatch.setattr(_ctx, "get_app", lambda: rotatable)

    with _Rotation(rotatable) as rotation:
        result = cli_runner.invoke(
            cli, ["display", "set-orientation", _KEY, "90"],
        )

    assert result.exit_code == 0, result.output
    _assert_one_clean_reload(rotatable, rotation)


def test_api_rotation_is_one_reload(rotatable: App) -> None:
    from trcc.ui.api.main import build_app

    with TestClient(build_app(trcc=rotatable)) as client, \
            _Rotation(rotatable) as rotation:
        response = client.post(
            f"/devices/{_KEY}/display/orientation", json={"degrees": 90},
        )

    assert response.status_code == 200, response.text
    _assert_one_clean_reload(rotatable, rotation)


# ── the two Qt skins, where it actually went wrong ──────────────────────


class _Stub:
    """A widget that answers anything and does nothing."""

    def isActive(self) -> bool:       # Qt API shape, not PEP 8's call
        return False

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> Any:
            return None
        return _noop


class _Widgets(dict):
    def __missing__(self, key: str) -> Any:
        self[key] = _Stub()
        return self[key]


def test_gui_rotation_is_one_reload(
    rotatable: App, tmp_path: Path, qapp: object,
) -> None:
    """The gui skin's rotation combo → ``LCDHandler.set_rotation``."""
    from trcc.ui.gui.lcd_handler import LCDHandler

    handler = LCDHandler(
        _KEY, _Widgets(), lambda cb, *a, **k: _Stub(),
        tmp_path, app=rotatable, lcd_idx=_KEY,
    )
    handler._pm.ui_active = True
    handler._pm.state.current_theme_path = Path(
        rotatable.settings.for_device(_KEY).current_theme,
    )

    with _Rotation(rotatable) as rotation:
        handler.set_rotation(90)

    _assert_one_clean_reload(rotatable, rotation)
    assert handler.current_theme_path == (
        rotatable.platform.paths().theme_dir(480, 854) / _THEME
    ), "the View kept a path the core had already moved"


def test_qtgui_rotation_is_one_reload(rotatable: App, qtbot: Any) -> None:
    """qtgui's Display panel → ``_on_apply``."""
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel

    panel = DisplayPanel(rotatable, BusBridge(rotatable.events))
    qtbot.addWidget(panel)
    panel._require_key = lambda: _KEY   # type: ignore[method-assign]
    index = panel._orientation.findData(90)
    assert index >= 0, "the panel offers no 90° option to drive"
    panel._orientation.setCurrentIndex(index)

    with _Rotation(rotatable) as rotation:
        panel._on_apply()

    _assert_one_clean_reload(rotatable, rotation)
