"""Optional second ``powermetrics`` sample (thermal, battery, network, disk).

Set ``TRCC_POWERMETRICS_EXTRA_SAMPLERS`` to a comma-separated subset of:
``thermal``, ``battery``, ``network``, ``disk``. Those samplers are appended to
the same ``gpu_power,cpu_power`` ``powermetrics`` run each poll (one process
invocation). Leave unset to request only GPU/CPU metrics from that sample.
"""
from __future__ import annotations

import os
import re
from typing import Any

from trcc.legacy.core.models import SensorInfo

_ALLOWED = frozenset({'thermal', 'battery', 'network', 'disk'})
_SPLIT = re.compile(r'\s*,\s*')

__all__ = [
    'extra_powermetrics_sampler_csv',
    'extra_powermetrics_sensor_specs',
    'extra_sensor_infos',
    'full_powermetrics_sampler_csv',
    'readings_from_powermetrics_extras',
]


def full_powermetrics_sampler_csv() -> str:
    """Single ``--samplers`` value: GPU/CPU plus optional extras (one powermetrics run)."""
    parts: list[str] = ['gpu_power', 'cpu_power']
    seen: set[str] = set(parts)
    extra = extra_powermetrics_sampler_csv()
    if extra:
        for tok in extra.split(','):
            if tok and tok not in seen:
                parts.append(tok)
                seen.add(tok)
    return ','.join(parts)


def extra_powermetrics_sampler_csv() -> str | None:
    """Return sanitized ``--samplers`` string for the extra poll, or None."""
    raw = os.environ.get('TRCC_POWERMETRICS_EXTRA_SAMPLERS', '')
    if not raw.strip():
        return None
    seen: list[str] = []
    for part in _SPLIT.split(raw.strip()):
        t = part.strip().lower()
        if t in _ALLOWED and t not in seen:
            seen.append(t)
    return ','.join(seen) if seen else None


def extra_powermetrics_sensor_specs() -> list[tuple[str, str, str, str]]:
    """``(id, name, category, unit)`` tuples for sensors implied by the env."""
    csv = extra_powermetrics_sampler_csv()
    if not csv:
        return []
    tokens = csv.split(',')
    specs: list[tuple[str, str, str, str]] = []
    for t in tokens:
        if t == 'thermal':
            specs.append(('iokit:thermal_pressure', 'Thermal pressure', 'other', ''))
        elif t == 'battery':
            specs.append(('iokit:battery_percent', 'Battery charge', 'usage', '%'))
        elif t == 'network':
            specs.extend((
                ('iokit:net_ibyte_rate', 'Network in (B/s)', 'other', 'B/s'),
                ('iokit:net_obyte_rate', 'Network out (B/s)', 'other', 'B/s'),
                ('iokit:net_ipacket_rate', 'Network in (pkt/s)', 'other', 'pkt/s'),
                ('iokit:net_opacket_rate', 'Network out (pkt/s)', 'other', 'pkt/s'),
            ))
        elif t == 'disk':
            specs.extend((
                ('iokit:disk_rbytes_per_s', 'Disk read (B/s)', 'other', 'B/s'),
                ('iokit:disk_wbytes_per_s', 'Disk write (B/s)', 'other', 'B/s'),
                ('iokit:disk_rops_per_s', 'Disk read ops/s', 'other', 'ops/s'),
                ('iokit:disk_wops_per_s', 'Disk write ops/s', 'other', 'ops/s'),
            ))
    return specs


_THERMAL_LEVEL: dict[str, float] = {
    'nominal': 0.0,
    'fair': 1.0,
    'moderate': 2.0,
    'serious': 3.0,
    'critical': 4.0,
    'trapping': 5.0,
}


def _thermal_to_float(val: Any) -> float | None:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    if not isinstance(val, str):
        return None
    key = val.strip().lower()
    if key in _THERMAL_LEVEL:
        return _THERMAL_LEVEL[key]
    return None


def readings_from_powermetrics_extras(root: dict[str, Any]) -> dict[str, float]:
    """Extract optional metrics from a plist dict (already ``plistlib.loads``)."""
    out: dict[str, float] = {}
    if not isinstance(root, dict):
        return out

    tp = root.get('thermal_pressure')
    v = _thermal_to_float(tp)
    if v is not None:
        out['iokit:thermal_pressure'] = v

    bat = root.get('battery')
    if isinstance(bat, dict):
        pc = bat.get('percent_charge')
        if isinstance(pc, (int, float)) and not isinstance(pc, bool):
            out['iokit:battery_percent'] = float(pc)

    net = root.get('network')
    if isinstance(net, dict):
        for src_key, dst in (
            ('ibyte_rate', 'iokit:net_ibyte_rate'),
            ('obyte_rate', 'iokit:net_obyte_rate'),
            ('ipacket_rate', 'iokit:net_ipacket_rate'),
            ('opacket_rate', 'iokit:net_opacket_rate'),
        ):
            x = net.get(src_key)
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                out[dst] = float(x)

    disk = root.get('disk')
    if isinstance(disk, dict):
        for src_key, dst in (
            ('rbytes_per_s', 'iokit:disk_rbytes_per_s'),
            ('wbytes_per_s', 'iokit:disk_wbytes_per_s'),
            ('rops_per_s', 'iokit:disk_rops_per_s'),
            ('wops_per_s', 'iokit:disk_wops_per_s'),
        ):
            x = disk.get(src_key)
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                out[dst] = float(x)

    return out


def extra_sensor_infos() -> list[SensorInfo]:
    """Build ``SensorInfo`` rows for the current ``TRCC_POWERMETRICS_EXTRA_SAMPLERS``."""
    return [
        SensorInfo(sid, name, cat, unit, 'iokit')
        for sid, name, cat, unit in extra_powermetrics_sensor_specs()
    ]
