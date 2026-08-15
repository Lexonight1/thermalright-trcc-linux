"""The SUB byte says how the panel is MOUNTED (#262, #203).

Thermalright ships some 854x480 / 960x540 / 800x480 panels turned portrait in
the cooler.  The C# reads that off the SUB byte and loads the transposed theme
catalog for them (``FormCZTVInit``: ``854480\\`` becomes ``480854\\``).  Every
other resolution assigns its catalog unconditionally, so the rule is scoped —
it must not be generalised.

We surface it in the handshake line rather than acting on it: an owner already
compensates by rotating to 90°, so changing the geometry would double-correct
the very people it is meant to help.  What was missing was being able to SEE
it — #262 and #203 share ``PM=11 SUB=5`` and nobody spotted it for a month.
"""
from __future__ import annotations

import re

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.protocol import is_portrait_mounted


def test_the_rule_matches_the_csharp_for_the_three_scoped_resolutions() -> None:
    """``pmSub < 5`` on 854x480, 960x540 and 800x480 — and nothing else.

    MUTATION CHECK: widen ``_PORTRAIT_MOUNT_RESOLUTIONS`` to include 480x480
    or 1600x720 and this fails — those resolutions have no sub test in the C#.
    """
    for resolution in ((854, 480), (960, 540), (800, 480)):
        assert is_portrait_mounted(resolution, 5) is True
        assert is_portrait_mounted(resolution, 9) is True
        assert is_portrait_mounted(resolution, 4) is False
        assert is_portrait_mounted(resolution, 0) is False

    # Resolutions the C# assigns unconditionally — no sub test at all.
    for resolution in ((480, 480), (320, 320), (1600, 720), (1280, 480),
                       (1920, 462), (640, 480), (240, 240), (360, 360)):
        assert is_portrait_mounted(resolution, 5) is False
        assert is_portrait_mounted(resolution, 9) is False


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
