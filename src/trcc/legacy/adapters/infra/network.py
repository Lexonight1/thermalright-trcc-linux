"""Network infrastructure — LAN IP detection for API server."""

import logging
import socket

log = logging.getLogger(__name__)


def get_lan_ip() -> str:
    """Auto-detect LAN IP by probing default route interface.

    Opens a UDP socket to a public DNS (no data sent) to determine
    which local interface the OS would route through.
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
