"""AliLcd — Device implementation for USBLCDNew "Ali" variant LCD hardware.

A DISTINCT bulk protocol from BulkLcd (GrandVision).  The Ali device
(0416:5406, Elite Vision 360 ARGB) is driven by its own C# worker thread
``ThreadSendDeviceDataALi`` (USBLCDNEW.decompiled.cs:548) — different write
endpoint, handshake, validation, and a fixed 320x320 RGB565 canvas.  Sharing
BulkLcd would mean a wire-selecting branch inside one class; the C# itself
splits the two into separate threads, so we mirror that with a separate wire.

Handshake:   write 16-byte request + 1024 zero pad → EP 0x02 (cs:571-577),
             read 1024 → validate resp[0] in (101, 102) (cs:610).
Frame send:  16-byte header + 320x320 RGB565 (204800 B) → EP 0x02,
             then read a 16-byte ack (cs:646-660).  Fixed 204800-B buffer
             (cs:628) — no PM/resolution table for this device.
"""
from __future__ import annotations

import logging

from ...core.errors import HandshakeError, TransportError
from ...core.models import HandshakeResult, ProductInfo, Wire
from ...core.ports import BulkTransport, Device
from ...core.protocol import DeviceProfile
from . import DeviceFactory

log = logging.getLogger(__name__)


# ── Wire constants (line-cited to USBLCDNEW.decompiled.cs) ──────────────

_EP_WRITE = 0x02          # WriteEndpointID.Ep02 (cs:569) — NOT 0x01 (GrandVision)
_EP_READ = 0x81           # ReadEndpointID.Ep01 (cs:568)

# 16-byte handshake request + 1024-byte zero pad (cs:571-577).
_HANDSHAKE_REQUEST = bytes([
    0xF5, 0x00, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00,
]) + bytes(1024)

# 16-byte frame header (cs:646-650); bytes[12:16] LE = 0x032000 = 204800.
_FRAME_HEADER = bytes([
    0xF5, 0x01, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x03, 0x00,
])

_HANDSHAKE_READ_SIZE = 1024      # byte[1024] read buffer (cs:570)
_ACK_READ_SIZE = 16              # Read(first, 0, 16, ...) (cs:660)
_HANDSHAKE_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 5000
_READ_TIMEOUT_MS = 1000

# Fixed 320x320 RGB565 — new byte[204800] = 320*320*2 (cs:628).
_WIDTH = 320
_HEIGHT = 320
_FRAME_BYTES = _WIDTH * _HEIGHT * 2
_VALID_IDENTITY = (101, 102)     # resp[0] (cs:610)


@DeviceFactory.register(Wire.BULK_ALI)
class AliLcd(Device[BulkTransport]):
    """USBLCDNew "Ali" bulk LCD (fixed 320x320 RGB565)."""

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        # Fixed canvas — the C# hardcodes the 204800-B buffer, no PM table.
        self._profile = DeviceProfile(_WIDTH, _HEIGHT, jpeg=False, big_endian=True)

    @property
    def profile(self) -> DeviceProfile | None:
        """Fixed 320x320 RGB565 profile (no handshake-derived geometry)."""
        return self._profile

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        log.info("AliLcd %s: opening transport", self.info.key)
        if not self._transport.open():
            log.error("AliLcd %s: transport open failed", self.info.key)
            raise HandshakeError(f"Failed to open USB transport for {self.info.key}")

        try:
            self._transport.write(_EP_WRITE, _HANDSHAKE_REQUEST, _HANDSHAKE_TIMEOUT_MS)
            resp = self._transport.read(_EP_READ, _HANDSHAKE_READ_SIZE, _HANDSHAKE_TIMEOUT_MS)
        except TransportError as e:
            log.error("AliLcd %s: handshake I/O failed: %s", self.info.key, e)
            raise HandshakeError(f"AliLcd handshake I/O failed: {e}") from e

        if len(resp) < 1 or resp[0] not in _VALID_IDENTITY:
            log.error(
                "AliLcd %s: handshake validation failed (len=%d, resp[0]=%s)",
                self.info.key, len(resp), resp[0] if resp else "N/A",
            )
            raise HandshakeError(
                f"AliLcd handshake validation failed "
                f"(len={len(resp)}, resp[0]={resp[0] if resp else 'N/A'})"
            )

        model_id = resp[0] - 1     # obj3[0] = array[0] - 1 (cs:614)
        result = HandshakeResult(
            resolution=self._profile.resolution,
            model_id=model_id,
            pm_byte=resp[0],
            fbl=self.info.fbl,
            raw_response=bytes(resp[:64]),
        )
        self._handshake = result
        log.info("AliLcd handshake OK: resp[0]=%d model_id=%d resolution=%s (RGB565)",
                 resp[0], model_id, result.resolution)
        return result

    def send(self, payload: bytes) -> bool:
        if not self._transport.is_open or self._handshake is None:
            log.error("AliLcd %s: send() called before connect()", self.info.key)
            raise TransportError(
                f"AliLcd {self.info.key} not connected — call connect() first"
            )

        if len(payload) != _FRAME_BYTES:
            log.warning("AliLcd %s: payload is %d bytes, expected %d (320x320 RGB565)",
                        self.info.key, len(payload), _FRAME_BYTES)

        frame = _FRAME_HEADER + payload
        log.debug("AliLcd %s: sending %d-byte frame (RGB565, 320x320)",
                  self.info.key, len(frame))

        def _write_frame() -> bool:
            # C# writes the header+buffer in one transfer, then reads a 16-B ack
            # (cs:653-660).  One write call; PyUSB splits into USB packets.
            self._transport.write(_EP_WRITE, frame, _WRITE_TIMEOUT_MS)
            self._transport.read(_EP_READ, _ACK_READ_SIZE, _READ_TIMEOUT_MS)
            return True

        # Shared reconnect + recovery policy (base Device): one in-place
        # close→open→handshake retry absorbs an intermittent NAK / heals a
        # stale handle after resume, before escalating to the recovery tracker.
        return self._send_with_recovery(_write_frame)

    def disconnect(self) -> None:
        log.info("AliLcd %s: disconnecting", self.info.key)
        self._transport.close()
        self._handshake = None
