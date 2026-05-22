"""Tests for BasePanel ABC (PySide6 + __init_subclass__ enforcement)."""

import pytest
from PySide6.QtWidgets import QApplication

from trcc.legacy.ui.gui.base import BasePanel


@pytest.fixture(scope="module")
def qapp():
    """Ensure a QApplication exists for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# =========================================================================
# __init_subclass__ enforcement
# =========================================================================


class TestEnforcement:
    """BasePanel enforces _setup_ui() on subclasses."""

    def test_incomplete_subclass_rejected(self, qapp):
        with pytest.raises(TypeError, match="must implement _setup_ui"):
            class BadPanel(BasePanel):
                pass

    def test_concrete_subclass_works(self, qapp):
        class GoodPanel(BasePanel):
            def _setup_ui(self):
                pass

        panel = GoodPanel()
        assert isinstance(panel, BasePanel)

    def test_intermediate_class_can_override(self, qapp):
        """BaseThemeBrowser-like intermediate class works."""

        class IntermediatePanel(BasePanel):
            def _setup_ui(self):
                pass  # satisfies contract

        class ConcretePanel(IntermediatePanel):
            pass  # inherits _setup_ui from IntermediatePanel

        panel = ConcretePanel()
        assert isinstance(panel, BasePanel)


# =========================================================================
# Virtual hooks (default no-ops)
# =========================================================================


class TestVirtualHooks:
    """Virtual hooks have safe defaults."""

    def _make_panel(self):
        class MinimalPanel(BasePanel):
            def _setup_ui(self):
                pass

        return MinimalPanel()

    def test_get_state_default_empty(self, qapp):
        panel = self._make_panel()
        assert panel.get_state() == {}

    def test_set_state_default_noop(self, qapp):
        panel = self._make_panel()
        panel.set_state({"key": "val"})  # should not raise

    def test_apply_language_default_noop(self, qapp):
        panel = self._make_panel()
        panel.apply_language("en")  # should not raise

    def test_overridden_get_state(self, qapp):
        class StatefulPanel(BasePanel):
            def __init__(self):
                super().__init__()
                self._value = 42
                self._setup_ui()

            def _setup_ui(self):
                pass

            def get_state(self):
                return {"value": self._value}

            def set_state(self, state):
                self._value = state.get("value", 0)

        panel = StatefulPanel()
        assert panel.get_state() == {"value": 42}
        panel.set_state({"value": 99})
        assert panel.get_state() == {"value": 99}


# =========================================================================
# Timer helpers
# =========================================================================


class TestTimerHelpers:
    """Periodic update timer management."""

    def test_start_and_stop(self, qapp):
        class TimerPanel(BasePanel):
            def _setup_ui(self):
                pass

        panel = TimerPanel()
        calls = []
        panel.start_periodic_updates(100, lambda: calls.append(1))
        assert panel._update_timer is not None
        assert panel._update_timer.isActive()
        panel.stop_periodic_updates()
        assert not panel._update_timer.isActive()

    def test_stop_without_start(self, qapp):
        class TimerPanel(BasePanel):
            def _setup_ui(self):
                pass

        panel = TimerPanel()
        panel.stop_periodic_updates()  # should not raise

    def test_restart_replaces_callback(self, qapp):
        class TimerPanel(BasePanel):
            def _setup_ui(self):
                pass

        panel = TimerPanel()
        calls_a = []
        calls_b = []
        panel.start_periodic_updates(100, lambda: calls_a.append(1))
        panel.start_periodic_updates(200, lambda: calls_b.append(1))
        assert panel._update_timer.interval() == 200


# =========================================================================
# Delegate signal
# =========================================================================


class TestDelegate:
    """invoke_delegate emits the delegate signal."""

    def test_invoke_delegate(self, qapp):
        class DelegatePanel(BasePanel):
            def _setup_ui(self):
                pass

        panel = DelegatePanel()
        received = []
        panel.delegate.connect(lambda cmd, info, data: received.append((cmd, info, data)))
        panel.invoke_delegate(42, "info", "data")
        assert received == [(42, "info", "data")]


# =========================================================================
# Real subclass checks
# =========================================================================


class TestRealSubclasses:
    """Existing panels are BasePanel subclasses."""

    def test_uc_device_is_base_panel(self, qapp):
        from trcc.legacy.ui.gui.uc_device import UCDevice
        assert issubclass(UCDevice, BasePanel)

    def test_uc_about_is_base_panel(self, qapp):
        from trcc.legacy.ui.gui.uc_about import UCAbout
        assert issubclass(UCAbout, BasePanel)

    def test_uc_preview_is_base_panel(self, qapp):
        from trcc.legacy.ui.gui.uc_preview import UCPreview
        assert issubclass(UCPreview, BasePanel)

    def test_base_theme_browser_is_base_panel(self, qapp):
        from trcc.legacy.ui.gui.base import BaseThemeBrowser
        assert issubclass(BaseThemeBrowser, BasePanel)
