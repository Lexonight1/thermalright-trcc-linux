"""GUI empty-state — zero devices lands on the deviceless home view.

Regression guard for the blank-main-panel bug: when no Thermalright device is
discovered, ``replay_initial_devices`` must show the device-independent
home/sysinfo view (live system metrics) instead of leaving the inert form
chrome up.  With a device it still lands on the device 'form' view.

Visibility is asserted with ``isVisibleTo(window)`` so the checks reflect the
explicit ``setVisible`` decisions our code makes, independent of whether the
offscreen top-level window is mapped to a screen.
"""
from __future__ import annotations

from pathlib import Path

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice

_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"


def test_zero_devices_shows_home_sysinfo_empty_state(tmp_path: Path) -> None:
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        window = TRCCApp(app=app)
        window.replay_initial_devices()
        assert not window._handlers, list(window._handlers)
        assert window.uc_system_info.isVisibleTo(window)
        assert not window.form_container.isVisibleTo(window)
    finally:
        app.close()


def test_one_device_shows_the_device_form_view(tmp_path: Path) -> None:
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        window = TRCCApp(app=app)
        window.replay_initial_devices()
        assert _KEY in window._handlers, list(window._handlers)
        assert window.form_container.isVisibleTo(window)
        assert not window.uc_system_info.isVisibleTo(window)
    finally:
        app.close()


def test_no_devices_hint_is_sourced_from_the_platform_port(tmp_path: Path) -> None:
    """The sidebar hint is the per-OS ``Platform.no_devices_hint()``, not hardcoded."""
    from trcc.ui.gui.trcc_app import TRCCApp

    platform = MockPlatform([], tmp_path)
    app = App(platform, renderer=QtRenderer())
    try:
        window = TRCCApp(app=app)
        assert window.uc_device.hint_label.text() == platform.no_devices_hint()
    finally:
        app.close()
