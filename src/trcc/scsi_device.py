"""
SCSI Device Bridge — connects MVC models to lcd_driver/device_detector.

models.py imports `from ..scsi_device import find_lcd_devices, send_image_to_device`
This module provides those two functions.

SCSI send protocol is inlined here (from trcc_handshake_v2) so everything
lives in one place under src/trcc/.  LCDDriver is used only for resolution
auto-detection during device discovery.
"""

import binascii
import os
import struct
import subprocess
import tempfile
from typing import Dict, List, Set

from .paths import require_sg_raw

# Track which devices have been initialized (poll + init sent)
_initialized_devices: Set[str] = set()

# NOTE: SCSI devices (0402:3922, 87CD:70DB, 0416:5406) cannot be identified
# beyond their VID:PID. The firmware reports "USBLCD / USB PRC System" for all
# variants (SE, PRO, Ultra). Model identification (PM/SUB bytes) only works on
# HID devices via the DA/DB/DC/DD handshake in hid_device.py.
# See tools/probe_usb_device.py for the full investigation.

# =========================================================================
# Low-level SCSI helpers (Mode 3 protocol, from USBLCD.exe 20480-20540)
# =========================================================================

# Base command for frame data chunks; chunk index goes in bits [27:24]
_FRAME_CMD_BASE = 0x101F5
_CHUNK_SIZE = 0x10000  # 64 KiB per chunk (except possibly the last)


def _get_frame_chunks(width: int, height: int) -> list:
    """Calculate frame chunk commands for a given resolution.

    Each chunk is up to 64 KiB. The command encodes the chunk index in
    bits [27:24] above the base command 0x101F5.

    For 320×320: 4 chunks (3×64K + 8K = 204,800 bytes)
    For 480×480: 8 chunks (7×64K + 2K = 460,800 bytes)
    """
    total = width * height * 2  # RGB565: 2 bytes per pixel
    chunks = []
    offset = 0
    idx = 0
    while offset < total:
        size = min(_CHUNK_SIZE, total - offset)
        cmd = _FRAME_CMD_BASE | (idx << 24)
        chunks.append((cmd, size))
        offset += size
        idx += 1
    return chunks


def _crc32(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def _build_header(cmd: int, size: int) -> bytes:
    """Build 20-byte SCSI command header: cmd(4) + zeros(8) + size(4) + crc32(4)."""
    header_16 = struct.pack('<I', cmd) + b'\x00' * 8 + struct.pack('<I', size)
    crc = _crc32(header_16)
    return header_16 + struct.pack('<I', crc)


def _scsi_read(dev: str, cdb: bytes, length: int) -> bytes:
    """Execute SCSI READ via sg_raw."""
    require_sg_raw()
    cdb_hex = ' '.join(f'{b:02x}' for b in cdb)
    cmd = ['sg_raw', '-r', str(length), dev] + cdb_hex.split()
    result = subprocess.run(cmd, capture_output=True, timeout=10)
    return result.stdout if result.returncode == 0 else b''


def _scsi_write(dev: str, header: bytes, data: bytes) -> bool:
    """Execute SCSI WRITE via sg_raw with temp file for payload."""
    require_sg_raw()
    cdb_hex = ' '.join(f'{b:02x}' for b in list(header[:16]))

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data)
        tmp_path = f.name

    cmd = ['sg_raw', '-s', str(len(data)), '-i', tmp_path, dev] + cdb_hex.split()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    finally:
        os.unlink(tmp_path)


def _init_device(dev: str):
    """Poll + init handshake (must be called before first frame send)."""
    # Step 1: Poll
    poll_header = _build_header(0xF5, 0xE100)
    _scsi_read(dev, poll_header[:16], 0xE100)

    # Step 2: Init
    init_header = _build_header(0x1F5, 0xE100)
    _scsi_write(dev, init_header, b'\x00' * 0xE100)


