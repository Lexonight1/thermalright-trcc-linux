"""PawnIO: the signed driver every Windows reading below the OS goes through.

CPU temperature and power live in the CPU's own registers, which Windows lets
only kernel code read.  PawnIO (namazso, https://pawnio.eu) is the signed,
open driver LibreHardwareMonitor and OpenRGB moved to when Defender began
quarantining WinRing0.  It runs small signed *modules* -- ``IntelMSR``,
``AMDFamily17``, ... from PawnIO.Modules (LGPL-2.1) -- and exposes their
``ioctl_*`` functions, so a client never gets raw access to the hardware.

TRCC talks to it ONLY through ``DeviceIoControl``.  PawnIO's licence (GPL-2.0)
makes an exception for exactly that -- programs that communicate with it
"solely through the device IO control interface" -- so TRCC never loads
``PawnIOLib.dll``, which would make it a combined work.

The wire, from the driver source (namazso/PawnIO 2.2.0, include/pawnio_um.h):

    device   \\\\?\\GLOBALROOT\\Device\\PawnIO   (\\\\.\\PawnIO is deprecated)
    LOAD     CTL_CODE(0xA1B2, 0x821, METHOD_BUFFERED, FILE_ANY_ACCESS)
             input: the whole signed module file, unchanged
    EXECUTE  CTL_CODE(0xA1B2, 0x841, ...)
             input: 32-byte NUL-padded function name, then u64 cells
             output: u64 cells; a ``_SIZED`` function wants EXACT counts

One module per handle: a second LOAD on a handle is refused, and closing the
handle unloads it.  The device admits SYSTEM and Administrators only: an
elevated TRCC opens it directly, any other reaches it through TRCC's SYSTEM
service (``make_pawnio_cpu``).

Every call's result is checked.  LibreHardwareMonitor's client hands back an
array of zeros when ``DeviceIoControl`` fails, which reads as 0 °C; here a
failed call raises :class:`PawnIoError` with the reason in words.
"""
from __future__ import annotations

import logging
import struct
import sys
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from ...core.logs import per_frame

log = logging.getLogger(__name__)
#: Per-tick: every sensor read is one EXECUTE.
frame_log = per_frame(__name__)

DEVICE_PATH = "\\\\?\\GLOBALROOT\\Device\\PawnIO"
_DEVICE_TYPE = 0xA1B2                   # 41394, k_device_type
_FN_NAME_LEN = 32                       # FN_NAME_LENGTH, NUL included
_CELL = struct.Struct("<Q")
_U64 = (1 << 64) - 1

_GENERIC_READ_WRITE = 0x80000000 | 0x40000000
_FILE_SHARE_READ_WRITE = 0x1 | 0x2
_OPEN_EXISTING = 3


def ctl_code(function: int) -> int:
    """``CTL_CODE(0xA1B2, function, METHOD_BUFFERED, FILE_ANY_ACCESS)``."""
    log.debug("ctl_code: 0x%x", function)
    return (_DEVICE_TYPE << 16) | (function << 2)


IOCTL_LOAD = ctl_code(0x821)            # 0xA1B22084
IOCTL_EXECUTE = ctl_code(0x841)         # 0xA1B22104


class PawnIoError(OSError):
    """A PawnIO call failed; ``code`` is the Windows error, the message why."""

    def __init__(self, code: int, message: str) -> None:
        log.debug("PawnIoError: %d %s", code, message)
        super().__init__(message)
        self.code = code


def explain(stage: str, code: int) -> str:
    """Why a PawnIO call at *stage* (open / load / execute) failed, in words.

    *code* is the Windows error the driver's NTSTATUS arrives as:
    2/3 FILE/PATH_NOT_FOUND, 5 ACCESS_DENIED, 50 NOT_SUPPORTED."""
    log.debug("explain: %s %d", stage, code)
    match stage, code:
        case "open", (2 | 3):
            reason = "PawnIO is not installed"
        case "open", 5:
            reason = "PawnIO admits only administrators -- TRCC is not elevated"
        case "load", 50:
            reason = "this PawnIO module is not for this CPU"
        case "execute", 5:
            reason = "the PawnIO module refused that register"
        case _:
            reason = f"PawnIO {stage} failed: WinError {code}"
    return reason


