"""LED PM-byte registry + Led.connect wiring.

Two layers:

  1. ``core/led_protocol.py`` purity: every PM byte in the legacy
     registry maps to the correct style + model name + style_sub;
     PA120 variant range (PMs 17-31 except 23) all flatten to PA120;
     unknown PM returns None.

  2. ``adapters/device/led.py`` integration: a successful handshake
     populates ``LedHandshakeResult`` via the registry, not via the
     static ``ProductInfo.led_style`` / ``ProductInfo.product`` that
     used to be the only source.
"""
from __future__ import annotations

import pytest

from trcc.next.adapters.device.led import (
    _HID_REPORT_SIZE,
    _MAGIC,
    Led,
)
from trcc.next.core.led_protocol import (
    _PM_REGISTRY,
    PmEntry,
    resolve_pm,
)
from trcc.next.core.models import Kind, LedStyle, ProductInfo, Wire

from .conftest import FakeBulkTransport

# ── Layer 1: registry purity ──────────────────────────────────────────


@pytest.mark.parametrize("pm,expected", sorted(_PM_REGISTRY.items()))
def test_resolve_pm_returns_registered_entry(pm: int, expected: PmEntry) -> None:
    """Every registered PM byte must resolve to its declared entry."""
    assert resolve_pm(pm) == expected


def test_resolve_pm_unknown_returns_none() -> None:
    """A PM byte outside the registry must resolve to None, not raise."""
    assert resolve_pm(99) is None
    assert resolve_pm(255) is None
    assert resolve_pm(0) is None


@pytest.mark.parametrize("pm", [17, 18, 19, 20, 21, 22, 24, 25, 26, 27, 28, 29, 30, 31])
def test_pa120_variant_range_resolves_to_pa120_style(pm: int) -> None:
    """PMs 17-22 + 24-31 are all PA120 firmware variants.

    Legacy ``PmRegistry`` builds these with a dict comprehension over
    ``range(17, 32) if pm not in (23,)``. Next/ unrolls them; this test
    locks that the unroll didn't drop or misclassify any entry.
    """
    entry = resolve_pm(pm)
    assert entry is not None
    assert entry.style is LedStyle.PA120
    assert entry.model_name == "PA120_DIGITAL"
    assert entry.style_sub == 0


def test_pm_23_is_rk120_not_pa120() -> None:
    """PM=23 is the carve-out from the PA120 range — RK120, not PA120."""
    entry = resolve_pm(23)
    assert entry is not None
    assert entry.style is LedStyle.PA120
    assert entry.model_name == "RK120_DIGITAL"


@pytest.mark.parametrize("pm,expected_sub", [(129, 1), (176, 1)])
def test_style_sub_entries_carry_their_sub_variant(
    pm: int, expected_sub: int,
) -> None:
    """LF11 (PM=129) and LF25 (PM=176) ship with style_sub=1."""
    entry = resolve_pm(pm)
    assert entry is not None
    assert entry.style_sub == expected_sub


def test_lf15_and_lf13_present() -> None:
    """LedStyle enum was extended to cover legacy style_ids 11+12.

    PM=144 → LF15 and PM=160 → LF13 would otherwise blow up on the
    enum lookup. Lock both styles end-to-end (enum value + resolve).
    """
    assert LedStyle.LF15 == "lf15"
    assert LedStyle.LF13 == "lf13"
    assert resolve_pm(144) == PmEntry(LedStyle.LF15, "LF15")
    assert resolve_pm(160) == PmEntry(LedStyle.LF13, "LF13")


# ── Layer 2: Led.connect populates LedHandshakeResult via registry ────


def _led_info() -> ProductInfo:
    """The one LED row in the next/ registry (no led_style hint)."""
    return ProductInfo(
        vid=0x0416, pid=0x8001,
        vendor="Winbond",
        product="LED Controller (FormLED)",
        wire=Wire.LED, kind=Kind.LED,
        device_type=1,
    )


def _scripted_handshake_response(pm: int, sub: int) -> bytes:
    """Build a 64-byte LED handshake reply with the given PM/SUB bytes.

    Mirrors Windows DeviceDataReceived1: bytes [0..3]=MAGIC, [4]=SUB,
    [5]=PM, [12]=1 (cmd ACK).
    """
    buf = bytearray(_HID_REPORT_SIZE)
    buf[0:4] = _MAGIC
    buf[4] = sub
    buf[5] = pm
    buf[12] = 1
    return bytes(buf)


@pytest.mark.parametrize("pm,expected_style,expected_name", [
    (1,   LedStyle.AX120, "FROZEN_HORIZON_PRO"),
    (32,  LedStyle.AK120, "AK120_DIGITAL"),
    (80,  LedStyle.LF12,  "LF12"),
    (128, LedStyle.LC1,   "LC1"),
    (144, LedStyle.LF15,  "LF15"),
    (208, LedStyle.CZ1,   "CZ1"),
])
def test_led_connect_populates_handshake_from_registry(
    pm: int, expected_style: LedStyle, expected_name: str,
) -> None:
    """Drives the full Led.connect with a scripted PM byte.

    The handshake result must reflect the registry resolution, not the
    static ProductInfo.led_style (which is None for the LED registry row).
    """
    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_handshake_response(pm, sub=0))
    led = Led(_led_info(), transport)

    led.connect()

    hs = led.led_handshake
    assert hs is not None
    assert hs.pm == pm
    assert hs.style is expected_style
    assert hs.model_name == expected_name


def test_led_connect_style_sub_propagated() -> None:
    """LF11 ships with style_sub=1 — must reach LedHandshakeResult."""
    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_handshake_response(pm=129, sub=0))
    led = Led(_led_info(), transport)

    led.connect()

    hs = led.led_handshake
    assert hs is not None
    assert hs.style is LedStyle.LF11
    assert hs.style_sub == 1


def test_led_connect_unknown_pm_falls_back_to_registry_defaults(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown PM → log warning, populate from ProductInfo defaults.

    LED registry row carries ``led_style=None`` and ``product=...`` so
    a unknown-firmware device still hands callers something sensible
    rather than blowing up.
    """
    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_handshake_response(pm=99, sub=0))
    led = Led(_led_info(), transport)

    with caplog.at_level("WARNING"):
        led.connect()

    hs = led.led_handshake
    assert hs is not None
    assert hs.pm == 99
    assert hs.style is None
    assert hs.model_name == "LED Controller (FormLED)"
    assert any("unknown PM=99" in r.message for r in caplog.records)
