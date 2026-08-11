"""NVML init classification + state reporting.

These cover the error-classification and warn-once behaviour without a real
NVIDIA driver: a fake exception carrying NVML's integer ``.value`` codes is
enough, and the module's ``getattr(module, ...)`` constant lookups fall back
to the real codes (9 / 18) when pynvml is absent.

**Nothing here mutates module state.**  Every case that needs init state
builds its own ``_NvmlRuntime``.  That is the point of the class: the four
module globals it replaced were shared mutable state, so any test that
triggered a real init attempt — or any *other* test file that ran a doctor or
a debug report first — left its outcome behind for every later reader in the
process.  On a box whose NVIDIA driver genuinely is mismatched, that turned
into ``test_a_fresh_runtime_carries_no_error`` reading back a stale
``RM has detected an NVML/RM version mismatch``, a failure about a code path
it never exercised.  See ``test_the_process_runtime_never_colours_a_fresh_one``,
which encodes exactly that bug.
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


class _FakePynvml:
    """A pynvml whose ``nvmlInit`` fails with a chosen NVML code."""

    NVML_ERROR_DRIVER_NOT_LOADED = 9
    NVML_ERROR_LIB_RM_VERSION_MISMATCH = 18

    def __init__(self, fail_with: int | None) -> None:
        self._fail_with = fail_with

    def nvmlInit(self) -> None:            # camelCase mirrors pynvml's own name
        if self._fail_with is not None:
            raise _FakeNvmlError(self._fail_with)


# ── error classification (pure functions — the module is an argument) ──


def test_driver_not_loaded_is_transient() -> None:
    # NVML_ERROR_DRIVER_NOT_LOADED == 9 — the GPU may autostart; retry quietly.
    assert nvml._is_transient_nvml_error(_FakeNvmlError(9), None) is True


def test_version_mismatch_is_not_transient() -> None:
    # NVML_ERROR_LIB_RM_VERSION_MISMATCH == 18 — won't fix itself on retry.
    assert nvml._is_transient_nvml_error(_FakeNvmlError(18), None) is False


def test_unknown_error_is_not_transient() -> None:
    assert nvml._is_transient_nvml_error(_FakeNvmlError(999), None) is False


def test_classification_reads_codes_off_the_module_it_is_given() -> None:
    """The constants come from the passed module, not a global.

    A module reporting a *different* DRIVER_NOT_LOADED reclassifies the same
    exception — which is what proves the lookup is not silently falling back
    to the hardcoded 9 in every case above.
    """
    class _Renumbered:
        NVML_ERROR_DRIVER_NOT_LOADED = 999

    assert nvml._is_transient_nvml_error(_FakeNvmlError(999), _Renumbered()) is True
    assert nvml._is_transient_nvml_error(_FakeNvmlError(9), _Renumbered()) is False


def test_fix_hint_for_version_mismatch_is_the_reload_command() -> None:
    hint = nvml._nvml_fix_hint(_FakeNvmlError(18), None)
    assert nvml.NVML_RELOAD_HINT in hint
    assert "modprobe" in hint


def test_fix_hint_for_other_error_is_generic_driver_advice() -> None:
    hint = nvml._nvml_fix_hint(_FakeNvmlError(999), None)
    assert "driver" in hint.lower()
    assert "modprobe" not in hint


# ── init behaviour, each on its own runtime ────────────────────────────


def test_non_transient_init_failure_warns_exactly_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A version mismatch logs WARNING once, not on every retry."""
    runtime = nvml._NvmlRuntime(_FakePynvml(fail_with=18), None)

    with caplog.at_level(logging.WARNING, logger="trcc.adapters.sensors.nvml"):
        assert runtime.ensure_init() is False
        assert runtime.ensure_init() is False  # retry — must not re-warn

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "NVML init failed" in warnings[0].message
    assert nvml.NVML_RELOAD_HINT in warnings[0].message


