#!/usr/bin/env python3
"""See every audio visual as the panel would: a contact sheet, plus numbers.

Each visual (and its first presets) is driven through the REAL engine, bound
to EGL as Linux binds it, with music -- a synthetic 120 BPM track by default,
or any audio file with ``--audio``.  For each it reports:

    lit     how much of the panel is not black (an empty visual reads ~0 %)
    motion  how much the picture changes frame to frame (a frozen one ~0)
    ms      wall time per frame on this machine

and saves one sheet with the last frame of each.  No device, no App.

    PYTHONPATH=src python3 dev/tools/visual_sheet.py sheet.png
    PYTHONPATH=src python3 dev/tools/visual_sheet.py sheet.png --audio song.mp3
    PYTHONPATH=src python3 dev/tools/visual_sheet.py sheet.png --only cavalier --size 480
"""
from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))


def _pink(n: int) -> np.ndarray:
    """Noise falling off as 1/f, as real music does -- white noise is far
    brighter at the top than any song, and would peg the high bands."""
    spectrum = np.fft.rfft(np.random.default_rng(1).normal(0, 1, n))
    spectrum /= np.sqrt(np.maximum(np.arange(spectrum.size), 1))
    pink = np.fft.irfft(spectrum, n)
    return pink / np.abs(pink).max()


def _saw(freq: np.ndarray | float, t: np.ndarray, harmonics: int = 12) -> np.ndarray:
    """A sawtooth from its harmonics: a lead with a real top end."""
    return sum(np.sin(2 * np.pi * k * freq * t) / k for k in range(1, harmonics + 1))


def synthetic(rate: int, seconds: float) -> np.ndarray:
    """120 BPM: a kick, a snare on two and four, a bass line, chords, a hat
    and a sawtooth lead stepping a scale -- stereo float32."""
    t = np.arange(int(rate * seconds)) / rate
    beat = np.exp(-((t * 2.0) % 1.0) * 8.0)
    snare = np.exp(-(((t * 2.0) + 1.0) % 2.0) * 14.0) * _pink(t.size)
    hat = np.exp(-((t * 8.0) % 1.0) * 30.0) * _pink(t.size)
    bass = np.sin(2 * np.pi * (55 + 55 * ((t // 2) % 2)) * t)
    chord = sum(np.sin(2 * np.pi * f * t) for f in (220, 277, 330))
    scale = np.array([440, 494, 554, 587, 659, 740, 831, 880])
    lead = _saw(scale[(t * 4).astype(int) % scale.size], t)
    mono = (0.7 * beat * np.sin(2 * np.pi * 50 * t) + 0.25 * bass + 0.08 * chord
            + 0.05 * hat + 0.15 * snare + 0.04 * lead)
    return np.repeat((mono / np.abs(mono).max()).astype("<f4"), 2)


def decoded(path: Path, rate: int, seconds: float, start: float) -> np.ndarray:
    """*seconds* of *path* from *start*, as interleaved stereo float32."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("--audio needs ffmpeg")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds), "-i", str(path),
         "-f", "f32le", "-ac", "2", "-ar", str(rate), "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<f4")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path, help="the contact sheet (.png)")
    ap.add_argument("--audio", type=Path, help="an audio file to play instead")
    ap.add_argument("--start", type=float, default=0.0,
                    help="where in --audio to start, seconds")
    ap.add_argument("--only", default="", help="visual kinds containing this")
    ap.add_argument("--presets", type=int, default=3,
                    help="presets per preset-driven visual (default 3)")
    ap.add_argument("--size", type=int, default=320, help="panel side, px")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--frames", type=int, default=1,
                    help="moments shown per visual, spread over the run "
                         "(default 1: the last frame)")
    args = ap.parse_args()

    from PySide6.QtGui import QGuiApplication, QImage, QPainter

    app = QGuiApplication(sys.argv[:1])
    from trcc.adapters.visual._gl import EglSurface
    from trcc.modules.visualizer.adapters._base import drawn_on
    from trcc.core.models import SOUND_RATE
    from trcc.modules.visualizer.core.models import VISUAL_FPS, VisualSpec

    engines = drawn_on(EglSurface)
    pcm = (decoded(args.audio, SOUND_RATE, args.seconds, args.start) if args.audio
           else synthetic(SOUND_RATE, args.seconds))
    step = SOUND_RATE // VISUAL_FPS * 2              # floats per frame, stereo
    frames = len(pcm) // step
    visuals_dir = Path.home() / ".trcc" / "data" / "visuals"
    jobs: list[tuple[str, str]] = []
    for kind, cls in sorted(engines.items()):
        if args.only and args.only not in kind:
            continue
        presets = cls.presets(visuals_dir)[:args.presets] or ("",)
        jobs += [(kind, p) for p in presets]

    size, shots = args.size, max(1, args.frames)
    cols = shots if shots > 1 else 4
    taken = {frames - 1 - (frames - 1) * k // shots for k in range(shots)}
    tiles = len(jobs) * shots
    sheet = QImage(cols * size, ((tiles + cols - 1) // cols) * (size + 22),
                   QImage.Format.Format_RGB888)
    sheet.fill(0x202020)
    paint = QPainter(sheet)
    paint.setPen(0xFFFFFF)
    print(f"{len(jobs)} visual(s), {frames} frames of "
          f"{'%s' % args.audio.name if args.audio else 'synthetic 120 BPM'}")
    print(f"{'visual':44} {'lit':>6} {'motion':>7} {'ms':>7}")
    for n, (kind, preset) in enumerate(jobs):
        cls = engines[kind]
        random.seed(3)
        engine = cls()
        path = cls.preset(visuals_dir, preset) if preset else None
        try:
            engine.open(VisualSpec(kind, size, size, preset=path))
        except Exception as e:                       # one broken visual, not all
            print(f"{kind + ' ' + preset:44} FAILED to open: {e}")
            continue
        last = None
        motion = []
        snapshots = []
        start = time.perf_counter()
        try:
            for i in range(frames):
                engine.feed(pcm[i * step:(i + 1) * step].tobytes())
                frame = np.frombuffer(bytes(engine.render().data), np.uint8)
                if last is not None:
                    motion.append(float(np.abs(frame.astype(int) - last).mean()))
                last = frame.astype(int)
                if i in taken:
                    snapshots.append((i, frame.reshape(size, size, 3)))
        finally:
            engine.close()
        ms = (time.perf_counter() - start) / frames * 1000
        pixels = last.reshape(size, size, 3) if last is not None else None
        lit = float((pixels.max(axis=2) > 24).mean() * 100) if pixels is not None else 0.0
        label = f"{kind} {Path(preset).stem}".strip()
        print(f"{label[:44]:44} {lit:5.1f}% {np.mean(motion) if motion else 0:7.2f} {ms:7.2f}")
        for k, (i, shot) in enumerate(snapshots):
            img = QImage(shot.tobytes(), size, size, size * 3,
                         QImage.Format.Format_RGB888).copy()
            tile = n * shots + k
            x, y = (tile % cols) * size, (tile // cols) * (size + 22)
            paint.drawImage(x, y, img)
            caption = f"{label} @{i / VISUAL_FPS:.1f}s" if shots > 1 else label
            paint.drawText(x + 4, y + size + 15, caption[:40])
    paint.end()
    sheet.save(str(args.out))
    print(f"sheet: {args.out}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
