"""HidLcd — Device implementation for HID-protocol LCD hardware.

Two firmware variants share one wire family:
    Type 2 ("H")   — VID 0x0416, PID 0x5302.  DA/DB/DC/DD magic +
                     512-byte init, 20-byte header + 512-aligned frame.
    Type 3 (F5)    — 0418:5303, 0418:5304 and 0416:5406.  F5-prefixed
                     1040-byte init, 204816-byte fixed-size frames with ACK.
                     0416:5406 (Elite Vision 360, #212) reached this class on
                     2026-08-19; it had a byte-identical implementation of the
                     same protocol on a wire of its own.

Type discriminator is `info.device_type` (2 or 3).  Everything else —
handshake template, packet building, endpoint addresses — lives in one
class.
"""
from __future__ import annotations

import dataclasses
import logging
import struct
import time

from ...core.errors import (
    HandshakeError,
    TransportError,
    UnsupportedOperationError,
)
from ...core.logs import per_frame
from ...core.models import HandshakeResult, ProductInfo, Wire
from ...core.ports import BulkTransport
from ...core.protocol import DeviceProfile, get_profile, pm_to_fbl
from . import _f5
from ._base import HANDSHAKE_TIMEOUT_MS, BaseBulkDevice

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


# =========================================================================
# Wire constants (from USBLCDNEW decompiled C#)
# =========================================================================

# Type 2 magic bytes and sizes
_TYPE2_MAGIC = bytes([0xDA, 0xDB, 0xDC, 0xDD])
_TYPE2_INIT_SIZE = 512
_TYPE2_RESPONSE_SIZE = 512

# Type 3 speaks the F5 protocol; its bytes live in ``_f5``.  0416:5406 had a
# second, byte-identical implementation on its own wire until 2026-08-19 —
# see ``_f5`` for what that cost and ``tests/test_f5_protocol.py`` for the gate.

_USB_BULK_ALIGNMENT = 512

# Frame timing (from C#).  The handshake's own pacing + retry budget is shared
# with the LED wire and lives on ``_base`` — this wire only adds its per-frame
# settle.
_DELAY_FRAME_TYPE2_S = 0.001
# A streaming firmware reboots on the init packet, so we skip it and let the
# panel finish its own boot before the first frame (#228 Frozen Warframe SE).
_QUIRK_BOOT_SETTLE_S = 3.0

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


