"""HWiNFO64 shared-memory sensor source.

When the user has HWiNFO64 installed with "Shared Memory Support"
enabled, the running instance publishes a memory-mapped file named
``Global\\HWiNFO_SENS_SM2`` containing the live sensor tree.  This
module attaches to it read-only and surfaces CPU + GPU readings
through next/'s ``CpuSource`` / ``GpuSource`` ABCs.

Wire format (reverse-engineered by ``namazso``):
https://gist.github.com/namazso/0c37be5a53863954c8c8279f66cfb1cc

  Header  (44 bytes)
      magic 0x49576853 'SiWH', version, last_update, sec_off, sec_size,
      sec_count, ent_off, ent_size, ent_count.
  Sensor  (264 bytes)
      id (u32), instance (u32), name_orig[128], name_user[128].
  Entry   (316 bytes)
      type (u32), sensor_index (u32), id (u32),
      name_orig[128], name_user[128], unit[16],
      value (f64), min (f64), max (f64), avg (f64).

Architecture mirrors legacy ``windows/sources/hwinfo.py`` — pure
``_parse_header(bytes)`` so tests can feed canned MMF dumps through the
same code path as production.  Production uses the Win32 ctypes
mapping; tests use ``_BytesMapping``.

Read-only: this module never spawns HWiNFO; it consumes whatever the
user already has running.
"""
from __future__ import annotations

import logging
import struct
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, NamedTuple

from ...core.ports import CpuSource, GpuSource

log = logging.getLogger(__name__)


# ── On-the-wire layout constants ─────────────────────────────────────


_MMF_NAME = "Global\\HWiNFO_SENS_SM2"
_FILE_MAP_READ = 0x0004
_HWINFO_MAGIC = 0x49576853                  # 'SiWH' little-endian
_NAME_LEN = 128                             # HWINFO_SENSORS_STRING_LEN2
_UNIT_LEN = 16                              # HWINFO_UNIT_STRING_LEN

_HEADER_FMT = "<IIIqIIIIII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Per-entry offset of the live ``value`` double: 3×u32 + 2×name + unit
_ENTRY_VALUE_OFFSET = 12 + (_NAME_LEN * 2) + _UNIT_LEN
_ENTRY_NAME_ORIG_OFFSET = 12               # after the three u32 fields
_ENTRY_NAME_USER_OFFSET = 12 + _NAME_LEN

# HWiNFO SensorType enum
TYPE_TEMP = 1
TYPE_VOLTAGE = 2
TYPE_FAN = 3
TYPE_CURRENT = 4
TYPE_POWER = 5
TYPE_CLOCK = 6
TYPE_USAGE = 7

# How long to reuse the entry-value snapshot before re-walking the
# entries section.  100 ms covers a full poll's worth of method calls
# from BaselineSensors without sticking on stale data.
_SNAPSHOT_TTL_S = 0.1


def _decode_cstr(blob: bytes) -> str:
    """Decode a fixed-length NUL-terminated C string (latin-1, lossless)."""
    return blob.split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()


class _Header(NamedTuple):
    """Decoded HWiNFO header."""
    magic: int
    version: int
    version2: int
    last_update: int
    sec_off: int
    sec_size: int
    sec_count: int
    ent_off: int
    ent_size: int
    ent_count: int


def _parse_header(buf: bytes) -> _Header:
    """Decode the 44-byte header.  Raises ValueError on bad magic / short buffer."""
    if len(buf) < _HEADER_SIZE:
        raise ValueError(
            f"HWiNFO header too short: {len(buf)} < {_HEADER_SIZE}",
        )
    fields = struct.unpack_from(_HEADER_FMT, buf, 0)
    header = _Header(*fields)
    if header.magic != _HWINFO_MAGIC:
        raise ValueError(
            f"HWiNFO magic mismatch: 0x{header.magic:08x} != "
            f"0x{_HWINFO_MAGIC:08x} (HWiNFO not running or wrong MMF)",
        )
    return header


# =========================================================================
# Mapping port — ABC for "give me bytes from the MMF"
# =========================================================================


class _MappingPort(ABC):
    """Adapter contract for the HWiNFO shared-memory backing store."""

    @abstractmethod
    def read(self, offset: int, length: int) -> bytes: ...

    @abstractmethod
    def close(self) -> None: ...


