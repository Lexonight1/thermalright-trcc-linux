"""Core domain-model helpers."""
from __future__ import annotations

import dataclasses

import pytest

from trcc.core.models import (
    HARDWARE_METRICS,
    METRICS,
    HardwareMetrics,
    oriented_resolution,
    parse_resolution,
)


@pytest.mark.parametrize("text,expected", [
    ("320x320", (320, 320)),
    ("1280x480", (1280, 480)),
    ("640X480", (640, 480)),   # case-insensitive separator
])
def test_parse_resolution_valid(text: str, expected: tuple[int, int]) -> None:
    assert parse_resolution(text) == expected


@pytest.mark.parametrize("bad", ["", "320", "x", "320x", "axb", "320x320x1"])
def test_parse_resolution_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError, match="bad resolution"):
        parse_resolution(bad)


@pytest.mark.parametrize("native,orientation,expected", [
    ((854, 480), 0, (854, 480)),     # landscape — unchanged
    ((854, 480), 180, (854, 480)),   # landscape flipped — still unchanged
    ((854, 480), 90, (480, 854)),    # portrait — swapped
    ((854, 480), 270, (480, 854)),   # portrait flipped — swapped
    ((320, 320), 90, (320, 320)),    # square — swap is a no-op
])
def test_oriented_resolution(
    native: tuple[int, int], orientation: int, expected: tuple[int, int],
) -> None:
    assert oriented_resolution(native, orientation) == expected


# ── volatile_frames — a panel fact, gated across the whole registry ──────


# Exactly the panels whose firmware reverts to the boot logo unless frames keep
# arriving.  Stated as a literal set so BOTH directions are gated: a device that
# stops being volatile fails, and a device that silently becomes volatile fails
# too.  Parametrising over ALL_DEVICES instead would only ever re-assert
# whatever the registry currently says, which gates nothing.
#
# MEASURED 2026-08-19, because it is tempting to claim more than is true: today
# NO wire carries both kinds (bulk 1/0, bulk_ali 1/0, ly 2/0, hid 0/3, scsi 0/2,
# led 0/1), so volatility IS still derivable from the wire.  The old
# wire-keyed set was not wrong about current hardware.  It becomes wrong the
# moment 0416:5406 moves to Wire.HID, where every other panel latches — which
# is precisely the merge this flag unblocks.
#
# This existed as VOLATILE_FRAME_WIRES keyed on Wire until 2026-08-19.  Only two
# of the four devices were covered by any test then (test_app_senders pinned
# 87ad:70db volatile and 0402:3922 not), so the LY pair and the Ali panel could
# have lost their keepalive with the suite still green — and a keepalive that
# stops being requested is invisible until someone's screen goes dark.
_VOLATILE_DEVICES = {
    (0x0416, 0x5406),   # Elite Vision 360 ARGB  (F5 protocol)
    (0x0416, 0x5408),   # Trofeo Vision 9.16     (LY)
    (0x0416, 0x5409),   # Trofeo Vision 9.16     (LY)
    (0x87AD, 0x70DB),   # GrandVision 360 AIO    (bulk)
}


def test_exactly_these_devices_declare_volatile_frames() -> None:
    """The volatile set is a fact about panels, not about wires."""
    from trcc.core.registry import ALL_DEVICES

    declared = {key for key, p in ALL_DEVICES.items() if p.volatile_frames}
    assert declared == _VOLATILE_DEVICES, (
        "volatile_frames drifted — a panel that needs a keepalive and stopped "
        "asking for one goes dark with no error anywhere"
    )


# =========================================================================
# METRICS — one metric identity, three spellings
# =========================================================================


def test_every_spelling_resolves_to_the_same_metric() -> None:
    """A DC pair, a field name and a sensor id all name one thing.

    Each layer holds a different vocabulary: the DC parser has ``(0, 2)``, the
    sensor DTO has ``cpu_percent``, the render path has ``cpu:usage``.  They
    must resolve to one object or the layers cannot talk.
    """
    by_pair = METRICS[(0, 2)]
    assert METRICS["cpu_percent"] is by_pair
    assert METRICS["cpu:usage"] is by_pair
    assert str(by_pair) == "cpu:usage"
    assert by_pair.pair == (0, 2)


def test_the_two_vocabularies_are_not_mechanically_convertible() -> None:
    """The reason this catalog exists, stated as a test.

    Nine of the twenty-four metrics differ between the field name and the
    sensor id by NO rule — ``cpu_percent``/``cpu:usage``,
    ``gpu_temp``/``gpu:primary:temp``, ``mem_*``/``memory:*``.  So the obvious
    ``field.replace("_", ":")`` is wrong for more than a third of them, and
    the failure is silent: the overlay element is dropped from the layout.

    If this ever drops to zero, the catalog is no longer earning its keep and
    someone should say so deliberately rather than discover it.
    """
    unconvertible = [
        m for m in (METRICS[p] for p in METRICS)
        if m.field.replace("_", ":", 1) != m.sensor_id
    ]
    assert len(unconvertible) == 9
    assert {m.field for m in unconvertible} >= {
        "cpu_percent", "gpu_temp", "mem_percent"}


def test_the_dc_alias_resolves_to_the_same_reading() -> None:
    """``(10000, 1)`` is the C# Fan-LCD sentinel for the cooler's own fan.

    It is an alias, not a metric: it resolves, but it is not a separate row.

    MUTATION CHECK: drop the ``aliases`` argument and this fails with KeyError.
    """
    assert METRICS[(10000, 1)] is METRICS["fan:cpu"]
    assert (10000, 1) not in list(METRICS)
    assert len(METRICS) == 24


def test_hardware_metrics_reads_by_any_spelling() -> None:
    """The DTO answers whichever vocabulary the caller happens to hold.

    This replaces a two-step ``table lookup -> getattr(name, None)`` that
    returned None for a typo and silently drew nothing.
    """
    m = HardwareMetrics(cpu_temp=52.0, mem_percent=43.0)

    assert m[(0, 1)] == 52.0
    assert m["cpu_temp"] == 52.0
    assert m["cpu:temp"] == 52.0
    assert m["memory:percent"] == 43.0
    assert "cpu:temp" in m
    assert "not_a_metric" not in m


def test_an_unknown_metric_is_loud_not_silent() -> None:
    """A typo must raise, not return None and draw an empty element."""
    with pytest.raises(KeyError):
        HardwareMetrics()["cpu:tmep"]


def test_every_metric_field_exists_on_the_dto() -> None:
    """A field name that does not exist would draw nothing, silently.

    ``getattr(metrics, name, None)`` was the old access path, so a typo in the
    table produced an empty overlay element and no error anywhere.

    MUTATION CHECK: rename any ``field=`` in METRICS and this fails.
    """
    names = {f.name for f in dataclasses.fields(HardwareMetrics)}
    for pair in METRICS:
        assert METRICS[pair].field in names


def test_the_legacy_tables_are_derived_not_maintained() -> None:
    """``HARDWARE_METRICS`` and the DC codec's table both come from METRICS.

    They used to be two hand-kept tables over the same keys, each claiming in
    its docstring to be the single source.  Derivation is what makes that
    claim true.

    MUTATION CHECK: hand-write either table again and this fails as soon as
    one row disagrees.
    """
    from trcc.services._dc import _HW_TO_SENSOR

    assert {p: METRICS[p].field for p in METRICS} == HARDWARE_METRICS
    assert {
        p: (m.sensor_id, m.fmt) for p, m in METRICS.by_dc_pair.items()
    } == _HW_TO_SENSOR
    assert len(_HW_TO_SENSOR) == len(METRICS) + 1      # + the fan alias
