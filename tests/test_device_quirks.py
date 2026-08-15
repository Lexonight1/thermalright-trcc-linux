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


def test_a_silent_quirked_panel_falls_through_to_the_standard_handshake(
    monkeypatch,
) -> None:
    """A reply identifies a panel; silence identifies nothing. (#244/#267/#268)

    ``bcdDevice`` 4.07 is NOT unique to the Frozen Warframe SE — Thermalright
    ships several different panels as ``0416:5302`` firmware 4.07, and the only
    thing separating them is the PM byte the streaming read exists to fetch.
    This test used to assert the opposite (silent → identify by fingerprint →
    pin the registry placeholder), and that assertion WAS the bug: a 1280×480
    Trofeo Vision got locked to 240×320 and never displayed anything, for four
    reporters on #244 plus #268, while #267 — the SE fingerprint the quirk was
    written for — stayed broken too.  Their own logs show the standard
    handshake answering ``PM=128 SUB=1 resolution=(1280, 480)`` moments later
    on the very same hardware.

    MUTATION CHECK — make ``_connect_streaming_firmware`` return a result
    instead of ``None`` when the reply is missing, and this fails with
    ``(240, 320)``.
    """
    monkeypatch.setattr("trcc.adapters.device.hid_lcd.time.sleep", lambda *_: None)
    full = bytearray(512)
    full[0:4] = b"\xda\xdb\xdc\xdd"
    full[4], full[5], full[12] = 1, 128, 0x01     # SUB=1, PM=128 — Trofeo Vision
    transport = FakeBulkTransport()
    transport.read_script = [b"", bytes(full)]    # silent, then the real reply
    dev = _make_type2(transport)
    dev.set_quirks(quirks_for(*_WF_SE))

    result = dev.connect()

    assert dev.is_connected
    assert result.resolution == (1280, 480), (
        "the registry placeholder was pinned onto a panel that never identified "
        "itself — the #244 regression"
    )
    assert result.pm_byte == 128 and result.sub_byte == 1
    assert transport.writes, "the fall-through must send the standard init packet"


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


# ── A quirk must not depend on which OTHER command ran first (#267) ──


def _quirk_app(tmp_path, scanned: bool):
    """An App whose platform reports the SE fingerprint, optionally pre-scanned."""
    from trcc.app import App
    from trcc.core.models import DeviceInfo

    from .mock_platform import MockPlatform

    platform = MockPlatform(
        [{"type": "lcd", "name": "Frozen Warframe SE", "vid": "0416",
          "pid": "5302", "pm": 58, "sub": 0, "bcd": "0x0407"}],
        tmp_path,
    )
    app = App(platform=platform)
    if scanned:                     # what DiscoverDevices would have cached
        app.remember_scan([DeviceInfo(vid=0x0416, pid=0x5302, bcd_device=0x0407)])
    return app


def test_quirks_resolve_on_a_direct_connect_with_no_prior_discovery(
    tmp_path,
) -> None:
    """Connecting straight to a device must resolve its firmware quirks.

    Quirks are keyed on ``(vid, pid, bcdDevice)`` and only ``DiscoverDevices``
    populated the fingerprint cache — but no CLI wire command runs it.
    ``trcc display color`` / ``load-theme`` / ``led render`` and ``trcc device
    connect`` all go straight to ``ConnectDevice``, so a quirked panel got its
    quirks in the GUI and none at all from the CLI.  For the Frozen Warframe SE
    that meant being sent the very init packet its quirk exists to suppress:
    the firmware reboots on it, and the frame then goes to a device that is no
    longer listening — "153600 bytes sent", blank screen (#267).

    MUTATION CHECK: drop the enumerate-when-missing branch in
    ``App._quirks_for`` and this fails — every quirk flag comes back False.
    """
    app = _quirk_app(tmp_path, scanned=False)

    quirks = app._quirks_for(0x0416, 0x5302)

    assert quirks.skip_init, "the SE's init packet reboots it — must be skipped"
    assert quirks.hid_reports and quirks.short_handshake
    assert quirks.portrait_native and quirks.keepalive_stream


