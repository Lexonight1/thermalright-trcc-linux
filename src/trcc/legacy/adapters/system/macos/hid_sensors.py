"""Apple Silicon HID sensor hub via IOKit + CoreFoundation (ctypes).

Ports the behavior of https://github.com/dkorunic/iSMC hid/get.go (GPL-3.0):
matching on PrimaryUsagePage / PrimaryUsage, IOHIDEventSystemClient*, thermal and
power events, and the PMU tdev sp78 raw-value heuristic.

Requires Darwin on native Apple Silicon (arm64) and exported C symbols from
IOKit; if anything is missing,
:func:`hid_layer_ready` is False and callers skip HID (SMC + powermetrics still work).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import re
import sys

from trcc.legacy.adapters.system.macos.hardware import _is_apple_silicon

log = logging.getLogger(__name__)

kIOHIDEventTypeTemperature = 15
kIOHIDEventTypePower = 25

# PrimaryUsagePage / PrimaryUsage (iSMC hid/get.go)
_PAGE_THERMAL = 0xFF00
_USAGE_THERMAL = 5
_PAGE_ELEC = 0xFF08
_USAGE_CURRENT = 2
_USAGE_VOLTAGE = 3

kCFStringEncodingUTF8 = 0x0800_0100
kCFNumberSInt32Type = 3


def _is_as_darwin() -> bool:
    return sys.platform == 'darwin' and _is_apple_silicon()


_cf: ctypes.CDLL | None = None
_iokit: ctypes.CDLL | None = None
_hid_bindings_ok = False
# dlsym — c_void_p.in_dll(CoreFoundation, 'kCFTypeDictionary*') resolves to NULL on
# some macOS/Python combos; RTLD_DEFAULT yields the real callback table addresses.
_kcf_key_callbacks_addr: int = 0
_kcf_value_callbacks_addr: int = 0


def _dlsym_cf_callbacks() -> bool:
    """Resolve kCFTypeDictionary{Key,Value}CallBacks via dlsym(RTLD_DEFAULT, …)."""
    global _kcf_key_callbacks_addr, _kcf_value_callbacks_addr
    if _kcf_key_callbacks_addr and _kcf_value_callbacks_addr:
        return True
    try:
        libsys = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        dlsym = libsys.dlsym
        dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        dlsym.restype = ctypes.c_void_p
        rtld_default = ctypes.c_void_p(-2)
        k = dlsym(rtld_default, b'kCFTypeDictionaryKeyCallBacks')
        v = dlsym(rtld_default, b'kCFTypeDictionaryValueCallBacks')
        if not k or not v:
            log.debug('dlsym CF dictionary callbacks failed k=%s v=%s', k, v)
            return False
        _kcf_key_callbacks_addr = int(k)
        _kcf_value_callbacks_addr = int(v)
        return True
    except Exception:
        log.debug('dlsym CF callbacks exception', exc_info=True)
        return False


def _try_bind_hid() -> bool:
    global _cf, _iokit, _hid_bindings_ok
    if not _is_as_darwin():
        return False
    if _hid_bindings_ok:
        return True
    try:
        cf_path = ctypes.util.find_library('CoreFoundation')
        io_path = ctypes.util.find_library('IOKit')
        if not cf_path or not io_path:
            return False
        _cf = ctypes.CDLL(cf_path)
        _iokit = ctypes.CDLL(io_path)
    except OSError:
        return False

    if not hasattr(_cf, 'CFRelease'):
        log.debug('CoreFoundation missing CFRelease')
        return False

    # CoreFoundation
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    _cf.CFRelease.restype = None

    for name, restype, argtypes in (
        ('CFDictionaryCreateMutable', ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]),
        ('CFDictionarySetValue', None,
         [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]),
        ('CFNumberCreate', ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]),
        ('CFStringCreateWithCString', ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]),
        ('CFArrayGetCount', ctypes.c_long, [ctypes.c_void_p]),
        ('CFArrayGetValueAtIndex', ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_long]),
        ('CFStringGetCString', ctypes.c_uint8,
         [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
    ):
        fn = getattr(_cf, name, None)
        if fn is None:
            log.debug('CoreFoundation missing %s', name)
            return False
        fn.restype = restype if restype is not None else None
        fn.argtypes = argtypes  # type: ignore[assignment]

    # IOKit HID event client (C API; present on Apple Silicon macOS)
    for name, restype, argtypes in (
        ('IOHIDEventSystemClientCreate', ctypes.c_void_p, [ctypes.c_void_p]),
        ('IOHIDEventSystemClientSetMatching', None,
         [ctypes.c_void_p, ctypes.c_void_p]),
        ('IOHIDEventSystemClientCopyServices', ctypes.c_void_p, [ctypes.c_void_p]),
        ('IOHIDServiceClientCopyProperty', ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_void_p]),
        # Must match iSMC hid/get.c (and IOKit): (client, int64_t, int32_t, int64_t).
        # Wrong widths corrupt the stack on arm64 and yield garbage floats from
        # IOHIDEventGetFloatValue.
        ('IOHIDServiceClientCopyEvent', ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]),
        ('IOHIDEventGetFloatValue', ctypes.c_double,
         [ctypes.c_void_p, ctypes.c_int32]),
    ):
        fn = getattr(_iokit, name, None)
        if fn is None:
            log.debug('IOKit missing %s — HID layer disabled', name)
            return False
        fn.restype = restype
        fn.argtypes = argtypes  # type: ignore[assignment]

    if not _dlsym_cf_callbacks():
        return False

    _hid_bindings_ok = True
    return True


def hid_layer_ready() -> bool:
    return _try_bind_hid()


def _cfstr(s: str) -> ctypes.c_void_p | None:
    assert _cf is not None
    p = _cf.CFStringCreateWithCString(
        None, s.encode('utf-8'), kCFStringEncodingUTF8)
    return p


def _cfnumber_i32(v: int) -> ctypes.c_void_p | None:
    assert _cf is not None
    buf = ctypes.c_int32(v)
    return _cf.CFNumberCreate(None, kCFNumberSInt32Type, ctypes.byref(buf))


def _matching_dict(page: int, usage: int) -> ctypes.c_void_p | None:
    assert _cf is not None
    if not _kcf_key_callbacks_addr or not _kcf_value_callbacks_addr:
        return None
    kcb = ctypes.c_void_p(_kcf_key_callbacks_addr)
    vcb = ctypes.c_void_p(_kcf_value_callbacks_addr)
    d = _cf.CFDictionaryCreateMutable(None, 0, kcb, vcb)
    if not d:
        return None
    k1 = _cfstr('PrimaryUsagePage')
    k2 = _cfstr('PrimaryUsage')
    n1 = _cfnumber_i32(page)
    n2 = _cfnumber_i32(usage)
    if not all((k1, k2, n1, n2)):
        for x in (k1, k2, n1, n2, d):
            if x:
                _cf.CFRelease(x)
        return None
    _cf.CFDictionarySetValue(d, k1, n1)
    _cf.CFDictionarySetValue(d, k2, n2)
    for x in (k1, k2, n1, n2):
        _cf.CFRelease(x)
    return d


def _cfstring_to_str(ref: ctypes.c_void_p) -> str:
    assert _cf is not None
    if not ref:
        return ''
    buf = ctypes.create_string_buffer(512)
    if _cf.CFStringGetCString(ref, buf, len(buf), kCFStringEncodingUTF8):
        return buf.value.decode('utf-8', errors='replace')
    return ''


def _iohid_field_base(event_type: int) -> int:
    return int(event_type) << 16


def _normalize_hid_thermal_celsius(name: str, val: float) -> float | None:
    """Turn IOHID thermal float into plausible °C, or None if unusable.

    Matches iSMC ``dumpThermalNamesValues``: PMU *tdev* channels may report raw
    sp78 (value ≈ °C×256). Other channels usually report °C directly. Values
    outside a sane range after conversion are dropped so bad API/ABI reads
    never surface as absurd temperatures.
    """
    if val != val or val in (float('inf'), float('-inf')):
        return None
    v = float(val)
    name_l = name.lower()

    if 'tdev' in name_l:
        m = re.search(r'tdev([1-9])', name_l)
        if m and v > 130.0:
            v = v / 256.0

    if -40.0 <= v <= 150.0:
        return v

    # Raw sp78-style (1…130 °C → 256…33280); covers some non-tdev PMU names.
    if 256.0 <= v <= 130.0 * 256.0:
        c = v / 256.0
        if -40.0 <= c <= 150.0:
            return c

    return None


def _collect_names_values(
    page: int,
    usage: int,
    event_type: int,
    *,
    thermal: bool,
    power_scale: float,
) -> list[tuple[str, float]]:
    assert _cf is not None and _iokit is not None
    out: list[tuple[str, float]] = []
    match = _matching_dict(page, usage)
    if not match:
        return out
    client = _iokit.IOHIDEventSystemClientCreate(None)
    if not client:
        _cf.CFRelease(match)
        return out
    try:
        _iokit.IOHIDEventSystemClientSetMatching(client, match)
        services = _iokit.IOHIDEventSystemClientCopyServices(client)
        if not services:
            return out
        try:
            n = int(_cf.CFArrayGetCount(services))
            prop_product = _cfstr('Product')
            if not prop_product:
                return out
            try:
                for i in range(n):
                    sc = _cf.CFArrayGetValueAtIndex(services, i)
                    if not sc:
                        continue
                    name_ref = _iokit.IOHIDServiceClientCopyProperty(
                        sc, prop_product)
                    name = _cfstring_to_str(name_ref) if name_ref else 'noname'
                    if name_ref:
                        _cf.CFRelease(name_ref)
                    ev = _iokit.IOHIDServiceClientCopyEvent(
                        sc, int(event_type), 0, 0)
                    val = 0.0
                    if ev:
                        val = float(_iokit.IOHIDEventGetFloatValue(
                            ev, _iohid_field_base(event_type)))
                        _cf.CFRelease(ev)
                    if power_scale != 1.0:
                        val = val / power_scale
                    if thermal:
                        val = _normalize_hid_thermal_celsius(name, val)
                        if val is None:
                            continue
                    if val > 0.0:
                        out.append((name, val))
            finally:
                _cf.CFRelease(prop_product)
        finally:
            _cf.CFRelease(services)
    finally:
        _cf.CFRelease(client)
        _cf.CFRelease(match)
    return out


def _dedupe_hid_pairs_by_name(pairs: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Keep the first row per Product name.

    IOHID often enumerates several services with the same ``Product`` string
    (e.g. PMU channels), which produced ``hid:PMU_foo``, ``hid:PMU_foo_1``, …
    and cluttered ``trcc sensors``. One logical channel per name matches how
    a single reading is usually shown in tools like iSMC.
    """
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for name, val in pairs:
        key = name.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, val))
    return out


