"""``AudioCapture.start`` never raises -- even when sounddevice cannot load.

Ubuntu 26.04's ``python3-sounddevice`` initialises PortAudio AT IMPORT and
raises ``PortAudioError`` when no sound server answers (measured in a clean
container, 2026-10-09).  ``start`` caught only ``ImportError``, so on a
headless box the audio visualizer's start crashed instead of switching off.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.services.audio import AudioCapture

#: The real method, captured before the per-test microphone guard replaces it.
#: The stand-in module below fails at import, so no stream can ever open.
_REAL_START = AudioCapture.start


def test_a_sounddevice_that_cannot_initialise_means_no_audio_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MUTATION CHECK: catch only ImportError again -> the OSError escapes."""
    (tmp_path / "sounddevice.py").write_text(
        "raise OSError('Error initializing PortAudio: no sound server')\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(AudioCapture, "start", _REAL_START)

    with caplog.at_level("WARNING"):
        assert AudioCapture().start() is False

    assert any("audio visualization disabled" in r.getMessage()
               for r in caplog.records if r.levelname == "WARNING")