def test_a_prior_discovery_is_still_honoured_and_costs_no_rescan(
    tmp_path,
) -> None:
    """The GUI path already cached the fingerprint at splash — resolving quirks
    must use it and not enumerate USB again on every connect."""
    app = _quirk_app(tmp_path, scanned=True)
    scans = 0
    real = app.platform.scan_devices

    def counting_scan():
        nonlocal scans
        scans += 1
        return real()

    app.platform.scan_devices = counting_scan   # type: ignore[method-assign]

    quirks = app._quirks_for(0x0416, 0x5302)

    assert quirks.skip_init
    assert scans == 0, "a cached fingerprint must not trigger an enumeration"


def test_an_unknown_fingerprint_still_yields_no_quirks(tmp_path) -> None:
    """Enumerating must not invent quirks for a device that has none —
    the empty default is still the answer for an unlisted fingerprint."""
    app = _quirk_app(tmp_path, scanned=False)

    quirks = app._quirks_for(0x0402, 0x3922)     # a SCSI panel, no quirks

    assert not quirks.skip_init and not quirks.hid_reports


def test_a_missing_hidapi_does_not_break_a_connect_that_does_not_need_it(
    tmp_path, monkeypatch,
) -> None:
    """The transport override is opt-in, so hidapi only matters if it is used.

    ``hid_reports`` used to switch the transport on fingerprint alone, before
    any handshake — so every ``0416:5302`` firmware 4.07 panel was forced onto
    hidapi whether or not it needed it, and a missing or wrong ``hid`` module
    (#244, #253) failed the connect outright.  The ordinary transport now goes
    first, so a panel it can serve never touches hidapi at all.

    MUTATION CHECK: make ``attach`` build the quirk transport unconditionally
    again and this fails — the connect comes back not-ok with an ImportError
    message.
    """
    from trcc.core.commands import ConnectDevice

    monkeypatch.setattr(
        "trcc.adapters.device.transport.HIDAPI_AVAILABLE", False, raising=False)
    app = _quirk_app(tmp_path, scanned=False)

    result = app.dispatch(ConnectDevice(key="0416:5302"))

    assert result.ok, f"ordinary transport should have served this: {result.message}"


def test_the_quirk_transport_is_tried_only_after_the_ordinary_one_fails(
    tmp_path, monkeypatch,
) -> None:
    """The unproven path goes second, so it can only ever help.

    That fingerprint is shared by several different panels; at least one
    handshakes correctly on the ordinary transport at its true landscape size
    (#267), while the override has never been confirmed to drive real hardware
    by anyone.  Trying it first could take away a path that was working, so a
    failed handshake earns it exactly one retry.

    MUTATION CHECK: drop the retry from ``_handshake_with_quirk_retry`` and
    this fails — the connect reports the first failure instead of recovering.
    """
    from trcc.adapters.device.hid_lcd import HidLcd
    from trcc.core.commands import ConnectDevice
    from trcc.core.errors import HandshakeError
    from trcc.core.models import Wire

    app = _quirk_app(tmp_path, scanned=False)
    # hidapi is not installed in the test environment, and this test is about
    # the RETRY, not about hidapi — so stand the override transport in with the
    # same scripted one the ordinary path uses.
    monkeypatch.setattr(
        "trcc.adapters.device.transport.HidApiTransport",
        lambda vid, pid, serial=None: app.platform.open_transport(
            Wire.HID, vid, pid),
    )
    real_connect = HidLcd.connect
    attempts = []

    def failing_first(self):
        attempts.append(self)
        if len(attempts) == 1:
            raise HandshakeError("ordinary transport said nothing")
        return real_connect(self)

    monkeypatch.setattr(HidLcd, "connect", failing_first)

    result = app.dispatch(ConnectDevice(key="0416:5302"))

    assert len(attempts) == 2, "the override should have earned one retry"
    assert attempts[0] is not attempts[1], "the retry must rebuild the device"
    assert result.ok, f"the retry should have recovered: {result.message}"
