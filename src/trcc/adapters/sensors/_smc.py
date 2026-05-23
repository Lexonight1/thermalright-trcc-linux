"""Apple SMC user-space client via IOKit ctypes.

Direct port of legacy ``src/trcc/adapters/system/macos/smc_client.py``
which derives its structure layout and read sequencing from iSMC's
``gosmc/smc.{c,h}`` (Dinko Korunic, GPL-3.0 — license-compatible with
TRCC's GPL-3.0).  Apple doesn't ship a public SMC API; every Mac
monitoring tool (Stats / iStat Menus / iSMC / macmon / this one)
re-implements the same SMC vocabulary against ``IOKit``.

Architecture:

  * ``SMCClient``           — owns the IOKit connection, provides
                              ``read_key_float()`` for one-shot reads.
  * ``_parse_smc_bytes``    — decodes ``sp78`` / ``fpe2`` / ``flt`` /
                              ``ui8`` / ``ui16`` / ``ui32`` / ``fp1f``
                              payloads.  Pure: takes raw bytes, returns
                              float.  Linux-testable in isolation.
  * ``SmcClientPort``       — protocol that ``SmcCpu`` / ``SmcGpu``
                              consume.  Real ``SMCClient`` satisfies it;
                              tests substitute an in-memory fake.

The ctypes IOKit binding code is import-safe on non-macOS: if
``IOKit.framework`` isn't on disk (Linux, Windows), ``_load_iokit``
returns ``None`` and ``SMCClient.open()`` cleanly reports failure.
That keeps the module a no-op everywhere except macOS without needing
a ``sys.platform`` gate on every call.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import struct
from typing import Protocol

log = logging.getLogger(__name__)


# =========================================================================
# SMC constants (per iSMC gosmc/smc.h)
# =========================================================================


_KERNEL_INDEX_SMC = 2
_SMC_CMD_READ_KEYINFO = 9
_SMC_CMD_READ_BYTES = 5
_kIOReturnSuccess = 0


# =========================================================================
# ctypes structures — sizeof(SMCKeyData_t) == 80 on Darwin
# =========================================================================


class _SMCKeyData_vers_t(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint8),
        ("minor", ctypes.c_uint8),
        ("build", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8),
        ("release", ctypes.c_uint16),
    ]


class _SMCKeyData_pLimitData_t(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("cpuPLimit", ctypes.c_uint32),
        ("gpuPLimit", ctypes.c_uint32),
        ("memPLimit", ctypes.c_uint32),
    ]


class _SMCKeyData_keyInfo_t(ctypes.Structure):
    _fields_ = [
        ("dataSize", ctypes.c_uint32),
        ("dataType", ctypes.c_uint32),
        ("dataAttributes", ctypes.c_uint8),
    ]


class _SMCKeyData_t(ctypes.Structure):
    _fields_ = [
        ("key", ctypes.c_uint32),
        ("vers", _SMCKeyData_vers_t),
        ("pLimitData", _SMCKeyData_pLimitData_t),
        ("keyInfo", _SMCKeyData_keyInfo_t),
        ("result", ctypes.c_uint8),
        ("status", ctypes.c_uint8),
        ("data8", ctypes.c_uint8),
        ("data32", ctypes.c_uint32),
        ("bytes", ctypes.c_uint8 * 32),
    ]


# =========================================================================
# Pure helpers — key encoding + payload decoding
# =========================================================================


def _smc_key_to_uint32(key: str) -> int:
    """Encode a 4-char SMC key as a big-endian uint32 ``mach_msg_id_t``."""
    raw = key.encode("latin-1", errors="replace")[:4].ljust(4, b" ")
    return struct.unpack(">I", raw)[0]


def _datatype_to_str(data_type: int) -> str:
    """Decode the 4-byte ``dataType`` field back to its FourCC string."""
    return struct.pack(">I", data_type).decode("latin-1", errors="replace")


def _decode_fan_rpm_raw(data_type: int, size: int, raw: bytes) -> float | None:
    """Decode fan RPM from an SMC payload, robust to Apple Silicon's
    ``flt``/``fpe2`` typing inconsistency.

    Apple Silicon ``F{i}Ac`` is usually advertised as 4-byte ``flt``
    and reads sanely as a little-endian float (~1300-3000 RPM).
    Intel uses ``fpe2``: big-endian uint16/4.  Mistyped keys (legacy
    firmware quirks) sometimes lie about the ``flt`` label — when
    the parsed float is implausible, fall back to fpe2 decoding.
    """
    if size < 2 or len(raw) < 2:
        return None
    dt = _datatype_to_str(data_type).rstrip()
    parsed = _parse_smc_bytes(data_type, raw, size)
    if dt == "flt" and size >= 4 and 0.0 <= parsed <= 20000.0 and parsed == parsed:
        return float(parsed)
    fpe2 = struct.unpack(">H", raw[:2])[0] / 4.0
    if 0.0 <= fpe2 <= 20000.0:
        return float(fpe2)
    return None


def _parse_smc_bytes(data_type: int, raw: bytes, size: int) -> float:
    """Decode an SMC payload.  Mirrors iSMC ``smc/conv.go`` fixed-point + int types.

    Pure function — no ctypes I/O, no platform check.  ``raw`` is the
    leading ``size`` bytes from ``SMCKeyData_t.bytes``.
    """
    dt = _datatype_to_str(data_type).rstrip()
    body = bytes(raw[:size])
    if len(body) < 1:
        return 0.0
    if len(body) < 2 and dt not in ("ui8", "si8"):
        return float(body[0])

    match dt:
        case "sp78":
            return struct.unpack(">h", body[:2])[0] / 256.0
        case "fpe2":
            return struct.unpack(">H", body[:2])[0] / 4.0
        case "flt":
            return struct.unpack("<f", body[:4])[0] if len(body) >= 4 else 0.0
        case "ui8":
            return float(body[0])
        case "ui16":
            return float(struct.unpack(">H", body[:2])[0])
        case "ui32":
            return float(struct.unpack(">I", body[:4])[0]) if len(body) >= 4 else 0.0
        case "fp1f":
            return struct.unpack(">H", body[:2])[0] / 32768.0
        case _:
            # Best-effort: treat as sp78 (most common temperature shape).
            if len(body) >= 2:
                return float(struct.unpack(">H", body[:2])[0]) / 256.0
            return float(body[0])


# =========================================================================
# IOKit binding — import-safe everywhere
# =========================================================================


def _load_iokit() -> ctypes.CDLL | None:
    """Return a bound IOKit handle, or None on non-macOS / load failure."""
    path = ctypes.util.find_library("IOKit")
    if not path:
        return None
    try:
        return ctypes.CDLL(path)
    except OSError:
        return None


def _bind_iokit(iokit: ctypes.CDLL) -> bool:
    """Configure argument / return types on the IOKit symbols we need."""
    try:
        if hasattr(iokit, "IOMainPort"):
            iokit.IOMainPort.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            iokit.IOMainPort.restype = ctypes.c_int
        if hasattr(iokit, "IOMasterPort"):
            iokit.IOMasterPort.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            iokit.IOMasterPort.restype = ctypes.c_int

        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceMatching.restype = ctypes.c_void_p

        iokit.IOServiceGetMatchingServices.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iokit.IOServiceGetMatchingServices.restype = ctypes.c_int

        iokit.IOIteratorNext.argtypes = [ctypes.c_uint32]
        iokit.IOIteratorNext.restype = ctypes.c_uint32

        iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
        iokit.IOObjectRelease.restype = ctypes.c_int

        iokit.IOServiceOpen.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iokit.IOServiceOpen.restype = ctypes.c_int

        iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
        iokit.IOServiceClose.restype = ctypes.c_int

        iokit.IOConnectCallStructMethod.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
        ]
        iokit.IOConnectCallStructMethod.restype = ctypes.c_int
        return True
    except Exception:
        log.debug("IOKit symbol binding failed", exc_info=True)
        return False


def _mach_task_self() -> int:
    """Return the current process's mach task port (for IOServiceOpen)."""
    libc_path = ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib"
    libc = ctypes.CDLL(libc_path)
    libc.mach_task_self.restype = ctypes.c_uint32
    libc.mach_task_self.argtypes = []
    return int(libc.mach_task_self())


