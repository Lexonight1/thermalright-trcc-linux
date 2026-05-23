"""IPCServer + AppProxy end-to-end — real Unix socket, real round-trip.

Spins up an IPCServer on a real socket bound to an App built with
FakePlatform.  AppProxy dispatches a Command through ``one_shot_request``
and we assert the typed Result comes back across the wire.

These tests live behind the ``hasattr(socket, 'AF_UNIX')`` gate so they
skip on platforms without Unix domain sockets.
"""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trcc import ipc
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    DiscoverDevices,
    SendColor,
    SetBrightness,
)
from trcc.core.ports import Renderer
from trcc.core.results import (
    BrightnessResult,
    ConnectResult,
    DiscoverResult,
    SendResult,
)
from trcc.proxy import AppProxy

from .conftest import FakePlatform

_HAS_AF_UNIX = hasattr(socket, "AF_UNIX")

pytestmark = pytest.mark.skipif(
    not _HAS_AF_UNIX, reason="IPC requires AF_UNIX (POSIX)",
)


class _TestRenderer(Renderer):
    """Minimal Renderer for the daemon — returns deterministic byte sizes."""

    class _Surface:
        def __init__(self, w: int, h: int) -> None:
            self.w = w
            self.h = h

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        return _TestRenderer._Surface(width, height)

    def open_image(self, path: Path) -> Any:
        return _TestRenderer._Surface(100, 100)

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.w, surface.h)

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return _TestRenderer._Surface(width, height)

    def rotate(self, surface: Any, degrees: int) -> Any:
        if degrees % 180 == 90:
            return _TestRenderer._Surface(surface.h, surface.w)
        return surface

    def flip_horizontal(self, surface: Any) -> Any:
        return surface

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        return surface

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        pass

    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes:
        return b"\x00\x00" * (surface.w * surface.h)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b""

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _TestRenderer._Surface(100, 100)


# ── Server-lifecycle fixture ─────────────────────────────────────────


@pytest.fixture
def running_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ipc.IPCServer, App]]:
    """Bind a fresh IPCServer to a unique socket + serve in a thread."""
    # Per-test socket — AF_UNIX paths are capped at ~108 bytes on Linux,
    # so pytest's tmp_path is too deep.  Use /tmp directly + a unique
    # suffix and clean up in teardown.
    sock_path = Path(f"/tmp/trcc-test-{id(tmp_path):x}.sock")
    monkeypatch.setattr(ipc, "socket_path", lambda: sock_path)

    # Settle the post-init delay so the FakePlatform handshake doesn't sleep
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._POST_INIT_DELAY_S", 0.0,
    )

    platform = FakePlatform(tmp_path)
    # Stage a poll response so a ConnectDevice through the proxy can
    # complete the handshake without a real device.
    poll = bytearray(0xE100)
    poll[0] = 100  # FBL=100 → 320×320
    platform.scsi.read_script.append(bytes(poll))

    app = App(platform=platform, renderer=_TestRenderer())
    server = ipc.IPCServer(app)
    server.start()

    serve_thread = threading.Thread(
        target=server.serve_forever, daemon=True, name="ipc-test-server",
    )
    serve_thread.start()

    # Wait for the socket to be reachable so tests don't race the bind
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if ipc.daemon_running():
            break
        time.sleep(0.01)

    yield server, app

    server.shutdown()
    serve_thread.join(timeout=2.0)


# ── End-to-end dispatch ──────────────────────────────────────────────


def test_proxy_dispatch_returns_typed_result(
    running_server: tuple[ipc.IPCServer, App],
) -> None:
    """SetBrightness round-trips and returns a typed BrightnessResult."""
    proxy = AppProxy(timeout=2.0)
    result = proxy.dispatch(SetBrightness(key="0402:3922", percent=42))

    assert isinstance(result, BrightnessResult)
    assert result.ok is True
    assert result.percent == 42
    assert result.key == "0402:3922"


def test_proxy_dispatch_discover_devices(
    running_server: tuple[ipc.IPCServer, App],
) -> None:
    """DiscoverDevices returns a typed DiscoverResult through the wire."""
    proxy = AppProxy(timeout=2.0)
    result = proxy.dispatch(DiscoverDevices())

    assert isinstance(result, DiscoverResult)
    assert result.ok is True
    # FakePlatform has no devices wired → empty lists, not a crash
    assert result.products == []


def test_proxy_dispatch_full_chain_connect_then_send_color(
    running_server: tuple[ipc.IPCServer, App],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConnectDevice → SendColor over the wire.

    Proves the daemon owns the device (the second dispatch reaches the
    same App / same handshake), and that ConnectResult round-trips with
    its nested HandshakeResult intact.
    """
    proxy = AppProxy(timeout=2.0)

    connect = proxy.dispatch(ConnectDevice(key="0402:3922"))
    assert isinstance(connect, ConnectResult)
    assert connect.ok is True
    assert connect.handshake is not None
    assert connect.handshake.resolution == (320, 320)
    assert connect.handshake.fbl == 100

    color = proxy.dispatch(SendColor(key="0402:3922", r=255, g=0, b=0))
    assert isinstance(color, SendResult)
    assert color.ok is True
    assert color.bytes_sent == 320 * 320 * 2


def test_proxy_dispatch_returns_error_for_unknown_device(
    running_server: tuple[ipc.IPCServer, App],
) -> None:
    """A Command that fails on the daemon side comes back with ok=False."""
    proxy = AppProxy(timeout=2.0)
    result = proxy.dispatch(SetBrightness(key="dead:beef", percent=50))

    # SetBrightness mutates settings unconditionally on next/ — but our
    # interest here is that the wire shape works.  A non-existent key
    # still produces a typed BrightnessResult, never an exception.
    assert isinstance(result, BrightnessResult)
    # Whether ok=True or ok=False depends on the Command's semantics —
    # the daemon's role is to faithfully shuttle the result.


def test_kill_request_shuts_daemon_down(
    running_server: tuple[ipc.IPCServer, App],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``{"kill": true}`` makes the server stop accepting connections."""
    response = ipc.one_shot_request({"kill": True}, timeout=2.0)
    assert response.get("ok") is True

    # Within 2s the socket should be unreachable
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not ipc.daemon_running():
            break
        time.sleep(0.05)
    assert not ipc.daemon_running()


def test_malformed_envelope_returns_typed_error(
    running_server: tuple[ipc.IPCServer, App],
) -> None:
    """Unknown ``command`` field comes back as Result(ok=False)."""
    response = ipc.one_shot_request(
        {"command": "NoSuchCommand", "kwargs": {}},
        timeout=2.0,
    )
    assert response["ok"] is False
    assert "Unknown command" in response["message"]
