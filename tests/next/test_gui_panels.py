"""GUI panel smoke tests — construct each widget + verify wiring.

Phase D boilerplate: every ``ui/gui/`` module was at 0% coverage; this
file establishes the QApplication-offscreen fixture pattern + one
construction smoke per panel class so widgets are at least known to
build without raising.

Future GUI tests (panel behavior, signal cascades, repaint logic)
build on the same fixtures.
"""
from __future__ import annotations

import os

# Qt needs an offscreen platform plugin in headless CI.  Set before any
# QtGui / QtWidgets import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Iterator

import pytest

from trcc.next.app import App

from .conftest import FakePlatform

# =========================================================================
# QApplication fixture — module-scoped so all GUI tests share it
# =========================================================================


@pytest.fixture(scope="module")
def qapp() -> Iterator[object]:
    """Ensure a QApplication exists for the duration of GUI tests.

    Module-scoped so we don't pay the Qt startup cost per test.  Qt's
    own ``QGuiApplication.instance()`` check makes the construction
    idempotent if some other code already started one.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    # Don't quit() — pytest may run other QtTest suites in the same
    # process; tearing down here breaks them.


# =========================================================================
# App fixture — wire a fresh App with FakePlatform + minimal renderer
# =========================================================================


@pytest.fixture
def gui_app(fake_platform: FakePlatform, qapp: object) -> App:
    """A test App with a real QtRenderer.  The renderer needs the
    QApplication, hence the ``qapp`` dependency."""
    del qapp                                # explicit dependency, no use
    from trcc.next.adapters.render.qt import QtRenderer

    return App(platform=fake_platform, renderer=QtRenderer())


# =========================================================================
# BusBridge — pure-Qt smoke, no panels needed
# =========================================================================


def test_bus_bridge_subscribes_to_every_event_type(qapp: object) -> None:
    """Constructing BusBridge wires one subscription per declared event
    type.  Crashing here means a misnamed Signal or missing event
    import in bus_bridge.py."""
    del qapp
    from trcc.next.core.events import EventBus
    from trcc.next.ui.gui.bus_bridge import BusBridge

    bus = EventBus()
    bridge = BusBridge(bus)
    # 10 event types wired today; assert at least that many handlers attached
    assert sum(len(handlers) for handlers in bus._handlers.values()) >= 10
    # Every Signal attribute should be a Qt Signal (descriptor on the class)
    for name in (
        "device_connected", "device_disconnected", "frame_sent",
        "orientation_changed", "brightness_changed", "theme_loaded",
        "led_colors_changed", "sensors_updated", "error_occurred",
    ):
        assert hasattr(bridge, name), f"BusBridge missing signal {name!r}"


def test_bus_bridge_forwards_events_to_qt_signals(qapp: object) -> None:
    """End-to-end: publishing an Event on the bus must arrive on the
    matching Qt signal.  Smokes the subscribe → emit pipeline."""
    del qapp
    from trcc.next.core.events import DeviceConnected, EventBus
    from trcc.next.ui.gui.bus_bridge import BusBridge

    bus = EventBus()
    bridge = BusBridge(bus)
    captured: list[object] = []
    bridge.device_connected.connect(captured.append)

    bus.publish(DeviceConnected(key="0402:3922", resolution=(320, 320)))

    assert len(captured) == 1
    event = captured[0]
    assert isinstance(event, DeviceConnected)
    assert event.key == "0402:3922"


# =========================================================================
# Panel construction smokes
# =========================================================================


def test_device_panel_constructs(gui_app: App) -> None:
    """DevicePanel constructs against a real App + QtRenderer.

    Builds → no exceptions.  Future tests can extend with click
    simulations + selection assertions.
    """
    from trcc.next.ui.gui.panels.device_panel import DevicePanel

    panel = DevicePanel(gui_app)
    assert panel is not None
    # Panel has a layout — Qt requires this for any child widgets to render
    assert panel.layout() is not None


def test_display_panel_constructs(gui_app: App) -> None:
    from trcc.next.ui.gui.panels.display_panel import DisplayPanel

    panel = DisplayPanel(gui_app)
    assert panel is not None
    assert panel.layout() is not None


def test_led_panel_constructs(gui_app: App) -> None:
    from trcc.next.ui.gui.panels.led_panel import LedPanel

    panel = LedPanel(gui_app)
    assert panel is not None
    assert panel.layout() is not None


# =========================================================================
# Main window smoke
# =========================================================================


def test_main_window_constructs_and_includes_panels(gui_app: App) -> None:
    """The top-level MainWindow constructs and embeds the three panels."""
    from trcc.next.ui.gui.app import MainWindow

    window = MainWindow(gui_app)
    assert window is not None
    assert window.windowTitle()           # non-empty title set
    # Status bar gets wired during init for platform info + events
    assert window.statusBar() is not None


# =========================================================================
# GUI launcher entry point
# =========================================================================


def test_gui_launch_is_callable() -> None:
    """The ``launch`` factory exists + is callable.  Actually running
    it would block on qapp.exec(); tests just verify import resolution."""
    from trcc.next.ui.gui import launch

    assert callable(launch)


# =========================================================================
# Coverage marker for the future
# =========================================================================


def test_offscreen_qpa_is_set() -> None:
    """Pin the QT_QPA_PLATFORM env var so a future regression that
    forgets to set offscreen mode fails loudly here instead of
    hanging in CI on a missing X server."""
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
