#!/usr/bin/env python3
"""
HID USB protocol layer for Type 2 ("H") and Type 3 ("ALi") LCD devices.

These devices use USB bulk transfers instead of SCSI.  Protocol details
reverse-engineered from the decompiled USBLCDNEW.exe (C# / LibUsbDotNet).

Type 2 — VID 0x0416, PID 0x5302  ("H" variant, DA/DB/DC/DD handshake)
Type 3 — VID 0x0418, PID 0x5303/0x5304  ("ALi" variant, F5 prefix)

The ``UsbTransport`` ABC abstracts the raw USB I/O so that:
  • Tests can inject a mock transport (no real hardware needed).
  • ``PyUsbTransport`` provides real USB via pyusb (libusb backend).
  • ``HidApiTransport`` provides an alternative via HIDAPI.

Linux dependencies (install one):
  • pyusb:  ``pip install pyusb``  (needs libusb1 — ``apt install libusb-1.0-0``)
  • hidapi: ``pip install hidapi`` (needs libhidapi — ``apt install libhidapi-dev``)
"""

import logging
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Set

import usb.core
import usb.util

from .core.models import (
    HandshakeResult,
    fbl_to_resolution,
    pm_to_fbl,
)

# hidapi is optional ([hid] extra)
try:
    import hid as hidapi  # pyright: ignore[reportMissingImports]
    HIDAPI_AVAILABLE = True
except ImportError:
    HIDAPI_AVAILABLE = False

log = logging.getLogger(__name__)

# pyusb is a hard dep — always True, exported for device_factory.transport_info()
PYUSB_AVAILABLE = True


# =========================================================================
# Constants (from USBLCDNEW.decompiled.cs)
# =========================================================================

# USB IDs (from UCDevice.cs: UsbHidDevice constructor calls)
TYPE2_VID = 0x0416
TYPE2_PID = 0x5302  # device2: UsbHidDevice(1046, 21250)

TYPE3_VID = 0x0418
TYPE3_PID = 0x5303  # device3: UsbHidDevice(1048, 21251); also 0x5304 = device4

# Endpoint addresses (LibUsbDotNet enum values)
EP_READ_01 = 0x81   # ReadEndpointID.Ep01
EP_WRITE_01 = 0x01  # WriteEndpointID.Ep01
EP_WRITE_02 = 0x02  # WriteEndpointID.Ep02

# Type 2 magic bytes
TYPE2_MAGIC = bytes([0xDA, 0xDB, 0xDC, 0xDD])

# Type 3 command prefix
TYPE3_CMD_PREFIX = bytes([0xF5, 0x00, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8])
TYPE3_FRAME_PREFIX = bytes([0xF5, 0x01, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8])

# Buffer / packet sizes
TYPE2_INIT_SIZE = 512
TYPE2_RESPONSE_SIZE = 512
TYPE3_INIT_SIZE = 1040   # 16-byte header + 1024 zeros
TYPE3_RESPONSE_SIZE = 1024
TYPE3_DATA_SIZE = 204800  # 320*320*2, fixed payload size
TYPE3_FRAME_TOTAL = 204816  # 16-byte prefix + 204800 data
TYPE3_ACK_SIZE = 16

# Alignment
USB_BULK_ALIGNMENT = 512

# Default timeout (ms) — for frame send / normal I/O
DEFAULT_TIMEOUT_MS = 100

# Handshake timeout (ms) — much longer than frame-send.
# Windows uses async HID API with no explicit timeout; our synchronous read
# needs a generous window.  UCDevice.cs retries at 200ms then 3s intervals.
HANDSHAKE_TIMEOUT_MS = 5000

# Handshake retry settings (UCDevice.cs Timer_event: 3 scans with increasing delay)
HANDSHAKE_MAX_RETRIES = 3
HANDSHAKE_RETRY_DELAY_S = 0.500

# Timing delays from C# (Thread.Sleep calls in USBLCDNEW.exe)
DELAY_PRE_INIT_S = 0.050    # Sleep(50)  — before sending init packet
DELAY_POST_INIT_S = 0.200   # Sleep(200) — after async init write+read
DELAY_FRAME_TYPE2_S = 0.001  # Sleep(1)  — between Type 2 frames
DELAY_FRAME_TYPE3_S = 0.0    # Type 3 has no inter-frame delay (write+ACK is blocking)

