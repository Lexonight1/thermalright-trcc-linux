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

from trcc.adapters.device.led import _HID_REPORT_SIZE, _MAGIC
from trcc.core.models import DeviceInfo, Wire
from trcc.core.ports import BulkTransport, ScsiTransport
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

    def open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        """Fresh SCSI transport scripted so ``connect()`` resolves the FBL."""
        spec = self._by_key.get((vid, pid))
        product = find_product(vid, pid)
        fbl = (
            (spec.fbl if spec and spec.fbl is not None else None)
            or (product.fbl if product else None)
            or 100
        )
        transport = FakeScsiTransport()
        transport.read_script.append(scsi_poll_reply(fbl))
        log.info("MockPlatform.open_scsi: %04x:%04x scripted fbl=%d",
                 vid, pid, fbl)
        return transport

    def open_bulk(self, vid: int, pid: int,
                  serial: str | None = None) -> BulkTransport:
        """Fresh bulk transport scripted per wire.

        ``App.attach`` routes every non-SCSI wire (BULK / HID / LY / LED)
        through ``open_bulk``, so the reply shape is chosen by the registry
        wire.  HID / LY handshakes have their own formats — not scripted yet;
        they get a warning so an unsupported spec degrades loudly.
        """
        spec = self._by_key.get((vid, pid))
        product = find_product(vid, pid)
        wire = product.wire if product else None
        pm = spec.pm if spec else 0
        sub = spec.sub if spec else 0
        transport = FakeBulkTransport()
        if wire is Wire.LED:
            transport.read_script.append(led_handshake_reply(pm, sub))
            log.info("MockPlatform.open_bulk: %04x:%04x LED scripted pm=%d sub=%d",
                     vid, pid, pm, sub)
        elif wire is Wire.HID or wire is Wire.LY:
            log.warning(
                "MockPlatform.open_bulk: %04x:%04x wire=%s not scripted yet — "
                "handshake will fail; only SCSI/BULK/LED are simulated so far",
                vid, pid, wire.value,
            )
        else:  # BULK (and the synthesized fallback)
            transport.read_script.append(bulk_handshake_reply(pm, sub))
            log.info("MockPlatform.open_bulk: %04x:%04x BULK scripted pm=%d sub=%d",
                     vid, pid, pm, sub)
        return transport
