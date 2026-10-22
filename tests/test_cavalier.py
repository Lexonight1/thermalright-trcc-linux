"""Cavalier's modes and CAVA's engine, ported -- measured against the real
CAVA, not against what the port thinks CAVA does."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage

from trcc.core.models import SOUND_RATE, RawFrame
from trcc.modules.visualizer.adapters._base import VISUALS, BaseVisual
from trcc.modules.visualizer.adapters._cava import Cava, monstercat
from trcc.modules.visualizer.adapters.cavalier import CavalierBars
from trcc.modules.visualizer.core.models import VISUAL_FPS, VisualSpec

_FRAME = 2756                    # samples a call, as the reference was run


def _reference_input() -> list[np.ndarray]:
    """The 24 calls fed to the real cavacore: two channels of tones in the
    16-bit range, a beat every 8 calls, silence at calls 14 and 15."""
    frames = []
    for i in range(24):
        t = (np.arange(_FRAME) + i * _FRAME) / 44100
        beat = 1.0 if i % 8 < 2 else 0.3
        left = sum((np.sin(2 * np.pi * f * t) * a
                    for f, a in ((60, beat), (440, .4), (3500, .25))),
                   np.zeros(_FRAME)) * 9000
        right = sum((np.sin(2 * np.pi * f * t) * a
                     for f, a in ((90, .6), (1200, .5), (7000, .2))),
                    np.zeros(_FRAME)) * 9000
        if i in (14, 15):
            left[:] = right[:] = 0
        frames.append(np.stack([left, right], 1).ravel())
    return frames


#: The real engine's answers: CAVA's own cavacore.c (commit 2992198)
#: compiled unmodified against a plain-DFT stand-in for FFTW's transform,
#: cava_init(6, 44100, 2, 1, 0.77, 50, 10000, CAVA_SCALING_LINEAR), fed
#: _reference_input() -- run 2026-10-13, printed to 9 decimals.
_CAVACORE_CUT_OFFS = (48.4497, 129.199, 301.465, 710.596, 1711.89, 4145.14,
                      10002.2)
_CAVACORE_BARS = {
    0: (0.028762885, 0.001942977, 0.024463892, 0.001265777, 0.014406918,
        0.001627280, 0.020581415, 0.003115283, 0.001123209, 0.030604788,
        0.001605607, 0.011344426),
    5: (0.229504619, 0.009490625, 0.085904036, 0.004351981, 0.049533720,
        0.005594898, 0.152945540, 0.010710933, 0.003861805, 0.105225072,
        0.005520380, 0.039004290),
    14: (0.543085427, 0.029349655, 0.194206430, 0.008940670, 0.101488398,
         0.010537678, 0.411867750, 0.036540249, 0.020840250, 0.229059578,
         0.029532260, 0.085787922),
    15: (0.555180126, 0.034659970, 0.203196257, 0.012489144, 0.113333915,
         0.014367896, 0.459967679, 0.056393902, 0.033862937, 0.249071298,
         0.047950197, 0.104100173),
    23: (1.000000000, 0.043974587, 0.420943588, 0.033647667, 0.216926524,
         0.036619075, 0.809057299, 0.099711974, 0.062639710, 0.484786181,
         0.088642349, 0.169207417),
}


def test_the_port_answers_as_the_real_cava_does() -> None:
    """Band layout, EQ, smoothing, silence and autosens -- every number, to
    the 9 decimals the reference was printed with."""
    cava = Cava(6, 44100, 2, noise_reduction=0.77, low_cut_off=50,
                high_cut_off=10000)
    assert cava.cut_off == pytest.approx(_CAVACORE_CUT_OFFS, rel=1e-5)   # %g
    bars = [cava.execute(frame) for frame in _reference_input()]
    for call, expected in _CAVACORE_BARS.items():
        assert bars[call] == pytest.approx(expected, abs=2e-9), call


def test_monstercat_lifts_the_neighbours_of_a_peak() -> None:
    """cava.c: none lower than the peak / 1.5 per step away."""
    assert monstercat(np.array([0.0, 1.0, 0.0, 0.0])).tolist() == pytest.approx(
        [1 / 1.5, 1.0, 1 / 1.5, 1 / 2.25])


# ── the modes ───────────────────────────────────────────────────────────

_MODES = sorted(k for k in VISUALS if k.startswith("cavalier-"))


def _music(visual: BaseVisual, frames: int = 30) -> None:
    n = SOUND_RATE // VISUAL_FPS
    for i in range(frames):
        t = (np.arange(n) + i * n) / SOUND_RATE
        sig = sum((np.sin(2 * np.pi * f * t) * a
                   for f, a in ((60, 1.0), (440, .5), (2500, .4), (7000, .2))),
                  np.zeros(n))
        visual.feed(np.repeat((0.3 * sig).astype("<f4"), 2).tobytes())


def _pixels(frame: RawFrame) -> np.ndarray:
    return np.frombuffer(frame.data, np.uint8).reshape(
        frame.height, frame.width, 3)


def test_cavaliers_eleven_modes_come_after_trccs_own() -> None:
    """The gallery's order is a rank, not the order modules loaded -- on
    the path the App lists from, the platform's catalog: TRCC Pipes, the
    Spike Circle, then Cavalier.  MUTATION CHECK: drop the sort in
    ranked_visuals and Cavalier's modes come first."""
    from trcc.adapters.system import host_platform_class
    from trcc.modules.visualizer.core.ports import ranked_visuals

    surface = host_platform_class().GL_SURFACE
    drawn = [cls.on_surface(surface) for cls in VISUALS.values()]
    kinds = [info.kind for info in ranked_visuals(drawn)]
    assert kinds[:2] == ["trcc-pipes", "trcc-spikes"]
    assert len(_MODES) == 11
    assert kinds[2:13] == [k for k in kinds if k.startswith("cavalier-")]


