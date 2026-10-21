"""CAVA's audio engine: sound in, smooth bar heights out -- ported to numpy.

CAVA (Console-based Audio Visualizer for ALSA) is what the Linux visualizers
are built on; Cavalier runs it.  Its engine turns the last few thousand
samples into one height per band, 0 to 1, that rises with the music and
falls with gravity -- calm bars rather than a jittery FFT.  This is that
engine, line for line in what it computes, with numpy's FFT in place of
FFTW (both unnormalised, so the numbers agree).

**Credit.**  Ported from CAVA by Karl Stavestrand
(https://github.com/karlstav/cava, MIT), at commit
299219826379e137f0f4c48bfdc96019360964ec:

* ``cavacore.c`` ``cava_init`` -- the FFT sizes for the rate (and the double
  length for the bass), the Hann windows, the log-spaced cut-off frequencies
  with their "push the spectrum up" fix, the bass/mid split at 100 Hz, and
  the hard-coded EQ; ``cava_execute`` -- the newest-first input buffer, the
  band sums of FFT magnitudes, the automatic sensitivity, the gravity
  fall-off and the integral smoothing, scaled by the measured frame rate;
* ``cava.c`` ``monstercat_filter`` -- the "monstercat" smoothing between
  neighbouring bars.

Modified 2026-10-13 for TRCC: ported from C to Python/numpy (FFTW's real
transforms become ``numpy.fft.rfft``; only the linear scaling mode and the
non-"waves" monstercat are ported, the ones Cavalier uses).  Distributed as
part of TRCC under GPL-3.0-or-later, keeping the notices below.

    cavacore.c / cavacore.h:
    Copyright (c) 2022 Karl Stavestrand <karl@stavestrand.no>

    cava.c (the repository's LICENSE):
    Copyright (c) 2015 Karl Stavestrand <karl@stavestrand.no>

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the
    following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
    NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
    DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
    OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
    USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from trcc.core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: cavacore splits the bass from the rest here, giving it a longer FFT.
_BASS_CUT_OFF = 100


#: cava_init: the FFT is 512 samples, times this for a rate up to the bound.
_FFT_STEPS = ((8125, 1), (16250, 2), (32500, 4), (75000, 8), (150000, 16),
              (300000, 32))


def _fft_size(rate: int) -> int:
    """cava_init's FFT length for *rate*: 512, doubled per octave of rate."""
    size = 512 * next((factor for bound, factor in _FFT_STEPS if rate <= bound),
                      64)
    log.debug("_fft_size: %d Hz -> %d", rate, size)
    return size


