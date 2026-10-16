"""The shared App's channel on Windows: a named pipe only this user may open.

CPython has no ``AF_UNIX`` on Windows at any version (gh-77589), so every UI
there used to build its own App.  This carries the same newline-delimited
JSON as the Unix socket (``ipc.py``), behind the same :class:`IpcChannel`
port, with streams shaped like sockets so ``ipc`` and ``proxy`` need no
Windows branch.

Security, measured on the win11 VM (2026-10-16) before it was written:

* **Name** ``\\\\.\\pipe\\trcc-<user SID>`` -- one App per user, as on Linux.
* **DACL** this user and SYSTEM only (``D:P``, protected).  The pipe's
  OWNER is set to this user's SID, and a client refuses a pipe owned by
  anyone else -- so another account that creates the name first (squatting)
  is never spoken to.  No account can make itself the owner of another's
  SID.
* **Label** Medium, no-write-up: an elevated App still admits this user's
  non-elevated UIs.  The default label of an object an elevated process
  makes is High, which refuses them.
* ``PIPE_REJECT_REMOTE_CLIENTS``: no connection from the network.
* ``FILE_FLAG_FIRST_PIPE_INSTANCE`` on the first instance: a second App
  cannot serve beside the first.
* Clients connect with ``SECURITY_IDENTIFICATION``: a server can identify
  them but never impersonate them.

Every read and write is overlapped, so a timeout and :meth:`_PipeStream.shutdown`
can interrupt it the way they interrupt a socket.  pywin32 is a declared
Windows dependency; without it ``ipc.transport`` falls back to in-process.
"""
from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path
from typing import Any, cast

try:
    import pywintypes  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32api  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32event  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32file  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32pipe  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32security  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    _PYWIN32: ImportError | None = None
except ImportError as _missing:
    # Off Windows, or without pywin32: the module still imports (every trcc
    # module must -- doc/REFERENCE_PORTS.md is generated from all of them),
    # and NamedPipeChannel() refuses with this.
    _PYWIN32 = _missing
    pywintypes = win32api = win32event = win32file = win32pipe = \
        win32security = cast(Any, None)

from ...core.logs import per_frame
from ...core.ports import IpcChannel

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_BUFFER = 65536
_FIRST_PIPE_INSTANCE = 0x00080000
_REJECT_REMOTE_CLIENTS = 0x00000008
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000
_ERROR_FILE_NOT_FOUND = 2
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_BUSY = 231
_ERROR_NO_DATA = 232
_ERROR_MORE_DATA = 234
_ERROR_PIPE_CONNECTED = 535
_ERROR_OPERATION_ABORTED = 995
_ERROR_IO_PENDING = 997


def _user_sid() -> Any:
    """This process's user SID (the same elevated or not)."""
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                           win32security.TOKEN_QUERY)
    sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    log.debug("_user_sid: %s", win32security.ConvertSidToStringSid(sid))
    return sid


def _event() -> Any:
    """A manual-reset event, unsignalled."""
    frame_log.debug("_event")
    return win32event.CreateEvent(None, True, False, None)


