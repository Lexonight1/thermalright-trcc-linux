"""ScsiLcd.send_boot_animation — wire-byte correctness.

Verifies that the compressed multi-frame upload protocol matches the
USBLCD.exe reverse-engineered sequence:

  * Phase 1: first frame, command 0x000201F5, frame_count in CDB word2.
  * Phase 2: each carousel frame, command 0x000301F5 with the per-frame
    dwell byte folded into the cmd's high byte (delay_ds * 10, capped
    at 250); frame index in CDB word2.

Payloads must round-trip through zlib decompression to the input bytes.
"""
from __future__ import annotations

import struct
import zlib

import pytest

from trcc.adapters.device.scsi_lcd import ScsiLcd
from trcc.core.errors import TransportError
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeScsiTransport

_ANIM_FIRST_CMD = 0x000201F5
_ANIM_CAROUSEL_CMD = 0x000301F5


def _poll_response(fbl: int, *, size: int = 0xE100) -> bytes:
    buf = bytearray(size)
    buf[0] = fbl
    return bytes(buf)


def _make_scsi(transport: FakeScsiTransport, *, fbl: int = 100,
               native: tuple[int, int] = (320, 320)) -> ScsiLcd:
    info = ProductInfo(
        vid=0x0402, pid=0x3922,
        vendor="ALi Corp", product="Frozen Warframe LCD",
        wire=Wire.SCSI, kind=Kind.LCD,
        device_type=1, fbl=fbl, native_resolution=native,
        orientations=(0, 90, 180, 270),
    )
    return ScsiLcd(info, transport)


def _connect(transport: FakeScsiTransport, fbl: int = 100,
             native: tuple[int, int] = (320, 320),
             monkeypatch: pytest.MonkeyPatch | None = None) -> ScsiLcd:
    if monkeypatch is not None:
        monkeypatch.setattr(
            "trcc.adapters.device.scsi_lcd._POST_INIT_DELAY_S", 0.0,
        )
    transport.read_script.append(_poll_response(fbl))
    device = _make_scsi(transport, fbl=fbl, native=native)
    device.connect()
    transport.sent.clear()       # drop the init-cdb so we only see anim writes
    return device


def _parse_anim_cdb(cdb: bytes) -> tuple[int, int, int]:
    """Unpack (cmd, word2, compressed_size) from a 16-byte anim CDB."""
    assert len(cdb) == 16
    cmd, _zero, word2, csize = struct.unpack("<IIII", cdb)
    return cmd, word2, csize


# ── Resolution gate ──────────────────────────────────────────────────