def _send_frame(dev: str, rgb565_data: bytes, width: int = 320, height: int = 320):
    """Send one RGB565 frame in SCSI chunks sized for the resolution."""
    chunks = _get_frame_chunks(width, height)
    total_size = sum(size for _, size in chunks)
    if len(rgb565_data) < total_size:
        rgb565_data += b'\x00' * (total_size - len(rgb565_data))

    offset = 0
    for cmd, size in chunks:
        header = _build_header(cmd, size)
        _scsi_write(dev, header, rgb565_data[offset:offset + size])
        offset += size


# =========================================================================
# Public API (used by core/models.py)
# =========================================================================

def find_lcd_devices() -> List[Dict]:
    """Detect connected LCD devices (SCSI and HID).

    Returns:
        List of dicts with keys: name, path, resolution, vendor, product,
        model, button_image, protocol, device_type, vid, pid
    """
    try:
        from .device_detector import detect_devices
    except ImportError:
        return []

    raw = detect_devices()
    devices = []

    for dev in raw:
        protocol = getattr(dev, 'protocol', 'scsi')
        device_type = getattr(dev, 'device_type', 1)

        if protocol == 'scsi':
            # SCSI devices need a /dev/sgX path
            if not dev.scsi_device:
                continue

            # Detect resolution via LCDDriver if possible
            resolution = (320, 320)
            try:
                from .lcd_driver import LCDDriver
                driver = LCDDriver(device_path=dev.scsi_device, auto_detect_resolution=True)
                if driver.implementation:
                    resolution = driver.implementation.resolution
            except Exception:
                pass

            devices.append({
                'name': f"{dev.vendor_name} {dev.product_name}",
                'path': dev.scsi_device,
                'resolution': resolution,
                'vendor': dev.vendor_name,
                'product': dev.product_name,
                'model': dev.model,
                'button_image': dev.button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': 'scsi',
                'device_type': 1,
                'implementation': dev.implementation,
            })
        elif protocol == 'hid':
            # HID devices use USB VID:PID directly (no SCSI path)
            # Path is a synthetic identifier for the factory
            hid_path = f"hid:{dev.vid:04x}:{dev.pid:04x}"

            model = dev.model
            button_image = dev.button_image

            # For LED devices sharing a VID:PID (e.g. 0416:8001), probe
            # the firmware via HID handshake to discover the real model.
            # Without this, all devices default to AX120_DIGITAL.
            if dev.implementation == 'hid_led':
                try:
                    from .led_device import probe_led_model
                    info = probe_led_model(dev.vid, dev.pid)
                    if info and info.model_name:
                        model = info.model_name
                        # Clear button_image — sidebar will show text label
                        # for models without a dedicated image asset.
                        button_image = ''
                except Exception:
                    pass  # Fall back to registry default

            devices.append({
                'name': f"{dev.vendor_name} {dev.product_name}",
                'path': hid_path,
                'resolution': (320, 320),  # Will be updated after HID handshake
                'vendor': dev.vendor_name,
                'product': dev.product_name,
                'model': model,
                'button_image': button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': 'hid',
                'device_type': device_type,
                'implementation': dev.implementation,
            })

    # Sort by path for stable ordinal assignment
    devices.sort(key=lambda d: d['path'])
    for i, d in enumerate(devices):
        d['device_index'] = i

    return devices


def send_image_to_device(
    device_path: str,
    rgb565_data: bytes,
    width: int,
    height: int,
) -> bool:
    """Send RGB565 image data to an LCD device via SCSI.

    Initializes (poll + init) on first send to each device, then skips
    init for subsequent sends.

    Args:
        device_path: SCSI device path (e.g. /dev/sg0)
        rgb565_data: Raw RGB565 pixel bytes (big-endian, width*height*2 bytes)
        width: Image width in pixels
        height: Image height in pixels

    Returns:
        True if the send succeeded.
    """
    try:
        if device_path not in _initialized_devices:
            _init_device(device_path)
            _initialized_devices.add(device_path)

        _send_frame(device_path, rgb565_data, width, height)
        return True
    except Exception as e:
        print(f"[!] SCSI send failed ({device_path}): {e}")
        # Allow re-init on next attempt
        _initialized_devices.discard(device_path)
        return False
