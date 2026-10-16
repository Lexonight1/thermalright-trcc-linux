"""The shared App's channel on every OS with ``AF_UNIX``: a socket file.

Linux, macOS and the BSDs (``BaseOS.IPC_CHANNEL``).  The file is mode 0600
in a per-user directory (``ipc.runtime_dir``), so only this user reaches
the App.  Windows names its own channel, a named pipe (``named_pipe.py``).
"""
from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any

from ...core.logs import per_frame
from ...core.ports import IpcChannel

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class _UnixListener:
    """A bound Unix socket: ``accept`` until ``wake`` or ``close``; ``remove``
    deletes the socket file -- only the one THIS listener made."""

    def __init__(self, sock: socket.socket, path: Path) -> None:
        self._sock = sock
        # The path we actually BOUND, and its inode.  ``remove`` must unlink
        # this, not a freshly-computed ``socket_path()``: that reads
        # XDG_RUNTIME_DIR, and if the environment moved between start and
        # shutdown we would delete a path we never owned -- plausibly a live
        # daemon's socket.
        self.path = path
        self._ino = path.stat().st_ino
        log.debug("_UnixListener: %s ino=%d", path, self._ino)

    def accept(self) -> tuple[socket.socket, Any]:
        frame_log.debug("_UnixListener.accept")
        return self._sock.accept()

    def wake(self) -> None:
        """Wake a blocked ``accept`` (it raises ``OSError``)."""
        log.debug("_UnixListener.wake: %s", self.path)
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            log.debug("_UnixListener.wake: SHUT_RDWR failed", exc_info=True)

    def close(self) -> None:
        """Stop listening.  The file stays until :meth:`remove`."""
        log.debug("_UnixListener.close: %s", self.path)
        self.wake()
        self._sock.close()

    def remove(self) -> None:
        """Remove the socket file -- last, since ``trcc kill`` returns when it
        is gone -- and only if it is still the one this listener made."""
        log.debug("_UnixListener.remove: %s", self.path)
        try:
            ours = self.path.stat().st_ino == self._ino
        except OSError:
            ours = False
        if not ours:
            # An orphaned App removing the LIVE one's socket left no App to
            # find, and the next UI started a third (#314).
            log.info("_UnixListener.remove: %s is not the socket this server "
                     "made — left in place", self.path)
            return
        try:
            self.path.unlink()
        except OSError:
            log.debug("_UnixListener.remove: unlink failed", exc_info=True)


class UnixSocketChannel(IpcChannel):
    """The App's socket file at *path*."""

    def __init__(self, path: Path) -> None:
        """``ImportError`` where ``AF_UNIX`` does not exist."""
        if not hasattr(socket, "AF_UNIX"):
            raise ImportError("this Python has no AF_UNIX")
        self._path = path
        frame_log.debug("UnixSocketChannel: %s", path)

    @property
    def address(self) -> str:
        frame_log.debug("UnixSocketChannel.address")
        return str(self._path)

    def connect(self, timeout: float | None) -> socket.socket:
        frame_log.debug("UnixSocketChannel.connect: timeout=%s", timeout)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(str(self._path))
        except OSError:
            sock.close()
            raise
        return sock

    def reachable(self) -> bool:
        """No file means no App, without a connect to find out."""
        if not self._path.exists():
            log.debug("UnixSocketChannel.reachable: no socket file")
            return False
        return super().reachable()

    def listen(self) -> _UnixListener:
        path = self._path
        log.info("UnixSocketChannel.listen: %s", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(8)
        path.chmod(0o600)
        return _UnixListener(sock, path)
