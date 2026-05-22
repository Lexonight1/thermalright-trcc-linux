"""HWiNFO64 shared-memory sensor source — best-class when user has it.

HWiNFO64 publishes a memory-mapped file named ``Global\\HWiNFO_SENS_SM2``
containing the full live sensor tree.  When the user has HWiNFO64
installed and "Shared Memory Support" enabled, this source attaches to
that MMF read-only and reads sensor values directly — no spawning, no
kernel driver of our own, no Defender concern from our side.

This is the same path the C# Thermalright app uses
(``shareMemory_SysInfo``).  We can't redistribute HWiNFO64 (license),
so this source is *detect-only*: if the MMF exists, we use it; if not,
LHM (tier 2) takes over.

Architecture (hexagonal, two adapters on one port):

    HWiNFOSource ──depends on──> _MappingPort (ABC)
                                     │
                       ┌─────────────┴─────────────┐
                  _HWiNFOMapping              _BytesMapping
                  Win32 ctypes (prod)         bytes buffer (tests + dump)

The pure ``_parse_header(bytes) -> _Header`` function is the only place
that knows the on-the-wire layout; both adapters return bytes and the
parser converts them to typed fields.  ``dev/dump_hwinfo_shm.py``
captures a live MMF dump using ``_HWiNFOMapping`` and shares the same
``_parse_header`` to size the writeout — zero duplicate ctypes wiring.

Format reference (reverse-engineered by ``namazso``):
https://gist.github.com/namazso/0c37be5a53863954c8c8279f66cfb1cc

Header: ``HWiNFO_SENSORS_SHARED_MEM2`` (44 bytes, magic ``0x49576853`` = 'SiWH').
Sensor element: 264 bytes (id, instance, two 128-byte names).
Entry element: 316 bytes (type, sensor_index, id, two 128-byte names,
16-byte unit, four doubles: value / min / max / avg).
"""
from __future__ import annotations

import ctypes
import logging
import struct
import sys
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, NamedTuple

from trcc.legacy.adapters.system.windows.sources._base import WindowsSensorSource
from trcc.legacy.core.models import SensorInfo

if TYPE_CHECKING:
    from trcc.legacy.adapters.system.windows.enumerator import WindowsSensorEnumerator

log = logging.getLogger(__name__)


# ── MMF + Win32 constants ──────────────────────────────────────────────

_MMF_NAME = "Global\\HWiNFO_SENS_SM2"
_FILE_MAP_READ = 0x0004
_HWINFO_MAGIC = 0x49576853  # 'SiWH' little-endian
_NAME_LEN = 128             # HWINFO_SENSORS_STRING_LEN2
_UNIT_LEN = 16              # HWINFO_UNIT_STRING_LEN

# Header: magic, ver, ver2, last_update, sec_off, sec_size, sec_count,
#         ent_off, ent_size, ent_count.
_HEADER_FMT = '<IIIqIIIIII'
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Sensor record: id (u32), instance (u32), name_orig[128], name_user[128].
_SENSOR_FMT = f'<II{_NAME_LEN}s{_NAME_LEN}s'

# Entry record: type (u32), sensor_index (u32), id (u32),
# name_orig[128], name_user[128], unit[16], value, min, max, avg.
_ENTRY_FMT = f'<III{_NAME_LEN}s{_NAME_LEN}s{_UNIT_LEN}sdddd'

# Offset of the live ``value`` double inside an entry record:
# 3×u32 + 2×name + unit = 12 + 256 + 16 = 284
_ENTRY_VALUE_OFFSET = 12 + (_NAME_LEN * 2) + _UNIT_LEN

# SensorType enum → (TRCC category, default unit if HWiNFO doesn't supply one).
# Reflects the namazso enum.  ``None`` (0) and ``Other`` (8) are skipped —
# they're either uninitialised or derived stats that overlap psutil.
_TYPE_MAP: dict[int, tuple[str, str]] = {
    1: ('temperature', '°C'),
    2: ('voltage',     'V'),
    3: ('fan',         'RPM'),
    4: ('current',     'A'),
    5: ('power',       'W'),
    6: ('clock',       'MHz'),
    7: ('usage',       '%'),
}


