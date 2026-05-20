"""API route smoke tests — every router exercised through FastAPI's TestClient.

Phase D boilerplate: one fixture that builds the full FastAPI app over
a FakePlatform + minimal renderer, plus one smoke test per router so
every route handler at least *imports cleanly + dispatches through the
Command bus + serializes a response*.

Future tests for specific API behavior land in this file (or sibling
``test_api_<router>.py`` files) using the same ``api_client`` fixture.
The fixture is the foundation; the smoke checks are the example.

Routes that need a connected device hit the "not attached" code path
and surface a structured error — that's a successful smoke (proves
the route reaches Command dispatch + Result serialization).  Real
end-to-end happy paths land alongside the parity tests where the
device fakes get fuller setup.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trcc.next.app import App
from trcc.next.core.ports import Renderer

from .conftest import FakePlatform

# =========================================================================
# Minimal renderer — FastAPI build_app needs *some* renderer to construct
# DisplayService; smoke tests don't render anything visible.
# =========================================================================


class _SmokeRenderer(Renderer):
    """No-op renderer just so DisplayService builds cleanly."""

    class _Surface:
        def __init__(self, w: int = 100, h: int = 100) -> None:
            self.w, self.h = w, h

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        return _SmokeRenderer._Surface(width, height)

    def open_image(self, path: Path) -> Any:
        return _SmokeRenderer._Surface()

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.w, surface.h)

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return _SmokeRenderer._Surface(width, height)

    def rotate(self, surface: Any, degrees: int) -> Any:
        return surface

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        return surface

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        pass

    def encode_rgb565(self, surface: Any) -> bytes:
        return b"\x00\x00" * (surface.w * surface.h)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b""

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _SmokeRenderer._Surface()


# =========================================================================
# Fixture — the API TestClient
# =========================================================================


@pytest.fixture
def api_client(fake_platform: FakePlatform) -> Iterator[TestClient]:
    """Builds the full FastAPI app + yields a TestClient.

    The underlying App is wired with the standard FakePlatform fixture
    so any route handler can be exercised; nothing actually talks to
    USB.  Future tests can pre-populate ``app.state.trcc.devices``
    before making requests when they need a connected-device scenario.
    """
    from trcc.next.ui.api.main import build_app

    trcc = App(platform=fake_platform, renderer=_SmokeRenderer())
    api = build_app(trcc=trcc)
    with TestClient(api) as client:
        yield client


# =========================================================================
# Root endpoint — meta smoke
# =========================================================================


def test_root_returns_endpoint_directory(api_client: TestClient) -> None:
    """``GET /`` lists every documented endpoint — proves the app
    constructs + every router included successfully."""
    resp = api_client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "TRCC API"
    assert body["version"] == "next"
    # The endpoint catalog mentions at least the canonical routes
    endpoints = " ".join(body["endpoints"])
    for canonical in (
        "GET  /devices",
        "POST /devices/{key}/connect",
        "POST /devices/{key}/display/brightness",
        "POST /devices/{key}/led/colors",
        "GET  /system/info",
        "POST /config/temp-unit",
    ):
        assert canonical in endpoints, f"endpoint catalog missing {canonical!r}"


def test_openapi_schema_loads(api_client: TestClient) -> None:
    """FastAPI's auto-generated OpenAPI must build — any router whose
    schemas don't resolve breaks this with a 500."""
    resp = api_client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "TRCC API"
    # All six routers contributed paths
    assert "/devices" in schema["paths"]
    assert any(p.startswith("/devices/{key}/display/") for p in schema["paths"])
    assert any(p.startswith("/devices/{key}/led/") for p in schema["paths"])
    assert "/system/info" in schema["paths"]
    assert "/config/temp-unit" in schema["paths"]


# =========================================================================
# devices router
# =========================================================================


