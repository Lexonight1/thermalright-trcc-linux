"""macOS Platform — single file, single class, all macOS logic."""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import logging
import platform
import shutil
import struct
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

if TYPE_CHECKING:
    from trcc.core.models import UsbAddress

from trcc.adapters.system._base import SensorEnumeratorBase
from trcc.adapters.system._shared import (
    _confirm,
    _copy_assets_to_user_dir,
    _posix_acquire_instance_lock,
    _posix_raise_existing_instance,
    _posix_wire_ipc_raise,
    _print_summary,
)
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

# ── Autostart ────────────────────────────────────────────────────────
_LAUNCH_AGENTS_DIR = Path.home() / 'Library' / 'LaunchAgents'
_LAUNCH_AGENT_FILE = _LAUNCH_AGENTS_DIR / 'com.thermalright.trcc.plist'

# ── Apple Silicon detection ──────────────────────────────────────────
IS_APPLE_SILICON = platform.machine() == 'arm64'

# ── IOKit framework bindings ─────────────────────────────────────────
_iokit_path = ctypes.util.find_library('IOKit')
_cf_path = ctypes.util.find_library('CoreFoundation')
_iokit = ctypes.cdll.LoadLibrary(_iokit_path) if _iokit_path else None
_cf = ctypes.cdll.LoadLibrary(_cf_path) if _cf_path else None

if _iokit:
    _iokit.IOServiceMatching.restype = ctypes.c_void_p
    _iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
    _iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
    _iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    _iokit.IOServiceOpen.restype = ctypes.c_int
    _iokit.IOServiceOpen.argtypes = [
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
        ctypes.POINTER(ctypes.c_uint),
    ]
    _iokit.IOConnectCallStructMethod.restype = ctypes.c_int
    _iokit.IOServiceClose.restype = ctypes.c_int
    _iokit.IOServiceClose.argtypes = [ctypes.c_uint]

# ── SMC constants ────────────────────────────────────────────────────
KERNEL_INDEX_SMC = 2
SMC_CMD_READ_KEYINFO = 9
SMC_CMD_READ_BYTES = 5

# SMC key table — Intel + Apple Silicon temperature keys.
# Discovery probes every key; only those returning valid values are registered.
# Apple Silicon keys vary by chip generation — trial-and-error handles this.
# Fans are discovered dynamically via FNum (see _discover_fans).
_SMC_KEYS: dict[str, tuple[str, str, str]] = {
    # Intel CPU temps
    'TC0P': ('CPU Proximity', 'temperature', '°C'),
    'TC0D': ('CPU Die', 'temperature', '°C'),
    'TC0E': ('CPU Core 0', 'temperature', '°C'),
    'TC1C': ('CPU Core 1', 'temperature', '°C'),
    'TC2C': ('CPU Core 2', 'temperature', '°C'),
    'TC3C': ('CPU Core 3', 'temperature', '°C'),
    # Apple Silicon CPU temps (performance cores — common across M1-M4)
    'Tp01': ('CPU P-Core 1', 'temperature', '°C'),
    'Tp02': ('CPU P-Core 2', 'temperature', '°C'),
    'Tp05': ('CPU P-Core 5', 'temperature', '°C'),
    'Tp09': ('CPU P-Core 9', 'temperature', '°C'),
    'Tp0T': ('CPU Package', 'temperature', '°C'),
    # Intel GPU temps
    'TG0P': ('GPU Proximity', 'temperature', '°C'),
    'TG0D': ('GPU Die', 'temperature', '°C'),
    # Apple Silicon GPU temps
    'Tg04': ('GPU Die 0', 'temperature', '°C'),
    'Tg05': ('GPU Die 1', 'temperature', '°C'),
    'Tg0f': ('GPU Die', 'temperature', '°C'),
    'Tg0j': ('GPU Die', 'temperature', '°C'),
    # Memory temps
    'Tm0P': ('Memory Proximity', 'temperature', '°C'),
    'Tm00': ('Memory Bank 0', 'temperature', '°C'),
    'Tm01': ('Memory Bank 1', 'temperature', '°C'),
    # Misc Intel
    'TN0P': ('Northbridge', 'temperature', '°C'),
    'TB0T': ('Battery', 'temperature', '°C'),
}

