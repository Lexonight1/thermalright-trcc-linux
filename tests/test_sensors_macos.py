"""macOS SMC sensor source — DI-injected fake client + decoder tests.

The real ``SMCClient`` only opens on macOS; the protocol logic and
chain composition are exercised here on the Linux dev box through a
``_FakeSmcClient`` that satisfies ``SmcClientPort`` with an in-memory
key → value dict.

The pure ``_parse_smc_bytes`` decoder gets direct byte-level coverage
since the on-wire layout is OS-blind once the bytes are in hand.
"""
from __future__ import annotations

import struct
from collections.abc import Iterable

import pytest

from trcc.adapters.sensors._smc import (
    APPLE_SILICON_CPU_TEMP_KEYS,
    APPLE_SILICON_GPU_TEMP_KEYS,
    INTEL_CPU_TEMP_KEYS,
    INTEL_GPU_TEMP_KEYS,
    SmcClientPort,
    _datatype_to_str,
    _parse_smc_bytes,
    _smc_key_to_uint32,
)

# =========================================================================
# Pure helpers — key encoding + payload decoding
# =========================================================================


def test_smc_key_to_uint32_round_trip() -> None:
    """4-char key → big-endian uint32 → back to string preserves bytes."""
    encoded = _smc_key_to_uint32("TC0P")
    decoded = _datatype_to_str(encoded)
    assert decoded == "TC0P"


def test_smc_key_to_uint32_pads_short_keys() -> None:
    """SMC keys are 4 bytes; shorter strings get space-padded."""
    encoded = _smc_key_to_uint32("TC")
    decoded = _datatype_to_str(encoded)
    assert decoded == "TC  "


# ── sp78 (signed 8.8 fixed point — typical for temperatures) ────────


def _encode_sp78(value_celsius: float) -> bytes:
    """Encode a temperature into the SMC sp78 wire format."""
    raw = int(value_celsius * 256.0)
    return struct.pack(">h", raw)


def test_parse_sp78_temperature() -> None:
    data_type = _smc_key_to_uint32("sp78")
    raw = _encode_sp78(42.5) + b"\x00" * 30
    assert abs(_parse_smc_bytes(data_type, raw, 2) - 42.5) < 0.01


def test_parse_sp78_subzero() -> None:
    """Negative temperatures (cold-soaked Macs) round-trip cleanly."""
    data_type = _smc_key_to_uint32("sp78")
    raw = _encode_sp78(-12.25) + b"\x00" * 30
    assert abs(_parse_smc_bytes(data_type, raw, 2) - (-12.25)) < 0.01


# ── fpe2 (unsigned 14.2 fixed point — fan RPM) ─────────────────────


def test_parse_fpe2_fan_rpm() -> None:
    data_type = _smc_key_to_uint32("fpe2")
    raw = struct.pack(">H", 4000)        # 4000 / 4 = 1000 RPM
    assert _parse_smc_bytes(data_type, raw, 2) == 1000.0


# ── flt (IEEE-754 little-endian — Apple Silicon fan RPM, some temps) ─


def test_parse_flt_little_endian() -> None:
    data_type = _smc_key_to_uint32("flt ")
    raw = struct.pack("<f", 65.25)
    assert abs(_parse_smc_bytes(data_type, raw, 4) - 65.25) < 0.001


# ── ui8 / ui16 / ui32 ──────────────────────────────────────────────


def test_parse_ui8() -> None:
    data_type = _smc_key_to_uint32("ui8 ")
    assert _parse_smc_bytes(data_type, b"\x42", 1) == 66.0


def test_parse_ui16_big_endian() -> None:
    data_type = _smc_key_to_uint32("ui16")
    raw = struct.pack(">H", 1234)
    assert _parse_smc_bytes(data_type, raw, 2) == 1234.0


def test_parse_ui32_big_endian() -> None:
    data_type = _smc_key_to_uint32("ui32")
    raw = struct.pack(">I", 99999)
    assert _parse_smc_bytes(data_type, raw, 4) == 99999.0


# ── fp1f (1.15 fixed point — voltage) ──────────────────────────────


