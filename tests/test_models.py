"""Core domain-model helpers."""
from __future__ import annotations

import pytest

from trcc.core.models import oriented_resolution, parse_resolution


@pytest.mark.parametrize("text,expected", [
    ("320x320", (320, 320)),
    ("1280x480", (1280, 480)),
    ("640X480", (640, 480)),   # case-insensitive separator
])
def test_parse_resolution_valid(text: str, expected: tuple[int, int]) -> None:
    assert parse_resolution(text) == expected


@pytest.mark.parametrize("bad", ["", "320", "x", "320x", "axb", "320x320x1"])
def test_parse_resolution_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError, match="bad resolution"):
        parse_resolution(bad)


@pytest.mark.parametrize("native,orientation,expected", [
    ((854, 480), 0, (854, 480)),     # landscape — unchanged
    ((854, 480), 180, (854, 480)),   # landscape flipped — still unchanged
    ((854, 480), 90, (480, 854)),    # portrait — swapped
    ((854, 480), 270, (480, 854)),   # portrait flipped — swapped
    ((320, 320), 90, (320, 320)),    # square — swap is a no-op
])
def test_oriented_resolution(
    native: tuple[int, int], orientation: int, expected: tuple[int, int],
) -> None:
    assert oriented_resolution(native, orientation) == expected


# ── volatile_frames — a panel fact, gated across the whole registry ──────


# Exactly the panels whose firmware reverts to the boot logo unless frames keep
# arriving.  Stated as a literal set so BOTH directions are gated: a device that
# stops being volatile fails, and a device that silently becomes volatile fails
# too.  Parametrising over ALL_DEVICES instead would only ever re-assert
# whatever the registry currently says, which gates nothing.
#
# MEASURED 2026-08-19, because it is tempting to claim more than is true: today
# NO wire carries both kinds (bulk 1/0, bulk_ali 1/0, ly 2/0, hid 0/3, scsi 0/2,
# led 0/1), so volatility IS still derivable from the wire.  The old
# wire-keyed set was not wrong about current hardware.  It becomes wrong the
# moment 0416:5406 moves to Wire.HID, where every other panel latches — which
# is precisely the merge this flag unblocks.
#
# This existed as VOLATILE_FRAME_WIRES keyed on Wire until 2026-08-19.  Only two
# of the four devices were covered by any test then (test_app_senders pinned
# 87ad:70db volatile and 0402:3922 not), so the LY pair and the Ali panel could
# have lost their keepalive with the suite still green — and a keepalive that
# stops being requested is invisible until someone's screen goes dark.
_VOLATILE_DEVICES = {
    (0x0416, 0x5406),   # Elite Vision 360 ARGB  (F5 protocol)
    (0x0416, 0x5408),   # Trofeo Vision 9.16     (LY)
    (0x0416, 0x5409),   # Trofeo Vision 9.16     (LY)
    (0x87AD, 0x70DB),   # GrandVision 360 AIO    (bulk)
}


def test_exactly_these_devices_declare_volatile_frames() -> None:
    """The volatile set is a fact about panels, not about wires."""
    from trcc.core.registry import ALL_DEVICES

    declared = {key for key, p in ALL_DEVICES.items() if p.volatile_frames}
    assert declared == _VOLATILE_DEVICES, (
        "volatile_frames drifted — a panel that needs a keepalive and stopped "
        "asking for one goes dark with no error anywhere"
    )