# Apple Silicon extended temperature keys (M1 through M5).
# Derived from iSMC smc/sensors.go (GPL-3.0, Copyright Dinko Korunic).
# Discovery probes all; only keys present on the actual chip are registered.
_AS_TEMP_KEYS: frozenset[str] = frozenset({
    # CPU P-cores (die/cluster temps across M1-M5 variants)
    'Tp00', 'Tp04', 'Tp05', 'Tp06', 'Tp08', 'Tp0C', 'Tp0D', 'Tp0E',
    'Tp0G', 'Tp0K', 'Tp0L', 'Tp0M', 'Tp0O', 'Tp0R', 'Tp0U', 'Tp0W',
    'Tp0X', 'Tp0a', 'Tp0b', 'Tp0c', 'Tp0d', 'Tp0g', 'Tp0h', 'Tp0i',
    'Tp0j', 'Tp0m', 'Tp0n', 'Tp0o', 'Tp0p', 'Tp0u', 'Tp0y',
    'Tp12', 'Tp16', 'Tp1E', 'Tp1F', 'Tp1G', 'Tp1K', 'Tp1Q', 'Tp1R',
    'Tp1S', 'Tp1j', 'Tp1n', 'Tp1t', 'Tp1w', 'Tp1z',
    'Tp22', 'Tp25', 'Tp28', 'Tp2B', 'Tp2E', 'Tp2J', 'Tp2M', 'Tp2Q',
    'Tp2T', 'Tp2W', 'Tp3P', 'Tp3X',
    # CPU package sensors
    'Tpx8', 'Tpx9', 'TpxA', 'TpxB', 'TpxC', 'TpxD',
    # CPU E-cores (efficiency cluster temps)
    'Te04', 'Te05', 'Te06', 'Te09', 'Te0G', 'Te0H', 'Te0I', 'Te0L',
    'Te0P', 'Te0Q', 'Te0R', 'Te0S', 'Te0T', 'Te0U', 'Te0V',
    # GPU dies
    'Tg0G', 'Tg0H', 'Tg0K', 'Tg0L', 'Tg0U', 'Tg0X', 'Tg0d', 'Tg0e',
    'Tg0g', 'Tg0k', 'Tg1U', 'Tg1Y', 'Tg1c', 'Tg1g', 'Tg1k',
    # Die fabric / interconnect
    'Tf14', 'Tf18', 'Tf19', 'Tf1A', 'Tf1D', 'Tf1E',
    'Tf24', 'Tf28', 'Tf29', 'Tf2A', 'Tf2D', 'Tf2E',
    # Memory (lowercase variant keys on some chips)
    'Tm0p', 'Tm1p', 'Tm2p',
})

# Max fan index to probe when FNum is unavailable.
_FNUM_FALLBACK = 4


# =========================================================================
# Private helper functions
# =========================================================================

# ── SMC structures ───────────────────────────────────────────────────


class SMCKeyData_vers_t(ctypes.Structure):
    _fields_ = [
        ('major', ctypes.c_uint8),
        ('minor', ctypes.c_uint8),
        ('build', ctypes.c_uint8),
        ('reserved', ctypes.c_uint8),
        ('release', ctypes.c_uint16),
    ]


class SMCKeyData_pLimitData_t(ctypes.Structure):
    _fields_ = [
        ('version', ctypes.c_uint16),
        ('length', ctypes.c_uint16),
        ('cpuPLimit', ctypes.c_uint32),
        ('gpuPLimit', ctypes.c_uint32),
        ('memPLimit', ctypes.c_uint32),
    ]


class SMCKeyData_keyInfo_t(ctypes.Structure):
    _fields_ = [
        ('dataSize', ctypes.c_uint32),
        ('dataType', ctypes.c_uint32),
        ('dataAttributes', ctypes.c_uint8),
    ]