# USB configuration values from C# (SetConfiguration / ClaimInterface)
USB_CONFIGURATION = 1
USB_INTERFACE = 0



# _PM_TO_FBL_OVERRIDES, fbl_to_resolution, pm_to_fbl imported from core.models

# Unified device → button image map (from UCDevice.cs ADDUserButton).
# Outer key: HID PM byte (0-255) or SCSI VID (>255).
# Inner key: HID SUB byte or SCSI PID.  None = default when sub/pid not matched.
DEVICE_BUTTON_IMAGE: dict[int, dict[Optional[int], str]] = {
    # -- HID Vision/RGB devices (case 257, PM + SUB) --
    1:   {0: 'A1GRAND VISION', 1: 'A1GRAND VISION',
          48: 'A1LM22', 49: 'A1LF14', None: 'A1GRAND VISION'},
    3:   {None: 'A1CORE VISION'},
    4:   {1: 'A1HYPER VISION', 2: 'A1RP130 VISION', 3: 'A1LM16SE'},
    5:   {None: 'A1Mjolnir VISION'},
    6:   {1: 'FROZEN WARFRAME Ultra', 2: 'A1FROZEN VISION V2'},
    7:   {1: 'A1Stream Vision', 2: 'A1Mjolnir VISION PRO'},
    9:   {None: 'A1LC2JD'},
    10:  {5: 'A1LF16', 6: 'A1LF18', None: 'A1LC3'},
    11:  {None: 'A1LF19'},
    12:  {None: 'A1LF167'},
    # -- HID LCD devices (case 2 + case 257 merged, PM + SUB) --
    32:  {0: 'A1ELITE VISION', 1: 'A1FROZEN WARFRAME PRO',
          None: 'A1ELITE VISION'},
    36:  {None: 'A1AS120 VISION'},
    50:  {None: 'A1FROZEN WARFRAME'},
    51:  {None: 'A1FROZEN WARFRAME'},
    52:  {None: 'A1BA120 VISION'},
    53:  {None: 'A1BA120 VISION'},
    54:  {None: 'A1LC5'},
    58:  {0: 'A1FROZEN WARFRAME SE', None: 'A1LM26'},
    64:  {0: 'A1FROZEN WARFRAME PRO', 1: 'A1LM22', 2: 'A1LM27'},
    65:  {0: 'A1ELITE VISION', 1: 'A1LF14'},
    100: {0: 'A1FROZEN WARFRAME PRO', 1: 'A1LM22',
          None: 'A1FROZEN WARFRAME PRO'},
    101: {0: 'A1ELITE VISION', 1: 'A1LF14', None: 'A1ELITE VISION'},
    128: {None: 'A1LM24'},
    # -- SCSI devices (VID → {PID: image}) --
    0x87CD: {0x70DB: 'A1CZTV', None: 'A1CZTV'},
    0x87AD: {0x70DB: 'A1GRAND VISION', None: 'A1GRAND VISION'},
    0x0402: {0x3922: 'A1FROZEN WARFRAME', None: 'A1FROZEN WARFRAME'},
    0x0416: {0x5406: 'A1CZTV', None: 'A1CZTV'},
}

# Keep old name as alias for backward compat
PM_TO_BUTTON_IMAGE = DEVICE_BUTTON_IMAGE


def get_button_image(key: int, sub: int = 0) -> Optional[str]:
    """Resolve device button image from PM+SUB (HID) or VID+PID (SCSI).

    Args:
        key: PM byte (0-255) for HID devices, or VID for SCSI devices.
        sub: SUB byte for HID devices, or PID for SCSI devices.

    Returns image name, or None if unknown.
    """
    sub_map = DEVICE_BUTTON_IMAGE.get(key)
    if sub_map is None:
        return None
    if sub in sub_map:
        return sub_map[sub]
    return sub_map.get(None)


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class HidHandshakeInfo(HandshakeResult):
    """HID-specific handshake info (extends HandshakeResult)."""
    device_type: int = 0      # 2 or 3
    mode_byte_1: int = 0     # Type 2: resp[5] (PM), Type 3: resp[0]-1
    mode_byte_2: int = 0     # Type 2: resp[4] (SUB), Type 3: 0
    fbl: Optional[int] = None  # FBL code resolved from PM/SUB


