#!/usr/bin/env python3
"""Two-registry chain smoke — PLATFORMS + DEVICES dispatch.

The cutover unified legacy's separate Protocol + Device layers into one
``Device`` ABC, so the chain is two factories, not three:

  * ``current_platform()`` picks the OS-appropriate ``Platform``
    subclass, registered by ``class XPlatform(BaseOS, key=...)``.
  * ``DEVICES[wire]`` returns the ``Device`` subclass for a wire,
    registered by ``class XLcd(BaseDevice, wire=Wire.X)``.

No real USB activity — this asserts registry population + dispatch only,
so it runs anywhere ``ruff + pyright`` does.  Add a check when you add a
new OS or wire registration.

Run:
    PYTHONPATH=src python dev/smoke_factories.py

Exit code 0 on full green, 1 on any divergence.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    from trcc.adapters.device import DEVICES
    from trcc.adapters.device.bulk_lcd import BulkLcd
    from trcc.adapters.device.hid_lcd import HidLcd
    from trcc.adapters.device.led import Led
    from trcc.adapters.device.ly_lcd import LyLcd
    from trcc.adapters.device.scsi_lcd import ScsiLcd
    from trcc.adapters.system import PLATFORMS, current_platform
    from trcc.core.errors import DeviceNotFoundError
    from trcc.core.models import Wire
    from trcc.core.ports import Device, Platform

    failures: list[str] = []

    # 1. PLATFORMS registry has all four OS keys.
    expected_os = {"linux", "win32", "darwin", "bsd"}
    actual_os = set(PLATFORMS)
    if actual_os != expected_os:
        failures.append(
            f"PLATFORMS registry = {sorted(actual_os)} "
            f"(expected {sorted(expected_os)})",
        )
    else:
        print(f"OK  PLATFORMS registry → {sorted(actual_os)}")

    # 2. current_platform() builds a Platform subclass for THIS OS.
    real_platform = current_platform()
    if not isinstance(real_platform, Platform):
        failures.append(
            f"current_platform() → {type(real_platform).__name__} "
            "is not a Platform subclass",
        )
    else:
        print(f"OK  current_platform() → {type(real_platform).__name__}")

    # 3. DEVICES registry binds every wire to the right class.
    expected_device: dict[Wire, type[Device]] = {
        Wire.SCSI: ScsiLcd,
        Wire.HID: HidLcd,
        Wire.BULK: BulkLcd,
        Wire.LY: LyLcd,
        Wire.LED: Led,
    }
    for wire, expected_cls in expected_device.items():
        try:
            actual = DEVICES[wire]
        except DeviceNotFoundError as e:
            failures.append(f"DEVICES[{wire.value}] raised {e}")
            continue
        if actual is not expected_cls:
            failures.append(
                f"DEVICES[{wire.value}] → {actual.__name__} "
                f"(expected {expected_cls.__name__})",
            )
        else:
            print(f"OK  DEVICES[{wire.value}] → {actual.__name__}")

    # 4. lookup raises (not returns None) on an unregistered wire.
    class _FakeWire:
        value = "nonexistent"

    try:
        DEVICES[_FakeWire()]  # type: ignore[arg-type]
    except DeviceNotFoundError:
        print("OK  DEVICES[unregistered] → DeviceNotFoundError")
    else:
        failures.append(
            "DEVICES[unregistered] did not raise "
            "DeviceNotFoundError",
        )

    if failures:
        print()
        for line in failures:
            print(f"FAIL  {line}")
        print(f"\n{len(failures)} check(s) failed.")
        return 1
    print("\nAll factory-chain checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
