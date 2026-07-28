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
from ...core.ports import BulkTransport
from ...core.protocol import DeviceProfile
from . import DeviceFactory
from ._base import BaseBulkDevice

log = logging.getLogger(__name__)


# ── Wire constants (line-cited to USBLCDNEW.decompiled.cs) ──────────────

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

# The canvas is a constant, not a handshake product — the C# hardcodes the
# 204800-B buffer and this device has no PM/resolution table.  Owning it at
# module scope keeps the one true geometry in one place and out of the
# handshake-derived ``_profile`` slot's Optional dance.
_PROFILE = DeviceProfile(_WIDTH, _HEIGHT, jpeg=False, big_endian=True)


@DeviceFactory.register(Wire.BULK_ALI)
class AliLcd(BaseBulkDevice):
    """USBLCDNew "Ali" bulk LCD (fixed 320x320 RGB565)."""

    _EP_WRITE = 0x02   # WriteEndpointID.Ep02 (cs:569) — NOT 0x01 (GrandVision)
    _EP_READ = 0x81    # ReadEndpointID.Ep01 (cs:568)

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._profile = _PROFILE

    def _reset_state(self) -> None:
        """Keep the profile across a disconnect — it is a constant, not a
        handshake product, so there is nothing stale to drop."""

    # ── Device ABC ────────────────────────────────────────────────────

    def _do_handshake(self) -> HandshakeResult:
        resp = self._exchange(_HANDSHAKE_REQUEST, _HANDSHAKE_READ_SIZE,
                              _HANDSHAKE_TIMEOUT_MS)

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
        return HandshakeResult(
            resolution=_PROFILE.resolution,
            model_id=model_id,
            pm_byte=resp[0],
            fbl=self.info.fbl,
            raw_response=bytes(resp[:64]),
        )

    def _handshake_detail(self, result: HandshakeResult) -> str:
        """Ali reports identity in resp[0] (== PM here); model_id is that − 1."""
        return f" (RGB565, model_id={result.model_id})"

    def _require_connected(self) -> None:
        """Also demand a completed handshake — the panel ignores frames until
        it has answered its identity exchange."""
        super()._require_connected()
        if self._handshake is None:
            log.error("AliLcd %s: send() called before connect()", self.info.key)
            raise TransportError(
                f"AliLcd {self.info.key} not connected — call connect() first"
            )

    def _prepare_frame(self, payload: bytes) -> bytes:
        """16-byte header + the fixed 204800-B RGB565 buffer (cs:646-650)."""
        if len(payload) != _FRAME_BYTES:
            log.warning("AliLcd %s: payload is %d bytes, expected %d (320x320 RGB565)",
                        self.info.key, len(payload), _FRAME_BYTES)
        frame = _FRAME_HEADER + payload
        log.debug("AliLcd %s: sending %d-byte frame (RGB565, 320x320)",
                  self.info.key, len(frame))
        return frame

    def _write_frame(self, frame: bytes) -> bool:
        # C# writes the header+buffer in one transfer, then reads a 16-B ack
        # (cs:653-660).  One write call; PyUSB splits into USB packets.
        self._transport.write(self._EP_WRITE, frame, _WRITE_TIMEOUT_MS)
        self._transport.read(self._EP_READ, _ACK_READ_SIZE, _READ_TIMEOUT_MS)
        return True
