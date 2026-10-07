"""Bus-pure device-connection-issue model + the DeviceConnectionIssues query.

A connect failure is recorded in the App model and read back by ANY UI via
the ``DeviceConnectionIssues`` command — the bus-pure way to learn a failure
that fired before the UI subscribed.  Same query for cli/api/gui/qtgui.
"""
from __future__ import annotations

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, DeviceConnectionIssues
from trcc.core.results import ConnectResult
from trcc.ui._errors import format_device_error


def test_note_clear_and_query(fake_platform) -> None:
    app = App(fake_platform)
    app.note_connect_issue(
        ConnectResult(ok=False, key="0402:3922", message="boom", hints=["h"]))
    assert [i.key for i in app.connection_issues()] == ["0402:3922"]
    app.clear_connect_issue("0402:3922")
    assert app.connection_issues() == []


def test_detach_clears_issue(fake_platform) -> None:
    app = App(fake_platform)
    app.note_connect_issue(ConnectResult(ok=False, key="dead:beef", message="x"))
    app.detach("dead:beef")
    assert app.connection_issues() == []


def test_connect_failure_recorded_and_queryable(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: ["NEEDS-X"])

    # Unknown key → attach raises DeviceNotFoundError → recorded.
    app.dispatch(ConnectDevice(key="dead:beef"))

    result = app.dispatch(DeviceConnectionIssues())
    assert result.ok is True
    assert len(result.issues) == 1
    assert result.issues[0].key == "dead:beef"
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
    from trcc.core.events import ErrorOccurred

    app = App(fake_platform)
    _no_scsi_node(app, monkeypatch)
    errors: list[str] = []
    app.events.subscribe(ErrorOccurred, lambda e: errors.append(e.message))

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
