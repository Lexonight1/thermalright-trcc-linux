"""MockPlatform — a Platform with scripted USB, for the multi-device dev mock.

The cutover dropped legacy's multi-device mock: ``dev/mock_gui.py`` now only
shows physically-plugged hardware (``FakePlatform.scan_devices()`` returns
``[]``).  This restores it — a ``Platform`` that surfaces a simulated device
*fleet* from specs (``dev/devices.json``) with scripted handshakes, so the real
GUI can render any device (the #136 portrait panels, widescreen, LED, several at
once) with **zero hardware**.

It subclasses the conftest ``FakePlatform`` — the same DI flow production uses,
never a duck-typed mock (per CLAUDE.md's MockPlatform rule) — inheriting the
non-USB surface (paths / sensors / autostart / hotplug / setup / …).  It
overrides only the USB seam:

  * :meth:`scan_devices` → one ``DeviceInfo`` per spec (the registry resolves
    the wire from vid/pid, so a spec only needs vid/pid + handshake bytes).
  * :meth:`open_scsi` / :meth:`open_bulk` → a **fresh** transport per device,
    pre-loaded with a scripted handshake reply so the device's own
    ``connect()`` resolves the spec's geometry.  (``FakePlatform`` hands back a
    single *shared* transport — fine for one device, wrong for a fleet.)

The per-wire handshake byte-builders live here as the single source of truth;
the geometry tests currently script the same bytes inline (a later DRY pass can
point them at these helpers).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from trcc.adapters.device.hid_lcd import (
    _TYPE2_MAGIC,
    _TYPE2_RESPONSE_SIZE,
    _TYPE3_RESPONSE_SIZE,
)
from trcc.adapters.device.led import _HID_REPORT_SIZE, _MAGIC
from trcc.adapters.device.ly_lcd import _PID_LY
from trcc.core.models import DeviceInfo, ProductInfo, Wire
from trcc.core.ports import BulkTransport, ScsiTransport, SensorEnumerator
from trcc.core.protocol import get_profile, pm_to_fbl
from trcc.core.registry import find_product

from .conftest import FakeBulkTransport, FakePlatform, FakeScsiTransport

log = logging.getLogger(__name__)


# ── Scripted handshake byte-builders (single source; mirror the wire) ────────


def scsi_poll_reply(fbl: int, *, size: int = 0xE100) -> bytes:
    """SCSI poll reply — ``ScsiLcd.connect`` reads byte 0 as the FBL code."""
    resp = bytearray(size)
    resp[0] = fbl
    return bytes(resp)


def bulk_handshake_reply(pm: int, sub: int = 0, *, size: int = 1024) -> bytes:
    """USBLCDNew bulk reply — PM at ``resp[24]``, SUB at ``resp[36]``.

    The validator requires ``len >= 41`` and ``resp[24] != 0``.
    """
    resp = bytearray(size)
    resp[24] = pm
    resp[36] = sub
    return bytes(resp)


def led_handshake_reply(pm: int, sub: int = 0) -> bytes:
    """LED handshake reply — magic at [0:4], SUB at [4], PM at [5], cmd ACK [12].

    Mirrors Windows ``DeviceDataReceived1`` (see ``Led.connect``); 64 bytes.
    """
    resp = bytearray(_HID_REPORT_SIZE)
    resp[0:4] = _MAGIC
    resp[4] = sub
    resp[5] = pm
    resp[12] = 1
    return bytes(resp)


def hid_type2_reply(pm: int, sub: int = 0) -> bytes:
    """HID Type-2 reply — magic [0:4], SUB [4], PM [5], cmd ACK [12].

    Mirrors ``HidLcd._validate_response_type2`` (magic + ``[12]==0x01``) +
    ``_parse_response_type2`` (``pm=[5] sub=[4]``).  No serial block —
    ``[16]`` stays 0 so ``has_serial`` is False.
    """
    resp = bytearray(_TYPE2_RESPONSE_SIZE)
    resp[0:4] = _TYPE2_MAGIC
    resp[4] = sub
    resp[5] = pm
    resp[12] = 0x01
    return bytes(resp)


def hid_type3_reply(fbl: int) -> bytes:
    """HID Type-3 reply — FBL encoded at ``[0]`` (= ``fbl + 1``).

    Mirrors ``HidLcd._validate_response_type3`` (``[0] ∈ {0x65, 0x66}``,
    i.e. FBL 100/101) + ``_parse_response_type3`` (``fbl = [0] - 1``).
    """
    resp = bytearray(_TYPE3_RESPONSE_SIZE)
    resp[0] = (fbl + 1) & 0xFF
    return bytes(resp)


def ly_reply(pm: int, sub: int = 0, *, is_ly1: bool = False,
             size: int = 64) -> bytes:
    """LY / LY1 reply — header ``[0]=3 [1]=0xFF [8]=1``; PM/SUB inverted.

    Mirrors ``LyLcd.connect``: the LY pid derives ``pm = 64 + [20]`` and
    ``sub = [22] + 1``; LY1 derives ``pm = 50 + [36]`` and ``sub = [22]``.
    We invert so ``connect()`` recovers the model's PM/SUB.  (For
    ``pm - 64 <= 3`` the device clamps to ``pm = 65`` — its real behaviour,
    mirrored faithfully.)
    """
    resp = bytearray(size)
    resp[0] = 3
    resp[1] = 0xFF
    resp[8] = 1
    if is_ly1:
        resp[36] = max(0, pm - 50) & 0xFF
        resp[22] = sub & 0xFF
    else:
        resp[20] = max(0, pm - 64) & 0xFF
        resp[22] = max(0, sub - 1) & 0xFF
    return bytes(resp)


def mock_handshake(product: ProductInfo, *, pm: int, sub: int, fbl: int) -> bytes:
    """The bytes ``<device>.connect()`` reads back — derived from our models.

    One source of truth that mirrors every adapter's handshake parser, so a
    vid/pid (→ ``ProductInfo``) plus the registry's ``fbl`` is enough to
    simulate ANY device's handshake with zero hardware.  ``pm`` defaults to
    ``fbl`` at the call site (the ``pm_to_fbl`` PM==FBL convention); only the
    few PM≠FBL panels (or FBL-224 widescreen disambiguation) need a
    ``devices.json`` override.
    """
    wire = product.wire
    if wire is Wire.SCSI:
        return scsi_poll_reply(fbl)
    if wire is Wire.LED:
        return led_handshake_reply(pm, sub)
    if wire is Wire.HID:
        return (hid_type2_reply(pm, sub) if product.device_type == 2
                else hid_type3_reply(fbl))
    if wire is Wire.LY:
        return ly_reply(pm, sub, is_ly1=(product.pid != _PID_LY))
    return bulk_handshake_reply(pm, sub)  # BULK + synthesized fallback


# ── Model-driven geometry resolver (faithful native_resolution) ──────────────


def resolve_handshake_geometry(product: ProductInfo) -> tuple[int, int, int]:
    """``(pm, sub, fbl)`` whose model profile reproduces ``native_resolution``.

    Faithful by construction: the simulated device resolves through
    ``connect()``'s own ``pm_to_fbl`` / ``get_profile`` to its registry-declared
    geometry.  We honour the recorded ``fbl`` (the device's identity — a
    320×320 panel must report FBL 100, not the FBL-0 default that brute PM
    search would pick) and only scan PM-space to DISAMBIGUATE the shared FBL
    192/224 codes or to recover the PM for a panel that records no ``fbl``
    (e.g. HID Type 2, whose 240×320 is FBL 50/51/53's 320×240 + rotate).

    Returns the recorded ``fbl`` with ``pm==fbl`` when nothing matches (LED
    segment displays report ``native_resolution=(0, 0)`` and resolve here).
    """
    native = product.native_resolution
    want = product.fbl
    prot_log = logging.getLogger("trcc.core.protocol")
    prev = prot_log.level
    prot_log.setLevel(logging.WARNING)  # silence the per-candidate INFO lines
    try:
        for pm in range(256):
            fbl = pm_to_fbl(pm)
            if want is not None and fbl != want:
                continue
            prof = get_profile(fbl, pm)
            w, h = prof.resolution
            if (w, h) == native or (prof.rotate and (h, w) == native):
                return pm, 0, fbl
    finally:
        prot_log.setLevel(prev)
    fbl = want or 100
    return fbl, 0, fbl


# ── Device spec ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """One simulated device, parsed from a ``dev/devices.json`` entry.

    ``pm`` / ``sub`` drive the scripted handshake for bulk + LED wires; ``fbl``
    (optional) overrides the SCSI poll byte when the registry default isn't the
    panel we want to simulate.  ``resolution`` / ``name`` are informational.
    """
    vid: int
    pid: int
    name: str
    pm: int = 0
    sub: int = 0
    fbl: int | None = None
    resolution: str | None = None

    @classmethod
    def parse(cls, raw: dict) -> DeviceSpec:
        """Build a spec from a raw JSON dict (vid/pid are hex strings)."""
        vid = int(str(raw["vid"]), 16)
        pid = int(str(raw["pid"]), 16)
        name = str(raw.get("name", f"{vid:04x}:{pid:04x}"))
        fbl = raw.get("fbl")
        return cls(
            vid=vid, pid=pid, name=name,
            pm=int(raw.get("pm", 0)),
            sub=int(raw.get("sub", 0)),
            fbl=int(fbl) if fbl is not None else None,
            resolution=raw.get("resolution"),
        )

    @property
    def key(self) -> tuple[int, int]:
        return (self.vid, self.pid)


# ── MockPlatform ─────────────────────────────────────────────────────────────


class MockPlatform(FakePlatform):
    """``FakePlatform`` + scripted multi-device USB.

    ``root`` is the data root the GUI writes to (``dev/.trcc`` for the mock GUI,
    a tmp dir for unit tests); it flows through ``FakePlatform`` → ``FakePaths``.
    """

    def __init__(self, specs: list[dict], root: Path) -> None:
        super().__init__(root)
        self._specs: list[DeviceSpec] = [DeviceSpec.parse(s) for s in specs]
        self._by_key: dict[tuple[int, int], DeviceSpec] = {
            s.key: s for s in self._specs
        }
        log.info("MockPlatform: %d spec(s) loaded, root=%s",
                 len(self._specs), root)

    def sensors(self) -> SensorEnumerator:
        """REAL host sensors — the mock fakes ONLY the USB handshake.

        The whole point of the multi-device mock is "a vid/pid + a scripted
        handshake stands in for the panel"; everything else is the live
        application.  So the overlay + System-Info show THIS dev computer's
        actual CPU/GPU/memory/fan metrics, not the deterministic fakes
        ``FakePlatform`` hands to unit tests.  Built the same way the real
        ``LinuxPlatform.sensors`` builds it (``build_linux_sensors``).
        """
        if self._sensors is None:
            from trcc.adapters.sensors.aggregator import build_linux_sensors
            log.info("MockPlatform.sensors: REAL host sensors (dev box)")
            self._sensors = build_linux_sensors()
        return self._sensors

    def scan_devices(self) -> list[DeviceInfo]:
        """Surface one ``DeviceInfo`` per spec that resolves in the registry."""
        log.info("MockPlatform.scan_devices: %d spec(s)", len(self._specs))
        out: list[DeviceInfo] = []
        for spec in self._specs:
            product = find_product(spec.vid, spec.pid)
            if product is None:
                log.warning(
                    "MockPlatform.scan_devices: %04x:%04x (%s) not in registry "
                    "— skipping; add it to core/registry.py to simulate it",
                    spec.vid, spec.pid, spec.name,
                )
                continue
            out.append(DeviceInfo(vid=spec.vid, pid=spec.pid))
            log.info("MockPlatform.scan_devices: + %s [%04x:%04x] wire=%s",
                     spec.name, spec.vid, spec.pid, product.wire.value)
        return out

    def _resolve_handshake(self, vid: int, pid: int) -> bytes:
        """Model-driven handshake reply for a device — every wire, one source.

        Geometry is resolved FAITHFULLY from the registry ``ProductInfo``: the
        ``(pm, sub, fbl)`` is the one whose model profile reproduces the
        device's ``native_resolution`` (see :func:`resolve_handshake_geometry`),
        so a ``devices.json`` entry needs only a vid/pid.  ``pm`` / ``sub`` /
        ``fbl`` in the spec override that default for a panel the model
        under-specifies.
        """
        product = find_product(vid, pid)
        if product is None:
            log.warning(
                "MockPlatform: %04x:%04x not in registry — empty handshake "
                "(add it to core/registry.py to simulate it)", vid, pid,
            )
            return b""
        spec = self._by_key.get((vid, pid))
        pm, sub, fbl = resolve_handshake_geometry(product)
        if spec is not None:
            if spec.fbl is not None:
                fbl = spec.fbl
            if spec.pm:
                pm = spec.pm
            if spec.sub:
                sub = spec.sub
        reply = mock_handshake(product, pm=pm, sub=sub, fbl=fbl)
        log.info(
            "MockPlatform handshake: %04x:%04x wire=%s type=%d pm=%d sub=%d "
            "fbl=%d (%d bytes)",
            vid, pid, product.wire.value, product.device_type,
            pm, sub, fbl, len(reply),
        )
        return reply

    def open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        """Fresh SCSI transport pre-loaded with the model-driven reply."""
        transport = FakeScsiTransport()
        transport.read_script.append(self._resolve_handshake(vid, pid))
        return transport

    def open_bulk(self, vid: int, pid: int,
                  serial: str | None = None) -> BulkTransport:
        """Fresh bulk transport for every non-SCSI wire (BULK/HID/LY/LED).

        ``App.attach`` routes them all through here; ``mock_handshake`` picks
        the reply shape by the registry wire + device_type, so every wire is
        now simulated (no more warn-and-skip for HID / LY).
        """
        transport = FakeBulkTransport()
        transport.read_script.append(self._resolve_handshake(vid, pid))
        return transport
