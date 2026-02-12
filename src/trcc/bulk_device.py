"""
Raw USB bulk device handler for USBLCDNew-type devices.

Handles devices with bInterfaceClass=255 (Vendor Specific) that use
raw USB bulk transfers instead of SCSI or HID.  Protocol reverse-engineered
from USBLCDNew.exe ThreadSendDeviceData (87AD:70DB GrandVision series).

Protocol:
  1. Handshake: write 64 bytes {0x12,0x34,0x56,0x78,...,byte[56]=0x01},
     read 1024 bytes.  resp[24]=PM, resp[36]=SUB.
  2. Frame send: 64-byte header + RGB565 pixel data, bulk write.
     ZLP if total size is 512-byte aligned.
"""

from __future__ import annotations

import logging
import struct

from .device_base import DeviceHandler, HandshakeResult

log = logging.getLogger(__name__)

# Handshake payload: 64 bytes from USBLCDNew ThreadSendDeviceData
_HANDSHAKE_PAYLOAD = bytes([
    0x12, 0x34, 0x56, 0x78, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 1, 0, 0, 0,
    0, 0, 0, 0,
])

_HANDSHAKE_READ_SIZE = 1024
_HANDSHAKE_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 1000
_FRAME_HEADER_SIZE = 64


class BulkDevice(DeviceHandler):
    """USB bulk device handler for USBLCDNew-type LCDs (87AD:70DB etc.).

    Uses pyusb for raw bulk endpoint I/O.  The kernel must not have
    claimed the interface (no usb-storage, no usbhid).
    """

    def __init__(self, vid: int, pid: int, usb_path: str = ""):
        self.vid = vid
        self.pid = pid
        self.usb_path = usb_path
        self._dev = None
        self._ep_out = None
        self._ep_in = None
        self.pm: int = 0
        self.sub_type: int = 0
        self.width: int = 0
        self.height: int = 0
        self._raw_handshake: bytes = b""

    def _open(self):
        """Find and claim the USB device."""
        import usb.core
        import usb.util

        dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)  # type: ignore[union-attr]
        if dev is None:
            raise RuntimeError(f"USB device {self.vid:04x}:{self.pid:04x} not found")

        # Detach kernel driver if attached
        for i in range(dev.get_active_configuration().bNumInterfaces):  # type: ignore[union-attr]
            if dev.is_kernel_driver_active(i):  # type: ignore[union-attr]
                dev.detach_kernel_driver(i)  # type: ignore[union-attr]
                log.debug("Detached kernel driver from interface %d", i)

        dev.set_configuration()  # type: ignore[union-attr]
        cfg = dev.get_active_configuration()  # type: ignore[union-attr]
        intf = cfg[(0, 0)]  # type: ignore[index]

        self._ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress
            ) == usb.util.ENDPOINT_OUT,
        )
        self._ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress
            ) == usb.util.ENDPOINT_IN,
        )

        if self._ep_out is None or self._ep_in is None:
            raise RuntimeError("Could not find bulk IN/OUT endpoints")

        self._dev = dev
        log.info("Opened bulk device %04x:%04x (EP OUT=0x%02x, EP IN=0x%02x)",
                 self.vid, self.pid,
                 self._ep_out.bEndpointAddress,  # type: ignore[union-attr]
                 self._ep_in.bEndpointAddress)  # type: ignore[union-attr]

    def handshake(self) -> HandshakeResult:
        """Send 64-byte handshake, read 1024-byte response.

        Extracts PM from resp[24], SUB from resp[36].
        """
        if self._dev is None:
            self._open()

        assert self._ep_out is not None
        assert self._ep_in is not None

        # Write handshake
        self._ep_out.write(_HANDSHAKE_PAYLOAD, timeout=_HANDSHAKE_TIMEOUT_MS)  # type: ignore[union-attr]
        log.debug("Handshake sent (%d bytes)", len(_HANDSHAKE_PAYLOAD))

        # Read response
        resp = bytes(self._ep_in.read(  # type: ignore[union-attr]
            _HANDSHAKE_READ_SIZE, timeout=_HANDSHAKE_TIMEOUT_MS
        ))
        self._raw_handshake = resp
        log.info("Handshake response: %d bytes", len(resp))
        log.debug("Response hex (first 64): %s",
                  " ".join(f"{b:02x}" for b in resp[:64]))

        # Validate: resp[24] must be non-zero (from CS code)
        if len(resp) < 41 or resp[24] == 0:
            log.warning("Handshake failed: resp[24]=%s (expected non-zero)",
                        resp[24] if len(resp) > 24 else "N/A")
            return HandshakeResult(raw_response=resp)

        # Extract PM and SUB (from USBLCDNew shared memory mapping)
        self.pm = resp[24]
        self.sub_type = resp[36]

        # Derive resolution from PM (same mapping as SCSI poll byte)
        res_map = {36: (240, 240), 50: (240, 320), 51: (320, 240),
                   100: (320, 320), 101: (480, 480)}
        resolution = res_map.get(self.pm)
        if resolution:
            self.width, self.height = resolution

        log.info("Bulk handshake OK: PM=%d, SUB=%d, resolution=%s",
                 self.pm, self.sub_type, resolution)

        return HandshakeResult(
            resolution=resolution,
            model_id=self.pm,
            raw_response=resp,
        )

    def send_frame(self, rgb565_data: bytes) -> bool:
        """Send one RGB565 frame via bulk write.

        Frame format (from USBLCDNew ThreadSendDeviceData):
          - 64-byte header (bytes[60:64] = data size as uint32 LE)
          - RGB565 pixel data
          - ZLP if total size is 512-byte aligned
        """
        if self._dev is None or self._ep_out is None:
            self.handshake()

        assert self._ep_out is not None

        data_size = len(rgb565_data)

        # Build 64-byte header
        header = bytearray(64)
        struct.pack_into("<I", header, 60, data_size)

        payload = bytes(header) + rgb565_data
        total = len(payload)

        try:
            self._ep_out.write(payload, timeout=_WRITE_TIMEOUT_MS)  # type: ignore[union-attr]

            # Send ZLP if 512-byte aligned (from CS code)
            if total % 512 == 0:
                self._ep_out.write(b"", timeout=_WRITE_TIMEOUT_MS)  # type: ignore[union-attr]

            return True
        except Exception:
            log.exception("Bulk frame send failed (%d bytes)", total)
            return False

    def close(self) -> None:
        """Release USB device."""
        if self._dev is not None:
            import usb.util
            usb.util.dispose_resources(self._dev)
            self._dev = None
            self._ep_out = None
            self._ep_in = None
            log.info("Bulk device closed")