# =========================================================================
# Abstract USB transport
# =========================================================================

class UsbTransport(ABC):
    """Abstract USB bulk transport — mockable for testing."""

    @abstractmethod
    def open(self) -> None:
        """Open the USB device and claim interface."""

    @abstractmethod
    def close(self) -> None:
        """Release interface and close."""

    @abstractmethod
    def write(self, endpoint: int, data: bytes, timeout: int = DEFAULT_TIMEOUT_MS) -> int:
        """Bulk write to endpoint.  Returns bytes transferred."""

    @abstractmethod
    def read(self, endpoint: int, length: int, timeout: int = DEFAULT_TIMEOUT_MS) -> bytes:
        """Bulk read from endpoint.  Returns data read."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the device is currently open."""


# =========================================================================
# Helpers
# =========================================================================

def _ceil_to_512(n: int) -> int:
    """Round *n* up to the next multiple of 512 (or *n* itself if aligned).

    Matches the C# expression::

        n / 512 * 512 + ((n % 512 != 0) ? 512 : 0)
    """
    return (n // USB_BULK_ALIGNMENT) * USB_BULK_ALIGNMENT + (
        USB_BULK_ALIGNMENT if n % USB_BULK_ALIGNMENT else 0
    )


# =========================================================================
# HID device base class
# =========================================================================

class HidDevice:
    """Base for HID LCD device handlers (Type 2 and Type 3).

    Provides shared init state and the handshake template:
    build_init_packet → delay → write → delay → read → validate → parse.
    Subclasses override the static packet/parse methods for their protocol.
    """

    def __init__(self, transport: UsbTransport):
        self.transport = transport
        self._initialized = False
        self.device_info: Optional[HidHandshakeInfo] = None

    @staticmethod
    @abstractmethod
    def build_init_packet() -> bytes: ...

    @staticmethod
    @abstractmethod
    def validate_response(resp: bytes) -> bool: ...

    @staticmethod
    @abstractmethod
    def parse_device_info(resp: bytes) -> HidHandshakeInfo: ...

    @abstractmethod
    def _response_size(self) -> int:
        """Expected response size for this device type."""
        ...

    def handshake(self) -> HidHandshakeInfo:
        """Perform the init handshake with retry (template method).

        Subclasses provide the packet format via build_init_packet(),
        validate_response(), parse_device_info(), and _response_size().

        Retries up to HANDSHAKE_MAX_RETRIES times (UCDevice.cs Timer_event
        retries at 200ms → 3s intervals).
        """
        init_pkt = self.build_init_packet()
        last_err: Optional[Exception] = None

        for attempt in range(1, HANDSHAKE_MAX_RETRIES + 1):
            try:
                time.sleep(DELAY_PRE_INIT_S)
                self.transport.write(EP_WRITE_02, init_pkt, HANDSHAKE_TIMEOUT_MS)
                time.sleep(DELAY_POST_INIT_S)

                resp = self.transport.read(
                    EP_READ_01, self._response_size(), HANDSHAKE_TIMEOUT_MS,
                )

                if not self.validate_response(resp):
                    log.warning(
                        "%s handshake attempt %d/%d: invalid response "
                        "(len=%d, first 16 bytes: %s)",
                        type(self).__name__, attempt, HANDSHAKE_MAX_RETRIES,
                        len(resp), resp[:16].hex() if resp else "empty",
                    )
                    last_err = RuntimeError(
                        f"{type(self).__name__} handshake failed: invalid response"
                    )
                    time.sleep(HANDSHAKE_RETRY_DELAY_S)
                    continue

                self.device_info = self.parse_device_info(resp)
                self._initialized = True
                return self.device_info

            except Exception as e:
                log.warning(
                    "%s handshake attempt %d/%d failed: %s",
                    type(self).__name__, attempt, HANDSHAKE_MAX_RETRIES, e,
                )
                last_err = e
                if attempt < HANDSHAKE_MAX_RETRIES:
                    time.sleep(HANDSHAKE_RETRY_DELAY_S)

        raise last_err or RuntimeError(
            f"{type(self).__name__} handshake failed after {HANDSHAKE_MAX_RETRIES} attempts"
        )

    @abstractmethod
    def send_frame(self, image_data: bytes) -> bool:
        """Send one image frame to the device."""
        ...

    def close(self) -> None:
        """Release resources (transport is managed externally)."""
        self._initialized = False
        self.device_info = None


