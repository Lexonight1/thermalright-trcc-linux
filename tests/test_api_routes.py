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

from trcc.app import App
from trcc.core.ports import Renderer
from trcc.core.protocol import FBL_PROFILES

from .conftest import FakePlatform

# Unique resolutions from the canonical FBL profile registry.
API_TEST_RESOLUTIONS: list[tuple[int, int]] = sorted({
    (p.width, p.height) for p in FBL_PROFILES.values()
})

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
    from trcc.ui.api.main import build_app

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
# auth — token + pairing
# =========================================================================


def test_health_endpoint_is_unauthed(api_client: TestClient) -> None:
    """``/health`` is reachable without any token — it's the liveness
    probe LB / monitoring hits."""
    from trcc.ui.api.main import configure_auth
    configure_auth("secret-token")
    try:
        resp = api_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body
    finally:
        configure_auth(None)


def test_request_without_token_rejected_when_auth_enforced(
    api_client: TestClient,
) -> None:
    """When ``_api_token`` is set, every non-exempt path requires a
    matching ``X-API-Token`` header.  401 otherwise."""
    from trcc.ui.api.main import configure_auth
    configure_auth("secret-token")
    try:
        resp = api_client.get("/devices")
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Invalid token"}
    finally:
        configure_auth(None)


def test_request_with_correct_token_accepted(api_client: TestClient) -> None:
    from trcc.ui.api.main import configure_auth
    configure_auth("secret-token")
    try:
        resp = api_client.get(
            "/devices", headers={"X-API-Token": "secret-token"},
        )
        assert resp.status_code == 200
    finally:
        configure_auth(None)


def test_request_with_wrong_token_rejected(api_client: TestClient) -> None:
    from trcc.ui.api.main import configure_auth
    configure_auth("secret-token")
    try:
        resp = api_client.get(
            "/devices", headers={"X-API-Token": "wrong-token"},
        )
        assert resp.status_code == 401
    finally:
        configure_auth(None)


def test_pair_disabled_without_pairing_code(api_client: TestClient) -> None:
    """``/pair`` returns 503 when ``set_pairing_code`` wasn't called."""
    from trcc.ui.api.main import configure_auth, set_pairing_code
    configure_auth("secret")
    set_pairing_code(None)
    try:
        resp = api_client.post("/pair", params={"code": "ABCDEF"})
        assert resp.status_code == 503
    finally:
        configure_auth(None)


