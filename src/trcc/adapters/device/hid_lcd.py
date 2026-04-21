"""HidLcd — Device implementation for HID-protocol LCD hardware.

Two firmware variants share one wire family:
    Type 2 ("H")   — VID 0x0416, PID 0x5302.  DA/DB/DC/DD magic +
                     512-byte init, 20-byte header + 512-aligned frame.
    Type 3 ("ALi") — VID 0x0418, PID 0x5303/0x5304.  F5-prefixed 1040-
                     byte init, 204816-byte fixed-size frames with ACK.

Type discriminator is `info.device_type` (2 or 3).  Everything else —
handshake template, packet building, endpoint addresses — lives in one
class.
"""
from __future__ import annotations

import logging
import struct
import time
from typing import Optional

from ...core.errors import HandshakeError, UnsupportedOperationError
from ...core.models import HandshakeResult, ProductInfo
from ...core.ports import BulkTransport, Device

log = logging.getLogger(__name__)


# =========================================================================
# Wire constants (from USBLCDNEW decompiled C#)
# =========================================================================

# Endpoints (LibUsbDotNet EP01 read / EP02 write)
_EP_READ = 0x81
_EP_WRITE = 0x02

# Type 2 magic bytes and sizes
_TYPE2_MAGIC = bytes([0xDA, 0xDB, 0xDC, 0xDD])
_TYPE2_INIT_SIZE = 512
_TYPE2_RESPONSE_SIZE = 512

# Type 3 command / frame prefixes and sizes
_TYPE3_CMD_PREFIX = bytes([0xF5, 0x00, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8])
_TYPE3_FRAME_PREFIX = bytes([0xF5, 0x01, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8])
_TYPE3_INIT_SIZE = 1040
_TYPE3_RESPONSE_SIZE = 1024
_TYPE3_DATA_SIZE = 204800   # 320*320*2
_TYPE3_ACK_SIZE = 16

_USB_BULK_ALIGNMENT = 512

# Handshake + frame timings (from C#)
_HANDSHAKE_TIMEOUT_MS = 5000
_HANDSHAKE_MAX_RETRIES = 3
_HANDSHAKE_RETRY_DELAY_S = 0.5
_DELAY_PRE_INIT_S = 0.050
_DELAY_POST_INIT_S = 0.200
_DELAY_FRAME_TYPE2_S = 0.001

_DEFAULT_FRAME_TIMEOUT_MS = 100


def _ceil_to_align(n: int, align: int = _USB_BULK_ALIGNMENT) -> int:
    """Round *n* up to the next multiple of *align*."""
    return (n + align - 1) // align * align