# =========================================================================
# Protocol — what SmcCpu / SmcGpu need from the client
# =========================================================================


class SmcClientPort(Protocol):
    """Subset of SMCClient that the sensor wrappers consume.

    Tests substitute an in-memory dict-backed fake so the SmcCpu /
    SmcGpu logic is Linux-testable.
    """

    @property
    def connected(self) -> bool: ...

    def open(self) -> bool: ...

    def close(self) -> None: ...

    def read_key_float(self, key: str) -> float | None: ...

    def read_key_uint32(self, key: str) -> int | None: ...

    def read_fan_rpm(self, key: str) -> float | None: ...


# =========================================================================
# SMCClient — real IOKit-backed implementation
# =========================================================================


_KEY_INFO_CACHE_SIZE = 100


class SMCClient:
    """User-space AppleSMC connection.

    Open lazily — construction is cheap and import-safe on non-macOS.
    ``open()`` returns False when IOKit isn't available or the SMC
    service can't be matched; subsequent ``read_key_float`` calls
    return None until ``open()`` succeeds.
    """

    def __init__(self) -> None:
        self._iokit: ctypes.CDLL | None = None
        self._conn: int = 0
        self._key_cache: dict[int, _SMCKeyData_keyInfo_t] = {}
        self._cache_order: list[int] = []

    @property
    def connected(self) -> bool:
        return self._conn != 0

    def open(self) -> bool:
        if self._conn:
            return True
        iokit = _load_iokit()
        if iokit is None or not _bind_iokit(iokit):
            return False

        master = ctypes.c_uint32(0)
        if hasattr(iokit, "IOMainPort"):
            ret = int(iokit.IOMainPort(0, ctypes.byref(master)))
        elif hasattr(iokit, "IOMasterPort"):
            ret = int(iokit.IOMasterPort(0, ctypes.byref(master)))
        else:
            log.debug("Neither IOMainPort nor IOMasterPort exists in this IOKit")
            return False
        if ret != _kIOReturnSuccess:
            log.debug("IOMainPort/IOMasterPort failed: %d", ret)
            return False

        matching = iokit.IOServiceMatching(b"AppleSMC")
        if not matching:
            log.debug("IOServiceMatching(AppleSMC) returned NULL")
            return False

        iterator = ctypes.c_uint32(0)
        ret = int(iokit.IOServiceGetMatchingServices(
            master.value, matching, ctypes.byref(iterator),
        ))
        if ret != _kIOReturnSuccess:
            log.debug("IOServiceGetMatchingServices failed: %d", ret)
            if iterator.value:
                iokit.IOObjectRelease(iterator.value)
            return False

        device = int(iokit.IOIteratorNext(iterator.value))
        iokit.IOObjectRelease(iterator.value)
        if device == 0:
            log.debug("No AppleSMC device in iterator")
            return False

        conn = ctypes.c_uint32(0)
        ret = int(iokit.IOServiceOpen(
            device, _mach_task_self(), 0, ctypes.byref(conn),
        ))
        iokit.IOObjectRelease(device)
        if ret != _kIOReturnSuccess:
            log.warning("IOServiceOpen(AppleSMC) failed: %d", ret)
            return False

        self._iokit = iokit
        self._conn = int(conn.value)
        log.info("SMC connection opened")
        return True

    def close(self) -> None:
        if self._conn and self._iokit is not None:
            self._iokit.IOServiceClose(self._conn)
        self._conn = 0
        self._iokit = None
        self._key_cache.clear()
        self._cache_order.clear()

    # ── Internal: one structmethod round-trip ──────────────────────

    def _smc_call(
        self, inp: _SMCKeyData_t, out: _SMCKeyData_t,
    ) -> int:
        assert self._iokit is not None
        osize = ctypes.c_size_t(ctypes.sizeof(_SMCKeyData_t))
        return int(self._iokit.IOConnectCallStructMethod(
            self._conn, _KERNEL_INDEX_SMC,
            ctypes.byref(inp), ctypes.sizeof(_SMCKeyData_t),
            ctypes.byref(out), ctypes.byref(osize),
        ))

    def _get_key_info(
        self, key_uint: int, out_info: _SMCKeyData_keyInfo_t,
    ) -> int:
        """Populate *out_info* with dataType / dataSize for *key_uint*."""
        if key_uint in self._key_cache:
            cached = self._key_cache[key_uint]
            out_info.dataSize = cached.dataSize
            out_info.dataType = cached.dataType
            out_info.dataAttributes = cached.dataAttributes
            return _kIOReturnSuccess

        inp = _SMCKeyData_t()
        out = _SMCKeyData_t()
        inp.key = key_uint
        inp.data8 = _SMC_CMD_READ_KEYINFO
        ret = self._smc_call(inp, out)
        if ret != _kIOReturnSuccess:
            return ret

        out_info.dataSize = out.keyInfo.dataSize
        out_info.dataType = out.keyInfo.dataType
        out_info.dataAttributes = out.keyInfo.dataAttributes

        if len(self._cache_order) >= _KEY_INFO_CACHE_SIZE:
            old = self._cache_order.pop(0)
            self._key_cache.pop(old, None)
        self._key_cache[key_uint] = _SMCKeyData_keyInfo_t(
            out_info.dataSize, out_info.dataType, out_info.dataAttributes,
        )
        self._cache_order.append(key_uint)
        return _kIOReturnSuccess

    def read_key_float(self, key: str) -> float | None:
        """Read a 4-char SMC key and return its decoded float value."""
        info, raw, size = self._read_key_raw(key)
        if info is None or raw is None:
            return None
        return _parse_smc_bytes(int(info.dataType), raw, size)

    def read_key_uint32(self, key: str) -> int | None:
        """Read a uint8/16/32 SMC key (e.g. ``FNum`` fan count)."""
        v = self.read_key_float(key)
        if v is None:
            return None
        return int(v)

    def read_fan_rpm(self, key: str) -> float | None:
        """Read an SMC fan key (e.g. ``F0Ac``) with Apple Silicon's
        ``flt``/``fpe2`` type quirks handled.

        Apple Silicon often exposes ``F{i}Ac`` as a 4-byte ``flt``
        with a sane RPM value; Intel Macs use ``fpe2`` packed as a
        big-endian uint16/4.  Try the parsed float first; fall back
        to fpe2 decoding when the float is out of the plausible
        0-20000 RPM range.
        """
        info, raw, _size = self._read_key_raw(key)
        if info is None or raw is None:
            return None
        return _decode_fan_rpm_raw(int(info.dataType), int(info.dataSize), raw)

    def _read_key_raw(
        self, key: str,
    ) -> tuple[_SMCKeyData_keyInfo_t | None, bytes | None, int]:
        """Common path: key info + READ_BYTES round-trip.  Returns
        ``(info, raw_bytes, size)`` so callers can decode according
        to the actual SMC ``dataType``.
        """
        if not self._conn or self._iokit is None or len(key) < 4:
            return None, None, 0
        key_uint = _smc_key_to_uint32(key[:4])

        info = _SMCKeyData_keyInfo_t()
        if self._get_key_info(key_uint, info) != _kIOReturnSuccess:
            return None, None, 0
        if info.dataSize == 0:
            return None, None, 0

        # READ_BYTES input only carries key + cmd + keyInfo.dataSize.
        # Echoing dataType back upsets some SMC stacks.
        inp = _SMCKeyData_t()
        out = _SMCKeyData_t()
        inp.key = key_uint
        inp.data8 = _SMC_CMD_READ_BYTES
        inp.keyInfo.dataSize = info.dataSize
        if self._smc_call(inp, out) != _kIOReturnSuccess:
            return None, None, 0

        return info, bytes(out.bytes), int(info.dataSize)


