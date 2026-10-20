"""PawnIO, the driver Windows CPU readings go through -- tested on Linux by
scripting its three Win32 calls.  The wire constants are the driver's own
(namazso/PawnIO 2.2.0 include/pawnio_um.h); the failure tests exist because
LibreHardwareMonitor's client reads a failed call as zeros."""
from __future__ import annotations

import struct
from types import SimpleNamespace
from typing import Any

import pytest

from trcc.adapters.sensors._pawnio import (
    DEVICE_PATH,
    IOCTL_EXECUTE,
    IOCTL_LOAD,
    PawnIoError,
    PawnIoModule,
    _DeviceIoPort,
    execute_payload,
)

BLOB = b"\x00\x02\x00\x00" + b"S" * 512 + b"\xe1\xf1amx"     # shape only


class _FakeDevice(_DeviceIoPort):
    """Records every call; answers EXECUTE from *replies*, fails on *fail*."""

    def __init__(self, *, open_error: int = 0, load_error: int = 0,
                 replies: list[bytes] | None = None,
                 execute_error: int = 0) -> None:
        self.calls: list[tuple] = []
        self.open_error, self.load_error = open_error, load_error
        self.execute_error = execute_error
        self.replies = list(replies or [])

    def open(self, path: str) -> int:
        self.calls.append(("open", path))
        if self.open_error:
            raise PawnIoError(self.open_error, "refused")
        return 0x1F4

    def ioctl(self, handle: int, code: int, payload: bytes,
              out_size: int) -> bytes:
        self.calls.append(("ioctl", handle, code, payload, out_size))
        if code == IOCTL_LOAD and self.load_error:
            raise PawnIoError(self.load_error, "not for this CPU")
        if code == IOCTL_EXECUTE and self.execute_error:
            raise PawnIoError(self.execute_error, "refused that register")
        return self.replies.pop(0) if code == IOCTL_EXECUTE else b""

    def close(self, handle: int) -> None:
        self.calls.append(("close", handle))


def test_the_ioctl_codes_are_the_drivers() -> None:
    """CTL_CODE(0xA1B2, 0x821/0x841, METHOD_BUFFERED, FILE_ANY_ACCESS)."""
    assert (IOCTL_LOAD, IOCTL_EXECUTE) == (0xA1B22084, 0xA1B22104)
    assert DEVICE_PATH == "\\\\?\\GLOBALROOT\\Device\\PawnIO"


def test_an_execute_request_is_the_name_then_little_endian_cells() -> None:
    payload = execute_payload("ioctl_read_msr", [0x1B1, -1])
    assert payload == (b"ioctl_read_msr".ljust(32, b"\0")
                       + struct.pack("<QQ", 0x1B1, (1 << 64) - 1))


@pytest.mark.parametrize("name", ["read_msr", "ioctl_" + "x" * 26])
def test_a_name_the_driver_would_refuse_is_refused_here(name: str) -> None:
    """It must start ``ioctl_`` and fit in 32 bytes with its NUL."""
    with pytest.raises(ValueError, match="not a PawnIO function name"):
        execute_payload(name, [])


def test_a_module_loads_the_whole_file_on_its_own_handle() -> None:
    device = _FakeDevice(replies=[struct.pack("<Q", 0x8800_0000)])
    with PawnIoModule(BLOB, name="IntelMSR", device=device) as module:
        assert module.execute("ioctl_read_msr", [0x1B1], 1) == (0x8800_0000,)
    assert device.calls == [
        ("open", DEVICE_PATH),
        ("ioctl", 0x1F4, IOCTL_LOAD, BLOB, 0),
        ("ioctl", 0x1F4, IOCTL_EXECUTE, execute_payload("ioctl_read_msr",
                                                        [0x1B1]), 8),
        ("close", 0x1F4)]


def test_a_module_for_another_cpu_is_refused_and_its_handle_closed() -> None:
    """MUTATION CHECK: drop the close() in PawnIoModule.__init__."""
    device = _FakeDevice(load_error=50)
    with pytest.raises(PawnIoError) as refused:
        PawnIoModule(BLOB, name="AMDFamily17", device=device)
    assert refused.value.code == 50
    assert device.calls[-1] == ("close", 0x1F4)


def test_a_refused_read_raises_and_never_reads_as_zero() -> None:
    """LibreHardwareMonitor's client returns zeros here -- 0 °C on a panel.

    MUTATION CHECK: return (0,) * outputs when the ioctl raises."""
    module = PawnIoModule(BLOB, device=_FakeDevice(execute_error=5))
    with pytest.raises(PawnIoError):
        module.execute("ioctl_read_msr", [0x1A2], 1)