@pytest.mark.parametrize("kind", _MODES)
def test_each_mode_draws_the_music_in_cavaliers_blue(kind: str) -> None:
    visual = VISUALS[kind]()
    visual.open(VisualSpec(kind, 160, 160))
    _music(visual)
    pixels = _pixels(visual.render())
    blue = np.all(np.abs(pixels.astype(int) - (53, 132, 228)) < 30, axis=2)
    assert blue.sum() > 30, kind


def test_a_full_mirror_draws_the_same_bars_up_and_down() -> None:
    visual = VISUALS["cavalier-bars"]()
    visual.open(VisualSpec("cavalier-bars", 160, 160, settings={"mirror": 1}))
    _music(visual)
    pixels = _pixels(visual.render()).astype(int)
    top, bottom = pixels[:80], pixels[80:][::-1]
    assert np.mean(np.abs(top - bottom)) < 4


def test_a_circle_shows_the_picture_in_its_middle(tmp_path: Path) -> None:
    """TRCC's addition: the user's picture inside the circle."""
    picture = tmp_path / "red.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0xFF0000)
    image.save(str(picture))
    visual = VISUALS["cavalier-bars-circle"]()
    visual.open(VisualSpec("cavalier-bars-circle", 160, 160, center=picture))
    _music(visual)
    assert tuple(_pixels(visual.render())[80, 80]) == (255, 0, 0)


def test_a_slider_rebuilds_the_engine_only_for_a_new_bar_count() -> None:
    visual = VISUALS["cavalier-bars"]()
    assert isinstance(visual, CavalierBars)
    visual.open(VisualSpec("cavalier-bars", 64, 64))
    engine = visual._cava
    visual.adjust({"colour": 10.0})
    assert visual._cava is engine
    visual.adjust({"bars": 12.0})
    assert visual._cava is not engine and visual._sample.size == 24



def test_a_burning_picture_sends_flames_up_from_a_circle(tmp_path: Path) -> None:
    """The picture's fire reaches Cavalier's modes too: flames rise from
    the circle it sits in."""
    picture = tmp_path / "white.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0xFFFFFF)
    image.save(str(picture))

    def above(fire: float) -> np.ndarray:
        visual = VISUALS["cavalier-bars-circle"]()
        visual.open(VisualSpec("cavalier-bars-circle", 160, 160,
                               center=picture, center_fire=fire))
        _music(visual)
        frame = None
        for _ in range(12):
            frame = visual.render()
        assert frame is not None
        return _pixels(frame)[30:55, 70:90].astype(int)

    cold, hot = above(0.0), above(1.0)
    assert (hot[..., 0] - hot[..., 2]).mean() > (cold[..., 0] - cold[..., 2]).mean() + 20




@pytest.mark.parametrize("kind", ["cavalier-wave", "cavalier-bars-circle"])
def test_flames_are_fire_coloured(kind: str, tmp_path: Path) -> None:
    """Wherever the fire changed a pixel, red leads green -- fire never
    burns green or blue.  Fire.flames hands back finished bytes; scaling them
    again wrapped past 255 and painted the user's flames green and blue
    (2026-10-13, on his panel).  MUTATION CHECK: multiply the flames by 255
    in CavalierVisual.render and this fails."""
    picture = tmp_path / "white.png"
    image = QImage(8, 8, QImage.Format.Format_RGB888)
    image.fill(0xFFFFFF)
    image.save(str(picture))

    def frame(fire: float) -> np.ndarray:
        visual = VISUALS[kind]()
        visual.open(VisualSpec(kind, 160, 160, center=picture, center_fire=fire))
        _music(visual)
        drawn = None
        for _ in range(12):
            drawn = visual.render()
        assert drawn is not None
        return _pixels(drawn).astype(int)

    cold, hot = frame(0.0), frame(1.0)
    # Only bare background: a shape's anti-aliased edge over a flame is a
    # fair blend of the two, and may lean blue.
    bare = np.all(cold == 0x24, axis=2)                   # Cavalier's grey
    burnt = bare & np.any(hot != cold, axis=2)
    assert burnt.sum() > 100, kind
    assert (hot[burnt][:, 0] >= hot[burnt][:, 1]).all(), kind
