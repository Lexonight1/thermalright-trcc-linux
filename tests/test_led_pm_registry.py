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

from pathlib import Path

import pytest

from trcc.adapters.device.led import (
    _HID_REPORT_SIZE,
    _MAGIC,
    Led,
)
from trcc.core.led_protocol import (
    _PM_REGISTRY,
    PmEntry,
    is_fingerprint_header,
    resolve_handshake,
    resolve_model_name,
    resolve_pm,
)
from trcc.core.models import Kind, LedStyle, ProductInfo, Wire

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


# =========================================================================
# Probe cache — survives app restart on same power cycle
# =========================================================================


def test_led_probe_cache_save_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful handshake writes (pm, sub, model_name) to disk."""
    import json as _json

    from trcc.adapters.device import led as led_mod
    cache_path = tmp_path / "led_probe_cache.json"
    monkeypatch.setattr(led_mod, "_PROBE_CACHE_PATH", cache_path)

    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_handshake_response(pm=80, sub=0))
    led = Led(_led_info(), transport)
    led.connect()

    assert cache_path.is_file()
    cache = _json.loads(cache_path.read_text(encoding="utf-8"))
    key = f"{0x0416:04x}_{0x8001:04x}"
    assert key in cache
    assert cache[key]["pm"] == 80
    assert cache[key]["sub"] == 0
    assert cache[key]["model_name"] == "LF12"


def test_led_probe_cache_falls_back_when_handshake_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live handshake unanswered → use the disk-cached entry instead.

    Simulates the "second app launch on same power cycle" case: the
    LED firmware already answered its one-shot handshake on the first
    launch (cache populated), so the second launch gets nothing from
    the wire but recovers identity from the cache.
    """
    import json as _json

    from trcc.adapters.device import led as led_mod

    # Seed the cache with a known-good entry (as if a previous launch
    # had successfully handshaken PM=80 SUB=0).
    cache_path = tmp_path / "led_probe_cache.json"
    cache_path.write_text(_json.dumps({
        f"{0x0416:04x}_{0x8001:04x}": {
            "pm": 80, "sub": 0, "model_name": "LF12",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(led_mod, "_PROBE_CACHE_PATH", cache_path)

    transport = FakeBulkTransport()
    # Empty read_script → reads raise → all handshake retries fail.
    led = Led(_led_info(), transport)

    result = led.connect()

    # Live handshake exhausted, fell through to cache — handshake_result
    # is reconstructed from the cached identity.
    assert result.pm_byte == 80
    assert result.sub_byte == 0
    hs = led.led_handshake
    assert hs is not None
    assert hs.pm == 80
    assert hs.style is LedStyle.LF12
    assert hs.model_name == "LF12"


def test_led_probe_cache_handles_corrupt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON cache file is treated as empty — re-saved fresh on
    next successful handshake.  Never blocks a real handshake."""
    from trcc.adapters.device import led as led_mod
    cache_path = tmp_path / "led_probe_cache.json"
    cache_path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(led_mod, "_PROBE_CACHE_PATH", cache_path)

    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_handshake_response(pm=128, sub=0))
    led = Led(_led_info(), transport)

    result = led.connect()

    assert result.pm_byte == 128
    # Corrupt file got overwritten with the successful result.
    import json as _json
    cache = _json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache[f"{0x0416:04x}_{0x8001:04x}"]["pm"] == 128


# =========================================================================
# Magic Qube — handshake fingerprint override (shares PM=208 with CZ1)
# =========================================================================

_QUBE_HEADER = bytes([0xDC, 0xDD, 0xAA, 0x01])


def _scripted_qube_response() -> bytes:
    """A 64-byte Magic Qube handshake reply.

    Mirrors the real hardware capture ``dc dd aa 01 00 d0 d0 …``: the
    non-standard header, SUB=0, PM=208 at both offset 5 and 6, and no
    cmd-ACK byte at offset 12 (the firmware leaves it 0).
    """
    buf = bytearray(_HID_REPORT_SIZE)
    buf[0:4] = _QUBE_HEADER
    buf[4] = 0      # SUB
    buf[5] = 208    # PM (0xD0)
    buf[6] = 208
    return bytes(buf)


def test_resolve_handshake_header_override_wins() -> None:
    """The Magic Qube header routes PM=208 to MAGIC_QUBE, not CZ1."""
    entry = resolve_handshake(_QUBE_HEADER, 208, 0)
    assert entry == PmEntry(LedStyle.MAGIC_QUBE, "MAGIC_QUBE")


def test_resolve_handshake_standard_header_keeps_registry() -> None:
    """A standard header falls through to the PM registry (real CZ1)."""
    assert resolve_handshake(_MAGIC, 208, 0) == PmEntry(LedStyle.CZ1, "CZ1")
    assert resolve_handshake(_MAGIC, 80, 0) == PmEntry(LedStyle.LF12, "LF12")


def test_resolve_model_name_recovers_override() -> None:
    """Cached fingerprint devices resolve back by model name."""
    assert resolve_model_name("MAGIC_QUBE") == PmEntry(
        LedStyle.MAGIC_QUBE, "MAGIC_QUBE",
    )
    assert resolve_model_name("CZ1") is None   # PM-registry devices excluded


def test_is_fingerprint_header() -> None:
    """The Magic Qube header is a known fingerprint; the standard magic isn't."""
    assert is_fingerprint_header(_QUBE_HEADER) is True
    assert is_fingerprint_header(_MAGIC) is False
    assert is_fingerprint_header(bytes(4)) is False


def test_led_connect_magic_qube_from_header() -> None:
    """Full Led.connect with the Magic Qube header → MAGIC_QUBE style."""
    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_qube_response())
    led = Led(_led_info(), transport)

    led.connect()

    hs = led.led_handshake
    assert hs is not None
    assert hs.pm == 208
    assert hs.style is LedStyle.MAGIC_QUBE
    assert hs.model_name == "MAGIC_QUBE"


def test_led_connect_magic_qube_no_anomaly_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A recognised fingerprint header must not log magic/cmd anomaly warnings."""
    transport = FakeBulkTransport()
    transport.read_script.append(_scripted_qube_response())
    led = Led(_led_info(), transport)

    with caplog.at_level("WARNING"):
        led.connect()

    assert not any("unexpected magic" in r.message for r in caplog.records)
    assert not any("unexpected cmd" in r.message for r in caplog.records)


def test_led_connect_cache_fallback_preserves_magic_qube(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached Magic Qube recovers as MAGIC_QUBE, not CZ1.

    The cache persists the model name but not the handshake header, so
    the fallback must recover the fingerprint device by name — otherwise
    PM=208 would resolve to CZ1 and drive the wrong layout.
    """
    import json as _json

    from trcc.adapters.device import led as led_mod
    cache_path = tmp_path / "led_probe_cache.json"
    cache_path.write_text(_json.dumps({
        f"{0x0416:04x}_{0x8001:04x}": {
            "pm": 208, "sub": 0, "model_name": "MAGIC_QUBE",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(led_mod, "_PROBE_CACHE_PATH", cache_path)

    transport = FakeBulkTransport()   # empty script → live handshake fails
    led = Led(_led_info(), transport)

    led.connect()

    hs = led.led_handshake
    assert hs is not None
    assert hs.style is LedStyle.MAGIC_QUBE
    assert hs.model_name == "MAGIC_QUBE"
