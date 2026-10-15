"""OpenRGB's colours for a cooler's LEDs, received over DDP (#160).

OpenRGB drives a network LED device by sending DDP -- the Distributed Display
Protocol 3waylabs published, which WLED and xLights speak too.  Its sender
(``Controllers/DDPController``, OpenRGB 1.0) writes, per packet::

    byte 0   flags        0x41: version 1, push
    byte 1   sequence     low 4 bits
    byte 2   data type    1
    byte 3   destination  1
    4..7     data offset  big-endian, in bytes
    8..9     data length  big-endian
    10..     RGB, 8 bits a channel

A 14-byte header carries a timecode (flag 0x10).  Each packet's colours are
written into the frame at its offset; OpenRGB pushes on every packet.

Bound to 127.0.0.1 only: nothing on the network can drive the cooler.  The
user adds a DDP device in OpenRGB at 127.0.0.1, port 4048, with the cooler's
LED count.
"""
from __future__ import annotations

import logging
import socket
import threading

from ...core.logs import per_frame
from ...core.ports import LedColorStream

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: OpenRGB's default DDP port, and WLED's.
DDP_PORT = 4048
_HEADER = 10
_FLAG_TIMECODE = 0x10
_FLAG_VERSION_MASK = 0xC0
_VERSION_1 = 0x40
#: A DDP packet carries at most 1440 bytes of colour: 480 LEDs.
_MAX_FRAME = 1440 * 64


def apply_packet(frame: bytearray, packet: bytes) -> bool:
    """Write one DDP packet's colours into *frame* at its offset.  False
    for anything that is not a version-1 DDP packet."""
    if len(packet) < _HEADER or packet[0] & _FLAG_VERSION_MASK != _VERSION_1:
        frame_log.debug("apply_packet: not DDP v1 (%d bytes)", len(packet))
        return False
    header = _HEADER + (4 if packet[0] & _FLAG_TIMECODE else 0)
    offset = int.from_bytes(packet[4:8], "big")
    length = min(int.from_bytes(packet[8:10], "big"), len(packet) - header)
    if length < 0 or offset + length > _MAX_FRAME:
        frame_log.debug("apply_packet: offset %d + %d out of range", offset,
                        length)
        return False
    if len(frame) < offset + length:
        frame.extend(bytes(offset + length - len(frame)))
    frame[offset:offset + length] = packet[header:header + length]
    frame_log.debug("apply_packet: %d byte(s) at %d", length, offset)
    return True


class DdpReceiver(LedColorStream):
    """A DDP listener on localhost, started by the first ``colors`` call."""

    def __init__(self, port: int | None = None) -> None:
        self._port = DDP_PORT if port is None else port
        log.debug("DdpReceiver: port %d", self._port)
        self._frame = bytearray()
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        # The port was taken: say so once, not on every LED tick.
        self._refused = False

    @property
    def port(self) -> int:
        """The bound port (the asked one, or the OS's pick for 0)."""
        port = self._sock.getsockname()[1] if self._sock else self._port
        log.debug("DdpReceiver.port: %d", port)
        return port

    @property
    def listening(self) -> bool:
        frame_log.debug("DdpReceiver.listening: %s", self._sock is not None)
        return self._sock is not None

    def colors(self, count: int) -> list[tuple[int, int, int]]:
        frame_log.debug("DdpReceiver.colors: %d", count)
        if self._sock is None and not self._refused:
            self._start()
        with self._lock:
            frame = bytes(self._frame[:count * 3])
        frame += bytes(count * 3 - len(frame))
        return [(frame[i], frame[i + 1], frame[i + 2])
                for i in range(0, count * 3, 3)]

    def _start(self) -> None:
        """Open the listener; a port another program holds is said, once."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", self._port))
        except OSError as e:
            sock.close()
            log.warning("DdpReceiver: cannot listen on 127.0.0.1:%d (%s) -- "
                        "OpenRGB's colours will not reach the cooler",
                        self._port, e)
            self._refused = True
            return
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._receive, args=(sock,),
                                        name="trcc-ddp", daemon=True)
        self._thread.start()
        log.info("DdpReceiver: listening on 127.0.0.1:%d for OpenRGB", self.port)

    def _receive(self, sock: socket.socket) -> None:
        """Apply every packet until ``stop`` closes the socket."""
        log.debug("DdpReceiver._receive: started")
        while self._sock is sock:
            try:
                packet = sock.recv(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                apply_packet(self._frame, packet)
        log.debug("DdpReceiver._receive: stopped")

    def stop(self) -> None:
        self._refused = False
        sock, self._sock = self._sock, None
        if sock is None:
            log.debug("DdpReceiver.stop: not listening")
            return
        sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._lock:
            self._frame.clear()
        log.info("DdpReceiver: stopped listening")
