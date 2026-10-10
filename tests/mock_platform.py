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
from dataclasses import dataclass, replace
from pathlib import Path

from trcc.adapters.device import _f5
from trcc.adapters.device.hid_lcd import (
    _TYPE2_MAGIC,
    _TYPE2_RESPONSE_SIZE,
)
from trcc.adapters.device.led import _HID_REPORT_SIZE, _MAGIC
from trcc.adapters.device.ly_lcd import _PID_LY
from trcc.adapters.rgb.corsair_dram import crc8
from trcc.adapters.system._base import disambiguate
from trcc.core.models import (
    DeviceInfo,
    ProductInfo,
    RamAccessState,
    RamAccessStatus,
    Wire,
)
from trcc.core.ports import (
    RamAccess,
    SensorEnumerator,
    SmBus,
    Transport,
)
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
    resp = bytearray(_f5.RESPONSE_SIZE)
    resp[0] = (fbl + 1) & 0xFF
    return bytes(resp)


def ly_reply(pm: int, sub: int = 0, *, is_ly1: bool = False,
             size: int = 64) -> bytes:
    """LY / LY1 reply — header ``[0]=3 [1]=0xFF [8]=1``; PM/SUB inverted.

    Inverted from the VENDOR's packing, not from our adapter.  Both wires read
    PM from reply byte 20 and SUB from byte 22 (``DCReadWriteAsync.cs:967`` and
    ``:1220``): LY publishes ``64 + [20]`` / ``1 + [22]``, LY1 publishes
    ``49 + [20]`` / ``[22]``.  (For ``pm - 64 <= 3`` the device clamps to
    ``pm = 65`` — its real behaviour, mirrored faithfully.)

    This docstring used to say "Mirrors ``LyLcd.connect``", and it did: it
    encoded ``resp[36] = pm - 50`` because that is what the adapter decoded,
    so the geometry test confirmed the adapter against itself and agreed with a
    defect for as long as it existed.  A harness that restates the
    implementation cannot fail it — invert the ORACLE.
    """
    resp = bytearray(size)
    resp[0] = 3
    resp[1] = 0xFF
    resp[8] = 1
    if is_ly1:
        resp[20] = max(0, pm - 49) & 0xFF
        resp[22] = sub & 0xFF
    else:
        resp[20] = max(0, pm - 64) & 0xFF
        resp[22] = max(0, sub - 1) & 0xFF
    return bytes(resp)


def ali_handshake_reply(*, size: int = _f5.RESPONSE_SIZE) -> bytes:
    """F5-protocol reply — identity at ``resp[0]``, for HID type-3 panels.

    The identity bytes and buffer size come from ``_f5``, not from a copy: this
    docstring used to restate the rule ("accepts ``resp[0] ∈ {101, 102}``") and
    hardcode ``size=1024``, which is how a harness drifts from the code it is
    meant to stand in for — it has produced a false bug report here before.
    ``_f5.VALID_IDENTITY[0]`` is 0x65 (101) → ``model_id = 100``.

    No PM/SUB — this device has a fixed 320x320 RGB565 canvas.
    """
    resp = bytearray(size)
    resp[0] = _f5.VALID_IDENTITY[0]
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
    # CRITICAL, not WARNING.  Brute-forcing PM space asks ``get_profile``
    # about FBLs no device reports, and each miss is a WARNING, not an INFO --
    # 220 of them, ~64 KB, once a panel's registry row stops declaring an
    # ``fbl`` and the skip above stops firing (0416:5302, 902620dd).  That
    # overshot the 64 KB pipe buffer of any harness capturing this process
    # and deadlocked it BEFORE it could bind.  A candidate probe is not a
    # diagnosis, so none of the sweep's output is worth a record.
    prot_log.setLevel(logging.CRITICAL)
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


def _parse_reply_hex(raw: object, name: str) -> bytes | None:
    """A ``devices.json`` ``reply`` hex string → bytes, or ``None`` if absent.

    Spaces and colons are stripped so a capture can be pasted in whatever shape
    the tool that produced it emits.  A malformed string is a WARNING and falls
    back to the normal scripted reply rather than raising: a typo in one fixture
    row must not take the whole mock fleet down.
    """
    if raw is None:
        return None
    text = str(raw).replace(":", "").replace(" ", "").replace("\n", "")
    try:
        reply = bytes.fromhex(text)
    except ValueError:
        log.warning("mock spec %s: reply=%r is not valid hex — ignoring it and "
                    "falling back to the model-derived handshake", name, raw)
        return None
    log.info("mock spec %s: verbatim %d-byte reply supplied", name, len(reply))
    return reply



