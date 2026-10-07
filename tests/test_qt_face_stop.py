"""A Qt face stops from any thread, through anything modal, and never under a
running coldplug.

Driven 2026-10-07: a window restarted after its App died showed "devices did
not connect" -- a modal -- and the App's stop notice (``quit``) did nothing:
Qt 6 lets a window refuse a quit.  ``exit`` cannot be refused, but it also ends
the splash's loop while its worker may still hold USB, so the splash waits the
worker out.

Each case runs in its own interpreter: ``exit`` acts on the whole
``QApplication``, which pytest-qt shares across the session.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

_FACE = """
    import sys, threading, time
    from PySide6.QtWidgets import QApplication, QMessageBox
    from trcc.ui._uis import _QtUI

    class Face(_QtUI):
        def run(self):
            return 0

    qapp = QApplication([])
    face = Face()

    def stop_later(delay):
        def go():
            time.sleep(delay)
            face.stop()
        threading.Thread(target=go, daemon=True).start()
"""


def _run(body: str) -> str:
    # The parent's own path: the suite's throwaway HOME hides user site-packages.
    path = os.pathsep.join([str(_SRC), *(p for p in sys.path if p)])
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": path}
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_FACE) + textwrap.dedent(body)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def test_a_stop_from_another_thread_ends_a_modal() -> None:
    """MUTATION CHECK: stop with ``quit`` and the modal stays up."""
    out = _run("""
        stop_later(0.3)
        # Fallback, so a stop that fails reads as a slow modal, not a hang.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: qapp.exit(9))
        t = time.monotonic()
        QMessageBox.warning(None, "t", "modal")
        print(f"{time.monotonic() - t:.2f}")
    """)

    assert float(out) < 2.0, f"the modal stayed up {out.strip()} s"


def test_a_stop_during_the_splash_waits_out_the_coldplug() -> None:
    """The splash returns only once its worker is done with USB.

    MUTATION CHECK: drop the wait and the splash returns while the worker
    is still running.
    """
    out = _run("""
        from PySide6.QtCore import QThread, Signal
        from trcc.ui.gui import splash

        class SlowWorker(QThread):
            failed = Signal(str)
            progress = Signal(str)
            def __init__(self, app):
                super().__init__()
            def run(self):
                time.sleep(1.0)

        workers = []
        def make(app):
            workers.append(SlowWorker(app))
            return workers[-1]
        splash.BootstrapWorker = make
        stop_later(0.2)
        splash.run_bootstrap_with_splash(None)
        print("finished" if workers[0].isFinished() else "STILL RUNNING")
        workers[0].wait()          # never destroy a running QThread on exit
    """)

    assert out.strip() == "finished"
