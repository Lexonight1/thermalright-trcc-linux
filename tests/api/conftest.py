"""API layer test fixtures — real Trcc via _boot.trcc(MockPlatform).

Per ``feedback_tests_emulate_app.md``: tests use the same DI flow as
production. No MagicMock(spec=Trcc) — real ``Trcc`` built against
``MockPlatform``, real ``ControllerBuilder``, real ``QtRenderer``.

Each fixture yields ``(trcc, lcd_or_led_device)`` so test bodies can
assert against actual device state after the API endpoint runs.

Fixtures:
  lcd_only_app   — LCD connected, LED absent
  no_device_app  — empty MockPlatform, no devices found
"""
from __future__ import annotations

import pytest


@pytest.fixture
def lcd_only_app(tmp_path, monkeypatch):
    """Real Trcc with one connected LCD via the production DI flow.

    DataManager.ensure_all is replaced with a spy so the test never
    triggers a real network download. Spy calls are exposed as
    ``trcc._ensure_all_calls`` for assertion.
    """
    from mock_platform import MockPlatform

    from trcc import _boot
    from trcc.legacy.adapters.infra.data_repository import DataManager
    from trcc.legacy.adapters.render.qt import QtRenderer
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.trcc import Trcc

    ensure_all_calls: list = []
    monkeypatch.setattr(
        DataManager, "ensure_all",
        classmethod(lambda cls, w, h, progress_fn=None:
                    ensure_all_calls.append((w, h))),
    )

    spec = [{"type": "lcd", "vid": "0402", "pid": "3922",
             "resolution": "320x320", "pm": 100}]
    root = tmp_path / '.trcc'
    root.mkdir(exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    platform = MockPlatform(spec, root=root)
    init_settings(platform)

    _boot._cached = None
    trcc = Trcc(platform, renderer=QtRenderer())
    trcc.discover(ensure_data=False)
    trcc._ensure_all_calls = ensure_all_calls
    _boot._cached = trcc
    yield trcc, trcc.lcd_device
    _boot._cached = None


@pytest.fixture
def no_device_app(tmp_path, monkeypatch):
    """Real Trcc with no devices — empty MockPlatform.

    DataManager.ensure_all is a no-op so any code path that triggers
    data extraction stays offline.
    """
    from mock_platform import MockPlatform

    from trcc import _boot
    from trcc.legacy.adapters.infra.data_repository import DataManager
    from trcc.legacy.adapters.render.qt import QtRenderer
    from trcc.legacy.conf import init_settings
    from trcc.legacy.core.trcc import Trcc

    monkeypatch.setattr(DataManager, "ensure_all",
                        classmethod(lambda cls, *a, **kw: None))

    root = tmp_path / '.trcc'
    root.mkdir(exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    platform = MockPlatform([], root=root)
    init_settings(platform)

    _boot._cached = None
    trcc = Trcc(platform, renderer=QtRenderer())
    trcc.discover(ensure_data=False)
    _boot._cached = trcc
    yield trcc, None
    _boot._cached = None
