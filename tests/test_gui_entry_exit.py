"""gui()/qtgui() must exit via SystemExit, not typer.Exit (#187).

They're direct entry points — the Windows ``trcc-gui.exe`` frozen ``__main__``
and the ``trcc-gui``/``trcc-lcd`` console scripts call them OUTSIDE typer's
runner.  ``typer.Exit`` only means something inside that runner; raised here it
escapes unhandled and the frozen ``__main__``'s ``except Exception`` re-raises
it as "Failed to execute script".  ``SystemExit`` (a ``BaseException``, not an
``Exception``) exits cleanly in every context and carries launch()'s code.
"""
from __future__ import annotations

import pytest


def test_gui_raises_systemexit_with_launch_code(monkeypatch: pytest.MonkeyPatch) -> None:
    import trcc.ui.gui as gui_mod
    from trcc.ui.cli.main import gui
    # gui() always passes start_hidden= now (#201); the mock must accept it.
    monkeypatch.setattr(gui_mod, "launch", lambda **_kw: 0)
    with pytest.raises(SystemExit) as exc:
        gui()
    assert exc.value.code == 0


def test_qtgui_raises_systemexit_with_launch_code(monkeypatch: pytest.MonkeyPatch) -> None:
    import trcc.ui.qtgui as qtgui_mod
    from trcc.ui.cli.main import qtgui
    seen: dict[str, object] = {}

    def _fake_launch(**kw: object) -> int:
        seen.update(kw)
        return 3

    # qtgui() always passes start_hidden= now, the same as gui() — the mock
    # must accept it.
    monkeypatch.setattr(qtgui_mod, "launch", _fake_launch)
    with pytest.raises(SystemExit) as exc:
        qtgui()
    assert exc.value.code == 3
    # THE SENTINEL TRAP, pinned: a DIRECT call (console script / frozen exe)
    # never goes through typer, so `resume` arrives as typer's OptionInfo —
    # which is TRUTHY.  `resume is True` is what keeps a direct launch
    # visible; a plain `if resume` would silently hide every window.
    assert seen["start_hidden"] is False


def test_gui_exit_survives_except_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """The frozen __main__ wraps the call in ``except Exception`` — the exit
    must NOT be an Exception subclass or it gets swallowed + re-raised as a
    crash dialog (the #187 regression: typer.Exit is a RuntimeError)."""
    import trcc.ui.gui as gui_mod
    from trcc.ui.cli.main import gui
    monkeypatch.setattr(gui_mod, "launch", lambda **_kw: 1)
    try:
        gui()
    except Exception:
        pytest.fail("gui() exit was caught by `except Exception` — frozen build would crash")
    except SystemExit as e:
        assert e.code == 1


def test_gui_direct_entry_shows_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Called directly (trcc-gui console script / frozen trcc-gui.exe, no
    typer), `resume` is typer's OptionInfo sentinel — gui() must coerce it so
    those launchers SHOW the window, not start hidden (#201 regression)."""
    import trcc.ui.gui as gui_mod
    from trcc.ui.cli.main import gui
    captured: dict[str, object] = {}

    def _fake_launch(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(gui_mod, "launch", _fake_launch)
    with pytest.raises(SystemExit):
        gui()
    assert captured["start_hidden"] is False


# ─────────────────────────────────────────────────────────────────────
# qtgui: a genuine close must END THE EVENT LOOP
#
# ``quitOnLastWindowClosed`` is False in both skins so a window-close hides
# to the tray.  That makes ``event.accept()`` insufficient on a real quit:
# ``qapp.exec()`` never returns, so ``run``'s ``finally: app.close()`` never
# runs — the metrics thread kept polling, the panel stayed lit and /dev/sgN
# stayed held.  gui always called ``app.quit()`` here; qtgui did not.
#
# Exercised on an uninitialised instance (the pattern in
# test_qtgui_video_ticker): closeEvent touches only _tray / _ticker / _video.
# ─────────────────────────────────────────────────────────────────────


class _FakeEvent:
    def __init__(self) -> None:
        self.accepted = False
        self.ignored = False

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


class _FakeTray:
    """``intercept_close`` returns True when the close is diverted to tray."""

    def __init__(self, *, divert: bool) -> None:
        self._divert = divert

    def intercept_close(self, event: object) -> bool:
        return self._divert


class _StopCounter:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


def _make_window(*, divert: bool):
    from trcc.ui.qtgui.app import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._tray = _FakeTray(divert=divert)      # type: ignore[attr-defined]
    win._ticker = _StopCounter()              # type: ignore[attr-defined]
    win._video = {"0402:3922": _StopCounter()}  # type: ignore[attr-defined]
    return win


def _patch_qapp(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record ``quit()`` on the session's real QApplication.

    Spying on the method rather than replacing ``QApplication.instance``:
    the instance is shared session-wide (the ``_qapplication`` fixture), so
    swapping it out breaks unrelated teardown.
    """
    quits: list[str] = []

    import trcc.ui.qtgui.app as qtgui_app

    def _record(_self: object) -> None:
        quits.append("quit")

    monkeypatch.setattr(qtgui_app.QApplication, "quit", _record)
    return quits


def test_qtgui_genuine_close_quits_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug: exiting qtgui left the process running forever."""
    quits = _patch_qapp(monkeypatch)
    win = _make_window(divert=False)
    event = _FakeEvent()

    win.closeEvent(event)

    assert event.accepted is True
    assert quits == ["quit"], (
        "a genuine close did not quit the event loop — qapp.exec() never "
        "returns, so App.close() (panel blank + device release) never runs"
    )
    assert win._ticker.stopped == 1              # type: ignore[attr-defined]
    assert win._video["0402:3922"].stopped == 1  # type: ignore[attr-defined]


def test_qtgui_close_to_tray_does_not_quit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hide-to-tray must still hide, not exit.

    The guard against over-fixing the above: closing the window with a tray
    available keeps the LCD running, so quitting here would be a worse bug
    than the one being fixed.
    """
    quits = _patch_qapp(monkeypatch)
    win = _make_window(divert=True)
    event = _FakeEvent()

    win.closeEvent(event)

    assert quits == [], "a close diverted to the tray must NOT quit the app"
    assert event.accepted is False
    assert win._ticker.stopped == 0              # type: ignore[attr-defined]
