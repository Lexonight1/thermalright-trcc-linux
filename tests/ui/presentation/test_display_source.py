"""``describe_source`` -- the one phrase qtgui and the CLI print for the source."""
from __future__ import annotations

import pytest

from trcc.ui.presentation.display_source import describe_source


@pytest.mark.parametrize(("source", "mode", "uri", "expected"), [
    ("background", "theme", None, "the theme's background"),
    ("background", "color", None, "a solid colour background"),
    ("background", "transparent", None, "no background"),
    ("screencast", "theme", None, "a screen cast"),
    ("media", "theme", "/home/u/Videos/clip.mp4", "the media player: clip.mp4"),
    ("media", "theme", "https://x.test/live.m3u8",
     "the media player: https://x.test/live.m3u8"),
])
def test_each_source_reads_as_one_phrase(
    source: str, mode: str, uri: str | None, expected: str,
) -> None:
    assert describe_source(source, mode, uri) == expected