def _frame_timeout_ms(packet_size: int) -> int:
    """Scale frame timeout with packet size (USB 2.0 ≈ 4 KB/ms + 100ms margin)."""
    return max(_DEFAULT_FRAME_TIMEOUT_MS, packet_size // 4 + 100)


# =========================================================================
# HidLcd
# =========================================================================


class HidLcd(Device[BulkTransport]):
    """HID-protocol LCD device (Type 2 or Type 3 firmware variants).

    Selection is by `info.device_type` (2 or 3); both variants share the
    handshake template (write init → delay → read response → validate →
    parse) and differ in packet layout and response validation.
    """

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        if info.device_type not in (2, 3):
            raise UnsupportedOperationError(
                f"HidLcd requires device_type 2 or 3, got {info.device_type}"
            )

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        """Open transport and perform the type-specific handshake."""
        if not self._transport.open():
            raise HandshakeError(
                f"Failed to open USB transport for {self.info.key}"
            )

        init_pkt = self._build_init_packet()
        response_size = self._response_size()

        last_err: Optional[Exception] = None
        for attempt in range(1, _HANDSHAKE_MAX_RETRIES + 1):
            try:
                time.sleep(_DELAY_PRE_INIT_S)
                self._transport.write(_EP_WRITE, init_pkt, _HANDSHAKE_TIMEOUT_MS)
                time.sleep(_DELAY_POST_INIT_S)

                resp = self._transport.read(
                    _EP_READ, response_size, _HANDSHAKE_TIMEOUT_MS,
                )

                if not self._validate_response(resp):
                    log.warning(
                        "HidLcd handshake attempt %d/%d: invalid response "
                        "(len=%d, first 16: %s)",
                        attempt, _HANDSHAKE_MAX_RETRIES,
                        len(resp), resp[:16].hex() if resp else "empty",
                    )
                    last_err = HandshakeError(
                        f"Invalid handshake response for {self.info.key}"
                    )
                    time.sleep(_HANDSHAKE_RETRY_DELAY_S)
                    continue

                result = self._parse_response(resp)
                self._handshake = result
                log.info("HidLcd type %d handshake OK: resolution=%s",
                         self.info.device_type, result.resolution)
                return result

            except Exception as e:
                log.warning("HidLcd handshake attempt %d/%d failed: %s",
                            attempt, _HANDSHAKE_MAX_RETRIES, e)
                last_err = e
                if attempt < _HANDSHAKE_MAX_RETRIES:
                    time.sleep(_HANDSHAKE_RETRY_DELAY_S)

        raise HandshakeError(
            f"HidLcd handshake failed after {_HANDSHAKE_MAX_RETRIES} attempts"
        ) from last_err

    def send(self, payload: bytes) -> bool:
        """Send one image frame (RGB565 or JPEG bytes, protocol-specific)."""
        if not self._transport.is_open:
            raise HandshakeError(
                f"HidLcd {self.info.key} not connected — call connect() first"
            )

        if self.info.device_type == 2:
            packet = self._build_frame_type2(payload)
        else:
            packet = self._build_frame_type3(payload)

        timeout = _frame_timeout_ms(len(packet))
        transferred = self._transport.write(_EP_WRITE, packet, timeout)
        if transferred == 0:
            return False

        if self.info.device_type == 2:
            time.sleep(_DELAY_FRAME_TYPE2_S)
            return transferred == len(packet)

        # Type 3: read ACK
        ack = self._transport.read(_EP_READ, _TYPE3_ACK_SIZE, _DEFAULT_FRAME_TIMEOUT_MS)
        return len(ack) > 0

    def disconnect(self) -> None:
        self._transport.close()
        self._handshake = None

    # ── Wire protocol — Type 2 variant ────────────────────────────────

    def _build_init_packet_type2(self) -> bytes:
        """Type 2 512-byte handshake: magic + command=1, zero-padded."""
        header = (
            _TYPE2_MAGIC
            + b'\x00' * 8
            + b'\x01\x00\x00\x00'
            + b'\x00' * 4
        )
        return header + b'\x00' * (_TYPE2_INIT_SIZE - len(header))

    def _validate_response_type2(self, resp: bytes) -> bool:
        """Type 2: resp[0:4] == DA DB DC DD && resp[12] == 0x01."""
        return (
            len(resp) >= 20
            and resp[0:4] == _TYPE2_MAGIC
            and resp[12] == 0x01
        )

    def _parse_response_type2(self, resp: bytes) -> HandshakeResult:
        """Type 2: PM at resp[5], SUB at resp[4], optional serial at resp[20:36]."""
        pm = resp[5]
        sub = resp[4]
        has_serial = len(resp) > 36 and resp[16] == 0x10
        serial = resp[20:36].hex().upper() if has_serial else ""
        # Type 2 resolution is carried by device_type + pm — the full
        # pm→fbl→resolution mapping lands in Phase 5 (services).  For
        # now, fall back to the native resolution from the product info.
        return HandshakeResult(
            resolution=self.info.native_resolution,
            model_id=pm,
            serial=serial,
            pm_byte=pm,
            sub_byte=sub,
            raw_response=bytes(resp[:64]),
        )

    def _build_frame_type2(self, image_data: bytes) -> bytes:
        """Type 2 frame: 20-byte header + image data, 512-aligned.

        Mode detection:
            JPEG (FF D8 magic) → header byte[6]=0x00, actual w×h in bytes[8:12]
            RGB565             → header byte[6]=0x01, hardcoded 240×320
        """
        is_jpeg = len(image_data) >= 2 and image_data[:2] == b'\xff\xd8'
        w, h = self.info.native_resolution

        header = bytearray([
            0xDA, 0xDB, 0xDC, 0xDD,   # magic
            0x02, 0x00,                # cmd_type = PICTURE
        ])
        if is_jpeg:
            header += b'\x00\x00'
            header += struct.pack('<HH', w, h)
        else:
            header += b'\x01\x00'
            header += struct.pack('<HH', 240, 320)  # C# hardcoded
        header += bytes([0x02, 0x00, 0x00, 0x00])
        header += struct.pack('<I', len(image_data))

        raw = bytes(header) + image_data
        return raw.ljust(_ceil_to_align(len(raw)), b'\x00')

    # ── Wire protocol — Type 3 variant ────────────────────────────────

    def _build_init_packet_type3(self) -> bytes:
        """Type 3 1040-byte handshake: F5 prefix + 16-byte header + 1024 zeros."""
        prefix = _TYPE3_CMD_PREFIX + b'\x00\x00\x00\x00' + b'\x00\x04\x00\x00'
        return prefix + b'\x00' * 1024

    def _validate_response_type3(self, resp: bytes) -> bool:
        """Type 3: resp[0] ∈ {0x65, 0x66} and len >= 14."""
        return len(resp) >= 14 and resp[0] in (0x65, 0x66)

    def _parse_response_type3(self, resp: bytes) -> HandshakeResult:
        """Type 3: FBL = resp[0] - 1 (0x65→100, 0x66→101); serial at resp[10:14]."""
        serial = resp[10:14].hex().upper()
        fbl = resp[0] - 1
        return HandshakeResult(
            resolution=self.info.native_resolution,
            model_id=fbl,
            serial=serial,
            pm_byte=fbl,
            sub_byte=0,
            fbl=fbl,
            raw_response=bytes(resp[:64]),
        )

    def _build_frame_type3(self, image_data: bytes) -> bytes:
        """Type 3 frame: 16-byte prefix + exactly 204800 bytes data."""
        prefix = _TYPE3_FRAME_PREFIX + b'\x00\x00\x00\x00' + struct.pack('<I', _TYPE3_DATA_SIZE)
        if len(image_data) < _TYPE3_DATA_SIZE:
            payload = image_data + b'\x00' * (_TYPE3_DATA_SIZE - len(image_data))
        else:
            payload = image_data[:_TYPE3_DATA_SIZE]
        return prefix + payload

    # ── Type-dispatching helpers ──────────────────────────────────────

    def _build_init_packet(self) -> bytes:
        return (self._build_init_packet_type2()
                if self.info.device_type == 2
                else self._build_init_packet_type3())

    def _response_size(self) -> int:
        return (_TYPE2_RESPONSE_SIZE
                if self.info.device_type == 2
                else _TYPE3_RESPONSE_SIZE)

    def _validate_response(self, resp: bytes) -> bool:
        return (self._validate_response_type2(resp)
                if self.info.device_type == 2
                else self._validate_response_type3(resp))

    def _parse_response(self, resp: bytes) -> HandshakeResult:
        return (self._parse_response_type2(resp)
                if self.info.device_type == 2
                else self._parse_response_type3(resp))
