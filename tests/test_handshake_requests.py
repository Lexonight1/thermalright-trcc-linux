"""The REQUEST half of every handshake — what we PUT on the wire.

The fourth axis of the device-coverage set ``test_device_surface_matrix.py``
names.  Those three all watch what comes BACK:

  * ``test_device_catalog_smoke.py`` — every variant CONNECTS on a scripted reply.
  * ``test_*_lcd_geometry.py`` — the reply parses to the right canvas.
  * ``test_device_surface_matrix.py`` — a frame renders and reaches the wire.

**THIS** watches what goes OUT first: the init packet each ``connect()`` writes,
and the size it asks back.  Nothing did, and the fake transport cannot notice —
``FakeBulkTransport.read()`` pops ``read_script[0]`` on the first read of any
size, unconditioned on what was written, so a completely wrong init packet still
gets a perfect reply and every downstream assertion passes.  Measured before this
file existed, of six handshake variants the request bytes were asserted for
**one** (HID Type 3, ``test_f5_protocol.py``); ``_build_init_packet_type2`` and
``Led._build_init_packet`` had zero test references anywhere in the tree.

The expected bytes are transcribed from the decompile, with the citation on the
row — never rebuilt from the constant under test, which is the trap
``mock_platform.ly_reply``'s docstring records paying for: a harness that
restates the implementation agrees with a defect for as long as it exists.

**The wire component is a different binary.**  ``TRCC.exe`` composes; the USB
senders live in ``USBLCDNEW.dll``, so citations below are to
``DCReadWriteAsync.cs`` in the ``USBLCDNEW`` decompile except LED, which
handshakes from ``TRCC.exe`` itself.  SCSI has **no managed oracle at all** —
``USBLCD.exe`` is native — which is stated on that test rather than papered over.

**Endpoints are deliberately not an oracle claim.**  ``PyUsbBulkTransport.write``
resolves the endpoint from the device's own descriptors and falls back to the
argument only when discovery found nothing (``transport.py:273``), so a class's
``_EP_WRITE`` is a default, not what reaches the wire.  The C# hardcodes EP09 for
LY where we pass 0x01 and that is NOT a divergence.  What IS checked here is that
exactly one write happens, carrying exactly these bytes.

MUTATION CHECK — 14 mutations of the SHIPPING code, MEASURED 2026-09-10, every
one caught: the bulk magic, the bulk read size, the Type-2 pad, the Type-2
command byte's index, the Type-2 response size, the LED command byte's index,
the LED report size, the LY pad, the LY read size, the SCSI poll command word,
the SCSI init payload no longer being zeros, and dropping a row from the table
(which fails the completeness test too).  The LY pair fails two rows each,
correctly — ``LyLcd`` serves both PIDs.  The two ``_f5`` mutations pass HERE and
fail ``test_f5_protocol.py::test_f5_constants_are_the_decompiled_values``, which
is the arrangement the comment above the literals describes.

Two of those mutations survived the first draft of this file, both for the same
reason: the row derived its expectation from the constant it was testing, so the
request and the expectation moved together.  That is the failure this whole file
exists to prevent, reproduced in the file itself on the first try.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from trcc.adapters.device import DEVICES, _f5
from trcc.adapters.device._base import BaseBulkDevice
from trcc.core.models import ProductInfo, Wire
from trcc.core.ports import Transport
from trcc.core.registry import ALL_DEVICES, find_product

from . import mock_platform as mock
from .conftest import FakeBulkTransport, FakeScsiTransport

# ── The oracle's request packets, transcribed ───────────────────────────
#
# Each is written as its command bytes plus an explicit pad, in the shape the
# C# builds it (a short literal array concatenated with a zero array), so the
# total length is a fact of the row rather than something to count.
#
# Sizes are spelled out here rather than imported, EXCEPT ``_f5``'s.  A gate may
# only reuse the constant it is testing when some other test pins that constant
# to a literal, and exactly one of them is: ``_f5.RESPONSE_SIZE`` is asserted to
# be 1024 in ``test_f5_protocol.py:71``, so the type-3 row can name it and a
# mutation is still caught — one file over, which is the point of one spelling.
# ``_TYPE2_RESPONSE_SIZE`` and ``_HID_REPORT_SIZE`` are pinned NOWHERE: every
# use in the tree, this suite's reply builders included, derives from them, so
# the tree agrees with itself and nothing states the value.  MEASURED: with
# ``read_size=_HID_REPORT_SIZE`` on the LED row, changing 64 to 32 in ``led.py``
# left all 13 tests green — the request and the expectation moved together.
# These literals are that anchor.

#: ``ThreadSendDeviceData`` (87ad:70db), ``DCReadWriteAsync.cs:321-329`` — one
#: 64-byte array, magic 0x12345678, a lone 1 at index 56.
_BULK_REQUEST = bytes([0x12, 0x34, 0x56, 0x78] + [0] * 52 + [1] + [0] * 7)

#: ``ThreadSendDeviceDataH`` (0416:5302), ``:537-541`` — a 20-byte command
#: concatenated with ``new byte[492]`` (``:536``), i.e. 512 on the wire.
_TYPE2_COMMAND = bytes([0xDA, 0xDB, 0xDC, 0xDD] + [0] * 8 + [1] + [0] * 7)
_TYPE2_REQUEST = _TYPE2_COMMAND + bytes(492)

#: ``ThreadSendDeviceDataLY`` (0416:5408), ``:917-921`` — a 16-byte command
#: concatenated with ``new byte[2032]`` (``:916``), i.e. 2048 on the wire.
_LY_COMMAND = bytes([0x02, 0xFF] + [0] * 6 + [1] + [0] * 7)
_LY_REQUEST = _LY_COMMAND + bytes(2032)

#: ``DeviceOnConnected1`` (``TRCC/UCDevice.cs:1215-1218``) — the LED handshake is
#: a 20-byte HID command, byte-identical to Type 2's.  The Windows HID stack
#: pads a report to the interface's report length; we speak the same endpoint
#: over libusb, so we write one full 64-byte report.  The command is the oracle;
#: the pad length is ours.
_LED_COMMAND = _TYPE2_COMMAND
_LED_REQUEST = _LED_COMMAND + bytes(44)          # 20 + 44 = one 64-byte report

#: ``ThreadSendDeviceDataLY1`` (0416:5409), ``:1173-1174`` — the SAME 16-byte
#: command as LY, concatenated with ``new byte[496]``, and a ``new byte[511]``
#: reply buffer (``:1172``).  Not what we send: see
#: :func:`test_ly1_sends_the_ly_request_not_its_own`.
_LY1_ORACLE_REQUEST_SIZE = 16 + 496
_LY1_ORACLE_READ_SIZE = 511

#: ``ScsiLcd._build_cdb(cmd, 0xE100)`` — cmd LE, 8 zeros, size LE.  NO ORACLE:
#: see :func:`test_scsi_polls_then_inits`.
_SCSI_POLL_CDB = bytes([0xF5, 0, 0, 0] + [0] * 8 + [0x00, 0xE1, 0x00, 0x00])
_SCSI_INIT_CDB = bytes([0xF5, 1, 0, 0] + [0] * 8 + [0x00, 0xE1, 0x00, 0x00])
_SCSI_TRANSFER_SIZE = 0xE100


@dataclass(frozen=True)
class Handshake:
    """One wire's handshake exchange, as the oracle states it.

    ``request`` is what ``connect()`` must write and ``read_size`` what it must
    ask back — the C# allocates its read buffer at the same site, so the pair
    travels together and diverging on either is a different conversation with
    the panel.
    """

    name: str
    vid: int
    pid: int
    reply: bytes
    request: bytes
    read_size: int
    citation: str


#: Every variant that handshakes over a bulk transport.  SCSI is the one wire
#: with a different transport shape (CDBs, not endpoints) and has its own test.
BULK_WIRE_HANDSHAKES: tuple[Handshake, ...] = (
    Handshake(
        name="bulk", vid=0x87AD, pid=0x70DB,
        reply=mock.bulk_handshake_reply(pm=5),
        request=_BULK_REQUEST, read_size=1024,
        citation="ThreadSendDeviceData, DCReadWriteAsync.cs:320-329",
    ),
    Handshake(
        name="hid-type2", vid=0x0416, pid=0x5302,
        reply=mock.hid_type2_reply(pm=58),
        request=_TYPE2_REQUEST, read_size=512,
        citation="ThreadSendDeviceDataH, DCReadWriteAsync.cs:535-541",
    ),
    Handshake(
        name="hid-type3", vid=0x0416, pid=0x5406,
        reply=mock.hid_type3_reply(fbl=100),
        # The one row that does NOT restate its bytes: ``_f5``'s literals are
        # pinned against the same decompile in
        # ``test_f5_protocol.py::test_f5_constants_are_the_decompiled_values``,
        # and spelling 1040 bytes a second time here is how the two copies
        # drift apart.
        request=_f5.init_packet(), read_size=_f5.RESPONSE_SIZE,
        citation="ThreadSendDeviceDataALi, DCReadWriteAsync.cs:715-716",
    ),
    Handshake(
        name="ly", vid=0x0416, pid=0x5408,
        reply=mock.ly_reply(pm=65),
        request=_LY_REQUEST, read_size=512,
        citation="ThreadSendDeviceDataLY, DCReadWriteAsync.cs:915-921",
    ),
    Handshake(
        name="led", vid=0x0416, pid=0x8001,
        reply=mock.led_handshake_reply(pm=1),
        request=_LED_REQUEST, read_size=64,
        citation="DeviceOnConnected1, TRCC/UCDevice.cs:1215-1218",
    ),
)


# There used to be an autouse fixture here redirecting the LED probe cache away
# from the real ``~/.trcc``, because ``Led.connect()`` saved to a module
# constant built from ``Path.home()`` and the LED row below would otherwise
# write a fake device into the user's own cache.  It is gone, and nothing
# replaced it: a ``Led`` nobody handed a state dir now persists nothing at all
# (``Device.set_state_dir``).  Isolation by construction, not by every test
# remembering to patch.


def _product(vid: int, pid: int) -> ProductInfo:
    product = find_product(vid, pid)
    assert product is not None, f"{vid:04x}:{pid:04x} is not in the registry"
    return product


def _connect(product: ProductInfo, transport: Transport):
    """Build this product's device the way ``App.attach`` does, and connect it."""
    device = DEVICES[product.wire](product, transport)
    device.connect()
    return device