@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """One simulated device, parsed from a ``dev/devices.json`` entry.

    ``pm`` / ``sub`` drive the scripted handshake for bulk + LED wires; ``fbl``
    (optional) overrides the SCSI poll byte when the registry default isn't the
    panel we want to simulate.  ``resolution`` / ``name`` are informational.

    ``bcd`` is the USB ``bcdDevice`` (firmware revision).  It is what
    ``DEVICE_QUIRKS`` is keyed on, so without it no simulated device could ever
    take a quirked path — the five #228 divergences (HID output reports, skipped
    init, short handshake, portrait-native, keepalive) were unreachable in the
    mock, which is how a quirk that broke four reporters' panels shipped
    unnoticed (#244).  Give a spec ``"bcd": "0407"`` to simulate that firmware.

    ``reply`` is a REPORTER'S ACTUAL HANDSHAKE BYTES, used verbatim.

    Every other field is a *value* we then pack per our own understanding of the
    wire, and the default geometry is brute-forced through the app's own
    ``pm_to_fbl`` / ``get_profile`` until it reproduces the registry's declared
    resolution.  That is faithful for "show me every SKU we believe in" — and
    structurally unable to reproduce "this cooler does not behave the way we
    believe", which is the shape of every reporter bug.  A device whose real
    reply disagrees with our tables cannot be expressed as (pm, sub, fbl) at
    all, because the disagreement may be the byte OFFSETS, the length, or a
    field we do not model.

    So this field takes bytes and asks no questions: paste what the device
    actually said, and the real adapters parse it locally.  Hex string in
    ``devices.json``, spaces and colons ignored::

        {"vid": "0416", "pid": "5302", "reply": "03 ff 00 ... 01"}
    """
    vid: int
    pid: int
    name: str
    pm: int = 0
    sub: int = 0
    fbl: int | None = None
    bcd: int = 0
    resolution: str | None = None
    reply: bytes | None = None

    @classmethod
    def parse(cls, raw: dict) -> DeviceSpec:
        """Build a spec from a raw JSON dict (vid/pid are hex strings)."""
        vid = int(str(raw["vid"]), 16)
        pid = int(str(raw["pid"]), 16)
        name = str(raw.get("name", f"{vid:04x}:{pid:04x}"))
        fbl = raw.get("fbl")
        bcd = raw.get("bcd")
        reply = raw.get("reply")
        return cls(
            vid=vid, pid=pid, name=name,
            pm=int(raw.get("pm", 0)),
            sub=int(raw.get("sub", 0)),
            fbl=int(fbl) if fbl is not None else None,
            bcd=int(str(bcd), 16) if bcd is not None else 0,
            resolution=raw.get("resolution"),
            reply=_parse_reply_hex(reply, name),
        )

    @property
    def key(self) -> tuple[int, int]:
        return (self.vid, self.pid)


# ── Scripted-USB surface (shared: tests' MockPlatform + dev's DevMockPlatform) ─
#
# Plain functions so BOTH the FakePlatform-based test mock and the real-host-
# based dev mock call the SAME scan/handshake code — the only difference between
# them is the base class they extend, never this logic.


def scan_device_infos(specs: list[DeviceSpec]) -> list[DeviceInfo]:
    """One ``DeviceInfo`` per spec that resolves in the registry.

    Each spec sits on its own simulated port (``1-<n>``) and the result goes
    through the REAL :func:`disambiguate`, exactly as a real scan does, so two
    IDENTICAL specs are two identical coolers under ``vid:pid@1-<n>`` (#287).
    Until 2026-09-25 the mock had no port and could not show that case at all.

    Two DIFFERENT specs under one vid:pid are still a mistake: the fleet
    scripts replies by ``(vid, pid)``, so the second never answers its own
    handshake — the widescreen families all live behind ``87ad:70db`` and it is
    easy to list two by accident.  That is warned about.  (To exercise several
    variants of one vid:pid, click them in the dev console's variant panel,
    which pins each reply as you go, rather than listing them here.)
    """
    first: dict[tuple[int, int], DeviceSpec] = {}
    out: list[DeviceInfo] = []
    for port, spec in enumerate(specs, start=1):
        seen = first.setdefault(spec.key, spec)
        if seen is not spec and replace(spec, name=seen.name) != seen:
            log.warning(
                "mock scan: %04x:%04x listed twice with DIFFERENT specs (%r "
                "shadows %r) — replies are scripted per vid:pid; use the "
                "variant panel to switch between them",
                spec.vid, spec.pid, seen.name, spec.name)
        product = find_product(spec.vid, spec.pid)
        if product is None:
            log.warning(
                "mock scan: %04x:%04x (%s) not in registry — skipping; add it "
                "to core/registry.py to simulate it",
                spec.vid, spec.pid, spec.name,
            )
            continue
        out.append(DeviceInfo(vid=spec.vid, pid=spec.pid, bcd_device=spec.bcd,
                              path=f"1-{port}"))
        log.info("mock scan: + %s [%04x:%04x] port=1-%d wire=%s bcdDevice=0x%04x",
                 spec.name, spec.vid, spec.pid, port, product.wire.value, spec.bcd)
    return disambiguate(out)


