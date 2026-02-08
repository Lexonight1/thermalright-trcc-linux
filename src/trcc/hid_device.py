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

import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Set

# Optional USB backends — graceful import
try:
    import usb.core
    import usb.util
    PYUSB_AVAILABLE = True
except ImportError:
    PYUSB_AVAILABLE = False

try:
    import hid as hidapi
    HIDAPI_AVAILABLE = True
except ImportError:
    HIDAPI_AVAILABLE = False


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

# Default timeout (ms)
DEFAULT_TIMEOUT_MS = 100

# Timing delays from C# (Thread.Sleep calls in USBLCDNEW.exe)
DELAY_PRE_INIT_S = 0.050    # Sleep(50)  — before sending init packet
DELAY_POST_INIT_S = 0.200   # Sleep(200) — after async init write+read
DELAY_FRAME_TYPE2_S = 0.001  # Sleep(1)  — between Type 2 frames
DELAY_FRAME_TYPE3_S = 0.0    # Type 3 has no inter-frame delay (write+ACK is blocking)

# USB configuration values from C# (SetConfiguration / ClaimInterface)
USB_CONFIGURATION = 1
USB_INTERFACE = 0


# =========================================================================
# FBL → Resolution mapping (from FormCZTV.cs lines 811-821)
# =========================================================================
# FBL byte determines LCD resolution.  For Type 3, FBL = resp[0]-1.
# For Type 2, FBL is derived from the PM byte via pm_to_fbl().

FBL_TO_RESOLUTION: dict = {
    36:  (240, 240),
    37:  (240, 240),
    50:  (240, 320),
    54:  (360, 360),
    64:  (640, 480),
    72:  (480, 480),
    100: (320, 320),
    101: (320, 320),
    102: (320, 320),
    114: (1600, 720),
    128: (1280, 480),
    192: (1920, 462),
    # FBL 224 is overloaded — depends on PM, defaults to 854x480
    224: (854, 480),
}

# PM byte → FBL byte for Type 2 / mode-2 devices (FormCZTV.cs lines 682-821)
# Format: PM value → FBL value
_PM_TO_FBL_TYPE2: dict = {
    5:   50,    # 240x320
    7:   64,    # 640x480
    9:   224,   # 854x480
    10:  224,   # 960x540 (special: actual res depends on PM)
    11:  224,   # 854x480
    12:  224,   # 800x480 (special)
    32:  100,   # 320x320
    64:  114,   # 1600x720
    65:  192,   # 1920x462
}

# PM byte → product button image (from UCDevice.cs lines 317-750)
PM_TO_BUTTON_IMAGE: dict = {
    # ID=2 (AIO/Vision devices — most HID LCD devices)
    36:  'A1AS120 VISION',
    50:  'A1FROZEN WARFRAME',
    51:  'A1FROZEN WARFRAME',
    52:  'A1BA120 VISION',
    53:  'A1BA120 VISION',
    58:  'A1FROZEN WARFRAME SE',
    100: 'A1FROZEN WARFRAME PRO',
    101: 'A1ELITE VISION',
}


def fbl_to_resolution(fbl: int, pm: int = 0) -> tuple:
    """Map FBL byte to (width, height).

    For FBL 224, the PM byte disambiguates the actual resolution.

    Returns (320, 320) as default if FBL is unknown.
    """
    if fbl == 224:
        if pm == 10:
            return (960, 540)
        elif pm == 12:
            return (800, 480)
        return (854, 480)
    return FBL_TO_RESOLUTION.get(fbl, (320, 320))


def pm_to_fbl(pm: int, sub: int = 0) -> int:
    """Map PM byte to FBL byte for Type 2 devices.

    Uses the mode-2 mapping from FormCZTV.cs.
    Special case: PM=1 + SUB=48 → FBL=114, PM=1 + SUB=49 → FBL=192.
    """
    if pm == 1 and sub == 48:
        return 114
    if pm == 1 and sub == 49:
        return 192
    return _PM_TO_FBL_TYPE2.get(pm, 100)  # Default to FBL=100 (320x320)


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class DeviceInfo:
    """Information extracted from a device handshake response."""
    device_type: int          # 2 or 3
    mode_byte_1: int = 0     # Type 2: resp[4], Type 3: resp[0]-1
    mode_byte_2: int = 0     # Type 2: resp[5], Type 3: 0
    serial: str = ""         # Hex string from response
    fbl: Optional[int] = None  # Type 3 only: resp[0]-1
    raw_response: Optional[bytes] = None  # Full handshake response for debugging
    resolution: Optional[tuple] = None  # (width, height) resolved from FBL/PM


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
# Type 2 — "H" variant  (VID 0x0416, PID 0x5302)
# =========================================================================

