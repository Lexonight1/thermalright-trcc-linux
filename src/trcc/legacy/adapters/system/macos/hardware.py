"""macOS hardware info via system_profiler.

Replaces Linux dmidecode/lsblk with macOS system_profiler JSON queries.
Same return types as Linux hardware.py — list[dict[str, str]].
"""
from __future__ import annotations

import json
import logging
import platform
import subprocess
import sys

log = logging.getLogger(__name__)


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
        import psutil
        mem = psutil.virtual_memory()
        unified = _is_apple_silicon()
        slots.append({
            'manufacturer': 'Apple',
            'part_number': 'Unknown',
            'type': 'Unified' if unified else 'Unknown',
            'speed': 'Unknown',
            'size': f'{mem.total // (1024 ** 3)} GB',
            'form_factor': 'Unified' if unified else 'Unknown',
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


def _is_apple_silicon() -> bool:
    """True when this process is native Apple Silicon (arm64 macOS).

    TRCC is not supported under Rosetta (x86_64 Python on Apple Silicon); use an
    arm64 interpreter so IOKit/HID and sampler paths match the machine.
    """
    return sys.platform == 'darwin' and platform.machine() == 'arm64'
