"""Linux device detection helpers.

linux_scsi_resolver: injected into DeviceDetector by builder on Linux.
find_lcd_devices: enriches raw DetectedDevice list with saved identity +
    LED probing — used by the GUI (trcc_app.py via scsi.py).
"""
from __future__ import annotations

import logging
import os
from operator import itemgetter

log = logging.getLogger(__name__)


def linux_scsi_resolver(vid: int, pid: int) -> str | None:
    """Map a VID:PID to its /dev/sg* or /dev/sd* path via sysfs.

    Walks /sys/class/scsi_generic/ to find the SCSI generic device whose
    USB parent matches the given VID:PID. Falls back to /dev/sd* block
    devices when the sg kernel module is not loaded.
    """
    from trcc.adapters.infra.data_repository import SysUtils

    # Pass 1: /dev/sg* (sg module loaded)
    for sg_name in SysUtils.find_scsi_devices():
        sysfs_base = f"/sys/class/scsi_generic/{sg_name}/device"
        if not os.path.exists(sysfs_base):
            continue
        resolved = _resolve_vid_pid(sysfs_base)
        if resolved and resolved == (vid, pid):
            return f"/dev/{sg_name}"

    # Pass 2: /dev/sd* block devices (sg module not loaded)
    for sd_name in SysUtils.find_scsi_block_devices():
        sysfs_base = f"/sys/block/{sd_name}/device"
        resolved = _resolve_vid_pid(sysfs_base)
        if resolved and resolved == (vid, pid):
            log.info("sg module not loaded — using block device /dev/%s", sd_name)
            return f"/dev/{sd_name}"

    return None


def _resolve_vid_pid(sysfs_base: str) -> tuple[int, int] | None:
    """Walk sysfs parents to find the VID:PID for a SCSI device."""
    try:
        device_path = os.path.realpath(sysfs_base)
        for _ in range(10):
            device_path = os.path.dirname(device_path)
            vid_path = os.path.join(device_path, "idVendor")
            pid_path = os.path.join(device_path, "idProduct")
            if os.path.exists(vid_path) and os.path.exists(pid_path):
                with open(vid_path) as vf:
                    v = int(vf.read().strip(), 16)
                with open(pid_path) as pf:
                    p = int(pf.read().strip(), 16)
                return v, p
    except (OSError, ValueError):
        pass
    log.warning("sysfs VID/PID walk failed for %s — skipping device", sysfs_base)
    return None


# ---------------------------------------------------------------------------
# find_lcd_devices — enriched device list for the GUI
# ---------------------------------------------------------------------------

def find_lcd_devices(detect_fn=None) -> list[dict]:
    """Detect connected LCD devices and enrich with saved identity + LED probe.

    Args:
        detect_fn: Callable returning raw DetectedDevice list. Defaults to
            DeviceDetector.detect. Injected by tests and composition roots.

    Returns:
        List of dicts with keys: name, path, resolution, vendor, product,
        model, button_image, protocol, device_type, vid, pid
    """
    if detect_fn is None:
        from trcc.adapters.device.detector import DeviceDetector
        detect_fn = DeviceDetector.detect

    raw = detect_fn()
    devices = []

    for dev in raw:
        protocol = getattr(dev, 'protocol', 'scsi')
        device_type = getattr(dev, 'device_type', 1)

        # Load previously resolved device identity from config (C# SetButtonImage).
        # First launch: no saved data, uses registry defaults. Handshake resolves
        # the real product and saves to config. Subsequent launches: correct button
        # image shown immediately. Re-verified on each handshake (detects cooler swaps).
        dev_idx = len(devices)
        saved_btn, saved_product = _load_saved_identity(dev_idx, dev.vid, dev.pid)

        if protocol == 'scsi':
            if not dev.scsi_device:
                continue
            product = saved_product or dev.product_name
            devices.append({
                'name': f"Thermalright {product}" if saved_product
                        else f"{dev.vendor_name} {dev.product_name}",
                'path': dev.scsi_device,
                'resolution': (0, 0),
                'vendor': dev.vendor_name,
                'product': product,
                'model': dev.model,
                'button_image': saved_btn or dev.button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': 'scsi',
                'device_type': 1,
                'implementation': dev.implementation,
            })
        elif protocol == 'hid':
            hid_path = f"hid:{dev.vid:04x}:{dev.pid:04x}"
            model = dev.model
            button_image = saved_btn or dev.button_image
            led_style_id = None

            if dev.implementation == 'hid_led':
                try:
                    from trcc.adapters.device.led import probe_led_model
                    from trcc.core.models import get_button_image
                    info = probe_led_model(dev.vid, dev.pid, usb_path=dev.usb_path)
                    if info and info.model_name:
                        model = info.model_name
                        led_style_id = info.style.style_id if info.style else None
                        if (btn := get_button_image(info.pm, info.sub_type, is_led=True)):
                            button_image = btn
                except Exception as e:
                    # Best-effort enrichment — probe_led_model returns None for
                    # the common failure paths (permission, busy) but its handshake
                    # retry layer can re-raise arbitrary types from the USB stack;
                    # broad-but-logged so detection still succeeds with generic LED.
                    log.debug(
                        "linux/detector: LED probe failed for %04x:%04x: %s",
                        dev.vid, dev.pid, e,
                    )

            product = saved_product or dev.product_name
            devices.append({
                'name': f"Thermalright {product}" if saved_product
                        else f"{dev.vendor_name} {dev.product_name}",
                'path': hid_path,
                'resolution': (0, 0),
                'vendor': dev.vendor_name,
                'product': product,
                'model': model,
                'led_style_id': led_style_id,
                'button_image': button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': 'hid',
                'device_type': device_type,
                'implementation': dev.implementation,
            })
        elif protocol in ('bulk', 'ly'):
            dev_path = f"{protocol}:{dev.vid:04x}:{dev.pid:04x}"
            product = saved_product or dev.product_name
            devices.append({
                'name': f"Thermalright {product}" if saved_product
                        else f"{dev.vendor_name} {dev.product_name}",
                'path': dev_path,
                'resolution': (0, 0),
                'vendor': dev.vendor_name,
                'product': product,
                'model': dev.model,
                'button_image': saved_btn or dev.button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': protocol,
                'device_type': device_type,
                'implementation': dev.implementation,
            })

    devices.sort(key=itemgetter('path'))
    for i, d in enumerate(devices):
        d['device_index'] = i

    return devices


def _load_saved_identity(
    dev_index: int, vid: int, pid: int,
) -> tuple[str | None, str | None]:
    try:
        from trcc.conf import Settings
        key = Settings.device_config_key(dev_index, vid, pid)
        cfg = Settings.get_device_config(key)
        return cfg.get('resolved_button_image'), cfg.get('resolved_product')
    except (OSError, KeyError, AttributeError, ImportError) as e:
        # Best-effort cache lookup — if Settings is unavailable (test
        # fixture, broken config.json), fall back to fresh detection.
        # Logged at debug because falling back is normal on first-run.
        log.debug(
            "linux/detector: cached button-image lookup failed for "
            "idx=%d vid=%04x pid=%04x — falling back to fresh detection: %s",
            dev_index, vid, pid, e,
        )
        return None, None

