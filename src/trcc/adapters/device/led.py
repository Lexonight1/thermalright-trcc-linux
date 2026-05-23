"""Led — Device implementation for RGB LED controllers (FormLED equivalent).

HID 64-byte report transport.  Shares the Type 2 DA/DB/DC/DD magic with
HID LCD devices, but LED packets use cmd=2 with per-LED RGB payload.

Payload shape passed to send():
    LedPayload(colors=[(r,g,b), ...], is_on=None, global_on=True, brightness=100)

Color scaling matches FormLED.cs SendHidVal:
    scaled = channel * (brightness/100) * 0.4
"""
from __future__ import annotations

import json
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

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


# =========================================================================
# LED probe cache — survives app restart on the same power cycle
# =========================================================================
# The LED firmware only responds to the HID handshake ONCE per power
# cycle.  After that, re-running the handshake returns garbage (or
# times out) — the device has already published its identity and won't
# repeat itself until the next power-on.
#
# Without a cache, the second app launch after a successful first run
# would get garbage and either crash or fall back to defaults.  A
# disk-backed cache keyed by (VID, PID, usb_path) lets the second
# launch skip the handshake and use the cached PM/SUB.
#
# Cache file: ``~/.trcc/led_probe_cache.json`` — matches legacy layout
# byte-for-byte so installs that migrated from legacy keep the cached
# entries.

_PROBE_CACHE_PATH = Path.home() / ".trcc" / "led_probe_cache.json"


def _probe_cache_key(vid: int, pid: int, usb_path: str = "") -> str:
    """Cache key: ``<vid>_<pid>_<usb_path>`` (path optional).

    The usb_path component disambiguates multiple identical-VID/PID
    devices on different bus positions.  Caller looks up the
    specific path first, falls back to the VID/PID-only key.
    """
    if usb_path:
        return f"{vid:04x}_{pid:04x}_{usb_path}"
    return f"{vid:04x}_{pid:04x}"


def _probe_cache_save(
    vid: int, pid: int,
    pm: int, sub: int, model_name: str,
    *, usb_path: str = "",
) -> None:
    """Persist a successful handshake result to the on-disk cache.

    Best-effort — log on failure, never raise.  Caller (LED Device
    connect) calls this after a successful handshake so the next
    same-power-cycle restart skips the now-broken handshake step.
    """
    try:
        _PROBE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache: dict[str, dict[str, object]] = {}
        if _PROBE_CACHE_PATH.is_file():
            try:
                cache = json.loads(
                    _PROBE_CACHE_PATH.read_text(encoding="utf-8"),
                )
            except (OSError, ValueError):
                log.debug("probe cache: corrupt at %s, rewriting fresh",
                          _PROBE_CACHE_PATH)
                cache = {}
        cache[_probe_cache_key(vid, pid, usb_path)] = {
            "pm": pm, "sub": sub, "model_name": model_name,
        }
        _PROBE_CACHE_PATH.write_text(
            json.dumps(cache, indent=2) + "\n", encoding="utf-8",
        )
        log.info("probe cache: saved %04x:%04x pm=%d sub=%d → %s",
                 vid, pid, pm, sub, _PROBE_CACHE_PATH)
    except OSError as e:
        log.warning("probe cache: save failed for %04x:%04x: %s: %s",
                    vid, pid, type(e).__name__, e)


def _probe_cache_load(
    vid: int, pid: int, *, usb_path: str = "",
) -> tuple[int, int, str] | None:
    """Return cached ``(pm, sub, model_name)`` for this device or None.

    Looks up the usb_path-specific key first; falls back to the
    VID/PID-only key so a device that was originally cached without
    a bus path still resolves.
    """
    try:
        if not _PROBE_CACHE_PATH.is_file():
            return None
        cache = json.loads(_PROBE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.debug("probe cache: load failed: %s: %s",
                  type(e).__name__, e)
        return None
    entry = cache.get(_probe_cache_key(vid, pid, usb_path))
    if entry is None and usb_path:
        entry = cache.get(_probe_cache_key(vid, pid))
    if entry is None:
        return None
    try:
        return (
            int(entry["pm"]),
            int(entry["sub"]),
            str(entry["model_name"]),
        )
    except (KeyError, ValueError, TypeError):
        log.debug("probe cache: entry malformed for %04x:%04x", vid, pid)
        return None


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
                # Cache the result so a second app launch on the same
                # power cycle can skip the now-broken handshake.  The
                # LED firmware only answers the HID handshake once per
                # power-on; subsequent handshakes return garbage.
                _probe_cache_save(
                    self.info.vid, self.info.pid,
                    self._pm, self._sub, model_name,
                )
                return result

            except Exception as e:
                last_err = e
                log.warning("Led handshake attempt %d/%d failed: %s",
                            attempt, _HANDSHAKE_MAX_RETRIES, e)
                if attempt < _HANDSHAKE_MAX_RETRIES:
                    time.sleep(_HANDSHAKE_RETRY_DELAY_S)

        # All live handshake attempts exhausted — the firmware likely
        # already published its identity earlier in this power cycle.
        # Fall back to the disk cache (if any) and reconstruct the
        # handshake result from it so the device is usable instead of
        # crashing out.
        cached = _probe_cache_load(self.info.vid, self.info.pid)
        if cached is not None:
            cpm, csub, cname = cached
            entry = resolve_pm(cpm, csub)
            log.warning(
                "Led handshake: live failed after %d retries — using "
                "disk cache (PM=%d SUB=%d model=%s, last_err=%s)",
                _HANDSHAKE_MAX_RETRIES, cpm, csub, cname, last_err,
            )
            self._pm = cpm
            self._sub = csub
            self._led_handshake = LedHandshakeResult(
                pm=cpm, sub_type=csub,
                style=entry.style if entry else self.info.led_style,
                model_name=cname,
                style_sub=entry.style_sub if entry else 0,
                raw_response=b"",
            )
            result = HandshakeResult(
                resolution=(0, 0),
                model_id=cpm,
                pm_byte=cpm,
                sub_byte=csub,
                raw_response=b"",
            )
            self._handshake = result
            return result

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
