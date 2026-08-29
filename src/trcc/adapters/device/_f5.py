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

Cited to the wire oracle: ``ThreadSendDeviceDataALi`` in the ``USBLCDNEW``
decompile, which drives this protocol.

That oracle used to be the Jul-2025 build, and this docstring used to warn that
"the current release ships a newer ``USBLCDNEW.exe`` nobody has read".  It has
now been read — the managed component is ``USBLCDNEW.dll``, not the ``.exe``,
which is native — and **every byte constant above is confirmed against it**:
the 1040-byte request, the ``F5 00``/``F5 01`` headers, the length at [12:16],
and the 204800 default.  The identity set was NOT confirmed; it was missing a
value.  See ``VALID_IDENTITY``.
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
# their model/FBL as ``resp[0] - 1`` (0x36 -> 53, 0x65 -> 100, 0x66 -> 101).
#
# ``0x36`` (54) was missing.  ``ThreadSendDeviceDataALi`` accepts three
# identities — ``if (array[0] == 54 || array[0] == 101 || array[0] == 102)``
# (``DCReadWriteAsync.cs:759``) — and we rejected the first, so such a panel
# never got past validation.  It is a panel we already model: ``resp[0] - 1``
# is FBL 53, which ``FBL_PROFILES`` gives as 320x240.
VALID_IDENTITY = (0x36, 0x65, 0x66)

# Payload bytes per frame, by identity.  The vendor sizes its frame buffer from
# the identity at handshake — ``num2 = 204800`` by default, ``153600`` when the
# panel answered 54 (``DCReadWriteAsync.cs:776``) — and those are exactly
# 320x320x2 and 320x240x2, the RGB565 canvases of FBL 100/101 and FBL 53.
_PAYLOAD_BY_IDENTITY = {0x36: 153600}


def payload_size(identity: int) -> int:
    """Frame payload bytes for a panel answering *identity*."""
    size = _PAYLOAD_BY_IDENTITY.get(identity, DATA_SIZE)
    log.debug("f5.payload_size: identity=0x%02X -> %d bytes", identity, size)
    return size


def init_packet() -> bytes:
    """The 1040-byte handshake request: 16-byte header + 1024 zero pad."""
    header = CMD_PREFIX + b"\x00\x00\x00\x00" + b"\x00\x04\x00\x00"
    log.debug("f5.init_packet: %d-byte request", len(header) + RESPONSE_SIZE)
    return header + b"\x00" * RESPONSE_SIZE


def frame_header(length: int = DATA_SIZE) -> bytes:
    """The 16-byte frame header carrying *length* little-endian at [12:16]."""
    frame_log.debug("f5.frame_header: length=%d", length)
    return FRAME_PREFIX + b"\x00\x00\x00\x00" + struct.pack("<I", length)
