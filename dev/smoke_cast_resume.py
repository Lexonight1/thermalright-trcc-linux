#!/usr/bin/env python3
"""A cast the user started comes back after the App restarts -- driven
through a REAL daemon, twice.

  TEST 1  over IPC: load a theme, start a cast
  TEST 2  stop the daemon; start a NEW one, which reads ``trcc.json`` and, at
          connect, primes the panel (``App._prime`` -> ``RestoreDeviceState``)
  TEST 3  the panel's source is the same cast, same region -- in the snapshot
  TEST 4  the new daemon's own log shows the restore resuming it and
          ``StartScreencast`` running.  TEST 3 alone cannot fail: the region
          stays saved whether or not the cast restarts, so the log is the
          proof that it did.

MUTATION CHECK: drop the screencast branch from ``RestoreDeviceState`` and
TEST 4 fails (measured 2026-11-04).

Safe on any machine: the daemon is ``dev/_mock_daemon.py`` (the real
``run_daemon`` on the scripted mock fleet; its screen capture is the fake
one, so no portal), the client is a raw ``AppProxy`` (it never starts an
App, so never a real daemon on real USB), and ``XDG_RUNTIME_DIR`` is a
throwaway (never a running TRCC's socket).  ``audio=False``: the daemon's
microphone would be the real one.  The theme is written here, so a fresh
checkout with no ``dev/.trcc`` catalog drives it too; ``dev/.trcc/trcc.json``
is put back as it was, or removed if there was none.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_DEV = Path(__file__).resolve().parent
_REPO = _DEV.parent
sys.path[:0] = [str(_REPO / "src"), str(_REPO), str(_DEV)]

_KEY = "0416:5302"                      # the mock fleet's 240x240 SCSI panel
_REGION = (0, 0, 64, 48)
_FAILURES: list[str] = []


def _check(cond: bool, label: str, detail: object = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  {detail}"))
    if not cond:
        _FAILURES.append(label)


def _theme(root: Path) -> Path:
    """A minimal 240x240 theme the real renderer draws."""
    from PySide6.QtGui import QColor, QImage

    theme = root / "CastResume"
    theme.mkdir(parents=True)
    (theme / "trcc.json").write_text(
        '{"name": "CastResume", "width": 240, "height": 240, "elements": []}',
        encoding="utf-8")
    img = QImage(240, 240, QImage.Format.Format_RGB888)
    img.fill(QColor(0, 64, 128))
    assert img.save(str(theme / "00.png"))
    return theme


def _log_since(log: Path, mark: int) -> str:
    """The log written after *mark* -- all of it, if it rotated meanwhile."""
    with log.open(encoding="utf-8", errors="replace") as f:
        if log.stat().st_size >= mark:
            f.seek(mark)
        return f.read()


def main() -> int:
    runtime = Path(tempfile.mkdtemp(prefix="trcc-cast-resume-"))
    os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("TRCC_DAEMON", None)        # the daemon process IS the daemon
    env.pop("WAYLAND_DISPLAY", None)

    from _mock_bootstrap import DEV_TRCC

    from trcc import ipc
    from trcc.core.commands import (
        LcdSnapshot,
        LoadTheme,
        StartScreencast,
        StopScreencast,
    )
    from trcc.daemon import kill_daemon
    from trcc.proxy import AppProxy

    conf, log = DEV_TRCC / "trcc.json", DEV_TRCC / "trcc.log"
    kept = runtime / "trcc.json.kept"
    if conf.exists():
        shutil.copy2(conf, kept)

    def start() -> subprocess.Popen[str]:
        d = subprocess.Popen([sys.executable, str(_DEV / "_mock_daemon.py")],
                             env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True)
        if not ipc.wait_for_daemon(timeout=20.0):
            d.kill()                    # kill BEFORE reading (smoke_daemon_gui)
            print(f"daemon did not come up:\n{d.communicate()[1][-2000:]}")
            raise SystemExit(1)
        return d

    def stop(d: subprocess.Popen[str]) -> None:
        if not kill_daemon(timeout=10.0):
            d.terminate()
        try:
            d.wait(timeout=10)
        except subprocess.TimeoutExpired:
            d.kill()

    daemon = None
    try:
        theme = _theme(runtime / "themes")
        daemon = start()
        proxy = AppProxy()
        loaded = proxy.dispatch(LoadTheme(key=_KEY, path=theme))
        _check(loaded.ok, "TEST 1: LoadTheme", loaded.message)
        cast = proxy.dispatch(StartScreencast(key=_KEY, x=_REGION[0], y=_REGION[1],
                                              w=_REGION[2], h=_REGION[3]))
        _check(cast.ok, "TEST 1: StartScreencast", cast.message)
        proxy.close()
        stop(daemon)
        mark = log.stat().st_size if log.exists() else 0

        daemon = start()                # the restart: a new App, from trcc.json
        proxy = AppProxy()
        deadline = time.monotonic() + 10.0
        new = ""
        while time.monotonic() < deadline:   # the prime runs during start-up
            new = _log_since(log, mark) if log.exists() else ""
            if "StartScreencast ok" in new:
                break
            time.sleep(0.2)
        after = proxy.dispatch(LcdSnapshot(key=_KEY))
        _check(after.display_source == "screencast",
               "TEST 3: after the restart the panel's source is the cast",
               after.display_source)
        _check(tuple(after.screencast_region or ()) == (*_REGION, False),
               "TEST 3: the same region", after.screencast_region)
        _check("resuming its screencast" in new,
               "TEST 4: RestoreDeviceState resumed it (daemon log)")
        _check("StartScreencast ok" in new,
               "TEST 4: StartScreencast ran in the new daemon (daemon log)")
        proxy.dispatch(StopScreencast(key=_KEY))
        proxy.close()
    finally:
        if daemon is not None and daemon.poll() is None:
            stop(daemon)
        if kept.exists():
            shutil.copy2(kept, conf)
        else:
            conf.unlink(missing_ok=True)
        shutil.rmtree(runtime, ignore_errors=True)

    if _FAILURES:
        print(f"\nFAIL: {len(_FAILURES)} assertion(s) failed: {_FAILURES}")
        return 1
    print("\nPASS: a cast comes back after the App restarts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
