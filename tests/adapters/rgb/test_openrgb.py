"""OpenRgbMirror against a fake OpenRGB SDK server (#160).

The fake encodes Device Data straight from OpenRGB's own SDK documentation
(``Documentation/OpenRGBSDK.md``, OpenRGB @9515475, GPL-2.0-or-later) -- the
tables, not the adapter's parser -- so a parser that drifted from the
documented layout fails here.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from trcc.adapters.rgb.openrgb import (
    OpenRgbMirror,
    stretch,
    update_leds_payload,
)
from trcc.core.models import RgbMirrorDevice


def _text(s: str) -> bytes:
    raw = s.encode() + b"\0"
    return struct.pack("<H", len(raw)) + raw


def _device_data(name: str, leds: int, protocol: int) -> bytes:
    """One Device Data block, field by field from the doc's table."""
    body = struct.pack("<i", 4)                       # type
    body += _text(name)
    if protocol >= 1:
        body += _text("Vendor")
    for s in ("desc", "1.0", "SER", "HID: /dev/x"):
        body += _text(s)
    body += struct.pack("<H", 2) + struct.pack("<i", 0)   # 2 modes, active 0
    for mode, colours in (("Direct", 0), ("Static", 1)):
        body += _text(mode)
        body += struct.pack("<iIIIIIIII", 0, 0, 0, 0, 1, 1, 0, 0, 1)
        body += struct.pack("<H", colours) + b"\x11\x22\x33\x00" * colours
    body += struct.pack("<H", 1)                      # one zone ...
    body += _text("Strip") + struct.pack("<iIII", 1, leds, leds, leds)
    matrix = struct.pack("<II", 1, 2) + struct.pack("<II", 0, 1)
    body += struct.pack("<H", len(matrix)) + matrix   # ... with a matrix map
    body += struct.pack("<H", leds)
    for i in range(leds):
        body += _text(f"LED {i}") + struct.pack("<I", i)
    body += struct.pack("<H", leds) + b"\0\0\0\0" * leds
    return struct.pack("<I", 4 + len(body)) + body


class FakeOpenRgb:
    """Answers like OpenRGB's NetworkServer and records what it is sent."""

    def __init__(self, devices: list[tuple[str, int]], *,
                 protocol: int | None = 4) -> None:
        self.devices = devices
        self.protocol = protocol            # None: never answers the version
        self.received: list[tuple[int, int, bytes]] = []
        self.client: socket.socket | None = None
        self._srv = socket.create_server(("127.0.0.1", 0))
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self._srv.accept()
        self.client = conn
        negotiated = 0
        try:
            while True:
                head = self._read(conn, 16)
                magic, dev, pid, size = struct.unpack("<4sIII", head)
                assert magic == b"ORGB"
                data = self._read(conn, size)
                self.received.append((dev, pid, data))
                if pid == 40 and self.protocol is not None:
                    negotiated = min(self.protocol,
                                     struct.unpack("<I", data)[0])
                    self._send(conn, 0, 40, struct.pack("<I", self.protocol))
                elif pid == 0:
                    self._send(conn, 0, 0, struct.pack("<I", len(self.devices)))
                elif pid == 1:
                    name, leds = self.devices[dev]
                    self._send(conn, dev, 1,
                               _device_data(name, leds, negotiated))
        except (OSError, struct.error):
            return

    def close(self) -> None:
        for sock in (self.client, self._srv):
            if sock is not None:
                sock.close()

    def announce_list_change(self) -> None:
        assert self.client is not None
        self._send(self.client, 0, 100, b"")

    @staticmethod
    def _send(conn: socket.socket, dev: int, pid: int, data: bytes) -> None:
        conn.sendall(struct.pack("<4sIII", b"ORGB", dev, pid, len(data)) + data)

    @staticmethod
    def _read(conn: socket.socket, size: int) -> bytes:
        out = b""
        while len(out) < size:
            chunk = conn.recv(size - len(out))
            if not chunk:
                raise OSError("closed")
            out += chunk
        return out

    def packets(self, pid: int) -> list[tuple[int, bytes]]:
        return [(d, data) for d, p, data in self.received if p == pid]

    def wait_for(self, pid: int, count: int = 1) -> None:
        deadline = time.monotonic() + 5
        while len(self.packets(pid)) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(self.packets(pid)) >= count, self.received


@pytest.fixture
def fake():  # type: ignore[no-untyped-def]
    """Build fake servers and mirrors; close every socket afterwards."""
    made: list = []

    def build(devices, **kw):  # type: ignore[no-untyped-def]
        server = FakeOpenRgb(devices, **kw)
        mirror = OpenRgbMirror(port=server.port)
        made.extend((mirror, server))
        return server, mirror

    yield build
    for thing in made:
        thing.close()


