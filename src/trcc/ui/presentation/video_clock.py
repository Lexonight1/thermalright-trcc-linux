"""A video's playback position as a clock — toolkit-free, shared by both skins.

The C# player shows ``elapsed/total`` as ``HH:MM:SS.mmm``
(``UCBoFangQiKongZhi.LongToTimer``).  Both Qt skins showed frame counts
instead; they now format the one ``(cursor, frame_count, fps)`` triple that
``VideoAdvanced`` and ``VideoStatusResult`` both carry, here, once.
"""
from __future__ import annotations

from ...core.logs import per_frame

frame_log = per_frame(__name__)


def _clock(ms: int) -> str:
    """``HH:MM:SS.mmm`` — hours are not wrapped, as ``LongToTimer`` does not."""
    frame_log.debug("_clock: ms=%d", ms)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{ms:03}"


def playback_clock(cursor: int, frame_count: int, fps: int) -> str:
    """``elapsed/total`` for frame *cursor* of *frame_count* at *fps*.

    ``fps <= 0`` (a decoder that reported no rate) shows zero rather than
    dividing by it.
    """
    frame_log.debug("playback_clock: cursor=%d frame_count=%d fps=%d",
                    cursor, frame_count, fps)
    if fps <= 0:
        return f"{_clock(0)}/{_clock(0)}"
    return f"{_clock(cursor * 1000 // fps)}/{_clock(frame_count * 1000 // fps)}"