def _connect_bulk(product: ProductInfo, transport: Transport) -> BaseBulkDevice:
    """:func:`_connect` for a bulk-transport wire, whose endpoints we read."""
    device = _connect(product, transport)
    assert isinstance(device, BaseBulkDevice)
    return device


# ── The request itself ──────────────────────────────────────────────────


@pytest.mark.parametrize("hs", BULK_WIRE_HANDSHAKES, ids=lambda h: h.name)
def test_handshake_writes_the_oracle_request(hs: Handshake) -> None:
    """``connect()`` writes exactly the oracle's init packet, exactly once."""
    transport = FakeBulkTransport()
    transport.read_script.append(hs.reply)

    device = _connect_bulk(_product(hs.vid, hs.pid), transport)

    assert transport.writes == [(device._EP_WRITE, hs.request)], hs.citation


@pytest.mark.parametrize("hs", BULK_WIRE_HANDSHAKES, ids=lambda h: h.name)
def test_handshake_asks_back_the_oracle_read_size(hs: Handshake) -> None:
    """...and asks back exactly as many bytes as the C# allocates for the reply.

    Separate from the write so a failure names which half moved.  The C# sizes
    its read buffer beside the request it sends, which is why the pair belongs
    to one row.
    """
    transport = FakeBulkTransport()
    transport.read_script.append(hs.reply)

    device = _connect_bulk(_product(hs.vid, hs.pid), transport)

    assert transport.reads == [(device._EP_READ, hs.read_size)], hs.citation


