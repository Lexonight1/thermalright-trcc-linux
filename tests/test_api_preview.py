"""The two preview routes, end-to-end — previously untested entirely.

Both used to hand-assemble the render (device → theme → sensors →
build_preview_surface → encode); both now dispatch ``BuildPreview`` and just
choose an HTTP shape for the answer.  These pin that shape: the bytes, the
media type, and which empty answer is a 404.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.models import Theme

from .mock_platform import MockPlatform

_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}
_KEY = "87ad:70db"
_PREVIEW = f"/devices/{_KEY}/display/preview"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """The real API over a real renderer + a connected mock device."""
    from trcc.ui.api.main import build_app

    trcc = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    trcc.attach(0x87AD, 0x70DB)
    assert trcc.dispatch(ConnectDevice(key=_KEY)).ok
    with TestClient(build_app(trcc=trcc)) as c:
        yield c


def _load_theme(client: TestClient, tmp_path: Path) -> None:
    client.app.state.trcc.active_themes[_KEY] = Theme(   # type: ignore[attr-defined]
        path=tmp_path / "theme", name="t",
        resolution=(854, 480), config={"elements": []},
    )


# ── GET /preview ─────────────────────────────────────────────────────────


def test_preview_404s_for_an_unattached_device(client: TestClient) -> None:
    resp = client.get("/devices/dead:beef/display/preview")

    assert resp.status_code == 404
    assert "dead:beef" in resp.json()["detail"]


def test_preview_404s_when_no_theme_is_loaded(client: TestClient) -> None:
    """Attached but nothing to show — a different 404 from "no such device"."""
    resp = client.get(_PREVIEW)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "No active theme — load one first"


def test_preview_returns_a_png_of_the_panel(
    client: TestClient, tmp_path: Path,
) -> None:
    _load_theme(client, tmp_path)

    resp = client.get(_PREVIEW)

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")


# ── WS /preview/stream ───────────────────────────────────────────────────


def test_preview_stream_refuses_an_unattached_device(client: TestClient) -> None:
    """Closed with 1008 before the accept — the client never sees a frame."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/devices/dead:beef/display/preview/stream",
        ) as ws:
            ws.receive_bytes()


def test_preview_stream_sends_jpeg_frames(
    client: TestClient, tmp_path: Path,
) -> None:
    _load_theme(client, tmp_path)

    with client.websocket_connect(f"{_PREVIEW}/stream") as ws:
        frame = ws.receive_bytes()

    assert frame.startswith(b"\xff\xd8\xff")   # JPEG SOI + marker
