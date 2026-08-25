"""The gui skin's overlay-restore seam — a reconnect must not decide the toggle.

``DeviceSettings.overlay_enabled`` is the single authority for whether the
overlay draws (see the ``build_overlay`` comment in ``services/display.py``).
The GUI's automatic restore used to DERIVE it from "does this theme carry
elements" and dispatch ``EnableOverlay`` with the answer, so a user who
switched the overlay off got it switched back on at the next launch — issue
**#276**, reported against v9.9.8 on CachyOS.

The dead giveaway was ``_load_theme_overlay_config(theme_dir, persist=False)``:
the restore path asked for "don't persist", and the body logged the flag and
then persisted anyway.

These drive the real handler over a real App so the whole chain runs —
``RestoreLastTheme`` → the overlay restore → ``Settings`` — rather than
asserting against a fake that would prove nothing about the seam.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice, EnableOverlay

from .mock_platform import MockPlatform

_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}
_KEY = "87ad:70db"


class _Widget:
    """Records every call the handler makes on a shared GUI widget."""

    def __init__(self) -> None:
        self.overlay_enabled: list[bool] = []
        self.loaded: list[dict[str, Any]] = []

    def set_overlay_enabled(self, enabled: bool) -> None:
        self.overlay_enabled.append(enabled)

    def load_from_overlay_config(self, config: dict[str, Any]) -> None:
        self.loaded.append(config)

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


class _Widgets(dict):
    def __missing__(self, key: str) -> Any:
        self[key] = _Widget()
        return self[key]


class _FakeTimer:
    def isActive(self) -> bool:      # Qt API shape, not PEP 8's call
        return False

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


def _theme_with_overlay(root: Path) -> Path:
    """A saved theme whose ``trcc.json`` carries one overlay element."""
    theme = root / "MyVideoTheme"
    theme.mkdir(parents=True, exist_ok=True)
    (theme / "trcc.json").write_text(json.dumps({
        "name": "MyVideoTheme", "width": 854, "height": 480,
        "overlay_enabled": True,
        "elements": [
            {"type": "text", "x": 10, "y": 20, "text": "CPU",
             "color": "#ffffff", "size": 24},
        ],
    }))
    return theme


@pytest.fixture
def handler(tmp_path: Path) -> tuple[Any, App, _Widget]:
    from trcc.ui.gui.lcd_handler import LCDHandler

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.attach(0x87AD, 0x70DB)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok

    widgets = _Widgets()
    theme_setting = widgets["theme_setting"]
    h = LCDHandler(
        app.devices[_KEY], widgets, lambda cb, *a, **k: _FakeTimer(),
        tmp_path, app=app, lcd_idx=_KEY,
    )
    h._pm.ui_active = True
    return h, app, theme_setting


def test_restore_keeps_the_overlay_off_the_user_switched_off(
    handler: tuple[Any, App, _Widget], tmp_path: Path,
) -> None:
    """#276: the reconnect path must not re-enable a deliberately-off overlay.

    The theme carries an element, which is exactly the state the old code
    read as "→ overlay enabled".
    """
    h, app, theme_setting = handler
    theme = _theme_with_overlay(tmp_path)
    app.settings.set_overlay_enabled(_KEY, False)

    h._restore_overlay_editor(theme)

    assert app.settings.for_device(_KEY).overlay_enabled is False, (
        "an automatic restore overwrote the user's persisted overlay toggle"
    )
    assert theme_setting.overlay_enabled == [False], (
        "the grid toggle must show the device's state, not the theme's layout"
    )
    assert h._pm.state.overlay_enabled is False


def test_restore_still_shows_the_theme_layout_in_the_editor(
    handler: tuple[Any, App, _Widget], tmp_path: Path,
) -> None:
    """Not writing the toggle must not cost the user their populated grid —
    the editor is repopulated either way (that is what the restore is for)."""
    h, app, theme_setting = handler
    theme = _theme_with_overlay(tmp_path)
    app.settings.set_overlay_enabled(_KEY, False)

    h._restore_overlay_editor(theme)

    assert theme_setting.loaded, "restore left the overlay editor empty"
    assert "custom_text" in theme_setting.loaded[-1]


def test_restore_keeps_the_overlay_on_when_the_user_left_it_on(
    handler: tuple[Any, App, _Widget], tmp_path: Path,
) -> None:
    """The authority cuts both ways — restore reports on as faithfully as off."""
    h, app, theme_setting = handler
    theme = _theme_with_overlay(tmp_path)
    app.settings.set_overlay_enabled(_KEY, True)

    h._restore_overlay_editor(theme)

    assert app.settings.for_device(_KEY).overlay_enabled is True
    assert theme_setting.overlay_enabled == [True]


def test_restore_dispatches_no_command_at_all(
    handler: tuple[Any, App, _Widget], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sharp invariant: a reconnect READS device state and writes none.

    Asserting the resulting value only catches the bug when the persisted
    value differs from what the theme would have implied.  This catches it
    either way, which is what makes it the real guard.

    It asserts no WRITE rather than no dispatch.  Reading device state
    through a ``Query`` is how a daemon-mode UI must read it at all — the
    handler holds an ``AppProxy`` that exposes only ``dispatch`` — so
    "dispatched nothing" was a proxy for the invariant, not the invariant.
    A ``Command`` here is still the bug this guards.
    """
    from trcc.core.commands import Query

    h, app, _ = handler
    theme = _theme_with_overlay(tmp_path)
    writes: list[str] = []
    real = app.dispatch

    def _record(command: Any) -> Any:
        if not isinstance(command, Query):
            writes.append(type(command).__name__)
        return real(command)

    monkeypatch.setattr(app, "dispatch", _record)
    h._restore_overlay_editor(theme)

    assert writes == [], (
        f"restore must not write device state, but dispatched {writes}"
    )


