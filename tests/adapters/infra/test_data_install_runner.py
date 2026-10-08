"""DataInstallRunner — the install must never sit on the caller's thread.

#275: ``ConnectDevice`` called ``ensure_all`` inline, so six archives
(~30 MB for a non-square panel) downloaded before connect returned — and the
GUI's splash waits on connect, so the main window could not appear until the
last byte landed.  A stalled route made that minutes; an unreachable one made
it far worse.  These tests pin the contract that fixed it: submitting returns
at once, the work happens elsewhere, and every UI hears ``DataInstalled``.

The classes are imported at module scope on purpose: ``conftest`` swaps
``ThreadDataInstallRunner`` for the sync one so the rest of the suite stays
deterministic, and that patch lands after this binding — these tests want the
real worker.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from tests.mock_platform import MockPlatform
from trcc.adapters.infra.data_install_runner import (
    SyncDataInstallRunner,
    ThreadDataInstallRunner,
)
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.events import DataInstalled, ErrorOccurred, EventBus
from trcc.services.data_install import EnsureDataResult

_SCSI = "0402:3922"          # the panel in #275 (Frozen Warframe)
_SPECS = [{"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100}]

#: How long a gated install waits before giving up.  NOT a threshold: it only
#: bounds a hang.  An install run INLINE blocks its caller until this expires,
#: so the order below reverses and the test fails — on any machine.
_HANG_S = 10.0


class _GatedService:
    """An install that cannot finish until the test opens its gate.

    Stands in for a large download.  It used to ``sleep(1.0)`` while the test
    asserted the caller returned within 0.2 s — a wall clock with a 2x margin
    that a slow CI runner tripped (0.43 s on 3.10) with nothing wrong.  Now the
    test asserts ORDER: the caller returned, THEN the install finished.
    """

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.gate = threading.Event()
        self.calls: list[tuple[int, int]] = []
        self.variants: list[tuple[str, str]] = []

    def ensure_all(self, resolution: tuple[int, int], variant: str = "",
                   mask_variant: str = "") -> EnsureDataResult:
        self.calls.append(resolution)
        self.variants.append((variant, mask_variant))
        self.gate.wait(_HANG_S)
        self.order.append("install finished")
        return EnsureDataResult(
            resolution=resolution, themes_ok=True, web_ok=True, masks_ok=True,
        )


class _Service:
    """Instant install with a settable outcome."""

    def __init__(self, *, ok: bool = True, raises: bool = False) -> None:
        self.ok = ok
        self.raises = raises
        self.calls: list[tuple[int, int]] = []
        self.variants: list[tuple[str, str]] = []

    def ensure_all(self, resolution: tuple[int, int], variant: str = "",
                   mask_variant: str = "") -> EnsureDataResult:
        self.calls.append(resolution)
        self.variants.append((variant, mask_variant))
        if self.raises:
            raise RuntimeError("network on fire")
        return EnsureDataResult(
            resolution=resolution,
            themes_ok=self.ok, web_ok=self.ok, masks_ok=self.ok,
        )


def _listen(bus: EventBus) -> tuple[list[DataInstalled], threading.Event]:
    """Collect DataInstalled events + an Event that fires on each one."""
    seen: list[DataInstalled] = []
    arrived = threading.Event()

    def _on(event: DataInstalled) -> None:
        seen.append(event)
        arrived.set()

    bus.subscribe(DataInstalled, _on)
    return seen, arrived


# ── the #275 contract ────────────────────────────────────────────────────


def test_submit_returns_before_a_slow_install_finishes() -> None:
    """MUTATION CHECK: run the install inline in ``submit`` → this fails."""
    bus = EventBus()
    order: list[str] = []
    service = _GatedService(order)
    runner = ThreadDataInstallRunner(service, bus)  # type: ignore[arg-type]
    seen, arrived = _listen(bus)
    try:
        runner.submit((320, 240))
        order.append("submit returned")
        service.gate.set()

        assert arrived.wait(timeout=_HANG_S), "install never completed"
        assert order == ["submit returned", "install finished"], (
            "submit waited for the install — it is back on the caller's "
            "thread, which is exactly the #275 startup hang")
        assert seen[0].resolution == (320, 240)
        assert seen[0].ok is True
    finally:
        runner.shutdown()


def test_connect_does_not_wait_for_the_download(tmp_path: Path) -> None:
    """The end-to-end guard: a slow install must not delay ConnectDevice.

    The GUI splash blocks on connect, so any time spent here is time the
    main window does not exist.
    MUTATION CHECK: call ``app.data_install.ensure_all`` inline in
    ``ConnectDevice`` (the #275 shape) → this fails.
    """
    app = App(MockPlatform(_SPECS, tmp_path))
    order: list[str] = []
    service = _GatedService(order)
    # BOTH seams point at the gated install: the runner (where the work belongs)
    # and app.data_install (where it used to happen inline).  Without the
    # second, conftest's noop ``ensure_all`` makes an inline call free and this
    # test passes even with the bug reintroduced -- verified by mutation.
    app.data_install = service                  # type: ignore[assignment]
    app.data_install_runner.shutdown()          # drop the fixture's runner
    app.data_install_runner = ThreadDataInstallRunner(
        service, app.events,                    # type: ignore[arg-type]
    )
    _seen, arrived = _listen(app.events)
    try:
        result = app.dispatch(ConnectDevice(key=_SCSI))
        order.append("connect returned")
        service.gate.set()

        assert result.ok is True
        assert arrived.wait(timeout=_HANG_S), "install never completed"
        assert order == ["connect returned", "install finished"], (
            "ConnectDevice waited for the download again (#275)")
    finally:
        app.close()


# ── worker behaviour ─────────────────────────────────────────────────────


def test_each_resolution_installs_once_however_often_it_is_submitted() -> None:
    """Discover and connect both submit the same panel; it downloads once."""
    bus = EventBus()
    service = _Service()
    runner = SyncDataInstallRunner(service, bus)  # type: ignore[arg-type]

    runner.submit((320, 240))
    runner.submit((320, 240))
    runner.submit((480, 854))

    assert service.calls == [(320, 240), (480, 854)]


def test_a_partial_install_is_reported_not_hidden() -> None:
    bus = EventBus()
    seen, _ = _listen(bus)
    runner = SyncDataInstallRunner(_Service(ok=False), bus)  # type: ignore[arg-type]

    runner.submit((320, 240))

    assert seen[0].ok is False


def test_a_raising_install_still_publishes_and_keeps_the_worker_alive() -> None:
    """Best-effort by contract: a failed download degrades, never propagates."""
    bus = EventBus()
    seen, arrived = _listen(bus)
    runner = ThreadDataInstallRunner(_Service(raises=True), bus)  # type: ignore[arg-type]
    try:
        runner.submit((320, 240))
        assert arrived.wait(timeout=10.0)
        assert seen[0].ok is False

        # The worker survived the exception and serves the next submission.
        arrived.clear()
        runner.submit((480, 854))
        assert arrived.wait(timeout=10.0), "worker died on the first failure"
        assert seen[-1].resolution == (480, 854)
    finally:
        runner.shutdown()


def _worker_alive() -> bool:
    return any(t.name == "trcc-data-install" and t.is_alive()
               for t in threading.enumerate())


def test_shutdown_stops_the_worker() -> None:
    """The named worker exists while running, and is gone after shutdown.

    The "is gone" half alone was VACUOUS: it asserted that no live thread is
    called ``trcc-data-install``, which is equally true when the worker was
    never given that name.  Renaming the thread left it green.  The thread
    name is identity -- ``QueueWorker`` takes it as a constructor argument and
    this is what holds it -- so the existence half has to be asserted too.
    """
    bus = EventBus()
    runner = ThreadDataInstallRunner(_Service(), bus)  # type: ignore[arg-type]
    _, arrived = _listen(bus)
    runner.submit((320, 240))
    assert arrived.wait(timeout=10.0)

    assert _worker_alive(), (
        "no live thread named 'trcc-data-install' while an install is in "
        "flight — the worker is unnamed or was never spawned"
    )

    runner.shutdown()

    assert not _worker_alive()


def test_submitting_after_shutdown_is_ignored() -> None:
    bus = EventBus()
    service = _Service()
    runner = ThreadDataInstallRunner(service, bus)  # type: ignore[arg-type]
    runner.shutdown()

    runner.submit((320, 240))

    assert service.calls == []


# ── #309: a failed download comes back, and the user hears about it ─────


class _Outcomes:
    """Install outcomes in order: each call takes the next, the last repeats."""

    def __init__(self, *oks: bool) -> None:
        self.oks = list(oks)
        self.calls: list[tuple[int, int]] = []

    def ensure_all(self, resolution: tuple[int, int], variant: str = "",
                   mask_variant: str = "") -> EnsureDataResult:
        ok = self.oks[min(len(self.calls), len(self.oks) - 1)]
        self.calls.append(resolution)
        return EnsureDataResult(resolution=resolution, themes_ok=ok,
                                web_ok=True, masks_ok=ok)


def _errors(bus: EventBus) -> list[ErrorOccurred]:
    seen: list[ErrorOccurred] = []
    bus.subscribe(ErrorOccurred, seen.append)
    return seen


def test_a_failed_install_is_tried_again_on_the_next_submit() -> None:
    """The claim used to outlive a FAILED install, so nothing ever retried it.

    The App outlives every window, so reopening the gui did not help either:
    empty grids until ``trcc kill`` or a replug.  A success still installs
    once (the test above).
    MUTATION CHECK: drop ``release`` in ``SyncDataInstallRunner.submit`` →
    this fails.
    """
    bus = EventBus()
    service = _Outcomes(False, True)
    runner = SyncDataInstallRunner(service, bus)  # type: ignore[arg-type]

    runner.submit((320, 240))
    runner.submit((320, 240))
    runner.submit((320, 240))

    assert service.calls == [(320, 240), (320, 240)]


def test_a_failed_install_reaches_the_user_not_only_the_log() -> None:
    """#309: the grids sat empty and nothing on screen said why."""
    bus = EventBus()
    errors = _errors(bus)
    runner = SyncDataInstallRunner(_Outcomes(False), bus)  # type: ignore[arg-type]

    runner.submit((320, 240))

    assert [(e.kind, e.message) for e in errors] == [(
        "download",
        "Could not download the themes, masks for the 320x240 panel. "
        "Check the network connection. "
        "It will be tried again when the panel reconnects.",
    )]


def test_the_app_retries_a_failed_install_by_itself() -> None:
    """An App autostarted before the network is up recovers without a replug.

    MUTATION CHECK: never start the retry timer → the second install never
    comes and this fails.
    """
    bus = EventBus()
    service = _Outcomes(False, True)
    seen, arrived = _listen(bus)
    errors = _errors(bus)
    runner = ThreadDataInstallRunner(
        service, bus, retry_delays_s=(0.01,),  # type: ignore[arg-type]
    )
    try:
        runner.submit((320, 240))
        deadline = time.monotonic() + _HANG_S
        while len(seen) < 2 and time.monotonic() < deadline:
            arrived.wait(0.05)
            arrived.clear()

        assert [e.ok for e in seen] == [False, True]
        assert service.calls == [(320, 240), (320, 240)]
        assert errors[0].message.endswith("Retrying in 0 s.")
    finally:
        runner.shutdown()


def test_the_retries_stop_after_the_last_delay() -> None:
    """Three tries, not a loop that hammers GitHub for the App's lifetime."""
    bus = EventBus()
    service = _Outcomes(False)
    seen, arrived = _listen(bus)
    errors = _errors(bus)
    runner = ThreadDataInstallRunner(
        service, bus, retry_delays_s=(0.01,),  # type: ignore[arg-type]
    )
    try:
        runner.submit((320, 240))
        deadline = time.monotonic() + _HANG_S
        while len(seen) < 2 and time.monotonic() < deadline:
            arrived.wait(0.05)
            arrived.clear()
        time.sleep(0.3)   # a third try would land well inside this

        assert len(service.calls) == 2
        assert errors[-1].message.endswith(
            "It will be tried again when the panel reconnects.")
    finally:
        runner.shutdown()


def test_shutdown_cancels_a_pending_retry() -> None:
    bus = EventBus()
    service = _Outcomes(False)
    _, arrived = _listen(bus)
    runner = ThreadDataInstallRunner(
        service, bus, retry_delays_s=(30.0,),  # type: ignore[arg-type]
    )
    runner.submit((320, 240))
    assert arrived.wait(timeout=_HANG_S)

    runner.shutdown()

    assert not any(t.name == "trcc-data-install-retry" and t.is_alive()
                   for t in threading.enumerate())

# ── the invariant the spawn guard actually protects ──────────────────────


class _OverlapProbe:
    """Records the highest number of installs running at the same moment."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0

    def ensure_all(self, resolution, variant="", mask_variant=""):
        del resolution, variant, mask_variant
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        time.sleep(0.15)
        with self._lock:
            self.live -= 1
        return SimpleNamespace(ok=True)


def test_two_submissions_are_served_one_at_a_time() -> None:
    """One worker drains the queue — submissions never overlap.

    ``_start`` spawns the worker on FIRST use and returns early on every call
    after that.  Nothing tested the early return, so deleting it left the
    whole suite green: every existing test submits once, or submits twice and
    only checks that the events arrive.

    What the guard protects is not a thread count, it is SERIALIZATION.  With
    it removed, a second worker spawns and two installs run concurrently —
    measured peak 1 -> 2 — and ``shutdown`` then joins only the last thread it
    stored, logging "did not stop within 2.0s".  ``VideoExportRunner`` states
    the same contract outright: "Serialized on purpose.  Two concurrent ffmpeg
    runs over the same machine finish no sooner together than in turn."

    So this asserts the overlap, not the thread count: a future runner that
    serialises some other way still passes, and one that does not still fails.
    """
    bus = EventBus()
    probe = _OverlapProbe()
    runner = ThreadDataInstallRunner(probe, bus)   # type: ignore[arg-type]
    try:
        runner.submit((320, 240))
        runner.submit((480, 854))          # distinct key — dedupe won't block it
        deadline = time.monotonic() + 10.0
        while probe.peak == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.4)                    # let both run to completion
        assert probe.peak == 1, (
            f"{probe.peak} installs ran at once — the worker is no longer "
            "serialising, so a second spawn is draining the same queue"
        )
    finally:
        runner.shutdown()