# =========================================================================
# Type 2 — "H" variant  (VID 0x0416, PID 0x5302)
# =========================================================================

class HidDeviceType2(HidDevice):
    """Protocol handler for Type 2 HID LCD devices.

    Uses Ep01 for reads, Ep02 for writes.
    Image data is sent with a 20-byte header, 512-byte aligned.
    """

    # -- Init packet ---------------------------------------------------

    @staticmethod
    def build_init_packet() -> bytes:
        """Build the 512-byte handshake packet.

        Layout (from C#)::

            [0xDA, 0xDB, 0xDC, 0xDD,   # magic
             0,0,0,0, 0,0,0,0,          # reserved
             0x01, 0,0,0,               # command = 1
             0,0,0,0]                   # reserved
            + 492 zero bytes            # padding to 512
        """
        header = (
            TYPE2_MAGIC
            + b'\x00' * 8
            + b'\x01\x00\x00\x00'
            + b'\x00' * 4
        )
        return header + b'\x00' * (TYPE2_INIT_SIZE - len(header))

    # -- Response parsing -----------------------------------------------

    @staticmethod
    def validate_response(resp: bytes) -> bool:
        """Check the handshake response matches expected pattern.

        Conditions (from UCDevice.cs DeviceDataReceived2)::

            data[1]==0xDA && data[2]==0xDB && data[3]==0xDC && data[4]==0xDD
            && data[13]==1

        Windows offsets are +1 due to HID Report ID prefix at data[0].
        Raw USB (PyUSB) equivalents: resp[0:4]==magic && resp[12]==1.

        Note: Windows does NOT require data[17]==0x10 for validation —
        that byte is only used for serial extraction (falls back to
        device path if absent).
        """
        if len(resp) < 20:
            return False
        return (
            resp[0:4] == TYPE2_MAGIC
            and resp[12] == 0x01
        )

    @staticmethod
    def parse_device_info(resp: bytes) -> HidHandshakeInfo:
        """Extract device info from a validated handshake response.

        From UCDevice.cs AddhidDeviceList (accounting for Report ID offset)::

            PM  = data[6]  → raw resp[5]   (product mode byte)
            SUB = data[5]  → raw resp[4]   (sub-variant byte)

        Serial is at data[21:37] → raw resp[20:36] when data[17]==0x10.

        PM+SUB → FBL → resolution via pm_to_fbl() and fbl_to_resolution().
        """
        pm = resp[5]
        sub = resp[4]
        has_serial = len(resp) > 36 and resp[16] == 0x10
        serial = resp[20:36].hex().upper() if has_serial else ""
        fbl = pm_to_fbl(pm, sub)
        resolution = fbl_to_resolution(fbl, pm)
        return HidHandshakeInfo(
            resolution=resolution,
            model_id=pm,
            serial=serial,
            raw_response=bytes(resp[:64]),
            device_type=2,
            mode_byte_1=pm,
            mode_byte_2=sub,
            fbl=fbl,
        )

    def _response_size(self) -> int:
        return TYPE2_RESPONSE_SIZE

    # -- Frame send -------------------------------------------------------

    @staticmethod
    def build_frame_packet(image_data: bytes) -> bytes:
        """Build a frame packet from raw image data.

        The 20-byte header is structured so that bytes[16:20] contain
        the image data length as a little-endian uint32.  The rest of
        the header is zeros (populated by the TRCC GUI in Windows via
        shared memory; we only need the size field for the device to
        parse it).

        The total transfer length is rounded up to the next 512-byte
        boundary (C#: ``num2 / 512 * 512 + (num2 % 512 != 0 ? 512 : 0)``).

        Returns the padded packet ready for USB bulk write.
        """
        data_len = len(image_data)
        # 20-byte header: 16 zero bytes + LE uint32 image size
        header = b'\x00' * 16 + struct.pack('<I', data_len)
        raw = header + image_data
        padded_len = _ceil_to_512(len(raw))
        return raw.ljust(padded_len, b'\x00')

    def send_frame(self, image_data: bytes) -> bool:
        """Send one image frame to the device.

        Matches C# frame loop: synchronous Transfer() to Ep02,
        then Thread.Sleep(1).

        Args:
            image_data: Raw image bytes (JPEG or other format the
                        device expects).

        Returns:
            True if the transfer succeeded.

        Raises:
            RuntimeError: If device not initialized.
        """
        if not self._initialized:
            raise RuntimeError("Type 2 device not initialized — call handshake() first")

        packet = self.build_frame_packet(image_data)
        transferred = self.transport.write(EP_WRITE_02, packet, DEFAULT_TIMEOUT_MS)

        # C#: Thread.Sleep(1) after frame transfer
        time.sleep(DELAY_FRAME_TYPE2_S)

        return transferred > 0


