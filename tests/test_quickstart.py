"""Quickstart guided flow + ResetDevice + new-user happy-path tests."""
from __future__ import annotations

import pytest

from trcc.app import App


@pytest.fixture
def _trcc_app(fake_platform):
    return App(fake_platform)


def test_quickstart_runs_full_sequence(_trcc_app) -> None:
    """No devices on FakePlatform → quickstart finishes at scan with WARN."""
    from trcc.core.commands import RunQuickstart

    result = _trcc_app.dispatch(RunQuickstart())
    # Both steps recorded
    step_names = [s.name for s in result.steps]
    assert "doctor" in step_names
    assert "scan" in step_names
    # No devices → not "completed_ok" in the strict sense
    assert result.completed_ok is False
    # But no FAIL — overall ok
    assert result.ok is True


def test_quickstart_every_step_has_hint_when_not_ok(_trcc_app) -> None:
    """Non-OK steps always carry an actionable hint."""
    from trcc.core.commands import RunQuickstart

    result = _trcc_app.dispatch(RunQuickstart())
    for step in result.steps:
        if step.status != "ok":
            assert step.next_step_hint, (
                f"Step {step.name!r} ({step.status}) has no fix hint — "
                "users won't know what to do."
            )


def test_reset_device_when_attached(_trcc_app, fake_platform) -> None:
    """ResetDevice after a successful attach clears state + reports ok."""
    from trcc.core.commands import ResetDevice

    # Wire up a device manually via the App's machinery — FakePlatform
    # has no real handshake but attach()/detach() round-trip cleanly.
    from trcc.core.models import Kind, ProductInfo, Wire
    info = ProductInfo(
        vid=0xdead, pid=0xbeef,
        vendor="Test", product="Stub",
        wire=Wire.BULK, kind=Kind.LCD,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )
    # Inject a fake device into app.devices directly so we can test the
    # detach + state-clear path without a real handshake.
    class _StubDevice:
        def __init__(self, info: ProductInfo) -> None:
            self.info = info
            self.is_connected = False
            self.key = info.key
        def disconnect(self) -> None:
            self.is_connected = False
    _trcc_app.devices[info.key] = _StubDevice(info)  # type: ignore[assignment]

    result = _trcc_app.dispatch(ResetDevice(key="dead:beef"))
    assert result.ok is True
    assert "dead:beef" in result.message
    assert "dead:beef" not in _trcc_app.devices


def test_reset_device_when_not_attached(_trcc_app) -> None:
    """ResetDevice for an unknown key returns structured error, not crash."""
    from trcc.core.commands import ResetDevice

    result = _trcc_app.dispatch(ResetDevice(key="dead:beef"))
    assert result.ok is False
    assert "not attached" in result.message.lower()


def test_quickstart_returns_actionable_steps_for_each_path(_trcc_app) -> None:
    """Doctor step always renders; scan step always renders.

    Real users see this — every line must be human-readable.
    """
    from trcc.core.commands import RunQuickstart

    result = _trcc_app.dispatch(RunQuickstart())
    for step in result.steps:
        # Every step has a non-empty message
        assert step.message
        # Status is from the documented set
        assert step.status in ("ok", "warn", "fail", "skipped")


def test_quickstart_cli_command_registered(cli_runner, cli_app) -> None:
    """``trcc quickstart --help`` lists the right options."""
    del cli_app
    from trcc.ui.cli.main import app
    result = cli_runner.invoke(app, ["quickstart", "--help"])
    assert result.exit_code == 0
    assert "Guided first-session flow" in result.output
    assert "--yes" in result.output