def execute_payload(function: str, inputs: Sequence[int]) -> bytes:
    """The EXECUTE input: *function*'s name, NUL-padded to 32 bytes, then
    each input as a little-endian u64.  ``ValueError`` for a name the driver
    would refuse -- it must start ``ioctl_`` and fit with its NUL."""
    log.debug("execute_payload: %s %d input(s)", function, len(inputs))
    name = function.encode("ascii")
    if not function.startswith("ioctl_") or len(name) >= _FN_NAME_LEN:
        raise ValueError(f"not a PawnIO function name: {function!r}")
    return name.ljust(_FN_NAME_LEN, b"\0") + b"".join(
        _CELL.pack(value & _U64) for value in inputs)


class _DeviceIoPort(ABC):
    """The three Win32 calls PawnIO needs -- a seam, so a test scripts them."""

    @abstractmethod
    def open(self, path: str) -> int:
        """A handle to *path*; ``PawnIoError`` (stage open) when refused."""

    @abstractmethod
    def ioctl(self, handle: int, code: int, payload: bytes,
              out_size: int) -> bytes:
        """``DeviceIoControl``: the bytes the driver returned, or
        ``PawnIoError`` carrying the Windows error."""

    @abstractmethod
    def close(self, handle: int) -> None:
        """Release *handle* -- the driver unloads its module."""


def _kernel32_prototyped(ctypes: Any) -> Any:
    """A private kernel32 whose three calls carry their real C prototypes.

    Without ``restype`` a 64-bit handle is truncated to a C int, and without
    ``use_last_error`` ``get_last_error()`` reports someone else's error.
    The same reasons as ``_hwinfo._kernel32_prototyped``.
    """
    log.debug("_kernel32_prototyped: building a private kernel32")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                ctypes.c_uint32, ctypes.c_void_p,
                                ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.c_void_p]
    k32.DeviceIoControl.restype = ctypes.c_int
    k32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.POINTER(ctypes.c_uint32),
                                    ctypes.c_void_p]
    k32.CloseHandle.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


class _Kernel32DeviceIo(_DeviceIoPort):
    """The real calls.  ``OSError`` off Windows -- the caller has no PawnIO."""

    _ctypes: Any                    # set in __init__ behind a sys.platform gate
    _k32: Any
    _invalid: Any

    def __init__(self) -> None:
        log.debug("_Kernel32DeviceIo.__init__: platform=%s", sys.platform)
        if sys.platform != "win32":
            raise OSError("PawnIO is a Windows driver")
        import ctypes

        self._ctypes = ctypes
        self._k32 = _kernel32_prototyped(ctypes)
        #: CreateFileW's INVALID_HANDLE_VALUE as a c_void_p reads it back.
        self._invalid = ctypes.c_void_p(-1).value

    def _last_error(self) -> int:
        code = int(self._ctypes.get_last_error())    # type: ignore[attr-defined]
        log.debug("_Kernel32DeviceIo._last_error: %d", code)
        return code

    def open(self, path: str) -> int:
        log.info("_Kernel32DeviceIo.open: %s", path)
        handle = self._k32.CreateFileW(path, _GENERIC_READ_WRITE,
                                       _FILE_SHARE_READ_WRITE, None,
                                       _OPEN_EXISTING, 0, None)
        if handle in (None, self._invalid):
            code = self._last_error()
            raise PawnIoError(code, explain("open", code))
        return int(handle)

    def ioctl(self, handle: int, code: int, payload: bytes,
              out_size: int) -> bytes:
        frame_log.debug("_Kernel32DeviceIo.ioctl: 0x%x in=%d out=%d", code,
                        len(payload), out_size)
        ctypes = self._ctypes
        inbuf = ctypes.create_string_buffer(payload, len(payload))
        outbuf = ctypes.create_string_buffer(max(out_size, 1))
        returned = ctypes.c_uint32(0)
        if not self._k32.DeviceIoControl(handle, code, inbuf, len(payload),
                                         outbuf, out_size,
                                         ctypes.byref(returned), None):
            error = self._last_error()
            stage = "load" if code == IOCTL_LOAD else "execute"
            raise PawnIoError(error, explain(stage, error))
        return outbuf.raw[:returned.value]

    def close(self, handle: int) -> None:
        log.debug("_Kernel32DeviceIo.close: %s", handle)
        self._k32.CloseHandle(handle)