# =========================================================================
# Type 3 — "ALi" variant  (VID 0x0418, PID 0x5303/0x5304)
# =========================================================================

class HidDeviceType3(HidDevice):
    """Protocol handler for Type 3 HID LCD devices.

    Uses Ep01 for reads, Ep02 for writes.
    Fixed-size 204816-byte frame writes with 16-byte ACK read.
    """

    # -- Init packet ---------------------------------------------------

    @staticmethod
    def build_init_packet() -> bytes:
        """Build the 1040-byte handshake packet.

        Layout (from C#)::

            [0xF5, 0x00, 0x01, 0x00,
             0xBC, 0xFF, 0xB6, 0xC8,
             0x00, 0x00, 0x00, 0x00,
             0x00, 0x04, 0x00, 0x00]   # 16-byte prefix
            + 1024 zero bytes          # padding
        """
        prefix = (
            TYPE3_CMD_PREFIX
            + b'\x00\x00\x00\x00'
            + b'\x00\x04\x00\x00'
        )
        return prefix + b'\x00' * 1024

    # -- Response parsing -----------------------------------------------

    @staticmethod
    def validate_response(resp: bytes) -> bool:
        """Check the handshake response.

        Condition (from C#)::

            resp[0] == 101 (0x65) || resp[0] == 102 (0x66)
        """
        if len(resp) < 14:
            return False
        return resp[0] in (0x65, 0x66)

    @staticmethod
    def parse_device_info(resp: bytes) -> HidHandshakeInfo:
        """Extract device info from a validated handshake response.

        From USBLCDNEW_PROTOCOL.md::

            fbl = resp[0] - 1  (0x65→100, 0x66→101)
            serial = hex string of resp[10:14]
        """
        serial = resp[10:14].hex().upper()
        fbl = resp[0] - 1
        resolution = fbl_to_resolution(fbl)
        return HidHandshakeInfo(
            resolution=resolution,
            model_id=fbl,
            serial=serial,
            raw_response=bytes(resp[:64]),
            device_type=3,
            mode_byte_1=fbl,
            fbl=fbl,
        )

    def _response_size(self) -> int:
        return TYPE3_RESPONSE_SIZE

    # -- Frame send -------------------------------------------------------

    @staticmethod
    def build_frame_packet(image_data: bytes) -> bytes:
        """Build a frame packet from raw image data.

        Matches C# frame construction::

            first = [0xF5,0x01,0x01,0x00, 0xBC,0xFF,0xB6,0xC8,
                     0,0,0,0, 0,0x20,0x03,0]    // 16-byte prefix
            first = first.Concat(array2).ToArray()  // + 204800 data

        Data is padded/truncated to exactly 204800 bytes.
        Total packet = 204816 bytes.
        """
        prefix = (
            TYPE3_FRAME_PREFIX
            + b'\x00\x00\x00\x00'
            + struct.pack('<I', TYPE3_DATA_SIZE)
        )
        # Pad or truncate image data to fixed size
        if len(image_data) < TYPE3_DATA_SIZE:
            padded = image_data + b'\x00' * (TYPE3_DATA_SIZE - len(image_data))
        else:
            padded = image_data[:TYPE3_DATA_SIZE]
        return prefix + padded

    def send_frame(self, image_data: bytes) -> bool:
        """Send one image frame and read ACK.

        Matches C# frame loop::

            usbEndpointWriter.Write(first, 100, out transferLength);  // sync write
            usbEndpointReader.Read(first, 0, 16, 100, out transferLength2);  // sync read ACK

        Args:
            image_data: Raw image bytes.

        Returns:
            True if the transfer and ACK succeeded.

        Raises:
            RuntimeError: If device not initialized.
        """
        if not self._initialized:
            raise RuntimeError("Type 3 device not initialized — call handshake() first")

        packet = self.build_frame_packet(image_data)
        transferred = self.transport.write(EP_WRITE_02, packet, DEFAULT_TIMEOUT_MS)
        if transferred == 0:
            return False

        # C#: usbEndpointReader.Read(first, 0, 16, 100, out transferLength2)
        ack = self.transport.read(EP_READ_01, TYPE3_ACK_SIZE, DEFAULT_TIMEOUT_MS)
        return len(ack) > 0


