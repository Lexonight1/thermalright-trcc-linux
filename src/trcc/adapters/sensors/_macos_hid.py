"""Apple Silicon HID sensor hub via IOKit + CoreFoundation (ctypes).

On Apple Silicon Macs the AppleSMC service exposes a sparse set of
thermal keys; the *real* die temperatures live on a private IOHID
sensor hub.  iSMC, macmon, and Stats all read from it.  We do too —
direct ctypes against macOS-shipped frameworks, zero deps.

Pipeline:

  ┌────────────────────────────────────────────────────────────┐
  │ IOKit.framework + CoreFoundation.framework                 │
  │   IOHIDEventSystemClientCreate                             │
  │   ...SetMatching({ PrimaryUsagePage, PrimaryUsage })       │
  │   ...CopyServices → array of IOHIDServiceClient            │
  │   IOHIDServiceClientCopyEvent(client, type, 0, 0)          │
  │   IOHIDEventGetFloatValue                                  │
  └────────────────────────────────────────────────────────────┘
          │
          ▼ (cached snapshot, shared between CPU/GPU sources)
   MacosHidCpu.temp() / MacosHidGpu.temp()

Algorithm reference: iSMC ``hid/get.go`` (GPL-3.0).  Heuristic for
classifying a HID Product name as CPU vs GPU is also ported from
legacy macOS sensor enumerator's ``_apple_silicon_hid_cpu_temp`` /
``_apple_silicon_hid_gpu_temp``.

Hardware unverified: this code reaches IOKit symbols that don't exist
off Apple Silicon, so every call short-circuits to ``None`` on Linux /
Intel Mac.  Per CLAUDE.md macOS protocol, a release advertising HID
sensors should wait for a reporter to confirm on real hardware.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import platform
import re
import sys
import time
from collections.abc import Callable

from ...core.ports import CpuSource, GpuSource

log = logging.getLogger(__name__)


# ── HID page/usage + event type constants (iSMC hid/get.go) ──────────

_kIOHIDEventTypeTemperature = 15
_kIOHIDEventTypePower = 25

_PAGE_THERMAL = 0xFF00
_USAGE_THERMAL = 5
_PAGE_ELEC = 0xFF08
_USAGE_CURRENT = 2
_USAGE_VOLTAGE = 3

_kCFStringEncodingUTF8 = 0x0800_0100
_kCFNumberSInt32Type = 3


# ── ctypes binding ───────────────────────────────────────────────────


def _is_apple_silicon_darwin() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


# Module-level singletons — the binding only happens once per process.
_cf: ctypes.CDLL | None = None
_iokit: ctypes.CDLL | None = None
_hid_bindings_ok = False
# CFDictionary key/value callbacks live behind opaque pointers we
# resolve via dlsym(RTLD_DEFAULT, …) — c_void_p.in_dll(…) returns NULL
# on some Python builds.
_kcf_key_callbacks_addr: int = 0
_kcf_value_callbacks_addr: int = 0


def _dlsym_cf_callbacks() -> bool:
    """Resolve ``kCFTypeDictionary{Key,Value}CallBacks`` via dlsym."""
    global _kcf_key_callbacks_addr, _kcf_value_callbacks_addr
    if _kcf_key_callbacks_addr and _kcf_value_callbacks_addr:
        return True
    try:
        libsys = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        dlsym = libsys.dlsym
        dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        dlsym.restype = ctypes.c_void_p
        rtld_default = ctypes.c_void_p(-2)
        k = dlsym(rtld_default, b"kCFTypeDictionaryKeyCallBacks")
        v = dlsym(rtld_default, b"kCFTypeDictionaryValueCallBacks")
        if not k or not v:
            log.debug("dlsym CF dict callbacks failed k=%s v=%s", k, v)
            return False
        _kcf_key_callbacks_addr = int(k)
        _kcf_value_callbacks_addr = int(v)
        return True
    except Exception:
        log.debug("dlsym CF callbacks raised", exc_info=True)
        return False


def _try_bind_hid() -> bool:
    """Bind CF + IOKit symbols needed for IOHIDEventSystem reads.

    Returns False off Apple Silicon Darwin, or when any required
    symbol can't be resolved — callers short-circuit to None.
    """
    global _cf, _iokit, _hid_bindings_ok
    if not _is_apple_silicon_darwin():
        return False
    if _hid_bindings_ok:
        return True
    try:
        cf_path = ctypes.util.find_library("CoreFoundation")
        io_path = ctypes.util.find_library("IOKit")
        if not cf_path or not io_path:
            log.debug("HID layer: CF/IOKit not found by find_library")
            return False
        _cf = ctypes.CDLL(cf_path)
        _iokit = ctypes.CDLL(io_path)
    except OSError:
        log.debug("HID layer: dlopen failed", exc_info=True)
        return False

    if not hasattr(_cf, "CFRelease"):
        log.debug("HID layer: CoreFoundation missing CFRelease")
        return False
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    _cf.CFRelease.restype = None

    for name, restype, argtypes in (
        ("CFDictionaryCreateMutable", ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]),
        ("CFDictionarySetValue", None,
         [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]),
        ("CFNumberCreate", ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]),
        ("CFStringCreateWithCString", ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]),
        ("CFArrayGetCount", ctypes.c_long, [ctypes.c_void_p]),
        ("CFArrayGetValueAtIndex", ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_long]),
        ("CFStringGetCString", ctypes.c_uint8,
         [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
    ):
        fn = getattr(_cf, name, None)
        if fn is None:
            log.debug("HID layer: CoreFoundation missing %s", name)
            return False
        fn.restype = restype if restype is not None else None
        fn.argtypes = argtypes  # type: ignore[assignment]

    for name, restype, argtypes in (
        ("IOHIDEventSystemClientCreate", ctypes.c_void_p, [ctypes.c_void_p]),
        ("IOHIDEventSystemClientSetMatching", None,
         [ctypes.c_void_p, ctypes.c_void_p]),
        ("IOHIDEventSystemClientCopyServices", ctypes.c_void_p,
         [ctypes.c_void_p]),
        ("IOHIDServiceClientCopyProperty", ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_void_p]),
        # Widths must match iSMC hid/get.c: (client, int64, int32, int64).
        # Wrong widths corrupt the stack on arm64 + yield garbage floats.
        ("IOHIDServiceClientCopyEvent", ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]),
        ("IOHIDEventGetFloatValue", ctypes.c_double,
         [ctypes.c_void_p, ctypes.c_int32]),
    ):
        fn = getattr(_iokit, name, None)
        if fn is None:
            log.debug("HID layer: IOKit missing %s — disabled", name)
            return False
        fn.restype = restype
        fn.argtypes = argtypes  # type: ignore[assignment]

    if not _dlsym_cf_callbacks():
        return False

    _hid_bindings_ok = True
    log.info("HID layer: bound CF + IOKit symbols on Apple Silicon")
    return True


def hid_layer_ready() -> bool:
    return _try_bind_hid()


# ── CF helpers ───────────────────────────────────────────────────────


def _cfstr(s: str) -> ctypes.c_void_p | None:
    assert _cf is not None
    return _cf.CFStringCreateWithCString(
        None, s.encode("utf-8"), _kCFStringEncodingUTF8,
    )


def _cfnumber_i32(v: int) -> ctypes.c_void_p | None:
    assert _cf is not None
    buf = ctypes.c_int32(v)
    return _cf.CFNumberCreate(None, _kCFNumberSInt32Type, ctypes.byref(buf))


def _matching_dict(page: int, usage: int) -> ctypes.c_void_p | None:
    assert _cf is not None
    if not _kcf_key_callbacks_addr or not _kcf_value_callbacks_addr:
        return None
    kcb = ctypes.c_void_p(_kcf_key_callbacks_addr)
    vcb = ctypes.c_void_p(_kcf_value_callbacks_addr)
    d = _cf.CFDictionaryCreateMutable(None, 0, kcb, vcb)
    if not d:
        return None
    k1 = _cfstr("PrimaryUsagePage")
    k2 = _cfstr("PrimaryUsage")
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
        return ""
    buf = ctypes.create_string_buffer(512)
    if _cf.CFStringGetCString(ref, buf, len(buf), _kCFStringEncodingUTF8):
        return buf.value.decode("utf-8", errors="replace")
    return ""


def _iohid_field_base(event_type: int) -> int:
    return int(event_type) << 16


# ── Sensor normalisation ─────────────────────────────────────────────


def _normalize_thermal_celsius(name: str, val: float) -> float | None:
    """Turn an IOHID thermal float into a plausible °C, or None.

    PMU *tdev* channels sometimes report raw sp78 (value ≈ °C×256);
    most others report °C directly.  Out-of-range readings are
    dropped so a bad ABI call never surfaces as an absurd temp.
    """
    if val != val or val in (float("inf"), float("-inf")):
        return None
    v = float(val)
    name_l = name.lower()
    if "tdev" in name_l:
        m = re.search(r"tdev([1-9])", name_l)
        if m and v > 130.0:
            v = v / 256.0
    if -40.0 <= v <= 150.0:
        return v
    if 256.0 <= v <= 130.0 * 256.0:
        c = v / 256.0
        if -40.0 <= c <= 150.0:
            return c
    return None


# ── HID readout ──────────────────────────────────────────────────────


def _collect_names_values(
    page: int,
    usage: int,
    event_type: int,
    *,
    thermal: bool,
    power_scale: float,
) -> list[tuple[str, float]]:
    """Enumerate one HID usage page; return ``[(product_name, value), …]``."""
    if not _try_bind_hid():
        return []
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
            prop_product = _cfstr("Product")
            if not prop_product:
                return out
            try:
                for i in range(n):
                    sc = _cf.CFArrayGetValueAtIndex(services, i)
                    if not sc:
                        continue
                    name_ref = _iokit.IOHIDServiceClientCopyProperty(
                        sc, prop_product,
                    )
                    name = _cfstring_to_str(name_ref) if name_ref else "noname"
                    if name_ref:
                        _cf.CFRelease(name_ref)
                    ev = _iokit.IOHIDServiceClientCopyEvent(
                        sc, int(event_type), 0, 0,
                    )
                    val = 0.0
                    if ev:
                        val = float(_iokit.IOHIDEventGetFloatValue(
                            ev, _iohid_field_base(event_type),
                        ))
                        _cf.CFRelease(ev)
                    if power_scale != 1.0:
                        val = val / power_scale
                    if thermal:
                        norm = _normalize_thermal_celsius(name, val)
                        if norm is None:
                            continue
                        val = norm
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


def _dedupe_by_name(
    pairs: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Keep the first row per Product name (PMU channels often repeat)."""
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for name, val in pairs:
        key = name.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append((name, val))
    return out