def _decode_cstr(blob: bytes) -> str:
    """Decode a fixed-length NUL-terminated C string (latin-1, lossless)."""
    return blob.split(b'\x00', 1)[0].decode('latin-1', errors='replace').strip()


# ── Pure header parser ─────────────────────────────────────────────────

class _Header(NamedTuple):
    """Decoded HWiNFO shared-memory header.  All u32 except ``last_update``."""

    magic: int
    version: int
    version2: int
    last_update: int    # int64 unix timestamp
    sec_off: int        # sensor section file offset
    sec_size: int       # bytes per sensor record
    sec_count: int      # number of sensor records
    ent_off: int        # entry (reading) section file offset
    ent_size: int       # bytes per entry record
    ent_count: int      # number of entry records

    @property
    def total_size(self) -> int:
        """Bytes from start of MMF that contain live data — the high-water mark.

        Anything beyond is unused padding inside the file mapping object.
        Used by the dump script to capture exactly the live region.
        """
        return max(
            self.sec_off + self.sec_size * self.sec_count,
            self.ent_off + self.ent_size * self.ent_count,
        )


def _parse_header(data: bytes) -> _Header:
    """Pure ``bytes → _Header``.  Raises ``ValueError`` if too short.

    Validation of the magic value is the caller's job (kept separate so
    this stays a pure decode for any header-shaped bytes — useful for
    edge-case tests of malformed headers).
    """
    if len(data) < _HEADER_SIZE:
        raise ValueError(
            f"HWiNFO header too short: {len(data)} < {_HEADER_SIZE}",
        )
    return _Header(*struct.unpack_from(_HEADER_FMT, data, 0))


# ── Port + adapters ────────────────────────────────────────────────────

class _MappingPort(ABC):
    """Byte-oriented I/O abstraction for the HWiNFO MMF.

    Two adapters today: the Win32 ctypes adapter (production) and the
    in-memory bytes adapter (tests + ``dump_hwinfo_shm.py``).  ``open()``
    is fail-soft — returning ``False`` is the documented "source not
    available" signal, not an exception.
    """

    @abstractmethod
    def open(self) -> bool:
        """Make the byte region available for ``read()``.  ``False`` = unavailable."""

    @abstractmethod
    def read(self, offset: int, size: int) -> bytes:
        """Return up to ``size`` bytes starting at ``offset``."""

    @abstractmethod
    def close(self) -> None:
        """Release any resources acquired by ``open()``."""


class _HWiNFOMapping(_MappingPort):
    """Production adapter — Win32 ``OpenFileMappingW`` + ``MapViewOfFile``.

    Owns the file-mapping handle + the mapped view pointer.  ``open()``
    succeeds only on Windows with HWiNFO64 running and Shared Memory
    Support enabled; everywhere else it returns ``False`` fast.
    """

    __slots__ = ("_handle", "_kernel32", "_view")

    def __init__(self) -> None:
        self._handle: int = 0
        self._view: int = 0
        self._kernel32: Any = None

    def open(self) -> bool:
        if sys.platform != 'win32':
            return False
        try:
            self._kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]
        except (AttributeError, OSError) as e:
            log.debug("ctypes.windll.kernel32 unavailable: %s", e)
            return False

        # OpenFileMappingW(dwDesiredAccess, bInheritHandle, lpName) → HANDLE
        self._kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        self._kernel32.OpenFileMappingW.argtypes = [
            ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p,
        ]
        handle_result = self._kernel32.OpenFileMappingW(
            _FILE_MAP_READ, False, _MMF_NAME,
        )
        self._handle = handle_result if handle_result is not None else 0
        if self._handle == 0:
            return False

        # MapViewOfFile(hMap, dwAccess, offHigh, offLow, bytesToMap) → LPVOID
        self._kernel32.MapViewOfFile.restype = ctypes.c_void_p
        self._kernel32.MapViewOfFile.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t,
        ]
        # bytesToMap=0 → map until end of file mapping object.
        view_result = self._kernel32.MapViewOfFile(
            self._handle, _FILE_MAP_READ, 0, 0, 0,
        )
        self._view = view_result if view_result is not None else 0
        if self._view == 0:
            self.close()
            return False
        return True

    def close(self) -> None:
        if self._kernel32 is None:
            return
        if self._view != 0:
            try:
                self._kernel32.UnmapViewOfFile(ctypes.c_void_p(self._view))
            except OSError:
                pass
            self._view = 0
        if self._handle != 0:
            try:
                self._kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            except OSError:
                pass
            self._handle = 0

    def read(self, offset: int, size: int) -> bytes:
        if self._view == 0:
            return b''
        return ctypes.string_at(self._view + offset, size)


