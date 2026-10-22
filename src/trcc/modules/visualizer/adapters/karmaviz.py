"""KarmaViz: a MilkDrop-style audio visual, ported from Jason Swint's KarmaViz.

KarmaViz (https://github.com/karmatripping/KarmaViz, 67e8a15) is MIT, (c) 2025
Jason Swint.  Its shaders, palettes and presets are imported as data by
``dev/tools/import_karmaviz.py`` (licence and changes beside them, in
``assets/karmaviz/``); its drawing is ``_karmaviz_gl``.  This ports what its
Python does each frame -- ``modules/karmaviz.py`` (``draw_waveform``,
``render``) and ``modules/audio_handler.py``:

* **the ear**, per 1024-sample chunk as its audio thread runs: the waveform
  peak-normalised; the spectrum -- Hann window, ``|rfft|``, decibels over
  -60..0, 128 mel bands, normalised; its beat detector, adaptive threshold
  and all; the music's energy and warmth;
* **the motion**: time sped by the music, the beat's pulse, the bounce
  spring, the rotation modes, symmetry chosen at random where a preset says;
* **the colour**: a palette's colours cycled and blended, the palette chosen
  by the music's energy and warmth unless the preset fixes one;
* **"change every N beats"**: a new warp map and waveform, as KarmaViz's beat
  transitions.  Off by default, so a tile keeps the look it shows.

KarmaViz steps at about 60 frames a second and its feedback evolves per step,
so each panel frame runs :data:`STEPS` of its steps and every per-step
constant keeps KarmaViz's value.  Left out, for a panel: the mouse, clicks,
its logo and its spectrogram overlay.  One change: KarmaViz picks a palette's
mood category by its place in a list (assuming five per category); here each
palette's own energy and warmth say it, into the same formulas.
"""
from __future__ import annotations

import json
import logging
import math
import random
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from trcc.core.logs import per_frame
from trcc.core.models import SOUND_CHANNELS, SOUND_RATE, RawFrame
from trcc.core.ports import GlSurface

from ..core.models import VISUAL_FPS, VisualAdjustment, VisualInfo, VisualSpec
from ._base import GlVisual
from ._karmaviz_gl import (
    SPECTRUM_SAMPLES,
    WAVE_SAMPLES,
    FrameValues,
    KarmaVizPainter,
    warp_names,
    waveform_names,
)
from ._shaders import SHADERS

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

DATA = SHADERS.parent / "karmaviz"
#: KarmaViz's steps per panel frame: it runs at about 60 a second.
STEPS = max(1, round(60 / VISUAL_FPS))
_STEP_S = 1.0 / (VISUAL_FPS * STEPS)
_MELS = 128


# ── the preset ───────────────────────────────────────────────────────

