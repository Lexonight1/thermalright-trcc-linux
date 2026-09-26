"""The UI bus — ``UserInterface`` + the ``UIS`` registry.

UI was always the third adapter family beside OS and Device, and the only one
without an ABC: five hand-written bring-up sequences with five signatures.
``App.start_session`` exists *because* that duplication drifted -- one copy
lost ``metrics_loop.start()`` and a reporter's daemon served a permanently
blank device (#148).  These gates hold the shape that prevents a sixth copy.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from trcc.ui import _uis as _uis_module  # noqa: F401  (populates UIS)
from trcc.ui._base import UIS, UserInterface

_SRC = Path(__file__).resolve().parent.parent / "src"


def test_every_registered_face_is_a_userinterface() -> None:
    """The registry holds classes, and each really implements the contract."""
    assert UIS, "UIS is empty — importing trcc.ui._uis should populate it"
    for name, cls in UIS.items():
        assert isinstance(cls, type) and issubclass(cls, UserInterface), (
            f"{name} -> {cls!r} is not a UserInterface"
        )
        assert cls.key == name, f"{cls.__name__}.key={cls.key!r} != {name!r}"
        assert not getattr(cls, "__abstractmethods__", None), (
            f"{cls.__name__} is registered but still abstract: "
            f"{sorted(cls.__abstractmethods__)}"
        )


def test_faces_live_outside_the_heavy_qt_packages() -> None:
    """A face defined under ``ui/gui`` or ``ui/qtgui`` would poison every run.

    Importing ANY submodule of those packages runs their ``__init__``, which
    builds the whole window: measured, ``import trcc.ui.gui.assets`` costs
    249 ms and loads 17 PySide6 modules.  Because this registry populates by
    eager side-effect import — the same idiom as ``DEVICES`` — a face placed
    there would put PySide6 on the critical path of ``trcc --help``.
    """
    for name, cls in UIS.items():
        module = cls.__module__
        assert not module.startswith(("trcc.ui.gui", "trcc.ui.qtgui")), (
            f"{name} is defined in {module}; importing it drags in the whole "
            "Qt window at registration time.  Keep faces in trcc/ui/_uis.py "
            "and defer the heavy import into run()."
        )


def test_the_bus_adds_no_heavy_import_to_a_cli_run() -> None:
    """``trcc <anything>`` must not pay for PySide6 / FastAPI / uvicorn.

    This is the gate the design exists to satisfy, and it is the one that
    would have caught the naive version: registering faces by importing their
    implementations costs ~700 ms against a ~350 ms baseline.  Kept as a
    SUBPROCESS because import state is global — asserting it in-process would
    pass or fail on whatever an earlier test happened to import.
    """
    code = (
        "import trcc.ui.cli.main, trcc.ui._uis, sys;"
        "heavy=[m for m in sys.modules"
        " if m.startswith(('PySide6','fastapi','uvicorn'))];"
        "print(len(heavy)); print(sorted(heavy)[:5])"
    )
    # The child must resolve the same packages the parent did.  Inheriting
    # PYTHONPATH=src alone is not enough: trcc's dependencies may live in the
    # USER site (``~/.local/lib/...``), which Python locates through ``HOME``
    # — and the suite deliberately redirects ``HOME`` to a tmp dir so no test
    # writes into the developer's own ``~``.  Passing the parent's resolved
    # ``sys.path`` makes this test independent of both, which is what a
    # subprocess measuring IMPORT COST should have been all along.
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(_SRC), *(p for p in sys.path if p)]),
    }
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, check=True,
    ).stdout.split("\n")
    assert out[0].strip() == "0", (
        f"the UI bus dragged {out[0].strip()} heavy module(s) into a CLI run: "
        f"{out[1]}.  A face's heavy import belongs inside run(), not at "
        "module scope."
    )


def test_daemon_face_never_composes_a_proxy(fake_platform) -> None:
    """``DaemonUI`` must build a LOCAL App even with ``TRCC_DAEMON=1``.

    The daemon owns USB directly.  If the flag leaks in from a shell profile
    or the client that spawned it, composing through the inherited default
    would reach for a daemon socket instead of opening USB — and the startup
    path through it re-spawns.  That is a fork bomb with an issue number
    (#162), and the guard is one ``os.environ.pop`` that is easy to lose.
    """
    from trcc.app import App
    from trcc.ui._uis import DaemonUI

    os.environ["TRCC_DAEMON"] = "1"
    try:
        app = DaemonUI().compose(fake_platform)
        assert isinstance(app, App), (
            f"DaemonUI composed {type(app).__name__}, not a local App — "
            "the daemon would proxy to itself (#162)"
        )
        assert "TRCC_DAEMON" not in os.environ, (
            "DaemonUI.compose must strip TRCC_DAEMON so nothing downstream "
            "in this process reaches for the socket"
        )
    finally:
        os.environ.pop("TRCC_DAEMON", None)
        app.close()


def test_daemon_preflight_refuses_a_peer_with_exit_one(monkeypatch) -> None:
    """A second daemon FAILED to start — exit 1, unlike the GUI's peer-exit 0.

    The GUI returns 0 because a peer means the running window was raised, so
    the launch did what the user wanted.  A second daemon did not.  Collapsing
    the two into one declaration would have changed this code.
    """
    from trcc import ipc
    from trcc.ui._uis import DaemonUI

    monkeypatch.setattr(ipc, "daemon_running", lambda: True)
    assert DaemonUI().preflight() == 1

    monkeypatch.setattr(ipc, "daemon_running", lambda: False)
    assert DaemonUI().preflight() is None


def test_unknown_face_is_rejected_not_substituted() -> None:
    """A miss RAISES, like ``DEVICES`` and unlike ``PLATFORMS``.

    Silently starting a different face of the app would be worse than stopping.
    """
    from trcc.core.errors import UnknownUserInterfaceError

    with pytest.raises(UnknownUserInterfaceError):
        UIS["no-such-face"]
