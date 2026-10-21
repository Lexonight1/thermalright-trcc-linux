"""The lively EQ base: sound in, band heights that dance out.

Every EQ visual that wants bars which move with the music draws from this.
Each frame the newest sound is windowed and transformed, split into bands
spaced as the ear hears them (log), and each band's loudest bin is read in
decibels against the loudest the music has been lately -- so a quiet song
fills the panel as a loud one does.  Then the motion:

* **rise at once** -- a beat is drawn the frame it lands;
* **fall steadily** -- at ``FALL`` heights a second, never slower;
* **peak caps** -- each band's high mark hangs for ``HOLD`` seconds, then
  drops under gravity.

Every rate is per second, worked out from how much sound each frame
brought, so the motion is the same at any frame rate.

**Credit.**  The feel -- instant rise, steady fall, hanging and falling peak
caps -- is the one XMMS's spectrum analyzer made familiar to Linux desktops
(XMMS by Peter Alm, Mikael Alm, Olle Hallnäs, Thomas Nilsson and 4Front
Technologies, GPL-2.0-or-later).  No XMMS code is used: written for TRCC
2026-10-14, under GPL-3.0-or-later.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from trcc.core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Samples per transform: 93 ms at 44.1 kHz, bins 10.8 Hz apart -- fine
#: enough to tell a kick from a bass line.
_FFT = 4096
#: How many decibels of loudness the bars span, below the recent loudest.
RANGE_DB = 42.0
#: The quietest sound that lights a bar, dBFS of one band's bin (tilted):
#: silence and hiss stay dark however quiet the music has been.
_FLOOR_DB = -90.0
#: How fast the loudness reference forgets a loud passage, dB a second.
_FORGET_DB = 3.0
#: Music's energy falls with frequency; this lifts each octave above 1 kHz
#: (and lowers each below) so the highs move too.  dB per octave.
_TILT_DB = 4.5
#: Heights a second a band falls when the sound drops.
FALL = 2.2
#: Seconds a peak cap hangs before it falls, and its gravity (heights/s²).
HOLD, GRAVITY = 0.35, 5.0


class Spectrum:
    """One analyzer: ``feed`` sound, read ``heights`` and ``peaks``, 0 to 1,
    one per band, lowest frequency first."""

    def __init__(self, bands: int, rate: int, low_hz: float = 40.0,
                 high_hz: float = 16000.0) -> None:
        log.info("Spectrum: %d band(s) %.0f-%.0f Hz at %d Hz", bands, low_hz,
                 high_hz, rate)
        if bands < 1 or not 0 < low_hz < high_hz <= rate / 2:
            raise ValueError(f"a spectrum takes 1+ bands rising within half "
                             f"the rate, not {bands} at {low_hz}-{high_hz} Hz")
        self.rate = rate
        self._buffer = np.zeros(_FFT)
        self._window = np.hanning(_FFT)
        # A full-scale sine reads 0 dB: the Hann window halves the amplitude.
        self._scale = 2.0 / self._window.sum()
        self._bands(bands, low_hz, high_hz)
        self._loudest = _FLOOR_DB
        self.heights = np.zeros(bands)
        self.peaks = np.zeros(bands)
        self._peak_age = np.zeros(bands)
        self._peak_speed = np.zeros(bands)

    def _bands(self, count: int, low: float, high: float) -> None:
        """Each band's bins, and its tilt.  A band narrower than a bin (the
        deep bass) reads the spectrum at its centre, between bins."""
        bin_hz = self.rate / _FFT
        edges = low * (high / low) ** (np.arange(count + 1) / count)
        centres = np.sqrt(edges[:-1] * edges[1:])
        self._centres = centres / bin_hz
        self._first = np.ceil(edges[:-1] / bin_hz).astype(int)
        self._last = np.ceil(edges[1:] / bin_hz).astype(int)   # exclusive
        self._tilt = _TILT_DB * np.log2(centres / 1000.0)
        log.debug("Spectrum._bands: %d band(s), %d between bins", count,
                  int((self._last <= self._first).sum()))

    def feed(self, mono: np.ndarray) -> None:
        """The next sound, -1 to 1, as many samples as the frame brought."""
        new = min(mono.size, _FFT)
        frame_log.debug("Spectrum.feed: %d sample(s)", mono.size)
        if new == 0:
            return
        self._buffer = np.concatenate([self._buffer[new:], mono[-new:]])
        level = self._levels()
        seconds = mono.size / self.rate
        loudest = float(level.max())
        self._loudest = max(loudest, self._loudest - _FORGET_DB * seconds)
        bottom = max(self._loudest - RANGE_DB, _FLOOR_DB)
        target = np.clip((level - bottom) / RANGE_DB, 0.0, 1.0)
        self.heights = np.maximum(target, self.heights - FALL * seconds)
        self._move_peaks(seconds)

    def _levels(self) -> np.ndarray:
        """Each band's loudest bin now, in dB, tilted."""
        magnitude = np.abs(np.fft.rfft(self._buffer * self._window)) * self._scale
        between = np.interp(self._centres, np.arange(magnitude.size), magnitude)
        loudest = np.array([magnitude[a:b].max() if b > a else 0.0
                            for a, b in zip(self._first, self._last, strict=True)])
        level = 20 * np.log10(np.maximum(np.maximum(loudest, between), 1e-9))
        frame_log.debug("Spectrum._levels: loudest %.1f dB", float(level.max()))
        return level + self._tilt

    def _move_peaks(self, seconds: float) -> None:
        """A cap rides up with its band; left behind, it hangs, then falls."""
        frame_log.debug("Spectrum._move_peaks: %.3f s", seconds)
        pushed = self.heights >= self.peaks
        self.peaks = np.where(pushed, self.heights, self.peaks)
        self._peak_age = np.where(pushed, 0.0, self._peak_age + seconds)
        falling = self._peak_age > HOLD
        self._peak_speed = np.where(falling, self._peak_speed + GRAVITY * seconds,
                                    0.0)
        self.peaks = np.maximum(self.peaks - self._peak_speed * seconds,
                                self.heights)

    @property
    def bass(self) -> float:
        """How hard the low end hits now, 0 to 1: the lowest eighth."""
        low = self.heights[:max(1, math.ceil(self.heights.size / 8))]
        frame_log.debug("Spectrum.bass: %d band(s)", low.size)
        return float(low.max())
