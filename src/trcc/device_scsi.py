"""
SCSI Device Bridge — connects MVC models to lcd_driver/device_detector.

models.py imports `from ..device_scsi import find_lcd_devices, send_image_to_device`
This module provides those two functions.

SCSI send protocol is inlined here (from trcc_handshake_v2) so everything
lives in one place under src/trcc/.  LCDDriver is used only for resolution
auto-detection during device discovery.
"""

import binascii
import logging
import struct
import subprocess
import tempfile
import time
from typing import Dict, List, Set

from .core.models import RESOLUTION_TO_PM as _RESOLUTION_TO_PM
from .core.models import HandshakeResult, fbl_to_resolution
from .data_repository import SysUtils

log = logging.getLogger(__name__)

# Boot signature: device still initializing its display controller
_BOOT_SIGNATURE = b'\xa1\xa2\xa3\xa4'
_BOOT_WAIT_SECONDS = 3.0
_BOOT_MAX_RETRIES = 5
# Brief pause after init before first frame (lets controller settle)
_POST_INIT_DELAY = 0.1

# Base command for frame data chunks; chunk index goes in bits [27:24]
_FRAME_CMD_BASE = 0x101F5
_CHUNK_SIZE = 0x10000  # 64 KiB per chunk (except possibly the last)


# =========================================================================
# SCSI device class
# =========================================================================