# =========================================================================
# Public API  (mirrors scsi_device.send_image_to_device)
# =========================================================================

# Track initialized devices by transport identity
_initialized_transports: Set[int] = set()
_device_handlers: dict = {}


def send_image_to_hid_device(
    transport: UsbTransport,
    image_data: bytes,
    device_type: int,
) -> bool:
    """Send image data to a HID LCD device.

    Performs handshake on first call per transport, then sends frames.

    Args:
        transport: Open USB transport to the device.
        image_data: Raw image bytes (JPEG or device-native format).
        device_type: 2 for "H" variant, 3 for "ALi" variant.

    Returns:
        True if the send succeeded.
    """
    transport_id = id(transport)

    try:
        if transport_id not in _initialized_transports:
            if device_type == 2:
                handler = HidDeviceType2(transport)
            elif device_type == 3:
                handler = HidDeviceType3(transport)
            else:
                raise ValueError(f"Unknown HID device type: {device_type}")

            handler.handshake()
            _device_handlers[transport_id] = handler
            _initialized_transports.add(transport_id)

        handler = _device_handlers[transport_id]
        return handler.send_frame(image_data)

    except Exception as e:
        print(f"[!] HID send failed: {e}")
        _initialized_transports.discard(transport_id)
        _device_handlers.pop(transport_id, None)
        return False


# =========================================================================
# Real transport: PyUSB  (libusb backend)
# =========================================================================
# Matches C# LibUsbDotNet flow:
#   UsbDevice.OpenUsbDevice(finder)
#   SetConfiguration(1)
#   ClaimInterface(0)
#   OpenEndpointReader(Ep01) / OpenEndpointWriter(Ep02)
#   ...
#   ReleaseInterface(0)
#   Close()

