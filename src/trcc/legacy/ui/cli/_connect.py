"""Shared CLI device connection helper — used by both LCD and LED commands."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def connect_device(device_path: str | None = None) -> int:
    """Connect device via discover(). Returns exit code (0 = success).

    Args:
        device_path: Optional device path (e.g. '/dev/sg0').
    """
    from trcc.legacy._boot import trcc

    log.debug("connecting device=%s", device_path)
    t = trcc()
    result = t.discover(path=device_path)
    devices = list(t.lcd_devices) + list(t.led_devices)
    if not result.success or not devices:
        error = result.error or "No device found."
        log.warning("connect failed: %s", error)
        print(error)
        print("Run 'trcc report' to diagnose.")
        return 1
    log.debug("connected successfully (%d device(s))", len(devices))
    return 0


def print_result(result: dict, *, preview: bool = False) -> int:
    """Print result message + optional ANSI preview. Returns exit code.

    Shared by LCD and LED CLI commands. Handles both image and color previews.
    """
    if not result["success"]:
        print(f"Error: {result.get('error', 'Unknown error')}")
        return 1
    if result.get("warning"):
        print(f"Warning: {result['warning']}")
    print(result["message"])
    if preview:
        if result.get("image"):
            from trcc.legacy.services import ImageService
            print(ImageService.to_ansi(result["image"]))
        elif result.get("colors"):
            from trcc.legacy.services import LEDService
            print(LEDService.zones_to_ansi(result["colors"]))
    return 0
