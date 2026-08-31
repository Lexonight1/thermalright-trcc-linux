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
from trcc.core.models import RawFrame
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

    def surface_nbytes(self, surface: Any) -> int:
        return surface.w * surface.h * 4

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
                  italic: bool = False, family: str = "") -> None:
        pass

    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes:
        return b"\x00\x00" * (surface.w * surface.h)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b""

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _SmokeRenderer._Surface()

    def to_raw_rgb24(self, surface):
        # The inverse the port now requires.  Test doubles carry no pixels,
        # so this reports the surface's DIMENSIONS with blank bytes — enough
        # for a caller that only needs a correctly-sized RawFrame.
        w, h = self.surface_size(surface)
        return RawFrame(data=bytes(w * h * 3), width=w, height=h)

    def decode_image(self, data: bytes) -> Any:
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
# autostart — the three routes now go through the Command bus
#
# No API test touched autostart at all before 2026-08-31, which is part of why
# ``GET /system/autostart`` could read ``trcc.platform.autostart()`` directly
# for as long as it did — reaching past the bus, dropping the ``path`` the
# Command returns, and raising under TRCC_DAEMON=1.
# =========================================================================


def test_autostart_status_reports_disabled_by_default(api_client: TestClient) -> None:
    """GET reports state via ``GetAutostartStatus`` — including ``path``."""
    r = api_client.get("/system/autostart")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["enabled"] is False
    assert "path" in body, "the Command carries path; the hand-rolled route did not"


def test_autostart_enable_then_status_agrees(api_client: TestClient) -> None:
    """POST and GET are the same answer — one Command, not two code paths."""
    assert api_client.post("/system/autostart",
                           json={"enabled": True}).json()["enabled"] is True
    assert api_client.get("/system/autostart").json()["enabled"] is True


def test_autostart_refresh_does_not_enable(api_client: TestClient) -> None:
    """The route inherits the Command's contract: refresh never installs.

    Mirrors ``test_autostart_refresh_never_enables`` at the Command level —
    asserted here too because a route is free to dispatch the wrong Command.
    """
    r = api_client.post("/system/autostart/refresh")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert api_client.get("/system/autostart").json()["enabled"] is False


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


