"""OpenRGB as a follower: TRCC sends the cooler's colours to OpenRGB's devices.

TRCC is a CLIENT of OpenRGB's SDK server (TCP 6742), so the motherboard, RAM,
fans and strips OpenRGB drives show what the cooler shows (#160).  OpenRGB
does not drive these coolers itself -- no Thermalright detector, none of our
USB ids -- so the two never fight over one device.

**Credit.**  The wire format is OpenRGB's, learned from and ported to Python
from the OpenRGB project (https://gitlab.com/CalcProgrammer1/OpenRGB,
GPL-2.0-or-later), read at commit 9515475 (2026-10-08):

* ``Documentation/OpenRGBSDK.md`` -- the packet header, the packet ids, and
  the Device / Mode / Zone / LED Data layouts per protocol version;
* ``NetworkProtocol.h`` -- the ids and port; ``RGBControllerInterface.h`` --
  ``ToRGBColor`` (``r | g << 8 | b << 16``);
* ``RGBController_Network.cpp`` ``CreateUpdateLEDsPacket`` -- the UpdateLEDs
  ``data_size`` counts its own four bytes;
* ``NetworkClient.cpp`` -- the client asks for the version, then uses the
  lower of the two (:1811-1817), so speaking protocol 1 is enough for every
  server since OpenRGB 0.5.

Protocol 1 because it is the smallest that carries the vendor string: no
segments, flags or per-zone modes to parse.  A server that never answers the
version request is protocol 0, and is spoken to as such.
"""
from __future__ import annotations

import dataclasses
import logging
import select
import socket
import struct
from collections import Counter
from collections.abc import Sequence

from ...core.logs import per_frame
from ...core.models import RgbMirrorDevice
from ...core.ports import RgbMirror

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

DEFAULT_PORT = 6742
CLIENT_NAME = "TRCC Linux"
#: The highest protocol this client speaks.
PROTOCOL = 1

_MAGIC = b"ORGB"
_HEADER = struct.Struct("<4sIII")          # magic, dev_id, pkt_id, pkt_size

# Packet ids (NetworkProtocol.h).
REQUEST_CONTROLLER_COUNT = 0
REQUEST_CONTROLLER_DATA = 1
REQUEST_PROTOCOL_VERSION = 40
SET_CLIENT_NAME = 50
DEVICE_LIST_UPDATED = 100
UPDATE_LEDS = 1050
SET_CUSTOM_MODE = 1100

#: How long a server gets to answer the version request before it is taken
#: to be protocol 0, which never answers it.
_VERSION_WAIT_S = 1.0


def header(dev_id: int, pkt_id: int, size: int) -> bytes:
    """The 16-byte NetPacketHeader."""
    frame_log.debug("header: dev=%d id=%d size=%d", dev_id, pkt_id, size)
    return _HEADER.pack(_MAGIC, dev_id, pkt_id, size)


def update_leds_payload(colors: Sequence[tuple[int, int, int]]) -> bytes:
    """UpdateLEDs data: ``data_size`` (itself included), count, RGBColors."""
    frame_log.debug("update_leds_payload: %d colour(s)", len(colors))
    body = struct.pack("<H", len(colors)) + b"".join(
        struct.pack("<I", r | g << 8 | b << 16) for r, g, b in colors)
    return struct.pack("<I", 4 + len(body)) + body


