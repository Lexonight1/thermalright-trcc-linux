"""A portrait-MOUNTED panel must start at 90 degrees, once.

Two reporters with the same cooler -- PM=11 SUB=5, 854x480 -- described the
same thing in different words: #203 "wont orientate properly ... I can make it
work by going 270", #262 "load-image fills the entire physical panel correctly,
but content appears rotated ~90 degrees".  Their panel is bolted into the
cooler sideways, the SUB byte says so, and we already resolved that at
handshake into ``DeviceProfile.portrait_mounted`` -- whose only consumer was a
string in the log.

The C# spends the same byte immediately: ``SetThemeInfo_ThemeML`` seeds
``themeDirection = 90`` and the portrait catalog when ``pmSub >= 5``, so a
vendor-app owner never sees the sideways picture at all.

THE SAFETY PROPERTY IS THE HALF WORTH GUARDING.  Seeding is first-boot only.
An owner who has already worked around this by setting 270 by hand has
persisted DeviceSettings, and re-seeding them would overwrite the workaround
with a different wrong answer on upgrade.  The C# gates on
``themeDirection == -1``; we gate on "no persisted entry for this key".

MUTATION CHECK -- in ``Settings.seed_mount_orientation``, delete the
``if key in self._devices:`` early return so every connect re-seeds.

MEASURED: **3 failed, 4 passed** -- ``[0]``, ``[180]`` and ``[270]`` of
``test_an_already_configured_device_is_never_reseeded``, while both first-boot
tests still pass.  ``[90]`` survives on purpose and is worth understanding: a
re-seed writes 90, the saved value IS 90, so the assertion cannot see the
difference.  That is why the parametrisation carries all four angles instead of
one -- a single-value version of this test written with 90 would guard nothing.

A mutation that only breaks the safety half is invisible to any test that
checks the feature half, and the feature half is the easy one to write.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.services.settings import Settings

# The two reporters' handshake, verbatim from their logs.
_REPORTER_PM, _REPORTER_SUB = 11, 5


def _settings(tmp_home: Path) -> Settings:
    from .conftest import FakePaths
    return Settings(FakePaths(tmp_home))


def test_the_reporters_panel_is_portrait_mounted() -> None:
    """PM=11 SUB=5 -> 854x480, portrait-mounted.  If this ever goes False the
    rest of the file is vacuous, so it is asserted rather than assumed."""
    _, profile = bulk_profile(_REPORTER_PM, _REPORTER_SUB)
    assert profile.resolution == (854, 480)
    assert profile.portrait_mounted is True


def test_a_portrait_mounted_panel_starts_upright(tmp_home: Path) -> None:
    """#203 / #262: first boot lands on 90, not 0."""
    settings = _settings(tmp_home)
    _, profile = bulk_profile(_REPORTER_PM, _REPORTER_SUB)

    degrees = settings.seed_mount_orientation("87ad:70db",
                                              profile.portrait_mounted)

    assert degrees == 90
    assert settings.for_device("87ad:70db").orientation == 90, (
        "a sideways-mounted panel still starts at 0 — the owner has to find "
        "the rotation dial to read their own screen (#203/#262)"
    )


def test_a_normally_mounted_panel_still_starts_at_zero(tmp_home: Path) -> None:
    """Every other panel is unchanged.  This is the regression half."""
    settings = _settings(tmp_home)
    _, profile = bulk_profile(64, 1)          # Wonder Vision UB 360, #169
    assert profile.portrait_mounted is False

    assert settings.seed_mount_orientation("87ad:70db",
                                           profile.portrait_mounted) == 0
    assert settings.for_device("87ad:70db").orientation == 0


@pytest.mark.parametrize("saved", [0, 90, 180, 270])
def test_an_already_configured_device_is_never_reseeded(
    tmp_home: Path, saved: int,
) -> None:
    """The safety property: an upgrade must not move a working display.

    270 is the value #203's reporter says they use to work around the bug.
    Overwriting it on upgrade would turn a fixed screen back into a broken one
    — the same class of harm as the original defect, delivered by the fix.
    """
    settings = _settings(tmp_home)
    settings.set_orientation("87ad:70db", saved)

    degrees = settings.seed_mount_orientation("87ad:70db", True)

    assert degrees == saved
    assert settings.for_device("87ad:70db").orientation == saved, (
        f"an owner who had chosen {saved}° was re-seeded on connect — their "
        f"display moves on upgrade for no reason they can see"
    )
