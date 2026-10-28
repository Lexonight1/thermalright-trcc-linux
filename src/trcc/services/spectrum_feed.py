"""The bands behind a theme's spectrum elements.

Two sources, one answer -- the newest ``(heights, peaks)``, 0 to 1:

* **audio**: what the computer is playing (``App.sound_input``, the monitor
  the audio visuals hear), through the lively EQ analyzer;
* **simulated**: a procedural dance with the same motion (rise at once, fall
  steadily, hanging peak caps), alive only while a player is playing --
  for systems whose sound cannot be tapped (a DAC fed straight through ALSA).

``SpectrumDriver`` starts the feed while a theme draws a spectrum element
and closes it when none does, so a theme without one never opens the sound.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import numpy as np

from ..core.logs import per_frame
from ..core.models import SOUND_CHANNELS, SOUND_RATE
from ..core.ports import SoundInput
from ..core.spectrum import Spectrum
from .playback_watch import PlaybackWatch

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Heights a second a simulated band falls: gentler than the real analyzer's.
_FALL = 1.1

#: Sound asked for per read / pictures a second of the simulation.
_FPS = 30


class SimulatedSpectrum:
    """Bands that dance like music: slow waves per band, bass-heavy, with a
    kick every half second or so that lifts the low bands.  Motion is the
    real analyzer's own (``Spectrum``)."""

    def __init__(self, bands: int) -> None:
        log.debug("SimulatedSpectrum: %d band(s)", bands)
        rng = np.random.default_rng()
        self._rng = rng
        self._motion = Spectrum(bands, SOUND_RATE)
        self._speed = rng.uniform(0.25, 1.4, (bands, 3))
        self._phase = rng.uniform(0, 2 * np.pi, (bands, 3))
        # Music's energy falls with frequency: bass bands stronger.
        self._weight = np.linspace(1.0, 0.55, bands)
        self._kick = np.clip(1.0 - np.arange(bands) / (bands * 0.6), 0.0, 1.0)
        self._t = 0.0
        self._next_beat = 0.0
        self._beat = 0.0

    def step(self, dt: float, playing: bool) -> tuple[np.ndarray, np.ndarray]:
        frame_log.debug("SimulatedSpectrum.step: %.3f s playing=%s", dt, playing)
        self._t += dt
        if playing and self._t >= self._next_beat:
            self._beat = self._rng.uniform(0.6, 1.0)
            self._next_beat = self._t + self._rng.uniform(0.55, 0.9)
        self._beat *= np.exp(-dt * 4.5)
        waves = np.sin(self._speed * self._t * 2 * np.pi + self._phase).mean(axis=1)
        target = (0.42 + 0.38 * waves) * self._weight + self._beat * self._kick * 0.5
        target = np.clip(target, 0.0, 1.0) if playing else np.zeros_like(target)
        m = self._motion
        m.heights = np.maximum(target, m.heights - _FALL * dt)
        m._move_peaks(dt)
        return m.heights.copy(), m.peaks.copy()


class SpectrumFeed:
    """The newest ``(heights, peaks)`` of the chosen source."""

    def __init__(self, sound: Callable[[], SoundInput]) -> None:
        log.debug("SpectrumFeed.__init__")
        self._make_sound = sound
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._input: SoundInput | None = None
        self._stop_flag = threading.Event()
        self._watch: PlaybackWatch | None = None
        self._config: tuple | None = None
        self._latest: tuple[np.ndarray, np.ndarray] | None = None

    @property
    def running(self) -> bool:
        frame_log.debug("SpectrumFeed.running")
        return self._thread is not None and self._thread.is_alive()

    @property
    def busy(self) -> bool:
        """Whether the bars have anything to show: sound is playing, or they
        have not yet fallen back to rest.  The driver idles when not."""
        frame_log.debug("SpectrumFeed.busy")
        if self._watch is None:
            return True                      # live audio is always listening
        if self._watch.playing:
            return True
        latest = self.latest()
        return latest is not None and float(latest[1].max()) > 0.001

    def start(self, bands: int, mode: str = "audio", lms_url: str = "",
              lms_player: str = "", mpris: tuple[str, ...] = ()) -> None:
        """Run the source for *mode*; restarts only when the settings change."""
        config = (bands, mode, lms_url, lms_player, mpris)
        if self.running and config == self._config:
            return
        self.stop()
        log.info("SpectrumFeed.start: %s", config)
        self._config = config
        self._stop_flag.clear()
        if mode == "simulated":
            self._watch = PlaybackWatch(lms_url, lms_player, mpris)
            target, args = self._run_simulated, (bands, self._watch)
        else:
            try:
                sound = self._make_sound()
                sound.open()
            except OSError as e:
                log.warning("SpectrumFeed: cannot listen (%s)", e)
                return
            self._input = sound
            target, args = self._run_audio, (sound, bands)
        self._thread = threading.Thread(
            target=target, args=args, daemon=True, name="trcc-spectrum")
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        sound, self._input = self._input, None
        if sound is not None:
            log.info("SpectrumFeed.stop")
            sound.close()
        watch, self._watch = self._watch, None
        if watch is not None:
            watch.stop()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._config = None
        with self._lock:
            self._latest = None

    def latest(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Heights and peaks, 0 to 1, lowest frequency first -- or None."""
        frame_log.debug("SpectrumFeed.latest")
        with self._lock:
            return self._latest

    def _run_simulated(self, bands: int, watch: PlaybackWatch) -> None:
        log.info("SpectrumFeed: simulating %d band(s)", bands)
        dance = SimulatedSpectrum(bands)
        last = time.monotonic()
        while not self._stop_flag.wait(1.0 / _FPS):
            now = time.monotonic()
            result = dance.step(now - last, watch.playing)
            last = now
            with self._lock:
                self._latest = result

    def _run_audio(self, sound: SoundInput, bands: int) -> None:
        analyzer = Spectrum(bands, SOUND_RATE)
        frames = SOUND_RATE // _FPS
        while True:
            try:
                raw = sound.read(frames)
            except OSError as e:
                log.warning("SpectrumFeed: sound read failed (%s)", e)
                return
            if not raw:
                return
            stereo = np.frombuffer(raw, dtype=np.float32)
            mono = stereo.reshape(-1, SOUND_CHANNELS).mean(axis=1)
            analyzer.feed(mono.astype(np.float64))
            with self._lock:
                self._latest = (analyzer.heights.copy(), analyzer.peaks.copy())
            frame_log.debug("SpectrumFeed: %d sample(s) analysed", mono.size)
