"""Adapters for other RGB systems that follow the cooler (#160)."""
from __future__ import annotations

import logging

from ...core.models import RgbFollowMode
from ...core.ports import RgbMirror
from .corsair_dram import CorsairDramMirror
from .openrgb import OpenRgbMirror

log = logging.getLogger(__name__)


def make_mirror(mode: RgbFollowMode, host: str, port: int) -> RgbMirror:
    """The follower for *mode*: OpenRGB at *host*:*port*, or Corsair RAM."""
    log.info("make_mirror: %s", mode.value)
    match mode:
        case RgbFollowMode.OPENRGB:
            return OpenRgbMirror(host, port)
        case RgbFollowMode.RAM:
            return CorsairDramMirror()
    raise ValueError(f"nothing follows in mode {mode.value!r}")
