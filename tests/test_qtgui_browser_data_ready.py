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


def test_the_local_browser_follows_a_theme_saved_or_deleted_elsewhere(
    qtbot, tmp_path: Path,
) -> None:
    """Saved or deleted in another UI -- the CLI, the gui -- the list used to
    keep what it last listed until its own Refresh: it re-listed only after
    ITS OWN save/import/delete.  Driven on the App, as another UI's Command
    is; asserted on the names the list shows.

    MUTATION CHECK -- MEASURED 2026-10-02: drop the browser's
    ``themes_changed`` hookup → fails.
    """
    from PySide6.QtCore import Qt

    from tests.conftest import renderable_theme
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import ConnectDevice, DeleteTheme, LoadTheme, SaveTheme

    app = App(MockPlatform(_SPECS, tmp_path), renderer=QtRenderer())
    try:
        assert app.dispatch(ConnectDevice(key="0402:3922")).ok
        themes = app.platform.paths().theme_dir(320, 320)
        renderable_theme(themes, "Theme1")
        assert app.dispatch(LoadTheme(key="0402:3922", path=themes / "Theme1")).ok
        panel = LocalThemeBrowser(app, BusBridge(app.events))
        qtbot.addWidget(panel)

        def names() -> set[str]:
            return {panel._list.item(i).data(Qt.ItemDataRole.UserRole + 1)
                    for i in range(panel._list.count())}

        qtbot.waitUntil(lambda: "Theme1" in names(), timeout=3000)
        saved = app.dispatch(SaveTheme(key="0402:3922", name="Elsewhere"))
        assert saved.ok, saved.message
        qtbot.waitUntil(lambda: "Elsewhere" in names(), timeout=3000)

        assert app.dispatch(DeleteTheme(path=Path(saved.theme_path))).ok
        qtbot.waitUntil(lambda: "Elsewhere" not in names(), timeout=3000)
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


# ── create-from-image names the theme after the picture (2026-09-26) ─────

def test_re_cropping_a_picture_replaces_its_theme(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staged under a random tempfile name, every crop became a NEW theme
    called ``trcc-crop-<random>``.  Named after the source, a re-crop of the
    same picture replaces its own theme."""
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QDialog

    from trcc.ui.qtgui import image_crop
    from trcc.ui.qtgui.panels import local_theme_browser as browser_mod

    class _AcceptingCrop:
        def __init__(self, parent: object) -> None:
            pass

        def load_image(self, source: str, **kw: object) -> None:
            pass

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def cropped(self) -> QImage:
            image = QImage(32, 32, QImage.Format.Format_RGB32)
            image.fill(0x0000FF)
            return image

    source = tmp_path / "holiday.png"
    source.write_bytes(b"picked by the user")
    app = App(MockPlatform(_SPECS, tmp_path / "root"))
    try:
        panel = LocalThemeBrowser(app, BusBridge(app.events))
        qtbot.addWidget(panel)
        monkeypatch.setattr(panel, "_device_key", lambda: "0402:3922")
        monkeypatch.setattr(panel, "_target_resolution", lambda key: (320, 320))
        monkeypatch.setattr(browser_mod.QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(source), "")))
        monkeypatch.setattr(image_crop, "ImageCropDialog", _AcceptingCrop)

        panel._on_create_from_image()
        panel._on_create_from_image()

        themes = sorted(p.name for p in
                        (app.platform.paths().user_content_dir()
                         / "single-image").iterdir())
        assert themes == ["holiday"]
    finally:
        app.close()
