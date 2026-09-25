"""LyLcd handshake-derived geometry tests.

Two PID variants:
    0x5408 (LY)   — PM = 64 + resp[20]  (resp[20] clamped to ≥1 when ≤3)
                    SUB = resp[22] + 1
    0x5409 (LY1)  — PM = 49 + resp[20]
                    SUB = resp[22]

Both read PM from resp[20]; they differ only in the constant added and in
whether SUB gets +1.  Cited: ``DCReadWriteAsync.cs:967`` (LY) and ``:1220``
(LY1), whose records the main assembly unpacks as PM=shm[4], SUB=shm[1]
(``Form1.cs:1071``).

Both variants resolve to FBL 192 (1920×462 widescreen JPEG) by default,
disambiguated to (1280, 480) or (1920, 440) for PMs 68/69 via _FBL_192_BY_PM.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from trcc.adapters.device.ly_lcd import LyLcd
from trcc.core.models import Kind, ProductInfo, Wire

from .conftest import FakeBulkTransport

# ── Synthetic handshake response ──────────────────────────────────────


def _ly_response(*, resp20: int = 0, resp22: int = 0, resp36: int = 0,
                 size: int = 512) -> bytes:
    """Build an LY handshake response.

    Validator requires len >= 37 and resp[0]=3, resp[1]=0xFF, resp[8]=1.
    Both variants read PM from resp[20] and SUB from resp[22].
    """
    resp = bytearray(size)
    resp[0] = 3
    resp[1] = 0xFF
    resp[8] = 1
    resp[20] = resp20
    resp[22] = resp22
    resp[36] = resp36
    return bytes(resp)


def _make_ly(transport: FakeBulkTransport, *, pid: int = 0x5408,
             native_resolution: tuple[int, int] = (1920, 462)) -> LyLcd:
    info = ProductInfo(
        vid=0x0416, pid=pid,
        vendor="Winbond",
        product=f"Trofeo Vision 9.16 (pid=0x{pid:04x})",
        wire=Wire.LY, kind=Kind.LCD,
        device_type=5, fbl=192, native_resolution=native_resolution,
        orientations=(0, 180),
    )
    return LyLcd(info, transport)


# ── LY (0x5408): PM = 64 + resp[20] ─────────────────────────────────


@pytest.mark.parametrize("resp20,expected_pm", [
    (0, 65),    # ≤3 clamped to 1 → PM=65
    (1, 65),    # ≤3 clamped to 1 → PM=65
    (2, 65),    # ≤3 clamped to 1 → PM=65
    (3, 65),    # ≤3 clamped to 1 → PM=65
    (4, 68),    # 4 → PM=68 (disambiguated to 1280×480)
    (5, 69),    # 5 → PM=69 (disambiguated to 1920×440)
])
def test_ly_pm_extraction_with_clamp(
    fake_bulk: FakeBulkTransport, resp20: int, expected_pm: int,
) -> None:
    """LY variant clamps resp[20] ≤ 3 to 1 before adding 64 (C# parity)."""
    fake_bulk.read_script.append(_ly_response(resp20=resp20))
    device = _make_ly(fake_bulk, pid=0x5408)

    result = device.connect()

    assert result.pm_byte == expected_pm


@pytest.mark.parametrize("resp20,expected_resolution", [
    (1, (1920, 462)),   # PM=65 → FBL=192 base
    (2, (1920, 462)),   # clamped to 1 → PM=65 → FBL=192 base
    (4, (1280, 480)),   # PM=68 → FBL=192, disambiguated to 1280×480
    (5, (1920, 440)),   # PM=69 → FBL=192, disambiguated to 1920×440
])
def test_ly_handshake_resolution_uses_fbl_192_disambiguation(
    fake_bulk: FakeBulkTransport,
    resp20: int, expected_resolution: tuple[int, int],
) -> None:
    """LY pulls resolution from get_profile(192, PM) — disambiguated by PM."""
    fake_bulk.read_script.append(_ly_response(resp20=resp20))
    device = _make_ly(fake_bulk, pid=0x5408)

    result = device.connect()

    assert result.resolution == expected_resolution
    assert device._profile is not None
    assert device._profile.resolution == expected_resolution
    # FBL=192 is always JPEG + rotate=True
    assert device._profile.jpeg is True
    assert device._profile.rotate is True


# ── LY1 (0x5409): PM = 49 + resp[20] ────────────────────────────────


@pytest.mark.parametrize("resp20,expected_pm,expected_resolution", [
    (16, 65, (1920, 462)),    # 49+16=65 → FBL=192
    (17, 66, (1920, 462)),    # 49+17=66 → FBL=192
    (19, 68, (1280, 480)),    # 49+19=68 → FBL=192 → disambiguated
    (20, 69, (1920, 440)),    # 49+20=69 → FBL=192 → disambiguated
])
def test_ly1_pm_extraction_and_resolution(
    fake_bulk: FakeBulkTransport,
    resp20: int, expected_pm: int, expected_resolution: tuple[int, int],
) -> None:
    """LY1 reads resp[20] and adds 49 — the vendor's ``obj3[4]``.

    It used to read ``resp[36]`` and add 50, which is the vendor's ``obj3[0]``
    — a slot the main assembly does not use as PM for this record shape.
    """
    fake_bulk.read_script.append(_ly_response(resp20=resp20))
    device = _make_ly(fake_bulk, pid=0x5409)

    result = device.connect()

    assert result.pm_byte == expected_pm
    assert result.resolution == expected_resolution


# ── chunk_cmd remains PID-driven (not profile-derived) ──────────────


def test_chunk_cmd_byte_8_value_per_variant(
    fake_bulk: FakeBulkTransport,
) -> None:
    """LY uses chunk header byte[8]=1; LY1 uses byte[8]=2."""
    device_ly = LyLcd(
        ProductInfo(
            vid=0x0416, pid=0x5408,
            vendor="Winbond", product="LY",
            wire=Wire.LY, kind=Kind.LCD,
            device_type=5, fbl=192, native_resolution=(1920, 462),
            orientations=(0, 180),
        ),
        FakeBulkTransport(),
    )
    device_ly1 = LyLcd(
        ProductInfo(
            vid=0x0416, pid=0x5409,
            vendor="Winbond", product="LY1",
            wire=Wire.LY, kind=Kind.LCD,
            device_type=5, fbl=192, native_resolution=(1920, 462),
            orientations=(0, 180),
        ),
        FakeBulkTransport(),
    )
    assert device_ly._chunk_cmd == 1
    assert device_ly1._chunk_cmd == 2


# ── Disconnect clears profile ────────────────────────────────────────


def test_disconnect_clears_profile(fake_bulk: FakeBulkTransport) -> None:
    fake_bulk.read_script.append(_ly_response(resp20=1))
    device = _make_ly(fake_bulk)
    device.connect()
    assert device._profile is not None

    device.disconnect()

    assert device._profile is None
    assert device._handshake is None


# ── Public profile property ──────────────────────────────────────────


def test_profile_property_exposes_cached_profile(
    fake_bulk: FakeBulkTransport,
) -> None:
    fake_bulk.read_script.append(_ly_response(resp20=4))   # PM=68 → 1280×480
    device = _make_ly(fake_bulk, pid=0x5408)
    device.connect()

    assert device.profile is device._profile
    assert device.profile is not None
    assert device.profile.resolution == (1280, 480)
    assert device.profile.jpeg is True


def test_profile_property_is_none_pre_handshake(
    fake_bulk: FakeBulkTransport,
) -> None:
    device = _make_ly(fake_bulk)
    assert device.profile is None


# ── Wire-frame size cap (#251) ────────────────────────────────────────
#
# The LY firmware silently DROPS a JPEG over roughly half a megabyte:
# send() completes, the ACK reads back, and the glass keeps the previous
# frame with nothing in the log.  encode_jpeg has always had a
# shrink-quality loop; the wire path just never passed it a target.


def test_ly_handshake_sets_the_firmware_frame_cap(
    fake_bulk: FakeBulkTransport,
) -> None:
    """A connected LY panel carries the JPEG ceiling.

    It used to come from an LY-only ``_MAX_FRAME_BYTES = 512 * 1024``, on the
    reading that the cap was a per-wire property.  It is not: the C#'s test
    lives in ``ImageToJpg`` with no device condition, so it applies to every
    JPEG panel and ``DeviceProfile`` carries it by default.  LY keeps the cap —
    it just no longer owns it, and the value tightened to the vendor's own.
    """
    fake_bulk.read_script.append(_ly_response(resp20=4))
    device = _make_ly(fake_bulk, pid=0x5408)
    device.connect()

    assert device.profile is not None
    assert device.profile.max_frame_bytes == 450_000
    assert 0 < device.profile.max_frame_bytes <= 1024 * 1024


def test_no_wire_is_left_uncapped() -> None:
    """Every panel carries the ceiling — this assertion is deliberately the
    inverse of what it used to say.

    It read ``max_frame_bytes == 0`` on the premise that leaving other wires
    uncapped changed nothing.  It changed nothing *visible*, which is the
    problem: an uncapped JPEG panel ships frames the firmware discards in
    silence — ``send()`` completes, the ACK reads clean, the glass keeps the
    previous image, nothing is logged.  #251 was that bug found on the one wire
    someone happened to measure.

    The field is present on RGB565 profiles too and is simply unused there;
    ``encode_payload`` only consults it on the JPEG path.  One default is
    simpler than a conditional and cannot be forgotten for a new panel.
    """
    from trcc.core.protocol import DeviceProfile, get_profile

    assert DeviceProfile(width=320, height=320).max_frame_bytes == 450_000
    assert get_profile(100).max_frame_bytes == 450_000
    assert get_profile(224).max_frame_bytes == 450_000   # 854x480 JPEG


def test_encode_payload_shrinks_an_oversized_frame() -> None:
    """An oversized JPEG must degrade in quality, not vanish (#251)."""
    import random

    from PySide6.QtGui import QColor, QGuiApplication, QImage

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.protocol import DeviceProfile

    _ = QGuiApplication.instance() or QGuiApplication([])
    renderer = QtRenderer()

    # High-entropy noise — the worst case for JPEG, and what the reporter
    # hit with dithered collages.  Small canvas keeps the test fast; the
    # cap is scaled down to match so the ratio is realistic.
    random.seed(7)
    img = QImage(480, 480, QImage.Format.Format_RGB888)
    for y in range(img.height()):
        for x in range(img.width()):
            img.setPixelColor(x, y, QColor(random.randint(0, 255),
                                           random.randint(0, 255),
                                           random.randint(0, 255)))

    uncapped = renderer.encode_payload(
        img, DeviceProfile(width=480, height=480, jpeg=True))
    cap = len(uncapped) // 2
    capped = renderer.encode_payload(
        img, DeviceProfile(width=480, height=480, jpeg=True,
                           max_frame_bytes=cap))

    assert len(capped) < len(uncapped), "cap had no effect"
    assert len(capped) <= cap, "capped frame still exceeds the firmware limit"


# ── #248: the PM the reporter actually has ─────────────────────────────────


def test_pm65_is_catalogued_not_guessed(caplog) -> None:
    """#248: a Trofeo Vision 9.16 owner reporting PM=65 was told to file a bug.

    Asserting the RESOLUTION here proves nothing, and my first version of this
    test did exactly that: FBL 192's fallback is already (1920, 462), so
    deleting the PM=65 row leaves the resolution identical and the assertion
    green.  MEASURED — the row was removed and 22 tests passed.

    The catalogue row buys the difference between "catalogued" and "guessed",
    and the only place that difference is visible is the WARNING telling an
    owner of a fully supported cooler that their device is unknown.  So that
    is what is asserted, with an uncatalogued PM alongside to prove the
    warning still fires when it should.
    """
    import logging

    from trcc.core.protocol import get_profile

    with caplog.at_level(logging.WARNING, logger="trcc.core.protocol"):
        prof = get_profile(192, 65)
    assert prof.resolution == (1920, 462)
    assert not [r for r in caplog.records if "UNKNOWN PM" in r.getMessage()], (
        "PM=65 is catalogued; warning its owner to file a bug about a "
        "supported cooler is the whole of #248")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="trcc.core.protocol"):
        get_profile(192, 99)
    assert [r for r in caplog.records if "UNKNOWN PM" in r.getMessage()], (
        "an uncatalogued PM must still warn — otherwise the test above passes "
        "because nothing warns at all")