class _BytesMapping(_MappingPort):
    """Test seam — wraps a ``bytes`` buffer captured from a real MMF dump."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, offset: int, length: int) -> bytes:
        return self._data[offset:offset + length]

    def close(self) -> None:
        pass


class _HWiNFOMapping(_MappingPort):
    """Win32 file-mapping of ``Global\\HWiNFO_SENS_SM2`` via ctypes.

    Constructor raises ``OSError`` when the MMF doesn't exist (HWiNFO
    not running) — caller catches and falls through the chain.
    """

    _kernel32: Any                  # set in __init__ behind a sys.platform gate
    _handle: Any
    _view: Any

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("HWiNFO MMF only available on Windows")
        # ctypes calls live here, gated by sys.platform so static analyzers
        # on non-Windows boxes don't trip on missing kernel32 attributes.
        import ctypes

        self._kernel32 = ctypes.windll.kernel32     # type: ignore[attr-defined]
        self._handle = self._kernel32.OpenFileMappingW(
            _FILE_MAP_READ, False, _MMF_NAME,
        )
        if not self._handle:
            err = ctypes.get_last_error()           # type: ignore[attr-defined]
            raise OSError(
                f"OpenFileMappingW({_MMF_NAME!r}) failed: WinError {err} — "
                "HWiNFO64 not running or 'Shared Memory Support' disabled",
            )
        self._view = self._kernel32.MapViewOfFile(
            self._handle, _FILE_MAP_READ, 0, 0, 0,
        )
        if not self._view:
            err = ctypes.get_last_error()           # type: ignore[attr-defined]
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
            raise OSError(f"MapViewOfFile failed: WinError {err}")

    def read(self, offset: int, length: int) -> bytes:
        import ctypes

        buf = (ctypes.c_char * length)()
        ctypes.memmove(buf, self._view + offset, length)  # type: ignore[operator]
        return bytes(buf)

    def close(self) -> None:
        if getattr(self, "_view", None):
            self._kernel32.UnmapViewOfFile(self._view)
            self._view = None
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _default_mapping_factory() -> _MappingPort | None:
    """Open the production MMF, or return None on failure."""
    if sys.platform != "win32":
        return None
    try:
        return _HWiNFOMapping()
    except OSError:
        log.debug("HWiNFO mapping unavailable")
        return None


# =========================================================================
# Entry index — cached at construction, indexes (name → value-offset)
# =========================================================================


class _EntryRef(NamedTuple):
    """One indexed entry: the byte offset of its ``value`` field + name."""
    entry_type: int           # 1-7 per HWiNFO SensorType
    sensor_index: int         # parent Sensor section row index
    name: str                 # user-edited or original sensor entry name
    value_offset: int         # MMF byte offset of the f64 value


class _Snapshot:
    """One mapping's parsed entry index with cached live values.

    Built once at construction (walks the entries section, recording
    each ``value_offset``); per-tick refresh re-reads just the doubles
    at those offsets.  Cheap once cached.
    """

    def __init__(self, mapping: _MappingPort) -> None:
        self._mapping = mapping
        self._entries: list[_EntryRef] = []
        self._sensors: list[str] = []
        self._values: dict[int, float] = {}
        self._last_refresh: float = 0.0
        self._index()

    def _index(self) -> None:
        """Walk the entries + sensors sections, cache every value offset."""
        header_bytes = self._mapping.read(0, _HEADER_SIZE)
        header = _parse_header(header_bytes)

        for i in range(header.sec_count):
            row_off = header.sec_off + i * header.sec_size
            buf = self._mapping.read(row_off, header.sec_size)
            # Sensor row: id (u32), instance (u32), name_orig[128], name_user[128]
            name_user_off = 8 + _NAME_LEN
            user_name = _decode_cstr(buf[name_user_off:name_user_off + _NAME_LEN])
            orig_name = _decode_cstr(buf[8:8 + _NAME_LEN])
            self._sensors.append(user_name or orig_name)

        for i in range(header.ent_count):
            row_off = header.ent_off + i * header.ent_size
            head = self._mapping.read(row_off, _ENTRY_VALUE_OFFSET)
            entry_type, sensor_index, _id = struct.unpack_from("<III", head, 0)
            name_user = _decode_cstr(
                head[_ENTRY_NAME_USER_OFFSET:_ENTRY_NAME_USER_OFFSET + _NAME_LEN],
            )
            name_orig = _decode_cstr(
                head[_ENTRY_NAME_ORIG_OFFSET:_ENTRY_NAME_ORIG_OFFSET + _NAME_LEN],
            )
            self._entries.append(_EntryRef(
                entry_type=entry_type,
                sensor_index=sensor_index,
                name=name_user or name_orig,
                value_offset=row_off + _ENTRY_VALUE_OFFSET,
            ))

    def refresh(self) -> None:
        """Re-read every entry's f64 value into the cache.

        Throttled to ``_SNAPSHOT_TTL_S`` so multiple ``cpu.temp() +
        cpu.usage()`` calls in the same poll iteration share one read.
        """
        now = time.monotonic()
        if now - self._last_refresh < _SNAPSHOT_TTL_S and self._values:
            return
        for idx, entry in enumerate(self._entries):
            raw = self._mapping.read(entry.value_offset, 8)
            self._values[idx] = struct.unpack_from("<d", raw, 0)[0]
        self._last_refresh = now

    # ── Query helpers ───────────────────────────────────────────────

    def sensor_name(self, sensor_index: int) -> str:
        if 0 <= sensor_index < len(self._sensors):
            return self._sensors[sensor_index]
        return ""

    def find(
        self,
        entry_type: int,
        *,
        sensor_name_contains: str | None = None,
        entry_name_contains: str | None = None,
    ) -> float | None:
        """First entry matching the filter, returns its live value."""
        self.refresh()
        sn_needle = sensor_name_contains.lower() if sensor_name_contains else None
        en_needle = entry_name_contains.lower() if entry_name_contains else None
        for idx, entry in enumerate(self._entries):
            if entry.entry_type != entry_type:
                continue
            if sn_needle is not None:
                parent = self.sensor_name(entry.sensor_index).lower()
                if sn_needle not in parent:
                    continue
            if en_needle is not None and en_needle not in entry.name.lower():
                continue
            return self._values.get(idx)
        return None

    def max_value(
        self,
        entry_type: int,
        *,
        sensor_name_contains: str | None = None,
    ) -> float | None:
        """Max value across all entries of *entry_type* under a sensor row."""
        self.refresh()
        sn_needle = sensor_name_contains.lower() if sensor_name_contains else None
        best: float | None = None
        for idx, entry in enumerate(self._entries):
            if entry.entry_type != entry_type:
                continue
            if sn_needle is not None:
                parent = self.sensor_name(entry.sensor_index).lower()
                if sn_needle not in parent:
                    continue
            value = self._values.get(idx)
            if value is None:
                continue
            if best is None or value > best:
                best = value
        return best


# =========================================================================
# Snapshot factory — module-level cache so CPU + GPU sources share one
# =========================================================================


_shared_snapshot: _Snapshot | None = None


def _shared(
    mapping_factory: Callable[[], _MappingPort | None] = _default_mapping_factory,
) -> _Snapshot | None:
    """Return a process-wide snapshot, building it once on first use."""
    global _shared_snapshot
    if _shared_snapshot is not None:
        return _shared_snapshot
    mapping = mapping_factory()
    if mapping is None:
        return None
    try:
        _shared_snapshot = _Snapshot(mapping)
    except ValueError as e:
        log.debug("HWiNFO snapshot index failed: %s", e)
        mapping.close()
        return None
    return _shared_snapshot


def reset_snapshot() -> None:
    """Drop the cached snapshot — tests reset between cases."""
    log.info("reset_snapshot: called")
    global _shared_snapshot
    if _shared_snapshot is not None:
        _shared_snapshot._mapping.close()
        _shared_snapshot = None


# =========================================================================
# HwinfoCpu / HwinfoGpu
# =========================================================================


class HwinfoCpu(CpuSource):
    """CPU readings via HWiNFO64 shared memory."""

    def __init__(
        self,
        *,
        snapshot_factory: Callable[[], _Snapshot | None] = _shared,
    ) -> None:
        self._snapshot = snapshot_factory()

    @property
    def name(self) -> str:
        return "HWiNFO64 (CPU)"

    def temp(self) -> float | None:
        if self._snapshot is None:
            return None
        # Prefer CPU Package; otherwise hottest core under a CPU sensor row.
        explicit = self._snapshot.find(TYPE_TEMP, entry_name_contains="cpu package")
        if explicit is not None:
            return explicit
        return self._snapshot.max_value(TYPE_TEMP, sensor_name_contains="cpu")

    def usage(self) -> float | None:
        if self._snapshot is None:
            return None
        return self._snapshot.find(TYPE_USAGE, entry_name_contains="total cpu usage") \
            or self._snapshot.max_value(TYPE_USAGE, sensor_name_contains="cpu")

    def freq(self) -> float | None:
        if self._snapshot is None:
            return None
        # Highest core clock — HWiNFO publishes one entry per core.
        return self._snapshot.max_value(TYPE_CLOCK, sensor_name_contains="cpu")

    def power(self) -> float | None:
        if self._snapshot is None:
            return None
        return self._snapshot.find(TYPE_POWER, entry_name_contains="cpu package") \
            or self._snapshot.max_value(TYPE_POWER, sensor_name_contains="cpu")


class HwinfoGpu(GpuSource):
    """GPU readings via HWiNFO64 shared memory.

    Constructed by ``discover_hwinfo_gpus`` once per HWiNFO sensor row
    whose user-facing name contains "GPU".  The aggregator dedups by
    key with NVML / LHM equivalents using vendor-normalized keys.
    """

    def __init__(
        self,
        sensor_row_name: str,
        *,
        discrete: bool,
        snapshot_factory: Callable[[], _Snapshot | None] = _shared,
    ) -> None:
        self._snapshot = snapshot_factory()
        self._row_name = sensor_row_name
        self._discrete = discrete

    @property
    def key(self) -> str:
        lowered = self._row_name.lower()
        if "nvidia" in lowered or "geforce" in lowered or "rtx" in lowered or "gtx" in lowered:
            return "nvidia:0"
        if "amd" in lowered or "radeon" in lowered or "rx " in lowered:
            return "amd:0"
        if "intel" in lowered or "arc " in lowered:
            return "intel:0"
        return f"hwinfo:{self._row_name.lower().replace(' ', '_')}"

    @property
    def name(self) -> str:
        return self._row_name

    @property
    def is_discrete(self) -> bool:
        return self._discrete

    def _find(self, entry_type: int, *, entry_name_contains: str | None = None) -> float | None:
        if self._snapshot is None:
            return None
        return self._snapshot.find(
            entry_type,
            sensor_name_contains=self._row_name,
            entry_name_contains=entry_name_contains,
        )

    def _max(self, entry_type: int) -> float | None:
        if self._snapshot is None:
            return None
        return self._snapshot.max_value(entry_type, sensor_name_contains=self._row_name)

    def temp(self) -> float | None:
        return self._find(TYPE_TEMP, entry_name_contains="gpu temperature") \
            or self._max(TYPE_TEMP)

    def usage(self) -> float | None:
        return self._find(TYPE_USAGE, entry_name_contains="gpu core load") \
            or self._max(TYPE_USAGE)

    def clock(self) -> float | None:
        return self._find(TYPE_CLOCK, entry_name_contains="gpu clock") \
            or self._max(TYPE_CLOCK)

    def power(self) -> float | None:
        return self._find(TYPE_POWER, entry_name_contains="gpu power")

    def fan(self) -> float | None:
        return self._max(TYPE_FAN)

    def vram_used(self) -> float | None:
        # SmallData entries don't get a strong type signal in HWiNFO; the
        # entry name carries the semantics ("GPU Memory Allocated").
        if self._snapshot is None:
            return None
        return self._snapshot.find(
            TYPE_USAGE, sensor_name_contains=self._row_name,
            entry_name_contains="memory allocated",
        )

    def vram_total(self) -> float | None:
        return None  # HWiNFO doesn't publish total VRAM in a stable shape


# =========================================================================
# GPU discovery — one HwinfoGpu per GPU sensor row
# =========================================================================


def discover_hwinfo_gpus(
    *,
    snapshot_factory: Callable[[], _Snapshot | None] = _shared,
) -> list[HwinfoGpu]:
    """Enumerate GPU sensor rows from the HWiNFO snapshot."""
    log.info("discover_hwinfo_gpus: called")
    snap = snapshot_factory()
    if snap is None:
        return []
    out: list[HwinfoGpu] = []
    seen: set[str] = set()
    for row in snap._sensors:           # snapshot's cached sensor names
        lower = row.lower()
        if "gpu" not in lower and "graphics" not in lower:
            continue
        if row in seen:
            continue
        seen.add(row)
        # iGPU heuristic: Intel + non-Arc usually means integrated.
        discrete = not ("intel" in lower and "arc" not in lower)
        out.append(HwinfoGpu(row, discrete=discrete,
                             snapshot_factory=snapshot_factory))
    return out


# ── Test seam exports ────────────────────────────────────────────────


def _snapshot_from_bytes(data: bytes) -> _Snapshot:
    """Build a snapshot from a raw MMF byte buffer — used by tests."""
    return _Snapshot(_BytesMapping(data))


__all__ = [
    "TYPE_CLOCK",
    "TYPE_FAN",
    "TYPE_POWER",
    "TYPE_TEMP",
    "TYPE_USAGE",
    "HwinfoCpu",
    "HwinfoGpu",
    "_BytesMapping",
    "_Header",
    "_Snapshot",
    "_parse_header",
    "_snapshot_from_bytes",
    "discover_hwinfo_gpus",
    "reset_snapshot",
]


# Mark the unused wintypes import as intentional — kept in the ctypes
# branch so static analyzers (which only run on Linux) see the import
# graph reach into the wintypes module.
_ = Any