def test_a_user_theme_click_still_establishes_the_toggle(
    handler: tuple[Any, App, _Widget], tmp_path: Path,
) -> None:
    """The fix is scoped to the AUTOMATIC path.

    A deliberate load is a source change: the theme establishes the layout and
    the toggle, which is legacy's behaviour and what the GUI standards
    document.  This is also the mutation check on the tests above — the old
    restore called exactly this function, so if it did not flip False→True
    here, those tests would pass against the bug.
    """
    h, app, theme_setting = handler
    theme = _theme_with_overlay(tmp_path)
    app.settings.set_overlay_enabled(_KEY, False)

    h._load_theme_overlay_config(theme)

    assert app.settings.for_device(_KEY).overlay_enabled is True, (
        "a user-initiated theme load must adopt the theme's overlay"
    )
    assert theme_setting.overlay_enabled == [True]


def test_a_theme_with_no_layout_switches_the_overlay_off_on_a_click(
    handler: tuple[Any, App, _Widget], tmp_path: Path,
) -> None:
    """The collapsed single-branch load keeps the no-layout behaviour."""
    h, app, theme_setting = handler
    bare = tmp_path / "BareTheme"
    bare.mkdir()
    app.settings.set_overlay_enabled(_KEY, True)

    h._load_theme_overlay_config(bare)

    assert app.settings.for_device(_KEY).overlay_enabled is False
    assert theme_setting.overlay_enabled == [False]
    assert theme_setting.loaded == [], "nothing to load, nothing loaded"


# ── #276 second symptom: the last deleted element must stay deleted ───────