class Cava:
    """One cava plan: ``execute`` new samples, get bar heights 0 to 1 --
    every left-channel bar, then every right one, low to high."""

    def __init__(self, bars: int, rate: int, channels: int = 2,
                 autosens: bool = True, noise_reduction: float = 0.77,
                 low_cut_off: int = 50, high_cut_off: int = 10000) -> None:
        log.info("Cava: %d bar(s) x %d channel(s) at %d Hz, %d-%d Hz, "
                 "noise reduction %.2f", bars, channels, rate, low_cut_off,
                 high_cut_off, noise_reduction)
        if channels not in (1, 2):
            raise ValueError(f"cava takes 1 or 2 channels, not {channels}")
        size = _fft_size(rate)
        if not 1 <= bars <= size // 2 + 1:
            raise ValueError(f"cava takes 1 to {size // 2 + 1} bars at "
                             f"{rate} Hz, not {bars}")
        if not 1 <= low_cut_off < high_cut_off <= rate // 2:
            raise ValueError("cava's cut-offs must rise, within half the rate")
        self.bars, self.channels, self.rate = bars, channels, rate
        self.autosens, self.noise_reduction = autosens, noise_reduction
        self.sens, self.sens_init = 1.0, True
        self.framerate, self.frame_skip = 75.0, 1
        self.fft_size, self.fft_bass_size = size, size * 2
        self.input = np.zeros(self.fft_bass_size * channels)
        self.bass_window = 0.5 * (1 - np.cos(
            2 * math.pi * np.arange(self.fft_bass_size) / (self.fft_bass_size - 1)))
        self.window = 0.5 * (1 - np.cos(
            2 * math.pi * np.arange(size) / (size - 1)))
        self._cut_offs(low_cut_off, high_cut_off)
        count = bars * channels
        self.fall, self.mem = np.zeros(count), np.zeros(count)
        self.peak, self.prev = np.zeros(count), np.zeros(count)

    def _cut_offs(self, low: int, high: int) -> None:
        """cava_init's band layout and EQ, with C's integer truncations."""
        bars, rate = self.bars, self.rate
        half_bass, half = self.fft_bass_size // 2, self.fft_size // 2
        frequency_constant = (math.log10(low / high)
                              / (1 / (bars + 1) - 1))
        lower = [0] * (bars + 1)
        upper = [0] * (bars + 1)
        cut_off = [0.0] * (bars + 1)
        relative = [0.0] * (bars + 1)
        self.bass_cut_off_bar = 0
        first_bar = True
        min_bandwidth = float(rate // self.fft_bass_size)
        for n in range(bars + 1):
            coefficient = (-frequency_constant
                           + (n + 1) / (bars + 1) * frequency_constant)
            cut_off[n] = high * 10 ** coefficient
            if n > 0 and cut_off[n - 1] >= cut_off[n]:
                cut_off[n] = cut_off[n - 1] + min_bandwidth
            relative[n] = cut_off[n] / (rate / 2)
            if cut_off[n] < _BASS_CUT_OFF:
                lower[n] = min(int(relative[n] * half_bass), half_bass)
                self.bass_cut_off_bar += 1
                if self.bass_cut_off_bar > 1:
                    first_bar = False
            else:
                lower[n] = min(math.ceil(relative[n] * half), half)
                if n == self.bass_cut_off_bar:
                    first_bar = True
                    if n > 0:
                        upper[n - 1] = int(relative[n] * half_bass) - 1
                else:
                    first_bar = False
            if n > 0:
                if not first_bar:
                    upper[n - 1] = lower[n] - 1
                    if lower[n] <= lower[n - 1]:
                        limit = half_bass if n < self.bass_cut_off_bar else half
                        if lower[n - 1] + 1 < limit + 1:
                            lower[n] = lower[n - 1] + 1
                            upper[n - 1] = lower[n] - 1
                elif upper[n - 1] < lower[n - 1]:
                    upper[n - 1] = lower[n - 1] + 1
            relative[n] = (lower[n] / half_bass if n < self.bass_cut_off_bar
                           else lower[n] / half)
            cut_off[n] = relative[n] * (rate / 2)
        self.lower, self.upper, self.cut_off = lower, upper, cut_off
        self.eq = np.array([
            1 / 2 ** 28 * cut_off[n + 1] ** 0.85
            / math.log2(self.fft_bass_size if n < self.bass_cut_off_bar
                        else self.fft_size)
            / (upper[n] - lower[n] + 1)
            for n in range(bars)])
        log.debug("Cava._cut_offs: bass bars %d, cut-offs %s",
                  self.bass_cut_off_bar, [round(c) for c in cut_off])

    def execute(self, samples: np.ndarray) -> np.ndarray:
        """New interleaved samples (16-bit range, as cava feeds them) in;
        the bars out -- smoothed, and 0 to 1 with autosens."""
        frame_log.debug("Cava.execute: %d sample(s)", samples.size)
        size = self.input.size
        new = min(samples.size, size)
        silence = True
        if new > 0:
            self.framerate -= self.framerate / 64.0
            self.framerate += (self.rate * self.frame_skip
                               / (new / self.channels) / 64.0)
            self.frame_skip = 1
            self.input[new:] = self.input[:size - new].copy()
            self.input[:new] = samples[:new][::-1]
            silence = not np.any(samples[:new])
        else:
            self.frame_skip += 1
        out = self._bands()
        if self.autosens:
            out *= self.sens
        overshoot = self._smooth(out)
        if self.autosens:
            if overshoot:
                self.sens *= 1 - 0.02 * (66 / self.framerate)
                self.sens_init = False
            elif not silence:
                self.sens *= 1 + 0.001 * (66 / self.framerate)
                if self.sens_init:
                    self.sens *= 1 + 0.1 * (66 / self.framerate)
        return out

    def _bands(self) -> np.ndarray:
        """The windowed FFTs of each channel, summed into the bars."""
        frame_log.debug("Cava._bands")
        channels = ([self.input[1::2], self.input[0::2]] if self.channels == 2
                    else [self.input])
        out = np.zeros(self.bars * self.channels)
        for c, raw in enumerate(channels):
            bass = np.abs(np.fft.rfft(raw[:self.fft_bass_size] * self.bass_window))
            rest = np.abs(np.fft.rfft(raw[:self.fft_size] * self.window))
            for n in range(self.bars):
                spectrum = bass if n < self.bass_cut_off_bar else rest
                out[c * self.bars + n] = (
                    spectrum[self.lower[n]:self.upper[n] + 1].sum() * self.eq[n])
        return out

    def _smooth(self, out: np.ndarray) -> bool:
        """Gravity fall-off, then the integral; True if a bar overshot 1."""
        framerate_mod = 66 / self.framerate
        gravity_mod = framerate_mod ** 2.5 * 2 / self.noise_reduction
        integral_mod = framerate_mod ** 0.1
        overshoot = False
        for n in range(out.size):
            if out[n] < self.prev[n] and self.noise_reduction > 0.1:
                out[n] = max(self.peak[n] * (1.0 - self.fall[n] * self.fall[n]
                                             * gravity_mod), 0.0)
                self.fall[n] += 0.028
            else:
                self.peak[n] = out[n]
                self.fall[n] = 0.0
            self.prev[n] = out[n]
            out[n] = self.mem[n] * self.noise_reduction / integral_mod + out[n]
            self.mem[n] = out[n]
            if self.autosens and out[n] > 1.0:
                overshoot = True
                out[n] = 1.0
        frame_log.debug("Cava._smooth: overshoot=%s", overshoot)
        return overshoot


def monstercat(bars: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """cava.c's monstercat smoothing: no bar sits lower than a neighbour
    divided by (1.5 x strength) per step away -- the soft hills between
    the peaks.  In place, in cava's order (each bar lifts those after it
    as they are reached)."""
    frame_log.debug("monstercat: %d bar(s)", bars.size)
    count = bars.size
    for z in range(count):
        for m in range(count):
            if m != z:
                bars[m] = max(bars[z] / (strength * 1.5) ** abs(z - m), bars[m])
    return bars
