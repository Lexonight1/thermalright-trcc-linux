"""``ffmpeg`` / ``ffprobe`` as TRCC's ``VideoTool`` -- the one place they run.

Services ask the port; none starts a program itself.  Each call carries
``SUBPROCESS_NO_WINDOW`` so Windows never flashes a console -- two of the five
probes this replaces did not, and every video load flashed one there.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ...core import toolchain
from ...core.models import SUBPROCESS_NO_WINDOW, VideoProbe
from ...core.ports import VideoTool

log = logging.getLogger(__name__)

#: A probe reads a container's header; a file that takes longer is not one.
PROBE_TIMEOUT_S = 10


class FfmpegTool(VideoTool):
    """The ``ffmpeg`` and ``ffprobe`` programs, found by ``core.toolchain``."""

    def probe(self, path: Path) -> VideoProbe:
        """One ``ffprobe`` for everything a caller needs to know about *path*."""
        log.debug("FfmpegTool.probe: %s", path)
        if not toolchain.present("ffprobe"):
            log.info("FfmpegTool.probe: no ffprobe -- %s is unknown", path.name)
            return VideoProbe()
        cmd = [toolchain.executable("ffprobe"), "-v", "error",
               "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate,nb_frames"
               ":format=duration",
               "-of", "json", str(path)]
        try:
            ran = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT_S,
                                 check=False, creationflags=SUBPROCESS_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("FfmpegTool.probe: ffprobe failed on %s (%s)", path.name, e)
            return VideoProbe()
        if ran.returncode != 0:
            log.warning("FfmpegTool.probe: ffprobe returned %d for %s",
                        ran.returncode, path.name)
            return VideoProbe()
        try:
            found = _read_probe(json.loads(ran.stdout or b"{}"))
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("FfmpegTool.probe: unreadable ffprobe output for %s (%s)",
                        path.name, e)
            return VideoProbe()
        log.info("FfmpegTool.probe: %s -> %s", path.name, found)
        return found


def _read_probe(data: dict[str, Any]) -> VideoProbe:
    """ffprobe's JSON as a ``VideoProbe``.  A container that records no length
    has one from its frame count and rate, as the gui's trimmer worked it out."""
    stream = (data.get("streams") or [{}])[0]
    fps = _rate(stream.get("r_frame_rate"))
    frames = _number(stream.get("nb_frames"), int)
    duration = _number((data.get("format") or {}).get("duration"), float)
    if not duration and frames and fps:
        duration = frames / fps
    log.debug("_read_probe: %s", data)
    return VideoProbe(duration_s=duration or None,
                      width=_number(stream.get("width"), int),
                      height=_number(stream.get("height"), int),
                      fps=fps, frames=frames)


def _number(value: Any, kind: type[int] | type[float]) -> Any:
    """*value* as *kind*, 0 when absent or not a number ("N/A")."""
    try:
        found = kind(value) if value not in (None, "", "N/A") else kind(0)
    except (TypeError, ValueError):
        found = kind(0)
    log.debug("_number: %r -> %r", value, found)
    return found if found > 0 else kind(0)


def _rate(value: Any) -> float:
    """A rate like ``"30000/1001"`` as frames per second, 0 when unknown."""
    num, _, den = str(value or "").partition("/")
    found = _number(num, float) / (_number(den, float) or 1.0) if den else _number(num, float)
    log.debug("_rate: %r -> %.3f", value, found)
    return found
