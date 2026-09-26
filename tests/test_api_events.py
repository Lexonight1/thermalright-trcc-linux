"""``WS /events`` — the observe half the REST API never had.

The API could dispatch and it could read.  Everything a UI learns without
asking reached the Qt skins through ``BusBridge`` and the CLI through
``AppProxy.events``, and reached a REST client not at all — so a browser
dashboard had to poll, and could not see anything transient at all: a video
export publishes each progress step once and never repeats it.

What these tests hold to:

* the envelope is ``ipc.encode_event``'s, byte-identical to what the daemon's
  Unix socket sends — one wire format, not two;
* an unknown ``types=`` name is REFUSED rather than silently subscribing the
  client to a stream that will never carry anything;
* the subscription is released when the socket goes, because an EventBus
  fanning out to a closed connection leaks for the life of the process.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from trcc.app import App
from trcc.core.events import (
    BrightnessChanged,
    DeviceConnected,
    ThemeLoaded,
    VideoExportFinished,
    VideoExportProgress,
)
from trcc.ipc import EVENT_TYPES, decode_event, encode_event


@pytest.fixture
def app(fake_platform) -> App:
    return App(fake_platform)


@pytest.fixture
def client(app: App):
    from trcc.ui.api.main import build_app
    return TestClient(build_app(app))


def _drain(ws, count: int) -> list[dict]:
    """Read exactly *count* envelopes.  Blocks — the test publishes first."""
    return [json.loads(ws.receive_text()) for _ in range(count)]


# ── The envelope ─────────────────────────────────────────────────────


def test_an_event_arrives_in_the_ipc_envelope(app: App, client) -> None:
    """Same shape the Unix socket sends, so one client works against both."""
    with client.websocket_connect("/events") as ws:
        event = ThemeLoaded(key="0402:3922", theme_name="Theme1")
        app.events.publish(event)
        got = _drain(ws, 1)[0]

    assert got == encode_event(event)
    assert got["event"] == "ThemeLoaded"
    assert got["fields"] == {"key": "0402:3922", "theme_name": "Theme1"}


def test_the_envelope_decodes_back_to_the_event(app: App, client) -> None:
    """Round trip, not just the write half.

    ``decode_event`` is what an ``AppProxy`` client already uses on the
    socket; a WS envelope it could not rebuild would mean the two wire
    formats had quietly diverged.
    """
    with client.websocket_connect("/events") as ws:
        app.events.publish(BrightnessChanged(key="0402:3922", percent=70))
        got = _drain(ws, 1)[0]

    rebuilt = decode_event(got)
    assert isinstance(rebuilt, BrightnessChanged)
    assert rebuilt.key == "0402:3922"
    assert rebuilt.percent == 70


def test_events_arrive_in_publish_order(app: App, client) -> None:
    """A progress stream read out of order is worse than none."""
    with client.websocket_connect("/events?types=VideoExportProgress") as ws:
        for pct in (0, 20, 85, 100):
            app.events.publish(
                VideoExportProgress(token="t", percent=pct, message=f"{pct}%"),
            )
        got = _drain(ws, 4)

    assert [e["fields"]["percent"] for e in got] == [0, 20, 85, 100]


def test_a_tuple_field_survives_as_a_json_array(app: App, client) -> None:
    """``resolution`` is a tuple, and JSON has no tuples.

    Worth pinning: ``decode_event`` coerces it back from its type hint, so a
    silent change to a list here would break the socket client too.
    """
    with client.websocket_connect("/events") as ws:
        app.events.publish(DeviceConnected(key="0402:3922", resolution=(320, 320)))
        got = _drain(ws, 1)[0]

    assert got["fields"]["resolution"] == [320, 320]
    assert decode_event(got).resolution == (320, 320)


# ── The types filter ─────────────────────────────────────────────────


def test_a_filter_delivers_only_what_was_asked_for(app: App, client) -> None:
    with client.websocket_connect("/events?types=ThemeLoaded") as ws:
        app.events.publish(BrightnessChanged(key="k", percent=1))
        app.events.publish(ThemeLoaded(key="k", theme_name="wanted"))
        got = _drain(ws, 1)[0]

    assert got["event"] == "ThemeLoaded"
    assert got["fields"]["theme_name"] == "wanted"


def test_a_filter_accepts_several_names(app: App, client) -> None:
    types = "VideoExportProgress,VideoExportFinished"
    with client.websocket_connect(f"/events?types={types}") as ws:
        app.events.publish(ThemeLoaded(key="k", theme_name="ignored"))
        app.events.publish(VideoExportProgress(token="t", percent=5, message="x"))
        app.events.publish(VideoExportFinished(token="t", ok=True, path="/a.zt"))
        got = _drain(ws, 2)

    assert [e["event"] for e in got] == [
        "VideoExportProgress", "VideoExportFinished",
    ]


@pytest.mark.parametrize("spec", ["*", ""])
def test_the_default_and_star_both_mean_everything(
    app: App, client, spec: str,
) -> None:
    with client.websocket_connect(f"/events?types={spec}") as ws:
        app.events.publish(ThemeLoaded(key="k", theme_name="t"))
        assert _drain(ws, 1)[0]["event"] == "ThemeLoaded"


def test_an_unknown_type_is_refused_not_silently_ignored(client) -> None:
    """Subscribing a typo to silence makes a client wait forever.

    ``IPCServer._handle_subscribe`` refuses the same input for the same
    reason; this is the WebSocket half of that ruling.
    """
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo, \
            client.websocket_connect("/events?types=ThemeLodaed"):
        pass
    assert excinfo.value.code == 1008


def test_one_bad_name_refuses_the_whole_subscription(client) -> None:
    """Partial acceptance would be worse: the client cannot tell which half
    it got, and would wait forever for the half it did not."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), \
            client.websocket_connect("/events?types=ThemeLoaded,Nonsense"):
        pass


