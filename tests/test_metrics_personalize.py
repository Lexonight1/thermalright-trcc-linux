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

from trcc.services.metrics_personalize import personalize_readings


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