class PyUsbTransport(UsbTransport):
    """Real USB transport using pyusb (libusb backend).

    Follows the exact C# LibUsbDotNet sequence:
    1. Find device by VID/PID
    2. SetConfiguration(1)
    3. ClaimInterface(0)
    4. Bulk read/write to endpoints

    Requires: ``pip install pyusb`` + ``apt install libusb-1.0-0``
    """

    def __init__(self, vid: int, pid: int, serial: Optional[str] = None):
        self._vid = vid
        self._pid = pid
        self._serial = serial
        self._device = None
        self._is_open = False
        # Auto-detected endpoints (populated on open)
        self._ep_out: Optional[int] = None
        self._ep_in: Optional[int] = None

    def open(self) -> None:
        """Find USB device, claim interface, and auto-detect endpoints.

        C# equivalent::

            UsbDeviceFinder finder = new UsbDeviceFinder(vid, pid, serial);
            usbDevice = UsbDevice.OpenUsbDevice(finder);
            usbDevice.SetConfiguration(1);
            usbDevice.ClaimInterface(0);
        """
        kwargs: dict[str, Any] = {'idVendor': self._vid, 'idProduct': self._pid}
        if self._serial:
            kwargs['serial_number'] = self._serial

        self._device = usb.core.find(**kwargs)  # type: ignore[union-attr]
        if self._device is None:
            raise RuntimeError(
                f"USB device not found: VID={self._vid:#06x} PID={self._pid:#06x}"
            )

        # Detach kernel driver if active (Linux-specific, matches C# ClaimInterface)
        try:
            if self._device.is_kernel_driver_active(USB_INTERFACE):  # type: ignore[union-attr]
                self._device.detach_kernel_driver(USB_INTERFACE)  # type: ignore[union-attr]
                log.debug("Detached kernel driver from interface %d", USB_INTERFACE)
        except Exception as e:
            log.debug("Kernel driver detach: %s", e)

        # C#: SetConfiguration(1), ClaimInterface(0)
        self._device.set_configuration(USB_CONFIGURATION)  # type: ignore[union-attr]
        usb.util.claim_interface(self._device, USB_INTERFACE)  # type: ignore[union-attr]
        self._is_open = True

        # Auto-detect endpoints from device descriptor
        self._detect_endpoints()

    def close(self) -> None:
        """Release interface and close.

        C# equivalent::

            usbDevice.ReleaseInterface(0);
            usbDevice.Close();
            UsbDevice.Exit();
        """
        if self._device is not None:
            try:
                usb.util.release_interface(self._device, USB_INTERFACE)  # type: ignore[union-attr]
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self._device)  # type: ignore[union-attr]
            except Exception:
                pass
            self._device = None
        self._is_open = False

    def _detect_endpoints(self) -> None:
        """Auto-detect IN/OUT endpoint addresses from the device descriptor.

        Some HID devices have EP 0x01 OUT instead of the expected EP 0x02 OUT.
        We enumerate the actual endpoints so writes go to the right address.
        """
        try:
            cfg = self._device.get_active_configuration()  # type: ignore[union-attr]
            intf = cfg[(USB_INTERFACE, 0)]  # type: ignore[index]
            for ep in intf:
                direction = usb.util.endpoint_direction(ep.bEndpointAddress)  # type: ignore[union-attr]
                if direction == usb.util.ENDPOINT_OUT and self._ep_out is None:  # type: ignore[union-attr]
                    self._ep_out = ep.bEndpointAddress
                elif direction == usb.util.ENDPOINT_IN and self._ep_in is None:  # type: ignore[union-attr]
                    self._ep_in = ep.bEndpointAddress
            log.debug(
                "Auto-detected endpoints: OUT=0x%02x IN=0x%02x",
                self._ep_out or 0, self._ep_in or 0,
            )
        except Exception as e:
            log.debug("Endpoint auto-detection failed: %s", e)

    def write(self, endpoint: int, data: bytes, timeout: int = DEFAULT_TIMEOUT_MS) -> int:
        """Bulk write — uses auto-detected OUT endpoint when available.

        C# equivalent::

            usbEndpointWriter.Transfer(data, 0, length, timeout, out transferred);
        """
        if not self._is_open or self._device is None:
            raise RuntimeError("Transport not open")
        # Use auto-detected OUT endpoint, fall back to caller's hint
        ep = self._ep_out if self._ep_out is not None else endpoint
        return self._device.write(ep, data, timeout=timeout)  # type: ignore[union-attr]

    def read(self, endpoint: int, length: int, timeout: int = DEFAULT_TIMEOUT_MS) -> bytes:
        """Bulk read — uses auto-detected IN endpoint when available.

        C# equivalent::

            usbEndpointReader.Read(buffer, 0, length, timeout, out transferred);
        """
        if not self._is_open or self._device is None:
            raise RuntimeError("Transport not open")
        # Use auto-detected IN endpoint, fall back to caller's hint
        ep = self._ep_in if self._ep_in is not None else endpoint
        data = self._device.read(ep, length, timeout=timeout)  # type: ignore[union-attr]
        return bytes(data)

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def ep_out(self) -> Optional[int]:
        """Auto-detected OUT endpoint address, or None."""
        return self._ep_out

    @property
    def ep_in(self) -> Optional[int]:
        """Auto-detected IN endpoint address, or None."""
        return self._ep_in

    @property
    def device(self) -> Any:
        """Raw pyusb device handle (for diagnostics)."""
        return self._device

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


