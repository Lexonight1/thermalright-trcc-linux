"""#323: the headless Qt app leaves GLib's default main context alone.

The renderer's own QGuiApplication (``_ensure_qt_app``) is built without Qt's
GLib dispatcher.  With it, Qt took GLib's default context for the main thread
and kept it; nothing runs Qt's loop in the daemon, so the logind listener's
GLib loop -- on another thread -- never ran, and PrepareForSleep was never
heard.  Each check runs in a fresh process: this one already has a Qt app
(pytest-qt), and Qt builds its dispatcher once per process.
"""
from __future__ import annotations

import json
import os
import site
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")

#: Build the headless app as TRCC does, then report what a GLib loop on
#: another thread can do, and what the environment was left holding.
_PROBE = """
import json, os, threading
from trcc.adapters.render.qt import _ensure_qt_app
_ensure_qt_app()
report = {"env": os.environ.get("QT_NO_GLIB")}
try:
    from gi.repository import GLib
except ImportError:
    print(json.dumps(report)); raise SystemExit
context = GLib.MainContext.default()
acquired = {}
def other():
    acquired["ok"] = context.acquire()
    if acquired["ok"]:
        context.release()
thread = threading.Thread(target=other); thread.start(); thread.join()
report["other_thread_acquires"] = acquired["ok"]
loop, fired = GLib.MainLoop(), threading.Event()
def tick():
    fired.set()
    return False
GLib.timeout_add(100, tick)
threading.Thread(target=loop.run, daemon=True).start()
report["background_loop_fires"] = fired.wait(5)
print(json.dumps(report), flush=True)
os._exit(0)
"""


def _probe(**env: str) -> dict[str, object]:
    clean = {k: v for k, v in os.environ.items() if k != "QT_NO_GLIB"}
    result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                            text=True, timeout=60, check=False,
                            env={**clean, "PYTHONPATH": _SRC,
                                 # PySide6 may be a per-user install; the
                                 # test's throwaway HOME would hide it.
                                 "PYTHONUSERBASE": site.getuserbase(), **env})
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_glib_loop_on_another_thread_runs_after_the_headless_app() -> None:
    """MUTATION CHECK: build the app without setting QT_NO_GLIB and both
    turn False -- the bug, as #323's reporter measured it."""
    report = _probe()
    if "other_thread_acquires" not in report:
        pytest.skip("PyGObject (the [wayland] extra) is not installed")
    assert (report["other_thread_acquires"], report["background_loop_fires"]) == (
        True, True)


def test_the_switch_is_never_left_for_a_child_process() -> None:
    assert _probe()["env"] is None


def test_a_switch_the_user_set_is_left_as_it_was() -> None:
    assert _probe(QT_NO_GLIB="0")["env"] == "0"