def test_transient_init_failure_stays_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DRIVER_NOT_LOADED never reaches WARNING — it's the normal autostart case."""
    runtime = nvml._NvmlRuntime(_FakePynvml(fail_with=9), None)

    with caplog.at_level(logging.DEBUG, logger="trcc.adapters.sensors.nvml"):
        assert runtime.ensure_init() is False

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_successful_init_is_reported_and_not_repeated() -> None:
    """A runtime that inits reports no error and does not re-enter nvmlInit."""
    class _CountingPynvml(_FakePynvml):
        def __init__(self) -> None:
            super().__init__(fail_with=None)
            self.calls = 0

        def nvmlInit(self) -> None:        # camelCase mirrors pynvml's own name
            self.calls += 1

    module = _CountingPynvml()
    runtime = nvml._NvmlRuntime(module, None)

    assert runtime.ensure_init() is True
    assert runtime.ensure_init() is True
    assert module.calls == 1
    assert runtime.state() == (True, True, None)


def test_unavailable_pynvml_warns_exactly_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """pynvml not importable (the #161 case) logs WARNING once, not per retry."""
    runtime = nvml._NvmlRuntime(None, "No module named 'pynvml'")

    with caplog.at_level(logging.WARNING, logger="trcc.adapters.sensors.nvml"):
        assert runtime.ensure_init() is False
        assert runtime.ensure_init() is False  # retry — must not re-warn

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not importable" in warnings[0].message
    assert "No module named 'pynvml'" in warnings[0].message
    assert "nvidia-ml-py" in warnings[0].message


def test_a_fresh_runtime_carries_no_error() -> None:
    """The state the GPU-offer decision consumes, on a runtime with no history."""
    runtime = nvml._NvmlRuntime(None, "No module named 'pynvml'")
    assert runtime.state() == (False, False, None)


def test_a_runtime_remembers_its_own_failure() -> None:
    """The counterpart: an error IS reported — by the runtime that hit it."""
    runtime = nvml._NvmlRuntime(_FakePynvml(fail_with=18), None)
    available, initialized, error = runtime.state()
    assert (available, initialized) == (True, False)
    assert error is not None and "18" in error


# ── the leak this class exists to prevent ──────────────────────────────


def test_the_process_runtime_never_colours_a_fresh_one() -> None:
    """One caller's init failure must never reach another's view.

    This encodes the original bug directly.  ``nvml_init_state()`` is
    documented to trigger an init attempt, so the doctor, a debug report and
    the twelve tests in ``test_diagnostics.py`` all write the process
    runtime's state — and on a machine whose kernel module and userspace
    libnvidia-ml are out of sync, what they write is a real error string.
    Before ``_NvmlRuntime``, that string was module-global and the next reader
    inherited it.

    MUTATION CHECK — point the second runtime at the shared one::

        fresh = nvml._runtime

    and this fails on any box with a broken NVIDIA driver.
    """
    nvml.nvml_init_state()          # dirty the process runtime, as callers do

    fresh = nvml._NvmlRuntime(None, "No module named 'pynvml'")
    assert fresh.state() == (False, False, None)


def test_one_runtimes_failure_never_reaches_another() -> None:
    """The same isolation between two explicit runtimes, driver-independent."""
    failed = nvml._NvmlRuntime(_FakePynvml(fail_with=18), None)
    assert failed.ensure_init() is False
    assert failed.state()[2] is not None

    fresh = nvml._NvmlRuntime(_FakePynvml(fail_with=18), None)
    assert fresh._error is None, "a new runtime started with inherited state"


# ── the public function ────────────────────────────────────────────────


def test_nvml_init_state_returns_three_tuple() -> None:
    reader_available, initialized, error = nvml.nvml_init_state()
    assert isinstance(reader_available, bool)
    assert isinstance(initialized, bool)
    assert error is None or isinstance(error, str)


def test_nvml_init_state_logs_resolved_tuple(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The state the GPU-offer decision consumes is traced at DEBUG."""
    with caplog.at_level(logging.DEBUG, logger="trcc.adapters.sensors.nvml"):
        nvml.nvml_init_state()

    assert any("nvml_init_state:" in r.message and r.levelno == logging.DEBUG
               for r in caplog.records)