class SMCKeyData_t(ctypes.Structure):
    _fields_ = [
        ('key', ctypes.c_uint32),
        ('vers', SMCKeyData_vers_t),
        ('pLimitData', SMCKeyData_pLimitData_t),
        ('keyInfo', SMCKeyData_keyInfo_t),
        ('result', ctypes.c_uint8),
        ('status', ctypes.c_uint8),
        ('data8', ctypes.c_uint8),
        ('data32', ctypes.c_uint32),
        ('bytes', ctypes.c_uint8 * 32),
    ]


# ── SMC byte parsing ────────────────────────────────────────────────


def _smc_key_to_int(key: str) -> int:
    """Convert 4-char SMC key to uint32."""
    return struct.unpack('>I', key.encode('ascii'))[0]


def _datatype_to_str(dt: int) -> str:
    """Convert dataType uint32 to 4-char string."""
    return struct.pack('>I', dt).decode('ascii', errors='replace')


def _parse_smc_bytes(data_type: int, raw: ctypes.Array, size: int) -> float:
    """Parse SMC raw bytes based on data type code."""
    dt = _datatype_to_str(data_type)
    b = bytes(raw[:size])
    if len(b) < 2:
        return float(b[0]) if b else 0.0

    match dt.rstrip():
        case 'sp78':  # signed 8.8 fixed-point (temps)
            return struct.unpack('>h', b[:2])[0] / 256.0
        case 'fpe2':  # unsigned 14.2 fixed-point (fan RPM)
            return struct.unpack('>H', b[:2])[0] / 4.0
        case 'flt':   # IEEE 754 float (little-endian on all Macs)
            return struct.unpack('<f', b[:4])[0] if len(b) >= 4 else 0.0
        case 'ui8':
            return float(b[0])
        case 'ui16':
            return float(struct.unpack('>H', b[:2])[0])
        case 'ui32' if len(b) >= 4:
            return float(struct.unpack('>I', b[:4])[0])
        case 'fp1f':  # 1.15 fixed-point
            return struct.unpack('>H', b[:2])[0] / 32768.0
        case _:
            # Best effort: treat as big-endian unsigned
            log.debug("SMC unknown data type '%s' (size=%d) — fallback BE uint",
                      dt.rstrip(), size)
            return float(struct.unpack('>H', b[:2])[0]) / 256.0


def _as_key_metadata(key: str) -> tuple[str, str, str]:
    """Derive (name, category, unit) for an Apple Silicon temp key."""
    suffix = key[2:]
    match key[:2]:
        case 'Tp':
            name = f'CPU P-Core {suffix}' if suffix != 'x' else f'CPU Package {suffix}'
        case 'Te':
            name = f'CPU E-Core {suffix}'
        case 'Tg':
            name = f'GPU Die {suffix}'
        case 'Tf':
            name = f'Die Fabric {suffix}'
        case 'Tm':
            name = f'Memory {suffix}'
        case _:
            name = f'Sensor {key}'
    return (name, 'temperature', '°C')


# ── Launch Agent plist ───────────────────────────────────────────────


def _launch_agent_plist() -> str:
    """Generate launchd plist content for autostart."""
    exec_path = AutostartManager.get_exec()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        '  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '    <key>Label</key>\n'
        '    <string>com.thermalright.trcc</string>\n'
        '    <key>ProgramArguments</key>\n'
        '    <array>\n'
        f'        <string>{exec_path}</string>\n'
        '        <string>gui</string>\n'
        '        <string>--resume</string>\n'
        '    </array>\n'
        '    <key>RunAtLoad</key>\n'
        '    <true/>\n'
        '    <key>KeepAlive</key>\n'
        '    <false/>\n'
        '</dict>\n'
        '</plist>\n'
    )


# ── Hardware info (system_profiler) ──────────────────────────────────


