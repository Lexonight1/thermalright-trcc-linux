"""BSD Platform — single file, single class, all BSD logic.

Everything the app needs from the OS lives on BSDPlatform.
No intermediate classes, no OS names leaking out.
Private helpers and a SensorEnumerator (lifecycle needs its own class)
are scoped to this file.
"""
from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trcc.core.models import UsbAddress

from trcc.adapters.system._base import SUBPROCESS_EXC, SensorEnumeratorBase
from trcc.adapters.system._shared import (
    _confirm,
    _copy_assets_to_user_dir,
    _posix_acquire_instance_lock,
    _posix_raise_existing_instance,
    _posix_wire_ipc_raise,
    _print_summary,
)
from trcc.core.models import SensorInfo
from trcc.core.ports import (
    AutostartManager,
    DoctorPlatformConfig,
    Metrics,
    Platform,
    ReportPlatformConfig,
)

if TYPE_CHECKING:
    from trcc.core.ports import SensorEnumerator as _SensorEnumeratorABC

log = logging.getLogger(__name__)


# =========================================================================
# Private constants
# =========================================================================

_AUTOSTART_DIR = Path.home() / '.config' / 'autostart'
_AUTOSTART_FILE = _AUTOSTART_DIR / 'trcc-linux.desktop'


# =========================================================================
# Private helper functions
# =========================================================================