class PawnIoModule:
    """One signed PawnIO module, loaded on its own handle.

    *blob* is the module file exactly as PawnIO.Modules ships it -- the
    driver checks its embedded signature.  ``PawnIoError`` when PawnIO is
    missing, TRCC is not elevated, or the module is not for this CPU (each
    module's ``main()`` refuses another vendor at load, which is how the
    right one is found).  A context manager: leaving it unloads the module.
    """

    def __init__(self, blob: bytes, *, name: str = "module",
                 device: _DeviceIoPort | None = None) -> None:
        log.info("PawnIoModule: loading %s (%d bytes)", name, len(blob))
        self.name = name
        self._io = device or _Kernel32DeviceIo()
        self._handle: int | None = self._io.open(DEVICE_PATH)
        try:
            self._io.ioctl(self._handle, IOCTL_LOAD, blob, 0)
        except PawnIoError as e:
            log.info("PawnIoModule: %s not loaded -- %s", name, e)
            self.close()
            raise
        log.info("PawnIoModule: %s loaded", name)

    def execute(self, function: str, inputs: Sequence[int],
                outputs: int) -> tuple[int, ...]:
        """Run *function* with *inputs*; its *outputs* u64 results.

        ``PawnIoError`` when the driver refuses -- never a row of zeros.
        """
        frame_log.debug("PawnIoModule.execute: %s.%s %s -> %d", self.name,
                        function, [hex(i) for i in inputs], outputs)
        if self._handle is None:
            raise PawnIoError(0, f"PawnIO module {self.name} is closed")
        raw = self._io.ioctl(self._handle, IOCTL_EXECUTE,
                             execute_payload(function, inputs),
                             outputs * _CELL.size)
        if len(raw) != outputs * _CELL.size:
            raise PawnIoError(0, f"PawnIO {self.name}.{function} returned "
                                 f"{len(raw)} bytes, not {outputs} value(s)")
        return tuple(value for (value,) in _CELL.iter_unpack(raw))

    def close(self) -> None:
        log.debug("PawnIoModule.close: %s open=%s", self.name,
                  self._handle is not None)
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._io.close(handle)

    def __enter__(self) -> PawnIoModule:
        log.debug("PawnIoModule.__enter__: %s", self.name)
        return self

    def __exit__(self, *exc: object) -> None:
        log.debug("PawnIoModule.__exit__: %s", self.name)
        self.close()


class PciBusLock:
    """``Global\\Access_PCI``: the named mutex monitoring tools hold around
    PCI config reads, so two of them never interleave the SMN index/data
    pair AMD temperatures go through.  LibreHardwareMonitor and others take
    it; TRCC takes it too, waiting 10 ms as they do.  Windows only."""

    NAME = "Global\\Access_PCI"
    WAIT_MS = 10
    _WAIT_TIMEOUT = 0x102

    _k32: Any                       # set in __init__ behind a sys.platform gate
    _handle: Any

    def __init__(self) -> None:
        log.debug("PciBusLock.__init__: %s", self.NAME)
        if sys.platform != "win32":
            raise OSError("Access_PCI is a Windows mutex")
        import ctypes

        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateMutexW.restype = ctypes.c_void_p
        self._k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                           ctypes.c_wchar_p]
        self._k32.WaitForSingleObject.restype = ctypes.c_uint32
        self._k32.WaitForSingleObject.argtypes = [ctypes.c_void_p,
                                                  ctypes.c_uint32]
        self._k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        self._handle = self._k32.CreateMutexW(None, False, self.NAME)
        if not self._handle:
            code = int(ctypes.get_last_error())     # type: ignore[attr-defined]
            raise PawnIoError(code, f"cannot open {self.NAME}: WinError {code}")

    @contextmanager
    def held(self) -> Iterator[None]:
        """The bus, for one read -- ``PawnIoError`` when another tool holds
        it past the wait, so this tick skips rather than interleaves."""
        frame_log.debug("PciBusLock.held: waiting %d ms", self.WAIT_MS)
        if self._k32.WaitForSingleObject(self._handle,
                                         self.WAIT_MS) == self._WAIT_TIMEOUT:
            raise PawnIoError(self._WAIT_TIMEOUT,
                              "the PCI bus is busy -- another monitor holds "
                              "Access_PCI")
        try:
            yield
        finally:
            self._k32.ReleaseMutex(self._handle)
