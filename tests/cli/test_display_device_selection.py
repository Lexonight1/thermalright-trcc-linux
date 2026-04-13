"""Tests for CLI per-device selection helpers in trcc.cli._display.

These helpers must work with both real TrccApp and test doubles used by CLI tests.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


def _dev(*, idx: int, path: str, is_lcd: bool = True):
    info = SimpleNamespace(device_index=idx, path=path)
    return SimpleNamespace(
        device_info=info,
        device_path=path,
        is_lcd=is_lcd,
    )


class TestResolveDevice:
    def test_prefers_app_lcd_when_no_selector(self):
        from trcc.cli._display import _resolve_device

        a = _dev(idx=0, path="/dev/sg0")
        b = _dev(idx=1, path="/dev/sg1")
        app = SimpleNamespace(devices=[a, b], lcd=b)

        assert _resolve_device(app, None) is b

    def test_path_selector_matches_device_path(self):
        from trcc.cli._display import _resolve_device

        a = _dev(idx=0, path="/dev/sg0")
        b = _dev(idx=1, path="/dev/sg1")
        app = SimpleNamespace(devices=[a, b])

        assert _resolve_device(app, "/dev/sg1") is b

    def test_numeric_selector_uses_sorted_index(self):
        from trcc.cli._display import _resolve_device

        # Out-of-order list, but sorted by device_index then path
        a = _dev(idx=1, path="/dev/sg9")
        b = _dev(idx=0, path="/dev/sg0")
        app = SimpleNamespace(devices=[a, b])

        assert _resolve_device(app, "1") is b
        assert _resolve_device(app, "2") is a

    def test_saved_selection_used_when_no_app_lcd(self):
        from trcc.cli._display import _resolve_device

        a = _dev(idx=0, path="/dev/sg0")
        b = _dev(idx=1, path="/dev/sg1")
        app = SimpleNamespace(devices=[a, b], lcd=None)

        with patch("trcc.conf.Settings.get_selected_device", return_value="/dev/sg1"):
            assert _resolve_device(app, None) is b