def _autostart_desktop_entry() -> str:
    """Generate XDG .desktop autostart content."""
    exec_path = AutostartManager.get_exec()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=TRCC Linux\n"
        "Comment=Thermalright LCD Control Center\n"
        f"Exec={exec_path} gui --resume\n"
        "Icon=trcc\n"
        "Terminal=false\n"
        "Categories=Utility;System;\n"
        "StartupWMClass=trcc-linux\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _sysctl(key: str) -> str:
    """Read a single sysctl value. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ['sysctl', '-n', key],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except SUBPROCESS_EXC as e:
        log.debug("sysctl -n %s returned %s", key, type(e).__name__)
    return ''


def get_memory_info() -> list[dict[str, str]]:
    """Get memory info via sysctl.

    FreeBSD doesn't expose per-DIMM details via sysctl like dmidecode.
    Returns a single entry with total memory from hw.physmem.
    """
    slots: list[dict[str, str]] = []

    if (physmem := _sysctl('hw.physmem')):
        try:
            total_bytes = int(physmem)
            total_gb = total_bytes / (1024 ** 3)
            slots.append({
                'manufacturer': 'Unknown',
                'part_number': '',
                'type': (_sysctl('dev.cpu.0.freq') and 'DDR') or 'Unknown',
                'speed': 'Unknown',
                'size': f'{total_gb:.0f} GB',
                'form_factor': 'Unknown',
                'locator': 'Total',
            })
        except (ValueError, TypeError):
            pass

    if not slots:
        import psutil
        mem = psutil.virtual_memory()
        slots.append({
            'manufacturer': 'Unknown',
            'part_number': '',
            'type': 'Unknown',
            'speed': 'Unknown',
            'size': f'{mem.total // (1024 ** 3)} GB',
            'form_factor': 'Unknown',
            'locator': 'Total',
        })

    return slots


def get_disk_info() -> list[dict[str, str]]:
    """Get disk info via geom disk list.

    Parses `geom disk list` output for name, size, and description.
    """
    disks: list[dict[str, str]] = []

    try:
        result = subprocess.run(
            ['geom', 'disk', 'list'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []

        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            line = line.strip()

            if line.startswith('Geom name:'):
                if current.get('name'):
                    disks.append(current)
                current = {'name': line.split(':', 1)[1].strip()}

            elif line.startswith('descr:'):
                current['model'] = line.split(':', 1)[1].strip()

            elif line.startswith('Mediasize:'):
                raw = line.split(':', 1)[1].strip()
                match = re.search(r'\(([^)]+)\)', raw)
                if match:
                    current['size'] = match.group(1)
                else:
                    parts = raw.split()
                    if parts:
                        try:
                            b = int(parts[0])
                            if b >= 1024 ** 4:
                                current['size'] = f'{b / (1024 ** 4):.1f} TB'
                            elif b >= 1024 ** 3:
                                current['size'] = f'{b / (1024 ** 3):.0f} GB'
                        except (ValueError, TypeError):
                            current['size'] = raw

            elif line.startswith('rotationrate:'):
                rate = line.split(':', 1)[1].strip()
                current['type'] = 'HDD' if rate != '0' else 'SSD'

        if current.get('name'):
            disks.append(current)

        for d in disks:
            d.setdefault('type', 'Unknown')
            d.setdefault('model', '')
            d.setdefault('size', '')
            d.setdefault('health', 'Unknown')

    except SUBPROCESS_EXC as e:
        log.debug("geom disk list returned %s", type(e).__name__)

    return disks


# =========================================================================
# SensorEnumerator — file-scoped, no OS prefix
# =========================================================================

class SensorEnumerator(SensorEnumeratorBase):
    """BSD hardware sensor discovery and reading.

    Sources: sysctl, pynvml, psutil.
    """

    def discover(self) -> list[SensorInfo]:
        self._sensors.clear()
        self._discover_psutil_base()
        self._discover_sysctl()
        self._discover_nvidia()
        self._discover_computed()
        return self._sensors

    # ── BSD-specific discovery ────────────────────────────────────────

    def _discover_sysctl(self) -> None:
        """Discover CPU temp sensors via sysctl dev.cpu.*.temperature."""
        try:
            result = subprocess.run(
                ['sysctl', '-a'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return

            for line in result.stdout.splitlines():
                if 'dev.cpu.' in line and '.temperature' in line:
                    match = re.match(r'dev\.cpu\.(\d+)\.temperature', line)
                    if match:
                        cpu_id = match.group(1)
                        self._sensors.append(SensorInfo(
                            f'sysctl:cpu{cpu_id}_temp',
                            f'CPU Core {cpu_id} Temp',
                            'temperature', '°C', 'sysctl',
                        ))

                if 'hw.acpi.thermal.tz' in line and '.temperature' in line:
                    match = re.match(r'hw\.acpi\.thermal\.tz(\d+)\.temperature', line)
                    if match:
                        tz_id = match.group(1)
                        self._sensors.append(SensorInfo(
                            f'sysctl:tz{tz_id}_temp',
                            f'ACPI Thermal Zone {tz_id}',
                            'temperature', '°C', 'sysctl',
                        ))

        except SUBPROCESS_EXC as e:
            log.debug("sysctl -a (sensor discovery) returned %s", type(e).__name__)

    # ── BSD-specific polling ──────────────────────────────────────────

    def _poll_platform(self, readings: dict[str, float]) -> None:
        self._poll_sysctl(readings)

    def _poll_sysctl(self, readings: dict[str, float]) -> None:
        """Read CPU temps via sysctl."""
        try:
            result = subprocess.run(
                ['sysctl', '-a'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return

            for line in result.stdout.splitlines():
                if 'dev.cpu.' in line and '.temperature' in line:
                    match = re.match(
                        r'dev\.cpu\.(\d+)\.temperature:\s*([\d.]+)', line,
                    )
                    if match:
                        cpu_id = match.group(1)
                        readings[f'sysctl:cpu{cpu_id}_temp'] = float(match.group(2))

                if 'hw.acpi.thermal.tz' in line and '.temperature' in line:
                    match = re.match(
                        r'hw\.acpi\.thermal\.tz(\d+)\.temperature:\s*([\d.]+)', line,
                    )
                    if match:
                        tz_id = match.group(1)
                        readings[f'sysctl:tz{tz_id}_temp'] = float(match.group(2))

        except SUBPROCESS_EXC as e:
            log.debug("sysctl -a (temperature poll) returned %s", type(e).__name__)

    # ── BSD-specific mapping ──────────────────────────────────────────

    def _build_mapping(self) -> dict[str, str]:
        sensors = self._sensors
        _ff = self._find_first
        mapping: dict[str, str] = {}
        self._map_common(mapping)

        mapping['cpu_temp'] = (
            _ff(sensors, source='sysctl', name_contains='Core 0', category='temperature')
            or _ff(sensors, source='sysctl', category='temperature')
        )

        mapping['gpu_temp'] = _ff(sensors, source='nvidia', category='temperature')
        mapping['gpu_usage'] = _ff(sensors, source='nvidia', category='gpu_busy')
        mapping['gpu_power'] = _ff(sensors, source='nvidia', category='power')

        mapping['mem_temp'] = ''

        self._map_fans(mapping, fan_sources=('nvidia',))

        return mapping


# =========================================================================
# BSDPlatform — THE one class
# =========================================================================

class BSDMetrics(Metrics):
    """BSD HardwareMetrics builder — composes its sources in __init__.

    Today: leans on the BSD :class:`SensorEnumerator` which composes
    ``sysctl hw.sensors`` + ``dev.cpu.N.temperature`` + psutil.  Future
    ``_read_*`` methods slot in as new BSD probes are added.
    """

    def __init__(self, enumerator: _SensorEnumeratorABC) -> None:
        super().__init__()
        self._enumerator = enumerator
        self._read_datetime()
        self._read_sensors(enumerator)
        # Future per-OS reads slot in here.


class BSDPlatform(Platform):
    """BSD Platform — all OS logic inline, no intermediaries."""

    def __init__(self) -> None:
        super().__init__()

    # ── Metrics ───────────────────────────────────────────────

    def _make_metrics(self) -> Metrics:
        return BSDMetrics(self.sensors)

    # ── Sensor factory ───────────────────────────────────────

    def _make_sensor_enumerator(self) -> SensorEnumerator:
        return SensorEnumerator()

    # ── Hardware discovery ────────────────────────────────────

    def detect_devices(self):
        from trcc.adapters.device.detector import DeviceDetector
        return DeviceDetector.make_detect_fn(scsi_resolver=None)()

    # ── Transport creation ────────────────────────────────────

    def create_scsi_transport(self, path: str,
                              vid: int = 0, pid: int = 0,
                              *, usb_address: UsbAddress | None = None) -> Any:
        from trcc.adapters.device.bsd.scsi import BSDScsiTransport
        from trcc.core.models import UsbAddress as _UsbAddress
        # BSD USB BOT binds by (bus, address). Prefer the explicit kwarg
        # threaded from DeviceInfo; fall back to parsing path for legacy callers.
        bound = usb_address or _UsbAddress.parse(path)
        return BSDScsiTransport(vid, pid, usb_address=bound)

    # ── Directories ───────────────────────────────────────────

    def resolve_assets_dir(self, pkg_dir: Any) -> Any:
        return _copy_assets_to_user_dir(pkg_dir)

    # ── Autostart (XDG .desktop — same as Linux) ─────────────

    def autostart_enable(self) -> None:
        _AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        _AUTOSTART_FILE.write_text(_autostart_desktop_entry())
        log.info("Autostart enabled: %s", _AUTOSTART_FILE)

    def autostart_disable(self) -> None:
        if _AUTOSTART_FILE.exists():
            _AUTOSTART_FILE.unlink()
        log.info("Autostart disabled")

    def autostart_enabled(self) -> bool:
        return _AUTOSTART_FILE.exists()

    def acquire_instance_lock(self) -> object | None:
        return _posix_acquire_instance_lock(self.config_dir())

    def raise_existing_instance(self) -> None:
        _posix_raise_existing_instance(self.config_dir())

    def _screen_capture_format(self) -> str | None:
        return 'x11grab'

    def wire_ipc_raise(self, app: Any, window: Any) -> None:
        _posix_wire_ipc_raise(app, window)

    # ── Administration ────────────────────────────────────────

    def get_pkg_manager(self) -> str | None:
        return 'pkg' if shutil.which('pkg') else None

    def check_deps(self) -> list:
        from trcc.adapters.infra.doctor import check_system_deps
        return check_system_deps(self.get_pkg_manager())

    def install_rules(self) -> int:
        return 1  # BSD: no rules to install

    def check_permissions(self, devices: list) -> list[str]:
        return []

    def get_system_files(self) -> list[str]:
        return []

    # ── Identity ──────────────────────────────────────────────

    def distro_name(self) -> str:
        return f"FreeBSD {platform.release()}"

    def no_devices_hint(self) -> str | None:
        return None

    def doctor_config(self) -> DoctorPlatformConfig:
        return DoctorPlatformConfig(
            distro_name=self.distro_name(),
            pkg_manager=self.get_pkg_manager(),
            check_libusb=True,
            extra_binaries=[],
            run_gpu_check=False,
            run_udev_check=False,
            run_selinux_check=False,
            run_rapl_check=False,
            run_polkit_check=False,
            run_winusb_check=False,
            enable_ansi=False,
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
        from trcc.adapters.infra.doctor import check_system_deps

        pm = self.get_pkg_manager()
        print(f"\n  TRCC Setup — {self.distro_name()}\n")
        actions: list[str] = []

        # Step 1/1: System dependencies
        print("  Step 1/1: System dependencies")
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
                        result = subprocess.run(dep.install_cmd.split())
                        if result.returncode == 0:
                            actions.append(f"Installed: {dep.name}")
                        else:
                            print(f"    [!!] Install failed (exit {result.returncode})")
            else:
                note = f" ({dep.note})" if dep.note else ""
                print(f"    [--]  {dep.name} — not installed{note}")
        print()

        _print_summary(actions)
        return 0

    # ── Help text ─────────────────────────────────────────────

    def archive_tool_install_help(self) -> str:
        return (
            "7z not found. Install via pkg:\n"
            "  sudo pkg install p7zip"
        )

    def ffmpeg_install_help(self) -> str:
        return "ffmpeg not found. Install:\n  sudo pkg install ffmpeg"

    # ── Hardware info ─────────────────────────────────────────

    def get_memory_info(self) -> list[dict[str, str]]:
        return get_memory_info()

    def get_disk_info(self) -> list[dict[str, str]]:
        return get_disk_info()