# ── CPU/GPU heuristics (from legacy sensors._apple_silicon_hid_*) ────


_CPU_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)performance\s+core"),
    re.compile(r"(?i)cpu\s+performance"),
    re.compile(r"(?i)efficiency\s+core"),
    re.compile(r"(?i)cpu\s+efficiency"),
    re.compile(r"(?i)tdie"),
    re.compile(r"(?i)tdev"),
    re.compile(r"(?i)\bTP[0-9]"),
    re.compile(r"(?i)soc|package|pmu_t"),
)

_GPU_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)graphics"),
    re.compile(r"(?i)gpu"),
    re.compile(r"(?i)gddr"),
    re.compile(r"(?i)grfx"),
)


def _hottest_matching(
    pairs: list[tuple[str, float]],
    patterns: tuple[re.Pattern[str], ...],
) -> float | None:
    """Highest reading among rows whose name matches any pattern,
    walking patterns in declared priority order."""
    for pat in patterns:
        best: float | None = None
        for name, val in pairs:
            if pat.search(name) and (best is None or val > best):
                best = val
        if best is not None:
            return best
    return None


# ── Shared snapshot ──────────────────────────────────────────────────


class _HidSnapshot:
    """Shared, TTL-cached HID reading.  Used by CPU + GPU sources so a
    metrics tick costs one IOKit traversal, not two.

    DI seam: ``reader`` is a no-arg callable that returns thermal
    pairs.  Production binds it to ``read_thermal_pairs``; tests
    inject canned pairs to exercise the heuristic from Linux.
    """

    def __init__(
        self,
        *,
        reader: Callable[[], list[tuple[str, float]]] | None = None,
        ttl_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = reader if reader is not None else read_thermal_pairs
        self._ttl = ttl_seconds
        self._clock = clock
        self._last_at: float = -1.0
        self._pairs: list[tuple[str, float]] = []

    def pairs(self) -> list[tuple[str, float]]:
        self._refresh_if_stale()
        return self._pairs

    def cpu_temp(self) -> float | None:
        return _hottest_matching(self.pairs(), _CPU_PATTERNS)

    def gpu_temp(self) -> float | None:
        return _hottest_matching(self.pairs(), _GPU_PATTERNS)

    def _refresh_if_stale(self) -> None:
        now = self._clock()
        if self._last_at >= 0 and (now - self._last_at) < self._ttl:
            return
        self._pairs = self._read()
        self._last_at = now
        log.debug("HidSnapshot.refresh: %d thermal pairs", len(self._pairs))


def read_thermal_pairs() -> list[tuple[str, float]]:
    """Dedup-by-name thermal readings from the HID event system."""
    log.debug("read_thermal_pairs: called")
    raw = _collect_names_values(
        _PAGE_THERMAL, _USAGE_THERMAL, _kIOHIDEventTypeTemperature,
        thermal=True, power_scale=1.0,
    )
    return _dedupe_by_name(raw)


# ── Sources ──────────────────────────────────────────────────────────


class MacosHidCpu(CpuSource):
    """CPU temperature via Apple Silicon HID hub.

    Returns None for usage / freq / power — the chain falls through
    to other sources.  On Intel / Linux this source is a no-op
    because ``_try_bind_hid`` returns False.
    """

    def __init__(self, snapshot: _HidSnapshot) -> None:
        self._snap = snapshot

    @property
    def name(self) -> str:
        return "Apple HID (CPU)"

    def temp(self) -> float | None:
        return self._snap.cpu_temp()

    def usage(self) -> float | None:
        return None

    def freq(self) -> float | None:
        return None

    def power(self) -> float | None:
        return None


class MacosHidGpu(GpuSource):
    """Apple Silicon integrated GPU temperature via HID hub.

    Vendor key ``apple:0`` matches what the aggregator's
    vendor-normalised dedup expects.  Returns None for everything
    except ``temp`` — power/usage/clock live in IOReport.
    """

    def __init__(self, snapshot: _HidSnapshot) -> None:
        self._snap = snapshot

    @property
    def key(self) -> str:
        return "apple:0"

    @property
    def name(self) -> str:
        return "Apple HID (GPU)"

    @property
    def is_discrete(self) -> bool:
        return False

    def temp(self) -> float | None:
        return self._snap.gpu_temp()

    def usage(self) -> float | None:
        return None

    def clock(self) -> float | None:
        return None

    def power(self) -> float | None:
        return None

    def fan(self) -> float | None:
        return None

    def vram_used(self) -> float | None:
        return None

    def vram_total(self) -> float | None:
        return None
