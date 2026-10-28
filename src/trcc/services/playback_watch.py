"""Whether anything is playing: a Lyrion (LMS) player, or a desktop player.

A simulated spectrum moves only while music does.  Two things can say so:

* a desktop player over MPRIS (Strawberry and most others) -- its
  ``PlaybackStatus`` read with ``busctl``;
* a Lyrion Music Server player -- its ``mode`` from the server's JSON-RPC.

One thread asks both every couple of seconds; either one playing is "playing".
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import urllib.request

log = logging.getLogger(__name__)

#: Seconds between looks.
_POLL_S = 2.0


def mpris_playing(names: tuple[str, ...]) -> bool:
    """Whether an MPRIS player whose bus name contains one of *names* (any
    player when empty) reports ``Playing``."""
    log.debug("mpris_playing: %s", names or "any")
    try:
        listed = subprocess.run(
            ["busctl", "--user", "list", "--no-legend"], capture_output=True,
            text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    for line in listed.splitlines():
        bus = line.split(None, 1)[0] if line.strip() else ""
        if not bus.startswith("org.mpris.MediaPlayer2."):
            continue
        if names and not any(n in bus.lower() for n in names):
            continue
        try:
            out = subprocess.run(
                ["busctl", "--user", "get-property", bus,
                 "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player",
                 "PlaybackStatus"], capture_output=True, text=True,
                timeout=3, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if "Playing" in out:
            return True
    return False


def lms_playing(url: str, player: str) -> bool:
    """Whether LMS at *url* says player *player* is in ``play`` mode."""
    log.debug("lms_playing: %s %s", url, player)
    body = json.dumps({"id": 1, "method": "slim.request",
                       "params": [player, ["status", "-", 1]]}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/jsonrpc.js", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as reply:
            return json.load(reply).get("result", {}).get("mode") == "play"
    except (OSError, ValueError):
        return False


class PlaybackWatch:
    """Polls the sources on a thread; ``playing`` is the newest answer."""

    def __init__(self, lms_url: str = "", lms_player: str = "",
                 mpris: tuple[str, ...] = ()) -> None:
        self.key = (lms_url, lms_player, mpris)
        self._lms_url, self._lms_player, self._mpris = lms_url, lms_player, mpris
        self.playing = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="trcc-playback-watch")
        self._thread.start()
        log.info("PlaybackWatch: lms=%s/%s mpris=%s", lms_url, lms_player,
                 mpris or "any")

    def stop(self) -> None:
        log.info("PlaybackWatch.stop")
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            now = mpris_playing(self._mpris)
            if not now and self._lms_url and self._lms_player:
                now = lms_playing(self._lms_url, self._lms_player)
            if now != self.playing:
                log.info("PlaybackWatch: playing=%s", now)
            self.playing = now
            self._stop.wait(_POLL_S)