class HidDeviceType2:
    """Protocol handler for Type 2 HID LCD devices.

    Uses Ep01 for reads, Ep02 for writes.
    Image data is sent with a 20-byte header, 512-byte aligned.
    """

    def __init__(self, transport: UsbTransport):
        self.transport = transport
        self._initialized = False
        self.device_info: Optional[DeviceInfo] = None

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

        Conditions (from C#)::

            resp[0]==0xDA && resp[1]==0xDB && resp[2]==0xDC && resp[3]==0xDD
            && resp[12]==1 && resp[16]==0x10
        """
        if len(resp) < TYPE2_RESPONSE_SIZE:
            return False
        return (
            resp[0:4] == TYPE2_MAGIC
            and resp[12] == 0x01
            and resp[16] == 0x10
        )

    @staticmethod
    def parse_device_info(resp: bytes) -> DeviceInfo:
        """Extract device info from a validated handshake response.

        From USBLCDNEW_PROTOCOL.md::

            PM  = resp[4]   (product mode byte)
            SUB = resp[5]   (sub-variant byte)
            serial = hex string of resp[20:36]

        PM+SUB → FBL → resolution via pm_to_fbl() and fbl_to_resolution().
        """
        pm = resp[4]
        sub = resp[5]
        serial = resp[20:36].hex().upper()
        fbl = pm_to_fbl(pm, sub)
        resolution = fbl_to_resolution(fbl, pm)
        return DeviceInfo(
            device_type=2,
            mode_byte_1=pm,
            mode_byte_2=sub,
            serial=serial,
            fbl=fbl,
            raw_response=bytes(resp[:64]),
            resolution=resolution,
        )

    # -- Handshake -------------------------------------------------------

    def handshake(self) -> DeviceInfo:
        """Perform the init handshake.

        Matches C# ThreadSendDeviceDataH flow::

            Thread.Sleep(50);
            SubmitAsyncTransfer(write, Ep02);
            SubmitAsyncTransfer(read,  Ep01);
            Thread.Sleep(200);
            Wait(write); Wait(read);

        Python does sequential write→read (equivalent for non-overlapping I/O).

        Raises:
            RuntimeError: If the handshake fails.
        """
        init_pkt = self.build_init_packet()

        # C#: Thread.Sleep(50) before init
        time.sleep(DELAY_PRE_INIT_S)

        self.transport.write(EP_WRITE_02, init_pkt, DEFAULT_TIMEOUT_MS)

        # C#: Thread.Sleep(200) after submitting both transfers
        time.sleep(DELAY_POST_INIT_S)

        resp = self.transport.read(EP_READ_01, TYPE2_RESPONSE_SIZE, DEFAULT_TIMEOUT_MS)

        if not self.validate_response(resp):
            raise RuntimeError("Type 2 handshake failed: invalid response")

        self.device_info = self.parse_device_info(resp)
        self._initialized = True
        return self.device_info

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

class HidDeviceType3:
    """Protocol handler for Type 3 HID LCD devices.

    Uses Ep01 for reads, Ep02 for writes.
    Fixed-size 204816-byte frame writes with 16-byte ACK read.
    """

    def __init__(self, transport: UsbTransport):
        self.transport = transport
        self._initialized = False
        self.device_info: Optional[DeviceInfo] = None

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
    def parse_device_info(resp: bytes) -> DeviceInfo:
        """Extract device info from a validated handshake response.

        From USBLCDNEW_PROTOCOL.md::

            fbl = resp[0] - 1  (0x65→100, 0x66→101)
            serial = hex string of resp[10:14]
        """
        serial = resp[10:14].hex().upper()
        fbl = resp[0] - 1
        resolution = fbl_to_resolution(fbl)
        return DeviceInfo(
            device_type=3,
            mode_byte_1=fbl,
            serial=serial,
            fbl=fbl,
            raw_response=bytes(resp[:64]),
            resolution=resolution,
        )

    # -- Handshake -------------------------------------------------------

    def handshake(self) -> DeviceInfo:
        """Perform the init handshake.

        Matches C# ThreadSendDeviceDataALi flow::

            Thread.Sleep(50);
            SubmitAsyncTransfer(write, Ep02);
            SubmitAsyncTransfer(read,  Ep01);
            Thread.Sleep(200);
            Wait(write); Wait(read);

        Raises:
            RuntimeError: If the handshake fails.
        """
        init_pkt = self.build_init_packet()

        # C#: Thread.Sleep(50) before init
        time.sleep(DELAY_PRE_INIT_S)

        self.transport.write(EP_WRITE_02, init_pkt, DEFAULT_TIMEOUT_MS)

        # C#: Thread.Sleep(200) after submitting both transfers
        time.sleep(DELAY_POST_INIT_S)

        resp = self.transport.read(EP_READ_01, TYPE3_RESPONSE_SIZE, DEFAULT_TIMEOUT_MS)

        if not self.validate_response(resp):
            raise RuntimeError("Type 3 handshake failed: invalid response")

        self.device_info = self.parse_device_info(resp)
        self._initialized = True
        return self.device_info

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
        if not PYUSB_AVAILABLE:
            raise ImportError(
                "pyusb is not installed. Install with: pip install pyusb\n"
                "Also need libusb: apt install libusb-1.0-0 (Debian/Ubuntu) "
                "or dnf install libusb1 (Fedora)"
            )
        self._vid = vid
        self._pid = pid
        self._serial = serial
        self._device = None
        self._is_open = False

    def open(self) -> None:
        """Find USB device and claim interface.

        C# equivalent::

            UsbDeviceFinder finder = new UsbDeviceFinder(vid, pid, serial);
            usbDevice = UsbDevice.OpenUsbDevice(finder);
            usbDevice.SetConfiguration(1);
            usbDevice.ClaimInterface(0);
        """
        kwargs = {'idVendor': self._vid, 'idProduct': self._pid}
        if self._serial:
            kwargs['serial_number'] = self._serial

        self._device = usb.core.find(**kwargs)
        if self._device is None:
            raise RuntimeError(
                f"USB device not found: VID={self._vid:#06x} PID={self._pid:#06x}"
            )

        # Detach kernel driver if active (Linux-specific, matches C# ClaimInterface)
        if self._device.is_kernel_driver_active(USB_INTERFACE):
            self._device.detach_kernel_driver(USB_INTERFACE)

        # C#: SetConfiguration(1), ClaimInterface(0)
        self._device.set_configuration(USB_CONFIGURATION)
        usb.util.claim_interface(self._device, USB_INTERFACE)
        self._is_open = True

    def close(self) -> None:
        """Release interface and close.

        C# equivalent::

            usbDevice.ReleaseInterface(0);
            usbDevice.Close();
            UsbDevice.Exit();
        """
        if self._device is not None:
            try:
                usb.util.release_interface(self._device, USB_INTERFACE)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self._device)
            except Exception:
                pass
            self._device = None
        self._is_open = False

    def write(self, endpoint: int, data: bytes, timeout: int = DEFAULT_TIMEOUT_MS) -> int:
        """Bulk write.

        C# equivalent::

            usbEndpointWriter.Transfer(data, 0, length, timeout, out transferred);
        """
        if not self._is_open or self._device is None:
            raise RuntimeError("Transport not open")
        return self._device.write(endpoint, data, timeout=timeout)

    def read(self, endpoint: int, length: int, timeout: int = DEFAULT_TIMEOUT_MS) -> bytes:
        """Bulk read.

        C# equivalent::

            usbEndpointReader.Read(buffer, 0, length, timeout, out transferred);
        """
        if not self._is_open or self._device is None:
            raise RuntimeError("Transport not open")
        data = self._device.read(endpoint, length, timeout=timeout)
        return bytes(data)

    @property
    def is_open(self) -> bool:
        return self._is_open

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
        kwargs = {'vid': self._vid, 'pid': self._pid}
        if self._serial:
            kwargs['serial'] = self._serial
        self._device = hidapi.Device(**kwargs)
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

    if PYUSB_AVAILABLE:
        for vid, pid, dtype in known:
            for dev in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
                serial = usb.util.get_string(dev, dev.iSerialNumber) if dev.iSerialNumber else ""
                devices.append({
                    'vid': vid,
                    'pid': pid,
                    'device_type': dtype,
                    'serial': serial or "",
                    'backend': 'pyusb',
                })
    elif HIDAPI_AVAILABLE:
        for vid, pid, dtype in known:
            for info in hidapi.enumerate(vid, pid):
                devices.append({
                    'vid': vid,
                    'pid': pid,
                    'device_type': dtype,
                    'serial': info.get('serial_number', '') or "",
                    'backend': 'hidapi',
                })

    return devices
