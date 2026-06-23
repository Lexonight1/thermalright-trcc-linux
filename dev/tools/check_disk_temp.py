"""Verify the disk/SSD-temperature sensor end to end on THIS machine.

Drives the real composition path — ``PlatformFactory.current().sensors()`` — so
it reports exactly what the app would render, then prints the per-drive detail
for the active OS backend:

  * Linux   — hwmon ``nvme`` / ``drivetemp`` nodes.
  * Windows — the LibreHardwareMonitor ``Storage`` node.  **Start
    LibreHardwareMonitor.exe (run as Administrator) first** — LHM needs
    elevation to publish drive temperatures, and a source checkout ships no
    bundled exe to auto-spawn.

Usage (from the repo root):

    python dev/tools/check_disk_temp.py

Exit code 0 = a live ``disk:temp`` was read; 1 = none (see the printed notes).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trcc.adapters.system import PlatformFactory


def main() -> int:
    print(f"OS: {sys.platform}\n")

    sensors = PlatformFactory.current().sensors()
    readings = sensors.read_all()          # one poll
    disk_readings = {k: v for k, v in readings.items() if k.startswith("disk:")}
    snap = sensors.snapshot()

    print(f"disk:* readings          : {disk_readings or '(none)'}")
    print(f"disk:temp                : {readings.get('disk:temp')}")
    print(f"HardwareMetrics.disk_temp: {snap.disk_temp}\n")

    if sys.platform == "win32":
        from trcc.adapters.sensors._lhm import (
            _probe_wmi_namespace,
            discover_lhm_disks,
        )
        ns = _probe_wmi_namespace()
        print(f"LHM WMI namespace available: {ns is not None}")
        if ns is None:
            print("  → LibreHardwareMonitor is not running (or not elevated). "
                  "Start LibreHardwareMonitor.exe as Administrator and retry.")
        disks = discover_lhm_disks()
        if not disks:
            print("  → No LHM 'Storage' drives discovered.")
        for d in disks:
            print(f"  {d.key:22} {d.name:28} temp={d.temp()}")
        print()

    if readings.get("disk:temp"):
        print("PASS — disk:temp is live; SSD temperature reaches HardwareMetrics.")
        return 0
    print("NO disk:temp — the sensor produced no reading (see notes above).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
