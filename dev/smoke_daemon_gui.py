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

  TEST 4  EVENT STREAM — the observe half.  ``AppProxy.events`` opens a
          subscription on the real daemon and a ``ConnectDevice`` dispatched
          afterwards arrives back as a typed ``DeviceConnected`` instance.

TEST 4 is the one that matters for the unified bus: until it passed, a GUI
could not run as a daemon client at all, because both Qt skins build a
``BusBridge(app.events)`` at construction and ``AppProxy`` had no such
attribute.  That single ``AttributeError`` is why ``TRCC_DAEMON=1`` stayed off
by default and the Command bus stayed optional.

``FrameSent.surface`` remains an in-process-only field — a remote client falls
back to re-rendering via ``BuildPreview``, which is a separate increment.
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
    from trcc.core.events import DeviceConnected
    from trcc.daemon import kill_daemon
    from trcc.proxy import AppProxy

    daemon = subprocess.Popen(
        [sys.executable, str(_DEV / "_mock_daemon.py")],
        env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        if not ipc.wait_for_daemon(timeout=15.0):
            # Kill BEFORE reading.  A daemon that never bound is usually still
            # alive -- often blocked writing to these very pipes -- so
            # ``communicate`` waits for an exit that is not coming and raises
            # TimeoutExpired.  The error path then destroys the only evidence
            # of why the daemon failed, which is the one thing it exists for.
            daemon.kill()
            out, err = daemon.communicate()
            print("daemon failed to come up.")
            print(f"  stdout: {out}\n  stderr: {err[-2000:]}")
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

        # ── TEST 4 (armed before TEST 2, which is what fires it) ──────────
        # ``AppProxy.events`` is the attribute that did not exist.  Subscribe
        # BEFORE the connect so the event we are waiting for is produced by a
        # real dispatch rather than a synthetic publish.
        import time
        observed: list[object] = []
        proxy.events.subscribe(DeviceConnected, observed.append)
        time.sleep(0.5)   # let the reader thread attach its subscription

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

        # ── TEST 4: the event crossed the socket as a typed instance ──────
        # Ours, not the first: the App answers before its own coldplug (#314),
        # so the fleet's other panel can connect -- and publish -- first.
        # Reading observed[0] made this fail whenever it did.
        def ours() -> list[object]:
            return [e for e in observed if getattr(e, "key", None) == key]

        deadline = time.time() + 5.0
        while not ours() and time.time() < deadline:
            time.sleep(0.05)
        _check(bool(observed),
               "TEST 4: DeviceConnected arrived over the event stream",
               "no event received — the observe half is not working")
        _check(all(isinstance(e, DeviceConnected) for e in observed),
               "TEST 4: it decoded to a typed Event, not a dict",
               f"got {[type(e).__name__ for e in observed]}")
        _check(bool(ours()),
               "TEST 4: the event's key survived the wire",
               f"no DeviceConnected for {key}; got keys "
               f"{[getattr(e, 'key', None) for e in observed]}")

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
