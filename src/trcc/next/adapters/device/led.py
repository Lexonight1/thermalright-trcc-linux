"""Led — Device implementation for RGB LED controllers (FormLED equivalent).

HID 64-byte report transport.  Shares the Type 2 DA/DB/DC/DD magic with
HID LCD devices, but LED packets use cmd=2 with per-LED RGB payload.

Payload shape passed to send():
    LedPayload(colors=[(r,g,b), ...], is_on=None, global_on=True, brightness=100)

Color scaling matches FormLED.cs SendHidVal:
    scaled = channel * (brightness/100) * 0.4
"""
from __future__ import annotations

import logging
import struct
import threading
import time
from dataclasses import dataclass, field

from ...core.errors import HandshakeError, TransportError, UnsupportedOperationError
from ...core.led_protocol import resolve_pm
from ...core.models import HandshakeResult, LedHandshakeResult, ProductInfo
from ...core.ports import BulkTransport, Device

log = logging.getLogger(__name__)


# ── Wire constants ─────────────────────────────────────────────────────

_EP_WRITE = 0x02
_EP_READ = 0x81

_MAGIC = bytes([0xDA, 0xDB, 0xDC, 0xDD])
_HID_REPORT_SIZE = 64
_HEADER_SIZE = 20
_CMD_INIT = 1
_CMD_DATA = 2
_COLOR_SCALE = 0.4

_HANDSHAKE_TIMEOUT_MS = 5000
_HANDSHAKE_MAX_RETRIES = 3
_HANDSHAKE_RETRY_DELAY_S = 0.5
_DELAY_PRE_INIT_S = 0.050
_DELAY_POST_INIT_S = 0.200
_DEFAULT_TIMEOUT_MS = 100


# ── Payload shape the send() caller provides ──────────────────────────


@dataclass(frozen=True, slots=True)
class LedPayload:
    """Structured payload for Led.send().

    colors:     per-LED RGB tuples (0-255).
    is_on:      per-LED boolean mask; None means all on.
    global_on:  master switch; False turns every LED off.
    brightness: 0-100 multiplier applied before the FormLED 0.4x scale.
    """
    colors: list[tuple[int, int, int]]
    is_on: list[bool] | None = None
    global_on: bool = True
    brightness: int = 100


# ── Led Device ────────────────────────────────────────────────────────


