"""Sensor aggregator — normalized key shape, primary GPU resolver."""
from __future__ import annotations

from trcc.adapters.sensors.aggregator import BaselineSensors

from .conftest import FakeCpu, FakeGpu, FakeMemory


def _sensors_with(gpus=None) -> BaselineSensors:
    return BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(),
        gpus=gpus or [], fans=[],
    )


def test_read_all_produces_normalized_cpu_keys() -> None:
    s = _sensors_with()
    r = s.read_all()

    assert r["cpu:temp"] == 42.0
    assert r["cpu:usage"] == 15.0
    assert r["cpu:freq"] == 3200.0
    assert r["cpu:power"] == 65.0


def test_read_all_produces_normalized_memory_keys() -> None:
    s = _sensors_with()
    r = s.read_all()

    assert r["memory:used"] == 8192.0
    assert r["memory:available"] == 24576.0
    assert r["memory:total"] == 32768.0
    assert r["memory:percent"] == 25.0


def test_gpu_readings_available_under_three_key_shapes() -> None:
    """Every GPU reading must be reachable by index, vendor-key, AND primary alias."""
    gpu = FakeGpu(0, discrete=True, vendor="nvidia")
    s = _sensors_with(gpus=[gpu])

    r = s.read_all()

    # Indexed access
    assert r["gpu:0:temp"] == 55.0
    # Vendor-keyed access
    assert r["gpu:nvidia:0:temp"] == 55.0
    # Primary alias
    assert r["gpu:primary:temp"] == 55.0


def test_primary_gpu_prefers_discrete() -> None:
    igpu = FakeGpu(0, discrete=False, vendor="intel")
    dgpu = FakeGpu(0, discrete=True, vendor="nvidia")

    # Pass in wrong order — aggregator sorts discrete first
    s = _sensors_with(gpus=[igpu, dgpu])
    primary = s.primary_gpu()

    assert primary is not None
    assert primary.key == "nvidia:0"
    assert s.read_all()["gpu:primary:temp"] == dgpu.temp()


def test_primary_gpu_falls_back_to_igpu_when_no_discrete() -> None:
    igpu = FakeGpu(0, discrete=False, vendor="intel")
    s = _sensors_with(gpus=[igpu])

    primary = s.primary_gpu()

    assert primary is not None
    assert primary.key == "intel:0"


def test_primary_gpu_is_none_on_headless() -> None:
    s = _sensors_with(gpus=[])

    assert s.primary_gpu() is None
    # No gpu:primary:* keys should appear
    r = s.read_all()
    assert not any(k.startswith("gpu:primary:") for k in r)


def test_discover_contains_one_reading_per_declared_key() -> None:
    s = _sensors_with(gpus=[FakeGpu(0, discrete=True, vendor="nvidia")])

    readings = s.discover()

    ids = {r.sensor_id for r in readings}
    # Minimum expected keys
    expected = {
        "cpu:temp", "cpu:usage", "cpu:freq", "cpu:power",
        "memory:used", "memory:percent",
        "gpu:0:temp", "gpu:nvidia:0:temp", "gpu:primary:temp",
        "time:hour", "date:year",
    }
    missing = expected - ids
    assert not missing, f"missing normalized keys: {missing}"


def test_none_values_omitted_from_flat_dict() -> None:
    """Source returning None for a reading must not produce an entry."""
    cpu = FakeCpu()
    cpu.values["power"] = None   # type: ignore[assignment]
    s = BaselineSensors(cpu=cpu, memory=FakeMemory(), gpus=[], fans=[])

    r = s.read_all()

    assert "cpu:power" not in r
    assert "cpu:temp" in r       # other readings unaffected
