"""LhmSubprocess lifecycle tests.

Exercises probe → spawn → wait → handle and the stop() ownership
contract entirely through DI seams.  No Windows, no WMI, no real
subprocess — every blocking dependency is injected.
"""
from __future__ import annotations

from trcc.adapters.sensors._lhm import LhmSubprocess


class _StubProcess:
    """Minimal Popen stand-in for ownership / stop tests."""

    def __init__(self, pid: int = 1234) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False
        self._wait_calls = 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        return 0

    def kill(self) -> None:
        self.killed = True


# ── probe-first: namespace already up ────────────────────────────────


def test_reuses_existing_namespace_without_spawn() -> None:
    existing = object()
    spawned = []
    lhm = LhmSubprocess(
        probe=lambda: existing,
        spawn=lambda: spawned.append("called") or None,
        wait=lambda: None,
    )
    handle = lhm.start()
    assert handle is existing
    assert spawned == [], "spawn must not run when probe already succeeds"


def test_reused_namespace_stop_is_noop() -> None:
    """stop() must not terminate a process WE didn't spawn."""
    existing = object()
    lhm = LhmSubprocess(
        probe=lambda: existing,
        spawn=lambda: None,
        wait=lambda: None,
    )
    lhm.start()
    lhm.stop()  # would-be no-op since no _owned_process


# ── probe miss → spawn → wait succeeds ───────────────────────────────


def test_spawns_bundled_exe_when_probe_misses() -> None:
    proc = _StubProcess()
    namespace_after_wait = object()
    lhm = LhmSubprocess(
        probe=lambda: None,
        spawn=lambda: proc,
        wait=lambda: namespace_after_wait,
    )
    handle = lhm.start()
    assert handle is namespace_after_wait
    assert lhm.namespace is namespace_after_wait


def test_stop_terminates_spawned_process() -> None:
    proc = _StubProcess()
    lhm = LhmSubprocess(
        probe=lambda: None,
        spawn=lambda: proc,
        wait=lambda: object(),
    )
    lhm.start()
    lhm.stop()
    assert proc.terminated is True
    assert proc.killed is False


def test_start_idempotent_after_spawn() -> None:
    """Second start() returns the cached handle without re-probing/re-spawning."""
    proc = _StubProcess()
    namespace = object()
    probe_calls = [0]
    spawn_calls = [0]

    def probe():
        probe_calls[0] += 1
        return None

    def spawn():
        spawn_calls[0] += 1
        return proc

    lhm = LhmSubprocess(probe=probe, spawn=spawn, wait=lambda: namespace)
    first = lhm.start()
    second = lhm.start()
    assert first is second
    assert probe_calls[0] == 1, "probe should run exactly once"
    assert spawn_calls[0] == 1, "spawn should run exactly once"


# ── probe miss → spawn fails (no bundled exe) ────────────────────────


def test_no_bundled_exe_returns_none() -> None:
    lhm = LhmSubprocess(
        probe=lambda: None,
        spawn=lambda: None,
        wait=lambda: object(),  # wait shouldn't be called when spawn fails
    )
    assert lhm.start() is None
    assert lhm.namespace is None


def test_no_bundled_exe_stop_is_noop() -> None:
    lhm = LhmSubprocess(
        probe=lambda: None,
        spawn=lambda: None,
        wait=lambda: object(),
    )
    lhm.start()
    lhm.stop()  # no _owned_process, must not crash


# ── probe miss → spawn succeeds → wait times out ─────────────────────


def test_wait_timeout_returns_none_but_owns_process() -> None:
    """If WMI namespace doesn't register, start() returns None but we
    still own the spawned process so stop() can terminate it.
    """
    proc = _StubProcess()
    lhm = LhmSubprocess(
        probe=lambda: None,
        spawn=lambda: proc,
        wait=lambda: None,
    )
    assert lhm.start() is None
    assert lhm.namespace is None
    lhm.stop()
    assert proc.terminated is True


# ── kill fallback when terminate raises ──────────────────────────────


class _StubProcessTerminateRaises(_StubProcess):
    def wait(self, timeout: float | None = None) -> int:
        from subprocess import TimeoutExpired
        raise TimeoutExpired(cmd=[], timeout=timeout or 0)


def test_terminate_timeout_falls_back_to_kill() -> None:
    proc = _StubProcessTerminateRaises()
    lhm = LhmSubprocess(
        probe=lambda: None,
        spawn=lambda: proc,
        wait=lambda: object(),
    )
    lhm.start()
    lhm.stop()
    assert proc.terminated is True
    assert proc.killed is True


# ── _wait_for_wmi_namespace polling loop ─────────────────────────────


def test_wait_polls_until_namespace_appears() -> None:
    from trcc.adapters.sensors._lhm import _wait_for_wmi_namespace

    handle = object()
    probe_calls = [0]

    def probe():
        probe_calls[0] += 1
        return handle if probe_calls[0] >= 3 else None

    fake_clock = [0.0]

    def clock():
        return fake_clock[0]

    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        fake_clock[0] += s

    result = _wait_for_wmi_namespace(
        probe=probe, timeout_s=10.0, interval_s=0.5,
        clock=clock, sleep=sleep,
    )
    assert result is handle
    assert probe_calls[0] == 3
    assert sleeps == [0.5, 0.5]


def test_wait_returns_none_on_timeout() -> None:
    from trcc.adapters.sensors._lhm import _wait_for_wmi_namespace

    fake_clock = [0.0]

    def clock():
        return fake_clock[0]

    def sleep(s: float) -> None:
        fake_clock[0] += s

    result = _wait_for_wmi_namespace(
        probe=lambda: None,
        timeout_s=2.0, interval_s=0.5,
        clock=clock, sleep=sleep,
    )
    assert result is None
    # Timed out at clock=2.0; 4 sleeps of 0.5 each.
    assert fake_clock[0] >= 2.0