# Exact (pm, sub, fbl) the dev console pins for a vid:pid — bypasses the
# geometry/spec fallback so an injected reply (incl. zero bytes) is honoured
# verbatim.  Keyed by (vid, pid); empty/absent → normal scripted behaviour.
ReplyOverride = dict[tuple[int, int], tuple[int, int, int]]


def scripted_handshake_bytes(
    by_key: dict[tuple[int, int], DeviceSpec], vid: int, pid: int,
    override: ReplyOverride | None = None,
) -> bytes:
    """Model-driven handshake reply for one device — every wire, one source.

    Resolution order, most specific first:

    1. a dev-console ``override`` (exact ``pm``/``sub``/``fbl``, used verbatim)
       — the live variant click, so it must beat anything on disk;
    2. a spec ``reply`` — a reporter's ACTUAL bytes, returned untouched.  This
       is the only path that does not go through our own packing, so it is the
       only one that can express a device disagreeing with our model;
    3. geometry resolved FAITHFULLY from the registry ``ProductInfo`` via
       :func:`resolve_handshake_geometry`, with a ``devices.json`` spec
       optionally overriding ``pm`` / ``sub`` / ``fbl``.
    """
    product = find_product(vid, pid)
    if product is None:
        log.warning("mock handshake: %04x:%04x not in registry — empty reply",
                    vid, pid)
        return b""
    spec = by_key.get((vid, pid))
    pinned = override.get((vid, pid)) if override else None

    if pinned is None and spec is not None and spec.reply is not None:
        log.info("mock handshake: %04x:%04x verbatim %d-byte reply from spec "
                 "(model NOT consulted)", vid, pid, len(spec.reply))
        return spec.reply

    if pinned is not None:
        pm, sub, fbl = pinned
    else:
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
        "mock handshake: %04x:%04x wire=%s type=%d pm=%d sub=%d fbl=%d (%d bytes)",
        vid, pid, product.wire.value, product.device_type, pm, sub, fbl, len(reply),
    )
    return reply


def scripted_scsi_transport(
    by_key: dict[tuple[int, int], DeviceSpec], vid: int, pid: int,
    override: ReplyOverride | None = None,
) -> FakeScsiTransport:
    """Fresh SCSI transport pre-loaded with the model-driven reply."""
    transport = FakeScsiTransport()
    transport.read_script.append(scripted_handshake_bytes(by_key, vid, pid, override))
    return transport


class _AckingBulkTransport(FakeBulkTransport):
    """``FakeBulkTransport`` that ACKs each frame, like a HID Type-3 panel.

    Type-3 (ALi) firmware acknowledges every image frame: ``HidLcd.send``
    writes the packet then reads a 16-byte ACK and treats an empty read as a
    send failure.  The base fake only scripts the one handshake reply, so the
    post-handshake ACK read would come back empty and every frame would
    "fail" — a simulation gap, not a device bug.  We supply a canned non-empty
    ACK for reads of exactly ``_f5.ACK_SIZE`` once the script is exhausted;
    the content isn't validated (only ``len(ack) > 0``), and every other read
    size still falls through to the base ``b""`` behaviour, so no other wire's
    handshake is perturbed.
    """

    def read(self, endpoint: int, length: int, timeout_ms: int = 100) -> bytes:
        if not self.read_script and length == _f5.ACK_SIZE:
            return bytes(_f5.ACK_SIZE)
        return super().read(endpoint, length, timeout_ms)


def scripted_bulk_transport(
    by_key: dict[tuple[int, int], DeviceSpec], vid: int, pid: int,
    override: ReplyOverride | None = None,
) -> FakeBulkTransport:
    """Fresh bulk transport (every non-SCSI wire: BULK/HID/LY/LED)."""
    transport = _AckingBulkTransport()
    transport.read_script.append(scripted_handshake_bytes(by_key, vid, pid, override))
    return transport


