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
