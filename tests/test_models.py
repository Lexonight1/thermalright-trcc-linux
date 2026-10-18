"""Core domain-model helpers."""
from __future__ import annotations

import dataclasses
from typing import Any, cast

import pytest

from trcc.core.models import (
    HARDWARE_METRICS,
    METRICS,
    HardwareMetrics,
    oriented_resolution,
    parse_resolution,
)
from trcc.core.registry import ALL_DEVICES


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


# ── keepalive — every registered device keeps its panel fed ──────────────


@pytest.mark.parametrize("key", sorted(ALL_DEVICES))
def test_every_registered_device_keeps_its_panel_fed(key) -> None:
    """Asked of the device class the registry builds, never of a flag.

    Every family gives up its picture without a stream (SCSI, bulk, LY, LED,
    HID 4.07 -- measured or reported) and the C# re-sends every family.  A
    class that overrides ``needs_keepalive`` to False fails here until its
    firmware is shown to hold a frame.  MUTATION CHECK: make the base default
    False -- every row fails.
    """
    from trcc.adapters.device import DEVICES

    info = ALL_DEVICES[key]
    device = DEVICES[info.wire](info, cast(Any, None))
    assert device.needs_keepalive is True


# The families whose firmware reverts in ~2-3 s keep the C#'s ~150 ms; every
# other device resends after 1 s.  Literal, so drift in EITHER direction fails.
_FAST_REVERT = {
    (0x87AD, 0x70DB),   # GrandVision 360 AIO    (bulk)
    (0x0416, 0x5408),   # Trofeo Vision 9.16     (LY)
    (0x0416, 0x5409),   # Trofeo Vision 9.16     (LY)
}


@pytest.mark.parametrize("key", sorted(ALL_DEVICES))
def test_every_registered_device_resends_inside_its_timeout(key) -> None:
    """MUTATION CHECK: drop ``BulkLcd.keepalive_interval`` -- the bulk row fails."""
    from trcc.adapters.device import DEVICES

    info = ALL_DEVICES[key]
    device = DEVICES[info.wire](info, cast(Any, None))
    assert device.keepalive_interval == (0.150 if key in _FAST_REVERT else 1.0)


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