class _PipeStream:
    """One connected end of the pipe, shaped like a socket."""

    def __init__(self, handle: Any, name: str) -> None:
        self._handle = handle
        self._name = name
        self._timeout: float | None = None
        # Signalled by ``shutdown``/``close``: wakes a read or write blocked
        # on another thread, as shutting a socket does.
        self._woken = _event()
        self._closed = False
        self._lock = threading.Lock()
        frame_log.debug("_PipeStream: %s", name)

    def settimeout(self, timeout: float | None) -> None:
        frame_log.debug("_PipeStream.settimeout: %s", timeout)
        self._timeout = timeout

    def _complete(self, overlapped: Any) -> int | None:
        """Wait for *overlapped*: its byte count, or None when woken."""
        wait_ms = (win32event.INFINITE if self._timeout is None
                   else max(0, int(self._timeout * 1000)))
        got = win32event.WaitForMultipleObjects(
            [overlapped.hEvent, self._woken], False, wait_ms)
        if got == win32event.WAIT_OBJECT_0:
            return win32file.GetOverlappedResult(self._handle, overlapped, False)
        # Timed out or woken: the I/O was issued on this thread, so CancelIo
        # reaches it; then wait for the cancel so the buffer is free.
        win32file.CancelIo(self._handle)
        try:
            win32file.GetOverlappedResult(self._handle, overlapped, True)
        except pywintypes.error as e:
            if e.winerror != _ERROR_OPERATION_ABORTED:
                log.debug("_PipeStream: cancel finished with %s", e)
        if got == win32event.WAIT_TIMEOUT:
            raise TimeoutError("timed out")
        return None

    def recv(self, size: int) -> bytes:
        """Up to *size* bytes; ``b""`` at end of stream or after ``shutdown``."""
        frame_log.debug("_PipeStream.recv: %d", size)
        if self._closed:
            return b""
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = _event()
        buffer: Any = win32file.AllocateReadBuffer(size)
        try:
            win32file.ReadFile(self._handle, buffer, overlapped)
            count = self._complete(overlapped)
        except pywintypes.error as e:
            if e.winerror == _ERROR_BROKEN_PIPE:
                return b""
            if e.winerror != _ERROR_MORE_DATA:
                raise OSError(e.winerror, e.strerror) from e
            count = size
        finally:
            win32api.CloseHandle(overlapped.hEvent)
        return b"" if count is None else bytes(buffer[:count])

    def sendall(self, data: bytes) -> None:
        frame_log.debug("_PipeStream.sendall: %d bytes", len(data))
        view = memoryview(data)
        while view:
            if self._closed:
                raise BrokenPipeError("pipe closed")
            overlapped = pywintypes.OVERLAPPED()
            overlapped.hEvent = _event()
            try:
                win32file.WriteFile(self._handle, view.tobytes(), overlapped)
                count = self._complete(overlapped)
            except pywintypes.error as e:
                raise BrokenPipeError(e.winerror, e.strerror) from e
            finally:
                win32api.CloseHandle(overlapped.hEvent)
            if count is None:
                raise BrokenPipeError("pipe shut down")
            view = view[count:]

    def makefile(self, mode: str = "rb") -> io.BufferedReader:
        """A buffered reader over :meth:`recv` (the event stream reads lines)."""
        log.debug("_PipeStream.makefile: %s", mode)
        if mode != "rb":
            raise ValueError(f"only 'rb' is supported, not {mode!r}")
        return io.BufferedReader(_PipeReader(self))

    def shutdown(self, how: int = 2) -> None:
        """Wake any blocked read (it returns EOF) or write (it raises)."""
        log.debug("_PipeStream.shutdown: %s how=%s", self._name, how)
        win32event.SetEvent(self._woken)

    def close(self) -> None:
        """Shut and release the handle.  Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        log.debug("_PipeStream.close: %s", self._name)
        win32event.SetEvent(self._woken)
        try:
            win32api.CloseHandle(self._handle)
        except pywintypes.error as e:
            log.debug("_PipeStream.close: %s", e)
        win32api.CloseHandle(self._woken)

    def __enter__(self) -> _PipeStream:
        frame_log.debug("_PipeStream.__enter__: %s", self._name)
        return self

    def __exit__(self, *exc: object) -> None:
        frame_log.debug("_PipeStream.__exit__: %s", self._name)
        self.close()


class _PipeReader(io.RawIOBase):
    """``io.RawIOBase`` over a :class:`_PipeStream`, for ``makefile``."""

    def __init__(self, stream: _PipeStream) -> None:
        super().__init__()
        self._stream = stream
        log.debug("_PipeReader: made")

    def readable(self) -> bool:
        frame_log.debug("_PipeReader.readable")
        return True

    def readinto(self, buffer: Any) -> int:
        frame_log.debug("_PipeReader.readinto: %d", len(buffer))
        data = self._stream.recv(len(buffer))
        buffer[:len(data)] = data
        return len(data)


class _PipeListener:
    """Serves the pipe: one instance always waiting, so a client never finds
    the name missing between two connections (measured: it gets
    ``ERROR_FILE_NOT_FOUND`` in that gap)."""

    def __init__(self, name: str, security: Any) -> None:
        self._name = name
        self._security = security
        self._stop = _event()
        self._lock = threading.Lock()
        self._accepting = False
        self._pending: Any = self._instance(first=True)
        log.info("_PipeListener: serving %s", name)

    def _instance(self, *, first: bool = False) -> Any:
        """A new instance of the pipe, ready to connect."""
        log.debug("_PipeListener._instance: first=%s", first)
        mode = win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED
        if first:
            mode |= _FIRST_PIPE_INSTANCE
        try:
            return win32pipe.CreateNamedPipe(
                self._name, mode,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE
                | win32pipe.PIPE_WAIT | _REJECT_REMOTE_CLIENTS,
                win32pipe.PIPE_UNLIMITED_INSTANCES, _BUFFER, _BUFFER, 0,
                self._security)
        except pywintypes.error as e:
            # ACCESS_DENIED on the first instance: another App serves the name.
            raise OSError(e.winerror, f"cannot serve {self._name}: "
                                      f"{e.strerror}") from e

    def accept(self) -> tuple[_PipeStream, None]:
        """The next client; ``OSError`` once :meth:`wake` or :meth:`close`."""
        frame_log.debug("_PipeListener.accept")
        with self._lock:
            if win32event.WaitForSingleObject(self._stop, 0) == win32event.WAIT_OBJECT_0:
                raise OSError("listener closed")
            handle, self._accepting = self._pending, True
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = _event()
        try:
            connected = self._connect(handle, overlapped)
            if connected == _ERROR_IO_PENDING:
                got = win32event.WaitForMultipleObjects(
                    [overlapped.hEvent, self._stop], False, win32event.INFINITE)
                if got != win32event.WAIT_OBJECT_0:
                    # Stopped: close the waiting instance HERE, its owner, so
                    # the name goes at once -- left open it answered every
                    # client "busy" until shutdown, and ``trcc kill`` waited.
                    win32file.CancelIo(handle)
                    win32api.CloseHandle(handle)
                    self._pending = None
                    raise OSError("listener closed")
            # The next instance BEFORE handing this one over.
            self._pending = self._instance()
        finally:
            win32api.CloseHandle(overlapped.hEvent)
            with self._lock:
                self._accepting = False
        return _PipeStream(handle, self._name), None

    @staticmethod
    def _connect(handle: Any, overlapped: Any) -> int:
        """Start waiting for a client on *handle*: ``_ERROR_IO_PENDING``, or 0
        when one is already there.

        A client can reach a waiting instance before this is called.  If it
        is still there the call fails with ``ERROR_PIPE_CONNECTED`` -- fine.
        If it already LEFT (a "is the App up?" probe does exactly that) the
        call fails with ``ERROR_NO_DATA``: the instance is disconnected and
        waits again.  Raising there stopped the whole App (VM, 2026-10-16).
        """
        for _ in range(8):
            try:
                return win32pipe.ConnectNamedPipe(handle, overlapped)
            except pywintypes.error as e:
                if e.winerror == _ERROR_PIPE_CONNECTED:
                    return 0
                if e.winerror != _ERROR_NO_DATA:
                    raise OSError(e.winerror, e.strerror) from e
                frame_log.debug("_PipeListener._connect: a client came and "
                                "went -- waiting again")
                win32pipe.DisconnectNamedPipe(handle)
        raise OSError(_ERROR_NO_DATA, "clients keep leaving before they speak")

    def wake(self) -> None:
        """Wake a blocked :meth:`accept` (it raises ``OSError``)."""
        log.debug("_PipeListener.wake: %s", self._name)
        win32event.SetEvent(self._stop)

    def close(self) -> None:
        """Stop serving: no instance waits any more."""
        log.debug("_PipeListener.close: %s", self._name)
        self.wake()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with self._lock:
                if not self._accepting:
                    pending, self._pending = self._pending, None
                    break
            time.sleep(0.01)
        else:
            log.warning("_PipeListener.close: accept did not stop in 2 s")
            return
        if pending is not None:
            try:
                win32api.CloseHandle(pending)
            except pywintypes.error as e:
                log.debug("_PipeListener.close: %s", e)

    def remove(self) -> None:
        """Nothing to delete: a pipe's name goes with its last handle."""
        log.debug("_PipeListener.remove: %s -- nothing on disk", self._name)


