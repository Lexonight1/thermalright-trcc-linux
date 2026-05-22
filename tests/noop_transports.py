"""Noop transports — fake USB at the wire, everything above is real.

These implement the real transport ABCs (ScsiTransport, UsbTransport) and
return canned bytes that mimic what a physical device would send. Every
layer above the transport — ScsiDevice, HidDeviceType2, LedHidSender,
the factory protocols — is production code.

Usage:
    transport = NoopScsiTransport(fbl=100, pm=32)
    dev = ScsiDevice("/dev/sg0", transport)
    result = dev.handshake()  # real code, fake USB
"""
from __future__ import annotations

from trcc.legacy.adapters.device.hid import UsbTransport
from trcc.legacy.adapters.device.scsi import ScsiTransport

# ═════════════════════════════════════════════════════════════════════════════
# SCSI — canned poll response, accepts writes
# ═════════════════════════════════════════════════════════════════════════════


class NoopScsiTransport(ScsiTransport):
    """Fake SCSI transport — returns canned handshake bytes, discards frames.

    The poll response (read_cdb) returns 0xE100 bytes with:
      byte[0] = FBL (resolution identifier)
      bytes[4:8] = zeros (not booting)
    This is what a real device sends after it finishes booting.
    """

    def __init__(self, fbl: int = 100, pm: int = 0) -> None:
        self._fbl = fbl
        self._pm = pm
        self._open = False
        self.frames_sent: int = 0

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def send_cdb(self, cdb: bytes, data: bytes) -> bool:
        self.frames_sent += 1
        return True

    def read_cdb(self, cdb: bytes, length: int) -> bytes:
        """Return canned poll response: FBL at byte[0], no boot signature."""
        resp = bytearray(length)
        resp[0] = self._fbl
        return bytes(resp)


# ═════════════════════════════════════════════════════════════════════════════
# USB (HID/LED/Bulk) — canned handshake response, accepts writes
# ═════════════════════════════════════════════════════════════════════════════


class NoopUsbTransport(UsbTransport):
    """Fake USB transport — returns canned HID/LED handshake, discards frames.

    Returns the canned response on any read whose length matches the response
    size (handshake reads).  Smaller reads (frame ACKs) get zeros.  This
    mirrors a real device: the firmware re-emits the handshake bytes every
    time the host writes the init packet, so re-handshake after sleep/resume
    or hot-plug works the same as the first call.
    """

    def __init__(self, handshake_response: bytes) -> None:
        self._resp = handshake_response
        self._open = False
        self.writes: int = 0

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, endpoint: int, data: bytes, timeout: int = 5000) -> int:
        self.writes += 1
        return len(data)

    def read(self, endpoint: int, length: int, timeout: int = 5000) -> bytes:
        if length == len(self._resp):
            return self._resp
        return b'\x00' * length


# ═════════════════════════════════════════════════════════════════════════════
# Response builders — construct valid handshake bytes for each protocol
# ═════════════════════════════════════════════════════════════════════════════


def build_hid_type2_response(pm: int, sub: int = 0) -> bytes:
    """Build a valid Type 2 HID handshake response.

    Layout (512 bytes):
      [0:4]   = DA DB DC DD (magic)
      [4]     = sub byte
      [5]     = pm byte
      [12]    = 0x01 (command ack)
    """
    resp = bytearray(512)
    resp[0:4] = bytes([0xDA, 0xDB, 0xDC, 0xDD])
    resp[4] = sub
    resp[5] = pm
    resp[12] = 0x01
    return bytes(resp)


def build_hid_type3_response(fbl: int = 100) -> bytes:
    """Build a valid Type 3 HID handshake response.

    Layout (1024 bytes):
      [0]     = fbl + 1 (0x65 for fbl=100, 0x66 for fbl=101)
      [10:14] = serial bytes
    """
    resp = bytearray(1024)
    resp[0] = fbl + 1  # 0x65 = 100, 0x66 = 101
    resp[10:14] = b'\xDE\xAD\xBE\xEF'  # fake serial
    return bytes(resp)


def build_led_response(pm: int, sub: int = 0) -> bytes:
    """Build a valid LED handshake response.

    Layout (64 bytes):
      [0:4]   = DA DB DC DD (magic, same as HID Type 2)
      [4]     = sub byte
      [5]     = pm byte
      [12]    = 0x01 (command ack)
    """
    resp = bytearray(64)
    resp[0:4] = bytes([0xDA, 0xDB, 0xDC, 0xDD])
    resp[4] = sub
    resp[5] = pm
    resp[12] = 0x01
    return bytes(resp)


def build_bulk_response(pm: int = 32, sub: int = 0) -> bytes:
    """Build a valid USBLCDNew bulk handshake response.

    Layout (1024 bytes):
      [24] = pm byte (must be non-zero)
      [36] = sub byte
    PM=32 → RGB565 mode; everything else → JPEG.
    """
    resp = bytearray(1024)
    resp[24] = pm
    resp[36] = sub
    return bytes(resp)


# ═════════════════════════════════════════════════════════════════════════════
# Bulk / LY — fake Device class (these protocols wrap a Device, not a transport)
# ═════════════════════════════════════════════════════════════════════════════


class NoopBulkLikeDevice:
    """Fake BulkDevice / LyDevice — satisfies the contract _BulkLikeProtocol calls.

    Bulk and LY don't go through ``DeviceProtocolFactory.create_usb_transport``;
    they wrap a Device that owns its pyusb endpoints directly.  The smoke
    matrix patches ``_make_device`` on each protocol class to return one of
    these instead, so the protocol's lifecycle runs end-to-end without ever
    hitting real USB.
    """

    def __init__(
        self,
        vid: int,
        pid: int,
        *,
        usb_address=None,
        resolution: tuple[int, int] = (480, 480),
        model_id: int = 32,
    ) -> None:
        from trcc.legacy.core.models import HandshakeResult
        self.vid = vid
        self.pid = pid
        self.usb_address = usb_address
        self._handshake_result = HandshakeResult(
            resolution=resolution, model_id=model_id,
        )
        self.frames_sent: int = 0
        self.closed: bool = False

    def handshake(self):
        return self._handshake_result

    def send_frame(self, image_data: bytes) -> bool:
        self.frames_sent += 1
        return True

    def close(self) -> None:
        self.closed = True


def build_ly_response(pm_raw: int = 1, sub: int = 0, *, pid: int = 0x5408) -> bytes:
    """Build a valid LY-protocol (Trofeo Vision) handshake response.

    Layout (512 bytes):
      [0]  = 0x03   (response code)
      [1]  = 0xFF   (magic)
      [8]  = 0x01   (ack)
      [20] = pm_raw  (LY:  PM = 64 + raw, clamped to 1 if <= 3)
      [22] = sub
      [36] = pm_raw  (LY1: PM = 50 + resp[36])
    """
    resp = bytearray(512)
    resp[0] = 0x03
    resp[1] = 0xFF
    resp[8] = 0x01
    if pid == 0x5408:
        resp[20] = pm_raw
        resp[22] = sub
    else:  # 0x5409
        resp[36] = pm_raw
        resp[22] = sub
    return bytes(resp)
