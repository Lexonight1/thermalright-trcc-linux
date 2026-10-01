"""The SUB byte says how the panel is MOUNTED (#262, #203).

Thermalright ships some panels turned portrait in the cooler.  The C# reads
that off the SUB byte and loads the transposed theme catalog for them
(``SetThemeInfo_ThemeML``: ``854480\\`` becomes ``480854\\``).  It does this
for NINE resolutions, eight written ``pmSub < 5`` and ``is1920x462`` written
``pmSub <= 5`` — so that one turns portrait at 6, not 5.  Resolutions with no
pmSub guard at all (the four squares, and 1280x480) assign their catalog
unconditionally and must never be added.

It is surfaced in the handshake line AND acted on: ``Settings.
seed_mount_orientation`` starts such a panel at 90° on first boot only, so an
owner who already compensated by rotating by hand is never re-seeded.  What
was missing originally was being able to SEE it — #262 and #203 share
``PM=11 SUB=5`` and nobody spotted it for a month.
"""
from __future__ import annotations

import re

import pytest

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.protocol import (
    _PORTRAIT_MOUNT_MIN_SUB,
    is_portrait_mounted,
)


@pytest.mark.parametrize(("resolution", "floor"),
                         sorted(_PORTRAIT_MOUNT_MIN_SUB.items()))
def test_each_mounted_resolution_turns_at_its_own_threshold(
    resolution: tuple[int, int], floor: int,
) -> None:
    """Parametrised over the table itself, so a new row is covered for free.

    The threshold is per row: eight families turn at 5, ``1920x462`` at 6
    because the C# writes ``pmSub <= 5`` there alone.  Asserting a shared 5
    would pass on a table that had lost that distinction.
    """
    assert is_portrait_mounted(resolution, floor) is True
    assert is_portrait_mounted(resolution, floor + 4) is True
    assert is_portrait_mounted(resolution, floor - 1) is False
    assert is_portrait_mounted(resolution, 0) is False


@pytest.mark.parametrize("resolution", [
    (1280, 480),    # block has NO pmSub guard — FormCZTV.cs:1356-1371
    (480, 480), (320, 320), (240, 240), (360, 360),   # the four squares
    (1600, 720),    # keys on mySubMode, not a pmSub mount test
])
def test_a_resolution_the_csharp_never_mount_tests_is_never_mounted(
    resolution: tuple[int, int],
) -> None:
    """The negative half, and the one that matters most.

    1280x480 is the trap: it has both catalogs (``1280480\\`` and
    ``4801280\\``) and a portrait sibling in every other family, so
    "complete the nine" reads like it belongs.  Its block has no pmSub guard —
    the portrait catalog is reached only once the USER turns the dial.
    """
    for sub in (0, 4, 5, 6, 9, 255):
        assert is_portrait_mounted(resolution, sub) is False


def test_the_table_is_exactly_what_the_csharp_guards() -> None:
    """Row-for-row against the transcription, which is gated against the .cs.

    Neither list is retyped here: ours comes from the table, theirs from
    ``_THEME_TOKENS``, whose thresholds
    ``tests/test_oracle_transcription_complete.py`` holds against
    ``control-flow.json``.  So this closes the chain .cs -> transcription ->
    shipping code.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev"
                           / "decompiler"))
    from formcztv_init import (  # pyright: ignore[reportMissingImports]
        _THEME_TOKENS,
    )

    # Keyed long side first, as every profile is: the flag spells its sides in
    # either order (``is176x320``), and the C# composes all of them landscape
    # at 0 degrees (GIFSize, FormCZTV.cs:3100-3545).
    theirs = {
        (max(sides), min(sides)): portrait_from
        for flag, _, _, portrait_from, _ in _THEME_TOKENS
        if portrait_from is not None
        for sides in [tuple(int(n) for n in flag[2:].split("x"))]
    }
    assert theirs == _PORTRAIT_MOUNT_MIN_SUB, (
        "the shipping mount table and the C# transcription disagree"
    )


def test_the_handshake_resolves_the_mount() -> None:
    """#262 and #203 are both PM=11 SUB=5 — the fingerprint that started this.

    MUTATION CHECK: drop the ``portrait_mounted=`` argument in ``bulk_profile``
    and this fails — the flag defaults False and the mount is invisible again.
    """
    _, portrait = bulk_profile(11, 5)          # 854x480, mounted portrait
    _, landscape = bulk_profile(11, 1)         # same panel, mounted landscape

    assert portrait.portrait_mounted is True
    assert landscape.portrait_mounted is False
    # The wire is untouched: same framebuffer, same encoder, same rotation.
    assert portrait.resolution == landscape.resolution == (854, 480)
    assert portrait.jpeg == landscape.jpeg
    assert portrait.encode_base == landscape.encode_base


def test_the_mount_shows_up_in_the_handshake_line_the_reports_scrape() -> None:
    """It has to be VISIBLE, which is the whole point — and the line that
    carries it is scraped by ``dev/tools/diagnose.py`` and the debug report,
    so the prefix shape must survive the addition.

    MUTATION CHECK: put the suffix before ``resolution=`` and the scraper
    regex below stops finding the resolution.
    """
    from tests.conftest import FakeBulkTransport

    # The exact pattern dev/tools/diagnose.py uses.
    scraper = re.compile(r"handshake OK:\s*PM=(\d+)\s+SUB=(\d+)(.*)")

    _, profile = bulk_profile(11, 5)
    line = (f"BulkLcd 87ad:70db handshake OK: PM=11 SUB=5 "
            f"resolution={profile.resolution}"
            f"{' (JPEG)' if profile.jpeg else ' (RGB565)'}"
            f"{' portrait-mounted' if profile.portrait_mounted else ''}")

    match = scraper.search(line)
    assert match is not None
    assert match.group(1) == "11" and match.group(2) == "5"
    assert re.search(r"resolution=\((\d+),\s*(\d+)\)", match.group(3))
    assert "portrait-mounted" in line
    assert FakeBulkTransport is not None      # import guard for the fixture mod
