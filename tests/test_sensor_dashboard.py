"""The sensor dashboard on the bus — ``GetSensorDashboard`` / ``SetSensorDashboard``.

Until these existed the layout at ``<config_dir>/system_config.json`` was
readable by exactly one UI: the legacy GUI imported the persistence adapter
directly, so cli / api / qtgui could not see the file at all.

What is pinned here is the part a review cannot see by reading the two
classes: that the Query really is a read (it must not create the file), that
the Result is a COPY (a widget must not be able to rebind a row by assignment
and skip the bus), and that both survive the daemon socket — because in-process
they would pass by sharing objects, which is exactly the bug.
"""
from __future__ import annotations

from trcc.app import App
from trcc.core.commands import GetSensorDashboard, SetSensorDashboard
from trcc.core.models import PanelConfig, SensorBinding
from trcc.ipc import (
    decode_command,
    decode_result,
    encode_command,
    encode_result,
)


def test_get_returns_the_six_default_panels(fake_platform) -> None:
    result = App(fake_platform).dispatch(GetSensorDashboard())
    assert result.ok
    assert [p.name for p in result.panels] == [
        "CPU", "GPU", "Memory", "HDD", "Network", "Fan",
    ]
    assert all(len(p.sensors) == 4 for p in result.panels)


def test_get_does_not_write_the_file(fake_platform) -> None:
    """A Query answers and changes nothing — including on disk.

    The GUI used to ``load(); auto_map(); save()`` at panel construction,
    which froze the auto-map at whatever the very first run happened to see:
    a pump idle at that moment stayed mis-bound forever.  The read now
    re-derives every time and only :class:`SetSensorDashboard` writes.
    """
    app = App(fake_platform)
    app.dispatch(GetSensorDashboard())
    assert not app.sysinfo.path.exists(), (
        f"GetSensorDashboard wrote {app.sysinfo.path} — it is a Query"
    )


def test_result_panels_are_copies_not_app_state(fake_platform) -> None:
    """Writing through the Result must change nothing.

    If the Result handed out ``app.sysinfo.panels`` itself, a widget could
    rebind a row by assignment and never dispatch — the exact bypass this
    pair of Commands exists to remove.  It would also work ONLY in-process:
    over the socket the caller gets objects rebuilt from JSON.
    """
    app = App(fake_platform)
    result = app.dispatch(GetSensorDashboard())
    before = app.sysinfo.panels[0].sensors[0].sensor_id

    result.panels[0].name = "hijacked"
    result.panels[0].sensors[0].sensor_id = "hijacked"

    assert app.sysinfo.panels[0].name == "CPU"
    assert app.sysinfo.panels[0].sensors[0].sensor_id == before


def test_set_persists_and_survives_a_restart(fake_platform) -> None:
    app = App(fake_platform)
    panels = app.dispatch(GetSensorDashboard()).panels
    panels[0].name = "My Cooler"
    panels[0].sensors[0] = SensorBinding("TEMP", "cpu:temp", "°C")

    written = app.dispatch(SetSensorDashboard(panels=tuple(panels)))
    assert written.ok
    assert app.sysinfo.path.exists()

    reread = App(fake_platform).dispatch(GetSensorDashboard())
    assert reread.panels[0].name == "My Cooler"
    assert reread.panels[0].sensors[0].sensor_id == "cpu:temp"


def test_set_stores_a_copy_of_the_commands_panels(fake_platform) -> None:
    """The saved layout must not alias the Command's own tuple."""
    app = App(fake_platform)
    panels = app.dispatch(GetSensorDashboard()).panels
    app.dispatch(SetSensorDashboard(panels=tuple(panels)))

    panels[0].name = "changed after the dispatch"
    assert app.sysinfo.panels[0].name == "CPU"


def test_set_refuses_an_empty_layout(fake_platform) -> None:
    """Saving nothing would make the next load fall back to defaults.

    A wipe dressed up as a write — and the UI path that could send it is a
    delete-the-last-panel click.
    """
    app = App(fake_platform)
    app.dispatch(GetSensorDashboard())
    result = app.dispatch(SetSensorDashboard(panels=()))

    assert not result.ok
    assert result.message
    assert not app.sysinfo.path.exists()


def test_add_delete_and_rename_all_go_through_the_one_verb(fake_platform) -> None:
    """One bulk verb covers rebind / add / delete / rename (SetOverlayConfig precedent)."""
    app = App(fake_platform)
    panels = app.dispatch(GetSensorDashboard()).panels

    panels.append(PanelConfig(0, "Custom", [
        SensorBinding(f"Sensor {i + 1}", "", "") for i in range(4)
    ]))
    del panels[1]
    panels[0].name = "Renamed"

    app.dispatch(SetSensorDashboard(panels=tuple(panels)))
    names = [p.name for p in App(fake_platform)
             .dispatch(GetSensorDashboard()).panels]
    assert names == ["Renamed", "Memory", "HDD", "Network", "Fan", "Custom"]