def test_parse_fp1f() -> None:
    data_type = _smc_key_to_uint32("fp1f")
    raw = struct.pack(">H", 32768)       # 1.0 in fp1f
    assert _parse_smc_bytes(data_type, raw, 2) == 1.0


def test_parse_unknown_type_falls_back_to_sp78() -> None:
    """Unfamiliar data types decode as sp78 — best-effort temperature shape."""
    data_type = _smc_key_to_uint32("ZzZz")
    raw = _encode_sp78(33.0)
    assert abs(_parse_smc_bytes(data_type, raw, 2) - 33.0) < 0.01


def test_parse_empty_bytes_is_zero() -> None:
    data_type = _smc_key_to_uint32("sp78")
    assert _parse_smc_bytes(data_type, b"", 0) == 0.0


# =========================================================================
# Fake SmcClient — exercises SmcCpu / SmcGpu via DI seam
# =========================================================================


class _FakeSmcClient:
    """In-memory stand-in for ``SMCClient``.

    Returns the canned float for any key in ``readings``; everything
    else → None (matches SMC behavior for unknown / unimplemented keys).
    """

    def __init__(self, readings: dict[str, float | None]) -> None:
        self._readings = readings
        self._connected = True

    @property
    def connected(self) -> bool:
        return self._connected

    def open(self) -> bool:
        self._connected = True
        return True

    def close(self) -> None:
        self._connected = False

    def read_key_float(self, key: str) -> float | None:
        return self._readings.get(key)


def _client_returning(readings: dict[str, float | None]) -> SmcClientPort:
    """Type-narrow helper for the DI seam."""
    return _FakeSmcClient(readings)


# =========================================================================
# SmcCpu
# =========================================================================


def _make_cpu(readings: dict[str, float | None],
              keys: Iterable[str] = INTEL_CPU_TEMP_KEYS):
    from trcc.adapters.sensors.macos import SmcCpu
    return SmcCpu(client=_client_returning(readings), keys=keys)


def test_smc_cpu_returns_hottest_reading() -> None:
    cpu = _make_cpu({"TC0P": 55.0, "TC0D": 78.0, "TC0E": 65.0})
    assert cpu.temp() == 78.0


def test_smc_cpu_returns_none_when_no_keys_read() -> None:
    cpu = _make_cpu({})
    assert cpu.temp() is None


def test_smc_cpu_skips_obviously_garbage_readings() -> None:
    """SMC sometimes returns sentinel values (huge / negative-extreme)
    for unimplemented keys — clamp to a sane range and skip the noise."""
    cpu = _make_cpu({
        "TC0P": 999.0,             # garbage
        "TC0D": -200.0,            # garbage
        "TC0E": 70.5,              # valid
    })
    assert cpu.temp() == 70.5


def test_smc_cpu_only_exposes_temp() -> None:
    """usage / freq / power fall through to the next chain entry."""
    cpu = _make_cpu({"TC0P": 50.0})
    assert cpu.usage() is None
    assert cpu.freq() is None
    assert cpu.power() is None


def test_smc_cpu_returns_none_when_disconnected() -> None:
    """A client that goes offline after construction → temp() returns None."""
    from trcc.adapters.sensors.macos import SmcCpu

    client = _FakeSmcClient({"TC0P": 50.0})
    cpu = SmcCpu(client=client)
    client.close()                       # simulate the SMC connection dropping
    assert cpu.temp() is None


# =========================================================================
# SmcGpu
# =========================================================================


def _make_gpu(readings: dict[str, float | None]):
    from trcc.adapters.sensors.macos import SmcGpu
    return SmcGpu(client=_client_returning(readings), keys=INTEL_GPU_TEMP_KEYS)


def test_smc_gpu_returns_hottest_temp() -> None:
    gpu = _make_gpu({"TG0P": 45.0, "TG0D": 62.5})
    assert gpu.temp() == 62.5


def test_smc_gpu_identity_defaults_to_intel() -> None:
    gpu = _make_gpu({"TG0P": 45.0})
    assert gpu.key == "intel:0"
    assert gpu.name == "Apple SMC (GPU)"
    assert gpu.is_discrete is False


