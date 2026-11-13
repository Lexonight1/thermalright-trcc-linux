"""Bus-pure device-connection-issue model + the DeviceConnectionIssues query.

A connect failure is recorded in the App model and read back by ANY UI via
the ``DeviceConnectionIssues`` command — the bus-pure way to learn a failure
that fired before the UI subscribed.  Same query for cli/api/gui/qtgui.
"""
from __future__ import annotations

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, DeviceConnectionIssues
from trcc.core.errors import DeviceNotFoundError
from trcc.core.results import ConnectResult
from trcc.ui._errors import format_device_error


def test_note_clear_and_query(fake_platform) -> None:
    app = App(fake_platform)
    app.note_connect_issue(
        ConnectResult(ok=False, key="0402:3922", message="boom", hints=["h"]))
    assert [i.key for i in app.connection_issues()] == ["0402:3922"]
    app.clear_connect_issue("0402:3922")
    assert app.connection_issues() == []


def _not_found(*_args: object, **_kwargs: object) -> None:
    raise DeviceNotFoundError("no such product")


def test_detach_clears_issue(fake_platform) -> None:
    app = App(fake_platform)
    app.note_connect_issue(ConnectResult(ok=False, key="87cd:70db", message="x"))
    app.detach("87cd:70db")
    assert app.connection_issues() == []


def test_connect_failure_recorded_and_queryable(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: ["NEEDS-X"])

    # attach raises DeviceNotFoundError → recorded.  An unknown key no longer
    # reaches attach (Settings refuses it first), so force it.
    monkeypatch.setattr(app, "attach", _not_found)
    app.dispatch(ConnectDevice(key="87cd:70db"))

    result = app.dispatch(DeviceConnectionIssues())
    assert result.ok is True
    assert len(result.issues) == 1
    assert result.issues[0].key == "87cd:70db"
    assert result.issues[0].hints == ["NEEDS-X"]


def test_query_empty_when_no_failures(fake_platform) -> None:
    app = App(fake_platform)
    assert app.dispatch(DeviceConnectionIssues()).issues == []


def test_format_device_error_joins_message_and_hints() -> None:
    r = ConnectResult(ok=False, key="k", message="boom", hints=["do x", "do y"])
    s = format_device_error(r)
    assert "boom" in s and "do x" in s and "do y" in s
    # No hints → just the message, no trailing newline noise.
    assert format_device_error(ConnectResult(ok=False, message="m")) == "m"


_NO_NODE = ("No SCSI device node found for 0402:3922 — check that the device "
            "is attached and the scsi_generic kernel module is loaded")


def _no_scsi_node(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    """``LinuxOS._open_scsi`` with no /dev/sgN: a usb add ~1 s early, or a VM."""
    from trcc.core.errors import TransportError

    def no_node(*_args: object, **_kwargs: object) -> object:
        raise TransportError(_NO_NODE)

    monkeypatch.setattr(app.platform, "open_transport", no_node)


def test_a_missing_scsi_node_fails_the_connect_instead_of_raising(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION CHECK: drop ``TransportError`` from the attach clause and the
    dispatch raises — no result, no recorded issue, nothing for a UI."""
    from trcc.core.events import ErrorOccurred, Event

    app = App(fake_platform)
    _no_scsi_node(app, monkeypatch)
    errors: list[str] = []

    def on_error(e: Event) -> None:
        assert isinstance(e, ErrorOccurred)
        errors.append(e.message)
    app.events.subscribe(ErrorOccurred, on_error)

    result = app.dispatch(ConnectDevice(key="0402:3922"))

    assert (result.ok, result.message) == (False, _NO_NODE)
    assert [i.message for i in app.connection_issues()] == [_NO_NODE]
    assert errors == [_NO_NODE]
    assert "0402:3922" not in app.devices


def test_a_command_that_connects_first_reports_a_missing_node(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """``App._connect_for`` runs ``EnsureConnected`` before every
    ``USES_DEVICE`` Command — the escape took those down with it."""
    from trcc.core.commands import SetBackground

    app = App(fake_platform)
    _no_scsi_node(app, monkeypatch)

    result = app.dispatch(SetBackground(key="0402:3922",
                                        path=tmp_path / "bg.png"))

    assert result.ok is False
    assert [i.message for i in app.connection_issues()] == [_NO_NODE]


#: PyUsbBulkTransport.read on a timeout: "USB read failed: {pyusb error}".
_TIMED_OUT = "USB read failed: [Errno 110] Operation timed out"


def test_a_failed_handshake_says_why_the_last_attempt_failed(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#330: the window showed "HidLcd handshake failed after 3 attempts" and
    nothing else -- the reason reached only the log.  Every UI shows the
    Result's message, so the reason belongs in it.  MUTATION CHECK: drop
    ``: {reason}`` from ``_handshake_retry``'s raise."""
    from trcc.adapters.device import _base
    from trcc.core.errors import TransportError

    from .conftest import FakeBulkTransport

    class Silent(FakeBulkTransport):
        def read(self, endpoint: int, length: int, timeout_ms: int = 100) -> bytes:
            raise TransportError(_TIMED_OUT)

    monkeypatch.setattr(_base.time, "sleep", lambda _s: None)
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "open_transport",
                        lambda *_a, **_k: Silent())

    result = app.dispatch(ConnectDevice(key="0416:5406"))

    said = f"HidLcd handshake failed after 3 attempts: {_TIMED_OUT}"
    assert (result.ok, result.message) == (False, said)
    assert [i.message for i in app.dispatch(DeviceConnectionIssues()).issues] == [said]
