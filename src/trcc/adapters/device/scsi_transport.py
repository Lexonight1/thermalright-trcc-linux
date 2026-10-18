"""The one SCSI transport every OS shares -- each OS supplies only its raw calls.

Linux (SG_IO), Windows (DeviceIoControl), macOS/BSD (USB BOT) and the Windows
service proxy each wrote the whole transport, and the parts that should have
been one drifted apart: which lines log per frame, what "the panel is gone"
does, whether a poll's delivered reply survives an error, who may hold the
panel at once.  This base owns all of that once; a subclass answers the hooks:

    _open_handle / _close_handle                     the OS handle
    _send / _read                                    one raw command each
    _claim / _release                                single owner (default: none)

Each raw command returns a :class:`CdbResult`.  The base turns ``gone`` into
the disconnect-class error the recovery threshold counts, logs a failure the
same way on every OS, and keeps data a subclass marked as delivered.
"""
from __future__ import annotations

import errno
import logging
from abc import abstractmethod
from typing import NamedTuple

from ...core.errors import TransportError
from ...core.logs import per_frame
from ...core.ports import ScsiTransport


class CdbResult(NamedTuple):
    """What one raw SCSI command did.  A NamedTuple, not a frozen dataclass:
    one is built per CDB, four per frame, and a frozen dataclass sets every
    field through ``object.__setattr__`` (measured 2026-10-18).

    ``ok``: it completed clean.  ``gone``: the device is unplugged or
    offlined -- no retry will help.  ``data``: the data-in the OS reports as
    delivered; a subclass fills it on a failed read only when the reply is
    trustworthy (the panel's handshake poll answers that way, #301).
    ``detail``: the OS's own status, for the log.
    """
    ok: bool
    gone: bool = False
    data: bytes = b""
    detail: str = ""


class BaseScsiTransport(ScsiTransport):
    """Open/close, the per-frame log, failure policy and single owner -- once."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._opened = False        # the base opened the handle and owns it
        # The concrete transport's own module, so a report still says which
        # OS path ran (``trcc.adapters.system.linux``), not this base.
        self._log = logging.getLogger(type(self).__module__)
        self._frame_log = per_frame(type(self).__module__)
        self._log.info("%s: bound to %s", type(self).__name__, path)

    # ── Template methods ─────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        opened = self._opened
        self._frame_log.debug("%s.is_open → %s (%s)",
                              type(self).__name__, opened, self._path)
        return opened

    def open(self) -> bool:
        if self._opened:
            self._log.debug("%s.open: %s already open",
                            type(self).__name__, self._path)
            return True
        if not self._open_handle():
            return False
        if not self._claim():
            self._close_handle()
            return False
        self._opened = True
        self._log.info("%s: opened %s", type(self).__name__, self._path)
        return True

    def close(self) -> None:
        if not self._opened:
            self._log.debug("%s.close: %s already closed",
                            type(self).__name__, self._path)
            return
        self._opened = False
        self._log.info("%s: closing %s", type(self).__name__, self._path)
        self._release()
        try:
            self._close_handle()
        except OSError as e:
            self._log.warning("%s: closing %s raised %s — continuing",
                              type(self).__name__, self._path, e)

    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
        """True when it completed clean; raises once the device is gone."""
        self._frame_log.debug("%s.send_cdb: cdb_len=%d data_len=%d timeout=%dms",
                              type(self).__name__, len(cdb), len(data),
                              timeout_ms)
        self._require_open("send_cdb")
        return self._judge(self._send(cdb, data, timeout_ms), "send_cdb").ok

    def read_cdb(self, cdb: bytes, length: int,
                 timeout_ms: int = 5000) -> bytes:
        """The data-in; empty when nothing trustworthy arrived."""
        self._log.debug("%s.read_cdb: cdb_len=%d length=%d timeout=%dms",
                        type(self).__name__, len(cdb), length, timeout_ms)
        self._require_open("read_cdb")
        return self._judge(self._read(cdb, length, timeout_ms), "read_cdb").data

    # ── Shared policy ────────────────────────────────────────────────────

    def _require_open(self, op: str) -> None:
        """A command on a closed handle is a caller bug: say so, then raise.

        Logs only when it raises: it runs on every CDB (four per frame)."""
        if not self._opened:
            self._log.error("%s.%s: %s not open", type(self).__name__, op,
                            self._path)
            raise TransportError(f"{type(self).__name__} {self._path} not open")

    def _judge(self, result: CdbResult, op: str) -> CdbResult:
        """Gone raises disconnect-class; a failure logs; delivered data stays.

        A gone device raised as ENODEV is what the recovery threshold counts,
        so it marks the panel lost (#254).  Any other failure is soft -- the
        next frame retries, as USBLCD.exe treats every write.  A clean
        result returns before any logging: four of these run per frame.
        """
        if result.ok:
            return result
        if result.gone:
            self._log.warning("%s %s on %s: %s -- the device is gone",
                              type(self).__name__, op, self._path,
                              result.detail)
            raise TransportError(
                f"{op} on {self._path}: device gone ({result.detail})",
            ) from OSError(errno.ENODEV, result.detail)
        if not result.ok:
            if result.data:
                self._log.info("%s on %s: %d byte(s) with %s — the panel's "
                               "usual poll answer, kept", op, self._path,
                               len(result.data), result.detail)
            else:
                self._log.warning("%s %s on %s failed: %s",
                                  type(self).__name__, op, self._path,
                                  result.detail)
        return result

    # ── Hooks ────────────────────────────────────────────────────────────

    @abstractmethod
    def _open_handle(self) -> bool:
        """Open the OS handle.  False when not answering yet; raise
        ``PermissionError_`` naming the fix when the user may not open it."""

    @abstractmethod
    def _close_handle(self) -> None:
        """Close the OS handle and drop anything cached for it."""

    @abstractmethod
    def _send(self, cdb: bytes, data: bytes, timeout_ms: int) -> CdbResult:
        """One CDB with a data-out phase."""

    @abstractmethod
    def _read(self, cdb: bytes, length: int, timeout_ms: int) -> CdbResult:
        """One CDB with a data-in phase of up to *length* bytes."""

    def _claim(self) -> bool:
        """Take the single-owner claim on the open handle.  No claim by
        default; an OS that can lock overrides this."""
        self._log.debug("%s._claim: no single-owner claim on this OS",
                        type(self).__name__)
        return True

    def _release(self) -> None:
        """Give the claim back before the handle closes."""
        self._log.debug("%s._release: nothing to release", type(self).__name__)