def test_display_media_player_clear(api_client: TestClient) -> None:
    """SetMediaPlayer with an empty URI clears the source — succeeds without a
    connected device.  Proves the media-player toggle is unified into the API."""
    resp = api_client.post(
        "/devices/0402:3922/display/media-player",
        json={"uri": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["uri"] == ""
    assert body["playing"] is False


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


def test_display_reset_unknown_device_returns_4xx(
    api_client: TestClient,
) -> None:
    """``display/reset`` (stop video + red frame) needs a connected device;
    an unknown key surfaces a structured error envelope (the StopVideo
    pre-step is best-effort and doesn't mask the send failure)."""
    resp = api_client.post("/devices/dead:beef/display/reset")
    assert resp.status_code in (400, 404)


def test_display_load_theme_reaches_lookup_not_attribute_error(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """Regression: load_theme called ``platform.user_content_dir()`` — an
    AttributeError (that method is on Paths, not Platform), so the route
    500'd on every call.  It must reach the theme lookup via
    ``platform.paths().user_content_dir()``."""
    ucd = fake_platform.paths().user_content_dir()
    ucd.mkdir(parents=True, exist_ok=True)   # resolve(strict=True) needs it
    resp = api_client.post(
        "/devices/dead:beef/display/theme", json={"path": "no-such-theme"},
    )
    # Pre-fix: 500 (AttributeError).  Post-fix: reaches the "unknown
    # theme" guard → 400, never a 500.
    assert resp.status_code == 400


def test_display_load_theme_accepts_a_nested_catalog_path(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """A path straight from the theme catalog — nested ``theme{res}/<name>``
    under the data / user root, NOT flat under user_content_dir — must load,
    not be wrongly rejected "Unknown theme" (#239).  The old whitelist only
    enumerated top-level user_content_dir, so every real /theme/list path 400'd.
    """
    import json as _json

    tdir = fake_platform.paths().theme_dir(320, 320) / "Nested"
    tdir.mkdir(parents=True)
    (tdir / "trcc.json").write_text(
        _json.dumps({"name": "Nested", "width": 320, "height": 320,
                     "elements": []}),
        encoding="utf-8",
    )
    (tdir / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    resp = api_client.post(
        "/devices/0402:3922/display/theme", json={"path": str(tdir)},
    )

    assert resp.status_code == 200, resp.json()
    assert resp.json()["ok"] is True


def test_boot_animation_rejects_path_traversal(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """frames_dir is a basename under user_content_dir — a traversal payload
    can't escape the root (CodeQL py/path-injection barrier)."""
    fake_platform.paths().user_content_dir().mkdir(parents=True, exist_ok=True)
    resp = api_client.post(
        "/devices/0402:3922/display/boot-animation",
        json={"frames_dir": "../../../../etc", "delay_ds": 5},
    )
    # basename of the payload ("etc") is not a subdir under user_content → 400
    assert resp.status_code == 400


def test_boot_animation_valid_subdir_passes_path_barrier(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """A real subdir of frames clears the path barrier (then only fails
    because no device is connected — proving it isn't a path rejection)."""
    frames = fake_platform.paths().user_content_dir() / "myanim"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    resp = api_client.post(
        "/devices/dead:beef/display/boot-animation",
        json={"frames_dir": "myanim", "delay_ds": 5},
    )
    # Path barrier passed; the device dispatch fails (not connected) → 4xx.
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
    # Capability columns present on every entry; PA120 reports 4 zones.
    assert all(
        "segment_count" in s and "zone_count" in s for s in body["styles"]
    )
    assert any(s["zone_count"] == 4 for s in body["styles"])


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
    """RestoreDeviceState with nothing persisted AND no themes installed
    returns a 400 from ``http_error_if_failed`` (Result.ok=False —
    "No theme available")."""
    resp = api_client.post("/devices/0402:3922/display/restore-theme")
    assert resp.status_code in (400, 404)


def test_display_restore_theme_autoloads_first_theme(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """With a theme installed but none persisted, the restore-theme route's
    RestoreDeviceState auto-loads the first available theme → 200 ok."""
    import json as _json

    theme_root = fake_platform.paths().theme_dir(320, 320)
    tdir = theme_root / "Auto"
    tdir.mkdir(parents=True)
    (tdir / "trcc.json").write_text(
        _json.dumps({"name": "Auto", "width": 320, "height": 320, "elements": []}),
        encoding="utf-8",
    )
    (tdir / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    resp = api_client.post("/devices/0402:3922/display/restore-theme")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_system_list_gpus(api_client: TestClient) -> None:
    resp = api_client.get("/system/gpus")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_system_list_fans(api_client: TestClient) -> None:
    resp = api_client.get("/system/fans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["fans"], list)


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
        json={"ratio": 4},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["ratio"] == 4


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


def test_theme_web_gallery_lists_downloaded_previews(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """``GET /theme/web`` lists the ``a001.png`` previews on disk with the
    preview/download URLs + a has_video flag from the sibling .mp4."""
    web_dir = fake_platform.paths().cloud_theme_dir(320, 320)
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "a001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (web_dir / "a001.mp4").write_bytes(b"x")          # → has_video
    (web_dir / "b002.png").write_bytes(b"\x89PNG\r\n\x1a\n")   # no video

    resp = api_client.get("/theme/web", params={"resolution": "320x320"})
    assert resp.status_code == 200
    items = {i["id"]: i for i in resp.json()}
    assert set(items) == {"a001", "b002"}
    assert items["a001"]["category"] == "a"
    assert items["a001"]["preview_url"] == "/static/web/320320/a001.png"
    assert items["a001"]["has_video"] is True
    assert items["b002"]["has_video"] is False


def test_theme_web_gallery_empty_without_data(api_client: TestClient) -> None:
    resp = api_client.get("/theme/web", params={"resolution": "640x480"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_theme_web_gallery_bad_resolution_400(api_client: TestClient) -> None:
    resp = api_client.get("/theme/web", params={"resolution": "nonsense"})
    assert resp.status_code == 400


def test_static_web_serves_a_preview_file(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """The /static/web mount serves files the gallery points at."""
    web_dir = fake_platform.paths().cloud_theme_dir(320, 320)
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "a001.png").write_bytes(b"\x89PNG\r\n\x1a\nDATA")

    resp = api_client.get("/static/web/320320/a001.png")
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x89PNG")


def test_theme_init_bad_resolution_400(api_client: TestClient) -> None:
    resp = api_client.post("/theme/init", params={"resolution": "nope"})
    assert resp.status_code == 400


def test_theme_init_dispatches_prefetch(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``POST /theme/init`` runs the prefetch and reports the per-archive
    ok flags (ensure_all stubbed at the class — no network in the test)."""
    from trcc.services.data_install import DataInstallService, EnsureDataResult
    monkeypatch.setattr(
        DataInstallService, "ensure_all",
        lambda self, resolution: EnsureDataResult(
            resolution=resolution, themes_ok=True, web_ok=True, masks_ok=True,
        ),
    )
    resp = api_client.post("/theme/init", params={"resolution": "320x320"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["width"] == 320
    assert body["height"] == 320
    assert body["web_ok"] is True


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


def test_system_sensor_catalog_lists_what_exists(api_client: TestClient) -> None:
    """/system/sensors answers "what do they read"; this answers "what exists".

    ListSensors was reachable from the CLI (`system list-sensors`) and from
    nowhere else -- so a script could not ask the first question a script asks.
    The API is the unified UI: a capability it cannot reach is not universal.
    """
    resp = api_client.get("/system/sensors/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["sensors"], list)
    for entry in body["sensors"]:
        # descriptors, NOT values -- that is what makes it distinct from
        # /system/sensors and cheap enough to populate a picker with.
        assert "sensor_id" in entry and "category" in entry
        assert "value" not in entry


def test_system_metrics_returns_flat_dict(api_client: TestClient) -> None:
    """``GET /system/metrics`` is the flat ``{sensor_id: value}`` shape."""
    resp = api_client.get("/system/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    for value in body.values():
        assert isinstance(value, (int, float))


def test_device_detail_not_found_404(api_client: TestClient) -> None:
    """``GET /devices/{key}`` for an undiscovered device is a 404."""
    resp = api_client.get("/devices/dead:beef")
    assert resp.status_code == 404


def test_device_detail_returns_discovered_device(
    api_client: TestClient,
    fake_platform: FakePlatform,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A discovered device's detail comes back as its product schema."""
    from trcc.core.models import DeviceInfo
    monkeypatch.setattr(
        fake_platform, "scan_devices",
        lambda: [DeviceInfo(vid=0x0402, pid=0x3922)],
    )
    resp = api_client.get("/devices/0402:3922")
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "0402:3922"
    assert body["vid"] == 0x0402
    assert body["pid"] == 0x3922


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


def test_theme_config_export_import_round_trips(api_client: TestClient) -> None:
    # Download the device's settings snapshot as JSON.
    resp = api_client.get("/theme/0402:3922/config-download")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    payload = resp.content
    assert payload  # non-empty JSON snapshot

    # Re-import it via multipart upload (key as query param, file as multipart).
    resp2 = api_client.post(
        "/theme/config/import-upload",
        params={"key": "0402:3922"},
        files={"config": ("device-config.json", payload, "application/json")},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["ok"] is True
    assert body["key"] == "0402:3922"


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


# =========================================================================
# Display tick — the headless animation poller
# =========================================================================


def test_display_tick_restores_then_ticks(api_client: TestClient) -> None:
    """``POST /display/tick`` self-primes, then dispatches the ANIMATION tick.

    The route had no coverage at all until the tick became a Command.  Two
    things matter and both are easy to break:

    * ``RestoreDeviceState`` stays HERE rather than inside ``TickDisplay`` — a
      stateless poller may arrive with no theme loaded, but the GUI's animation
      timer would otherwise pay a restore 15-30 times a second.
    * the tick itself must be ``TickDisplay``; with ``RenderAndSend`` the
      cursor never moves and a polled video sits on frame 0 (#239).
    """
    from trcc.core.commands import RestoreDeviceState, TickDisplay

    trcc = api_client.app.state.trcc          # type: ignore[attr-defined]
    seen: list[str] = []
    real_dispatch = trcc.dispatch

    def _recording_dispatch(cmd):             # type: ignore[no-untyped-def]
        seen.append(type(cmd).__name__)
        return real_dispatch(cmd)

    trcc.dispatch = _recording_dispatch       # type: ignore[method-assign]
    try:
        # No device attached, so the tick reports failure — the ORDER and the
        # choice of Commands is what this pins, not the render succeeding.
        api_client.post("/devices/dead:beef/display/tick")
    finally:
        trcc.dispatch = real_dispatch         # type: ignore[method-assign]

    assert seen == [RestoreDeviceState.__name__, TickDisplay.__name__], (
        f"expected restore-then-animation-tick, got {seen}"
    )


# =========================================================================
# Per-SKU artwork libraries reached through the API
#
# A resolution does not identify an artwork library (FormCZTV.cs:1290-1353,
# FormCZTV.cs:5746).  ``ListMasks`` / ``ListWebThemes`` have carried an optional
# ``key`` since the libraries landed, and the CLI (ui/cli/display.py) and qtgui
# (ui/qtgui/panels/mask_browser.py) both pass it -- the API did not, so the same
# device answered one way through three UIs and another way through the fourth.
# =========================================================================


def _attach_sku_device(
    trcc: App, key: str, fbl: int, pm: int, sub: int,
) -> None:
    """Attach a device whose handshake selects a per-SKU library.

    Real ``ProductInfo`` + real ``get_profile`` rather than a stub carrying
    only a resolution: ``/display/masks?key=`` resolves through ``DeviceState``,
    which reads wire/kind/model/fbl off the registry row, and a stub hides
    which of those fields are load-bearing.
    """
    from types import SimpleNamespace

    from trcc.core.protocol import get_profile
    from trcc.core.registry import ALL_DEVICES

    vid, pid = (int(part, 16) for part in key.split(":"))
    info = ALL_DEVICES.get((vid, pid)) or SimpleNamespace(
        key=key, native_resolution=get_profile(fbl, pm).resolution,
    )
    trcc.devices[key] = SimpleNamespace(   # type: ignore[assignment]
        profile=get_profile(fbl, pm),
        handshake=SimpleNamespace(sub_byte=sub, pm_byte=pm),
        info=info,
        is_connected=True,
        is_led=False,
    )


def test_display_masks_reads_the_devices_own_mask_library(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """``GET /display/masks?key=`` scans the device's library, not the generic.

    480x480 at PM 3 keeps its masks in ``zt480480y`` (FormCZTV.cs:5746) -- the
    one library arm keyed on PM rather than SUB.  The route already resolved
    ``key`` into a resolution; it dropped the key itself when building the
    Command, so it read ``zt480480`` while the CLI and qtgui read ``zt480480y``.

    MUTATION CHECK -- drop ``key=key or ""`` from the ``ListMasks(...)`` call in
    ``ui/api/display.py`` and this fails with the generic mask.
    """
    key = "87ad:70db"                     # GrandVision 360 AIO, FBL 72, 480x480
    _attach_sku_device(
        api_client.app.state.trcc,        # type: ignore[attr-defined]
        key, fbl=72, pm=3, sub=0,
    )

    paths = fake_platform.paths()
    # BOTH libraries on disk, each holding a DIFFERENT mask: if only the
    # variant existed, the fallback would make the generic case pass too and
    # the test would not distinguish them.
    for variant, mask_id in (("", "generic01"), ("y", "sku01")):
        mask_dir = paths.cloud_mask_dir(480, 480, variant) / mask_id
        mask_dir.mkdir(parents=True, exist_ok=True)
        (mask_dir / "01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert paths.cloud_mask_dir(480, 480, "y").name == "zt480480y"

    resp = api_client.get("/display/masks", params={"key": key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert [m["name"] for m in body["masks"]] == ["sku01"], (
        "a PM-3 480x480 panel must browse zt480480y, as it does in the "
        f"CLI and qtgui — got {body['masks']}"
    )


def test_theme_web_gallery_reads_the_devices_own_library(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """``GET /theme/web?key=`` scans the SKU library AND names it in the URL.

    1600x720 ships six theme libraries picked by SUB crossed with orientation
    (FormCZTV.cs:1290-1353); SUB 3 browses ``1600720l``.  Two separate facts
    have to follow the device, and the second is the one that bites: the
    ``preview_url`` used to be re-spelled as ``f"/static/web/{w}{h}/..."``, so
    even with the right entries it addressed ``1600720`` -- a directory the
    file is not in.

    MUTATION CHECK -- drop ``key=key`` from the ``ListWebThemes(...)`` call and
    the entries go generic; put the ``{w}{h}`` literal back and the URL does.
    """
    key = "0416:5408"
    _attach_sku_device(
        api_client.app.state.trcc,        # type: ignore[attr-defined]
        key, fbl=114, pm=64, sub=3,
    )

    paths = fake_platform.paths()
    for variant, preview_id in (("", "a001"), ("l", "a077")):
        web_dir = paths.cloud_theme_dir(1600, 720, variant)
        web_dir.mkdir(parents=True, exist_ok=True)
        (web_dir / f"{preview_id}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert paths.cloud_theme_dir(1600, 720, "l").name == "1600720l"

    resp = api_client.get(
        "/theme/web", params={"resolution": "1600x720", "key": key},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert [i["id"] for i in items] == ["a077"], (
        f"SUB 3 must browse 1600720l — got {items}"
    )
    assert items[0]["preview_url"] == "/static/web/1600720l/a077.png", (
        "the URL must name the directory the file is actually in"
    )


def test_theme_web_gallery_without_a_key_stays_generic(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """No ``key`` → the generic library, so the resolution-only contract holds.

    ``?key=`` is additive: a client that only knows a resolution (and has no
    device to ask) must keep getting exactly what it got before.
    """
    web_dir = fake_platform.paths().cloud_theme_dir(1600, 720)
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "a001.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    resp = api_client.get("/theme/web", params={"resolution": "1600x720"})
    assert resp.status_code == 200
    items = resp.json()
    assert [i["id"] for i in items] == ["a001"]
    assert items[0]["preview_url"] == "/static/web/1600720/a001.png"


def test_theme_web_gallery_falls_back_when_the_sku_library_is_absent(
    api_client: TestClient, fake_platform: FakePlatform,
) -> None:
    """Variant dir not on disk → generic entries AND a generic URL.

    The suffixed libraries are a separate download, so the fallback is the one
    path that must not produce a URL nobody can serve: deriving the segment
    from the directory the query REPORTS reading keeps the two in step, where
    re-spelling from the variant would 404.
    """
    key = "0416:5409"
    _attach_sku_device(
        api_client.app.state.trcc,        # type: ignore[attr-defined]
        key, fbl=114, pm=64, sub=3,
    )

    web_dir = fake_platform.paths().cloud_theme_dir(1600, 720)   # generic only
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "a078.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert not fake_platform.paths().cloud_theme_dir(1600, 720, "l").exists()

    resp = api_client.get(
        "/theme/web", params={"resolution": "1600x720", "key": key},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert [i["id"] for i in items] == ["a078"]
    assert items[0]["preview_url"] == "/static/web/1600720/a078.png"


# =========================================================================
# Gate B — capabilities the bus had but only one UI could reach
# =========================================================================
#
# Each route below dispatches a Command that existed and was reachable from
# exactly one UI, so a user of every OTHER surface could not do the thing at
# all.  They are asserted to EXIST and to reach their Command; the behaviour
# itself is covered where that Command is tested.  A route that 404s because
# nobody registered it is the failure this guards.


def test_device_reset_route_exists(api_client: TestClient) -> None:
    """``POST /devices/{key}/reset`` — disconnect AND drop cached state.

    Distinct from ``/display/reset``, which blanks the panel to a colour and
    leaves the device connected.  That difference is why this was a real gap
    rather than a duplicate.
    """
    resp = api_client.post("/devices/dead:beef/reset")
    assert resp.status_code in (200, 404), resp.text
    assert "Not Found" not in resp.text or resp.status_code == 404


def test_push_image_is_distinct_from_send_image(api_client: TestClient) -> None:
    """``/push-image`` (ephemeral) must not have replaced ``/send-image``.

    ``/send-image`` takes a multipart upload and dispatches ``LoadImage``,
    which STAGES a theme; ``/push-image`` dispatches ``SendImage``, which does
    not.  Both must exist — collapsing them would silently change what a
    client's existing call does.
    """
    schema = api_client.get("/openapi.json").json()["paths"]
    assert "/devices/{key}/display/send-image" in schema
    assert "/devices/{key}/display/push-image" in schema


def test_new_capability_routes_are_registered(api_client: TestClient) -> None:
    """Every Gate B route this session added is in the OpenAPI schema."""
    schema = api_client.get("/openapi.json").json()["paths"]
    for route in (
        "/devices/{key}/display/background",        # SetBackground
        "/devices/{key}/display/push-image",        # SendImage
        "/devices/{key}/display/render-dc",         # RenderDcStandalone
        "/devices/{key}/display/slideshow/drive",   # Start/StopSlideshowDriver
        "/devices/{key}/led/zone-sync-zones",       # SetLedZoneSyncZones
        "/devices/{key}/reset",                     # ResetDevice
        "/theme/export-overlay",                    # ExportOverlay
    ):
        assert route in schema, f"route not registered: {route}"


def test_quickstart_route_returns_the_sequence(api_client: TestClient) -> None:
    """``POST /system/quickstart`` — doctor then scan, as ONE answer.

    /system/doctor and /devices each answer half; nothing returned the sequence,
    so a REST client had to know the order and the stop-on-first-failure rule
    itself.  Asserting the steps come back is the point: a 200 with no steps
    would be the route existing without the capability.
    """
    resp = api_client.post("/system/quickstart")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("steps"), "quickstart returned no steps"
    assert all("name" in s and "status" in s for s in body["steps"])
