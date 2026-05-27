"""BSD sysctl sensor source — wire-format parsing via DI seam.

The real ``sysctl`` only exists on BSDs (and Linux for some unrelated
shim).  Tests inject canned ``sysctl -a`` strings — one per OS the
parser knows about — and exercise the full ``SysctlCpu.temp()`` path
from the Linux dev box.
"""
from __future__ import annotations

from trcc.adapters.sensors._sysctl import SysctlCpu

# ── FreeBSD ──────────────────────────────────────────────────────────


_FREEBSD_OUTPUT = """\
hw.physmem: 34359738368
dev.cpu.0.temperature: 42.5C
dev.cpu.1.temperature: 47.5C
dev.cpu.2.temperature: 45.0C
dev.cpu.3.temperature: 51.0C
hw.acpi.thermal.tz0.temperature: 39.4C
kern.osrelease: 14.0-RELEASE
"""


def test_freebsd_temp_returns_hottest_core() -> None:
    cpu = SysctlCpu(runner=lambda: _FREEBSD_OUTPUT, system="FreeBSD")
    assert cpu.temp() == 51.0


def test_freebsd_temp_ignores_acpi_thermal_zone() -> None:
    """ACPI thermal zones share the regex prefix but are excluded here."""
    output = "hw.acpi.thermal.tz0.temperature: 99.9C\n"
    cpu = SysctlCpu(runner=lambda: output, system="FreeBSD")
    assert cpu.temp() is None


def test_freebsd_temp_returns_none_on_empty_output() -> None:
    cpu = SysctlCpu(runner=lambda: "", system="FreeBSD")
    assert cpu.temp() is None


def test_freebsd_temp_returns_none_when_no_cpu_lines() -> None:
    """sysctl ran but reported nothing thermal-related."""
    cpu = SysctlCpu(runner=lambda: "hw.physmem: 0\n", system="FreeBSD")
    assert cpu.temp() is None


# ── OpenBSD ─────────────────────────────────────────────────────────


_OPENBSD_OUTPUT = """\
hw.model=AMD Ryzen 9 7950X
hw.sensors.cpu0.temp0:34.50 degC
hw.sensors.cpu1.temp0:38.50 degC
hw.sensors.cpu2.temp0:36.00 degC
hw.sensors.acpitz0.temp0:45.40 degC
"""


def test_openbsd_temp_returns_hottest_core() -> None:
    cpu = SysctlCpu(runner=lambda: _OPENBSD_OUTPUT, system="OpenBSD")
    assert cpu.temp() == 38.5


def test_openbsd_temp_ignores_acpitz_zones() -> None:
    cpu = SysctlCpu(
        runner=lambda: "hw.sensors.acpitz0.temp0:99.99 degC\n",
        system="OpenBSD",
    )
    assert cpu.temp() is None


# ── NetBSD ───────────────────────────────────────────────────────────


def test_netbsd_temp_uses_machdep_key() -> None:
    output = "machdep.cpu_temperature: 55.2\n"
    cpu = SysctlCpu(runner=lambda: output, system="NetBSD")
    assert cpu.temp() == 55.2


# ── DragonFlyBSD reuses FreeBSD patterns ────────────────────────────


def test_dragonfly_reuses_freebsd_pattern() -> None:
    cpu = SysctlCpu(runner=lambda: _FREEBSD_OUTPUT, system="DragonFly")
    assert cpu.temp() == 51.0


# ── Unknown system — no regex registered ────────────────────────────


def test_unknown_system_returns_none() -> None:
    cpu = SysctlCpu(runner=lambda: _FREEBSD_OUTPUT, system="Plan9")
    assert cpu.temp() is None


# ── usage/freq/power are intentionally None — psutil covers them ────


def test_sysctl_only_exposes_temp() -> None:
    cpu = SysctlCpu(runner=lambda: _FREEBSD_OUTPUT, system="FreeBSD")
    assert cpu.usage() is None
    assert cpu.freq() is None
    assert cpu.power() is None


# ── Name reflects the active OS ─────────────────────────────────────


def test_name_includes_system() -> None:
    assert SysctlCpu(runner=lambda: "", system="OpenBSD").name == "sysctl (OpenBSD)"


# ── Runner exceptions degrade silently ──────────────────────────────


def test_runner_returning_empty_string_does_not_crash() -> None:
    """Default runner returns "" on subprocess failure — exercise that branch."""
    cpu = SysctlCpu(runner=lambda: "", system="FreeBSD")
    assert cpu.temp() is None


def test_garbage_value_skipped_not_raised() -> None:
    """A non-numeric value should not bubble up to the caller."""
    output = "dev.cpu.0.temperature: not_a_number\n"
    cpu = SysctlCpu(runner=lambda: output, system="FreeBSD")
    assert cpu.temp() is None


def test_freebsd_runner_default_factory_smoke() -> None:
    """Default runner doesn't crash on Linux — returns empty string."""
    from trcc.adapters.sensors._sysctl import _default_runner

    output = _default_runner()
    assert isinstance(output, str)


# ── build_bsd_sensors composes the chain ─────────────────────────────


def test_build_bsd_sensors_assembles_chain(monkeypatch) -> None:
    """The factory falls through to psutil when sysctl returns no data."""
    from trcc.adapters.sensors import bsd as bsd_factory

    monkeypatch.setattr(
        "trcc.adapters.sensors._sysctl._default_runner",
        lambda: "",
    )
    sensors = bsd_factory.build_bsd_sensors()
    cpu = sensors.cpu()
    # psutil always provides usage; verify chain falls through cleanly
    assert cpu.usage() is not None
    # temp may be None on a dev box; just verify no exception
    cpu.temp()


