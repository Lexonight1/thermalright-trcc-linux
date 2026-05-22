"""Device detection, selection, and probing."""
from __future__ import annotations

import logging

from trcc.legacy.ui.cli import _cli_handler

log = logging.getLogger(__name__)


def _probe(dev):
    """Resolve device details via Trcc's protocol-aware probe.

    Thin wrapper kept for the existing CLI call sites — the actual
    HID / Bulk / LED handshake logic lives on ``Trcc.probe`` so UIs
    don't import ``DeviceProtocolFactory`` directly.
    """
    from trcc.legacy._boot import trcc as _trcc
    return _trcc().probe(dev)


def _format(dev, probe=False):
    """Format a detected device for display."""
    vid_pid = f"[{dev.vid:04x}:{dev.pid:04x}]"
    proto = dev.protocol.upper()
    if dev.scsi_device:
        path = dev.scsi_device
    elif dev.protocol in ("hid", "bulk", "ly", "led"):
        path = f"{dev.vid:04x}:{dev.pid:04x}"
    else:
        path = "No device path found"
    line = f"{path} — {dev.product_name} {vid_pid} ({proto})"

    if not probe:
        return line

    if not (info := _probe(dev)):
        return line

    details = []
    if 'model' in info:
        details.append(f"model: {info['model']}")
    if 'resolution' in info:
        w, h = info['resolution']
        details.append(f"resolution: {w}x{h}")
    if 'pm' in info:
        details.append(f"PM={info['pm']}")
    if 'serial' in info:
        details.append(f"serial: {info['serial'][:16]}")

    if details:
        line += f" ({', '.join(details)})"
    return line


@_cli_handler
def detect(show_all=False, detect_fn=None, os_platform=None):
    """Detect LCD device."""
    from trcc.legacy.conf import Settings
    log.debug("detect called show_all=%s", show_all)
    if detect_fn is None or os_platform is None:
        from trcc.legacy._boot import trcc as _trcc
        app = _trcc()
        if detect_fn is None:
            detect_fn = app.detect
        if os_platform is None:
            os_platform = app.os
    devices = detect_fn()
    log.debug("detected %d device(s)", len(devices))

    if not devices:
        print("No compatible TRCC LCD device detected.")
        if (hint := os_platform.no_devices_hint()):
            print(hint)
        return 1

    selected = Settings.get_selected_device()
    if show_all or len(devices) > 1:
        for i, dev in enumerate(devices, 1):
            marker = "*" if dev.path == selected else " "
            print(f"{marker} [{i}] {_format(dev, probe=True)}")
        if len(devices) > 1:
            print("\nUse 'trcc select N' to switch devices")
    else:
        print(f"Active: {_format(devices[0], probe=True)}")

    for warning in os_platform.check_permissions(devices):
        print(f"\n{warning}")

    return 0


@_cli_handler
def select(number, detect_fn=None):
    """Select a device by number.

    DEPRECATED: CLI now auto-discovers every invocation. Use --lcd N /
    --led N on per-command flags to target a specific device.
    """
    import sys

    from trcc.legacy.conf import Settings

    print(
        "Note: 'trcc select' is deprecated — auto-discovery replaces it. "
        "Use --lcd N or --led N on per-command flags.",
        file=sys.stderr,
    )
    log.debug("select device number=%d", number)
    if detect_fn is None:
        from trcc.legacy._boot import trcc as _trcc
        detect_fn = _trcc().detect
    if not (devices := detect_fn()):
        print("No devices found.")
        return 1

    if number < 1 or number > len(devices):
        print("Invalid device number. Available devices:")
        for i, dev in enumerate(devices, 1):
            print(f"  [{i}] {_format(dev)}")
        return 1

    device = devices[number - 1]
    Settings.save_selected_device(device.path)
    print(f"Selected: {_format(device)}")
    return 0
