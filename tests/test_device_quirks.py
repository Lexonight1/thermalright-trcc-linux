"""Per-firmware DeviceQuirks — the #228 Frozen Warframe SE (bcdDevice 4.07)
isolation mechanism.

Proves the five firmware divergences are honored for the exact fingerprint AND
that every other device is untouched (empty quirks = family default).  The
device itself is verified by the reporter (adamkoehler1990) on real hardware;
these lock the LOGIC — transport selection input, handshake acceptance, the
portrait-native transpose, keepalive, and the single-session no-reconnect gate.
"""
from __future__ import annotations

import pytest

from trcc.core.models import (
    DeviceInfo,
    DeviceQuirks,
    quirks_for,
)
from trcc.core.protocol import get_profile

from .conftest import FakeBulkTransport
from .test_hid_lcd_geometry import _make_type2

_WF_SE = (0x0416, 0x5302, 0x0407)   # Frozen Warframe SE firmware 4.07


# ── Registry: the fingerprint opts in, everything else stays default ──


def test_warframe_se_407_opts_into_every_quirk() -> None:
    q = quirks_for(*_WF_SE)
    assert q == DeviceQuirks(
        hid_reports=True, skip_init=True, short_handshake=True,
        portrait_native=True, keepalive_stream=True,
    )


@pytest.mark.parametrize("fingerprint", [
    (0x0416, 0x5302, 0x0100),   # same panel, different firmware revision
    (0x0416, 0x5302, 0x0000),   # unknown bcdDevice
    (0x0402, 0x3922, 0x0407),   # different device, same bcdDevice
])
def test_other_devices_get_no_quirks(fingerprint: tuple[int, int, int]) -> None:
    assert quirks_for(*fingerprint) == DeviceQuirks()


def test_device_info_quirks_property() -> None:
    assert DeviceInfo(vid=0x0416, pid=0x5302, bcd_device=0x0407).quirks.hid_reports
    assert DeviceInfo(vid=0x0416, pid=0x5302, bcd_device=0x0101).quirks == DeviceQuirks()


# ── Seam 3: short handshake acceptance ────────────────────────────────


def _short_reply() -> bytes:
    # The firmware's real 8-byte reply: magic + SUB@[4]=0 + PM@[5]=0x3a(58)
    return bytes([0xDA, 0xDB, 0xDC, 0xDD, 0x00, 0x3A, 0x00, 0x00])


def test_short_handshake_rejected_without_quirk() -> None:
    dev = _make_type2(FakeBulkTransport())
    # Default quirks: the 8-byte reply fails the >=20-byte validator.
    assert dev._validate_response_type2(_short_reply()) is False


def test_short_handshake_accepted_with_quirk() -> None:
    dev = _make_type2(FakeBulkTransport())
    dev.set_quirks(quirks_for(*_WF_SE))
    assert dev._validate_response_type2(_short_reply()) is True
    # Garbage without the magic is still rejected even with the quirk.
    assert dev._validate_response_type2(b"\x00\x01\x02\x03\x04\x05") is False


# ── Seam 4: portrait-native transpose ─────────────────────────────────


def test_portrait_native_transposes_and_drops_rotate() -> None:
    dev = _make_type2(FakeBulkTransport())
    dev.set_quirks(quirks_for(*_WF_SE))
    base = get_profile(58, 58)                    # 320x240 rotate=True
    assert base.resolution == (320, 240) and base.rotate is True
    native = dev._portrait_native(base)
    assert native.resolution == (240, 320)        # portrait raster
    assert native.rotate is False                 # device self-orients


def test_portrait_native_is_noop_without_quirk() -> None:
    dev = _make_type2(FakeBulkTransport())        # default quirks
    base = get_profile(58, 58)
    assert dev._portrait_native(base) is base


# ── Seams 2+3+4 together: the streaming-firmware connect ──────────────


def test_streaming_connect_skips_init_and_pins_portrait(monkeypatch) -> None:
    monkeypatch.setattr("trcc.adapters.device.hid_lcd.time.sleep", lambda *_: None)
    transport = FakeBulkTransport()
    transport.read_script = [_short_reply()]
    dev = _make_type2(transport)
    dev.set_quirks(quirks_for(*_WF_SE))

    result = dev.connect()

    # NO init packet was written (it reboots this firmware).
    assert transport.writes == []
    # Connected, portrait-native, PM/SUB parsed from the short reply.
    assert dev.is_connected
    assert result.resolution == (240, 320)
    assert result.pm_byte == 58 and result.sub_byte == 0
    assert dev.profile is not None and dev.profile.rotate is False


def test_streaming_connect_works_without_any_reply(monkeypatch) -> None:
    # A firmware that volunteers nothing: identify by fingerprint, still connect.
    monkeypatch.setattr("trcc.adapters.device.hid_lcd.time.sleep", lambda *_: None)
    transport = FakeBulkTransport()               # empty read_script → b""
    dev = _make_type2(transport)
    dev.set_quirks(quirks_for(*_WF_SE))
    result = dev.connect()
    assert dev.is_connected
    assert result.resolution == (240, 320)        # registry FBL 58, transposed


# ── Seam 5: keepalive ─────────────────────────────────────────────────


def test_keepalive_stream_quirk_marks_needs_keepalive() -> None:
    dev = _make_type2(FakeBulkTransport())
    assert dev.needs_keepalive is False           # HID wire is not volatile
    dev.set_quirks(quirks_for(*_WF_SE))
    assert dev.needs_keepalive is True


# ── Isolation: a normal Type-2 device is completely unaffected ────────


def test_normal_type2_still_uses_full_handshake(monkeypatch) -> None:
    monkeypatch.setattr("trcc.adapters.device.hid_lcd.time.sleep", lambda *_: None)
    transport = FakeBulkTransport()
    full = bytearray(512)
    full[0:4] = b"\xda\xdb\xdc\xdd"
    full[4], full[5], full[12] = 0, 58, 0x01
    transport.read_script = [bytes(full)]
    dev = _make_type2(transport)                  # no quirks

    result = dev.connect()

    # The normal path DID write the init packet and used the >=20-byte reply.
    assert len(transport.writes) == 1
    assert result.resolution == (320, 240)        # landscape, rotate=True path
    assert dev.needs_keepalive is False