def test_devices_list_returns_discover_response(api_client: TestClient) -> None:
    """``GET /devices`` always succeeds — empty list when FakePlatform
    reports no attached devices."""
    resp = api_client.get("/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["products"] == []


def test_devices_connect_unknown_returns_4xx(api_client: TestClient) -> None:
    """Connecting an unknown vid:pid surfaces a structured error,
    not a 500.  Proves the dispatch path → result envelope works."""
    resp = api_client.post("/devices/dead:beef/connect")
    # http_error_if_failed maps ok=False → 400 by default
    assert resp.status_code in (400, 404)


def test_devices_disconnect_unknown_returns_4xx(api_client: TestClient) -> None:
    resp = api_client.post("/devices/dead:beef/disconnect")
    assert resp.status_code in (400, 404)


# =========================================================================
# display router
# =========================================================================


def test_display_set_orientation_validates_degrees(
    api_client: TestClient,
) -> None:
    """Pydantic enforces 0 <= degrees <= 270; an out-of-range value
    surfaces as 422, not 500."""
    resp = api_client.post(
        "/devices/0402:3922/display/orientation",
        json={"degrees": 1000},
    )
    assert resp.status_code == 422


def test_display_set_brightness_on_valid_key(api_client: TestClient) -> None:
    """SetBrightness mutates Settings unconditionally — succeeds even
    without a connected device."""
    resp = api_client.post(
        "/devices/0402:3922/display/brightness",
        json={"percent": 50},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["percent"] == 50


def test_display_send_color_unknown_device_returns_4xx(
    api_client: TestClient,
) -> None:
    """``display/color`` needs a connected device; an unknown key
    surfaces a structured error envelope."""
    resp = api_client.post(
        "/devices/dead:beef/display/color",
        json={"r": 255, "g": 0, "b": 0},
    )
    assert resp.status_code in (400, 404)


# =========================================================================
# led router
# =========================================================================


def test_led_set_color_persists_via_settings(api_client: TestClient) -> None:
    """SetLedColor writes to per-device LedDeviceSettings without
    needing a connected device — succeeds with ok=True."""
    resp = api_client.post(
        "/devices/0416:8001/led/color",
        json={"color": [10, 20, 30]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_led_set_mode_round_trip(api_client: TestClient) -> None:
    """Mode enum validation: every legal value the regex accepts is
    routed to a valid LEDMode."""
    resp = api_client.post(
        "/devices/0416:8001/led/mode",
        json={"mode": "rainbow"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_led_set_mode_rejects_unknown(api_client: TestClient) -> None:
    """Unknown mode strings fail Pydantic validation before reaching
    the Command bus."""
    resp = api_client.post(
        "/devices/0416:8001/led/mode",
        json={"mode": "supernova"},
    )
    assert resp.status_code == 422


def test_led_set_brightness_validates_range(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/brightness",
        json={"percent": 200},
    )
    assert resp.status_code == 422


# =========================================================================
# system router
# =========================================================================


def test_system_info_returns_platform_metadata(api_client: TestClient) -> None:
    resp = api_client.get("/system/info")
    assert resp.status_code == 200
    body = resp.json()
    # GetPlatformInfo Command surfaces distro + install method
    assert "distro" in body or "ok" in body


def test_system_sensors_returns_readings_list(api_client: TestClient) -> None:
    resp = api_client.get("/system/sensors")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["readings"], list)


# =========================================================================
# config router
# =========================================================================


def test_config_set_temp_unit_round_trips(api_client: TestClient) -> None:
    resp = api_client.post("/config/temp-unit", json={"unit": "F"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["unit"] == "F"


def test_config_set_language_round_trips(api_client: TestClient) -> None:
    resp = api_client.post("/config/language", json={"language": "de"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_config_set_refresh_interval_round_trips(api_client: TestClient) -> None:
    resp = api_client.post(
        "/config/refresh-interval", json={"seconds": 3.0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


# =========================================================================
# theme router — listing only (save/export/import need real fixtures)
# =========================================================================


def test_theme_save_requires_key(api_client: TestClient) -> None:
    """SaveTheme needs a connected device + active theme; unknown key
    returns a structured error, not a 500."""
    resp = api_client.post(
        "/theme/save",
        json={"key": "dead:beef", "name": "test"},
    )
    assert resp.status_code in (400, 404, 422)
