"""A device-connect failure carries the per-OS hint to BOTH channels.

The reason a connect failed (e.g. "run as administrator") is sourced from
the DI'd ``platform.check_permissions()`` and rides out on the sync
``ConnectResult`` AND the ``ErrorOccurred`` event — so every UI can show
*why* the panel stayed blank instead of failing silently.
"""
from __future__ import annotations

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.events import ErrorOccurred
from trcc.core.results import ConnectResult


def test_models_default_to_empty_hints() -> None:
    assert ErrorOccurred(message="x").hints == []
    assert ConnectResult(ok=False, key="k", message="m").hints == []


def test_connect_failure_carries_platform_hints(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions",
                        lambda: ["NEEDS-ELEVATION"])

    captured: list[ErrorOccurred] = []
    app.events.subscribe(ErrorOccurred, captured.append)

    # An unknown key → app.attach raises DeviceNotFoundError → the failure path.
    result = app.dispatch(ConnectDevice(key="dead:beef"))

    assert result.ok is False
    assert result.hints == ["NEEDS-ELEVATION"]          # sync channel
    assert captured and captured[0].hints == ["NEEDS-ELEVATION"]  # event channel


def test_connect_failure_hints_empty_when_platform_clean(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: [])

    result = app.dispatch(ConnectDevice(key="dead:beef"))

    assert result.ok is False
    assert result.hints == []   # nothing to add → raw message stands


# ── a SUSPENDED panel is not a broken one (#150) ──────────────────────

def _suspended(ms: int = 4200):
    from trcc.core.models import UsbPowerState
    return UsbPowerState(control="auto", runtime_status="suspended",
                         autosuspend_delay_ms=10000, suspended_time_ms=ms,
                         supports_remote_wakeup=True)


def _awake():
    from trcc.core.models import UsbPowerState
    return UsbPowerState(control="on", runtime_status="active",
                         supports_remote_wakeup=False)


def _failing_handshake(app, monkeypatch):
    """Make attach() succeed and connect() time out — armangido's exact shape."""
    from trcc.core.errors import HandshakeError
    from trcc.core.models import DeviceQuirks

    class _Asleep:
        # Stands in for a Device, so it carries what the connect path reads off
        # one: ``quirks`` decides whether a failed handshake earns a retry on a
        # firmware's overriding transport.  Empty here — this panel is merely
        # asleep, not quirked.
        quirks = DeviceQuirks()

        def connect(self):
            raise HandshakeError("USB read failed: [Errno 110] Operation timed out")

    monkeypatch.setattr(app, "attach", lambda vid, pid, **kw: _Asleep())
    monkeypatch.setattr(app, "detach", lambda key: None)
    monkeypatch.setattr(app, "note_connect_issue", lambda result: None)


def test_suspended_panel_explains_itself_instead_of_a_bare_timeout(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[Errno 110] on a sleeping panel is indistinguishable from a dead one.

    #150: the kernel autosuspends the panel ~10s after the frame stream stops
    (by design, #143), and the next connect times out looking like hardware
    failure. The app knew the power state all along and never said.
    """
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: [])
    monkeypatch.setattr(app.platform, "usb_power_state",
                        lambda vid, pid: _suspended())
    _failing_handshake(app, monkeypatch)

    result = app.dispatch(ConnectDevice(key="0416:5302"))

    assert result.ok is False
    assert "Errno 110" in result.message          # the raw truth still stands
    joined = " ".join(result.hints)
    assert "USB-suspended" in joined and "not broken" in joined
    assert "4200ms" in joined                     # resolved value, not a guess
    assert "trcc display play" in joined          # the actual way out


def test_an_awake_panel_gets_no_suspend_hint(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely broken device must not be excused as 'just asleep'."""
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: [])
    monkeypatch.setattr(app.platform, "usb_power_state", lambda vid, pid: _awake())
    _failing_handshake(app, monkeypatch)

    result = app.dispatch(ConnectDevice(key="0416:5302"))

    assert result.ok is False
    assert result.hints == []


def test_diagnosis_never_breaks_the_diagnostic(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If reading power state raises, we lose the hint — never the error."""
    def _boom(vid, pid):
        raise OSError("sysfs went away")

    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: [])
    monkeypatch.setattr(app.platform, "usb_power_state", _boom)
    _failing_handshake(app, monkeypatch)

    result = app.dispatch(ConnectDevice(key="0416:5302"))

    assert result.ok is False
    assert "Errno 110" in result.message
    assert result.hints == []


def test_platforms_without_usb_power_are_silent(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows/macOS/BSD return None — no hint, no invented claim."""
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: [])
    monkeypatch.setattr(app.platform, "usb_power_state", lambda vid, pid: None)
    _failing_handshake(app, monkeypatch)

    assert app.dispatch(ConnectDevice(key="0416:5302")).hints == []