# =========================================================================
# Key tables — Intel default; Apple Silicon behind env flag
# =========================================================================


# Intel Mac CPU temperature keys.  Always enabled.
INTEL_CPU_TEMP_KEYS: tuple[str, ...] = (
    "TC0P", "TC0D", "TC0E", "TC1C", "TC2C", "TC3C",
)

# Intel Mac GPU temperature keys.
INTEL_GPU_TEMP_KEYS: tuple[str, ...] = (
    "TG0P", "TG0D",
)

# Apple Silicon CPU temperature keys — derived from iSMC gosmc/sensors.go.
# Behind ``TRCC_NEXT_APPLE_SILICON_SMC=1`` until reporter-confirmed.
APPLE_SILICON_CPU_TEMP_KEYS: tuple[str, ...] = (
    # M1-M5 P-core die / cluster sensors
    "Tp00", "Tp01", "Tp02", "Tp04", "Tp05", "Tp06", "Tp08", "Tp09",
    "Tp0C", "Tp0D", "Tp0E", "Tp0G", "Tp0K", "Tp0L", "Tp0M", "Tp0O",
    "Tp0R", "Tp0T", "Tp0U", "Tp0W", "Tp0X",
    "Tp0a", "Tp0b", "Tp0c", "Tp0d", "Tp0g", "Tp0h", "Tp0i", "Tp0j",
    "Tp0m", "Tp0n", "Tp0o", "Tp0p", "Tp0u", "Tp0y",
    "Tp12", "Tp16", "Tp1E", "Tp1F", "Tp1G", "Tp1K", "Tp1Q", "Tp1R",
    "Tp1S", "Tp1j", "Tp1n", "Tp1t", "Tp1w", "Tp1z",
    "Tp22", "Tp25", "Tp28", "Tp2B", "Tp2E", "Tp2J", "Tp2M", "Tp2Q",
    "Tp2T", "Tp2W", "Tp3P", "Tp3X",
    # CPU package sensors
    "Tpx8", "Tpx9", "TpxA", "TpxB", "TpxC", "TpxD",
    # E-core cluster
    "Te04", "Te05", "Te06", "Te09", "Te0G", "Te0H", "Te0I", "Te0L",
    "Te0P", "Te0Q", "Te0R", "Te0S", "Te0T", "Te0U", "Te0V",
)

# Apple Silicon GPU temperature keys.
APPLE_SILICON_GPU_TEMP_KEYS: tuple[str, ...] = (
    "Tg04", "Tg05", "Tg0f", "Tg0j",
    "Tg0G", "Tg0H", "Tg0K", "Tg0L", "Tg0U", "Tg0X", "Tg0d", "Tg0e",
    "Tg0g", "Tg0k", "Tg1U", "Tg1Y", "Tg1c", "Tg1g", "Tg1k",
)