def stretch(colors: Sequence[tuple[int, int, int]],
            count: int) -> list[tuple[int, int, int]]:
    """*colors* over *count* LEDs: each takes the source colour at its place."""
    frame_log.debug("stretch: %d -> %d", len(colors), count)
    if not colors or count <= 0:
        return []
    return [colors[i * len(colors) // count] for i in range(count)]


class _Reader:
    """Walks one Device Data block (OpenRGBSDK.md, "Device Data")."""

    def __init__(self, data: bytes) -> None:
        frame_log.debug("_Reader.__init__: %d byte(s)", len(data))
        self._data = data
        self._at = 0

    def take(self, fmt: str) -> int:
        frame_log.debug("_Reader.take: %s at %d", fmt, self._at)
        (value,) = struct.unpack_from(fmt, self._data, self._at)
        self._at += struct.calcsize(fmt)
        return value

    def text(self) -> str:
        frame_log.debug("_Reader.text: at %d", self._at)
        size = self.take("<H")
        raw = self._data[self._at:self._at + size]
        self._at += size
        return raw.rstrip(b"\0").decode("utf-8", errors="replace")

    def skip(self, size: int) -> None:
        frame_log.debug("_Reader.skip: %d at %d", size, self._at)
        self._at += size


def parse_device(index: int, data: bytes, protocol: int) -> RgbMirrorDevice:
    """Name and LED count from a controller-data reply at *protocol* (0/1).

    Every field is walked even though two are kept: the colours sit at the
    very end, after variable-length modes, zones and LEDs.
    """
    log.debug("parse_device: index=%d %d byte(s) protocol=%d", index,
              len(data), protocol)
    r = _Reader(data)
    r.take("<I")                               # data_size
    r.take("<i")                               # type
    name = r.text()
    if protocol >= 1:
        r.text()                               # vendor
    for _ in range(4):
        r.text()                               # description, version, serial, location
    modes = r.take("<H")
    r.take("<i")                               # active_mode
    for _ in range(modes):
        r.text()                               # mode name
        r.skip(4 * 9)                          # value .. color_mode (protocol < 3)
        r.skip(4 * r.take("<H"))               # mode colours
    for _ in range(r.take("<H")):              # zones
        r.text()
        r.skip(4 * 4)                          # type, leds_min, leds_max, leds_count
        r.skip(r.take("<H"))                   # matrix map, if any
    for _ in range(r.take("<H")):              # leds
        r.text()
        r.skip(4)                              # led value
    return RgbMirrorDevice(index=index, name=name, led_count=r.take("<H"))


def named_by_ref(devices: Sequence[RgbMirrorDevice]) -> tuple[RgbMirrorDevice, ...]:
    """Each device with a stable ``ref``: its name, ``#2``, ``#3``... after
    a name another device shares.  OpenRGB's index can change between runs
    (detection order); the name a user ticked must not."""
    log.debug("named_by_ref: %d device(s)", len(devices))
    totals = Counter(d.name for d in devices)
    seen: Counter[str] = Counter()
    named = []
    for device in devices:
        seen[device.name] += 1
        ref = (device.name if totals[device.name] == 1
               else f"{device.name} #{seen[device.name]}")
        named.append(dataclasses.replace(device, ref=ref))
    return tuple(named)


class OpenRgbMirror(RgbMirror):
    """A client of one OpenRGB SDK server."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 timeout_s: float = 3.0) -> None:
        log.debug("OpenRgbMirror.__init__: %s:%d", host, port)
        self._host, self._port, self._timeout = host, port, timeout_s
        self._sock: socket.socket | None = None
        self._protocol = 0
        self._devices: tuple[RgbMirrorDevice, ...] | None = None
        # Devices TRCC has put in direct mode: only those it sends colours,
        # so a device the user left out keeps its own lighting.
        self._custom: set[int] = set()

    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        """Connect if needed; list again after OpenRGB says the list changed."""
        self._connect()
        self._drain()
        if self._devices is None:
            self._devices = self._list()
        frame_log.debug("OpenRgbMirror.devices: %d", len(self._devices))
        return self._devices

    def show(self, device: RgbMirrorDevice,
             colors: Sequence[tuple[int, int, int]]) -> None:
        sock = self._connect()
        if device.index not in self._custom:
            log.info("OpenRgbMirror: %s to direct mode", device.name)
            sock.sendall(header(device.index, SET_CUSTOM_MODE, 0))
            self._custom.add(device.index)
        payload = update_leds_payload(stretch(colors, device.led_count))
        frame_log.debug("OpenRgbMirror.show: %s %d LED(s)", device.name,
                        device.led_count)
        sock.sendall(header(device.index, UPDATE_LEDS, len(payload)) + payload)

    def close(self) -> None:
        log.debug("OpenRgbMirror.close: connected=%s", self._sock is not None)
        if self._sock is not None:
            self._sock.close()
        self._sock, self._devices = None, None
        self._custom.clear()

    # ── Internals ─────────────────────────────────────────────────────

    def _connect(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        log.info("OpenRgbMirror: connecting to %s:%d", self._host, self._port)
        sock = socket.create_connection((self._host, self._port),
                                        timeout=self._timeout)
        self._sock = sock
        try:
            self._protocol = self._negotiate()
            name = CLIENT_NAME.encode() + b"\0"
            sock.sendall(header(0, SET_CLIENT_NAME, len(name)) + name)
        except OSError:
            self.close()
            raise
        log.info("OpenRgbMirror: connected, protocol %d", self._protocol)
        return sock

    def _negotiate(self) -> int:
        """The lower of the server's version and ours; 0 if it never says."""
        assert self._sock is not None
        self._sock.sendall(header(0, REQUEST_PROTOCOL_VERSION, 4)
                           + struct.pack("<I", PROTOCOL))
        try:
            self._sock.settimeout(_VERSION_WAIT_S)
            _, data = self._receive(REQUEST_PROTOCOL_VERSION)
        except TimeoutError:
            log.info("_negotiate: no version reply — a protocol 0 server")
            return 0
        finally:
            self._sock.settimeout(self._timeout)
        server = struct.unpack("<I", data)[0]
        log.debug("_negotiate: server protocol %d", server)
        return min(server, PROTOCOL)

    def _list(self) -> tuple[RgbMirrorDevice, ...]:
        """Every device, each switched to its custom (direct) mode."""
        assert self._sock is not None
        self._sock.sendall(header(0, REQUEST_CONTROLLER_COUNT, 0))
        _, data = self._receive(REQUEST_CONTROLLER_COUNT)
        count = struct.unpack_from("<I", data)[0]
        found = []
        for index in range(count):
            request = struct.pack("<I", self._protocol) if self._protocol else b""
            self._sock.sendall(header(index, REQUEST_CONTROLLER_DATA,
                                      len(request)) + request)
            _, data = self._receive(REQUEST_CONTROLLER_DATA)
            found.append(parse_device(index, data, self._protocol))
        log.info("OpenRgbMirror: %d device(s): %s", len(found),
                 ", ".join(f"{d.name} ({d.led_count})" for d in found))
        return named_by_ref(found)

    def _receive(self, wanted: int) -> tuple[int, bytes]:
        """The next packet with id *wanted*; notices a list change on the way."""
        frame_log.debug("_receive: waiting for %d", wanted)
        while True:
            dev_id, pkt_id, data = self._packet()
            if pkt_id == wanted:
                return dev_id, data
            self._note(pkt_id)

    def _drain(self) -> None:
        """Read what OpenRGB sent unasked -- a device-list change, mostly."""
        frame_log.debug("_drain")
        assert self._sock is not None
        while select.select([self._sock], [], [], 0)[0]:
            _, pkt_id, _ = self._packet()
            self._note(pkt_id)

    def _note(self, pkt_id: int) -> None:
        if pkt_id == DEVICE_LIST_UPDATED:
            log.info("OpenRgbMirror: OpenRGB's device list changed")
            self._devices = None
            self._custom.clear()       # indexes may now name other devices
        else:
            log.debug("OpenRgbMirror: ignoring packet %d", pkt_id)

    def _packet(self) -> tuple[int, int, bytes]:
        frame_log.debug("_packet")
        raw = self._exactly(_HEADER.size)
        magic, dev_id, pkt_id, size = _HEADER.unpack(raw)
        if magic != _MAGIC:
            raise OSError(f"not an OpenRGB server: header {raw[:4]!r}")
        return dev_id, pkt_id, self._exactly(size)

    def _exactly(self, size: int) -> bytes:
        frame_log.debug("_exactly: %d", size)
        assert self._sock is not None
        chunks, need = [], size
        while need:
            chunk = self._sock.recv(need)
            if not chunk:
                raise OSError("OpenRGB closed the connection")
            chunks.append(chunk)
            need -= len(chunk)
        return b"".join(chunks)
