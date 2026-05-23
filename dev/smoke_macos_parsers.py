#!/usr/bin/env python3
"""macOS sensor parsing smoke — runs on Linux via DI seams.

The real ``dev/smoke_macos.py`` requires Apple hardware.  This harness
covers the *parsing + heuristic* logic of the macOS sensor port using
canned inputs:

- ``powermetrics`` plist parser → expected dict of metrics
- HID thermal-name heuristic → expected CPU / GPU pick from a sample
  ``[(product_name, value), …]`` list
- ``_PowermetricsSnapshot`` + ``_HidSnapshot`` caching + TTL behavior
- ``_decode_fan_rpm_raw`` for the Apple Silicon ``flt``/``fpe2``
  typing dance on ``F{i}Ac`` keys
- ``discover_smc_fans`` end-to-end via an in-memory SmcClientPort fake

Run::

    PYTHONPATH=src python3 dev/smoke_macos_parsers.py

Exit 0 = all assertions passed, 1 = first failure.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


# ── canned fixtures ──────────────────────────────────────────────────


_PLIST_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>processor</key>
    <dict>
        <key>cpu_power</key><real>4280.0</real>
        <key>gpu_power</key><real>3100.5</real>
        <key>ane_power</key><real>120.0</real>
        <key>combined_power</key><real>7500.0</real>
        <key>clusters</key>
        <array>
            <dict>
                <key>cpus</key>
                <array>
                    <dict><key>freq_hz</key><real>3200000000.0</real></dict>
                    <dict><key>freq_hz</key><real>3220000000.0</real></dict>
                </array>
            </dict>
        </array>
    </dict>
    <key>gpu</key>
    <dict>
        <key>dvfm_states</key>
        <array>
            <dict><key>used_ratio</key><real>0.15</real></dict>
            <dict><key>used_ratio</key><real>0.05</real></dict>
        </array>
        <key>freq_hz</key><integer>1200</integer>
    </dict>
</dict>
</plist>"""


_HID_PAIRS_AS = [
    ("PMU tdev1", 47.5),
    ("CPU Performance Core 0", 62.0),
    ("CPU Performance Core 1", 64.0),
    ("CPU Efficiency Core 0", 41.0),
    ("GPU 0", 58.0),
    ("Graphics Memory", 49.0),
    ("ANE", 38.0),
]


# ── tests ────────────────────────────────────────────────────────────


def test_plist_parser() -> None:
    from trcc.adapters.sensors._powermetrics import parse_powermetrics_plist

    parsed = parse_powermetrics_plist(_PLIST_SAMPLE)
    assert parsed is not None, "parse_powermetrics_plist returned None"
    assert parsed.get("cpu_power") == 4.28, parsed
    assert parsed.get("gpu_power") == 3.1005, parsed
    assert parsed.get("ane_power") == 0.12, parsed
    assert parsed.get("combined_power") == 7.5, parsed
    assert parsed.get("gpu_busy") == 20.0, parsed   # 0.15 + 0.05 → 20%
    assert parsed.get("gpu_clock") == 1200.0, parsed
    assert parsed.get("cpu_freq") == 3220.0, parsed
    print("  ✓ plist parser: 7 metrics parsed correctly")


def test_powermetrics_snapshot_ttl() -> None:
    from trcc.adapters.sensors._powermetrics import _PowermetricsSnapshot

    calls: list[str] = []

    def fake_clock() -> float:
        return 0.0 if len(calls) < 2 else 5.0

    def fake_fetcher(samplers: str) -> bytes:
        calls.append(samplers)
        return _PLIST_SAMPLE

    snap = _PowermetricsSnapshot(
        fetcher=fake_fetcher, ttl_seconds=1.0, clock=fake_clock,
    )

    assert snap.get("cpu_power") == 4.28
    assert snap.get("gpu_power") == 3.1005
    # Second read inside the TTL: clock still 0.0 (calls=1 after first
    # refresh, second .get sees clock=5.0 due to fake_clock branching).
    # We're testing that within the same TTL window only ONE refresh
    # happens, so call this twice with the clock pinned and check.
    pinned_calls: list[str] = []

    def pinned_fetcher(samplers: str) -> bytes:
        pinned_calls.append(samplers)
        return _PLIST_SAMPLE

    snap2 = _PowermetricsSnapshot(
        fetcher=pinned_fetcher, ttl_seconds=10.0, clock=lambda: 0.0,
    )
    snap2.get("cpu_power")
    snap2.get("gpu_power")
    snap2.get("cpu_freq")
    assert len(pinned_calls) == 1, pinned_calls
    print("  ✓ powermetrics snapshot caches within TTL "
          "(1 fetch for 3 reads)")


