"""Commands — UI contract dispatched through App.dispatch."""
from __future__ import annotations

from trcc.app import App
from trcc.core.commands import (
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    GetPlatformInfo,
    ReadSensors,
)


def test_read_sensors_returns_merged_descriptors_and_live_values(fake_platform) -> None:
    """ReadSensors must enrich discover() with read_all() values."""
    app = App(fake_platform)
    result = app.dispatch(ReadSensors())

    assert result.ok is True
    assert result.readings, "at least some readings expected"

    by_id = {r.sensor_id: r for r in result.readings}
    # CPU temp from the Fake CPU source
    assert by_id["cpu:temp"].value == 42.0
    # GPU temp from the Fake NVIDIA GPU
    assert by_id["gpu:primary:temp"].value == 55.0


def test_get_platform_info_returns_fake_platform_fields(fake_platform) -> None:
    app = App(fake_platform)
    r = app.dispatch(GetPlatformInfo())

    assert r.ok is True
    assert r.distro_name == "Fake Linux"
    assert r.install_method == "test"
    # Paths all derive from the FakePaths root
    assert r.config_dir.endswith(str(fake_platform.paths().config_dir()))


def test_autostart_enable_then_status_reports_enabled(fake_platform) -> None:
    app = App(fake_platform)
    # Baseline
    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is False

    app.dispatch(EnableAutostart())
    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is True


def test_autostart_disable_clears_state(fake_platform) -> None:
    app = App(fake_platform)
    app.dispatch(EnableAutostart())

    app.dispatch(DisableAutostart())

    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is False