# ── MockPlatform ─────────────────────────────────────────────────────────────


class MockPlatform(FakePlatform):
    """``FakePlatform`` + scripted multi-device USB.

    ``root`` is the data root the GUI writes to (``dev/.trcc`` for the mock GUI,
    a tmp dir for unit tests); it flows through ``FakePlatform`` → ``FakePaths``.
    """

    def __init__(self, specs: list[dict], root: Path, *,
                 host_sensors: bool = False) -> None:
        super().__init__(root)
        # FakePlatform's fixed sensors unless a caller asks for THIS computer's.
        # Off by default because the host's are not the test's to read: CI's VM
        # has no CPU temperature sensor (six qtgui tests failed there alone), and
        # the memory clock runs ``pkexec dmidecode`` -- 263 times per suite run
        # until 2026-10-07, as root, through the polkit rule this project
        # installs.  A dev tool that wants real metrics passes True.
        self._host_sensors = host_sensors
        self._specs: list[DeviceSpec] = [DeviceSpec.parse(s) for s in specs]
        self._by_key: dict[tuple[int, int], DeviceSpec] = {
            s.key: s for s in self._specs
        }
        self._reply_override: ReplyOverride = {}
        log.info("MockPlatform: %d spec(s) loaded, root=%s",
                 len(self._specs), root)

    def set_active_reply(self, vid: int, pid: int, *,
                         pm: int, sub: int, fbl: int) -> None:
        """Pin the exact handshake reply a vid:pid returns on the next connect.

        The dev console calls this, then re-runs ConnectDevice so the app
        re-handshakes against the injected reply and re-presents.
        """
        self._reply_override[(vid, pid)] = (pm, sub, fbl)
        log.info("MockPlatform.set_active_reply: %04x:%04x pm=%d sub=%d fbl=%d",
                 vid, pid, pm, sub, fbl)

    def sensors(self) -> SensorEnumerator:
        """REAL host sensors when ``host_sensors=True`` — a dev tool's choice.

        The whole point of the multi-device mock is "a vid/pid + a scripted
        handshake stands in for the panel"; everything else is the live
        application.  So the overlay + System-Info show THIS dev computer's
        actual CPU/GPU/memory/fan metrics, not the deterministic fakes
        ``FakePlatform`` hands to unit tests.  Built the same way the real
        ``LinuxOS.sensors`` builds it (``build_linux_sensors``).
        """
        if not self._host_sensors:
            return super().sensors()
        if self._sensors is None:
            from trcc.adapters.sensors.aggregator import build_linux_sensors
            log.info("MockPlatform.sensors: REAL host sensors (dev box)")
            self._sensors = build_linux_sensors()
        return self._sensors

    def scan_devices(self) -> list[DeviceInfo]:
        """Surface one ``DeviceInfo`` per spec that resolves in the registry."""
        log.info("MockPlatform.scan_devices: %d spec(s)", len(self._specs))
        return scan_device_infos(self._specs)

    def open_transport(self, wire: Wire, vid: int, pid: int,
                       serial: str | None = None,
                       unit: str = "", *,
                       hid_reports: bool = False) -> Transport:
        """Hand back the scripted transport for *wire* (see Platform.open_transport).

        *unit* is accepted and not used to pick a script: two units of one
        model are the SAME model, so they replay the same handshake.  What
        #287 needed from the mock was two Devices under two keys, which
        ``App.attach`` now provides.  *hid_reports* (the firmware-override
        transport) replays the same scripted bulk reply.
        """
        if wire is Wire.SCSI:
            return scripted_scsi_transport(
                self._by_key, vid, pid, self._reply_override)
        return scripted_bulk_transport(
            self._by_key, vid, pid, self._reply_override)


# ── Scripted Corsair RGB memory (the SMBus a stand-in platform hands out) ────

#: The device info the maintainer's stick at 0x19 returned on 2026-10-08:
#: Corsair (0x1B1C), Vengeance RGB DDR5 (0x0701), protocol 4; checksum 0xee.
CORSAIR_STICK_INFO = bytes.fromhex(
    "1c1b010708000600020109000dc00316" "0484cbafcef91964c61b00f504000000")