def read_hid_sensor_pairs() -> list[tuple[str, str, str, str, float]]:
    """Return rows: (sensor_id, display_name, category, unit, sample_value).

    sample_value is used only at discover time to filter dead channels (>0).
    Poll uses the same collection path.
    """
    if not hid_layer_ready():
        return []
    rows: list[tuple[str, str, str, str, float]] = []

    used: set[str] = set()

    def add_rows(pairs: list[tuple[str, float]], category: str, unit: str) -> None:
        for name, val in pairs:
            base = 'hid:' + re.sub(r'[^a-zA-Z0-9_.-]+', '_', name).strip('_')
            sid = base
            n = 0
            while sid in used:
                n += 1
                sid = f'{base}_{n}'
            used.add(sid)
            rows.append((sid, name, category, unit, val))

    therm = _dedupe_hid_pairs_by_name(_collect_names_values(
        _PAGE_THERMAL, _USAGE_THERMAL, kIOHIDEventTypeTemperature,
        thermal=True, power_scale=1.0,
    ))
    add_rows(therm, 'temperature', '°C')

    curr = _dedupe_hid_pairs_by_name(_collect_names_values(
        _PAGE_ELEC, _USAGE_CURRENT, kIOHIDEventTypePower,
        thermal=False, power_scale=1000.0,
    ))
    add_rows(curr, 'current', 'A')

    volt = _dedupe_hid_pairs_by_name(_collect_names_values(
        _PAGE_ELEC, _USAGE_VOLTAGE, kIOHIDEventTypePower,
        thermal=False, power_scale=1000.0,
    ))
    add_rows(volt, 'voltage', 'V')

    return rows


def poll_hid_readings(sensor_ids: frozenset[str]) -> dict[str, float]:
    """Current values for the given hid:* sensor ids."""
    if not sensor_ids or not hid_layer_ready():
        return {}
    readings: dict[str, float] = {}
    for sid, _name, _cat, _unit, val in read_hid_sensor_pairs():
        if sid in sensor_ids:
            readings[sid] = val
    return readings
