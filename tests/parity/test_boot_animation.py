"""Boot-animation parity — CDB sequence + zlib payloads.

Both trees implement the same USBLCD.exe protocol: phase 1 sends a
zlib-compressed first frame with cmd ``0x000201F5`` and the frame
count in CDB word2; phase 2 sends each carousel frame with cmd
``0x000301F5``, the dwell byte folded into the cmd's high byte, and
the frame index in word2.

zlib level 3 is deterministic (no entropy source), so identical input
bytes through identical compression produce identical output.  Any
diff at this layer is a real bug in the CDB build / payload routing.

Drives both implementations through a recording transport that
captures every ``send_cdb(cdb, data)`` call, then asserts the two
sequences byte-equal.
"""
from __future__ import annotations

from collections.abc import Sequence

import pytest

from tests.parity._shared import assert_bytes_equal

# =========================================================================
# Recording transports — minimal shape per tree
# =========================================================================


class _LegacyRecordingTransport:
    """Implements legacy ``ScsiTransport`` by capturing every send."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, bytes]] = []

    def open(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def send_cdb(self, cdb: bytes, data: bytes) -> bool:
        self.sent.append((bytes(cdb), bytes(data)))
        return True

    def read_cdb(self, cdb: bytes, length: int) -> bytes:
        return b""


class _NextRecordingTransport:
    """Implements next/ ``ScsiTransport``; signature carries timeout_ms."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, bytes]] = []
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def send_cdb(self, cdb: bytes, data: bytes, timeout_ms: int = 5000) -> bool:
        del timeout_ms
        self.sent.append((bytes(cdb), bytes(data)))
        return True

    def read_cdb(self, cdb: bytes, length: int, timeout_ms: int = 5000) -> bytes:
        del timeout_ms
        return b""


# =========================================================================
# Device builders — legacy + next/ in pre-connected state
# =========================================================================


def _legacy_device(
    *, width: int, height: int,
) -> tuple[object, _LegacyRecordingTransport]:
    """Build a legacy ScsiDevice short-circuited to "already initialized"."""
    from trcc.legacy.adapters.device.scsi import ScsiDevice

    transport = _LegacyRecordingTransport()
    device = ScsiDevice(
        device_path="/dev/null",
        transport=transport,         # type: ignore[arg-type]
        width=width, height=height,
        vid=0x0402, pid=0x3922,
    )
    device._initialized = True
    return device, transport


def _next_device(
    *, width: int, height: int,
) -> tuple[object, _NextRecordingTransport]:
    """Build a next/ ScsiLcd with a populated handshake."""
    from trcc.legacy.adapters.device.scsi_lcd import ScsiLcd
    from trcc.legacy.core.models import HandshakeResult, Kind, ProductInfo, Wire
    from trcc.legacy.core.protocol import get_profile

    info = ProductInfo(
        vid=0x0402, pid=0x3922, vendor="ALi", product="parity",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=100, native_resolution=(width, height),
        orientations=(0, 90, 180, 270),
    )
    transport = _NextRecordingTransport()
    device = ScsiLcd(info, transport)  # type: ignore[arg-type]
    fbl = 100 if (width, height) == (320, 320) else 36 if (width, height) == (240, 240) else 38 if (width, height) == (240, 320) else 58
    device._profile = get_profile(fbl, fbl)
    device._handshake = HandshakeResult(
        resolution=(width, height), model_id=fbl,
        pm_byte=fbl, sub_byte=0, fbl=fbl, raw_response=b"\x00" * 64,
    )
    return device, transport


# =========================================================================
# Disable wall-clock sleeps in both trees
# =========================================================================


