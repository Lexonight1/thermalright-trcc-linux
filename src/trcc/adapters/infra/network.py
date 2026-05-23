"""Network infrastructure — LAN IP detection for API server.

Pure stdlib (``socket``) — no deps.  Used by ``trcc api`` so that
binding ``0.0.0.0`` can print the actual reachable URL instead of the
literal ``0.0.0.0`` (useless to the user).
"""
from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)


def get_lan_ip() -> str:
    """Auto-detect LAN IP by probing the default route interface.

    Opens a UDP socket to a public DNS (no data sent) so the OS
    populates the local endpoint with whichever interface it would
    route through.  Falls back to loopback when offline.
    """
    log.debug("get_lan_ip: probing default route interface")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            log.debug("get_lan_ip: detected ip=%s", ip)
            return ip
    except OSError:
        log.debug("get_lan_ip: OSError, falling back to 127.0.0.1")
        return "127.0.0.1"
