"""A freed BusBridge takes its EventBus subscriptions with it.

It never unsubscribed.  Each bridge subscribes one forwarder per event type it
mirrors (40), and when the bridge was collected the forwarders stayed on the
App's bus: every later publish raised "Signal source has been deleted" in each
of them, and the bus logged a traceback per dead bridge.  Found 2026-10-01 by
the viewfinder drive, whose temporary bridge was collected and took the frames'
input with it.  Latent in production -- both windows hold their bridge for
their whole life -- and exactly the trap the next widget with its own bridge
walks into, burying a report's real errors under tracebacks.

MUTATION CHECK -- MEASURED 2026-10-01: drop the ``destroyed`` hookup in
``BusBridge._wire`` and both tests here fail.
"""
from __future__ import annotations

import gc
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.mock_platform import MockPlatform
from trcc.app import App
from trcc.core.events import ScreencastStopped
from trcc.ui.bus_bridge import BusBridge


@pytest.fixture
def app(tmp_path: Path, qapp: Any) -> Iterator[App]:
    built = App(MockPlatform([], tmp_path, host_sensors=False))
    yield built
    built.close()


def _handlers(app: App) -> int:
    return sum(len(v) for v in app.events._handlers.values())   # pyright: ignore[reportPrivateUsage]


def test_a_freed_bridge_leaves_no_subscription_behind(app: App) -> None:
    baseline = _handlers(app)
    kept = BusBridge(app.events)
    with_one = _handlers(app)
    assert with_one > baseline, "a bridge subscribed nothing -- the test is vacuous"

    for _ in range(3):
        BusBridge(app.events)
    gc.collect()

    assert _handlers(app) == with_one, "freed bridges left forwarders on the bus"
    del kept
    gc.collect()
    assert _handlers(app) == baseline


def test_a_publish_after_a_bridge_is_freed_logs_no_error(
    app: App, caplog: pytest.LogCaptureFixture,
) -> None:
    BusBridge(app.events)
    gc.collect()
    with caplog.at_level(logging.ERROR, logger="trcc.core.events"):
        app.events.publish(ScreencastStopped(key="0000:0000"))
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


# ── Frames only while a preview is on screen ────────────────────────────────

def _frame_listeners(bus: Any) -> int:
    from trcc.core.events import FrameSent
    return bus.subscriber_count(FrameSent)


def test_frames_follow_the_preview_on_screen(qtbot: Any) -> None:
    """Legacy skipped the preview while the window was not visible
    (``is_app_visible`` -> ``lcd_handler.py:548``); the cutover dropped it and
    a window in the tray cost the App a fifth of a core.  Off while no preview
    shows: before the first show, on hide, when its window hides, minimised,
    and on another page.  On again -- with one "frames resumed" -- when back.

    MUTATION CHECK: make ``_sync_frames`` always True."""
    from PySide6.QtWidgets import QStackedWidget, QWidget

    from trcc.core.events import EventBus

    bus = EventBus()
    bridge = BusBridge(bus)
    window = QStackedWidget()
    qtbot.addWidget(window)
    preview, other = QWidget(), QWidget()
    window.addWidget(preview)
    window.addWidget(other)
    resumed: list[bool] = []
    bridge.frames_resumed.connect(lambda: resumed.append(True))

    bridge.follow_previews(preview)
    assert _frame_listeners(bus) == 0, "nothing on screen yet"

    window.show()
    qtbot.waitUntil(lambda: _frame_listeners(bus) == 1)
    assert resumed == [True]

    window.setCurrentWidget(other)                     # another page
    qtbot.waitUntil(lambda: _frame_listeners(bus) == 0)
    window.setCurrentWidget(preview)
    qtbot.waitUntil(lambda: _frame_listeners(bus) == 1)

    window.hide()                                      # closed to the tray
    qtbot.waitUntil(lambda: _frame_listeners(bus) == 0)
    window.show()
    qtbot.waitUntil(lambda: _frame_listeners(bus) == 1)

    window.showMinimized()
    qtbot.waitUntil(lambda: _frame_listeners(bus) == 0)
    assert len(resumed) == 3


def test_a_bridge_with_no_preview_to_follow_takes_every_frame() -> None:
    """A headless tool or a test builds a bridge with no window at all."""
    from trcc.core.events import EventBus

    bus = EventBus()
    bridge = BusBridge(bus)
    assert _frame_listeners(bus) == 1
    del bridge


def test_ram_lighting_switched_anywhere_reaches_every_window(qtbot: Any) -> None:
    """A switch from the CLI, the API or the other window shows in this one.

    MUTATION CHECK: drop RamLightingChanged from the bridge's pairs."""
    from trcc.core.events import EventBus, RamLightingChanged
    from trcc.core.models import RamAccessState

    bus = EventBus()
    bridge = BusBridge(bus)
    with qtbot.waitSignal(bridge.app_settings_changed, timeout=2000) as got:
        bus.publish(RamLightingChanged(state=RamAccessState.ON))
    assert got.args[0].state is RamAccessState.ON
