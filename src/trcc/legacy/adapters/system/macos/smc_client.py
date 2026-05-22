"""Apple SMC via IOKit — ctypes bindings aligned with iSMC's gosmc (smc.c / smc.h).

Derived from the logic in https://github.com/dkorunic/iSMC (gosmc/smc.c, gosmc/smc.h),
GPL-3.0. Uses IOMainPort + IOServiceGetMatchingServices like gosmc, full
IOConnectCallStructMethod argtypes, and SMCReadKey-style sequencing (keyInfo.dataSize
before READ_BYTES). Key-info cache matches gosmc's energy-saving cache.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import struct

log = logging.getLogger(__name__)

KERNEL_INDEX_SMC = 2
SMC_CMD_READ_KEYINFO = 9
SMC_CMD_READ_BYTES = 5

kIOReturnSuccess = 0

# ── Structures (match gosmc/smc.h; sizeof(SMCKeyData_t) == 80 on Darwin) ─────


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


KEY_INFO_CACHE_SIZE = 100


def smc_key_to_uint32(key: str) -> int:
    """Four-byte SMC key as big-endian uint32 (same as gosmc _strtoul layout)."""
    return struct.unpack('>I', key.encode('latin-1', errors='replace')[:4].ljust(4, b' '))[0]


def datatype_to_str(dt: int) -> str:
    return struct.pack('>I', dt).decode('latin-1', errors='replace')


def parse_smc_bytes(data_type: int, raw: ctypes.Array, size: int) -> float:
    """Decode SMC payload; mirrors iSMC smc/conv.go fixed-point and integer types."""
    dt = datatype_to_str(data_type)
    b = bytes(raw[:size])
    if len(b) < 1:
        return 0.0
    if len(b) < 2 and dt.rstrip() not in ('ui8', 'si8'):
        return float(b[0]) if b else 0.0

    match dt.rstrip():
        case 'sp78':
            return struct.unpack('>h', b[:2])[0] / 256.0
        case 'fpe2':
            return struct.unpack('>H', b[:2])[0] / 4.0
        case 'flt':
            # iSMC smc/conv.go fltToFloat32: IEEE754 bits are little-endian on wire.
            return struct.unpack('<f', b[:4])[0] if len(b) >= 4 else 0.0
        case 'ui8':
            return float(b[0])
        case 'ui16':
            return float(struct.unpack('>H', b[:2])[0])
        case 'ui32':
            return float(struct.unpack('>I', b[:4])[0]) if len(b) >= 4 else 0.0
        case 'fp1f':
            return struct.unpack('>H', b[:2])[0] / 32768.0
        case _:
            return float(struct.unpack('>H', b[:2])[0]) / 256.0 if len(b) >= 2 else float(b[0])


def _load_iokit() -> ctypes.CDLL | None:
    path = ctypes.util.find_library('IOKit')
    if not path:
        return None
    try:
        return ctypes.CDLL(path)
    except OSError:
        return None


def _bind_iokit(iokit: ctypes.CDLL) -> bool:
    try:
        if hasattr(iokit, 'IOMainPort'):
            iokit.IOMainPort.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
            iokit.IOMainPort.restype = ctypes.c_int
        if hasattr(iokit, 'IOMasterPort'):
            iokit.IOMasterPort.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
            iokit.IOMasterPort.restype = ctypes.c_int

        iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        iokit.IOServiceMatching.restype = ctypes.c_void_p

        iokit.IOServiceGetMatchingServices.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iokit.IOServiceGetMatchingServices.restype = ctypes.c_int

        iokit.IOIteratorNext.argtypes = [ctypes.c_uint32]
        iokit.IOIteratorNext.restype = ctypes.c_uint32

        iokit.IOObjectRelease.argtypes = [ctypes.c_uint32]
        iokit.IOObjectRelease.restype = ctypes.c_int

        iokit.IOServiceOpen.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iokit.IOServiceOpen.restype = ctypes.c_int

        iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
        iokit.IOServiceClose.restype = ctypes.c_int

        iokit.IOConnectCallStructMethod.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        iokit.IOConnectCallStructMethod.restype = ctypes.c_int
        return True
    except Exception:
        log.debug('IOKit bind failed', exc_info=True)
        return False


def _mach_task_self() -> int:
    libc_path = ctypes.util.find_library('c') or '/usr/lib/libSystem.B.dylib'
    libc = ctypes.CDLL(libc_path)
    libc.mach_task_self.restype = ctypes.c_uint32
    libc.mach_task_self.argtypes = []
    return int(libc.mach_task_self())


class SMCClient:
    """User-space AppleSMC connection."""

    def __init__(self) -> None:
        self._iokit: ctypes.CDLL | None = None
        self._conn: int = 0
        self._key_cache: dict[int, SMCKeyData_keyInfo_t] = {}
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
        ret: int
        if hasattr(iokit, 'IOMainPort'):
            ret = int(iokit.IOMainPort(0, ctypes.byref(master)))
        elif hasattr(iokit, 'IOMasterPort'):
            ret = int(iokit.IOMasterPort(0, ctypes.byref(master)))
        else:
            log.debug('Neither IOMainPort nor IOMasterPort available')
            return False
        if ret != kIOReturnSuccess:
            log.debug('IOMainPort/IOMasterPort failed: %s', ret)
            return False

        matching = iokit.IOServiceMatching(b'AppleSMC')
        if not matching:
            log.debug('IOServiceMatching(AppleSMC) returned NULL')
            return False

        iterator = ctypes.c_uint32(0)
        ret = int(iokit.IOServiceGetMatchingServices(
            master.value, matching, ctypes.byref(iterator),
        ))
        if ret != kIOReturnSuccess:
            log.debug('IOServiceGetMatchingServices failed: %s', ret)
            if iterator.value:
                iokit.IOObjectRelease(iterator.value)
            return False

        device = int(iokit.IOIteratorNext(iterator.value))
        iokit.IOObjectRelease(iterator.value)
        if device == 0:
            log.debug('No AppleSMC device in iterator')
            return False

        conn = ctypes.c_uint32(0)
        task = _mach_task_self()
        ret = int(iokit.IOServiceOpen(device, task, 0, ctypes.byref(conn)))
        iokit.IOObjectRelease(device)
        if ret != kIOReturnSuccess:
            log.warning('IOServiceOpen(AppleSMC) failed: %s', ret)
            return False

        self._iokit = iokit
        self._conn = int(conn.value)
        log.info('SMC connection opened (gosmc-style IOMainPort path)')
        return True

    def close(self) -> None:
        if self._conn and self._iokit:
            self._iokit.IOServiceClose(self._conn)
        self._conn = 0
        self._iokit = None
        self._key_cache.clear()
        self._cache_order.clear()

    def _smc_call(self, inp: SMCKeyData_t, out: SMCKeyData_t) -> int:
        assert self._iokit is not None
        osize = ctypes.c_size_t(ctypes.sizeof(SMCKeyData_t))
        return int(self._iokit.IOConnectCallStructMethod(
            self._conn,
            KERNEL_INDEX_SMC,
            ctypes.byref(inp),
            ctypes.sizeof(SMCKeyData_t),
            ctypes.byref(out),
            ctypes.byref(osize),
        ))

    def _get_key_info(self, key_uint: int, out_info: SMCKeyData_keyInfo_t) -> int:
        if key_uint in self._key_cache:
            cached = self._key_cache[key_uint]
            out_info.dataSize = cached.dataSize
            out_info.dataType = cached.dataType
            out_info.dataAttributes = cached.dataAttributes
            return kIOReturnSuccess

        inp = SMCKeyData_t()
        out = SMCKeyData_t()
        inp.key = key_uint
        inp.data8 = SMC_CMD_READ_KEYINFO
        ret = self._smc_call(inp, out)
        if ret != kIOReturnSuccess:
            return ret
        out_info.dataSize = out.keyInfo.dataSize
        out_info.dataType = out.keyInfo.dataType
        out_info.dataAttributes = out.keyInfo.dataAttributes

        if len(self._cache_order) >= KEY_INFO_CACHE_SIZE:
            old = self._cache_order.pop(0)
            self._key_cache.pop(old, None)
        self._key_cache[key_uint] = SMCKeyData_keyInfo_t(
            out_info.dataSize, out_info.dataType, out_info.dataAttributes,
        )
        self._cache_order.append(key_uint)
        return kIOReturnSuccess

    def _read_key_raw(
        self, key: str,
    ) -> tuple[int | None, int | None, ctypes.Array | None]:
        """Return (data_type, data_size, bytes buffer) or (None, None, None)."""
        if not self._conn or self._iokit is None or len(key) < 4:
            return None, None, None
        key4 = key[:4]
        key_uint = smc_key_to_uint32(key4)

        info = SMCKeyData_keyInfo_t()
        if self._get_key_info(key_uint, info) != kIOReturnSuccess:
            return None, None, None
        if info.dataSize == 0:
            return None, None, None

        # READ_BYTES input must match gosmc SMCReadKey: only key, data8, and
        # keyInfo.dataSize — leave dataType/dataAttributes zero (not echoed from
        # KEYINFO). Some macOS SMC stacks reject non-zero type fields here.
        inp = SMCKeyData_t()
        out = SMCKeyData_t()
        inp.key = key_uint
        inp.data8 = SMC_CMD_READ_BYTES
        inp.keyInfo.dataSize = info.dataSize

        ret = self._smc_call(inp, out)
        if ret != kIOReturnSuccess:
            return None, None, None
        return int(info.dataType), int(info.dataSize), out.bytes

    def read_key_float(self, key: str) -> float | None:
        """Read four-char SMC key; returns None if missing or error."""
        data_type, data_size, buf = self._read_key_raw(key)
        if data_type is None or buf is None or data_size is None:
            return None
        return parse_smc_bytes(data_type, buf, data_size)

    def read_fan_rpm(self, key: str) -> float | None:
        data_type, data_size, buf = self._read_key_raw(key)
        return decode_fan_rpm_raw(data_type, data_size, buf)

    def read_key_uint32(self, key: str) -> int | None:
        """Read ui8/ui16/ui32 SMC keys (e.g. FNum fan count)."""
        v = self.read_key_float(key)
        if v is None:
            return None
        return int(v)


def decode_fan_rpm_raw(
    data_type: int | None,
    data_size: int | None,
    buf: ctypes.Array | None,
) -> float | None:
    """Decode fan RPM from SMC key payload (shared by :class:`SMCClient` and tests).

    Apple Silicon often exposes **F0Ac** / **F1Ac** as **flt** (~1.3k RPM). The
    first two bytes of that float are *not* fpe2-packed RPM; use the parsed float
    when it is in a sane RPM range. Otherwise fall back to fpe2 / ui16 heuristics
    (Intel and mis-typed keys).
    """
    if data_type is None or buf is None or data_size is None:
        return None
    if data_size < 2:
        return parse_smc_bytes(data_type, buf, data_size)

    dt = datatype_to_str(data_type).rstrip()
    parsed = parse_smc_bytes(data_type, buf, data_size)
    if dt == 'flt' and data_size >= 4:
        if parsed != parsed:  # NaN
            pass  # fall through — may be mislabeled fpe2 payload
        elif 0.0 <= parsed <= 20000.0:
            return float(parsed)
        # else: wrong float (e.g. old BE decode) or mis-tagged type — try fpe2/ui16

    raw16 = struct.unpack('>H', bytes(buf[i] for i in range(2)))[0]
    fpe2_rpm = raw16 / 4.0

    if raw16 == 0:
        return 0.0 if (parsed is None or parsed == 0) else float(parsed)

    if parsed != parsed:  # NaN
        parsed = None

    # Prefer fpe2 when parsed is clearly wrong vs packed nibbles.
    if 80.0 <= fpe2_rpm <= 15000.0:
        if (
            parsed is None
            or parsed > 8000.0
            or (
                parsed is not None
                and parsed > fpe2_rpm * 4.0
                and parsed > 3000.0
            )
        ):
            return fpe2_rpm

    # Literal uint16 RPM (decoded value tracks raw16, not raw16/4).
    if (
        parsed is not None
        and 80.0 <= parsed <= 15000.0
        and abs(parsed - float(raw16)) < 1.5
    ):
        return float(parsed)

    if 80.0 <= fpe2_rpm <= 15000.0:
        return fpe2_rpm
    if parsed is not None and 80.0 <= parsed <= 15000.0:
        return float(parsed)
    if 80.0 <= float(raw16) <= 15000.0:
        return float(raw16)
    if parsed is not None and parsed == 0:
        return 0.0
    return parsed
