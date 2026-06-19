"""WindowsPlatform.configure_dpi — DPI awareness restoration.

Exercises the Windows DPI hook from the Linux dev box by constructing
``WindowsPlatform`` (it builds cleanly off-Windows) and injecting a fake
``ctypes.windll`` so we can assert the exact Win32 call without a
Windows host.  Mirrors legacy ``windows_platform.py:285``.
"""
from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from trcc.adapters.system.windows import WindowsPlatform
from trcc.core.ports import Platform


def test_base_platform_configure_dpi_is_noop() -> None:
    """The ABC default does nothing — Linux/macOS/BSD inherit it."""
    assert Platform.configure_dpi(object()) is None  # type: ignore[arg-type]


def test_windows_configure_dpi_sets_per_monitor_awareness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SetProcessDpiAwareness(2) = PROCESS_PER_MONITOR_DPI_AWARE."""
    calls: list[int] = []
    fake_shcore = SimpleNamespace(
        SetProcessDpiAwareness=lambda level: calls.append(level)
    )
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(shcore=fake_shcore), raising=False
    )

    WindowsPlatform().configure_dpi()

    assert calls == [2]


def test_windows_configure_dpi_swallows_missing_shcore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older Windows / non-Windows build (no shcore) must not crash."""

    class _NoShcore:
        def __getattr__(self, name: str) -> object:
            raise AttributeError(name)

    monkeypatch.setattr(ctypes, "windll", _NoShcore(), raising=False)

    # Must return cleanly despite the AttributeError on .shcore.
    assert WindowsPlatform().configure_dpi() is None
