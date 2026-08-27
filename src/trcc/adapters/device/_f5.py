"""The F5 wire protocol — spoken by two device classes, defined once here.

``AliLcd`` (``Wire.BULK_ALI``, 0416:5406) and ``HidLcd``'s **type 3** variant
(``device_type != 2``, serving 0418:5303 / 0418:5304) speak the *same* protocol.
That was not a resemblance — the bytes were identical, built independently in
two files:

* the same 1040-byte handshake request (16-byte ``F5 00 …`` header + 1024 zeros)
* the same 16-byte frame header (``F5 01 …`` + length little-endian at [12:16])
* the same 204800-byte RGB565 payload, 1024-byte response, 16-byte ack
* the same identity test — ``AliLcd`` spelled it ``(101, 102)`` and ``HidLcd``
  spelled it ``(0x65, 0x66)``, which are the same two numbers

Two spellings of one fact drift, and a protocol correction applied to one file
and not the other is invisible until a panel stops working.  So the bytes live
here and both classes consume them; ``tests/test_f5_protocol.py`` asserts the
two paths still emit identical bytes, so they cannot silently diverge again.

**Scope: bytes only.**  The two classes still differ in validation strictness,
payload padding and identity parsing — real behavioural differences, documented
at each site rather than unified, because 0416:5406 is a reporter's panel
(#212) and changing what it sends is not something a mock can vouch for.

Cited to the readable wire oracle: the ``USBLCDNEW`` decompile, whose
``ThreadSendDeviceDataALi`` drives this protocol.  Note that oracle is the
Jul-2025 build; the current release ships a newer ``USBLCDNEW.exe`` nobody has
read, so these constants are faithful to the binary we *have*, not provably to
the one that ships today.
"""
from __future__ import annotations

import logging
import struct

from ...core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

# 8-byte magic opening both packets; byte 1 selects command (00) vs frame (01).
CMD_PREFIX = bytes([0xF5, 0x00, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8])
FRAME_PREFIX = bytes([0xF5, 0x01, 0x01, 0x00, 0xBC, 0xFF, 0xB6, 0xC8])

HEADER_SIZE = 16          # both packets open with a 16-byte header
RESPONSE_SIZE = 1024      # handshake read buffer
ACK_SIZE = 16             # post-frame acknowledgement read
DATA_SIZE = 204800        # 320 * 320 * 2 — fixed RGB565 canvas
INIT_SIZE = HEADER_SIZE + RESPONSE_SIZE   # 1040-byte handshake request

# The identity byte the panel answers with, at ``resp[0]``.  Both classes derive
# their model/FBL as ``resp[0] - 1`` (0x65 -> 100, 0x66 -> 101).
VALID_IDENTITY = (0x65, 0x66)


def init_packet() -> bytes:
    """The 1040-byte handshake request: 16-byte header + 1024 zero pad."""
    header = CMD_PREFIX + b"\x00\x00\x00\x00" + b"\x00\x04\x00\x00"
    log.debug("f5.init_packet: %d-byte request", len(header) + RESPONSE_SIZE)
    return header + b"\x00" * RESPONSE_SIZE


def frame_header(length: int = DATA_SIZE) -> bytes:
    """The 16-byte frame header carrying *length* little-endian at [12:16]."""
    frame_log.debug("f5.frame_header: length=%d", length)
    return FRAME_PREFIX + b"\x00\x00\x00\x00" + struct.pack("<I", length)
