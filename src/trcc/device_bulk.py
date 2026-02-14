"""
Raw USB bulk device handler for USBLCDNew-type devices.

Handles devices with bInterfaceClass=255 (Vendor Specific) that use
raw USB bulk transfers instead of SCSI or HID.  Protocol reverse-engineered
from USBLCDNew.exe ThreadSendDeviceData (87AD:70DB GrandVision series).

Protocol:
  1. Handshake: write 64 bytes {0x12,0x34,0x56,0x78,...,byte[56]=0x01},
     read 1024 bytes.  resp[24]=PM, resp[36]=SUB.
  2. Frame send: 64-byte header + payload, bulk write.
     cmd=2 for JPEG (all PMs except 32), cmd=3 for raw RGB565 (PM=32).
     ZLP after payload as frame delimiter.
"""

from __future__ import annotations

import logging
import struct

from .core.models import HandshakeResult, fbl_to_resolution, pm_to_fbl

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

# PM values with explicit resolution overrides for bulk devices.
# All others default to FBL=72 → 480x480.
_BULK_KNOWN_PMS = {5, 7, 9, 10, 11, 12, 32, 64, 65}

# C# FormCZTVInit: myDeviceMode=2 (JPEG) for all USBLCDNew devices,
# except PM=32 which overrides to myDeviceMode=4 (RGB565, cmd=3).
_BULK_RGB565_PMS = {32}


def _bulk_resolution(pm: int, sub: int = 0) -> tuple[int, int]:
    """Map bulk device PM+SUB to (width, height).

    Uses the shared pm_to_fbl() + fbl_to_resolution() pipeline for
    known PM values.  Everything else defaults to 480x480 (FBL=72).
    """
    if pm in _BULK_KNOWN_PMS or (pm == 1 and sub in (48, 49)):
        fbl = pm_to_fbl(pm, sub)
        return fbl_to_resolution(fbl, pm)
    return (480, 480)


_HANDSHAKE_READ_SIZE = 1024
_HANDSHAKE_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 5000
_FRAME_HEADER_SIZE = 64
_WRITE_CHUNK_SIZE = 16 * 1024  # 16 KiB per USB bulk write


class BulkDevice:
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
        self.use_jpeg: bool = True  # C#: all bulk use JPEG except PM=32
        self._raw_handshake: bytes = b""

    def _open(self):
        """Find and claim the USB device."""
        import usb.core
        import usb.util

        dev = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        if dev is None:
            raise RuntimeError(f"USB device {self.vid:04x}:{self.pid:04x} not found")

        # Detach kernel driver before set_configuration() to avoid EBUSY.
        # Can't rely on get_active_configuration() — it throws if no config
        # is active yet.  Try interfaces 0-3 (covers all known devices).
        for i in range(4):
            try:
                if dev.is_kernel_driver_active(i):  # type: ignore[union-attr]
                    dev.detach_kernel_driver(i)  # type: ignore[union-attr]
                    log.debug("Detached kernel driver from interface %d", i)
            except (usb.core.USBError, NotImplementedError):
                pass

        dev.set_configuration()  # type: ignore[union-attr]
        cfg = dev.get_active_configuration()  # type: ignore[union-attr]

        # Prefer vendor-specific interface (bInterfaceClass=255)
        intf = None
        for candidate in cfg:
            if candidate.bInterfaceClass == 255:  # type: ignore[union-attr]
                intf = candidate
                break
        if intf is None:
            intf = cfg[(0, 0)]  # type: ignore[index]

        usb.util.claim_interface(dev, intf.bInterfaceNumber)  # type: ignore[union-attr]

        self._ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
            ),
        )
        self._ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
            ),
        )

        if self._ep_out is None or self._ep_in is None:
            raise RuntimeError("Could not find bulk IN/OUT endpoints")

        self._intf = intf.bInterfaceNumber  # type: ignore[union-attr]
        self._dev = dev
        log.info("Opened bulk device %04x:%04x (intf=%d, EP OUT=0x%02x, EP IN=0x%02x)",
                 self.vid, self.pid, self._intf,
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

        # C#: myDeviceMode=2 (JPEG) for all USBLCDNew, except PM=32 → mode 4 (RGB565)
        self.use_jpeg = self.pm not in _BULK_RGB565_PMS

        # Derive resolution from PM+SUB (from FormCZTVInit in FormCZTV.cs).
        # Bulk devices (87AD:70DB) get FBL=72 hardcoded by USBLCDNEW.exe,
        # then PM overrides FBL for certain device models.
        resolution = _bulk_resolution(self.pm, self.sub_type)
        if resolution:
            self.width, self.height = resolution

        log.info("Bulk handshake OK: PM=%d, SUB=%d, resolution=%s, jpeg=%s",
                 self.pm, self.sub_type, resolution, self.use_jpeg)

        return HandshakeResult(
            resolution=resolution,
            model_id=self.pm,
            raw_response=resp,
        )

    def send_frame(self, image_data: bytes) -> bool:
        """Send one frame via bulk write.

        C# protocol (FormCZTV.cs ImageToJpg / ImageTo565):
          - JPEG mode (cmd=2): all PMs except 32.  Payload is JPEG bytes.
          - RGB565 mode (cmd=3): PM=32 only.  Payload is raw RGB565 pixels.

        Header format (64 bytes):
          offset  0: magic  12 34 56 78
          offset  4: cmd    2 = JPEG, 3 = raw RGB565
          offset  8: width  (LE u32)
          offset 12: height (LE u32)
          offset 56: mode   2
          offset 60: payload length (LE u32)
        Followed by payload in 16 KiB chunks, then a ZLP delimiter.
        """
        if self._dev is None or self._ep_out is None:
            self.handshake()

        assert self._ep_out is not None

        data_size = len(image_data)
        cmd = 2 if self.use_jpeg else 3

        # Build 64-byte header
        header = bytearray(64)
        header[0:4] = _HANDSHAKE_PAYLOAD[0:4]           # magic 12 34 56 78
        struct.pack_into("<I", header, 4, cmd)            # cmd
        struct.pack_into("<I", header, 8, self.width)    # width
        struct.pack_into("<I", header, 12, self.height)  # height
        struct.pack_into("<I", header, 56, 2)            # mode
        struct.pack_into("<I", header, 60, data_size)    # payload length

        try:
            # Send header
            self._ep_out.write(bytes(header), timeout=_WRITE_TIMEOUT_MS)  # type: ignore[union-attr]

            # Send payload in chunks
            offset = 0
            while offset < data_size:
                chunk = image_data[offset:offset + _WRITE_CHUNK_SIZE]
                self._ep_out.write(chunk, timeout=_WRITE_TIMEOUT_MS)  # type: ignore[union-attr]
                offset += len(chunk)

            # ZLP frame delimiter
            self._ep_out.write(b"", timeout=_WRITE_TIMEOUT_MS)  # type: ignore[union-attr]

            log.debug("Bulk frame sent: %dx%d, cmd=%d, %d bytes",
                      self.width, self.height, cmd, data_size)
            return True
        except Exception:
            log.exception("Bulk frame send failed (cmd=%d, %d bytes)", cmd, data_size)
            return False

    def close(self) -> None:
        """Release USB device."""
        if self._dev is not None:
            import usb.util
            try:
                usb.util.release_interface(self._dev, self._intf)
            except Exception:
                pass
            usb.util.dispose_resources(self._dev)
            self._dev = None
            self._ep_out = None
            self._ep_in = None
            log.info("Bulk device closed")
