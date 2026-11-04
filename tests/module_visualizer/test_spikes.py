"""The lively EQ base (``_spectrum``) and the Spike Circle drawn from it.

The base's promise is motion: a beat drawn the frame it lands, a steady
fall, caps that hang and drop -- the same at any frame rate, and a quiet
song as lively as a loud one.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage

from trcc.core.models import SOUND_RATE
from trcc.modules.visualizer.adapters._base import VISUALS, BaseVisual
from trcc.modules.visualizer.adapters._spectrum import FALL, HOLD, Spectrum
from trcc.modules.visualizer.adapters.spikes import SpikeCircle
from trcc.modules.visualizer.core.models import VISUAL_FPS, VisualSpec

_FRAME = SOUND_RATE // VISUAL_FPS


def _tone(hz: float, level: float = 0.5, samples: int = _FRAME,
          start: int = 0) -> np.ndarray:
    t = (np.arange(samples) + start) / SOUND_RATE
    return level * np.sin(2 * np.pi * hz * t)


def _silence(samples: int = _FRAME) -> np.ndarray:
    return np.zeros(samples)


def _band_of(spectrum: Spectrum, hz: float) -> int:
    edges = 40.0 * (16000.0 / 40.0) ** (np.arange(spectrum.heights.size + 1)
                                        / spectrum.heights.size)
    return int(np.searchsorted(edges, hz) - 1)


def test_a_tone_lights_its_own_band() -> None:
    spectrum = Spectrum(32, SOUND_RATE)
    for i in range(4):
        spectrum.feed(_tone(1000, start=i * _FRAME))
    assert int(np.argmax(spectrum.heights)) == _band_of(spectrum, 1000)


def test_a_beat_is_drawn_the_frame_it_lands() -> None:
    """Instant rise.  MUTATION CHECK: smooth the rise (heights += (target -
    heights) / 2) and the first loud frame stops short."""
    spectrum = Spectrum(32, SOUND_RATE)
    for _ in range(8):
        spectrum.feed(_silence())
    spectrum.feed(_tone(80, level=0.8, samples=4096))
    assert spectrum.heights[_band_of(spectrum, 80)] > 0.9


def test_the_bars_fall_steadily_not_at_once() -> None:
    """After the sound stops each band falls FALL heights a second."""
    spectrum = Spectrum(32, SOUND_RATE)
    for i in range(6):
        spectrum.feed(_tone(1000, start=i * _FRAME))
    band = _band_of(spectrum, 1000)
    before = spectrum.heights[band]
    spectrum.feed(_silence(8192))                    # the FFT window is quiet
    after = spectrum.heights[band]
    assert after == pytest.approx(before - FALL * 8192 / SOUND_RATE, abs=1e-9)


def test_a_cap_hangs_then_falls_and_never_below_its_bar() -> None:
    spectrum = Spectrum(16, SOUND_RATE)
    spectrum.feed(_tone(1000, level=0.8, samples=4096))
    band = _band_of(spectrum, 1000)
    top = spectrum.peaks[band]
    spectrum.feed(_silence(int(SOUND_RATE * HOLD * 0.8)))
    assert spectrum.peaks[band] == top               # hanging
    for _ in range(VISUAL_FPS):
        spectrum.feed(_silence())
        assert (spectrum.peaks >= spectrum.heights).all()
    assert spectrum.peaks[band] < top                # fallen


def test_silence_stays_dark() -> None:
    spectrum = Spectrum(32, SOUND_RATE)
    rng = np.random.default_rng(1)
    for _ in range(VISUAL_FPS * 2):
        spectrum.feed(rng.normal(0, 1e-5, _FRAME))   # hiss far below music
    assert spectrum.heights.max() == 0.0


def test_a_quiet_song_dances_as_a_loud_one() -> None:
    """The bars read against the loudest the music has been lately.
    MUTATION CHECK: pin ``_loudest`` to 0 dB and the quiet song stays low."""
    def settled(level: float) -> float:
        spectrum = Spectrum(32, SOUND_RATE)
        for i in range(VISUAL_FPS * 2):
            spectrum.feed(_tone(500, level=level, start=i * _FRAME))
        return float(spectrum.heights.max())

    assert settled(0.02) == pytest.approx(settled(0.8), abs=0.02)


def test_the_motion_is_the_same_at_any_frame_rate() -> None:
    """The same silence after a tone falls as far in 8 frames as in 32."""
    def fallen(frames: int) -> float:
        spectrum = Spectrum(16, SOUND_RATE)
        spectrum.feed(_tone(1000, level=0.8, samples=4096))
        spectrum.feed(_silence(4096))                # the window empties
        start = spectrum.heights.copy()
        for _ in range(frames):
            spectrum.feed(_silence(16384 // frames))
        return float((start - spectrum.heights).max())

    assert fallen(8) == pytest.approx(fallen(32), abs=1e-9)


# ── the Spike Circle ────────────────────────────────────────────────────

def _spikes(**settings: float) -> SpikeCircle:
    visual = VISUALS["trcc-spikes"]()
    assert isinstance(visual, SpikeCircle)
    visual.open(VisualSpec("trcc-spikes", 160, 160, settings=settings))
    return visual


def _feed(visual: BaseVisual, signal: np.ndarray) -> None:
    for chunk in np.array_split(signal, max(1, signal.size // _FRAME)):
        visual.feed(np.repeat(chunk.astype("<f4"), 2).tobytes())


def _pixels(visual: BaseVisual) -> np.ndarray:
    frame = visual.render()
    return np.frombuffer(frame.data, np.uint8).reshape(frame.height, frame.width, 3)


def _music(seconds: float = 1.5) -> np.ndarray:
    t = np.arange(int(SOUND_RATE * seconds)) / SOUND_RATE
    return 0.3 * np.sum([np.sin(2 * np.pi * f * t) * a
                         for f, a in ((60, 1.0), (440, .5), (2500, .4), (7000, .2))],
                        axis=0)


def _lit_radii(pixels: np.ndarray) -> np.ndarray:
    """How far from the middle each lit pixel is."""
    y, x = np.nonzero(pixels.max(axis=2) > 60)
    return np.hypot(x - 79.5, y - 79.5)


#: The circle's radius at rest: 0.6 of 0.97 of half of 160.
_CIRCLE = 80 * 0.97 * 0.6


def test_silence_is_a_plain_circle_and_music_spikes_its_edge() -> None:
    quiet, loud = _spikes(), _spikes()
    _feed(quiet, np.zeros(SOUND_RATE))
    _feed(loud, _music())
    still, spiked = _lit_radii(_pixels(quiet)), _lit_radii(_pixels(loud))
    assert (still.min() > _CIRCLE - 2, still.max() < _CIRCLE + 2) == (True, True)
    assert (spiked > _CIRCLE * 1.1).sum() > 50


def test_it_breathes_rather_than_jumps() -> None:
    """One loud frame after silence: the analyzer's bars are up at once,
    the circle's spikes only on their way.  MUTATION CHECK: draw
    ``self._spectrum.heights`` instead of the eased ``_shown``."""
    visual = _spikes()
    _feed(visual, np.zeros(SOUND_RATE))
    _feed(visual, _music(1 / VISUAL_FPS))
    assert visual._spectrum is not None
    jumped = visual._spectrum.heights
    shown = visual._shown
    assert (jumped.max() > 0.5, shown.max() < jumped.max() * 0.6) == (True, True)


def test_the_two_halves_are_a_mirror() -> None:
    """Bass at twelve o'clock, the treble at six, both sides the same."""
    visual = _spikes()
    _feed(visual, _music())
    pixels = _pixels(visual).astype(int)
    assert np.mean(np.abs(pixels[:, :80] - pixels[:, 80:][:, ::-1])) < 3


def test_the_ring_holds_the_picture(tmp_path: Path) -> None:
    picture = tmp_path / "red.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0xFF0000)
    image.save(str(picture))
    visual = VISUALS["trcc-spikes"]()
    visual.open(VisualSpec("trcc-spikes", 160, 160, center=picture))
    _feed(visual, _music())
    assert tuple(_pixels(visual)[80, 80]) == (255, 0, 0)


def test_a_slider_restarts_the_analyzer_only_for_a_new_spike_count() -> None:
    visual = _spikes()
    analyzer = visual._spectrum
    visual.adjust({"colour": 10.0})
    assert visual._spectrum is analyzer
    visual.adjust({"spikes": 40.0})
    assert visual._spectrum is not None
    assert visual._spectrum.heights.size == 20