# =========================================================================
# Real transport: HIDAPI
# =========================================================================
# Alternative backend for devices that also expose an HID interface.
# Some USB LCD devices enumerate as HID — HIDAPI can access them
# without needing root or udev rules on some distros.

class HidApiTransport(UsbTransport):
    """USB transport using HIDAPI (hidapi library).

    This is an alternative to PyUSB for devices that expose HID
    interfaces.  HIDAPI uses the OS HID driver, which may not require
    root access.

    Note: HIDAPI read/write are report-based (max 64 bytes per
    report for interrupt endpoints).  For bulk transfers > 64 bytes,
    PyUsbTransport is preferred.  This transport splits large writes
    into report-sized chunks.

    Requires: ``pip install hidapi`` + ``apt install libhidapi-dev``
    """

    def __init__(self, vid: int, pid: int, serial: Optional[str] = None):
        if not HIDAPI_AVAILABLE:
            raise ImportError(
                "hidapi is not installed. Install with: pip install hidapi\n"
                "Also need libhidapi: apt install libhidapi-dev (Debian/Ubuntu) "
                "or dnf install hidapi-devel (Fedora)"
            )
        self._vid = vid
        self._pid = pid
        self._serial = serial
        self._device = None
        self._is_open = False

    def open(self) -> None:
        """Open HID device by VID/PID."""
        kwargs: dict[str, Any] = {'vid': self._vid, 'pid': self._pid}
        if self._serial:
            kwargs['serial'] = self._serial
        # hidapi 0.14 uses Device (uppercase), 0.15+ uses device (lowercase)
        DeviceClass = getattr(hidapi, 'device', None) or getattr(hidapi, 'Device', None)
        if DeviceClass is None:
            raise ImportError("hidapi module has neither 'device' nor 'Device' class")
        self._device = DeviceClass(**kwargs)
        self._device.nonblocking = 0  # blocking reads
        self._is_open = True

    def close(self) -> None:
        """Close HID device."""
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
        self._is_open = False

    def write(self, endpoint: int, data: bytes, timeout: int = DEFAULT_TIMEOUT_MS) -> int:
        """Write data via HID output report.

        HIDAPI write() prepends a report ID byte (0x00 for default).
        We send the data with report ID 0.

        Note: endpoint parameter is ignored — HIDAPI routes to the
        device's single OUT endpoint.
        """
        if not self._is_open or self._device is None:
            raise RuntimeError("Transport not open")
        # HIDAPI expects report ID as first byte
        report = bytes([0x00]) + data
        return self._device.write(report)

    def read(self, endpoint: int, length: int, timeout: int = DEFAULT_TIMEOUT_MS) -> bytes:
        """Read data via HID input report.

        Note: endpoint parameter is ignored — HIDAPI routes to the
        device's single IN endpoint.
        """
        if not self._is_open or self._device is None:
            raise RuntimeError("Transport not open")
        data = self._device.read(length, timeout)
        return bytes(data) if data else b''

    @property
    def is_open(self) -> bool:
        return self._is_open

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


# =========================================================================
# Device discovery helper
# =========================================================================

def find_hid_devices() -> list:
    """Scan for Type 2 and Type 3 HID LCD devices.

    Tries pyusb first, falls back to hidapi enumeration.

    Returns:
        List of dicts with keys: vid, pid, device_type, serial, backend
    """
    devices = []

    known = [
        (TYPE2_VID, TYPE2_PID, 2),
        (TYPE3_VID, TYPE3_PID, 3),
    ]

    for vid, pid, dtype in known:
        found = usb.core.find(find_all=True, idVendor=vid, idProduct=pid)
        for dev in found or []:
            serial_idx = getattr(dev, 'iSerialNumber', 0)
            serial = usb.util.get_string(dev, serial_idx) if serial_idx else ""
            devices.append({
                'vid': vid,
                'pid': pid,
                'device_type': dtype,
                'serial': serial or "",
                'backend': 'pyusb',
            })

    return devices
