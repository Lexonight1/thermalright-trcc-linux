"""Tests for ``services.metrics_personalize.personalize_readings``.

Pure-function tests — no fixtures, no mocks.  Covers:

  * Default args (C, hdd_enabled=True) pass through unchanged.
  * temp_unit="F" converts every ``:temp`` key, leaves non-temps alone.
  * hdd_enabled=False drops every ``disk:*`` key.
  * The combination applies both transformations.
  * Input dict is not mutated.
  * Empty input → empty output.
  * Mixed-case prefix matching ("Disk:" should NOT match — keys are
    canonical lowercase).
"""
from __future__ import annotations

from trcc.core.models import CpuMetrics, GpuMetrics, HardwareMetrics
from trcc.services.metrics_personalize import (
    personalize_metrics,
    personalize_readings,
)


def test_defaults_pass_through_unchanged() -> None:
    raw = {"cpu:temp": 33.0, "cpu:usage": 25.0, "disk:read": 1.5}
    out = personalize_readings(raw)
    assert out == raw
    # And we got a NEW dict, not the same object.
    assert out is not raw


def test_temp_unit_f_converts_only_temp_keys() -> None:
    raw = {
        "cpu:temp": 0.0,
        "cpu:usage": 25.0,
        "gpu:primary:temp": 100.0,
        "memory:percent": 50.0,
        "fan:hwmon:nct6798:fan1:rpm": 1200.0,
    }
    out = personalize_readings(raw, temp_unit="F")
    assert out["cpu:temp"] == 32.0          # 0°C  → 32°F
    assert out["gpu:primary:temp"] == 212.0  # 100°C → 212°F
    assert out["cpu:usage"] == 25.0          # untouched
    assert out["memory:percent"] == 50.0     # untouched
    assert out["fan:hwmon:nct6798:fan1:rpm"] == 1200.0  # untouched


def test_temp_unit_c_passes_temps_unchanged() -> None:
    raw = {"cpu:temp": 33.0, "gpu:primary:temp": 30.0}
    out = personalize_readings(raw, temp_unit="C")
    assert out == raw


def test_hdd_disabled_drops_disk_keys() -> None:
    raw = {
        "cpu:temp": 33.0,
        "disk:read": 1.5,
        "disk:write": 0.5,
        "disk:activity": 12.0,
        "disk:0:temp": 40.0,
        "net:up": 100.0,
    }
    out = personalize_readings(raw, hdd_enabled=False)
    assert "disk:read" not in out
    assert "disk:write" not in out
    assert "disk:activity" not in out
    assert "disk:0:temp" not in out
    assert out["cpu:temp"] == 33.0
    assert out["net:up"] == 100.0
    assert len(out) == 2


def test_hdd_enabled_keeps_disk_keys() -> None:
    raw = {"cpu:temp": 33.0, "disk:read": 1.5}
    out = personalize_readings(raw, hdd_enabled=True)
    assert out == raw


def test_both_transformations_compose() -> None:
    raw = {
        "cpu:temp": 0.0,
        "disk:0:temp": 40.0,
        "disk:read": 1.5,
        "memory:percent": 60.0,
    }
    out = personalize_readings(raw, temp_unit="F", hdd_enabled=False)
    # disk:* gone
    assert "disk:0:temp" not in out
    assert "disk:read" not in out
    # cpu:temp converted to °F
    assert out["cpu:temp"] == 32.0
    # memory:percent untouched
    assert out["memory:percent"] == 60.0


def test_input_dict_not_mutated() -> None:
    raw = {"cpu:temp": 50.0, "disk:read": 1.5}
    snapshot = dict(raw)
    personalize_readings(raw, temp_unit="F", hdd_enabled=False)
    assert raw == snapshot


def test_empty_input_yields_empty_output() -> None:
    assert personalize_readings({}) == {}
    assert personalize_readings({}, temp_unit="F", hdd_enabled=False) == {}


def test_key_order_preserved_for_surviving_keys() -> None:
    raw = {
        "cpu:temp": 33.0,
        "disk:read": 1.5,    # dropped
        "gpu:primary:temp": 30.0,
        "memory:percent": 50.0,
    }
    out = personalize_readings(raw, hdd_enabled=False)
    assert list(out.keys()) == ["cpu:temp", "gpu:primary:temp", "memory:percent"]


