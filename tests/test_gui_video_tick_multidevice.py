"""The multi-display gate inside the gui skin's video tick.

Every ``LCDHandler`` shares ONE preview/progress widget set, so only the
handler that currently owns the panel may write to it — that is what
``ui_active`` gates.  It deliberately does NOT gate the tick itself:
``set_inactive`` keeps the per-device animation timer running "so the LCD keeps
showing its theme while another device owns the GUI panel".

Gate the wrong thing and every LCD you are not looking at freezes on screen
while the focused one plays.  That invariant had no test at all — it was
verified only by reading — which is why it is pinned here.

``LCDHandler`` takes its widget dict and its timer factory as constructor args,
so two handlers can share one fake widget set exactly as production does and
``_on_video_tick`` can be driven directly: no QApplication, no real device.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

_KEY_A = "0402:3922"      # the background device
_KEY_B = "87ad:70db"      # the device that owns the panel


class _FakePreview:
    """The SHARED preview/progress widget set."""

    def __init__(self) -> None:
        self.progress_calls: list[tuple] = []

    def set_progress(self, *args: Any) -> None:
        self.progress_calls.append(args)

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


class _FakeWidget:
    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


class _Widgets(dict):
    """Auto-vivifying widget dict — handlers touch more than the preview."""

    def __missing__(self, key: str) -> Any:
        self[key] = _FakeWidget()
        return self[key]


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = 0
        self._active = False

    def start(self, ms: int) -> None:
        self._active = True

    def stop(self) -> None:
        self.stopped += 1
        self._active = False

    def isActive(self) -> bool:      # Qt API shape, not PEP 8's call
        return self._active

    def __getattr__(self, name: str) -> Any:
        def _noop(*a: Any, **k: Any) -> None:
            return None
        return _noop


class _FakePlayback:
    def __init__(self, frames: int = 30, fps: int = 15) -> None:
        self.frames = [b"x"] * frames
        self.fps = fps
        self.cursor = 0
        self.paused = False
        self.advanced = 0

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def advance(self) -> None:
        self.advanced += 1
        self.cursor = (self.cursor + 1) % len(self.frames)


class _FakeDevice:
    def __init__(self, key: str, connected: bool = True) -> None:
        self.info = type("_I", (), {"key": key})()
        self.is_connected = connected
        self.profile = None


class _FakeApp:
    """Answers TickDisplay the way the real Command does."""

    def __init__(self) -> None:
        self.devices: dict[str, _FakeDevice] = {}
        self._playbacks: dict[str, _FakePlayback] = {}
        self.dispatched: list[tuple[str, str]] = []

    @property
    def media(self) -> Any:
        outer = self

        class _Media:
            def playback(self, key: str) -> Any:
                return outer._playbacks.get(key)

        return _Media()

    def dispatch(self, cmd: Any) -> Any:
        name = type(cmd).__name__
        key = getattr(cmd, "key", "")
        self.dispatched.append((name, key))
        playback = self._playbacks.get(key)
        cursor = frame_count = interval_ms = None
        if name == "TickDisplay" and playback is not None and playback.frames:
            playback.advance()
            cursor = playback.cursor
            frame_count = playback.frame_count
            interval_ms = max(1, int(1000 / playback.fps))
        device = self.devices.get(key)
        ok = device is not None and device.is_connected
        return type("_R", (), {
            "ok": ok, "bytes_sent": 1234, "theme_name": "T",
            "message": "ok" if ok else "not connected",
            "cursor": cursor, "frame_count": frame_count,
            "interval_ms": interval_ms,
        })()


@pytest.fixture
def two_handlers(tmp_path: Path) -> tuple[Any, Any, _FakeApp, _FakePreview]:
    """Two LCDHandlers sharing one widget set — B active, A in the background."""
    from trcc.ui.gui.lcd_handler import LCDHandler

    preview = _FakePreview()
    widgets = _Widgets({"preview": preview})
    app = _FakeApp()
    for key in (_KEY_A, _KEY_B):
        app.devices[key] = _FakeDevice(key)
        app._playbacks[key] = _FakePlayback()

    def make_timer(callback: Any, *a: Any, **k: Any) -> _FakeTimer:
        return _FakeTimer()

    handler_a = LCDHandler(app.devices[_KEY_A], widgets, make_timer, tmp_path,
                           app=app, lcd_idx=_KEY_A)
    handler_b = LCDHandler(app.devices[_KEY_B], widgets, make_timer, tmp_path,
                           app=app, lcd_idx=_KEY_B)
    handler_b._pm.ui_active = True
    handler_a._pm.ui_active = False
    return handler_a, handler_b, app, preview


def test_background_device_still_ticks(two_handlers: Any) -> None:
    """THE INVARIANT: an unfocused device keeps rendering, or its LCD freezes.

    If the dispatch is ever put behind ``ui_active``, every LCD except the one
    on screen stops updating — the exact regression this change could cause.
    """
    handler_a, _, app, _ = two_handlers

    handler_a._on_video_tick()

    assert app._playbacks[_KEY_A].advanced == 1, "background video must advance"
    assert ("TickDisplay", _KEY_A) in app.dispatched, (
        "background device must still render — do not gate the dispatch"
    )


def test_background_device_never_writes_the_shared_progress_widget(
    two_handlers: Any,
) -> None:
    """...but it must not touch the widget set another device owns."""
    handler_a, _, _, preview = two_handlers

    handler_a._on_video_tick()

    assert preview.progress_calls == [], (
        "an inactive handler wrote the shared progress widget"
    )


def test_active_device_does_write_the_shared_progress_widget(
    two_handlers: Any,
) -> None:
    """The gate lets exactly one handler through — the one owning the panel."""
    _, handler_b, app, preview = two_handlers

    handler_b._on_video_tick()

    assert len(preview.progress_calls) == 1
    _percent, cursor, total = preview.progress_calls[0]
    assert (cursor, total) == (app._playbacks[_KEY_B].cursor, 30)


def test_both_devices_tick_but_only_the_active_one_draws(
    two_handlers: Any,
) -> None:
    """The combined shape, which is what a user actually sees on two panels."""
    handler_a, handler_b, app, preview = two_handlers

    handler_a._on_video_tick()
    handler_b._on_video_tick()

    assert app._playbacks[_KEY_A].advanced == 1
    assert app._playbacks[_KEY_B].advanced == 1
    assert len(preview.progress_calls) == 1, "only the active device may draw"


def test_cleared_playback_stops_the_animation_timer(two_handlers: Any) -> None:
    """No playback → the Result's video fields are None → stop ticking.

    ``frame_count is None`` is how a UI tells "not a video" from "frame 0 of a
    video"; the handler uses that transition to stop its own timer.
    """
    handler_a, _, app, _ = two_handlers
    # A tick only fires while the timer runs, and _stop_animation_timer is
    # idempotent (early-returns when already stopped), so start it first.
    handler_a._start_animation_timer(33, reason="test")
    app._playbacks.pop(_KEY_A)

    handler_a._on_video_tick()

    assert handler_a._animation_timer.stopped == 1


def test_disconnected_device_still_advances_its_cursor(
    two_handlers: Any,
) -> None:
    """Advance happens BEFORE the connected-check, as it always did.

    An unplugged device's video keeps running so it resumes in sync rather than
    frozen where it dropped — preserved deliberately when the advance moved
    into the Command.
    """
    handler_a, _, app, _ = two_handlers
    app.devices[_KEY_A].is_connected = False

    handler_a._on_video_tick()

    assert app._playbacks[_KEY_A].advanced == 1