class NamedPipeChannel(IpcChannel):
    """``\\\\.\\pipe\\trcc-<user SID>``: this user's App, on Windows."""

    def __init__(self, path: Path | None = None, name: str | None = None) -> None:
        """*path* is the Unix socket's and unused here: a pipe is named for the
        user.  *name* only for tests, which must not take a running App's pipe.

        ``ImportError`` without pywin32 -- ``ipc.transport`` then says so and
        the UI runs its own App."""
        if _PYWIN32 is not None:
            raise ImportError(f"pywin32 is not importable: {_PYWIN32}")
        self._sid = _user_sid()
        sid = win32security.ConvertSidToStringSid(self._sid)
        self.name = name or rf"\\.\pipe\trcc-{sid}"
        # Owner + DACL: this user and SYSTEM, nobody inherits in.  Label:
        # Medium, no-write-up, so an elevated App admits its user's
        # non-elevated UIs.
        self._sddl = f"O:{sid}D:P(A;;GA;;;{sid})(A;;GA;;;SY)S:(ML;;NW;;;ME)"
        log.debug("NamedPipeChannel: %s", self.name)

    @property
    def address(self) -> str:
        frame_log.debug("NamedPipeChannel.address")
        return self.name

    def connect(self, timeout: float | None) -> _PipeStream:
        frame_log.debug("NamedPipeChannel.connect: timeout=%s", timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                handle = win32file.CreateFile(
                    self.name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING,
                    win32file.FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT
                    | _SECURITY_IDENTIFICATION, None)
                break
            except pywintypes.error as e:
                if e.winerror == _ERROR_FILE_NOT_FOUND:
                    raise ConnectionRefusedError(
                        e.winerror, f"no App serves {self.name}") from e
                if e.winerror != _ERROR_PIPE_BUSY:
                    raise OSError(e.winerror, e.strerror) from e
                left = (None if deadline is None
                        else deadline - time.monotonic())
                if left is not None and left <= 0:
                    raise TimeoutError("every pipe instance is busy") from e
                try:
                    win32pipe.WaitNamedPipe(
                        self.name, win32pipe.NMPWAIT_WAIT_FOREVER
                        if left is None else max(1, int(left * 1000)))
                except pywintypes.error as waited:
                    # 121: no instance came free in time; 2: the App went.
                    if waited.winerror == _ERROR_FILE_NOT_FOUND:
                        raise ConnectionRefusedError(
                            waited.winerror, f"no App serves {self.name}",
                        ) from waited
                    raise TimeoutError(waited.winerror,
                                       "every pipe instance is busy") from waited
        self._check_owner(handle)
        stream = _PipeStream(handle, self.name)
        stream.settimeout(timeout)
        return stream

    def _check_owner(self, handle: Any) -> None:
        """Refuse a pipe another account made under our name."""
        owner = win32security.GetSecurityInfo(
            handle, win32security.SE_KERNEL_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION,
        ).GetSecurityDescriptorOwner()
        if owner != self._sid:
            win32api.CloseHandle(handle)
            log.warning("NamedPipeChannel: %s is owned by %s, not this "
                        "user -- refused", self.name,
                        win32security.ConvertSidToStringSid(owner))
            raise PermissionError(f"{self.name} belongs to another account")
        frame_log.debug("NamedPipeChannel._check_owner: ours")

    def listen(self) -> _PipeListener:
        log.info("NamedPipeChannel.listen: %s", self.name)
        security = win32security.SECURITY_ATTRIBUTES()
        security.SECURITY_DESCRIPTOR = (
            win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                self._sddl, win32security.SDDL_REVISION_1))
        return _PipeListener(self.name, security)
