"""Single-instance raise-existing-window marshals onto the Qt main thread.

Regression guard for #196 ("app hangs when relaunched while minimized to
tray").  ``SingleInstance`` invokes ``on_raise`` from its accept thread; the
GUI wires that to ``TRCCApp.raise_requested.emit``.  The slot MUST run on the
Qt main thread (QueuedConnection) — a direct cross-thread QWidget call
deadlocks the event loop and the window never comes back.

We emit from a worker thread and assert the connected slot executes on the
main thread, never synchronously on the worker.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App


def test_raise_requested_runs_slot_on_main_thread(qtbot: object, tmp_path: Path) -> None:
    from trcc.ui.gui.trcc_app import TRCCApp

    main_ident = threading.get_ident()
    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        window = TRCCApp(app=app)

        # Probe connected with the SAME queued semantics as the real slot, so
        # it records the thread the marshalled slot actually runs on.
        slot_ident: dict[str, int] = {}
        window.raise_requested.connect(
            lambda: slot_ident.__setitem__("id", threading.get_ident()),
            type=Qt.ConnectionType.QueuedConnection,
        )

        window.hide()
        QApplication.processEvents()
        assert not window.isVisible()

        worker_ident: dict[str, int] = {}

        def emit_from_worker() -> None:
            worker_ident["id"] = threading.get_ident()
            window.raise_requested.emit()

        worker = threading.Thread(target=emit_from_worker, name="raise-worker")
        worker.start()
        worker.join(timeout=2.0)
        assert not worker.is_alive(), "emit blocked the worker thread"

        # The queued slot has NOT run yet — emit only posted the event.
        assert "id" not in slot_ident

        # Pump the main-thread event loop; now the marshalled slot fires.
        for _ in range(50):
            QApplication.processEvents()
            if "id" in slot_ident:
                break

        assert slot_ident.get("id") == main_ident, "slot did not run on main thread"
        assert worker_ident["id"] != main_ident, "worker shared the main thread"
        assert window.isVisible(), "window was not restored"
    finally:
        app.close()