def test_prefix_match_is_lowercase_canonical() -> None:
    # Canonical sensor ids use lowercase prefixes; an uppercase "Disk:"
    # key (unusual) should NOT be filtered — defensive against a future
    # source that mistakenly emits uppercase.  Document by test.
    raw = {"Disk:Read": 1.5, "disk:read": 2.5}
    out = personalize_readings(raw, hdd_enabled=False)
    assert "disk:read" not in out
    assert out["Disk:Read"] == 1.5


def test_suffix_match_for_temp_is_exact() -> None:
    # ":temp" suffix matches; ":temperature" would too if it were used.
    # Verify the boundary: "cpu:tempo" (hypothetical typo) should NOT
    # be converted.
    raw = {
        "cpu:temp": 50.0,
        "cpu:tempo": 50.0,
        "disk:0:temp": 50.0,
    }
    out = personalize_readings(raw, temp_unit="F")
    assert out["cpu:temp"] == 122.0
    assert out["cpu:tempo"] == 50.0           # not converted
    assert out["disk:0:temp"] == 122.0        # ends in :temp


# ── personalize_metrics — the typed sibling ──────────────────────────


def test_metrics_default_pass_through() -> None:
    m = HardwareMetrics(cpu_temp=33.0, gpu_temp=30.0, cpu_percent=50.0)
    out = personalize_metrics(m)
    assert out.cpu_temp == 33.0
    assert out.gpu_temp == 30.0
    assert out.cpu_percent == 50.0
    assert out is not m  # new object


def test_metrics_fahrenheit_converts_every_temp_field() -> None:
    m = HardwareMetrics(
        cpu_temp=50.0, gpu_temp=100.0, mem_temp=40.0, disk_temp=25.0,
        cpu_percent=50.0,  # non-temp untouched
        cpus=[CpuMetrics(temp=50.0, usage=50.0)],
        gpus=[GpuMetrics(temp=100.0, usage=2.0)],
    )
    out = personalize_metrics(m, temp_unit="F")
    assert out.cpu_temp == 122.0
    assert out.gpu_temp == 212.0
    assert out.mem_temp == 104.0
    assert out.disk_temp == 77.0
    assert out.cpu_percent == 50.0            # non-temp untouched
    assert out.cpus[0].temp == 122.0          # per-unit converted
    assert out.gpus[0].temp == 212.0
    assert out.cpus[0].usage == 50.0          # per-unit non-temp untouched


def test_metrics_zero_temp_is_no_reading_not_32f() -> None:
    # 0.0 == "no reading" (snapshot coalesces absent sensors to 0.0).
    # Converting it to 32°F would fabricate a reading the formatters
    # render instead of "NC".  It must stay 0.0.
    m = HardwareMetrics(
        cpu_temp=0.0, gpu_temp=0.0, mem_temp=0.0,
        cpus=[CpuMetrics(temp=0.0)], gpus=[GpuMetrics(temp=0.0)],
    )
    out = personalize_metrics(m, temp_unit="F")
    assert out.cpu_temp == 0.0
    assert out.gpu_temp == 0.0
    assert out.mem_temp == 0.0
    assert out.cpus[0].temp == 0.0
    assert out.gpus[0].temp == 0.0


def test_metrics_hdd_disabled_zeroes_disk_and_filters_readings() -> None:
    m = HardwareMetrics(
        disk_temp=42.0, disk_activity=18.0, disk_read=99.0, disk_write=5.0,
        cpu_temp=40.0,
        readings={"disk:read": 99.0, "cpu:temp": 40.0},
    )
    out = personalize_metrics(m, hdd_enabled=False)
    assert out.disk_temp == 0.0
    assert out.disk_activity == 0.0
    assert out.disk_read == 0.0
    assert out.disk_write == 0.0
    assert out.cpu_temp == 40.0               # non-disk survives
    assert "disk:read" not in out.readings    # dict filtered too
    assert out.readings["cpu:temp"] == 40.0


def test_metrics_input_not_mutated() -> None:
    m = HardwareMetrics(cpu_temp=50.0, cpus=[CpuMetrics(temp=50.0)],
                        readings={"cpu:temp": 50.0})
    personalize_metrics(m, temp_unit="F", hdd_enabled=False)
    assert m.cpu_temp == 50.0
    assert m.cpus[0].temp == 50.0
    assert m.readings == {"cpu:temp": 50.0}
