"""Unix-socket client for the root-only ``trcc-powermetrics`` LaunchDaemon helper.

The main TRCC process stays unprivileged; a separately installed daemon runs
``powermetrics -f plist`` on the helper's behalf and returns the XML payload over
a framed binary channel.

Install: from the DMG, open ``PrivilegedHelper`` and run ``sudo ./install-helper.sh``,
or build ``native/macos/trcc_powermetrics_helper`` and run that script from the
checkout. Override the socket path with
``TRCC_POWERMETRICS_SOCKET`` (set empty to disable helper and use local
``subprocess`` only).
"""
from __future__ import annotations

import logging
import os
import re
import socket

log = logging.getLogger(__name__)

_DEFAULT_SOCKET = '/var/run/trcc-powermetrics.sock'
_MAGIC = b'TRC1'
_MAX_BODY = 2_000_000
_MAX_SAMPLERS_LEN = 256

_SAFE_SAMPLERS = re.compile(r'^[a-zA-Z0-9_,]+$')

__all__ = [
    'fetch_powermetrics_bytes',
    'fetch_powermetrics_text',
    'powermetrics_socket_path',
    'samplers_allowed',
]


def powermetrics_socket_path() -> str | None:
    """Return helper socket path, or None if disabled via ``TRCC_POWERMETRICS_SOCKET=``."""
    raw = os.environ.get('TRCC_POWERMETRICS_SOCKET', _DEFAULT_SOCKET)
    if not raw.strip():
        return None
    return raw


def samplers_allowed(s: str) -> bool:
    if not s or len(s) > _MAX_SAMPLERS_LEN:
        return False
    return bool(_SAFE_SAMPLERS.fullmatch(s))


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError('short read from powermetrics helper')
        buf.extend(chunk)
    return bytes(buf)


def fetch_powermetrics_bytes(
    samplers: str,
    *,
    timeout: float = 12.0,
) -> bytes | None:
    """Fetch one ``powermetrics`` snapshot (plist XML by default) from the helper."""
    path = powermetrics_socket_path()
    if path is None:
        return None
    if not samplers_allowed(samplers):
        log.debug('powermetrics helper: rejected unsafe samplers %r', samplers)
        return None
    payload = (samplers + '\n').encode('utf-8')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(payload)
            hdr = _read_exact(sock, 8)
            if len(hdr) < 8 or hdr[:4] != _MAGIC:
                log.debug('powermetrics helper: bad magic or short header')
                return None
            status = int.from_bytes(hdr[4:8], 'big')
            body_len = int.from_bytes(_read_exact(sock, 4), 'big')
            if body_len > _MAX_BODY:
                log.debug('powermetrics helper: body too large')
                return None
            body = _read_exact(sock, body_len) if body_len else b''
            if status != 0:
                msg = body.decode('utf-8', errors='replace')[:500]
                log.debug('powermetrics helper error status=%s: %s', status, msg)
                return None
            return body
    except OSError as e:
        log.debug('powermetrics helper unavailable: %s', e)
        return None


def fetch_powermetrics_text(
    samplers: str,
    *,
    timeout: float = 12.0,
) -> str | None:
    """Fetch from helper and decode as UTF-8 text (legacy / debugging)."""
    raw = fetch_powermetrics_bytes(samplers, timeout=timeout)
    if raw is None:
        return None
    return raw.decode('utf-8', errors='replace')
