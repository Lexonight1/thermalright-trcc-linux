"""Parse ``powermetrics -f plist`` XML (single sample, first NUL-separated chunk)."""
from __future__ import annotations

import logging
import plistlib
from typing import Any

log = logging.getLogger(__name__)

__all__ = ['parse_powermetrics_plist', 'parse_powermetrics_plist_root']


def _mw_to_w(mw: Any) -> float | None:
    if isinstance(mw, bool) or not isinstance(mw, (int, float)):
        return None
    v = float(mw) / 1000.0
    if v != v or v < 0.0 or v > 4000.0:
        return None
    return v


def _freq_hz_to_mhz(hz: Any) -> float | None:
    if isinstance(hz, bool) or not isinstance(hz, (int, float)):
        return None
    f = float(hz)
    if f <= 0:
        return None
    # CPU clusters report Hz (~3e9); GPU block uses MHz (~600–3000).
    mhz = f / 1e6 if f > 1e6 else f
    if mhz < 1.0 or mhz > 8000.0:
        return None
    return mhz


def _gpu_busy_percent(gpu: dict[str, Any]) -> float | None:
    states = gpu.get('dvfm_states')
    if not isinstance(states, list) or not states:
        return None
    total = 0.0
    for st in states:
        if isinstance(st, dict):
            r = st.get('used_ratio')
            if isinstance(r, (int, float)) and not isinstance(r, bool):
                total += float(r)
    pct = 100.0 * total
    if pct < 0.0 or pct > 100.0:
        return None
    return pct


def _max_cpu_mhz_from_processor(proc: dict[str, Any]) -> float | None:
    clusters = proc.get('clusters')
    if not isinstance(clusters, list):
        return None
    mhz_vals: list[float] = []
    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        cpus = cl.get('cpus')
        if not isinstance(cpus, list):
            continue
        for cpu in cpus:
            if not isinstance(cpu, dict):
                continue
            m = _freq_hz_to_mhz(cpu.get('freq_hz'))
            if m is not None:
                mhz_vals.append(m)
    return max(mhz_vals) if mhz_vals else None


def parse_powermetrics_plist_root(root: dict[str, Any]) -> dict[str, float]:
    """Extract GPU/CPU powermetrics fields from an already-loaded plist dict."""
    out: dict[str, float] = {}
    proc = root.get('processor')
    gpu = root.get('gpu')

    if isinstance(gpu, dict):
        busy = _gpu_busy_percent(gpu)
        if busy is not None:
            out['iokit:gpu_busy'] = busy
        gclk = _freq_hz_to_mhz(gpu.get('freq_hz'))
        if gclk is not None:
            out['iokit:gpu_clock'] = gclk

    if isinstance(proc, dict):
        for key, sid in (
            ('cpu_power', 'iokit:cpu_power'),
            ('gpu_power', 'iokit:gpu_power'),
            ('ane_power', 'iokit:ane_power'),
            ('combined_power', 'iokit:combined_power'),
        ):
            w = _mw_to_w(proc.get(key))
            if w is not None:
                out[sid] = w
        max_mhz = _max_cpu_mhz_from_processor(proc)
        if max_mhz is not None:
            out['psutil:cpu_freq'] = max_mhz

    return out


def parse_powermetrics_plist(data: bytes | str) -> dict[str, float] | None:
    """Return GPU/CPU sensor readings from one plist sample, or None if not plist / parse error."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    chunk = data.split(b'\x00', 1)[0].strip()
    if not chunk.startswith(b'<?xml') and not chunk.startswith(b'<plist'):
        return None
    try:
        root = plistlib.loads(chunk)
    except Exception:
        log.debug('powermetrics plist parse failed', exc_info=True)
        return None
    if not isinstance(root, dict):
        return None

    out = parse_powermetrics_plist_root(root)
    return out if out else None