class ScriptedCorsairStick:
    """A Corsair lighting controller, register for register.

    Reads: the id pair (0x43 / 0x44), the active buffer one byte at a time
    (0x40), its checksum (0x42) and a ready status (0x30).  Writes: 0x61
    selects the device info, 0x0B resets the write buffer, 0x21 rewinds,
    0x20 appends a byte, 0x82 commits the buffer as the effect (1) or the
    colours (2), and 0x31 / 0x32 take a direct colour packet.  Any other
    read raises: a script that is asked something it never answered is a
    protocol change, and should fail loudly.
    """

    def __init__(self, info: bytes = CORSAIR_STICK_INFO,
                 checksum: int | None = None,
                 ids: tuple[int, int] = (0x1B, 0x04)) -> None:
        log.debug("ScriptedCorsairStick: ids %s", ids)
        self.info, self.ids = info, ids
        self.checksum = crc8(info) if checksum is None else checksum
        self.at = 0
        self.reading = True               # info selected; False: write buffer
        self.buffer = bytearray()
        self.blocks: list[tuple[int, bytes]] = []
        self.effect = b""                 # the committed effect configuration
        self.colors = b""                 # the committed colour data

    def read(self, register: int) -> int:
        log.debug("ScriptedCorsairStick.read: 0x%02x", register)
        match register:
            case 0x43:
                return self.ids[0]
            case 0x44:
                return self.ids[1]
            case 0x40:
                self.at += 1
                return self.info[self.at - 1]
            case 0x42:
                return self.checksum if self.reading else crc8(self.buffer)
            case 0x30:
                return 0x00
        raise AssertionError(f"unexpected read of 0x{register:02x}")

    def write(self, register: int, value: int) -> None:
        log.debug("ScriptedCorsairStick.write: 0x%02x = 0x%02x", register, value)
        match register:
            case 0x61:
                self.reading = True
            case 0x21:
                self.at = 0
            case 0x0B:
                self.reading, self.buffer = False, bytearray()
            case 0x20:
                self.buffer.append(value)
            case 0x82 if value == 1:
                self.effect = bytes(self.buffer)
            case 0x82 if value == 2:
                self.colors = bytes(self.buffer)
            case _:
                raise AssertionError(
                    f"unexpected write of 0x{value:02x} to 0x{register:02x}")


class ScriptedSmBus(SmBus):
    """Scripted sticks by address; every access is recorded, an empty
    address NACKs as real hardware does."""

    def __init__(self, chips: dict[int, ScriptedCorsairStick],
                 number: int = 3) -> None:
        super().__init__(number)
        log.debug("ScriptedSmBus: i2c-%d sticks at %s", number,
                  [hex(a) for a in chips])
        self.chips = chips
        self.touched: list[tuple[str, int, int]] = []
        self.closed = False

    def _chip(self, address: int) -> ScriptedCorsairStick:
        if address not in self.chips:
            raise OSError(6, "No such device or address")
        return self.chips[address]

    def read_byte_data(self, address: int, register: int) -> int:
        self.touched.append(("read", address, register))
        return self._chip(address).read(register)

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        self.touched.append(("write", address, register))
        self._chip(address).write(register, value)

    def write_block_data(self, address: int, register: int,
                         data: bytes) -> None:
        self.touched.append(("block", address, register))
        assert len(data) <= 32
        self._chip(address).blocks.append((register, bytes(data)))

    def close(self) -> None:
        log.debug("ScriptedSmBus.close: i2c-%d", self.number)
        self.closed = True


def scripted_ram() -> ScriptedSmBus:
    """The maintainer's two Vengeance RGB DDR5 sticks, at 0x19 and 0x1B."""
    log.info("scripted_ram: two Corsair sticks on a scripted i2c-3")
    return ScriptedSmBus({0x19: ScriptedCorsairStick(),
                          0x1B: ScriptedCorsairStick()})


class ScriptedRamAccess(RamAccess):
    """The RAM-lighting grant, in memory: switching it asks nobody for a
    password and writes no udev rule -- a mock window's "Enable" must never
    reach the host's polkit."""

    def __init__(self, state: RamAccessState = RamAccessState.ON) -> None:
        log.info("ScriptedRamAccess: starts %s", state.value)
        self.state = state

    def status(self) -> RamAccessStatus:
        log.debug("ScriptedRamAccess.status: %s", self.state.value)
        return RamAccessStatus(self.state,
                               f"RAM lighting is {self.state.value} (scripted)")

    def enable(self) -> RamAccessStatus:
        log.info("ScriptedRamAccess.enable")
        self.state = RamAccessState.ON
        return self.status()

    def disable(self) -> RamAccessStatus:
        log.info("ScriptedRamAccess.disable")
        self.state = RamAccessState.OFF
        return self.status()
