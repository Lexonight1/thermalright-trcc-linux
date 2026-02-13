"""Backward-compat re-exports — moved to media_player.py."""

from trcc.media_player import (
    FFMPEG_AVAILABLE,
    AbstractMediaPlayer,
    GIFAnimator,
    GIFThemeLoader,
    ThemeZtPlayer,
    VideoPlayer,
)

__all__ = [
    "FFMPEG_AVAILABLE",
    "AbstractMediaPlayer",
    "GIFAnimator",
    "GIFThemeLoader",
    "ThemeZtPlayer",
    "VideoPlayer",
]
