#!/usr/bin/env python3
"""Play a moving rainbow over DDP, exactly as OpenRGB sends it (#160).

Proves the OpenRGB LED mode without OpenRGB: put a cooler in that mode
(``trcc led mode KEY openrgb``), run this, and the cooler's LEDs show the
rainbow, LED i the i-th colour.  The packets copy OpenRGB 1.0's DDP sender
(``Controllers/DDPController``): flags 0x41 (version 1, push), data type 1,
destination 1, big-endian offset and length, RGB.

    python3 dev/tools/ddp_send.py                 # 84 LEDs, 30 fps, 10 s
    python3 dev/tools/ddp_send.py --leds 31 --seconds 0 --port 4048
"""
from __future__ import annotations

import argparse
import colorsys
import socket
import time

#: OpenRGB splits a frame into packets of at most 1440 bytes of colour.
_CHUNK = 1440


def packets(rgb: bytes, sequence: int) -> list[bytes]:
    """One frame as OpenRGB's DDP packets."""
    return [bytes([0x41, sequence & 0x0F, 1, 1]) + off.to_bytes(4, "big")
            + len(rgb[off:off + _CHUNK]).to_bytes(2, "big") + rgb[off:off + _CHUNK]
            for off in range(0, len(rgb), _CHUNK)]


def rainbow(leds: int, phase: float) -> bytes:
    """A full rainbow across *leds*, turned by *phase* (0..1)."""
    out = bytearray()
    for i in range(leds):
        r, g, b = colorsys.hsv_to_rgb((i / leds + phase) % 1.0, 1.0, 1.0)
        out += bytes((int(r * 255), int(g * 255), int(b * 255)))
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4048)
    ap.add_argument("--leds", type=int, default=84)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--seconds", type=float, default=10.0,
                    help="how long to play; 0 = until Ctrl-C")
    args = ap.parse_args()
    print(f"DDP rainbow -> {args.host}:{args.port}, {args.leds} LEDs, "
          f"{args.fps:g} fps")
    end = time.monotonic() + args.seconds if args.seconds else float("inf")
    frame = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            while time.monotonic() < end:
                for packet in packets(rainbow(args.leds, frame / 120), frame):
                    sock.sendto(packet, (args.host, args.port))
                frame += 1
                time.sleep(1 / args.fps)
        except KeyboardInterrupt:
            pass
    print(f"sent {frame} frame(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
