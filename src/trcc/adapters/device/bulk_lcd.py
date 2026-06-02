"""BulkLcd — Device implementation for raw-bulk USBLCDNew devices.

Vendor-specific (bInterfaceClass=255) LCD hardware that doesn't speak
SCSI or HID.  Protocol from USBLCDNew.exe ThreadSendDeviceData
(87AD:70DB GrandVision series and related products).

Handshake:   write 64-byte request → read 1024-byte response.
             PM at resp[24], SUB at resp[36].
Frame send:  64-byte header + payload (JPEG or raw RGB565),
             chunked into 16 KiB USB writes, ZLP on 512-byte alignment.
"""
from __future__ import annotations

import logging
import struct

from ...core.errors import (
    DeviceDisconnectedError,
    HandshakeError,
    TransportError,
)
from ...core.models import HandshakeResult, ProductInfo, Wire
from ...core.ports import BulkTransport, Device
from ...core.protocol import DeviceProfile, get_profile, pm_to_fbl
from . import DeviceFactory

log = logging.getLogger(__name__)


# ── Wire constants ─────────────────────────────────────────────────────

_EP_WRITE = 0x01
_EP_READ = 0x81

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
_WRITE_TIMEOUT_MS = 5000
_WRITE_CHUNK_SIZE = 16 * 1024

# PM values that use raw RGB565 (cmd=3); everything else uses JPEG (cmd=2).
_RGB565_PMS: set[int] = {32}