def test_scsi_polls_then_inits() -> None:
    """SCSI writes a poll CDB, then an init CDB with a zero-filled transfer.

    **This row has no oracle.**  The SCSI sender lives in ``USBLCD.exe``, which
    is native — the only one of the app's binaries ILSpy cannot read — so these
    bytes are pinned against our own implementation, and this test catches a
    CHANGE, never a WRONG value.  Said out loud because every other row here is
    a parity claim and this one must not be read as one.

    It is still worth pinning: the poll CDB was invisible to the whole suite
    until ``FakeScsiTransport.reads`` existed (``read_cdb`` recorded nothing),
    and the one assertion that reads like it covers the poll —
    ``test_transports.py``'s "First CDB must be the poll command" — is looking
    at ``sent[0]``, which is the INIT.  The two differ only in the command
    word's second byte (0xF5 vs 0x1F5), so the assertion held either way.
    """
    transport = FakeScsiTransport()
    transport.read_script.append(mock.scsi_poll_reply(fbl=100))

    _connect(_product(0x0402, 0x3922), transport)

    assert transport.reads == [(_SCSI_POLL_CDB, _SCSI_TRANSFER_SIZE)]
    assert transport.sent == [
        (_SCSI_INIT_CDB, bytes(_SCSI_TRANSFER_SIZE)),
    ]


