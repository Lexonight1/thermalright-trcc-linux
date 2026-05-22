"""Windows Platform — single class with a strategy-chain sensor enumerator.

Sensor enumeration moved out to ``trcc/adapters/system/windows/`` as a
sub-package; this file owns the ``WindowsPlatform`` adapter only.  Add
new sensor sources by dropping a module under ``windows/sources/``
decorated with ``@WindowsSensorSource.register('key')`` — zero
touchpoints here.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

if TYPE_CHECKING:
    from trcc.legacy.adapters.system.windows.enumerator import WindowsSensorEnumerator
    from trcc.legacy.core.models import UsbAddress

from trcc.legacy.adapters.system._shared import (
    _confirm,
    _copy_assets_to_user_dir,
    _print_summary,
)
from trcc.legacy.core.ports import (
    DoctorPlatformConfig,
    Metrics,
    Platform,
    ReportPlatformConfig,
)

if TYPE_CHECKING:
    from trcc.legacy.core.ports import SensorEnumerator as _SensorEnumeratorABC

log = logging.getLogger(__name__)


# =========================================================================
# Private constants
# =========================================================================

_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_REG_VALUE = 'TRCC Linux'


# =========================================================================
# Private helper functions
# =========================================================================

def _winreg() -> Any:
    """Return winreg stdlib module."""
    import winreg  # pyright: ignore[reportMissingImports]
    return winreg


def _format_size(size_bytes: str | int | None) -> str:
    if not size_bytes:
        return ''
    try:
        b = int(size_bytes)
        if b >= 1024 ** 4:
            return f'{b / (1024 ** 4):.1f} TB'
        if b >= 1024 ** 3:
            return f'{b / (1024 ** 3):.0f} GB'
        if b >= 1024 ** 2:
            return f'{b / (1024 ** 2):.0f} MB'
        return f'{b} B'
    except (ValueError, TypeError):
        return str(size_bytes)


def _memory_form_factor(code: int | None) -> str:
    factors = {8: 'DIMM', 12: 'SODIMM', 13: 'RIMM', 15: 'FB-DIMM', 16: 'Die'}
    return factors.get(code or 0, 'Unknown')


def _memory_type(code: int | None) -> str:
    types = {20: 'DDR', 21: 'DDR2', 24: 'DDR3', 26: 'DDR4', 30: 'LPDDR4', 34: 'DDR5', 35: 'LPDDR5'}
    return types.get(code or 0, 'Unknown')


def _disk_type(disk: object) -> str:
    model = (getattr(disk, 'Model', '') or '').upper()
    media_type = (getattr(disk, 'MediaType', '') or '').upper()
    if 'SSD' in model or 'NVME' in model or 'SOLID' in media_type:
        return 'SSD'
    if 'HDD' in model or 'FIXED' in media_type:
        return 'HDD'
    return 'Unknown'


def _get_disk_health(device_id: str | None) -> str:
    if not device_id:
        return 'Unknown'
    try:
        from ._windows_wmi import wmi_handle
        w = wmi_handle(namespace='root\\WMI')
        for status in w.MSStorageDriver_FailurePredictStatus():
            if status.Active:
                return 'FAILED' if status.PredictFailure else 'PASSED'
    except ImportError:
        log.debug("wmi package unavailable for disk health probe")
    except Exception as e:
        # WMI/COM exceptions don't share a common Python base — keep broad, log.
        log.debug("WMI MSStorageDriver_FailurePredictStatus(%s) returned %s", device_id, type(e).__name__)
    return 'Unknown'


def get_memory_info() -> list[dict[str, str]]:
    """Get DRAM slot info via WMI Win32_PhysicalMemory."""
    slots: list[dict[str, str]] = []
    try:
        from ._windows_wmi import wmi_handle
        w = wmi_handle()
        for mem in w.Win32_PhysicalMemory():
            slot: dict[str, str] = {}
            slot['manufacturer'] = (mem.Manufacturer or '').strip()
            slot['part_number'] = (mem.PartNumber or '').strip()
            slot['speed'] = str(mem.ConfiguredClockSpeed or mem.Speed or '')
            slot['configured_memory_speed'] = str(mem.ConfiguredClockSpeed or '')
            slot['size'] = _format_size(mem.Capacity)
            slot['form_factor'] = _memory_form_factor(mem.FormFactor)
            slot['type'] = _memory_type(mem.SMBIOSMemoryType)
            slot['locator'] = mem.DeviceLocator or ''
            slot['rank'] = str(mem.Rank or '')
            slot['data_width'] = str(mem.DataWidth or '')
            slot['total_width'] = str(mem.TotalWidth or '')
            if slot['size'] and slot['size'] != '0':
                slots.append(slot)
    except ImportError:
        log.debug("wmi package not available — using psutil fallback")
        mem = psutil.virtual_memory()
        slots.append({
            'manufacturer': 'Unknown', 'part_number': 'Unknown',
            'type': 'Unknown', 'speed': 'Unknown',
            'size': f'{mem.total // (1024 ** 3)} GB',
            'form_factor': 'Unknown', 'locator': 'Total',
        })
    except Exception:
        log.exception("WMI memory query failed")
    return slots


def get_disk_info() -> list[dict[str, str]]:
    """Get physical disk info via WMI Win32_DiskDrive."""
    disks: list[dict[str, str]] = []
    try:
        from ._windows_wmi import wmi_handle
        w = wmi_handle()
        for disk in w.Win32_DiskDrive():
            info: dict[str, str] = {}
            info['name'] = disk.DeviceID or ''
            info['model'] = (disk.Model or '').strip()
            info['size'] = _format_size(disk.Size)
            info['type'] = _disk_type(disk)
            info['health'] = _get_disk_health(disk.DeviceID)
            disks.append(info)
    except ImportError:
        log.debug("wmi package not available")
    except Exception:
        log.exception("WMI disk query failed")
    return disks


# =========================================================================
# WindowsPlatform — THE one class
# =========================================================================

class WindowsMetrics(Metrics):
    """Windows HardwareMetrics builder — composes its sources in __init__.

    Today: leans on :class:`WindowsSensorEnumerator` which already chains
    HWiNFO64 + LibreHardwareMonitor + MSAcpi + psutil + pynvml.  Future
    growth slots cleanly here as new ``_read_*`` methods called from
    __init__ — ADLX for AMD, IntelL0 for Intel, registry reads, WMI
    one-shots — without touching the enumerator or Platform.
    """

    def __init__(self, enumerator: _SensorEnumeratorABC) -> None:
        super().__init__()
        self._enumerator = enumerator
        # __init__ IS the build.  Each _read_* call fills self.record.
        self._read_datetime()           # base ABC helper
        self._read_sensors(enumerator)  # base ABC helper — walks WindowsSensorEnumerator chain
        # Future per-OS reads slot in here:
        #   self._read_adlx()       — AMD GPU via ADLX driver
        #   self._read_intel_l0()   — Intel GPU via Level Zero
        #   self._read_wmi_oneshots()  — Win32 namespace one-offs


class WindowsPlatform(Platform):
    """Windows Platform — sensor work delegated to ``windows.WindowsSensorEnumerator``."""

    def __init__(self) -> None:
        super().__init__()

    # ── Metrics ───────────────────────────────────────────────

    def _make_metrics(self) -> Metrics:
        return WindowsMetrics(self.sensors)

    # ── Sensor enumerator ─────────────────────────────────────

    def _make_sensor_enumerator(self) -> WindowsSensorEnumerator:
        from trcc.legacy.adapters.system.windows.enumerator import (
            WindowsSensorEnumerator,
        )
        return WindowsSensorEnumerator()

    # ── Device detection ──────────────────────────────────────

    def detect_devices(self):
        from trcc.legacy.adapters.device.windows.detector import WindowsDeviceDetector
        return WindowsDeviceDetector.detect()

    # ── Transport creation ────────────────────────────────────

    def create_scsi_transport(self, path: str, vid: int = 0, pid: int = 0,
                              *, usb_address: UsbAddress | None = None) -> Any:
        # DeviceIoControl binds by \\.\PhysicalDriveN — usb_address is irrelevant.
        from trcc.legacy.adapters.device.windows.scsi import WindowsScsiTransport
        return WindowsScsiTransport(path)

    # ── Screen capture ────────────────────────────────────────

    def _screen_capture_format(self) -> str | None:
        return 'gdigrab'

    # ── Directories (override) ────────────────────────────────

    def resolve_assets_dir(self, pkg_dir: Any) -> Any:
        return _copy_assets_to_user_dir(pkg_dir)

    # ── Autostart (winreg) ────────────────────────────────────

    def autostart_enable(self) -> None:
        from trcc.legacy.core.ports import AutostartManager
        wr = _winreg()
        exec_path = AutostartManager.get_exec()
        key = wr.OpenKey(wr.HKEY_CURRENT_USER, _REG_KEY, 0, wr.KEY_SET_VALUE)
        wr.SetValueEx(key, _REG_VALUE, 0, wr.REG_SZ, f'"{exec_path}" gui --resume')
        wr.CloseKey(key)
        log.info("Autostart enabled: HKCU\\%s", _REG_KEY)

    def autostart_disable(self) -> None:
        try:
            wr = _winreg()
            key = wr.OpenKey(wr.HKEY_CURRENT_USER, _REG_KEY, 0, wr.KEY_SET_VALUE)
            wr.DeleteValue(key, _REG_VALUE)
            wr.CloseKey(key)
        except OSError:
            pass
        log.info("Autostart disabled")

    def autostart_enabled(self) -> bool:
        try:
            wr = _winreg()
            key = wr.OpenKey(wr.HKEY_CURRENT_USER, _REG_KEY, 0, wr.KEY_READ)
            wr.QueryValueEx(key, _REG_VALUE)
            wr.CloseKey(key)
            return True
        except OSError:
            return False

    def acquire_instance_lock(self) -> object | None:
        import msvcrt  # pyright: ignore[reportMissingImports]
        lock_path = Path(self.config_dir()) / "trcc-linux.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = open(lock_path, "w")
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]
            fh.write(str(os.getpid()))
            fh.flush()
            return fh
        except OSError:
            return None

    def raise_existing_instance(self) -> None:
        pass  # No SIGUSR1 on Windows

    def configure_dpi(self) -> None:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except (OSError, AttributeError) as e:
            log.debug("SetProcessDpiAwareness returned %s (older Windows or shcore absent)", type(e).__name__)

    def minimize_on_close(self) -> bool:
        return True

    def configure_stdout(self) -> None:
        import io
        import sys as _sys
        if hasattr(_sys.stdout, 'buffer'):
            _sys.stdout = io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')
        if hasattr(_sys.stderr, 'buffer'):
            _sys.stderr = io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace')

    # ── Administration ────────────────────────────────────────

    def get_pkg_manager(self) -> str | None:
        return 'winget' if shutil.which('winget') else None

    def check_deps(self) -> list:
        from trcc.legacy.adapters.infra.doctor import check_system_deps
        return check_system_deps(self.get_pkg_manager())

    def install_rules(self) -> int:
        """Print Zadig-based WinUSB driver installation guide."""
        from trcc.legacy.core.models import BULK_DEVICES, HID_LCD_DEVICES, LED_DEVICES, LY_DEVICES

        winusb_vids: set[tuple[int, int]] = set()
        for registry in (BULK_DEVICES, HID_LCD_DEVICES, LED_DEVICES, LY_DEVICES):
            for vid, pid in registry:
                winusb_vids.add((vid, pid))

        print("\n  TRCC WinUSB Driver Setup\n")
        print("  SCSI devices use the default USB Mass Storage driver — no setup needed.\n")
        print("  HID, Bulk, and LY devices need the WinUSB driver.")
        print("  Install via Zadig (https://zadig.akeo.ie/):\n")
        print("  1. Run Zadig → Options → List All Devices")
        print("  2. Select your Thermalright device")
        print("  3. Set target driver to WinUSB")
        print("  4. Click 'Replace Driver'")
        print("  5. Replug the USB device\n")
        print("  Devices that need WinUSB:")
        for vid, pid in sorted(winusb_vids):
            for registry in (BULK_DEVICES, HID_LCD_DEVICES, LED_DEVICES, LY_DEVICES):
                if (vid, pid) in registry:
                    print(f"    {vid:04X}:{pid:04X}  {registry[(vid, pid)].product}")
                    break
        print()
        return 0

    def check_permissions(self, devices: list) -> list[str]:
        return []

    def get_system_files(self) -> list[str]:
        return []

    # ── Identity ──────────────────────────────────────────────

    def distro_name(self) -> str:
        import platform
        return f"Windows {platform.version()}"

    def no_devices_hint(self) -> str | None:
        return (
            "\nOn Windows, non-SCSI devices (HID, Bulk, LY) need the\n"
            "WinUSB driver. Run 'trcc setup-winusb' for instructions."
        )

    def doctor_config(self) -> DoctorPlatformConfig:
        return DoctorPlatformConfig(
            distro_name=self.distro_name(),
            pkg_manager=self.get_pkg_manager(),
            check_libusb=False,
            extra_binaries=[],
            run_gpu_check=False,
            run_udev_check=False,
            run_selinux_check=False,
            run_rapl_check=False,
            run_polkit_check=False,
            run_winusb_check=True,
            enable_ansi=True,
        )

    def report_config(self) -> ReportPlatformConfig:
        return ReportPlatformConfig(
            distro_name=self.distro_name(),
            collect_lsusb=False,
            collect_udev=False,
            collect_selinux=False,
            collect_rapl=False,
            collect_device_permissions=False,
        )

    # ── Setup operations ──────────────────────────────────────

    def run_setup(self, auto_yes: bool = False) -> int:
        from trcc.legacy.adapters.infra.doctor import check_system_deps

        pm = self.get_pkg_manager()
        print(f"\n  TRCC Setup — {self.distro_name()}\n")
        actions: list[str] = []

        print("  Step 1/2: System dependencies")
        deps = check_system_deps(pm)
        for dep in deps:
            if dep.ok:
                ver = f" {dep.version}" if dep.version else ""
                print(f"    [OK]  {dep.name}{ver}")
            elif dep.required:
                note = f" ({dep.note})" if dep.note else ""
                print(f"    [!!]  {dep.name} — MISSING{note}")
                if dep.install_cmd and pm:
                    if _confirm(f"Install? -> {dep.install_cmd}", auto_yes):
                        result = subprocess.run(dep.install_cmd.split(), capture_output=True)
                        if result.returncode == 0:
                            actions.append(f"Installed: {dep.name}")
                        else:
                            print(f"    [!!] Install failed (exit {result.returncode})")
            else:
                note = f" ({dep.note})" if dep.note else ""
                print(f"    [--]  {dep.name} — not installed{note}")
        print()

        print("  Step 2/2: USB driver")
        print("    SCSI devices use default USB Mass Storage — no setup needed.")
        print("    HID/Bulk/LY devices need WinUSB via Zadig (https://zadig.akeo.ie/).")
        print()

        _print_summary(actions)
        return 0

    # ── Help text ─────────────────────────────────────────────

    def archive_tool_install_help(self) -> str:
        return (
            "7z not found. Install 7-Zip for Windows:\n"
            "  Download from https://7-zip.org/ and install, or run:\n"
            "  winget install 7zip.7zip"
        )

    def ffmpeg_install_help(self) -> str:
        return "ffmpeg not found. Install:\n  winget install Gyan.FFmpeg"

    # ── Hardware info ─────────────────────────────────────────

    def get_memory_info(self) -> list[dict[str, str]]:
        return get_memory_info()

    def get_disk_info(self) -> list[dict[str, str]]:
        return get_disk_info()
