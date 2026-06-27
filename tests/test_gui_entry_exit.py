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
    monkeypatch.setattr(qtgui_mod, "launch", lambda: 3)
    with pytest.raises(SystemExit) as exc:
        qtgui()
    assert exc.value.code == 3


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