class HidLcd(BaseBulkDevice, wire=Wire.HID):
    """HID-protocol LCD device (Type 2 or Type 3 firmware variants).

    Selection is by `info.device_type` (2 or 3); both variants share the
    handshake template (write init → delay → read response → validate →
    parse) and differ in packet layout and response validation.

    ``self._profile`` is populated by the handshake and carries PM-derived
    geometry + encoding flags (jpeg, big_endian, rotate).  Frame builders read
    it instead of ``info.native_resolution``, so a variant that reports a
    different resolution at handshake is honored.
    """

    # LibUsbDotNet EP01 read / EP02 write.
    _EP_WRITE = 0x02

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        if info.device_type not in (2, 3):
            raise UnsupportedOperationError(
                f"HidLcd requires device_type 2 or 3, got {info.device_type}"
            )

    # ── Device ABC ────────────────────────────────────────────────────

    def _handshake_detail(self, result: HandshakeResult) -> str:
        """Which firmware variant answered — the two speak different packets."""
        return f" (type {self.info.device_type})"

    def _require_connected(self) -> None:
        """Also demand a completed handshake — the panel ignores frames until
        it has answered its identity exchange.

        Carried over when ``AliLcd`` merged into this class (0416:5406), where
        it was load-bearing rather than decorative.  ``connect()`` sets
        ``_handshake`` only AFTER ``_do_handshake()`` returns, so a connect that
        RAISED leaves the transport open with no handshake — the base guard
        passes and frames go to a panel that never identified itself.

        MEASURED across all six classes rather than assumed: ``BulkLcd``
        guards the same window but on ``_profile``, not ``_handshake``;
        ``ScsiLcd`` / ``LyLcd`` / ``Led`` do not guard it at all and will send
        frames after a failed connect.  Three of six, in two different
        spellings, is a leftover rather than a design — worth unifying on the
        base once someone can exercise the LY and SCSI failure paths.
        """
        super()._require_connected()
        if self._handshake is None:
            log.error("HidLcd %s: send() called before a completed handshake",
                      self.info.key)
            raise TransportError(
                f"HidLcd {self.info.key} not connected — call connect() first"
            )

    def _do_handshake(self) -> HandshakeResult:
        """Perform the type-specific handshake, retrying a bad exchange.

        The streaming-firmware path (#228) is *offered* to a quirked firmware,
        not forced on it: it only succeeds if the panel actually volunteers a
        reply identifying itself.  A quirked fingerprint that says nothing has
        told us nothing, so we fall through to the ordinary handshake rather
        than guess — see :meth:`_connect_streaming_firmware`.
        """
        if self._quirks.skip_init:
            if (result := self._connect_streaming_firmware()) is not None:
                return result
            log.info("HidLcd %s: streaming firmware volunteered nothing — "
                     "falling through to the standard handshake",
                     self.info.key)
        return self._handshake_retry(
            self._build_init_packet(), self._response_size(),
            self._validate_and_parse,
        )

    def _validate_and_parse(self, resp: bytes) -> HandshakeResult:
        """Accept the reply or reject it as a retryable attempt."""
        if not self._validate_response(resp):
            log.warning("HidLcd handshake: invalid response (len=%d, first 16: %s)",
                        len(resp), resp[:16].hex() if resp else "empty")
            raise HandshakeError(
                f"Invalid handshake response for {self.info.key}"
            )
        return self._parse_response(resp)

    def _connect_streaming_firmware(self) -> HandshakeResult | None:
        """Handshake a firmware that reboots on the init packet and streams
        without the normal exchange (#228 Frozen Warframe SE, bcdDevice 4.07).

        Faithful to the reporter's working driver: no init packet, let the
        panel settle, and read the short (8-byte) reply it volunteers for
        PM/SUB.  When that reply names a PM we pin a portrait-native profile —
        the panel self-orients, so we must NOT pre-rotate.

        Returns ``None`` when the panel volunteered nothing, so the caller can
        run the ordinary handshake instead.

        **Why silence must not be treated as identification (#244/#267/#268).**
        ``bcdDevice`` 4.07 is NOT unique to the Frozen Warframe SE — Thermalright
        ships several different panels as ``0416:5302`` firmware 4.07, and the
        only thing separating them is the PM byte this method exists to read.
        Trusting the fingerprint alone meant a silent panel got the registry's
        placeholder geometry: the Trofeo Vision, a 1280×480 device, was pinned
        to 240×320 and never displayed anything, for four reporters.  Their own
        logs show the standard handshake answering ``PM=128 SUB=1
        resolution=(1280, 480)`` moments later on the very same hardware.
        A reply identifies a panel; silence identifies nothing.
        """
        log.info("HidLcd %s: streaming-firmware connect (init skipped)",
                 self.info.key)
        time.sleep(_QUIRK_BOOT_SETTLE_S)
        resp = b""
        try:
            resp = self._transport.read(
                self._EP_READ, self._response_size(), HANDSHAKE_TIMEOUT_MS,
            )
        except Exception as e:
            log.info("HidLcd %s: no unsolicited handshake (%s)", self.info.key, e)

        if not (len(resp) >= 6 and resp[0:4] == _TYPE2_MAGIC):
            log.info("HidLcd %s: no streaming handshake reply (%d byte(s)) — "
                     "the fingerprint alone does not identify the panel",
                     self.info.key, len(resp))
            return None
        pm, sub = resp[5], resp[4]
        if not pm:
            log.info("HidLcd %s: streaming reply carried PM=0 — no panel identity",
                     self.info.key)
            return None
        log.info("HidLcd %s: short handshake reply PM=%d SUB=%d",
                 self.info.key, pm, sub)

        fbl = pm_to_fbl(pm, sub)
        profile = self._portrait_native(self._base_profile(fbl, pm))
        self._profile = profile
        log.info("HidLcd %s: streaming connect OK, portrait-native %s",
                 self.info.key, profile.resolution)
        return HandshakeResult(
            resolution=profile.resolution, model_id=pm, serial="",
            pm_byte=pm, sub_byte=sub, fbl=fbl, raw_response=bytes(resp),
        )

    def _base_profile(self, fbl: int | None, pm: int) -> DeviceProfile:
        """Resolve the pre-quirk geometry profile: from the FBL when known,
        else from the registry ``native_resolution`` (a streaming firmware that
        volunteered no handshake is still identified by its registry row)."""
        if fbl is not None:
            return get_profile(fbl, pm)
        return DeviceProfile(*self.info.native_resolution)

    def _portrait_native(self, base: DeviceProfile) -> DeviceProfile:
        """Portrait-native profile for a self-orienting firmware: transpose the
        landscape rotate profile to its true portrait raster and drop the
        pre-rotate, so the render composes upright and ships the raw raster the
        panel expects (#228).  A no-op unless the ``portrait_native`` quirk is
        set."""
        if not self._quirks.portrait_native:
            return base
        w, h = base.resolution
        return dataclasses.replace(
            base, width=min(w, h), height=max(w, h), rotate=False,
        )

    def _prepare_frame(self, payload: bytes) -> bytes:
        """Build the type-specific packet (RGB565 or JPEG bytes inside)."""
        if self.info.device_type == 2:
            packet = self._build_frame_type2(payload)
        else:
            packet = self._build_frame_type3(payload)
        frame_log.debug("HidLcd %s (type %d): sending %d-byte packet",
                  self.info.key, self.info.device_type, len(packet))
        return packet

    def _write_frame(self, frame: bytes) -> bool:
        """Type 2 streams 512-byte reports; Type 3 writes one blob + reads ACK."""
        timeout = _frame_timeout_ms(len(frame))
        if self.info.device_type == 2:
            # The C# ThreadSendDeviceData2 (hidList2) delivers the picture
            # as a sequence of 512-byte writes, never one blob — the
            # firmware reads fixed-size reports and only latches the frame
            # when it arrives that way. A single bulk write of the whole
            # frame handshakes clean and reports every byte transferred,
            # yet the panel stays on its boot logo (#150). The packet is
            # 512-aligned, so every chunk is exactly full. Slice through a
            # memoryview so the chunking is zero-copy — this runs ~300×
            # per frame at video framerate.
            view = memoryview(frame)
            for offset in range(0, len(frame), _USB_BULK_ALIGNMENT):
                chunk = view[offset:offset + _USB_BULK_ALIGNMENT]
                # A SHORT write (< chunk) is the real failure.  On Windows
                # the HID driver prepends a 1-byte Report ID, so a full
                # 512-byte write reports 513 transferred — that's NOT short,
                # it's the whole chunk plus the report byte, so ``>=`` passes
                # it (an ``!= len`` check wrongly failed every Windows HID
                # Type-2 frame with "short chunk write", #240).
                if self._transport.write(self._EP_WRITE, chunk, timeout) < len(chunk):
                    log.warning("HidLcd %s: short chunk write at offset %d",
                                self.info.key, offset)
                    return False
            time.sleep(_DELAY_FRAME_TYPE2_S)
            return True
        transferred = self._transport.write(self._EP_WRITE, frame, timeout)
        if transferred == 0:
            log.warning("HidLcd %s: write returned 0 transferred", self.info.key)
            return False
        ack = self._transport.read(
            self._EP_READ, _f5.ACK_SIZE, _DEFAULT_FRAME_TIMEOUT_MS,
        )
        if not ack:
            log.warning("HidLcd %s: Type 3 ACK read returned empty", self.info.key)
        return len(ack) > 0

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
        """Type 2: resp[0:4] == DA DB DC DD && resp[12] == 0x01.

        A ``short_handshake`` firmware answers with fewer than 20 bytes (e.g.
        the 8-byte ``da db dc dd 00 3a 00 00`` of the Frozen Warframe SE 4.07,
        #228) — the magic alone is enough to accept it; PM/SUB still parse from
        [5]/[4].
        """
        if not (len(resp) >= 6 and resp[0:4] == _TYPE2_MAGIC):
            return False
        if self._quirks.short_handshake:
            return True
        return len(resp) >= 20 and resp[12] == 0x01

    def _parse_response_type2(self, resp: bytes) -> HandshakeResult:
        """Type 2: PM at resp[5], SUB at resp[4], optional serial at resp[20:36].

        Geometry comes from the PM byte via ``pm_to_fbl`` + ``get_profile``
        — a device that reports e.g. PM=58 surfaces as 320×240, not the
        registry's static native_resolution.

        The portrait-native transpose is deliberately NOT applied here.  It
        describes one self-orienting streaming firmware, and a panel earns it
        by *volunteering* the short reply that proves it is that firmware —
        see :meth:`_connect_streaming_firmware`.  Applying it on this path
        instead keyed it on ``bcdDevice`` alone, which several different
        panels share: a 1280×480 Trofeo Vision answering PM=128 was transposed
        to 480×1280 and never displayed (#244/#268).
        """
        pm = resp[5]
        sub = resp[4]
        has_serial = len(resp) > 36 and resp[16] == 0x10
        serial = resp[20:36].hex().upper() if has_serial else ""
        fbl = pm_to_fbl(pm, sub)
        self._profile = get_profile(fbl, pm)
        return HandshakeResult(
            resolution=self._profile.resolution,
            model_id=pm,
            serial=serial,
            pm_byte=pm,
            sub_byte=sub,
            fbl=fbl,
            raw_response=bytes(resp),
        )

    def _build_frame_type2(self, image_data: bytes) -> bytes:
        """Type 2 frame: 20-byte header + image data, 512-aligned.

        Mode detection:
            JPEG (FF D8 magic) → header byte[6]=0x00, actual w×h in bytes[8:12]
            RGB565             → header byte[6]=0x01, hardcoded 240×320

        For JPEG, the header carries the device's PM-derived resolution
        (cached in ``self._profile`` at handshake). Pre-handshake callers
        fall back to ``info.native_resolution`` so smoke tests that build
        frames before connect() still produce a valid header.
        """
        is_jpeg = len(image_data) >= 2 and image_data[:2] == b'\xff\xd8'
        w, h = (self._profile.resolution if self._profile is not None
                else self.info.native_resolution)

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
        return _f5.init_packet()

    def _validate_response_type3(self, resp: bytes) -> bool:
        """Type 3: ``resp[0]`` is a known F5 identity and len >= 14.

        Reads ``_f5.VALID_IDENTITY`` rather than respelling it.  The pair used to
        be a literal here while the shared constant sat unused — the exact
        duplication ``_f5`` exists to prevent — so widening the set there did not
        widen it here.
        """
        return len(resp) >= 14 and resp[0] in _f5.VALID_IDENTITY

    def _parse_response_type3(self, resp: bytes) -> HandshakeResult:
        """Type 3: FBL = resp[0] - 1 (0x65→100, 0x66→101); serial at resp[10:14].

        Geometry comes from the FBL via ``get_profile`` (PM=FBL for Type 3).
        """
        serial = resp[10:14].hex().upper()
        fbl = resp[0] - 1
        # Sized from the identity at handshake, as the vendor does — a panel
        # answering 0x36 takes a 320x240 canvas, not the 320x320 default.
        self._f5_payload = _f5.payload_size(resp[0])
        self._profile = get_profile(fbl, fbl)
        return HandshakeResult(
            resolution=self._profile.resolution,
            model_id=fbl,
            serial=serial,
            pm_byte=fbl,
            sub_byte=0,
            fbl=fbl,
            raw_response=bytes(resp),
        )

    def _build_frame_type3(self, image_data: bytes) -> bytes:
        """Type 3 frame: 16-byte prefix + exactly this panel's payload size."""
        size = getattr(self, "_f5_payload", _f5.DATA_SIZE)
        prefix = _f5.frame_header(size)
        if len(image_data) < size:
            payload = image_data + b'\x00' * (size - len(image_data))
        else:
            payload = image_data[:size]
        return prefix + payload

    # ── Type-dispatching helpers ──────────────────────────────────────

    def _build_init_packet(self) -> bytes:
        return (self._build_init_packet_type2()
                if self.info.device_type == 2
                else self._build_init_packet_type3())

    def _response_size(self) -> int:
        return (_TYPE2_RESPONSE_SIZE
                if self.info.device_type == 2
                else _f5.RESPONSE_SIZE)

    def _validate_response(self, resp: bytes) -> bool:
        return (self._validate_response_type2(resp)
                if self.info.device_type == 2
                else self._validate_response_type3(resp))

    def _parse_response(self, resp: bytes) -> HandshakeResult:
        return (self._parse_response_type2(resp)
                if self.info.device_type == 2
                else self._parse_response_type3(resp))
