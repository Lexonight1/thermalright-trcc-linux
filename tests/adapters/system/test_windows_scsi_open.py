"""``WindowsScsiTransport`` -- which open failures are the user's setup, and the
pass-through it makes on ``BaseScsiTransport``.

Driven on any OS: ``CreateFileW`` and ``GetLastError`` are scripted, the
decision under test is ours.
"""
from __future__ import annotations

import ctypes
from typing import Any

import pytest

import trcc.adapters.system.windows as windows
from trcc.core.errors import PermissionError_


class _Kernel32:
    """``CreateFileW`` fails; ``GetLastError`` says why."""

    def CreateFileW(self, *_args: Any) -> int:
        return -1


def _failing_with(monkeypatch: pytest.MonkeyPatch, err: int) -> None:
    monkeypatch.setattr(windows, "_kernel32", lambda: _Kernel32())
    monkeypatch.setattr(ctypes, "GetLastError", lambda: err, raising=False)


def test_access_denied_is_a_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only elevation opens it; retrying every few minutes would not.

    MUTATION CHECK: return False on ERROR_ACCESS_DENIED again and this fails.
    """
    _failing_with(monkeypatch, 5)

    with pytest.raises(PermissionError_, match="Run as administrator"):
        windows.WindowsScsiTransport(r"\\.\PhysicalDrive3").open()


@pytest.mark.parametrize("err", [2, 32])
def test_a_drive_that_is_gone_or_busy_is_worth_retrying(
    monkeypatch: pytest.MonkeyPatch, err: int,
) -> None:
    """Not found (replug) and sharing violation (another process) can pass."""
    _failing_with(monkeypatch, err)

    assert windows.WindowsScsiTransport(r"\\.\PhysicalDrive3").open() is False


# ── The pass-through itself, on BaseScsiTransport ────────────────────────


class _PassThrough:
    """A drive behind DeviceIoControl: records each call, answers as told."""

    def __init__(self, err: int = 0, status: int = 0,
                 reply: bytes = b"") -> None:
        self.err, self.status, self.reply = err, status, reply
        self.calls: list[tuple[int, bytes, int]] = []   # (ioctl, data-out, address)

    def CreateFileW(self, *_args: Any) -> int:
        return 7

    def CloseHandle(self, _handle: int) -> int:
        return 1

    def DeviceIoControl(self, _handle: int, code: int, inbuf: Any, *_rest: Any) -> int:
        sptd = inbuf._obj.sptd
        length = sptd.DataTransferLength
        if sptd.DataIn == windows._SCSI_IOCTL_DATA_IN:
            ctypes.memmove(sptd.DataBuffer, self.reply, len(self.reply))
            sptd.DataTransferLength = len(self.reply)
            sent = b""
        else:
            sent = ctypes.string_at(sptd.DataBuffer, length)
        self.calls.append((code, sent, sptd.DataBuffer))
        if self.err:
            return 0
        sptd.ScsiStatus = self.status
        return 1


def _open(monkeypatch: pytest.MonkeyPatch, drive: _PassThrough) -> Any:
    monkeypatch.setattr(windows, "_kernel32", lambda: drive)
    monkeypatch.setattr(ctypes, "GetLastError", lambda: drive.err, raising=False)
    transport = windows.WindowsScsiTransport(r"\\.\PhysicalDrive3")
    assert transport.open()
    return transport


def test_a_frame_goes_direct_byte_for_byte_into_a_reused_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION CHECK: rebuild the buffer per write -- the address changes."""
    drive = _PassThrough()
    transport = _open(monkeypatch, drive)
    chunk = bytes(range(256)) * 256

    assert transport.send_cdb(b"\x01" * 16, chunk) is True
    assert transport.send_cdb(b"\x01" * 16, chunk[::-1]) is True

    (code1, sent1, addr1), (code2, sent2, addr2) = drive.calls
    assert (code1, code2) == (0x4D014, 0x4D014)
    assert (sent1, sent2) == (chunk, chunk[::-1])
    assert addr1 == addr2


@pytest.mark.parametrize("err", [433, 1167, 1617])
def test_a_drive_that_is_gone_raises_disconnect_class(
    monkeypatch: pytest.MonkeyPatch, err: int,
) -> None:
    from trcc.core.device_recovery import is_disconnect_error
    from trcc.core.errors import TransportError

    transport = _open(monkeypatch, _PassThrough(err=err))
    with pytest.raises(TransportError) as raised:
        transport.send_cdb(b"\x00" * 16, b"frame")
    assert is_disconnect_error(raised.value)


def test_a_general_failure_is_soft_as_usblcd_retries_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ERROR_GEN_FAILURE (31): USBLCD.exe retries it, so the next frame does."""
    transport = _open(monkeypatch, _PassThrough(err=31))
    assert transport.send_cdb(b"\x00" * 16, b"frame") is False


def test_the_poll_reads_direct_and_returns_what_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive = _PassThrough(reply=b"\x64" + b"\x00" * 7)
    transport = _open(monkeypatch, drive)

    assert transport.read_cdb(b"\xf5" + b"\x00" * 15, 0xE100) == drive.reply
    assert drive.calls[0][0] == 0x4D014


def test_the_polls_timeout_keeps_the_reply_it_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured on 0402:3922: ERROR_SEM_TIMEOUT, status GOOD, reply in the
    buffer (byte 0 = 0x64, its FBL).  Dropping it read every Windows panel
    as FBL 100.  MUTATION CHECK: keep nothing on 121 -- this fails."""
    transport = _open(monkeypatch, _PassThrough(err=121, reply=b"\x64" + b"\x00" * 7))
    reply = transport.read_cdb(b"\xf5" + b"\x00" * 15, 0xE100)
    assert (len(reply), reply[0]) == (0xE100, 0x64)


def test_a_timeout_that_delivered_nothing_keeps_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The buffer is zeroed first, so an old reply cannot pass for a new one."""
    drive = _PassThrough(reply=b"\x64" + b"\x00" * 7)
    transport = _open(monkeypatch, drive)
    assert transport.read_cdb(b"\xf5" + b"\x00" * 15, 0xE100)[0] == 0x64
    drive.err, drive.reply = 121, b""
    assert transport.read_cdb(b"\xf5" + b"\x00" * 15, 0xE100) == b""


def test_any_other_failed_read_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _open(monkeypatch, _PassThrough(err=31, reply=b"\x64" * 8))
    assert transport.read_cdb(b"\xf5" + b"\x00" * 15, 0xE100) == b""