def test_hid_cpu_gpu_heuristic() -> None:
    from trcc.adapters.sensors._macos_hid import _HidSnapshot

    snap = _HidSnapshot(reader=lambda: list(_HID_PAIRS_AS), ttl_seconds=10.0)
    cpu = snap.cpu_temp()
    gpu = snap.gpu_temp()
    # "Performance Core" pattern wins for CPU → max of 62/64 → 64.0
    assert cpu == 64.0, cpu
    # GPU priority order is "graphics" > "gpu" > "gddr" > "grfx".
    # "Graphics Memory" matches "graphics" before "GPU 0" sees "gpu"
    # — this mirrors legacy's pattern-priority heuristic verbatim.
    assert gpu == 49.0, gpu
    print("  ✓ HID heuristic: CPU=64.0°C (perf-core max), "
          "GPU=49.0°C (graphics-mem matches first)")


def test_hid_snapshot_dedup_via_reader() -> None:
    from trcc.adapters.sensors._macos_hid import _HidSnapshot

    calls = {"n": 0}

    def reader() -> list[tuple[str, float]]:
        calls["n"] += 1
        return list(_HID_PAIRS_AS)

    snap = _HidSnapshot(reader=reader, ttl_seconds=10.0, clock=lambda: 0.0)
    snap.cpu_temp()
    snap.gpu_temp()
    snap.cpu_temp()
    assert calls["n"] == 1, calls
    print("  ✓ HID snapshot caches: 1 read serves 3 metric queries")


def test_fan_rpm_decoder() -> None:
    from trcc.adapters.sensors._smc import _decode_fan_rpm_raw

    # ``flt`` LE float = 1300 RPM
    flt_payload = struct.pack("<f", 1300.0)
    flt_type = struct.unpack(">I", b"flt ")[0]
    rpm = _decode_fan_rpm_raw(flt_type, 4, flt_payload + b"\x00" * 28)
    assert rpm is not None and 1299.0 < rpm < 1301.0, rpm

    # ``fpe2`` big-endian uint16 / 4: encode 1200 RPM → 4800
    fpe2_payload = struct.pack(">H", 4800)
    fpe2_type = struct.unpack(">I", b"fpe2")[0]
    rpm = _decode_fan_rpm_raw(fpe2_type, 2, fpe2_payload + b"\x00" * 30)
    assert rpm == 1200.0, rpm

    # Implausible flt → falls back to fpe2 reading
    bad_flt = struct.pack("<f", 99999.0)
    fpe2_after = struct.pack(">H", 6800)
    rpm = _decode_fan_rpm_raw(flt_type, 4, fpe2_after + bad_flt[2:])
    assert rpm == 1700.0, rpm   # 6800 / 4

    print("  ✓ fan RPM decode: flt + fpe2 + flt-with-fpe2-fallback")


def test_discover_smc_fans_via_fake_client() -> None:
    from trcc.adapters.sensors.macos import SmcFan, discover_smc_fans

    class FakeSmc:
        connected = True
        opened = False

        def open(self) -> bool:
            self.opened = True
            return True

        def close(self) -> None:
            self.opened = False

        def read_key_float(self, key: str) -> float | None:
            return None

        def read_key_uint32(self, key: str) -> int | None:
            return 2 if key == "FNum" else None

        def read_fan_rpm(self, key: str) -> float | None:
            return {"F0Ac": 1300.0, "F1Ac": 2100.0}.get(key)

    fans = discover_smc_fans(FakeSmc())
    assert len(fans) == 2, fans
    assert all(isinstance(f, SmcFan) for f in fans), fans
    assert fans[0].rpm() == 1300, fans[0].rpm()
    assert fans[1].rpm() == 2100, fans[1].rpm()
    assert fans[0].key == "smc:fan0", fans[0].key
    assert fans[1].name == "Apple SMC Fan 1", fans[1].name
    print("  ✓ discover_smc_fans: 2 fans materialised from FNum=2")


def main() -> int:
    tests = [
        ("powermetrics plist parser", test_plist_parser),
        ("powermetrics snapshot TTL", test_powermetrics_snapshot_ttl),
        ("HID CPU/GPU heuristic", test_hid_cpu_gpu_heuristic),
        ("HID snapshot caching", test_hid_snapshot_dedup_via_reader),
        ("SMC fan RPM decoder", test_fan_rpm_decoder),
        ("SMC fan discovery", test_discover_smc_fans_via_fake_client),
    ]
    print(f"macOS parser smoke (Linux-side) — {len(tests)} test(s)")
    print("-" * 60)
    for label, fn in tests:
        print(f"[{label}]")
        try:
            fn()
        except AssertionError as e:
            print(f"  ✗ FAIL: {e}")
            return 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ ERROR: {type(e).__name__}: {e}")
            return 1
    print("-" * 60)
    print("All macOS parser smoke checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
