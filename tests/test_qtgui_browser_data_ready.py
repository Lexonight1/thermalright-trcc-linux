"""qtgui asset browsers must re-list when the first-run data lands.

Since #275 the archives download in the background so the window can open
immediately — which means every browser grid is built BEFORE its assets exist.
The gui skin already had a refresh path (``notify_data_ready``); qtgui had
NOTHING listening, so on a first run its theme and mask grids would have stayed
empty for the whole session.

This is the View↔bus binding seam — a real Qt signal, delivered queued from the
install worker — so it needs a real QApplication (``qtbot``) rather than a
hand-rolled stub.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock_platform import MockPlatform
from trcc.app import App
from trcc.core.events import DataInstalled
from trcc.ui.bus_bridge import BusBridge
from trcc.ui.qtgui.panels.local_theme_browser import LocalThemeBrowser
from trcc.ui.qtgui.panels.mask_browser import MaskBrowser

_SPECS = [{"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100}]


@pytest.mark.parametrize("panel_cls", [LocalThemeBrowser, MaskBrowser])
def test_data_installed_re_lists_the_grid(qtbot, tmp_path: Path,
                                          panel_cls: type) -> None:
    app = App(MockPlatform(_SPECS, tmp_path))
    try:
        bus = BusBridge(app.events)
        panel = panel_cls(app, bus)
        qtbot.addWidget(panel)

        refreshed: list[int] = []

        def _record() -> None:
            refreshed.append(1)

        panel.refresh = _record

        app.events.publish(DataInstalled(resolution=(320, 320), ok=True))

        qtbot.waitUntil(lambda: bool(refreshed), timeout=3000)
        assert refreshed, (
            f"{panel_cls.__name__} ignored DataInstalled — its grid would stay "
            "empty for the whole first run (#275)"
        )
    finally:
        app.close()


def test_every_asset_browser_can_re_list(qtbot, tmp_path: Path) -> None:
    """The base wires the signal for ALL asset browsers, so a new one added
    later inherits the behaviour instead of having to remember it."""
    from trcc.ui.qtgui.panels._browser_base import AssetBrowserPanel

    subclasses = AssetBrowserPanel.__subclasses__()
    assert subclasses, "no asset browsers found — has the base moved?"
    for cls in subclasses:
        assert "refresh" in dir(cls), f"{cls.__name__} cannot re-list"