class ScsiDevice:
    """SCSI LCD device handler wrapping sg_raw subprocess calls."""

    # Track which devices have been initialized (poll + init sent)
    _initialized_devices: Set[str] = set()

    def __init__(self, device_path: str, width: int = 320, height: int = 320):
        self.device_path = device_path
        self.width = width
        self.height = height
        self._initialized = False

    # --- Low-level SCSI helpers (Mode 3 protocol) ---

    @staticmethod
    def _get_frame_chunks(width: int, height: int) -> list:
        """Calculate frame chunk commands for a given resolution.

        Each chunk is up to 64 KiB. The command encodes the chunk index in
        bits [27:24] above the base command 0x101F5.

        For 320x320: 4 chunks (3x64K + 8K = 204,800 bytes)
        For 480x480: 8 chunks (7x64K + 2K = 460,800 bytes)
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

    @staticmethod
    def _crc32(data: bytes) -> int:
        return binascii.crc32(data) & 0xFFFFFFFF

    @staticmethod
    def _build_header(cmd: int, size: int) -> bytes:
        """Build 20-byte SCSI command header: cmd(4) + zeros(8) + size(4) + crc32(4)."""
        header_16 = struct.pack('<I', cmd) + b'\x00' * 8 + struct.pack('<I', size)
        crc = ScsiDevice._crc32(header_16)
        return header_16 + struct.pack('<I', crc)

    @staticmethod
    def _scsi_read(dev: str, cdb: bytes, length: int) -> bytes:
        """Execute SCSI READ via sg_raw."""
        SysUtils.require_sg_raw()
        cdb_hex = ' '.join(f'{b:02x}' for b in cdb)
        cmd = ['sg_raw', '-r', str(length), dev] + cdb_hex.split()
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        return result.stdout if result.returncode == 0 else b''

    @staticmethod
    def _scsi_write(dev: str, header: bytes, data: bytes) -> bool:
        """Execute SCSI WRITE via sg_raw with temp file for payload."""
        SysUtils.require_sg_raw()
        cdb_hex = ' '.join(f'{b:02x}' for b in list(header[:16]))

        with tempfile.NamedTemporaryFile(delete=True) as f:
            f.write(data)
            f.flush()
            cmd = ['sg_raw', '-s', str(len(data)), '-i', f.name, dev] + cdb_hex.split()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return result.returncode == 0

    @staticmethod
    def _init_device(dev: str) -> int:
        """Poll + init handshake (must be called before first frame send).

        Matches USBLCD.exe initialization sequence:
        1. Poll (cmd=0xF5) -> read 0xE100 bytes
        2. If bytes[4:8] == 0xA1A2A3A4, device is still booting -> wait 3s, re-poll
        3. Init (cmd=0x1F5) -> write 0xE100 zeros
        4. Brief delay to let display controller settle before first frame

        Returns:
            FBL byte (poll response byte[0]).  This IS the FBL directly --
            the ASCII value maps to a resolution via fbl_to_resolution().
        """
        poll_header = ScsiDevice._build_header(0xF5, 0xE100)

        # Step 1: Poll with boot state check
        response = b''
        for attempt in range(_BOOT_MAX_RETRIES):
            response = ScsiDevice._scsi_read(dev, poll_header[:16], 0xE100)
            if len(response) >= 8 and response[4:8] == _BOOT_SIGNATURE:
                log.info("Device %s still booting (attempt %d/%d), waiting %.0fs...",
                         dev, attempt + 1, _BOOT_MAX_RETRIES, _BOOT_WAIT_SECONDS)
                time.sleep(_BOOT_WAIT_SECONDS)
            else:
                break

        # Extract FBL from poll response byte[0]
        fbl = response[0] if response else 100  # default FBL 100 = 320x320
        log.debug("SCSI poll byte[0] = %d (FBL)", fbl)

        # Step 2: Init
        init_header = ScsiDevice._build_header(0x1F5, 0xE100)
        ScsiDevice._scsi_write(dev, init_header, b'\x00' * 0xE100)

        # Step 3: Brief delay to let display controller settle
        time.sleep(_POST_INIT_DELAY)

        return fbl

    @staticmethod
    def _send_frame(dev: str, rgb565_data: bytes, width: int = 320, height: int = 320):
        """Send one RGB565 frame in SCSI chunks sized for the resolution."""
        chunks = ScsiDevice._get_frame_chunks(width, height)
        total_size = sum(size for _, size in chunks)
        if len(rgb565_data) < total_size:
            rgb565_data += b'\x00' * (total_size - len(rgb565_data))

        offset = 0
        for cmd, size in chunks:
            header = ScsiDevice._build_header(cmd, size)
            ScsiDevice._scsi_write(dev, header, rgb565_data[offset:offset + size])
            offset += size

    # --- Instance methods ---

    def handshake(self) -> HandshakeResult:
        """Poll + init the SCSI device.

        Reads FBL from poll response byte[0] and resolves
        the actual LCD resolution via fbl_to_resolution().
        """
        fbl = ScsiDevice._init_device(self.device_path)
        resolution = fbl_to_resolution(fbl)
        self.width, self.height = resolution
        self._initialized = True
        return HandshakeResult(resolution=resolution)

    def send_frame(self, rgb565_data: bytes) -> bool:
        """Send one RGB565 frame."""
        if not self._initialized:
            self.handshake()
        ScsiDevice._send_frame(self.device_path, rgb565_data, self.width, self.height)
        return True

    def close(self) -> None:
        """Mark as uninitialized (no persistent resources to release)."""
        self._initialized = False
        ScsiDevice._initialized_devices.discard(self.device_path)


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

            # Default resolution — actual resolution detected on handshake
            # via _init_device() -> poll byte[0] -> fbl_to_resolution().
            resolution = (320, 320)

            # SCSI poll byte[0] = resolution code = PM (matches USBLCD.exe).
            # Use it to resolve variant-specific button image.
            button_image = dev.button_image
            scsi_pm = _RESOLUTION_TO_PM.get(resolution)
            if scsi_pm is not None:
                from .device_hid import get_button_image
                resolved = get_button_image(scsi_pm, 0)
                if resolved:
                    button_image = resolved

            devices.append({
                'name': f"{dev.vendor_name} {dev.product_name}",
                'path': dev.scsi_device,
                'resolution': resolution,
                'vendor': dev.vendor_name,
                'product': dev.product_name,
                'model': dev.model,
                'button_image': button_image,
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

            # All LED devices share PID 0x8001 — probe via HID handshake
            # to discover the real model (AX120, PA120, LC1, etc.).
            if dev.implementation == 'hid_led':
                try:
                    from .device_led import PmRegistry, probe_led_model
                    info = probe_led_model(dev.vid, dev.pid,
                                           usb_path=dev.usb_path)
                    if info and info.model_name:
                        model = info.model_name
                        btn = PmRegistry.get_button_image(info.pm, info.sub_type)
                        if btn:
                            button_image = btn
                except Exception:
                    pass  # Fall back to registry default

            devices.append({
                'name': f"{dev.vendor_name} {dev.product_name}",
                'path': hid_path,
                'resolution': (0, 0),  # Unknown until HID handshake (PM->FBL->resolution)
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
        elif protocol == 'bulk':
            # Bulk USB devices — no SCSI path, use VID:PID
            bulk_path = f"bulk:{dev.vid:04x}:{dev.pid:04x}"

            # Resolution unknown until handshake; default from device registry
            resolution = (0, 0)

            devices.append({
                'name': f"{dev.vendor_name} {dev.product_name}",
                'path': bulk_path,
                'resolution': resolution,
                'vendor': dev.vendor_name,
                'product': dev.product_name,
                'model': dev.model,
                'button_image': dev.button_image,
                'vid': dev.vid,
                'pid': dev.pid,
                'protocol': 'bulk',
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
        if device_path not in ScsiDevice._initialized_devices:
            ScsiDevice._init_device(device_path)
            ScsiDevice._initialized_devices.add(device_path)

        ScsiDevice._send_frame(device_path, rgb565_data, width, height)
        return True
    except Exception as e:
        log.error("SCSI send failed (%s): %s", device_path, e)
        # Allow re-init on next attempt
        ScsiDevice._initialized_devices.discard(device_path)
        return False