class _RealGridHandler:
    """Wires a real ``UCThemeSetting`` to a real ``LCDHandler``.

    Drives the chain the reporter drives — grid delete → ``elements_changed``
    → the panel's delegate → ``on_overlay_changed`` → ``SetOverlayConfig`` —
    rather than calling the handler directly, because the bug lived in the hop
    between them.
    """

    def __init__(self, app: App, root: Path) -> None:
        from trcc.ui.gui.lcd_handler import LCDHandler
        from trcc.ui.gui.uc_theme_setting import UCThemeSetting

        self.panel = UCThemeSetting()
        self.handler = LCDHandler(
            app.devices[_KEY],
            {"theme_setting": self.panel, "preview": _Widget()},
            lambda cb, *a, **k: _FakeTimer(), root, app=app, lcd_idx=_KEY,
        )
        self.handler._pm.ui_active = True
        self.panel.delegate.connect(self._forward)

    def _forward(self, cmd: int, info: Any, data: Any) -> None:
        from trcc.ui.gui.uc_theme_setting import UCThemeSetting
        if cmd == UCThemeSetting.CMD_OVERLAY_CHANGED:
            self.handler.on_overlay_changed(info)


def _two_element_theme(root: Path) -> Path:
    d = root / "TwoUp"
    d.mkdir(parents=True, exist_ok=True)
    (d / "trcc.json").write_text(json.dumps({
        "name": "TwoUp", "width": 854, "height": 480, "overlay_enabled": True,
        "elements": [
            {"type": "text", "x": 10, "y": 20, "text": "A",
             "color": "#ffffff", "size": 24},
            {"type": "text", "x": 40, "y": 20, "text": "B",
             "color": "#ffffff", "size": 24},
        ],
    }))
    return d


def test_deleting_the_last_overlay_element_makes_it_stay_gone(
    handler: tuple[Any, App, _Widget], tmp_path: Path, qapp: object,
) -> None:
    """#276: *"whichever one I delete last still appears"*.

    Two defects stacked here.  ``on_overlay_changed`` dropped an empty payload
    on a falsiness guard, so the deletion never reached the bus; and even when
    it did, an empty user layer fell through to the theme's elements.  Both
    have to be right or the last element comes back.
    """
    from trcc.core.commands import LoadTheme
    from trcc.services.overlay import resolve_overlay_elements

    _, app, _ = handler
    theme = _two_element_theme(tmp_path)
    assert app.dispatch(LoadTheme(key=_KEY, path=theme)).ok
    ui = _RealGridHandler(app, tmp_path)
    ui.handler._load_theme_overlay_config(theme)

    def drawn() -> int:
        s = app.settings.for_device(_KEY)
        active = app.active_themes.get(_KEY)
        return len(resolve_overlay_elements(
            active.config if active else {}, s.user_overlay_elements,
        ))

    assert drawn() == 2, "the theme's two elements should be on screen"

    ui.panel.overlay_grid.delete_element(0)
    assert drawn() == 1, "deleting one of two must leave one"

    ui.panel.overlay_grid.delete_element(0)
    assert app.settings.for_device(_KEY).user_overlay_elements == [], (
        "deleting the last element must reach the bus as an explicit empty "
        "layout — a falsiness guard used to drop it"
    )
    assert drawn() == 0, (
        "an emptied layout must draw nothing, not fall back to the theme's "
        "elements — this is the element the reporter watched come back"
    )


def test_emptying_the_layout_does_not_switch_the_overlay_on(
    handler: tuple[Any, App, _Widget], tmp_path: Path, qapp: object,
) -> None:
    """Editing implies wanting to see it; deleting everything does not.

    Scoped to the EMPTY payload deliberately: deleting one of several is an
    ordinary edit and re-enabling on it is the intended behaviour, so a
    two-element theme would prove nothing about the empty case.
    """
    from trcc.core.commands import LoadTheme

    _, app, _ = handler
    theme = _theme_with_overlay(tmp_path)          # exactly one element
    assert app.dispatch(LoadTheme(key=_KEY, path=theme)).ok
    ui = _RealGridHandler(app, tmp_path)
    ui.handler._load_theme_overlay_config(theme)
    app.dispatch(EnableOverlay(key=_KEY, enabled=False))
    ui.handler._pm.state.overlay_enabled = False

    ui.panel.overlay_grid.delete_element(0)        # the only one → empty

    assert app.settings.for_device(_KEY).user_overlay_elements == []
    assert app.settings.for_device(_KEY).overlay_enabled is False, (
        "emptying the layout answered a 'remove everything' by switching the "
        "overlay on"
    )