@pytest.fixture(autouse=True)
def _zero_anim_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both trees ``time.sleep`` between boot-animation frames.  Skip
    the wait so the matrix runs in milliseconds — the byte output is
    independent of the delay between sends."""
    monkeypatch.setattr(
        "trcc.legacy.adapters.device.scsi._ANIM_FIRST_DELAY_S", 0.0, raising=False,
    )
    monkeypatch.setattr(
        "trcc.legacy.adapters.device.scsi._ANIM_FRAME_DELAY_S", 0.0, raising=False,
    )
    monkeypatch.setattr(
        "trcc.legacy.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
        raising=False,
    )
    monkeypatch.setattr(
        "trcc.legacy.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
        raising=False,
    )


# =========================================================================
# Fixture frames — deterministic content
# =========================================================================


def _synthetic_frames(
    *, count: int, width: int, height: int,
) -> list[bytes]:
    """Build *count* deterministic frames of ``width*height*2`` bytes.

    Frame *i* is filled with byte ``i`` so zlib produces a distinct
    payload per frame (otherwise dedup-style compression artifacts
    could mask off-by-one errors in the index field).
    """
    size = width * height * 2
    return [bytes([i & 0xFF]) * size for i in range(count)]


# =========================================================================
# Wire-byte sequencer — call both trees, return their `sent` lists
# =========================================================================


def _drive(
    width: int, height: int, count: int, delays_ds: Sequence[int],
) -> tuple[list[tuple[bytes, bytes]], list[tuple[bytes, bytes]]]:
    frames = _synthetic_frames(count=count, width=width, height=height)

    legacy_dev, legacy_tx = _legacy_device(width=width, height=height)
    legacy_dev.send_boot_animation(frames, list(delays_ds))  # type: ignore[attr-defined]

    next_dev, next_tx = _next_device(width=width, height=height)
    next_dev.send_boot_animation(frames, list(delays_ds))  # type: ignore[attr-defined]

    return legacy_tx.sent, next_tx.sent


# =========================================================================
# Matrix parameters — every supported resolution
# =========================================================================


_RESOLUTIONS: list[tuple[int, int]] = [
    (240, 240),
    (240, 320),
    (320, 240),
    (320, 320),
]


# =========================================================================
# Parity tests
# =========================================================================


@pytest.mark.parametrize(("width", "height"), _RESOLUTIONS,
                         ids=lambda v: f"{v}")
def test_first_frame_cdb_and_payload_match(width: int, height: int) -> None:
    """The phase-1 CDB + zlib payload are byte-identical between trees."""
    legacy_sent, next_sent = _drive(width, height, count=3, delays_ds=[5, 5, 5])
    assert legacy_sent[0][0] == next_sent[0][0], (
        f"first-frame CDB differs at {width}x{height}: "
        f"legacy={legacy_sent[0][0].hex()} next={next_sent[0][0].hex()}"
    )
    assert_bytes_equal(
        legacy_sent[0][1], next_sent[0][1],
        label=f"{width}x{height} first-frame zlib payload",
    )


@pytest.mark.parametrize(("width", "height"), _RESOLUTIONS,
                         ids=lambda v: f"{v}")
def test_full_carousel_sequence_matches(width: int, height: int) -> None:
    """Every (CDB, payload) pair across the full sequence matches."""
    count = 5
    delays = [3, 7, 12, 25, 1]            # mix of values incl. saturation cap
    legacy_sent, next_sent = _drive(width, height, count, delays_ds=delays)

    assert len(legacy_sent) == len(next_sent) == count + 1, (
        f"sent count mismatch at {width}x{height}: "
        f"legacy={len(legacy_sent)} next={len(next_sent)}"
    )
    for i, ((legacy_cdb, legacy_payload), (next_cdb, next_payload)) in enumerate(
        zip(legacy_sent, next_sent, strict=True),
    ):
        assert legacy_cdb == next_cdb, (
            f"CDB mismatch on send #{i} at {width}x{height}: "
            f"legacy={legacy_cdb.hex()} next={next_cdb.hex()}"
        )
        assert_bytes_equal(
            legacy_payload, next_payload,
            label=f"{width}x{height} carousel payload #{i}",
        )


def test_delay_byte_saturation_matches() -> None:
    """A 99-decisecond delay → wire byte saturates at 250 on both trees.

    Pinned at 320×320 (the canonical SCSI panel) since the saturation
    logic is resolution-independent.
    """
    legacy_sent, next_sent = _drive(
        320, 320, count=2, delays_ds=[99, 99],
    )
    # Carousel CDB at index 1 carries the delay byte for frame 0
    legacy_carousel = legacy_sent[1][0]
    next_carousel = next_sent[1][0]
    assert legacy_carousel == next_carousel
    # Top byte of cmd field = (delay_ds * 10), saturated at 250.
    assert legacy_carousel[3] == 250


def test_short_delays_default_to_ten_deciseconds() -> None:
    """``delays_ds`` shorter than ``frames`` — both trees should fall
    back to 10 ds for the unspecified entries."""
    legacy_sent, next_sent = _drive(
        320, 320, count=3, delays_ds=[5],
    )
    assert len(legacy_sent) == len(next_sent) == 4
    for i in range(len(legacy_sent)):
        assert legacy_sent[i] == next_sent[i], (
            f"divergence on send #{i} when delays_ds is short"
        )