def test_unbound_rows_survive_a_save(fake_platform) -> None:
    """``sensor_id=""`` is how the user says "nothing here"."""
    app = App(fake_platform)
    panels = app.dispatch(GetSensorDashboard()).panels
    panels[0].sensors[0] = SensorBinding("TEMP", "", "°C")
    app.dispatch(SetSensorDashboard(panels=tuple(panels)))

    reread = App(fake_platform).dispatch(GetSensorDashboard())
    # Re-read auto-maps it again -- which is the self-healing behaviour, and
    # the reason the Query reports how many rows it filled.
    assert reread.auto_mapped >= 1


def test_the_command_survives_the_daemon_socket(fake_platform) -> None:
    """Two levels of nesting: list[PanelConfig] each holding list[SensorBinding]."""
    app = App(fake_platform)
    command = SetSensorDashboard(
        panels=tuple(app.dispatch(GetSensorDashboard()).panels),
    )
    restored = decode_command(encode_command(command))

    assert isinstance(restored, SetSensorDashboard)
    assert isinstance(restored.panels[0], PanelConfig)
    assert isinstance(restored.panels[0].sensors[0], SensorBinding)
    assert restored.panels == command.panels


def test_the_result_survives_the_daemon_socket(fake_platform) -> None:
    result = App(fake_platform).dispatch(GetSensorDashboard())
    restored = decode_result(encode_result(result))

    assert restored.panels == result.panels
    assert restored.auto_mapped == result.auto_mapped


def test_auto_map_leaves_a_row_unbound_when_the_host_cannot_read_it() -> None:
    """The documented ``--`` path, which could not fire until now.

    ``auto_map``'s contract is "non-fan rows whose target id is not available
    on this host stay unbound — the panel renders ``--``".  Measured on
    2026-09-14 it never happened for 16 of its 20 exact-id targets: they are
    STATIC catalog keys that ``discover()`` advertised on every machine, so
    every row bound and a macOS host showed ``0`` for CPU Usage / Clock / Power
    instead of ``--`` — and ``auto_map`` PERSISTS what it binds.
    """
    from trcc.adapters.infra.sysinfo_config import SysInfoConfig
    from trcc.adapters.sensors.aggregator import BaselineSensors
    from trcc.core.ports import CpuSource

    from .conftest import FakeMemory

    class _TempOnlyCpu(CpuSource):
        @property
        def name(self) -> str:
            return "temp-only"

        def temp(self) -> float | None:
            return 47.5

    enum = BaselineSensors(cpu=_TempOnlyCpu(), memory=FakeMemory(),
                           gpus=[], fans=[])
    cfg = SysInfoConfig.__new__(SysInfoConfig)
    cfg.panels = SysInfoConfig.defaults()
    cfg.auto_map(enum.discover())

    cpu_panel = next(p for p in cfg.panels if p.category_id == 1)
    bound = {b.label: b.sensor_id for b in cpu_panel.sensors}
    assert bound["TEMP"] == "cpu:temp"
    assert [lbl for lbl in ("Usage", "Clock", "Power") if bound[lbl]] == [], (
        "a row bound to a sensor this host can never read renders 0, not --"
    )


# ── #145: the FAN rows pick the LCD's fan slots ──────────────────────────────


def _pin_cpufan(panels: list[PanelConfig], sensor_id: str) -> list[PanelConfig]:
    fan_panel = next(p for p in panels if p.category_id == 6)
    fan_panel.sensors[0] = SensorBinding("CPUFAN", sensor_id, "RPM")
    return panels


def test_rebinding_a_fan_row_moves_the_lcds_fan_slot(fake_platform) -> None:
    """Choosing the header for CPUFAN IS rebinding the dashboard's FAN row, as
    in the C#.  The two used to be separate policies and could disagree."""
    app = App(fake_platform)
    panels = list(app.dispatch(GetSensorDashboard()).panels)

    assert app.dispatch(SetSensorDashboard(
        panels=tuple(_pin_cpufan(panels, "gpu:primary:temp")))).ok

    # The next sweep applies it, as a GPU or disk choice does; ask the policy
    # directly rather than wait out the cached sweep.
    sensors = app.platform.sensors()
    readings = sensors.read_all()
    assert sensors.fan_slots(readings)["fan:cpu"] == readings["gpu:primary:temp"]


def test_a_saved_fan_row_pins_the_slot_after_a_restart(fake_platform) -> None:
    """Every process seeds the pins at start -- a CLI render or the daemon
    shows the fan the user picked, not only the session that picked it."""
    first = App(fake_platform)
    panels = list(first.dispatch(GetSensorDashboard()).panels)
    assert first.dispatch(SetSensorDashboard(
        panels=tuple(_pin_cpufan(panels, "gpu:primary:temp")))).ok

    fake_platform._sensors = None           # a new process builds its own
    restarted = App(fake_platform)

    readings = restarted.platform.sensors().read_all()
    assert readings["fan:cpu"] == readings["gpu:primary:temp"]
