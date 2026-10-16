"""A file dropped on a window's preview does what that window's picker does
(#265): a video or GIF plays in the media player, an image becomes the
background (gui: through its cropper).  One file, a kind the media table
knows -- anything else is refused, and says so."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QWidget

from trcc.core.commands import SetBackground, SetMediaPlayer
from trcc.core.models import MediaKind
from trcc.ui.qt_file_drop import REFUSED, FileDropTarget, dropped_file


def _urls(*names: str) -> list[QUrl]:
    return [QUrl(n) if "://" in n else QUrl.fromLocalFile(n) for n in names]


@pytest.mark.parametrize(("names", "expected"), [
    (["/v/clip.mp4"], (Path("/v/clip.mp4"), MediaKind.ANIMATED)),
    (["/v/loop.gif"], (Path("/v/loop.gif"), MediaKind.ANIMATED)),
    (["/p/bob.png"], (Path("/p/bob.png"), MediaKind.IMAGE)),
    (["/d/notes.txt"], REFUSED),
    (["/v/a.mp4", "/v/b.mp4"], REFUSED),             # one file, not a playlist
    (["https://example.test/a.mp4"], REFUSED),        # a web address is not a file
    ([], REFUSED),
])
def test_what_a_drop_carries(names: list[str], expected: object) -> None:
    assert dropped_file(_urls(*names)) == expected


def _mime(*names: str) -> QMimeData:
    mime = QMimeData()
    mime.setUrls(_urls(*names))
    return mime


_DRAG = (Qt.DropAction.CopyAction, Qt.MouseButton.LeftButton,
         Qt.KeyboardModifier.NoModifier)


def _enter(point: QPoint, mime: QMimeData) -> QDragEnterEvent:
    """A drag entering at *point*, in whichever form this PySide6 binds.

    PySide6 6.12 takes a QPointF and deprecates the QPoint form -- an error
    under this suite's warning filter; 6.11.1 binds only the QPoint form, so
    a QPointF there is a TypeError.  Measured on both, 2026-10-15."""
    actions, buttons, mods = _DRAG
    try:
        return QDragEnterEvent(QPointF(point), actions, mime, buttons, mods)
    except TypeError:
        return QDragEnterEvent(point, actions, mime, buttons, mods)


def test_the_target_accepts_one_file_and_reports_the_drop(qtbot: Any) -> None:
    widget = QWidget()
    qtbot.addWidget(widget)
    target = FileDropTarget(widget)
    got: list[tuple[Path, MediaKind]] = []
    refused: list[str] = []
    target.dropped.connect(lambda p, k: got.append((p, k)))
    target.refused.connect(refused.append)
    actions, buttons, mods = _DRAG
    point = widget.rect().center()
    # An event holds only a pointer to its mime data: keep each one alive.
    video, text = _mime("/v/c.mp4"), _mime("/d/x.txt")
    enter = _enter(point, video)
    QApplication.sendEvent(widget, enter)
    nope = _enter(point, text)
    QApplication.sendEvent(widget, nope)
    # Qt delivers a drop only after an accepted drag: the video's.
    QApplication.sendEvent(widget, QDropEvent(QPointF(point), actions, video,
                                              buttons, mods))

    assert (widget.acceptDrops(), enter.isAccepted(), nope.isAccepted()) == (
        True, True, False)
    assert (got, refused) == ([(Path("/v/c.mp4"), MediaKind.ANIMATED)], [REFUSED])


class _Lcd:
    device_key = "0402:3922"
    lcd_size = (320, 320)


def _gui(qtbot: Any, fake_platform: Any) -> tuple[Any, list[object]]:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.ui.gui.trcc_app import TRCCApp

    window = TRCCApp(app=App(platform=fake_platform, renderer=QtRenderer()))
    qtbot.addWidget(window)
    sent: list[object] = []
    window._app = SimpleNamespace(      # type: ignore[assignment]
        dispatch=lambda cmd: sent.append(cmd) or SimpleNamespace(ok=True, message=""))
    window._active_lcd = lambda: _Lcd()  # type: ignore[method-assign]
    return window, sent


def test_gui_a_dropped_video_plays_in_the_media_player(
        qtbot: Any, fake_platform: Any) -> None:
    """MUTATION CHECK: route ANIMATED to the cropper -- nothing is sent."""
    window, sent = _gui(qtbot, fake_platform)
    window._on_file_dropped(Path("/v/clip.mp4"), MediaKind.ANIMATED)
    assert sent == [SetMediaPlayer(key="0402:3922", uri="/v/clip.mp4")]


def test_gui_a_dropped_image_opens_the_cropper(
        qtbot: Any, fake_platform: Any, tmp_path: Path) -> None:
    from PySide6.QtGui import QImage

    picture = tmp_path / "bob.png"
    QImage(8, 8, QImage.Format.Format_RGB888).save(str(picture))
    window, sent = _gui(qtbot, fake_platform)
    shown: list[str] = []
    window._show_cutter = shown.append  # type: ignore[method-assign]
    window._on_file_dropped(picture, MediaKind.IMAGE)
    assert (shown, sent, window._cut_mode) == (["image"], [], "background")


@pytest.mark.parametrize(("path", "kind", "command"), [
    (Path("/v/clip.mp4"), MediaKind.ANIMATED,
     SetMediaPlayer(key="0402:3922", uri="/v/clip.mp4")),
    (Path("/p/bob.png"), MediaKind.IMAGE,
     SetBackground(key="0402:3922", path=Path("/p/bob.png"))),
])
def test_qtgui_a_drop_sends_what_its_pickers_send(
        path: Path, kind: MediaKind, command: object) -> None:
    from trcc.ui.qtgui.app import MainWindow

    sent: list[object] = []
    said: list[str] = []
    window = SimpleNamespace(
        _lcd_selection=SimpleNamespace(key="0402:3922"),
        _app=SimpleNamespace(dispatch=lambda cmd: sent.append(cmd)
                             or SimpleNamespace(message="ok")),
        _status=SimpleNamespace(showMessage=lambda m, t: said.append(m)))
    MainWindow._on_file_dropped(window, path, kind)  # type: ignore[arg-type]
    assert (sent, said) == ([command], ["ok"])
