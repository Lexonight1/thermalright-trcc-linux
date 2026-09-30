"""``playback_clock`` — the C#'s ``LongToTimer`` position label, pure."""
from __future__ import annotations

import pytest

from trcc.ui.presentation.video_clock import playback_clock


@pytest.mark.parametrize(("cursor", "frame_count", "fps", "expected"), [
    (150, 300, 30, "00:00:05.000/00:00:10.000"),
    (0, 300, 30, "00:00:00.000/00:00:10.000"),
    (1, 300, 30, "00:00:00.033/00:00:10.000"),          # truncated, not rounded up
    (111_705, 111_706, 30, "01:02:03.500/01:02:03.533"),  # every field carries
    (0, 10_800_000, 30, "00:00:00.000/100:00:00.000"),   # hours do not wrap
])
def test_the_clock_reads_elapsed_over_total(
    cursor: int, frame_count: int, fps: int, expected: str,
) -> None:
    assert playback_clock(cursor, frame_count, fps) == expected


def test_a_video_with_no_rate_shows_zero_instead_of_dividing_by_it() -> None:
    assert playback_clock(5, 300, 0) == "00:00:00.000/00:00:00.000"
