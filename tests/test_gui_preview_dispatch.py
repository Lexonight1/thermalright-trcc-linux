"""The gui skin's preview seam — real handler, real App, real render.

``LCDHandler.rebuild_preview`` is the fallback the GUI takes whenever a send
carries no surface (SendColor / SendImage / keepalive / pre-load).  It used to
assemble the render itself out of four reaches; it now dispatches
``BuildPreview``.  These drive the real handler over a real App so the whole
chain — dispatch → Command → DisplayService → QImage → widget — is exercised,
not a fake dispatch that would prove nothing about the seam.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QImage

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.models import Theme

from .mock_platform import MockPlatform

_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}
_KEY = "87ad:70db"


class _CapturingPreview:
    """The shared preview widget set — records what the handler shows."""

    def __init__(self) -> None:
        self.images: list[Any] = []

    def set_image(self, image: Any, fast: bool = False) -> None:
        self.images.append(image)

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


class _Widgets(dict):
    def __missing__(self, key: str) -> Any:
        self[key] = _CapturingPreview()
        return self[key]


class _FakeTimer:
    def isActive(self) -> bool:      # Qt API shape, not PEP 8's call
        return False

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


@pytest.fixture
def handler(tmp_path: Path) -> tuple[Any, App, _CapturingPreview]:
    from trcc.ui.gui.lcd_handler import LCDHandler

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.attach(0x87AD, 0x70DB)
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    # SUB 5 = portrait-MOUNTED, so connect seeds 90 like the vendor app does.
    # This test asserts the rendered surface's ASPECT, so state the angle
    # rather than inherit it (mount rule: tests/test_mount_orientation_seed.py).
    app.settings.set_orientation(_KEY, 0)

    preview = _CapturingPreview()
    widgets = _Widgets({"preview": preview})
    h = LCDHandler(
        app.devices[_KEY], widgets, lambda cb, *a, **k: _FakeTimer(),
        tmp_path, app=app, lcd_idx=_KEY,
    )
    h._pm.ui_active = True
    return h, app, preview


def test_rebuild_preview_shows_the_rendered_surface(
    handler: tuple[Any, App, _CapturingPreview], tmp_path: Path,
) -> None:
    """Nothing cached yet → the handler dispatches and paints what came back."""
    h, app, preview = handler
    app.active_themes[_KEY] = Theme(
        path=tmp_path / "theme", name="t",
        resolution=(854, 480), config={"elements": []},
    )

    h.rebuild_preview()

    assert preview.images, "handler never painted a preview"
    shown = preview.images[-1]
    assert isinstance(shown, QImage)
    assert (shown.width(), shown.height()) == (854, 480)


def test_rebuild_preview_paints_nothing_before_a_theme_is_loaded(
    handler: tuple[Any, App, _CapturingPreview],
) -> None:
    """Pre-load is the GUI's normal state — no theme, no paint, no warning
    storm (the Command answers ok with no surface)."""
    h, _, preview = handler

    h.rebuild_preview()

    assert preview.images == []
