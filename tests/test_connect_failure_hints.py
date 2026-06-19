"""A device-connect failure carries the per-OS hint to BOTH channels.

The reason a connect failed (e.g. "run as administrator") is sourced from
the DI'd ``platform.check_permissions()`` and rides out on the sync
``ConnectResult`` AND the ``ErrorOccurred`` event — so every UI can show
*why* the panel stayed blank instead of failing silently.
"""
from __future__ import annotations

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.events import ErrorOccurred
from trcc.core.results import ConnectResult


def test_models_default_to_empty_hints() -> None:
    assert ErrorOccurred(message="x").hints == []
    assert ConnectResult(ok=False, key="k", message="m").hints == []


def test_connect_failure_carries_platform_hints(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions",
                        lambda: ["NEEDS-ELEVATION"])

    captured: list[ErrorOccurred] = []
    app.events.subscribe(ErrorOccurred, captured.append)

    # An unknown key → app.attach raises DeviceNotFoundError → the failure path.
    result = app.dispatch(ConnectDevice(key="dead:beef"))

    assert result.ok is False
    assert result.hints == ["NEEDS-ELEVATION"]          # sync channel
    assert captured and captured[0].hints == ["NEEDS-ELEVATION"]  # event channel


def test_connect_failure_hints_empty_when_platform_clean(
    fake_platform, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = App(fake_platform)
    monkeypatch.setattr(app.platform, "check_permissions", lambda: [])

    result = app.dispatch(ConnectDevice(key="dead:beef"))

    assert result.ok is False
    assert result.hints == []   # nothing to add → raw message stands
