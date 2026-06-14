#!/usr/bin/env python3
"""Daemon-mode smoke — Command bus round-trips over the Unix socket.

Spawns ``dev/_mock_daemon.py`` (the real ``run_daemon`` against a scripted
two-device ``MockPlatform`` fleet) on a throwaway socket, connects a real
``AppProxy``, and asserts three Commands survive the JSON round-trip:

  TEST 1  DiscoverDevices — the 2-device fleet's identity (vid/pid) travels
          intact (compared against an in-process scan of the same SPECS).
  TEST 2  ConnectDevice   — the daemon attaches + handshakes the scripted
          panel and the ConnectResult comes back over the wire.
  TEST 3  SetBrightness   — the command bus mutates connected-device state
          daemon-side and returns ok.

Lifecycle uses only production helpers: ``ipc.wait_for_daemon`` to poll,
``AppProxy`` to dispatch, ``daemon.kill_daemon`` ({"kill": True}) to tear down.
The socket is shared by pointing ``$XDG_RUNTIME_DIR`` at a tmp dir — the same
mechanism ``ipc.socket_path()`` honours in production — so nothing patches
``ipc`` internals.

NOTE: frame / event streaming over IPC is intentionally NOT tested.  The
current daemon is dispatch-only — ``AppProxy`` exposes ``dispatch()`` alone,
and ``ipc`` flags ``FrameSent.surface`` as an in-process-only field.  A
frame-over-IPC test returns if/when the "GUI as remote daemon client"
event-streaming feature is built (see CLAUDE.md "Daemon Mode → still pending").
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_DEV = Path(__file__).resolve().parent
sys.path.insert(0, str(_DEV))
sys.path.insert(0, str(_DEV.parent / "src"))
sys.path.insert(0, str(_DEV.parent))

from _mock_bootstrap import DEV_TRCC
from _mock_daemon import SPECS

_FAILURES: list[str] = []


def _check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        _FAILURES.append(label)
        print(f"  FAIL  {label}  {detail}")


def main() -> int:
    # A throwaway XDG_RUNTIME_DIR → ``ipc.socket_path()`` resolves to
    # ``<tmp>/trcc.sock`` for BOTH the spawned daemon and our client helpers.
    runtime_dir = Path(tempfile.mkdtemp(prefix="trcc-daemon-smoke-"))
    child_env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime_dir),
                     QT_QPA_PLATFORM="offscreen")
    child_env.pop("TRCC_DAEMON", None)  # the daemon process IS the daemon
    # Our own process must resolve the same socket.
    os.environ["XDG_RUNTIME_DIR"] = str(runtime_dir)

    from trcc import ipc
    from trcc.core.commands import ConnectDevice, DiscoverDevices, SetBrightness
    from trcc.daemon import kill_daemon
    from trcc.proxy import AppProxy

    daemon = subprocess.Popen(
        [sys.executable, str(_DEV / "_mock_daemon.py")],
        env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        if not ipc.wait_for_daemon(timeout=15.0):
            out, err = daemon.communicate(timeout=2)
            print("daemon failed to come up.")
            print(f"  stdout: {out}\n  stderr: {err}")
            return 1

        proxy = AppProxy()

        # ── TEST 1: DiscoverDevices identity round-trips ──────────────────
        from tests.mock_platform import MockPlatform
        expected = {
            (i.vid, i.pid) for i in MockPlatform(SPECS, DEV_TRCC).scan_devices()
        }
        result = proxy.dispatch(DiscoverDevices())
        got = {(d.vid, d.pid) for d in result.devices}
        _check(result.ok, "TEST 1: DiscoverDevices ok")
        _check(got == expected, "TEST 1: device identity intact over IPC",
               f"expected {expected}, got {got}")

        # ── TEST 2: ConnectDevice handshakes daemon-side over IPC ─────────
        key = "87ad:70db"  # the bulk 854x480 panel (scripted handshake)
        conn = proxy.dispatch(ConnectDevice(key=key))
        _check(conn.ok, f"TEST 2: ConnectDevice({key}) ok",
               getattr(conn, "message", ""))
        _check(conn.handshake is not None or conn.led_handshake is not None,
               "TEST 2: ConnectResult carries a handshake over IPC")

        # ── TEST 3: SetBrightness mutates connected device over IPC ───────
        bright = proxy.dispatch(SetBrightness(key=key, percent=75))
        _check(bright.ok, "TEST 3: SetBrightness(75) ok",
               getattr(bright, "message", ""))

        if _FAILURES:
            print(f"\nFAIL: {len(_FAILURES)} assertion(s) failed: {_FAILURES}")
            return 1
        print("\nPASS: all assertions passed over IPC")
        return 0
    finally:
        print("\nshutting down daemon...")
        if not kill_daemon(timeout=5.0):
            print("  kill_daemon failed; sending SIGTERM")
            daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
        shutil.rmtree(runtime_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