def test_every_event_in_the_tree_is_subscribable(client) -> None:
    """The filter is derived from ``EVENT_TYPES``, so a new Event needs no
    change here — this asserts that stays true."""
    names = ",".join(sorted(EVENT_TYPES))
    with client.websocket_connect(f"/events?types={names}"):
        pass


# ── Auth ─────────────────────────────────────────────────────────────


def test_a_wrong_token_is_rejected(client) -> None:
    """Browsers cannot set a header on a WS handshake, so the token rides
    the query string — same posture as ``/preview/stream``."""
    from starlette.websockets import WebSocketDisconnect

    from trcc.ui.api.main import configure_auth
    configure_auth("s3cret")
    try:
        with pytest.raises(WebSocketDisconnect) as excinfo, \
                client.websocket_connect("/events?token=wrong"):
            pass
        assert excinfo.value.code == 1008
    finally:
        configure_auth(None)


def test_the_right_token_is_accepted(app: App, client) -> None:
    from trcc.ui.api.main import configure_auth
    configure_auth("s3cret")
    try:
        with client.websocket_connect("/events?token=s3cret") as ws:
            app.events.publish(ThemeLoaded(key="k", theme_name="t"))
            assert _drain(ws, 1)[0]["event"] == "ThemeLoaded"
    finally:
        configure_auth(None)


# ── Lifecycle ────────────────────────────────────────────────────────


def test_the_subscription_is_released_when_the_socket_closes(
    app: App, client,
) -> None:
    """An EventBus fanning out to a dead socket leaks for the process's life.

    Exactly the bug that made ``AppProxy.close`` leak a reader thread per
    window, which surfaced as an intermittent cross-test flake rather than
    as anything obviously wrong.
    """
    before = len(app.events._handlers[ThemeLoaded])
    with client.websocket_connect("/events") as ws:
        app.events.publish(ThemeLoaded(key="k", theme_name="t"))
        _drain(ws, 1)
        assert len(app.events._handlers[ThemeLoaded]) == before + 1

    # Publishing after the close must reach nobody new and must not raise.
    app.events.publish(ThemeLoaded(key="k", theme_name="after"))
    assert len(app.events._handlers[ThemeLoaded]) == before


def test_two_clients_each_get_every_event(app: App, client) -> None:
    """Fan-out, not hand-off — one daemon serves many watchers."""
    with client.websocket_connect("/events?types=ThemeLoaded") as a, \
            client.websocket_connect("/events?types=ThemeLoaded") as b:
        app.events.publish(ThemeLoaded(key="k", theme_name="both"))
        assert _drain(a, 1)[0]["fields"]["theme_name"] == "both"
        assert _drain(b, 1)[0]["fields"]["theme_name"] == "both"


def test_a_disconnect_does_not_disturb_the_other_client(
    app: App, client,
) -> None:
    with client.websocket_connect("/events?types=ThemeLoaded") as survivor:
        with client.websocket_connect("/events?types=ThemeLoaded"):
            pass
        app.events.publish(ThemeLoaded(key="k", theme_name="still here"))
        assert _drain(survivor, 1)[0]["fields"]["theme_name"] == "still here"


def test_the_route_is_mounted_on_the_app(client) -> None:
    """A router nobody included is a feature nobody has."""
    from trcc.ui.api.main import api_routes

    paths = {getattr(r, "path", "") for r in api_routes(client.app)}
    assert "/events" in paths