def test_smc_gpu_clock_power_fan_vram_are_all_none() -> None:
    """SMC doesn't expose these consistently — chain falls through."""
    gpu = _make_gpu({"TG0P": 45.0})
    assert gpu.usage() is None
    assert gpu.clock() is None
    assert gpu.power() is None
    assert gpu.fan() is None
    assert gpu.vram_used() is None
    assert gpu.vram_total() is None


# =========================================================================
# Key-table selection — Apple Silicon env flag
# =========================================================================


def test_intel_keys_only_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env flag, only Intel keys are in the rotation."""
    from trcc.adapters.sensors.macos import _select_cpu_temp_keys

    monkeypatch.delenv("TRCC_NEXT_APPLE_SILICON_SMC", raising=False)
    keys = _select_cpu_temp_keys()
    assert set(keys) == set(INTEL_CPU_TEMP_KEYS)
    assert "Tp09" not in keys           # Apple Silicon


def test_apple_silicon_keys_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TRCC_NEXT_APPLE_SILICON_SMC=1`` extends the rotation."""
    from trcc.adapters.sensors.macos import _select_cpu_temp_keys

    monkeypatch.setenv("TRCC_NEXT_APPLE_SILICON_SMC", "1")
    keys = _select_cpu_temp_keys()
    intel = set(INTEL_CPU_TEMP_KEYS)
    apple = set(APPLE_SILICON_CPU_TEMP_KEYS)
    assert intel.issubset(keys)
    assert apple.issubset(keys)


def test_gpu_key_selection_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.sensors.macos import _select_gpu_temp_keys

    monkeypatch.delenv("TRCC_NEXT_APPLE_SILICON_SMC", raising=False)
    assert set(_select_gpu_temp_keys()) == set(INTEL_GPU_TEMP_KEYS)

    monkeypatch.setenv("TRCC_NEXT_APPLE_SILICON_SMC", "1")
    keys = _select_gpu_temp_keys()
    assert set(INTEL_GPU_TEMP_KEYS).issubset(keys)
    assert set(APPLE_SILICON_GPU_TEMP_KEYS).issubset(keys)


def test_apple_silicon_keys_disabled_when_env_unset_or_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only exact ``"1"`` enables — ``"0"`` / empty / unset all disable."""
    from trcc.adapters.sensors.macos import _select_cpu_temp_keys

    for value in ("0", "true", "yes", ""):
        monkeypatch.setenv("TRCC_NEXT_APPLE_SILICON_SMC", value)
        keys = _select_cpu_temp_keys()
        assert set(keys) == set(INTEL_CPU_TEMP_KEYS), \
            f"env={value!r} unexpectedly enabled Apple Silicon"


# =========================================================================
# Factory composes the chain correctly
# =========================================================================


def test_build_macos_sensors_does_not_crash_on_non_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMC client open() returns False on Linux; chain falls through to psutil."""
    from trcc.adapters.sensors import macos as factory

    monkeypatch.delenv("TRCC_NEXT_APPLE_SILICON_SMC", raising=False)
    sensors = factory.build_macos_sensors()
    cpu = sensors.cpu()
    # psutil always provides usage; verify chain falls through cleanly
    assert cpu.usage() is not None


def test_apple_silicon_log_message_when_enabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Explicit log line when AS keys are turned on — operator-visible."""
    import logging

    from trcc.adapters.sensors import macos as factory

    monkeypatch.setenv("TRCC_NEXT_APPLE_SILICON_SMC", "1")
    with caplog.at_level(logging.INFO, logger="trcc.adapters.sensors.macos"):
        factory.build_macos_sensors()

    assert any("Apple Silicon keys ENABLED" in r.message for r in caplog.records)


# ── Apple Silicon key tables are non-empty (catch accidental deletions) ─


def test_apple_silicon_cpu_key_table_is_populated() -> None:
    assert len(APPLE_SILICON_CPU_TEMP_KEYS) >= 50      # sanity floor


def test_apple_silicon_gpu_key_table_is_populated() -> None:
    assert len(APPLE_SILICON_GPU_TEMP_KEYS) >= 10