Colour = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Palette:
    """One KarmaViz palette and the mood it suits."""
    name: str
    colours: tuple[Colour, ...]
    energy: str           # high / moderate / low
    warmth: str           # warm / cool / neutral

    @classmethod
    def all(cls) -> tuple[Palette, ...]:
        found = []
        for path in sorted((DATA / "palettes").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append(cls(data["name"], tuple(tuple(c) for c in data["colors"]),
                             data.get("energy_level", "moderate"),
                             data.get("warmth", "neutral")))
        log.info("Palette.all: %d", len(found))
        return tuple(found)


@dataclass(frozen=True, slots=True)
class Look:
    """What a KarmaViz preset sets."""
    waveform: str
    warp: str | None
    animation_speed: float
    audio_speed_boost: float
    glow: float
    trail: float
    smoke: float
    symmetry: int             # -1: chosen at random on each change
    kaleidoscope: int
    warp_intensity: float
    waveform_scale: float
    warp_first: bool
    rotation_mode: int        # 0 none, 1 clockwise, 2 anticlockwise, 3 beat
    pulse: bool
    bounce: bool
    palette: tuple[Colour, ...] | None   # None: chosen by the music's mood

    @classmethod
    def read(cls, path: Path, palettes: tuple[Palette, ...]) -> Look:
        data = json.loads(path.read_text(encoding="utf-8"))
        v, p = data["visualizer_settings"], data["palette_info"]
        fixed = next((x.colours for x in palettes
                      if p.get("palette_mode") == "Fixed"
                      and x.name == p.get("selected_palette_name")), None)
        look = cls(
            waveform=v["current_waveform_name"],
            warp=data["warp_map_info"].get("active_warp_map_name") or None,
            animation_speed=float(v["animation_speed"]),
            audio_speed_boost=float(v["audio_speed_boost"]),
            glow=float(v["glow_intensity"]), trail=float(v["trail_intensity"]),
            smoke=float(v["smoke_intensity"]), symmetry=int(v["symmetry_mode"]),
            kaleidoscope=int(v["kaleidoscope_sections"]),
            warp_intensity=float(v["warp_intensity"]),
            waveform_scale=float(v["waveform_scale"]), warp_first=bool(v["warp_first"]),
            rotation_mode=int(v["rotation_mode"]), pulse=bool(v["pulse_enabled"]),
            bounce=bool(v["bounce_enabled"]), palette=fixed)
        log.info("Look.read: %s -> %s + %s, palette %s", path.name, look.waveform,
                 look.warp, p.get("selected_palette_name"))
        return look


# ── the ear ──────────────────────────────────────────────────────────

def mel_filterbank(mels: int, bins: int) -> np.ndarray:
    """KarmaViz's triangular mel filters (``create_mel_filterbank_for_size``):
    *mels* x *bins*, from 0 Hz to the Nyquist frequency."""
    top = SOUND_RATE / 2.0
    top_mel = 2595.0 * math.log10(1.0 + top / 700.0)        # hz_to_mel
    points = 700.0 * (10.0 ** (np.linspace(0.0, top_mel, mels + 2) / 2595.0) - 1.0)
    edges = np.clip(np.floor((bins - 1) * points / top).astype(int), 0, bins - 1)
    bank = np.zeros((mels, bins))
    for i in range(mels):
        left, centre, right = edges[i], edges[i + 1], edges[i + 2]
        if centre != left:
            bank[i, left:centre] = (np.arange(left, centre) - left) / (centre - left)
        if right != centre:
            bank[i, centre:right] = (right - np.arange(centre, right)) / (right - centre)
    log.debug("mel_filterbank: %dx%d", mels, bins)
    return bank


class Ear:
    """KarmaViz's audio thread, chunk by chunk: what it hears, and its beats."""

    def __init__(self) -> None:
        log.debug("Ear.__init__")
        self._window = np.hanning(WAVE_SAMPLES)
        self._mel = mel_filterbank(_MELS, WAVE_SAMPLES // 2 + 1)
        self._pending = np.zeros(0, dtype=np.float32)
        self.chunk = np.zeros(WAVE_SAMPLES, dtype=np.float32)
        self.mel = np.zeros(_MELS)
        self.sensitivity = 1.5
        self.excess = 0.0
        self._beats = 0
        self._energy: float | None = None
        self._threshold: float | None = None
        self._since_beat = math.inf

    def hear(self, pcm: bytes) -> None:
        """Interleaved float32 sound, split into KarmaViz's chunks."""
        samples = np.frombuffer(pcm, dtype="<f4")
        samples = samples[: samples.size - samples.size % SOUND_CHANNELS]
        mono = samples.reshape(-1, SOUND_CHANNELS).mean(axis=1)
        frame_log.debug("Ear.hear: %d frame(s)", mono.size)
        self._pending = np.concatenate((self._pending, mono))
        while self._pending.size >= WAVE_SAMPLES:
            self._chunk(self._pending[:WAVE_SAMPLES])
            self._pending = self._pending[WAVE_SAMPLES:]

    def take_beats(self) -> int:
        """Beats heard since last asked."""
        beats, self._beats = self._beats, 0
        frame_log.debug("Ear.take_beats: %d", beats)
        return beats

    @property
    def wave(self) -> np.ndarray:
        """The chunk, peak-normalised -- the waveform texture."""
        peak = float(np.max(np.abs(self.chunk)))
        frame_log.debug("Ear.wave: peak %.3f", peak)
        return self.chunk / peak if peak > 0 else np.zeros_like(self.chunk)

    @property
    def spectrum(self) -> np.ndarray:
        """The mel bands spread over the spectrum texture, as KarmaViz
        resamples them (whole indices)."""
        frame_log.debug("Ear.spectrum")
        return self.mel[np.linspace(0, _MELS - 1, SPECTRUM_SAMPLES).astype(np.int32)]

    def mood(self) -> tuple[float, float]:
        """(energy, warmth), each 0..1 -- ``analyze_mood``."""
        energy = float(np.clip(np.sqrt(np.mean(np.square(self.chunk))) * 4.0, 0.0, 1.0))
        crossings = int(np.count_nonzero(np.diff(np.signbit(self.chunk))))
        warmth = 1.0 - float(np.clip(crossings / (WAVE_SAMPLES * 0.25), 0.0, 1.0))
        log.debug("Ear.mood: energy %.2f warmth %.2f", energy, warmth)
        return energy, warmth

    def _chunk(self, chunk: np.ndarray) -> None:
        frame_log.debug("Ear._chunk")
        self.chunk = chunk.astype(np.float32)
        magnitude = np.abs(np.fft.rfft(np.nan_to_num(chunk) * self._window))
        decibels = 20.0 * np.log10(np.maximum(magnitude, 1e-10))
        scaled = np.clip((np.maximum(decibels, -60.0) + 60.0) / 60.0, 0.0, 1.0)
        mel = np.maximum(self._mel @ scaled, 0.0)
        self.mel = mel / mel.max() if mel.max() > 0 else mel
        self._since_beat += WAVE_SAMPLES / SOUND_RATE
        if self._beat(self.mel):
            self._beats += 1
            self._since_beat = 0.0

    def _beat(self, bands: np.ndarray) -> bool:
        """``_detect_beat``, mel path: bass-weighted energy against an
        adaptive threshold, a spike over its smoothed level, 50 ms apart."""
        n = bands.size
        bass, sub, mid = max(3, min(20, int(n * 0.15))), max(2, min(10, int(n * 0.08))), \
            max(5, min(30, int(n * 0.2)))
        energy = (np.sum(np.sqrt(bands[:bass])) * 2.0 + np.sum(np.sqrt(bands[:sub])) * 1.5
                  + (np.sum(np.sqrt(bands[bass:bass + mid])) * 0.4 if bass + mid <= n else 0.0))
        energy = float(energy / max(bass + sub, 1))
        if self._energy is None or self._threshold is None:
            self._energy, self._threshold = energy, max(energy * 0.3, 0.01)
        rate = 0.4 if energy > self._energy else 0.1
        self._energy = (1 - rate) * self._energy + rate * energy
        follow = 0.05 if abs(self._threshold - self._energy * 0.5) > 0.02 else 0.001
        self._threshold = max((1 - follow) * self._threshold + follow * self._energy * 0.4,
                              0.01)
        threshold = max(self._threshold / float(np.clip(self.sensitivity, 0.25, 4.0)), 0.005)
        beat = (energy > threshold and self._since_beat > 0.05
                and energy / max(self._energy, 1e-4) > 1.05)
        if beat:
            self.excess = float(np.clip((energy - threshold) / max(threshold, 1e-4), 0.0, 5.0))
        frame_log.debug("Ear._beat: energy %.3f threshold %.3f -> %s", energy, threshold, beat)
        return beat


# ── the colour ───────────────────────────────────────────────────────

class Colours:
    """KarmaViz's waveform colour: a palette's colours in turn, blended; the
    palette fixed by the preset, or picked by the music's mood each second."""

    def __init__(self, palettes: tuple[Palette, ...], fixed: tuple[Colour, ...] | None) -> None:
        log.info("Colours: %s", "fixed" if fixed else f"by mood, {len(palettes)} palettes")
        self._palettes, self._fixed = palettes, fixed
        start = fixed or random.choice(palettes).colours
        self._now = np.array(start, dtype=np.float32)
        self._target = self._now.copy()
        self._blend = 1.0
        self._index = self._tick = self._steps = 0
        self._moods: deque[tuple[float, float]] = deque(maxlen=5)

    def step(self, ear: Ear, amplitude: float) -> tuple[float, float, float]:
        """One KarmaViz step's colour, 0..1 each."""
        self._steps += 1
        if self._fixed is None and self._steps % 60 == 0:
            self._moods.append(ear.mood())
            energy, warmth = np.mean(np.array(self._moods), axis=0)
            weights = [self._weight(p, float(energy), float(warmth)) for p in self._palettes]
            chosen = random.choices(self._palettes, weights=weights, k=1)[0].colours
            if len(chosen) != len(self._target) or not np.array_equal(chosen, self._target):
                self._target = np.array(chosen, dtype=np.float32)
                self._blend = 0.0
        if self._blend < 1.0:
            self._blend = min(1.0, self._blend + 0.02)
            size = max(len(self._now), len(self._target))
            now = np.vstack([self._now, np.repeat(self._now[-1:], size - len(self._now), 0)])
            to = np.vstack([self._target, np.repeat(self._target[-1:], size - len(self._target), 0)])
            self._now = np.floor(now * (1 - self._blend) + to * self._blend)
        cycle = max(3, int(5 * (1.0 - amplitude * 3.5)))
        self._tick += 1
        if self._tick >= cycle:
            self._tick, self._index = 0, (self._index + 1) % len(self._now)
        t = self._tick / cycle
        t = t * t * (3.0 - 2.0 * t)
        here = self._now[self._index % len(self._now)]
        nxt = self._now[(self._index + 1) % len(self._now)]
        colour = np.floor(here * (1 - t) + nxt * t) / 255.0
        brightness = 1.0 + amplitude * 0.8
        red, green, blue = (float(min(1.0, c * brightness)) for c in colour)
        frame_log.debug("Colours.step: %.2f %.2f %.2f", red, green, blue)
        return red, green, blue

    @staticmethod
    def _weight(palette: Palette, energy: float, warmth: float) -> float:
        """``_calculate_mood_weights``' formulas, on the palette's own mood."""
        if palette.warmth == "neutral":                     # the rainbow
            return 1.5 * (1.0 + energy) * (1.0 + abs(warmth - 0.5))
        weight = {"high": 1.0 + energy * 2.0, "low": 2.0 - energy}.get(
            palette.energy, 1.0 + (1.0 - abs(energy - 0.5) * 2.0))
        weight *= 1.0 + warmth if palette.warmth == "warm" else 2.0 - warmth
        frame_log.debug("Colours._weight: %s %.2f", palette.name, weight)
        return weight


# ── the visual ───────────────────────────────────────────────────────

class KarmaViz(GlVisual, key="karmaviz"):
    """KarmaViz's presets, one gallery tile each."""

    RANK = 20                       # after TRCC Pipes and Cavalier, before MilkDrop
    INFO = VisualInfo(
        kind="karmaviz", title="KarmaViz", category="KarmaViz", uses_presets=True,
        adjustments=(
            VisualAdjustment("speed", "Speed", 0.25, 3.0, 1.0),
            VisualAdjustment("warp", "Warp", 0.0, 2.0, 1.0),
            VisualAdjustment("beat_sensitivity", "Beat sensitivity", 0.25, 4.0, 1.5),
            VisualAdjustment("change_beats", "Change every N beats (0: never)", 0.0, 64.0, 0.0),
        ))

    @classmethod
    def presets(cls, folder: Path) -> tuple[str, ...]:
        """KarmaViz's own presets -- they ship with TRCC, so *folder* (the
        user's visuals folder) has none of them."""
        del folder
        found = tuple(sorted(p.name for p in (DATA / "presets").glob("*.json")))
        log.info("KarmaViz.presets: %d built in", len(found))
        return found

    @classmethod
    def preset(cls, folder: Path, name: str) -> Path | None:
        """*name* as one of its built-in presets -- a bare file name, never a
        path that could reach elsewhere."""
        del folder
        path = DATA / "presets" / name
        ok = bool(name) and Path(name).name == name and path.suffix == ".json" \
            and path.is_file()
        log.debug("KarmaViz.preset: %r -> %s", name, path if ok else None)
        return path if ok else None

    def __init__(self) -> None:
        log.debug("KarmaViz.__init__")
        self._surface: GlSurface | None = None
        self._painter: KarmaVizPainter | None = None
        self._palettes = Palette.all()
        self._ear = Ear()
        self._look: Look | None = None
        self._colours: Colours | None = None
        self._waves, self._warps = waveform_names(), warp_names()
        self._wave = ""
        self._warp: str | None = None
        self._w = self._h = 0
        self._speed = self._warp_scale = 1.0
        self._change_beats = 0
        self._time = 0.0
        self._clock = 0.0
        self._boost = 0.0
        self._boost2 = 0.0
        self._amplitude = self._smoothed = 0.0
        self._pulse_now = self._pulse_target = 0.0
        self._pulse_start = -math.inf
        self._last_beat = -math.inf
        self._beat_gaps: deque[float] = deque(maxlen=8)
        self._beat_count = 0
        #: Beats since the look last changed -- or since "change every N
        #: beats" was moved.  Counting from the start instead changed the
        #: look the moment a new N divided the running total.
        self._beats_since_change = 0
        self._turn = 1
        self._bounce_height = self._bounce_speed = 0.0
        self._last_bounce = 0.0
        self._symmetry = 0
        self._values: FrameValues | None = None

    # ── VisualEngine ─────────────────────────────────────────────────

    def open(self, spec: VisualSpec) -> None:
        preset = spec.preset or self.preset(Path(), self.presets(Path())[0])
        log.info("KarmaViz.open: %dx%d preset=%s settings=%s", spec.width, spec.height,
                 preset, dict(spec.settings))
        if preset is None:
            raise OSError("KarmaViz has no presets")
        self._w, self._h = spec.width, spec.height
        self._look = look = Look.read(preset, self._palettes)
        self._colours = Colours(self._palettes, look.palette)
        self._boost2 = look.audio_speed_boost
        self._symmetry = random.randint(0, 10) if look.symmetry == -1 else look.symmetry
        self.adjust(spec.settings)
        self._surface = surface = self._open_surface()
        try:
            self._painter = KarmaVizPainter(surface, self._w, self._h)
            self._use(look.waveform, look.warp)
        except OSError:
            self.close()
            raise

    def adjust(self, settings: Mapping[str, float]) -> None:
        """The sliders: each at *settings*' value, else its default."""
        values = {a.name: a.default for a in self.INFO.adjustments}
        values.update(settings)
        log.info("KarmaViz.adjust: %s", values)
        self._speed = float(values["speed"])
        self._warp_scale = float(values["warp"])
        self._ear.sensitivity = float(values["beat_sensitivity"])
        change_beats = round(values["change_beats"])
        if change_beats != self._change_beats:
            log.info("KarmaViz.adjust: change every %d beats, counting from now",
                     change_beats)
            self._beats_since_change = 0
        self._change_beats = change_beats

    def feed(self, pcm: bytes) -> None:
        frame_log.debug("KarmaViz.feed: %d byte(s)", len(pcm))
        self._ear.hear(pcm)

    def render(self) -> RawFrame:
        frame_log.debug("KarmaViz.render: t=%.2f", self._time)
        painter = self._painter
        if painter is None or self._look is None:
            raise OSError("KarmaViz is not open")
        beats = self._ear.take_beats()
        painter.sound(self._ear.wave, self._ear.spectrum)
        values = self._step(beats)
        painter.step(values)
        for _ in range(STEPS - 1):
            values = self._step(0)
            painter.step(values)
        return RawFrame(data=painter.picture(values), width=self._w, height=self._h)

    def close(self) -> None:
        log.info("KarmaViz.close: open=%s", self._surface is not None)
        if self._painter is not None:
            self._painter.close()
            self._painter = None
        if self._surface is not None:
            self._surface.close()
            self._surface = None

    # ── one KarmaViz step ────────────────────────────────────────────

    def _use(self, wave: str, warp: str | None) -> None:
        log.info("KarmaViz._use: %s + %s", wave, warp)
        assert self._painter is not None
        self._painter.use(wave, warp)
        self._wave, self._warp = wave, warp

    def _step(self, beats: int) -> FrameValues:
        """``draw_waveform`` and ``render``'s arithmetic, once."""
        look, colours = self._look, self._colours
        assert look is not None and colours is not None
        self._clock += _STEP_S
        self._amplitude = float(np.mean(np.abs(self._ear.chunk))) * 8.0
        self._smoothed = 0.1 * (self._smoothed + abs(self._amplitude * 0.2))
        for _ in range(beats):
            self._on_beat(look)
        if not beats:
            self._spring()
        target = abs(self._amplitude) * look.audio_speed_boost * 0.5
        self._boost = 0.75 * self._boost + 0.15 * target
        self._boost2 = 0.75 * self._boost2 + 0.15 * self._boost
        speed = abs(look.animation_speed * self._speed + self._boost2)
        self._time += speed * _STEP_S
        colour = colours.step(self._ear, self._smoothed)
        rotation = {1: 0.1, 2: -0.1, 3: 0.1 * self._turn}.get(look.rotation_mode, 0.0)
        frame_log.debug("KarmaViz._step: speed %.3f beats %d", speed, beats)
        return FrameValues(
            time=self._time, animation_speed=speed, rotation=rotation,
            pulse_scale=self._pulse(look), bounce=look.bounce,
            bounce_height=self._bounce_height, colour=colour, symmetry=self._symmetry,
            kaleidoscope_sections=look.kaleidoscope, glow=look.glow, trail=look.trail,
            smoke=look.smoke, warp_intensity=look.warp_intensity * self._warp_scale,
            waveform_scale=look.waveform_scale, warp_first=look.warp_first)

    def _on_beat(self, look: Look) -> None:
        """A beat: its interval, the pulse, the bounce, the turn -- and,
        every N beats, a new warp map and waveform."""
        gap = self._clock - self._last_beat
        if 0.2 <= gap <= 2.0:
            self._beat_gaps.append(gap)
        self._last_beat = self._clock
        self._beat_count += 1
        self._pulse_target = (0.1 + self._ear.excess + self._smoothed) * 0.5
        self._pulse_start = self._clock
        if look.bounce:
            self._bounce_speed = min(12.0, 1.0 + self._ear.excess * 4.0 + self._smoothed * 4.0)
            self._bounce_height = 0.0
            self._last_bounce = self._clock
        if look.rotation_mode == 3:
            self._turn = -self._turn
        self._beats_since_change += 1
        frame_log.debug("KarmaViz._on_beat: %d (%d since a change)",
                        self._beat_count, self._beats_since_change)
        if 0 < self._change_beats <= self._beats_since_change:
            self._beats_since_change = 0
            wave = random.choice([w for w in self._waves if w != self._wave] or [self._wave])
            warp = random.choice([w for w in self._warps if w != self._warp] or [self._warp])
            if look.symmetry == -1:
                self._symmetry = random.randint(0, 10)
            log.info("KarmaViz: beat %d -- change to %s + %s", self._beat_count, wave, warp)
            self._use(wave, warp)

    def _spring(self) -> None:
        """The bounce between beats: a damped spring, decaying."""
        since = self._clock - self._last_bounce
        dt = min(since, 1.0 / 60.0)
        self._bounce_speed = ((self._bounce_speed - 4.0 * self._bounce_height * dt)
                              * (1.0 - 0.3 * dt))
        self._bounce_height += self._bounce_speed * dt
        decay = math.exp(-1.2 * since)
        self._bounce_height *= decay
        self._bounce_speed *= decay
        frame_log.debug("KarmaViz._spring: %.3f", self._bounce_height)

    def _pulse(self, look: Look) -> float:
        """The zoom on the beat: attack fast, decay slow, a sine hump per
        beat interval, a slow breath under it."""
        if not look.pulse:
            return 1.0
        rate = 0.15 * 2.0 if self._pulse_target > self._pulse_now else 0.15 * 0.5
        self._pulse_now += (self._pulse_target - self._pulse_now) * rate
        since_pulse = self._clock - self._pulse_start
        if since_pulse > 0.1:
            self._pulse_target *= math.exp(-1.5 * since_pulse)
        breath = 0.02 * math.sin(self._clock * 2.0)
        if self._pulse_now <= 0.001:
            return 1.0 + breath * 0.5
        interval = sum(self._beat_gaps) / len(self._beat_gaps) if self._beat_gaps else 0.5
        progress = min(1.0, (self._clock - self._last_beat) / interval)
        attack = (1.0 - (1.0 - since_pulse / 0.08) ** 3.0) if since_pulse < 0.08 else 1.0
        swell = self._pulse_now * math.sin(progress * math.pi) * attack
        frame_log.debug("KarmaViz._pulse: %.3f", swell)
        return 1.0 + (swell + breath) * 0.5