def test_rejects_unsupported_resolution(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """480×480 (FBL=72) isn't in the boot-anim resolution set → reject + log."""
    device = _connect(fake_scsi, fbl=72, native=(480, 480), monkeypatch=monkeypatch)
    uploaded = device.send_boot_animation([b"\x00" * 100], delays_ds=[10])
    assert uploaded == 0
    assert fake_scsi.sent == []   # nothing went on the wire


# ── Frame-count gate ─────────────────────────────────────────────────


def test_rejects_zero_frames(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _connect(fake_scsi, monkeypatch=monkeypatch)
    assert device.send_boot_animation([], delays_ds=[]) == 0
    assert fake_scsi.sent == []


def test_rejects_too_many_frames(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """249 frames must be rejected (firmware cap)."""
    device = _connect(fake_scsi, monkeypatch=monkeypatch)
    frames = [b"\x00" * 100] * 249
    assert device.send_boot_animation(frames, delays_ds=[10] * 249) == 0


# ── Pre-connect ──────────────────────────────────────────────────────


def test_raises_when_not_connected(fake_scsi: FakeScsiTransport) -> None:
    device = _make_scsi(fake_scsi)
    with pytest.raises(TransportError, match="not connected"):
        device.send_boot_animation([b"\x00" * 100], delays_ds=[10])


# ── First-frame CDB ──────────────────────────────────────────────────


def test_first_frame_cdb_has_total_count_in_word2(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1 CDB carries the total frame count, no delay byte folded in."""
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
    )
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
    )
    device = _connect(fake_scsi, monkeypatch=monkeypatch)
    frames = [b"A" * 256, b"B" * 256, b"C" * 256]

    uploaded = device.send_boot_animation(frames, delays_ds=[5, 5, 5])

    assert uploaded == 3
    assert len(fake_scsi.sent) == 4   # 1 first + 3 carousel

    first_cdb, first_payload = fake_scsi.sent[0]
    cmd, word2, csize = _parse_anim_cdb(first_cdb)
    assert cmd == _ANIM_FIRST_CMD, f"Phase-1 cmd should be 0x{_ANIM_FIRST_CMD:08x}"
    assert word2 == 3              # total frame count
    assert csize == len(first_payload)
    # Payload round-trips through zlib to the source bytes
    assert zlib.decompress(first_payload) == b"A" * 256


# ── Carousel-frame CDB ───────────────────────────────────────────────


def test_carousel_cdbs_carry_index_and_delay_byte(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each carousel frame CDB: index in word2, delay_ds*10 in cmd[31:24]."""
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
    )
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
    )
    device = _connect(fake_scsi, monkeypatch=monkeypatch)
    frames = [b"X" * 128, b"Y" * 128]
    delays = [3, 7]                 # 3 ds → 30, 7 ds → 70

    device.send_boot_animation(frames, delays_ds=delays)

    # Phase 2 entries are positions 1 and 2 in the recorded list
    car_a_cdb, car_a_payload = fake_scsi.sent[1]
    cmd_a, word2_a, csize_a = _parse_anim_cdb(car_a_cdb)
    assert cmd_a & 0x00FFFFFF == _ANIM_CAROUSEL_CMD
    assert (cmd_a >> 24) & 0xFF == 30          # 3 ds × 10
    assert word2_a == 0                         # frame index 0
    assert csize_a == len(car_a_payload)
    assert zlib.decompress(car_a_payload) == b"X" * 128

    car_b_cdb, _ = fake_scsi.sent[2]
    cmd_b, word2_b, _ = _parse_anim_cdb(car_b_cdb)
    assert cmd_b & 0x00FFFFFF == _ANIM_CAROUSEL_CMD
    assert (cmd_b >> 24) & 0xFF == 70          # 7 ds × 10
    assert word2_b == 1


def test_carousel_delay_byte_caps_at_250(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 25 ds delay → 250 on the wire; a 99 ds delay also saturates at 250."""
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
    )
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
    )
    device = _connect(fake_scsi, monkeypatch=monkeypatch)
    frames = [b"P" * 64, b"Q" * 64]

    device.send_boot_animation(frames, delays_ds=[25, 99])

    cmd_25, _, _ = _parse_anim_cdb(fake_scsi.sent[1][0])
    cmd_99, _, _ = _parse_anim_cdb(fake_scsi.sent[2][0])
    assert (cmd_25 >> 24) & 0xFF == 250
    assert (cmd_99 >> 24) & 0xFF == 250


def test_short_delays_default_to_ten_deciseconds(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When delays_ds is shorter than frames, missing entries default to 10 ds."""
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
    )
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
    )
    device = _connect(fake_scsi, monkeypatch=monkeypatch)
    frames = [b"M" * 32] * 3

    device.send_boot_animation(frames, delays_ds=[5])  # only 1 explicit

    # Frame 0 uses explicit delay 5 → wire byte 50
    cmd0, _, _ = _parse_anim_cdb(fake_scsi.sent[1][0])
    assert (cmd0 >> 24) & 0xFF == 50

    # Frames 1, 2 fall back to default 10 ds → wire byte 100
    cmd1, _, _ = _parse_anim_cdb(fake_scsi.sent[2][0])
    cmd2, _, _ = _parse_anim_cdb(fake_scsi.sent[3][0])
    assert (cmd1 >> 24) & 0xFF == 100
    assert (cmd2 >> 24) & 0xFF == 100


# ── Mid-stream failure returns partial count ─────────────────────────


def test_mid_stream_send_failure_returns_partial_count(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two frames in, transport fails — return index of failure (i.e. 2)."""
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
    )
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
    )
    device = _connect(fake_scsi, monkeypatch=monkeypatch)

    # Fail every send after the 3rd recorded CDB (1 first + 2 carousel = 3).
    real_send = fake_scsi.send_cdb
    call_count = {"n": 0}

    def flaky(cdb: bytes, data: bytes, timeout_ms: int = 5000) -> bool:
        call_count["n"] += 1
        if call_count["n"] >= 4:
            return False
        return real_send(cdb, data, timeout_ms)

    monkeypatch.setattr(fake_scsi, "send_cdb", flaky)

    uploaded = device.send_boot_animation(
        [b"\x01" * 64, b"\x02" * 64, b"\x03" * 64],
        delays_ds=[5, 5, 5],
    )

    assert uploaded == 2   # frame index where failure happened


def test_first_frame_failure_returns_zero(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the first-frame send fails, return 0 — nothing else gets sent."""
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
    )
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
    )
    device = _connect(fake_scsi, monkeypatch=monkeypatch)
    fake_scsi.send_should_succeed = False

    uploaded = device.send_boot_animation([b"\x00" * 64], delays_ds=[5])

    assert uploaded == 0
    # Only the first frame CDB was attempted, no carousel writes
    assert len(fake_scsi.sent) == 1


# ── All supported resolutions accepted ───────────────────────────────


@pytest.mark.parametrize("fbl,resolution", [
    (36, (240, 240)),   # FBL 36 → 240×240
    (38, (240, 320)),   # FBL 38 → 240×320 portrait
    (58, (320, 240)),   # FBL 58 → 320×240 landscape
    (100, (320, 320)),  # FBL 100 → 320×320
])
def test_all_supported_resolutions_accepted(
    fake_scsi: FakeScsiTransport, monkeypatch: pytest.MonkeyPatch,
    fbl: int, resolution: tuple[int, int],
) -> None:
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
    )
    monkeypatch.setattr(
        "trcc.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
    )
    device = _connect(fake_scsi, fbl=fbl, native=resolution, monkeypatch=monkeypatch)

    uploaded = device.send_boot_animation([b"\x00" * 64], delays_ds=[10])

    assert uploaded == 1, f"FBL={fbl} resolution={resolution} should be accepted"