# ── OpenBSD hw.sensors fan parser ───────────────────────────────────


_OPENBSD_FAN_OUTPUT = """\
hw.sensors.lm0.temp0=43.00 degC
hw.sensors.lm0.fan0:1607 RPM
hw.sensors.lm0.fan1:892 RPM
hw.sensors.it0.fan0:1234 RPM
hw.sensors.ipmi0.fan0:5400 RPM
hw.sensors.acpitz0.temp0:55.00 degC
"""


def test_discover_openbsd_fans_finds_all_drivers() -> None:
    """All four fans across three drivers (lm, it, ipmi) are enumerated."""
    from trcc.adapters.sensors._sysctl import discover_openbsd_fans

    fans = discover_openbsd_fans(runner=lambda: _OPENBSD_FAN_OUTPUT)
    keys = sorted(f.key for f in fans)
    assert keys == [
        "sysctl:ipmi0:fan0",
        "sysctl:it0:fan0",
        "sysctl:lm0:fan0",
        "sysctl:lm0:fan1",
    ]


def test_openbsd_fan_rpm_returns_int() -> None:
    from trcc.adapters.sensors._sysctl import discover_openbsd_fans

    fans = discover_openbsd_fans(runner=lambda: _OPENBSD_FAN_OUTPUT)
    by_key = {f.key: f for f in fans}
    assert by_key["sysctl:lm0:fan0"].rpm() == 1607
    assert by_key["sysctl:lm0:fan1"].rpm() == 892
    assert by_key["sysctl:ipmi0:fan0"].rpm() == 5400


def test_openbsd_fan_percent_always_none() -> None:
    """hw.sensors framework doesn't expose PWM duty cycle."""
    from trcc.adapters.sensors._sysctl import discover_openbsd_fans

    fans = discover_openbsd_fans(runner=lambda: _OPENBSD_FAN_OUTPUT)
    assert fans[0].percent() is None


def test_openbsd_fan_name_default_label() -> None:
    from trcc.adapters.sensors._sysctl import discover_openbsd_fans

    fans = discover_openbsd_fans(runner=lambda: "hw.sensors.lm0.fan0:1607 RPM\n")
    assert fans[0].name == "lm0 fan0"


def test_discover_openbsd_fans_empty_when_no_fan_lines() -> None:
    from trcc.adapters.sensors._sysctl import discover_openbsd_fans

    fans = discover_openbsd_fans(runner=lambda: "hw.sensors.lm0.temp0=43.00 degC\n")
    assert fans == []


def test_discover_openbsd_fans_handles_empty_output() -> None:
    from trcc.adapters.sensors._sysctl import discover_openbsd_fans

    fans = discover_openbsd_fans(runner=lambda: "")
    assert fans == []


def test_openbsd_fan_snapshot_caches_within_ttl() -> None:
    """A poll tick re-reads sysctl once; sibling fans share the same output."""
    from trcc.adapters.sensors._sysctl import _SysctlSnapshot, discover_openbsd_fans

    calls = []
    fake_clock = [0.0]

    def runner() -> str:
        calls.append(1)
        return _OPENBSD_FAN_OUTPUT

    snap = _SysctlSnapshot(
        runner=runner, ttl_s=1.0, clock=lambda: fake_clock[0],
    )
    fans = discover_openbsd_fans(snapshot=snap)
    # initial discovery already produced one run
    initial_calls = len(calls)
    # all fans read within TTL → no additional sysctl runs
    [f.rpm() for f in fans]
    assert len(calls) == initial_calls
    # advance past TTL → next rpm() re-runs sysctl
    fake_clock[0] += 2.0
    fans[0].rpm()
    assert len(calls) == initial_calls + 1


def test_discover_openbsd_fans_skips_malformed_lines() -> None:
    """Non-RPM 'fan' tokens shouldn't fabricate FanSource entries."""
    from trcc.adapters.sensors._sysctl import discover_openbsd_fans

    output = (
        "hw.sensors.lm0.fan0:not_a_number RPM\n"
        "hw.sensors.lm0.fan1:2000 RPM\n"
    )
    fans = discover_openbsd_fans(runner=lambda: output)
    keys = [f.key for f in fans]
    assert keys == ["sysctl:lm0:fan1"]


def test_build_bsd_sensors_includes_fans_on_openbsd(monkeypatch) -> None:
    """Factory wires OpenBSD fans only when platform.system() == 'OpenBSD'."""
    from trcc.adapters.sensors import bsd as bsd_factory

    monkeypatch.setattr("platform.system", lambda: "OpenBSD")
    monkeypatch.setattr(
        "trcc.adapters.sensors._sysctl._default_runner",
        lambda: _OPENBSD_FAN_OUTPUT,
    )
    sensors = bsd_factory.build_bsd_sensors()
    fan_keys = sorted(f.key for f in sensors.fans())
    assert fan_keys == [
        "sysctl:ipmi0:fan0",
        "sysctl:it0:fan0",
        "sysctl:lm0:fan0",
        "sysctl:lm0:fan1",
    ]


def test_build_bsd_sensors_no_fans_on_freebsd(monkeypatch) -> None:
    """FreeBSD has no universal fan sysctl — factory yields zero fans."""
    from trcc.adapters.sensors import bsd as bsd_factory

    monkeypatch.setattr("platform.system", lambda: "FreeBSD")
    monkeypatch.setattr(
        "trcc.adapters.sensors._sysctl._default_runner",
        lambda: _OPENBSD_FAN_OUTPUT,  # even if output contains hw.sensors
    )
    sensors = bsd_factory.build_bsd_sensors()
    assert sensors.fans() == []
