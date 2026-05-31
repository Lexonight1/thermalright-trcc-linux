"""AudioCapture — microphone capture + spectrum bands for screencast overlay.

Ports the legacy ``services.audio.AudioCapture`` into next/.  Same shape:

* ``start(device=None, samplerate=44100, blocksize=1024) -> bool``
* ``stop()``
* ``running: bool``
* ``get_spectrum() -> np.ndarray`` — current ``NUM_BANDS`` floats in [0, 1]

Optional dependency: ``sounddevice``.  When absent, ``start()`` returns
False without raising, and the screencast pipeline silently disables
the spectrum visualizer.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

NUM_BANDS = 16        # bars in the spectrum visualizer
SMOOTHING = 0.6       # exponential smoothing (0 = no smoothing, 1 = frozen)


class AudioCapture:
    """Captures microphone input and computes per-band FFT magnitudes.

    Thread-safe: the audio callback runs on a sounddevice thread; the
    GUI calls ``get_spectrum()`` from the Qt main thread.  Both reads
    and writes hold ``self._lock`` so the visualizer never sees a torn
    update.
    """

    def __init__(self, bands: int = NUM_BANDS) -> None:
        self._bands = bands
        self._spectrum = np.zeros(bands, dtype=np.float32)
        self._lock = threading.Lock()
        self._stream: Any = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(
        self,
        device: int | None = None,
        samplerate: int = 44100,
        blocksize: int = 1024,
    ) -> bool:
        """Start capturing from *device* (None = OS default mic).

        Returns True on success, False when ``sounddevice`` is absent
        or the OS denies access to a microphone.  Never raises — the
        GUI checks the bool and adjusts visualization accordingly.
        """
        if self._running:
            return True
        try:
            import sounddevice as sd  # pyright: ignore[reportMissingImports]
        except ImportError:
            log.warning("sounddevice not installed — audio visualization disabled")
            return False
        try:
            self._stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=samplerate,
                blocksize=blocksize,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._running = True
            log.info(
                "AudioCapture started (device=%s, rate=%d, block=%d)",
                device, samplerate, blocksize,
            )
            return True
        except Exception as e:
            log.warning("AudioCapture.start() failed: %s", e)
            self._stream = None
            return False

    def stop(self) -> None:
        """Stop the stream and reset the spectrum to zeros."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.debug(
                    "AudioCapture: stream stop/close failed during cleanup",
                    exc_info=True,
                )
            self._stream = None
        self._running = False
        with self._lock:
            self._spectrum[:] = 0

    def get_spectrum(self) -> np.ndarray:
        """Return a copy of the current band magnitudes (0.0 – 1.0)."""
        log.debug("get_spectrum: bands=%d", self._bands)
        with self._lock:
            return self._spectrum.copy()

    # ── Internal — runs on sounddevice's audio thread ────────────────

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        del frames, time_info  # only ``indata`` + ``status`` used
        if status:
            log.debug("AudioCapture: stream status: %s", status)

        signal = indata[:, 0]
        fft = np.abs(np.fft.rfft(signal))

        n = len(fft)
        bands = np.zeros(self._bands, dtype=np.float32)
        # Logarithmic frequency spacing — perceptually closer to musical
        # bands than linear binning.
        indices = np.logspace(0, np.log10(n), self._bands + 1, dtype=int)
        indices = np.clip(indices, 0, n - 1)
        for i in range(self._bands):
            lo, hi = indices[i], indices[i + 1]
            if hi <= lo:
                hi = lo + 1
            bands[i] = np.mean(fft[lo:hi])

        peak = bands.max()
        if peak > 0:
            bands /= peak

        with self._lock:
            self._spectrum = SMOOTHING * self._spectrum + (1 - SMOOTHING) * bands