class Led(Device[BulkTransport]):
    """RGB LED controller over HID 64-byte reports."""

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._send_lock = threading.Lock()
        self._pm: int = 0
        self._sub: int = 0
        self._led_handshake: LedHandshakeResult | None = None

    @property
    def is_led(self) -> bool:
        return True

    @property
    def led_handshake(self) -> LedHandshakeResult | None:
        """LED-specific handshake info (pm, sub_type, style)."""
        return self._led_handshake

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        log.info("Led %s: opening transport", self.info.key)
        if not self._transport.open():
            log.error("Led %s: transport open failed", self.info.key)
            raise HandshakeError(f"Failed to open USB transport for {self.info.key}")

        last_err: Exception | None = None
        init_pkt = self._build_init_packet()

        for attempt in range(1, _HANDSHAKE_MAX_RETRIES + 1):
            try:
                time.sleep(_DELAY_PRE_INIT_S)
                self._transport.write(_EP_WRITE, init_pkt, _HANDSHAKE_TIMEOUT_MS)
                time.sleep(_DELAY_POST_INIT_S)

                resp = self._transport.read(
                    _EP_READ, _HID_REPORT_SIZE, _HANDSHAKE_TIMEOUT_MS,
                )

                if len(resp) < 7:
                    last_err = HandshakeError(
                        f"Response too short ({len(resp)} bytes)"
                    )
                    time.sleep(_HANDSHAKE_RETRY_DELAY_S)
                    continue

                # Windows DeviceDataReceived1 doesn't validate magic/cmd —
                # we warn on mismatch but accept.
                if resp[0:4] != _MAGIC:
                    log.warning("Led handshake: unexpected magic %s", resp[0:4].hex())
                if len(resp) > 12 and resp[12] != 1:
                    log.warning("Led handshake: unexpected cmd byte %d", resp[12])

                # PM and SUB extraction — matches Windows UCDevice.cs offsets:
                # raw resp[5] = PM, raw resp[4] = SUB
                self._pm = resp[5]
                self._sub = resp[4]

                # PM byte → device style + readable model name. Falls back to
                # the registry's ``led_style`` / ``product`` when the firmware
                # reports a PM not in PmRegistry (e.g. a new SKU).
                entry = resolve_pm(self._pm, self._sub)
                if entry is not None:
                    style = entry.style
                    model_name = entry.model_name
                    style_sub = entry.style_sub
                else:
                    log.warning("Led handshake: unknown PM=%d (sub=%d); "
                                "falling back to registry defaults",
                                self._pm, self._sub)
                    style = self.info.led_style
                    model_name = self.info.product
                    style_sub = 0

                self._led_handshake = LedHandshakeResult(
                    pm=self._pm, sub_type=self._sub,
                    style=style,
                    model_name=model_name,
                    style_sub=style_sub,
                    raw_response=bytes(resp[:64]),
                )
                result = HandshakeResult(
                    resolution=(0, 0),        # LEDs have no screen resolution
                    model_id=self._pm,
                    pm_byte=self._pm,
                    sub_byte=self._sub,
                    raw_response=bytes(resp[:64]),
                )
                self._handshake = result
                log.info("Led handshake OK: PM=%d SUB=%d style=%s model=%s",
                         self._pm, self._sub,
                         style.value if style else "—",
                         model_name or "—")
                return result

            except Exception as e:
                last_err = e
                log.warning("Led handshake attempt %d/%d failed: %s",
                            attempt, _HANDSHAKE_MAX_RETRIES, e)
                if attempt < _HANDSHAKE_MAX_RETRIES:
                    time.sleep(_HANDSHAKE_RETRY_DELAY_S)

        raise HandshakeError(
            f"Led handshake failed after {_HANDSHAKE_MAX_RETRIES} attempts"
        ) from last_err

    def send(self, payload: LedPayload) -> bool:
        """Send one LED color update.  Payload must be a LedPayload.

        Colors arrive in logical (UI / segment-display) order; the
        per-style wire-remap table reorders them to physical-wire order
        before the packet is built. Styles without a remap table (and
        the pre-handshake / unknown-PM cases) pass through unchanged.
        """
        if not isinstance(payload, LedPayload):
            log.error("Led %s: send() got %s, expected LedPayload",
                      self.info.key, type(payload).__name__)
            raise UnsupportedOperationError(
                "Led.send() requires a LedPayload; "
                f"got {type(payload).__name__}"
            )
        if not self._transport.is_open:
            log.error("Led %s: send() called before connect()", self.info.key)
            raise TransportError(
                f"Led {self.info.key} not connected — call connect() first"
            )

        # Apply wire-order remap using the handshake-resolved style.
        # Both colors AND the optional is_on mask arrive in logical
        # (UI / segment-display) order — the per-style table reorders
        # both to physical-wire order before the packet is built. Skip
        # the work when style is unknown or no table applies (identity
        # passthrough).
        if self._led_handshake is not None and payload.colors:
            from ...services.led_segment import remap_led_colors
            style = self._led_handshake.style
            style_sub = self._led_handshake.style_sub
            remapped_colors = remap_led_colors(payload.colors, style, style_sub)
            remapped_is_on: list[bool] | None = payload.is_on
            if remapped_colors is not payload.colors and payload.is_on is not None:
                # Reuse the remap function by lifting booleans into a
                # color-shaped sequence so the same table applies.
                colored_mask = [(1, 0, 0) if v else (0, 0, 0) for v in payload.is_on]
                physical_mask = remap_led_colors(colored_mask, style, style_sub)
                remapped_is_on = [c[0] == 1 for c in physical_mask]
            if remapped_colors is not payload.colors:
                payload = LedPayload(
                    colors=remapped_colors,
                    is_on=remapped_is_on,
                    global_on=payload.global_on,
                    brightness=payload.brightness,
                )

        packet = self._build_packet(payload)
        log.debug("Led %s: sending %d-byte packet (%d colors)",
                  self.info.key, len(packet), len(payload.colors))

        if not self._send_lock.acquire(blocking=False):
            log.debug("Led %s: already sending — skipped", self.info.key)
            return False

        try:
            remaining = len(packet)
            offset = 0
            while remaining > 0:
                chunk_size = min(remaining, _HID_REPORT_SIZE)
                chunk = packet[offset:offset + chunk_size]
                if len(chunk) < _HID_REPORT_SIZE:
                    chunk = chunk + b"\x00" * (_HID_REPORT_SIZE - len(chunk))
                self._transport.write(_EP_WRITE, chunk, _DEFAULT_TIMEOUT_MS)
                remaining -= chunk_size
                offset += chunk_size
            return True
        except TransportError:
            log.exception("Led send failed")
            return False
        finally:
            self._send_lock.release()

    def disconnect(self) -> None:
        log.info("Led %s: disconnecting", self.info.key)
        self._transport.close()
        self._handshake = None
        self._led_handshake = None

    # ── Packet builders ───────────────────────────────────────────────

    @staticmethod
    def _build_init_packet() -> bytes:
        header = bytearray(_HID_REPORT_SIZE)
        header[0:4] = _MAGIC
        header[12] = _CMD_INIT
        return bytes(header)

    @staticmethod
    def _build_header(payload_length: int) -> bytes:
        header = bytearray(_HEADER_SIZE)
        header[0:4] = _MAGIC
        header[12] = _CMD_DATA
        struct.pack_into("<H", header, 16, payload_length)
        return bytes(header)

    @classmethod
    def _build_packet(cls, payload: LedPayload) -> bytes:
        count = len(payload.colors)
        payload_len = count * 3
        header = cls._build_header(payload_len)

        brightness = max(0, min(100, payload.brightness)) / 100.0
        body = bytearray(payload_len)
        for i, (r, g, b) in enumerate(payload.colors):
            on = payload.global_on and (
                payload.is_on[i] if payload.is_on is not None else True
            )
            if on:
                body[i * 3] = min(255, max(0, int(r * brightness * _COLOR_SCALE)))
                body[i * 3 + 1] = min(255, max(0, int(g * brightness * _COLOR_SCALE)))
                body[i * 3 + 2] = min(255, max(0, int(b * brightness * _COLOR_SCALE)))
            # else: stays 0,0,0 (off)
        return header + bytes(body)


# `field` kept visible for future `LedPayload` extensions
_ = field