def test_a_current_openrgb_lists_its_devices_at_protocol_1(fake) -> None:
    server, mirror = fake([("ASUS Aura Motherboard", 8), ("RAM", 5)])
    devices = mirror.devices()
    assert devices == (RgbMirrorDevice(0, "ASUS Aura Motherboard", 8),
                       RgbMirrorDevice(1, "RAM", 5))
    assert server.packets(40) == [(0, struct.pack("<I", 1))]
    assert server.packets(50) == [(0, b"TRCC Linux\0")]
    assert server.packets(1) == [(0, struct.pack("<I", 1)),
                                 (1, struct.pack("<I", 1))]
    server.wait_for(1100, 2)
    assert server.packets(1100) == [(0, b""), (1, b"")]


def test_a_protocol_0_server_is_asked_without_a_version(fake) -> None:
    server, mirror = fake([("Old strip", 3)], protocol=None)
    devices = mirror.devices()
    assert devices == (RgbMirrorDevice(0, "Old strip", 3),)
    assert server.packets(1) == [(0, b"")]


def test_show_stretches_the_coolers_colours_over_the_device(fake) -> None:
    server, mirror = fake([("Strip", 4)])
    (strip,) = mirror.devices()
    mirror.show(strip, [(255, 0, 0), (0, 0, 255)])
    server.wait_for(1050)
    dev, data = server.packets(1050)[0]
    assert dev == 0
    # data_size counts itself (CreateUpdateLEDsPacket); RGBColor r|g<<8|b<<16.
    assert data == (struct.pack("<IH", 4 + 2 + 16, 4)
                    + struct.pack("<IIII", 0xFF, 0xFF, 0xFF0000, 0xFF0000))


def test_a_device_list_change_is_listed_again(fake) -> None:
    server, mirror = fake([("A", 2)])
    assert [d.name for d in mirror.devices()] == ["A"]
    server.devices.append(("B", 3))
    server.announce_list_change()
    deadline = time.monotonic() + 5
    while len(mirror.devices()) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [d.name for d in mirror.devices()] == ["A", "B"]


def test_nothing_listening_raises_oserror() -> None:
    with socket.create_server(("127.0.0.1", 0)) as s:
        port = s.getsockname()[1]
    with pytest.raises(OSError):
        OpenRgbMirror(port=port, timeout_s=0.5).devices()


@pytest.mark.parametrize(("source", "count", "out"), [
    ([(1, 1, 1)], 3, [(1, 1, 1)] * 3),
    ([(1, 0, 0), (2, 0, 0)], 4, [(1, 0, 0), (1, 0, 0), (2, 0, 0), (2, 0, 0)]),
    ([(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0)], 2, [(1, 0, 0), (3, 0, 0)]),
    ([], 3, []),
])
def test_stretch(source, count, out) -> None:
    assert stretch(source, count) == out


def test_the_credit_names_openrgb_its_licence_and_commit() -> None:
    from trcc.adapters.rgb import openrgb
    doc = openrgb.__doc__ or ""
    for needed in ("OpenRGB", "GPL-2.0-or-later", "9515475",
                   "gitlab.com/CalcProgrammer1/OpenRGB"):
        assert needed in doc
    assert Path(openrgb.__file__).name == "openrgb.py"


def test_an_update_payload_is_what_openrgb_builds() -> None:
    assert update_leds_payload([(1, 2, 3)]) == struct.pack(
        "<IHI", 10, 1, 1 | 2 << 8 | 3 << 16)


def test_a_cooler_render_reaches_openrgb_over_the_wire(tmp_path, fake) -> None:  # type: ignore[no-untyped-def]
    """The production chain: RenderLed -> the App's follower -> the real
    OpenRgbMirror -> an OpenRGB server, which receives the cooler's colours."""
    import sys

    from trcc.app import App
    from trcc.core.commands import ConnectDevice, RenderLed, SetRgbFollow
    from trcc.core.models import RgbFollowMode

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tests.mock_platform import MockPlatform

    server, _ = fake([("Motherboard", 3)])
    app = App(MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], tmp_path))
    try:
        assert app.dispatch(ConnectDevice(key="0416:8001")).ok
        assert app.dispatch(SetRgbFollow(mode=RgbFollowMode.OPENRGB,
                                         port=server.port)).ok
        rendered = app.dispatch(RenderLed(key="0416:8001"))
        server.wait_for(1050)
        _, data = server.packets(1050)[0]
        size, count = struct.unpack_from("<IH", data)
        assert (size, count) == (4 + 2 + 4 * 3, 3)
        sent = [struct.unpack_from("<I", data, 6 + 4 * i)[0] for i in range(3)]
        want = [r | g << 8 | b << 16
                for r, g, b in stretch(rendered.colors, 3)]
        assert sent == want
    finally:
        app.close()