def test_a_short_reply_raises() -> None:
    module = PawnIoModule(BLOB, device=_FakeDevice(replies=[b"\x01\x02"]))
    with pytest.raises(PawnIoError, match="returned 2 bytes, not 1 value"):
        module.execute("ioctl_read_msr", [0x1A2], 1)


def test_a_closed_module_says_so_and_closes_once() -> None:
    device = _FakeDevice()
    module = PawnIoModule(BLOB, device=device)
    module.close()
    module.close()
    assert [c for c in device.calls if c[0] == "close"] == [("close", 0x1F4)]
    with pytest.raises(PawnIoError, match="closed"):
        module.execute("ioctl_read_msr", [0x1A2], 1)


@pytest.mark.parametrize(("stage", "code", "reason"), [
    ("open", 2, "PawnIO is not installed"),
    ("open", 3, "PawnIO is not installed"),
    ("open", 5, "PawnIO admits only administrators -- TRCC is not elevated"),
    ("load", 50, "this PawnIO module is not for this CPU"),
    ("execute", 5, "the PawnIO module refused that register"),
    ("execute", 31, "PawnIO execute failed: WinError 31"),
])
def test_each_failure_says_why_in_words(stage: str, code: int,
                                        reason: str) -> None:
    from trcc.adapters.sensors._pawnio import explain
    assert explain(stage, code) == reason


# ── The real Win32 layer, scripted on Linux ──────────────────────────

class _FakeFn:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.restype: Any = None
        self.argtypes: Any = None
        self.calls: list[tuple] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.result(*args) if callable(self.result) else self.result


def _kernel32_device(monkeypatch: pytest.MonkeyPatch, *, handle: int,
                     ioctl: Any, last_error: int = 0) -> tuple[Any, Any]:
    import ctypes
    import sys

    from trcc.adapters.sensors._pawnio import _Kernel32DeviceIo

    k32 = SimpleNamespace(CreateFileW=_FakeFn(handle),
                          DeviceIoControl=_FakeFn(ioctl),
                          CloseHandle=_FakeFn(1))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: k32,
                        raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last_error,
                        raising=False)
    return _Kernel32DeviceIo(), k32


def test_the_win32_calls_carry_their_c_prototypes(monkeypatch) -> None:
    """A 64-bit handle through a C-int restype is truncated -- the HWiNFO
    view address taught that (_hwinfo._kernel32_prototyped)."""
    import ctypes

    device, k32 = _kernel32_device(monkeypatch, handle=0x7FF6_0000_1000,
                                   ioctl=1)
    assert k32.CreateFileW.restype is ctypes.c_void_p
    assert k32.CloseHandle.argtypes == [ctypes.c_void_p]
    assert device.open(DEVICE_PATH) == 0x7FF6_0000_1000
    path, access, share, _sec, disposition, _flags, _tmpl = \
        k32.CreateFileW.calls[0]
    assert (path, access, share, disposition) == (
        DEVICE_PATH, 0xC0000000, 0x3, 3)


def test_an_invalid_handle_raises_with_the_reason(monkeypatch) -> None:
    import ctypes

    invalid = ctypes.c_void_p(-1).value         # INVALID_HANDLE_VALUE
    assert invalid is not None
    device, _ = _kernel32_device(monkeypatch, handle=invalid,
                                 ioctl=1, last_error=2)
    with pytest.raises(PawnIoError, match="PawnIO is not installed"):
        device.open(DEVICE_PATH)


def test_a_device_io_control_returns_what_the_driver_wrote(monkeypatch) -> None:
    import ctypes

    def answer(_h, _code, _inbuf, _insize, outbuf, _outsize, returned,
               _ov) -> int:
        ctypes.memmove(outbuf, struct.pack("<Q", 42), 8)
        returned._obj.value = 8
        return 1

    device, k32 = _kernel32_device(monkeypatch, handle=0x10, ioctl=answer)
    assert device.ioctl(0x10, IOCTL_EXECUTE, b"x" * 40, 8) == \
        struct.pack("<Q", 42)
    assert k32.DeviceIoControl.calls[0][1] == IOCTL_EXECUTE


@pytest.mark.parametrize(("code", "reason"), [
    (IOCTL_LOAD, "this PawnIO module is not for this CPU"),
    (IOCTL_EXECUTE, "PawnIO execute failed: WinError 50"),
])
def test_a_failed_device_io_control_names_its_stage(monkeypatch, code: int,
                                                    reason: str) -> None:
    device, _ = _kernel32_device(monkeypatch, handle=0x10, ioctl=0,
                                 last_error=50)
    with pytest.raises(PawnIoError, match=reason):
        device.ioctl(0x10, code, b"", 0)


def test_off_windows_there_is_no_pawnio() -> None:
    from trcc.adapters.sensors._pawnio import _Kernel32DeviceIo
    with pytest.raises(OSError, match="PawnIO is a Windows driver"):
        _Kernel32DeviceIo()
