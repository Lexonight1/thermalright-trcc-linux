"""NVML init classification + state reporting.

These cover the error-classification and warn-once behaviour without a real
NVIDIA driver: a fake exception carrying NVML's integer ``.value`` codes is
enough, and the module's ``getattr(pynvml, ...)`` constant lookups fall back
to the real codes (9 / 18) when pynvml is absent.
"""
from __future__ import annotations

import logging

import pytest

from trcc.adapters.sensors import nvml


class _FakeNvmlError(Exception):
    """Stand-in for ``pynvml.NVMLError`` — carries the integer error code."""

    def __init__(self, value: int) -> None:
        self.value = value
        super().__init__(f"nvml error {value}")


def test_driver_not_loaded_is_transient() -> None:
    # NVML_ERROR_DRIVER_NOT_LOADED == 9 — the GPU may autostart; retry quietly.
    assert nvml._is_transient_nvml_error(_FakeNvmlError(9)) is True


def test_version_mismatch_is_not_transient() -> None:
    # NVML_ERROR_LIB_RM_VERSION_MISMATCH == 18 — won't fix itself on retry.
    assert nvml._is_transient_nvml_error(_FakeNvmlError(18)) is False


def test_unknown_error_is_not_transient() -> None:
    assert nvml._is_transient_nvml_error(_FakeNvmlError(999)) is False


def test_fix_hint_for_version_mismatch_is_the_reload_command() -> None:
    hint = nvml._nvml_fix_hint(_FakeNvmlError(18))
    assert nvml.NVML_RELOAD_HINT in hint
    assert "modprobe" in hint


def test_fix_hint_for_other_error_is_generic_driver_advice() -> None:
    hint = nvml._nvml_fix_hint(_FakeNvmlError(999))
    assert "driver" in hint.lower()
    assert "modprobe" not in hint


def test_nvml_init_state_returns_three_tuple() -> None:
    reader_available, initialized, error = nvml.nvml_init_state()
    assert isinstance(reader_available, bool)
    assert isinstance(initialized, bool)
    assert error is None or isinstance(error, str)


@pytest.fixture
def _fake_pynvml(monkeypatch: pytest.MonkeyPatch):
    """Inject a controllable fake pynvml and reset the module init state."""
    monkeypatch.setattr(nvml, "_AVAILABLE", True)
    monkeypatch.setattr(nvml, "_initialized", False)
    monkeypatch.setattr(nvml, "_init_error", None)
    monkeypatch.setattr(nvml, "_warned_init_failure", False)
    yield monkeypatch


def test_non_transient_init_failure_warns_exactly_once(
    _fake_pynvml: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """A version mismatch logs WARNING once, not on every retry."""

    class _Pynvml:
        NVML_ERROR_DRIVER_NOT_LOADED = 9
        NVML_ERROR_LIB_RM_VERSION_MISMATCH = 18

        def nvmlInit(self) -> None:
            raise _FakeNvmlError(18)

    _fake_pynvml.setattr(nvml, "pynvml", _Pynvml())

    with caplog.at_level(logging.WARNING, logger="trcc.adapters.sensors.nvml"):
        assert nvml._ensure_init() is False
        assert nvml._ensure_init() is False  # retry — must not re-warn

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "NVML init failed" in warnings[0].message
    assert nvml.NVML_RELOAD_HINT in warnings[0].message


def test_transient_init_failure_stays_at_debug(
    _fake_pynvml: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """DRIVER_NOT_LOADED never reaches WARNING — it's the normal autostart case."""

    class _Pynvml:
        NVML_ERROR_DRIVER_NOT_LOADED = 9
        NVML_ERROR_LIB_RM_VERSION_MISMATCH = 18

        def nvmlInit(self) -> None:
            raise _FakeNvmlError(9)

    _fake_pynvml.setattr(nvml, "pynvml", _Pynvml())

    with caplog.at_level(logging.DEBUG, logger="trcc.adapters.sensors.nvml"):
        assert nvml._ensure_init() is False

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_unavailable_pynvml_warns_exactly_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """pynvml not importable (the #161 case) logs WARNING once, not per retry."""
    monkeypatch.setattr(nvml, "_AVAILABLE", False)
    monkeypatch.setattr(nvml, "pynvml", None)
    monkeypatch.setattr(nvml, "_initialized", False)
    monkeypatch.setattr(nvml, "_warned_unavailable", False)
    monkeypatch.setattr(nvml, "_import_error", "No module named 'pynvml'")

    with caplog.at_level(logging.WARNING, logger="trcc.adapters.sensors.nvml"):
        assert nvml._ensure_init() is False
        assert nvml._ensure_init() is False  # retry — must not re-warn

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not importable" in warnings[0].message
    assert "No module named 'pynvml'" in warnings[0].message
    assert "nvidia-ml-py" in warnings[0].message


def test_nvml_init_state_logs_resolved_tuple(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """The state the GPU-offer decision consumes is traced at DEBUG."""
    monkeypatch.setattr(nvml, "_AVAILABLE", False)
    monkeypatch.setattr(nvml, "pynvml", None)
    monkeypatch.setattr(nvml, "_initialized", False)
    monkeypatch.setattr(nvml, "_warned_unavailable", True)  # silence the warn path
    monkeypatch.setattr(nvml, "_import_error", "No module named 'pynvml'")

    with caplog.at_level(logging.DEBUG, logger="trcc.adapters.sensors.nvml"):
        state = nvml.nvml_init_state()

    assert state == (False, False, None)
    assert any("nvml_init_state:" in r.message and r.levelno == logging.DEBUG
               for r in caplog.records)