def test_pair_exchanges_code_for_token(api_client: TestClient) -> None:
    """Correct pairing code → 200 + the persistent API token."""
    from trcc.ui.api.main import configure_auth, set_pairing_code
    configure_auth("the-real-token")
    set_pairing_code("ABCDEF")
    try:
        resp = api_client.post("/pair", params={"code": "ABCDEF"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["token"] == "the-real-token"
    finally:
        configure_auth(None)
        set_pairing_code(None)


def test_pair_rejects_wrong_code(api_client: TestClient) -> None:
    from trcc.ui.api.main import configure_auth, set_pairing_code
    configure_auth("the-real-token")
    set_pairing_code("ABCDEF")
    try:
        resp = api_client.post("/pair", params={"code": "WRONG1"})
        assert resp.status_code == 403
    finally:
        configure_auth(None)
        set_pairing_code(None)


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


def test_led_toggle_global(api_client: TestClient) -> None:
    """ToggleLed flips global_on through the API."""
    resp = api_client.post(
        "/devices/0416:8001/led/toggle",
        json={"on": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "global" in body["message"].lower()


def test_led_toggle_zone_out_of_range_rejected(api_client: TestClient) -> None:
    """Negative zone numbers fail Pydantic validation."""
    resp = api_client.post(
        "/devices/0416:8001/led/toggle",
        json={"on": True, "zone": -1},
    )
    assert resp.status_code == 422


def test_led_zone_sync_enable(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/zone-sync",
        json={"enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_led_select_zone(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/select-zone",
        json={"zone": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "3" in body["message"]


def test_led_toggle_segment(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/toggle-segment",
        json={"index": 7, "on": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "Segment 7" in body["message"]


def test_led_snapshot(api_client: TestClient) -> None:
    """LedSnapshot returns the persisted LED state."""
    resp = api_client.get("/devices/0416:8001/led/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["key"] == "0416:8001"
    assert "mode" in body


def test_led_styles_listing(api_client: TestClient) -> None:
    """``GET /led/styles`` returns the PM registry."""
    resp = api_client.get("/led/styles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["styles"]) > 0
    assert any(s["style"] == "ax120" for s in body["styles"])


def test_led_modes_listing(api_client: TestClient) -> None:
    resp = api_client.get("/led/modes")
    assert resp.status_code == 200
    body = resp.json()
    assert "STATIC" in body["modes"]
    assert "RAINBOW" in body["modes"]


def test_display_snapshot(api_client: TestClient) -> None:
    resp = api_client.get("/devices/0402:3922/display/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["key"] == "0402:3922"


def test_display_restore_theme_no_persisted(api_client: TestClient) -> None:
    """RestoreLastTheme with nothing persisted returns a 400 from
    ``http_error_if_failed`` (Result.ok=False)."""
    resp = api_client.post("/devices/0402:3922/display/restore-theme")
    assert resp.status_code in (400, 404)


def test_system_list_gpus(api_client: TestClient) -> None:
    resp = api_client.get("/system/gpus")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_system_snapshot(api_client: TestClient) -> None:
    resp = api_client.get("/system/snapshot")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "language" in body
    assert "refresh_interval_s" in body


# --- Tier 2 -----------------------------------------------------------------


def test_led_clock_format(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/clock-format",
        json={"is_24h": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["is_24h"] is False


def test_led_week_start(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/week-start",
        json={"sunday_first": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["sunday_first"] is True


def test_led_memory_ratio(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/memory-ratio",
        json={"ratio_mode": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["ratio_mode"] is False


def test_led_disk_index(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/disk-index",
        json={"index": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["index"] == 1


def test_led_disk_index_negative_rejected(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0416:8001/led/disk-index",
        json={"index": -1},
    )
    assert resp.status_code == 422


def test_system_hdd_enabled(api_client: TestClient) -> None:
    resp = api_client.post(
        "/system/hdd-enabled", json={"enabled": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is True


def test_display_background_mode_color(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/background-mode",
        json={"mode": "color"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["mode"] == "color"


def test_display_background_mode_rejects_bad(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/background-mode",
        json={"mode": "bogus"},
    )
    assert resp.status_code == 422


def test_display_overlay_background(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/overlay-background",
        json={"color": [17, 34, 51]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["color"] == [17, 34, 51]


# --- Tier 3 -----------------------------------------------------------------


def test_display_pause_video_no_playback(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/pause-video",
        json={"paused": True},
    )
    assert resp.status_code in (400, 404)


def test_display_seek_negative_rejected(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/seek-video",
        json={"frame": -1},
    )
    assert resp.status_code == 422


def test_display_loop_no_playback(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/loop-video",
        json={"loop": False},
    )
    assert resp.status_code in (400, 404)


@pytest.mark.parametrize("resolution", API_TEST_RESOLUTIONS)
def test_display_masks_listing_empty(
    api_client: TestClient, resolution: tuple[int, int],
) -> None:
    """``GET /display/masks?width=W&height=H`` returns ok=True with an
    empty list when the user has no masks at that resolution yet.
    Parametrized over every resolution in FBL_PROFILES to prove the
    resolution-aware path port resolves for every supported canvas."""
    w, h = resolution
    resp = api_client.get(
        "/display/masks", params={"width": w, "height": h},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["masks"] == []


def test_theme_delete_unknown(api_client: TestClient) -> None:
    """Deleting a non-existent theme returns a structured error."""
    resp = api_client.delete("/theme/definitely-not-real-9zq")
    assert resp.status_code in (400, 404)


def test_system_fonts_listing(api_client: TestClient) -> None:
    resp = api_client.get("/system/fonts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_system_disks_listing(api_client: TestClient) -> None:
    resp = api_client.get("/system/disks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


# --- Tier 4 — overlay element CRUD ----------------------------------------


def test_overlay_add_returns_id(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/overlay-elements",
        json={"type": "text", "x": 5, "y": 10, "text": "hi"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["element"]["id"].startswith("el_")


def test_overlay_add_rejects_bad_type(api_client: TestClient) -> None:
    resp = api_client.post(
        "/devices/0402:3922/display/overlay-elements",
        json={"type": "bogus"},
    )
    assert resp.status_code == 422


def test_overlay_update_unknown(api_client: TestClient) -> None:
    resp = api_client.patch(
        "/devices/0402:3922/display/overlay-elements/el_nope",
        json={"x": 5},
    )
    assert resp.status_code in (400, 404)


def test_overlay_delete_unknown(api_client: TestClient) -> None:
    resp = api_client.delete(
        "/devices/0402:3922/display/overlay-elements/el_nope",
    )
    assert resp.status_code in (400, 404)


def test_overlay_round_trip(api_client: TestClient) -> None:
    """Add → update → flash → delete one element via the API."""
    add = api_client.post(
        "/devices/0402:3922/display/overlay-elements",
        json={"type": "text", "text": "hi", "element_id": "el_api"},
    )
    assert add.status_code == 200
    eid = add.json()["element"]["id"]
    upd = api_client.patch(
        f"/devices/0402:3922/display/overlay-elements/{eid}",
        json={"text": "bye"},
    )
    assert upd.status_code == 200
    flash = api_client.post(
        f"/devices/0402:3922/display/overlay-elements/{eid}/flash",
        json={"duration_ms": 500},
    )
    assert flash.status_code == 200
    rm = api_client.delete(
        f"/devices/0402:3922/display/overlay-elements/{eid}",
    )
    assert rm.status_code == 200


def test_overlay_set_config_bulk(api_client: TestClient) -> None:
    """PUT replaces the element list wholesale."""
    resp = api_client.put(
        "/devices/0402:3922/display/overlay-elements",
        json={"elements": [
            {"id": "a", "type": "text", "text": "one"},
            {"id": "b", "type": "metric", "metric": "cpu_temp"},
        ]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["elements"]) == 2


# --- Tier 5 — cloud themes (offline list) ----------------------------------


def test_theme_cloud_list_returns_catalog(api_client: TestClient) -> None:
    resp = api_client.get("/theme/cloud")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert any(c["prefix"] == "a" for c in body["categories"])
    assert any(t["id"] == "a001" for t in body["themes"])


def test_theme_cloud_list_unknown_category(api_client: TestClient) -> None:
    resp = api_client.get("/theme/cloud", params={"category": "zzz"})
    # Result.ok = False → http_error_if_failed → 400
    assert resp.status_code in (400, 404)


# --- i18n --------------------------------------------------------------------


def test_list_languages_returns_full_set(api_client: TestClient) -> None:
    """``GET /system/languages`` lists every i18n code."""
    resp = api_client.get("/system/languages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    codes = {lang["code"] for lang in body["languages"]}
    assert {"en", "zh", "fr", "de", "ja"} <= codes


def test_set_language_rejects_unknown_code(api_client: TestClient) -> None:
    """``POST /config/language`` with an unknown ISO code returns 400."""
    resp = api_client.post(
        "/config/language", json={"language": "zz_unknown"},
    )
    assert resp.status_code in (400, 404)


# --- Diagnostics ------------------------------------------------------------


def test_system_health(api_client: TestClient) -> None:
    resp = api_client.get("/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert any(c["name"] == "python-version" for c in body["checks"])


def test_system_doctor(api_client: TestClient) -> None:
    resp = api_client.get("/system/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert "rendered" in body


def test_system_debug_report_in_memory(api_client: TestClient) -> None:
    resp = api_client.post("/system/debug-report", json={"log_tail_lines": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "## Platform" in body["rendered_text"]
    assert body["output_path"] == ""


def test_system_debug_report_to_path(
    api_client: TestClient, tmp_path,
) -> None:
    out = tmp_path / "bundle.txt"
    resp = api_client.post(
        "/system/debug-report",
        json={"output_path": str(out), "log_tail_lines": 10},
    )
    assert resp.status_code == 200
    assert out.is_file()


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


def test_config_set_time_format_round_trips(api_client: TestClient) -> None:
    resp = api_client.post("/config/time-format", json={"fmt": "12h"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["fmt"] == "12h"


def test_config_set_time_format_rejects_bad_value(api_client: TestClient) -> None:
    resp = api_client.post("/config/time-format", json={"fmt": "bogus"})
    assert resp.status_code == 422  # Pydantic pattern validation


def test_config_set_date_format_round_trips(api_client: TestClient) -> None:
    resp = api_client.post("/config/date-format", json={"fmt": "dd.MM.yyyy"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["fmt"] == "dd.MM.yyyy"


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


def test_theme_list_returns_empty_under_empty_dir(
    api_client: TestClient, tmp_path: Path,
) -> None:
    """ListThemes against an empty directory returns ok with an empty
    list — proves the route reaches Command dispatch + Result
    serialization end-to-end."""
    resp = api_client.get("/theme/list", params={"directory": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["themes"] == []
    assert body["directory"] == str(tmp_path)


def test_theme_list_finds_themes_in_dir(
    api_client: TestClient, tmp_path: Path,
) -> None:
    """ListThemes returns each theme directory containing config.json."""
    theme_dir = tmp_path / "MyTheme"
    theme_dir.mkdir()
    (theme_dir / "trcc.json").write_text(
        '{"width": 480, "height": 480, "elements": []}',
    )
    resp = api_client.get("/theme/list", params={"directory": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["themes"]) == 1
    assert body["themes"][0]["name"] == "MyTheme"
    assert body["themes"][0]["resolution"] == [480, 480]
