"""Audio capture and spectrum visualization for screencast overlay."""
from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# Number of frequency bands for the visualizer
NUM_BANDS = 16
# Smoothing factor (0 = no smoothing, 1 = frozen)
SMOOTHING = 0.6


class AudioCapture:
    """Captures microphone audio and computes spectrum bands.

    Uses sounddevice to stream mic input. FFT produces frequency-domain
    data, binned into NUM_BANDS bars for visualization.

    Thread-safe: the audio callback runs on a sounddevice thread,
    `get_spectrum()` is called from the GUI thread.
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

    def start(self, device: int | None = None, samplerate: int = 44100,
              blocksize: int = 1024) -> bool:
        """Start capturing from the default microphone.

        Returns True if started successfully, False if sounddevice
        is unavailable or no mic is found.
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
            log.info("Audio capture started (device=%s, rate=%d, block=%d)",
                     device, samplerate, blocksize)
            return True
        except Exception as e:
            log.warning("Failed to start audio capture: %s", e)
            self._stream = None
            return False

    def stop(self) -> None:
        """Stop capturing."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                # Best-effort stream cleanup — sounddevice may have already
                # closed the stream. Logged at debug for -vv visibility.
                log.debug(
                    "audio: stream stop/close failed during cleanup",
                    exc_info=True,
                )
            self._stream = None
        self._running = False
        with self._lock:
            self._spectrum[:] = 0

    def get_spectrum(self) -> np.ndarray:
        """Return current spectrum bands (0.0–1.0 each). Thread-safe."""
        with self._lock:
            return self._spectrum.copy()

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info: Any, status: Any) -> None:
        """Called by sounddevice on the audio thread."""
        if status:
            log.debug("Audio status: %s", status)

        # Mono signal → FFT
        signal = indata[:, 0]
        fft = np.abs(np.fft.rfft(signal))

        # Bin into bands (logarithmic spacing for musical frequencies)
        n = len(fft)
        bands = np.zeros(self._bands, dtype=np.float32)
        indices = np.logspace(0, np.log10(n), self._bands + 1, dtype=int)
        indices = np.clip(indices, 0, n - 1)
        for i in range(self._bands):
            lo, hi = indices[i], indices[i + 1]
            if hi <= lo:
                hi = lo + 1
            bands[i] = np.mean(fft[lo:hi])

        # Normalize to 0–1 range
        peak = bands.max()
        if peak > 0:
            bands /= peak

        # Smooth with previous frame
        with self._lock:
            self._spectrum = SMOOTHING * self._spectrum + (1 - SMOOTHING) * bands
