"""OpenRGB's colours for one cooler, over DDP on localhost (#160).

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
written into the frame at its offset.  Bound to 127.0.0.1 only: nothing on the
network can drive a cooler.  One port per cooler; the user adds a DDP device
in OpenRGB at 127.0.0.1 with that port and the cooler's LED count.
Moved here from core with the retired OpenRGB LED mode (#333).
"""
from __future__ import annotations

import logging
import socket
import threading
import time

from trcc.core.logs import per_frame
from trcc.modules.rgb.core.models import DDP_HOST
from trcc.modules.rgb.core.ports import DdpListener

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



class DdpPort(DdpListener):
    """A DDP listener on 127.0.0.1:*port* -- a port another program holds is
    said once, in ``error``, and nothing listens."""

    def __init__(self, port: int) -> None:
        log.info("DdpPort: opening 127.0.0.1:%d", port)
        self._frame = bytearray()
        self._lock = threading.Lock()
        self._last: float | None = None
        self._error = ""
        self._asked = port
        #: The port bound -- read once, at bind: the OS's pick for 0.
        self._port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((DDP_HOST, port))
        except OSError as e:
            sock.close()
            self._error = f"port {port} is taken ({e.strerror or e})"
            log.warning("DdpPort: cannot listen on 127.0.0.1:%d (%s) -- "
                        "OpenRGB's colours will not reach this cooler", port, e)
            return
        sock.settimeout(0.5)
        self._port = sock.getsockname()[1]
        self._sock = sock
        self._thread = threading.Thread(target=self._receive, args=(sock,),
                                        name=f"trcc-ddp-{self.port}", daemon=True)
        self._thread.start()
        log.info("DdpPort: listening on 127.0.0.1:%d for OpenRGB", self.port)

    @property
    def port(self) -> int:
        """The bound port (the asked one, or the OS's pick for 0) -- kept from
        the bind.  It asked the socket every time, and a ``close`` on another
        thread could clear and close it between the check and the ask:
        EBADF on the receive thread (ci_local, 2026-11-10)."""
        frame_log.debug("DdpPort.port: %d", self._port)
        return self._port

    @property
    def error(self) -> str:
        frame_log.debug("DdpPort.error: %r", self._error)
        return self._error

    @property
    def last_packet(self) -> float | None:
        frame_log.debug("DdpPort.last_packet: %s", self._last)
        return self._last

    def colors(self, count: int) -> list[tuple[int, int, int]]:
        frame_log.debug("DdpPort.colors: %d", count)
        with self._lock:
            frame = bytes(self._frame[:count * 3])
        frame += bytes(count * 3 - len(frame))
        return [(frame[i], frame[i + 1], frame[i + 2])
                for i in range(0, count * 3, 3)]

    def _receive(self, sock: socket.socket) -> None:
        """Apply every packet until ``close`` closes the socket."""
        log.debug("DdpPort._receive: started on %d", self.port)
        while self._sock is sock:
            try:
                packet = sock.recv(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                if apply_packet(self._frame, packet):
                    self._last = time.monotonic()
        log.debug("DdpPort._receive: stopped")

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            log.debug("DdpPort.close: not listening")
            return
        sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        log.info("DdpPort: stopped listening on %d", self._asked)
