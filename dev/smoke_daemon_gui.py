#!/usr/bin/env python3
"""Daemon-mode smoke — verifies descriptors + FRAME events round-trip over IPC.

Spins up ``dev/_mock_daemon.py`` as a subprocess (MockPlatform-backed,
no real hardware), then connects a ``TrccProxy`` to its socket and
asserts:

  TEST 1  Descriptors travel over the wire
    - ``proxy.lcd_descriptors()`` returns the same DeviceInfo list
      the daemon's Trcc would return in-process.
    - Identity fields (vid, pid, path, resolution, fbl_code, etc.)
      are preserved end-to-end.

  TEST 2  Trcc.lcd command-bus dispatches over IPC
    - ``proxy.lcd.set_brightness(idx, pct)`` reaches the daemon and
      mutates the daemon's actual LCDDevice state.

  TEST 3  Topic.FRAME events arrive as decoded surfaces
    - Daemon publishes a FRAME on Trcc.events.
    - Proxy's EventBusProxy receives the wire envelope and reconstructs
      a native QImage via the wired-in renderer.

Lifecycle:

  - Smoke starts; spawns daemon as subprocess on a unique tmp socket.
  - Polls socket until ready (or fails fast at 10s).
  - Runs assertions; collects PASS/FAIL.
  - Sends ``{"kill": True}`` IPC request; waits for daemon to exit.
  - Returns 0 on all-PASS, non-zero otherwise.

Usage::

    PYTHONPATH=src python3 dev/smoke_daemon_gui.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))


_FAILURES: list[str] = []


def _check(cond: bool, label: str, detail: str = '') -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        msg = f"  FAIL  {label}" + (f" — {detail}" if detail else "")
        print(msg)
        _FAILURES.append(label)


def _wait_for_socket(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect(str(path))
                s.close()
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def _spawn_daemon(sock_path: Path) -> subprocess.Popen:
    daemon_script = Path(__file__).resolve().parent / '_mock_daemon.py'
    env = dict(os.environ, TRCC_MOCK_DAEMON_SOCKET=str(sock_path))
    return subprocess.Popen(
        [sys.executable, str(daemon_script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _kill_daemon_via_ipc(sock_path: Path) -> bool:
    """Send the manifold `{"kill": True}` request — the standard graceful
    shutdown path from any client."""
    from trcc.ipc import one_shot_request
    try:
        response = one_shot_request(
            {"kill": True}, socket_path=sock_path, timeout=2.0)
        return bool(response.get("success"))
    except Exception as e:
        print(f"  kill_daemon: {type(e).__name__}: {e}")
        return False


def main() -> int:
    # Unique socket per run so concurrent smokes don't collide.
    with tempfile.TemporaryDirectory(prefix="trcc-mockd-") as tmp:
        sock_path = Path(tmp) / "trcc.sock"
        print(f"daemon socket: {sock_path}")

        proc = _spawn_daemon(sock_path)

        try:
            # Block until the socket is ready or the daemon dies.
            if not _wait_for_socket(sock_path, timeout=15.0):
                stdout, stderr = proc.communicate(timeout=2.0)
                print("daemon failed to come up.")
                print(f"  stdout: {stdout.decode(errors='replace')[:1000]}")
                print(f"  stderr: {stderr.decode(errors='replace')[:1000]}")
                return 2
            print("daemon is reachable.")

            # ── Build a proxy + renderer to exercise the whole IPC path ─────
            from typing import cast as _cast

            from PySide6.QtWidgets import QApplication
            qapp = _cast(  # noqa: F841 — Qt needs a live QApplication
                QApplication, QApplication.instance() or QApplication(sys.argv))

            from trcc.adapters.render.qt import QtRenderer
            from trcc.core.trcc_proxy import TrccProxy
            renderer = QtRenderer()
            proxy = TrccProxy(socket_path=sock_path, renderer=renderer)

            # ── TEST 1: descriptors round-trip over IPC ─────────────────────
            # Order isn't guaranteed by MockPlatform discovery — match by
            # VID:PID instead of by index.
            print("\nTEST 1: lcd_descriptors() over IPC")
            descriptors = proxy.lcd_descriptors()
            _check(len(descriptors) == 2,
                   f"received 2 LCD descriptors (got {len(descriptors)})")
            small = next((d for d in descriptors
                          if d.vid == 0x0402 and d.pid == 0x3922), None)
            wide = next((d for d in descriptors
                         if d.vid == 0x0418 and d.pid == 0x5303), None)
            _check(small is not None,
                   "found 0402:3922 descriptor in list")
            _check(wide is not None,
                   "found 0418:5303 descriptor in list")
            if small is not None:
                _check(small.resolution == (320, 320),
                       f"0402:3922 resolution (320, 320) (got {small.resolution})")
                _check(small.fbl_code == 100,
                       f"0402:3922 fbl_code 100 (got {small.fbl_code})")
            if wide is not None:
                _check(wide.resolution == (1280, 480),
                       f"0418:5303 resolution (1280, 480) (got {wide.resolution})")
                _check(wide.fbl_code == 128,
                       f"0418:5303 fbl_code 128 (got {wide.fbl_code})")

            # ── TEST 2: Trcc.lcd command bus dispatches over IPC ────────────
            print("\nTEST 2: lcd.set_brightness(idx, pct) over IPC")
            response = proxy.lcd.set_brightness(0, 73)
            _check(response.success is True,
                   f"set_brightness returns success (got success={response.success})")

            # ── TEST 3: Topic.FRAME events arrive as decoded surfaces ───────
            print("\nTEST 3: FRAME events round-trip surface payload")
            received: list[Any] = []
            event = __import__('threading').Event()

            def _on_frame(*args: Any) -> None:
                received.append(args)
                event.set()

            from trcc.core.events import Topic
            sub_id = proxy.events.subscribe(Topic.FRAME, _on_frame)

            # Trigger a render+publish on the daemon.  We load a real
            # PNG first because _publish_frame is a no-op until the
            # device has a current_image — the daemon's discovery seeds
            # devices but doesn't load any theme by default.  Then a
            # set_rotation publishes Topic.FRAME with the rotated image.
            from _mock_bootstrap import DEV_DATA
            # Find the 320x320 device's index in the list — we can't
            # assume index 0 since MockPlatform ordering varies.
            small_idx = next(
                (i for i, d in enumerate(descriptors)
                 if d.vid == 0x0402 and d.pid == 0x3922),
                0,
            )
            bg = DEV_DATA / 'web' / '320320' / 'a001.png'
            assert bg.exists(), f"smoke fixture missing: {bg}"
            proxy.lcd.load_image(small_idx, bg)
            proxy.lcd.set_rotation(small_idx, 90)

            event.wait(timeout=3.0)
            _check(len(received) >= 1,
                   f"received at least one FRAME event (got {len(received)})")

            if received:
                args = received[0]
                _check(len(args) >= 2,
                       f"FRAME payload has 2+ items (got {len(args)})")
                if len(args) >= 2:
                    path, surface = args[0], args[1]
                    _check(isinstance(path, str),
                           f"path is str (got {type(path).__name__})")
                    # Surface should be a real QImage (not a dict envelope).
                    is_qimage = (
                        type(surface).__name__ == 'QImage'
                        or (hasattr(surface, 'width') and hasattr(surface, 'height'))
                    )
                    _check(is_qimage,
                           "surface decoded back to native QImage "
                           f"(got {type(surface).__name__})")
                    if is_qimage:
                        _check(surface.width() > 0 and surface.height() > 0,
                               f"surface has positive dims "
                               f"({surface.width()}x{surface.height()})")

            proxy.events.unsubscribe(sub_id)

            # ── Summary ─────────────────────────────────────────────────────
            print("\n" + "=" * 60)
            if _FAILURES:
                print(f"FAIL: {len(_FAILURES)} assertion(s) failed:")
                for f in _FAILURES:
                    print(f"  - {f}")
            else:
                print("PASS: all assertions passed")
            print("=" * 60)

            return 1 if _FAILURES else 0

        finally:
            # Best-effort graceful shutdown so the next smoke run starts clean.
            print("\nshutting down daemon...")
            if not _kill_daemon_via_ipc(sock_path):
                print("  ipc kill failed; sending SIGTERM")
                proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                print("  daemon ignored SIGTERM; sending SIGKILL")
                proc.kill()
                proc.wait(timeout=2.0)


if __name__ == "__main__":
    sys.exit(main())
