"""A cut video must outlive the export's staging dir and the GUI session.

Two defects, one symptom (#271): the trimmer's Theme.zt was staged under
``tempfile.mkdtemp`` and persisted as the device's background override
verbatim, so the override pointed at a file that vanished on reboot; and
every GUI close ran ``StopVideo``, which cleared the override outright.
Cut a video, close the app, reopen -- gone.

Drives the real window offscreen against the mock platform, exactly the
scaffold ``test_gui_empty_state`` uses.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import show_a_theme
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.services.media import MediaService, Playback

_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"


def _jpeg(w: int = 320, h: int = 320) -> bytes:
    """A real encoded frame -- playbacks hold JPEG bytes, not pixels."""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(0xFF000000)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 100)
    buf.close()
    return bytes(ba)


def test_a_cut_video_becomes_the_background_as_delivered_and_survives_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The export now arrives already in the user's background library, which
    survives a reboot (#271).  gui used to copy it out of /tmp into a place of
    its own choosing -- a decision no other UI made; it now persists exactly
    the path it is handed, and makes no copy."""
    from trcc.ui.gui.trcc_app import TRCCApp

    # No ffmpeg in the loop: any .zt "decodes" to three black frames.
    def fake_load(self, device_key, path, size, **kwargs):  # type: ignore[no-untyped-def]
        w, h = size if size is not None else (320, 320)
        playback = Playback(frames=[_jpeg(w, h)] * 3, fps=15)
        self._playbacks[device_key] = playback
        return playback

    monkeypatch.setattr(MediaService, "load_video", fake_load)

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        show_a_theme(app, _KEY)
        library = app.platform.paths().user_background_dir(320, 320)
        library.mkdir(parents=True)
        delivered = library / "0123456789abcdef.zt"
        delivered.write_bytes(b"ZT\x00kept-by-the-export")
        window = TRCCApp(app=app)
        window.replay_initial_devices()

        window._on_video_cut_done(str(delivered))

        assert app.settings.for_device(_KEY).background_path == str(delivered)
        assert not (Path(app.platform.paths().user_content_dir())
                    / "backgrounds").exists(), "gui made its own copy again"
        window.close()          # closeEvent -> every handler's cleanup()
        assert app.settings.for_device(_KEY).background_path == str(delivered), (
            "closing the GUI must not wipe the persisted background")
    finally:
        app.close()
    # Playback is the App's: a window closing leaves it playing, and the App
    # frees it when IT closes.
    assert app.media.playback(_KEY) is None, "closing the App must unload playback"
    assert app.settings.for_device(_KEY).background_path == str(delivered)


# =========================================================================
# The W/H buttons must carry their choice out of the panel (#291)
# =========================================================================


def test_the_fit_buttons_carry_their_choice_out_of_the_panel(qtbot) -> None:
    """Button → signal → the window's ``ExportVideoClip``.

    The panel recorded the press into ``_width_fit`` and nothing ever read
    it, so both buttons produced the same letterboxed export and the same
    preview.  This is the hop that was missing, and the widget was
    constructed in NO test, which is why nothing noticed.

    The default matters as much as the presses: it must be ``None`` (auto),
    not ``WIDTH``.  The old flag was a bool defaulting to True, so carrying
    it through unchanged would have switched every untouched export to a
    forced-width CROP.

    MUTATION CHECK -- re-default ``_fit_mode`` to ``FitMode.WIDTH`` and the
    first assertion fails; stop passing it to ``emit`` and the rest do.
    """
    from trcc.core.models import FitMode
    from trcc.ui.gui.uc_video_cut import UCVideoCut

    panel = UCVideoCut()
    qtbot.addWidget(panel)

    # Untouched: the auto arm, which is what every export did before.
    assert panel._fit_mode is None

    # A loaded clip and a trim, so the real ``_on_export`` guard passes.
    panel._video_path = "/nonexistent/clip.mp4"
    panel._start_ms, panel._end_ms = 0, 500

    for handler, expected in ((panel._on_width_fit, FitMode.WIDTH),
                              (panel._on_height_fit, FitMode.HEIGHT)):
        handler()
        assert panel._fit_mode is expected
        # Drive the REAL emitter -- ``_on_export`` is what the Apply button
        # calls.  Emitting the signal by hand here would assert my own
        # arithmetic rather than the panel's, which is the fixture trap this
        # whole issue turned on.
        panel._is_processing = False
        with qtbot.waitSignal(panel.export_requested, timeout=1000) as sig:
            panel._on_export()
        assert sig.args[3] is expected, (
            "the fit the user pressed never left the panel — the window "
            "builds ExportVideoClip from these arguments"
        )
        assert sig.args[:3] == [0, 500, 0]


def test_the_trimmer_exports_at_the_frame_rate_pressed(qtbot) -> None:
    """The C#'s 15/24 buttons (UCVideoCut.cs:2712-2723), 15 untouched.

    Every export was 24: the first commit hardcoded it and the selector was
    never ported.  Driven through the real buttons and the real emitter.
    """
    from trcc.ui.gui.uc_video_cut import UCVideoCut

    panel = UCVideoCut()
    qtbot.addWidget(panel)
    panel._video_path = "/nonexistent/clip.mp4"
    panel._start_ms, panel._end_ms = 0, 500

    for press, expected in ((None, 15), (24, 24), (15, 15)):
        if press is not None:
            panel._fps_btns[press].click()
        panel._is_processing = False
        with qtbot.waitSignal(panel.export_requested, timeout=1000) as sig:
            panel._on_export()
        assert sig.args[4] == expected


def test_qtgui_exports_at_the_frame_rate_chosen(qtbot) -> None:
    """qtgui's 15/24 actions reach ExportVideoClip, 15 untouched."""
    from pathlib import Path
    from types import SimpleNamespace

    from PySide6.QtCore import QObject, Signal

    from trcc.core.commands import DeviceCanvas, ExportVideoClip
    from trcc.ui.qtgui.video_crop import VideoCropDialog

    class _Bus(QObject):
        video_export_progress = Signal(object)
        video_export_finished = Signal(object)

    sent: list = []

    class _App:
        def dispatch(self, cmd):
            sent.append(cmd)
            if isinstance(cmd, DeviceCanvas):
                return SimpleNamespace(ok=False)
            return SimpleNamespace(ok=False, message="recorded")

    dialog = VideoCropDialog(_App(), _Bus(), "0402:3922")  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog._video_path = Path("/nonexistent/clip.mp4")

    def exported_fps() -> int:
        dialog._on_export_clicked()
        return [c for c in sent if isinstance(c, ExportVideoClip)][-1].fps

    assert exported_fps() == 15
    by_fps = {a.data(): a for a in dialog._fps_group.actions()}
    by_fps[24].trigger()
    assert exported_fps() == 24