@DeviceFactory.register(Wire.BULK)
class BulkLcd(Device[BulkTransport]):
    """Raw USB bulk LCD device (USBLCDNew protocol)."""

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._pm: int = 0
        self._sub: int = 0
        # Cached at handshake. Carries PM-derived resolution from the FBL
        # tables, with the Bulk-specific JPEG-vs-RGB565 override:
        # USBLCDNew uses JPEG (cmd=2) for every PM except 32, which forces
        # RGB565 (cmd=3). FBL_PROFILES alone wouldn't capture that.
        self._profile: DeviceProfile | None = None

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived profile; None pre-handshake."""
        return self._profile

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        log.info("BulkLcd %s: opening transport", self.info.key)
        if not self._transport.open():
            log.error("BulkLcd %s: transport open failed", self.info.key)
            raise HandshakeError(f"Failed to open USB transport for {self.info.key}")

        try:
            self._transport.write(_EP_WRITE, _HANDSHAKE_PAYLOAD, _HANDSHAKE_TIMEOUT_MS)
            resp = self._transport.read(_EP_READ, _HANDSHAKE_READ_SIZE, _HANDSHAKE_TIMEOUT_MS)
        except TransportError as e:
            log.error("BulkLcd %s: handshake I/O failed: %s", self.info.key, e)
            raise HandshakeError(f"BulkLcd handshake I/O failed: {e}") from e

        if len(resp) < 41 or resp[24] == 0:
            log.error(
                "BulkLcd %s: handshake validation failed (len=%d, resp[24]=%s)",
                self.info.key, len(resp),
                resp[24] if len(resp) > 24 else "N/A",
            )
            raise HandshakeError(
                f"BulkLcd handshake validation failed "
                f"(len={len(resp)}, resp[24]={resp[24] if len(resp) > 24 else 'N/A'})"
            )

        self._pm = resp[24]
        self._sub = resp[36]

        # Build the cached profile. Resolution comes from the FBL tables
        # (mirrors legacy ``_bulk_resolution``); the jpeg flag is the
        # Bulk-specific override (USBLCDNew → JPEG except PM=32).
        fbl = pm_to_fbl(self._pm, self._sub)
        base = get_profile(fbl, self._pm)
        self._profile = DeviceProfile(
            width=base.width, height=base.height,
            jpeg=(self._pm not in _RGB565_PMS),
            big_endian=base.big_endian, rotate=base.rotate,
            encode_base=base.encode_base, encode_invert=base.encode_invert,
            encode_sub_bases=base.encode_sub_bases,
            encode_pm_bases=base.encode_pm_bases,
        )

        result = HandshakeResult(
            resolution=self._profile.resolution,
            model_id=self._pm,
            pm_byte=self._pm,
            sub_byte=self._sub,
            fbl=fbl,
            raw_response=bytes(resp[:64]),
        )
        self._handshake = result
        log.info("BulkLcd handshake OK: PM=%d SUB=%d resolution=%s (%s)",
                 self._pm, self._sub, result.resolution,
                 "JPEG" if self._profile.jpeg else "RGB565")
        return result

    def send(self, payload: bytes) -> bool:
        if not self._transport.is_open or self._profile is None:
            log.error("BulkLcd %s: send() called before connect()", self.info.key)
            raise TransportError(
                f"BulkLcd {self.info.key} not connected — call connect() first"
            )

        # Resolution + encoding come from the handshake-derived profile.
        # USBLCDNew header carries actual w/h (used by the device firmware
        # for de-blocking JPEG / interpreting the RGB565 buffer).
        width, height = self._profile.resolution
        cmd = 2 if self._profile.jpeg else 3

        header = bytearray(64)
        header[0:4] = _HANDSHAKE_PAYLOAD[0:4]
        struct.pack_into("<I", header, 4, cmd)
        struct.pack_into("<I", header, 8, width)
        struct.pack_into("<I", header, 12, height)
        struct.pack_into("<I", header, 56, 2)
        struct.pack_into("<I", header, 60, len(payload))

        frame = bytes(header) + payload
        log.debug("BulkLcd %s: sending %d-byte frame (%s, %dx%d)",
                  self.info.key, len(frame),
                  "JPEG" if self._profile.jpeg else "RGB565", width, height)

        # Two-attempt loop with reconnect between attempts.  KVM USB
        # passthrough + slower hubs intermittently NAK a frame; legacy
        # ``BulkDevice.send_frame:199-225`` handled this by closing,
        # re-handshaking, and retrying once before giving up.  The
        # outer except hands the FINAL error to the recovery tracker
        # so the per-frame retry doesn't burn G14's consecutive-error
        # budget.
        last_exc: BaseException | None = None
        for attempt in range(2):
            try:
                for offset in range(0, len(frame), _WRITE_CHUNK_SIZE):
                    self._transport.write(
                        _EP_WRITE, frame[offset:offset + _WRITE_CHUNK_SIZE],
                        _WRITE_TIMEOUT_MS,
                    )
                # Zero-length packet on 512-byte alignment (frame delimiter)
                if len(frame) % 512 == 0:
                    self._transport.write(_EP_WRITE, b"", _WRITE_TIMEOUT_MS)
                break  # success
            except Exception as e:
                last_exc = e
                if attempt == 0:
                    log.warning(
                        "BulkLcd %s: send attempt 1 failed (%s) — "
                        "reconnecting and retrying",
                        self.info.key, e,
                    )
                    try:
                        self._transport.close()
                        self._transport.open()
                        self.connect()
                    except Exception as reconnect_err:
                        log.warning(
                            "BulkLcd %s: reconnect failed: %s",
                            self.info.key, reconnect_err,
                        )
                        # Fall through — second attempt will likely also
                        # raise, and the recovery tracker handles it.
                    continue
                # Second attempt also failed — defer to the tracker.
                verdict = self._recovery.note_error(e)
                if verdict == "threshold":
                    try:
                        self._transport.close()
                    except OSError as close_err:
                        log.debug("BulkLcd %s: close raised: %s",
                                  self.info.key, close_err)
                    raise DeviceDisconnectedError(
                        f"BulkLcd {self.info.key} disconnected after "
                        f"{self._recovery.consecutive_failures} consecutive failures",
                    ) from e
                return False
        else:  # pragma: no cover — 'break' or 'return' always exits
            log.debug("BulkLcd %s: send loop exhausted; last_exc=%s",
                      self.info.key, last_exc)
            return False

        recovered = self._recovery.note_success()
        if recovered:
            log.info("BulkLcd %s: send recovered after %d disconnect failure(s)",
                     self.info.key, recovered)
        return True

    def disconnect(self) -> None:
        log.info("BulkLcd %s: disconnecting", self.info.key)
        self._transport.close()
        self._handshake = None
        self._profile = None