# ── A known divergence, pinned rather than hidden ───────────────────────


def test_ly1_sends_the_ly_request_not_its_own() -> None:
    """LY1 (0416:5409) gets LY's handshake, and the C# gives it a different one.

    ``ThreadSendDeviceDataLY1`` (``DCReadWriteAsync.cs:1172-1177``) builds the
    SAME 16-byte command as LY and then concatenates ``new byte[496]`` —
    ``_LY1_ORACLE_REQUEST_SIZE`` on the wire, not 2048 — and allocates
    ``new byte[511]`` for the reply (``_LY1_ORACLE_READ_SIZE``), not 512.
    ``LyLcd`` has one ``_HANDSHAKE_PAYLOAD`` and one ``_HANDSHAKE_READ_SIZE``
    for both PIDs, so LY1 is sent LY's.

    The class already knows the variants apart (``_chunk_cmd``, and the PM/SUB
    constants), so this is an omission rather than a decision — but nobody owns
    an LY1 and no reporter has raised it, and changing bytes on hardware we
    cannot test could break a panel that works today.  So it is PINNED: this
    test asserts what we actually send, and fails if someone changes it without
    reading this.  If you are here because it failed, you are making the C#'s
    split real — which needs an LY1 owner to confirm, not a green suite.

    Recorded in ``SESSION.md`` under FOUND AND LEFT STANDING.
    """
    transport = FakeBulkTransport()
    transport.read_script.append(mock.ly_reply(pm=65, is_ly1=True))

    device = _connect_bulk(_product(0x0416, 0x5409), transport)

    assert transport.writes == [(device._EP_WRITE, _LY_REQUEST)]
    assert transport.reads == [(device._EP_READ, 512)]
    # The divergence itself, as an assertion rather than as prose beside one:
    # it flips the moment somebody closes it, which sends them to the docstring
    # above instead of to a green suite.
    assert len(_LY_REQUEST) != _LY1_ORACLE_REQUEST_SIZE, (
        "LyLcd now builds the C#'s LY1 request — that needs an LY1 owner to "
        "confirm on glass, not a passing test.  Update this test with theirs.")
    assert transport.reads[0][1] != _LY1_ORACLE_READ_SIZE, (
        "LyLcd now asks back the C#'s LY1 511 — same caveat.")


# ── The table names its own universe, so the registry checks it ─────────


def test_every_registered_product_has_its_request_gated() -> None:
    """Every product in the registry is covered by a row above.

    The request a device writes is a function of ``(wire, device_type)`` — HID
    branches on the type, everything else is per wire — so that pair is the
    universe, and it comes from ``ALL_DEVICES`` rather than from the table.  A
    table that decides for itself what it covers reports 100% of whatever it
    happens to contain: adding a device on a new wire with no row here must
    fail, not quietly leave the wire ungated.
    """
    gated = {
        (_product(hs.vid, hs.pid).wire, _product(hs.vid, hs.pid).device_type)
        for hs in BULK_WIRE_HANDSHAKES
    }
    gated.add((Wire.SCSI, _product(0x0402, 0x3922).device_type))

    ungated = {
        (p.wire, p.device_type) for p in ALL_DEVICES.values()
    } - gated

    assert not ungated, (
        f"no handshake-request row for {sorted((w.value, t) for w, t in ungated)}"
        " — add one to BULK_WIRE_HANDSHAKES with its decompile citation"
    )