# ── #251: every ACK is timed, so a report can show what a freeze answers ────
#
# The reporter's panel froze for 10+ minutes while every send "succeeded", and
# nothing recorded what the ACK looked like while it did.  The C# re-handshakes
# when an ACK misses 100 ms; ours waits 1000 ms and ignores it.  Only the
# first ACK and each crossing of that line log at INFO/WARNING -- the lines a
# default `trcc report` keeps -- per-frame detail stays at -vvv.


def _ack_ly(monkeypatch, fake_bulk, *latencies_ms: float) -> Any:
    """An LY device whose ACK reads take the scripted latencies."""
    from trcc.adapters.device import ly_lcd

    clock: list[float] = []
    for ms in latencies_ms:
        clock += [0.0, ms / 1000]
    ticks = iter(clock)
    monkeypatch.setattr(ly_lcd.time, "monotonic", lambda: next(ticks))
    fake_bulk.read = lambda ep, n, timeout_ms=100: bytes(range(16)) * 32  # type: ignore[method-assign]
    return _make_ly(fake_bulk)


def test_the_first_ack_is_the_reports_baseline(monkeypatch, fake_bulk, caplog) -> None:
    ly = _ack_ly(monkeypatch, fake_bulk, 3.0, 4.0)
    with caplog.at_level(logging.INFO, logger="trcc.adapters.device.ly_lcd"):
        ly._write_frame(b"x" * 512)
        ly._write_frame(b"x" * 512)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["LyLcd 0416:5408: first ACK 512 byte(s) in 3.0 ms "
                     "[00 01 02 03 04 05 06 07]"]


def test_a_slow_ack_warns_once_and_recovery_is_noted(monkeypatch, fake_bulk, caplog) -> None:
    ly = _ack_ly(monkeypatch, fake_bulk, 3.0, 250.0, 300.0, 5.0)
    with caplog.at_level(logging.INFO, logger="trcc.adapters.device.ly_lcd"):
        for _ in range(4):
            ly._write_frame(b"x" * 512)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ACK took 250.0 ms, past the C#'s 100 ms" in warnings[0].getMessage()
    assert "ACKs back under 100 ms (5.0 ms)" in caplog.records[-1].getMessage()


def test_an_ack_record_never_carries_the_whole_buffer(monkeypatch, fake_bulk, caplog) -> None:
    ly = _ack_ly(monkeypatch, fake_bulk, 250.0)
    with caplog.at_level(logging.INFO, logger="trcc.adapters.device.ly_lcd"):
        ly._write_frame(b"x" * 512)
    assert all(len(r.getMessage()) < 200 for r in caplog.records)