class _BytesMapping(_MappingPort):
    """In-memory adapter — bytes buffer.  For tests + ``dump_hwinfo_shm`` verify.

    ``open()`` succeeds when the buffer is large enough to hold a header.
    ``read(offset, size)`` slices; out-of-range slices return shorter bytes
    (same defensive contract as the Win32 adapter, which returns whatever
    ``ctypes.string_at`` reads at the address).
    """

    __slots__ = ("_data",)

    def __init__(self, data: bytes) -> None:
        self._data = data

    def open(self) -> bool:
        return len(self._data) >= _HEADER_SIZE

    def read(self, offset: int, size: int) -> bytes:
        end = offset + size
        return self._data[offset:end]

    def close(self) -> None:
        pass


# ── HWiNFOSource — the registered strategy ─────────────────────────────

@WindowsSensorSource.register('hwinfo')
class HWiNFOSource(WindowsSensorSource):
    """HWiNFO64 strategy: read ``Global\\HWiNFO_SENS_SM2`` directly.

    The mapping port is injected (default ``_HWiNFOMapping``); tests pass
    ``_BytesMapping(fixture_bytes)`` so the parser is verifiable on Linux
    with no Win32 surface.
    """

    priority = 10  # Tried first — most accurate when user has HWiNFO.
    name = "HWiNFO64"
    provides_gpu = True  # HWiNFO covers NVIDIA / AMD / Intel GPUs.

    __slots__ = ("_entry_layout", "_gpu_seen", "_mapping", "_sensor_names")

    def __init__(self, mapping: _MappingPort | None = None) -> None:
        self._mapping: _MappingPort = mapping if mapping is not None else _HWiNFOMapping()
        # Cache discovered topology so poll() doesn't re-walk the header.
        # sensor_index → display label ("CPU [#0]", "GeForce RTX 4090", ...).
        self._sensor_names: dict[int, str] = {}
        # One tuple per registered reading: (sid, offset_of_value_field).
        self._entry_layout: list[tuple[str, int]] = []
        self._gpu_seen = False

    # ── Strategy hooks ───────────────────────────────────────────────────

    def probe(self) -> bool:
        if not self._mapping.open():
            return False
        try:
            header = _parse_header(self._mapping.read(0, _HEADER_SIZE))
        except ValueError as e:
            log.debug("HWiNFO header parse failed: %s", e)
            self._mapping.close()
            return False
        if header.magic != _HWINFO_MAGIC:
            log.debug("HWiNFO MMF magic mismatch: got 0x%08x expected 0x%08x",
                      header.magic, _HWINFO_MAGIC)
            self._mapping.close()
            return False
        return True

    def contribute(self, enum: WindowsSensorEnumerator) -> None:
        """Walk the sensor + entry sections; register one SensorInfo per entry."""
        header = self._read_header()
        self._read_sensor_names(header)
        self._register_entries(enum, header)
        log.info("HWiNFO discovery: %d readings across %d sensor groups",
                 len(self._entry_layout), len(self._sensor_names))
        enum._register_poll(self.poll)

    def poll(self, enum: WindowsSensorEnumerator,
             readings: dict[str, float]) -> None:
        """Re-read every cached entry offset for fresh ``value`` doubles."""
        for sid, value_offset in self._entry_layout:
            try:
                raw = self._mapping.read(value_offset, 8)
                if len(raw) == 8:
                    readings[sid] = struct.unpack_from('<d', raw, 0)[0]
            except OSError as e:
                # MMF can vanish if HWiNFO exits mid-poll — bail cleanly.
                log.debug("HWiNFO poll read failed: %s", e)
                return

    def stop(self) -> None:
        self._mapping.close()
        self._sensor_names.clear()
        self._entry_layout.clear()

    # ── GPU enumeration hook ─────────────────────────────────────────────

    def gpu_list(self) -> list[tuple[str, str]]:
        """Return GPU-like sensor groups for the device picker."""
        return [
            (f'hwinfo:{idx}', label)
            for idx, label in self._sensor_names.items()
            if _looks_like_gpu(label)
        ]

    @property
    def gpu_seen(self) -> bool:
        return self._gpu_seen

    # ── Header / section walkers ─────────────────────────────────────────

    def _read_header(self) -> _Header:
        return _parse_header(self._mapping.read(0, _HEADER_SIZE))

    def _read_sensor_names(self, header: _Header) -> None:
        """Populate ``self._sensor_names`` from the sensor section."""
        self._sensor_names.clear()
        sensor_struct_size = struct.calcsize(_SENSOR_FMT)
        for i in range(header.sec_count):
            raw = self._mapping.read(
                header.sec_off + i * header.sec_size,
                header.sec_size,
            )
            if len(raw) < sensor_struct_size:
                continue
            _id, _inst, name_orig, name_user = struct.unpack_from(_SENSOR_FMT, raw, 0)
            user_name = _decode_cstr(name_user)
            orig_name = _decode_cstr(name_orig)
            label = user_name if user_name != '' else orig_name
            self._sensor_names[i] = label if label != '' else f'Sensor {i}'

    def _register_entries(
        self, enum: WindowsSensorEnumerator, header: _Header,
    ) -> None:
        """Register one ``SensorInfo`` per entry; cache each value-field offset."""
        self._entry_layout.clear()
        self._gpu_seen = False
        entry_struct_size = struct.calcsize(_ENTRY_FMT)

        for i in range(header.ent_count):
            entry_start = header.ent_off + i * header.ent_size
            raw = self._mapping.read(entry_start, header.ent_size)
            if len(raw) < entry_struct_size:
                continue
            (
                stype, sensor_index, entry_id,
                name_orig, name_user, unit_blob,
                _value, _vmin, _vmax, _vavg,
            ) = struct.unpack_from(_ENTRY_FMT, raw, 0)

            mapping = _TYPE_MAP.get(stype)
            if mapping is None:
                continue  # None / Other — skip
            category, default_unit = mapping
            decoded_unit = _decode_cstr(unit_blob)
            unit = decoded_unit if decoded_unit != '' else default_unit
            user_name = _decode_cstr(name_user)
            orig_name = _decode_cstr(name_orig)
            if user_name != '':
                entry_name = user_name
            elif orig_name != '':
                entry_name = orig_name
            else:
                entry_name = f'Reading {entry_id}'
            parent = self._sensor_names.get(sensor_index, f'Sensor {sensor_index}')
            label = f'{parent} {entry_name}'.strip()
            sid = f'hwinfo:{sensor_index}:{entry_id}'

            enum._sensors.append(
                SensorInfo(sid, label, category, unit, 'hwinfo'),
            )
            self._entry_layout.append((sid, entry_start + _ENTRY_VALUE_OFFSET))

            if category == 'temperature' and _looks_like_gpu(parent):
                self._gpu_seen = True


# ── Helpers ─────────────────────────────────────────────────────────────

_GPU_HINTS = ('gpu', 'geforce', 'radeon', 'graphics', 'nvidia', 'amd ', 'intel arc')


def _looks_like_gpu(label: str) -> bool:
    """Heuristic for GPU-ish sensor parent labels.  HWiNFO doesn't tag types."""
    low = label.lower()
    return any(hint in low for hint in _GPU_HINTS)


__all__ = [
    'HWiNFOSource',
    '_BytesMapping',
    '_HWiNFOMapping',
    '_Header',
    '_MappingPort',
    '_parse_header',
]