def _run_profiler(data_type: str) -> dict:
    """Run system_profiler and return parsed JSON."""
    try:
        result = subprocess.run(
            ['system_profiler', data_type, '-json'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        log.debug("system_profiler %s failed", data_type)
    return {}


def get_memory_info() -> list[dict[str, str]]:
    """Get DRAM info via system_profiler SPMemoryDataType.

    Returns one dict per populated DIMM slot, matching Linux format:
        manufacturer, part_number, type, speed, size, form_factor, etc.

    Note: Apple Silicon unified memory reports as a single entry.
    """
    slots: list[dict[str, str]] = []
    data = _run_profiler('SPMemoryDataType')
    items = data.get('SPMemoryDataType', [])

    for item in items:
        # Apple Silicon: top-level has 'dimm_type', 'SPMemoryDataType' items
        # Intel: top-level items contain nested DIMMs
        dimms = item.get('_items', [item])
        for dimm in dimms:
            slot: dict[str, str] = {}
            slot['manufacturer'] = dimm.get('dimm_manufacturer', 'Apple')
            slot['part_number'] = dimm.get('dimm_part_number', '')
            slot['type'] = dimm.get('dimm_type', '')
            slot['speed'] = dimm.get('dimm_speed', '')
            slot['size'] = dimm.get('dimm_size', '')
            slot['form_factor'] = dimm.get('dimm_form_factor', '')
            slot['locator'] = dimm.get('_name', '')
            if slot['size']:
                slots.append(slot)

    # Fallback: psutil total
    if not slots:
        mem = psutil.virtual_memory()
        slots.append({
            'manufacturer': 'Apple',
            'part_number': 'Unknown',
            'type': 'Unified' if IS_APPLE_SILICON else 'Unknown',
            'speed': 'Unknown',
            'size': f'{mem.total // (1024 ** 3)} GB',
            'form_factor': 'Unified' if IS_APPLE_SILICON else 'Unknown',
            'locator': 'Total',
        })

    return slots


def get_disk_info() -> list[dict[str, str]]:
    """Get physical disk info via system_profiler SPStorageDataType.

    Returns one dict per disk, matching Linux format:
        name, model, size, type (SSD/HDD), health.
    """
    disks: list[dict[str, str]] = []
    data = _run_profiler('SPStorageDataType')
    items = data.get('SPStorageDataType', [])

    for item in items:
        info: dict[str, str] = {}
        info['name'] = item.get('bsd_name', '')
        info['model'] = item.get('physical_drive', {}).get('device_name', '')
        info['size'] = item.get('size_in_bytes', '')
        if info['size']:
            try:
                b = int(info['size'])
                if b >= 1024 ** 4:
                    info['size'] = f'{b / (1024 ** 4):.1f} TB'
                elif b >= 1024 ** 3:
                    info['size'] = f'{b / (1024 ** 3):.0f} GB'
            except (ValueError, TypeError):
                pass
        media_type = item.get('physical_drive', {}).get('medium_type', '')
        if 'solid' in media_type.lower() or 'ssd' in media_type.lower():
            info['type'] = 'SSD'
        elif 'rotational' in media_type.lower():
            info['type'] = 'HDD'
        else:
            info['type'] = 'SSD'  # Modern Macs are all SSD
        info['health'] = item.get('smart_status', 'Unknown')
        if info['name'] or info['model']:
            disks.append(info)

    return disks



# =========================================================================
# MacOSPlatform — THE one class
# =========================================================================

class MacOSMetrics(Metrics):
    """macOS HardwareMetrics builder — composes its sources in __init__.

    Today: leans on :class:`MacOSSensorEnumerator` which composes IOKit
    SMC + Apple Silicon HID + powermetrics + psutil + pynvml.  Future
    growth slots in as ``_read_*`` methods called from __init__ — macmon
    ANE on Apple Silicon, IOReportSubscribe for push-based metrics, etc.
    """

    def __init__(self, enumerator: _SensorEnumeratorABC) -> None:
        super().__init__()
        self._enumerator = enumerator
        self._read_datetime()
        self._read_sensors(enumerator)
        # Future per-OS reads slot in here:
        #   self._read_macmon_ane()      — Apple Silicon ANE telemetry
        #   self._read_ioreport_push()   — Push-based metrics via IOReportSubscribe


class MacOSPlatform(Platform):
    """macOS Platform — all OS logic inline."""

    def __init__(self) -> None:
        super().__init__()

    # ── Metrics ───────────────────────────────────────────────

    def _make_metrics(self) -> Metrics:
        return MacOSMetrics(self.sensors)

    # ── Sensor factory ───────────────────────────────────────

    def _make_sensor_enumerator(self) -> SensorEnumeratorBase:
        """Native macOS sensor enumerator — IOKit SMC + Apple Silicon HID
        + powermetrics + psutil + pynvml.  Lives in the macos/ adapter
        package (10 modules; see ``adapters/system/macos/sensors.py``)."""
        from trcc.adapters.system.macos.sensors import MacOSSensorEnumerator
        return MacOSSensorEnumerator()

    # ── Hardware discovery ────────────────────────────────────

    def detect_devices(self):
        from trcc.adapters.device.detector import DeviceDetector
        return DeviceDetector.make_detect_fn(scsi_resolver=None)()

    # ── Transport creation ────────────────────────────────────

    def create_scsi_transport(self, path: str, vid: int = 0, pid: int = 0,
                              *, usb_address: UsbAddress | None = None) -> Any:
        from trcc.adapters.device.macos.scsi import MacOSScsiTransport
        from trcc.core.models import UsbAddress as _UsbAddress
        # macOS USB BOT binds by (bus, address). Prefer the explicit kwarg
        # threaded from DeviceInfo; fall back to parsing the path if a legacy
        # caller didn't pass it.
        bound = usb_address or _UsbAddress.parse(path)
        return MacOSScsiTransport(vid=vid, pid=pid, usb_address=bound)

    # ── Directories ───────────────────────────────────────────

    def resolve_assets_dir(self, pkg_dir: Any) -> Any:
        return _copy_assets_to_user_dir(pkg_dir)

    # ── Autostart (Launch Agent) ─────────────────────────────

    def autostart_enable(self) -> None:
        _LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        _LAUNCH_AGENT_FILE.write_text(_launch_agent_plist())
        log.info("Autostart enabled: %s", _LAUNCH_AGENT_FILE)

    def autostart_disable(self) -> None:
        if _LAUNCH_AGENT_FILE.exists():
            _LAUNCH_AGENT_FILE.unlink()
        log.info("Autostart disabled")

    def autostart_enabled(self) -> bool:
        return _LAUNCH_AGENT_FILE.exists()

    def acquire_instance_lock(self) -> object | None:
        return _posix_acquire_instance_lock(self.config_dir())

    def raise_existing_instance(self) -> None:
        _posix_raise_existing_instance(self.config_dir())

    def _screen_capture_format(self) -> str | None:
        return 'avfoundation'

    def wire_ipc_raise(self, app: Any, window: Any) -> None:
        _posix_wire_ipc_raise(app, window)

    # ── Administration ────────────────────────────────────────

    def get_pkg_manager(self) -> str | None:
        return 'brew' if shutil.which('brew') else None

    def check_deps(self) -> list:
        from trcc.adapters.infra.doctor import check_system_deps
        return check_system_deps(self.get_pkg_manager())

    def install_rules(self) -> int:
        return 1  # macOS: no rules to install

    def check_permissions(self, devices: list) -> list[str]:
        return []

    def get_system_files(self) -> list[str]:
        return []

    # ── Identity ──────────────────────────────────────────────

    def distro_name(self) -> str:
        return f"macOS {platform.mac_ver()[0]}"

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

        # Step 1/2: System dependencies
        print("  Step 1/2: System dependencies")
        if not pm:
            print("    [!!]  Homebrew not found — install from https://brew.sh/")
            print()
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

        # Step 2/2: USB access
        print("  Step 2/2: USB access")
        print("    SCSI devices need sudo to detach the kernel driver.")
        print("    HID devices work without root.")
        print("    Apple Silicon: sensor reading needs sudo for powermetrics.")
        print()

        _print_summary(actions)
        return 0

    # ── Help text ─────────────────────────────────────────────

    def archive_tool_install_help(self) -> str:
        return (
            "7z not found. Install via Homebrew:\n"
            "  brew install p7zip"
        )

    def ffmpeg_install_help(self) -> str:
        return "ffmpeg not found. Install:\n  brew install ffmpeg"

    # ── Hardware info ─────────────────────────────────────────

    def get_memory_info(self) -> list[dict[str, str]]:
        return get_memory_info()

    def get_disk_info(self) -> list[dict[str, str]]:
        return get_disk_info()
