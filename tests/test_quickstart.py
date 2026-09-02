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


def test_reset_cycles_the_device_and_restores_its_display(tmp_path) -> None:
    """Reset is a POWER-CYCLE: disconnect, reconnect, put the display back.

    It used to be ``DisconnectDevice`` byte for byte — same guard, same
    ``app.detach``, same event, only the message differed — so ``trcc device
    reset`` on a stuck panel left it dark AND disconnected, and the docstring
    told the user to run ``connect`` themselves.

    Two things must hold and BOTH are asserted, because asserting only the
    first passes with the restore deleted: the device is ATTACHED again, and
    its persisted theme is ACTIVE again.  ``app.detach`` pops
    ``active_themes``, so a theme present afterwards can only have been put
    back by the restore step.
    """
    import json

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import ConnectDevice, ResetDevice

    from .mock_platform import MockPlatform

    key = "0402:3922"
    app = App(MockPlatform(
        [{"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100}], tmp_path,
    ), renderer=QtRenderer())
    app.attach(0x0402, 0x3922)
    assert app.dispatch(ConnectDevice(key=key)).ok

    theme_dir = tmp_path / "persisted"
    theme_dir.mkdir(parents=True)
    (theme_dir / "trcc.json").write_text(json.dumps(
        {"name": "persisted", "width": 320, "height": 320, "elements": []},
    ), encoding="utf-8")
    (theme_dir / "00.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    app.settings.set_current_theme(key, str(theme_dir.resolve()))

    result = app.dispatch(ResetDevice(key=key))

    assert result.ok, result.message
    assert key in app.devices, (
        "reset left the device detached — that is a disconnect, not a reset"
    )
    assert app.devices[key].is_connected
    assert key in app.active_themes, (
        "the device came back with no theme — detach pops active_themes, so "
        "the restore step is the only thing that can put it back"
    )
    assert app.active_themes[key].name == "persisted"
    assert "restored" in result.message


def test_reset_reports_failure_when_the_device_does_not_come_back(
    _trcc_app, fake_platform,
) -> None:
    """A device that cannot reconnect must NOT report a successful reset.

    The old command could not fail this way — it only detached — so a reset
    that left nothing attached still said ok.  Driven with a stub device that
    ``FakePlatform`` cannot re-attach, which is exactly that case.
    """
    from trcc.core.commands import ResetDevice
    from trcc.core.models import Kind, ProductInfo, Wire

    info = ProductInfo(
        vid=0xdead, pid=0xbeef,
        vendor="Test", product="Stub",
        wire=Wire.BULK, kind=Kind.LCD,
        native_resolution=(320, 320),
        orientations=(0, 90, 180, 270),
    )

    class _StubDevice:
        def __init__(self, info: ProductInfo) -> None:
            self.info = info
            self.is_connected = False
            self.key = info.key

        def disconnect(self) -> None:
            self.is_connected = False

    _trcc_app.devices[info.key] = _StubDevice(info)  # type: ignore[assignment]

    result = _trcc_app.dispatch(ResetDevice(key="dead:beef"))

    assert result.ok is False
    assert "did not come back" in result.message
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
