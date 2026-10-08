"""The OpenRGB address both windows take as one "host:port" field (#160).

Toolkit-free and shared, so the two skins cannot parse it differently.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

#: A hostname or IP address -- the same rule the API enforces on its body.
_HOST = re.compile(r"^[A-Za-z0-9.\-]+$")


def parse_openrgb_address(text: str) -> tuple[str, int] | None:
    """``"host:port"`` (or just ``"host"``, port 6742) -> ``(host, port)``.

    ``None`` for anything that is not a plain host and a port in 1-65535.
    """
    log.debug("parse_openrgb_address: %r", text)
    host, sep, port_text = text.strip().rpartition(":")
    if not sep:
        host, port_text = port_text, "6742"
    if not _HOST.match(host) or not port_text.isdigit():
        return None
    port = int(port_text)
    return (host, port) if 0 < port < 65536 else None


def format_openrgb_address(host: str, port: int) -> str:
    """The field's text for a saved host and port."""
    log.debug("format_openrgb_address: %s:%d", host, port)
    return f"{host}:{port}"
