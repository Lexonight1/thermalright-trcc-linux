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

from tests.mock_platform import MockPlatform
from trcc.adapters.infra.data_install_runner import (
    SyncDataInstallRunner,
    ThreadDataInstallRunner,
)
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.events import DataInstalled, EventBus
from trcc.services.data_install import EnsureDataResult

_SCSI = "0402:3922"          # the panel in #275 (Frozen Warframe)
_SPECS = [{"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100}]

# Long enough that "did we wait for it?" is unambiguous, short enough that a
# test which DOES wait still finishes.
_SLOW_S = 1.0
# A submit that returns in under this plainly did not run the install.
_IMMEDIATE_S = 0.2


class _SlowService:
    """An install that takes real time — stands in for a large download."""

    def __init__(self, delay: float = _SLOW_S) -> None:
        self.delay = delay
        self.calls: list[tuple[int, int]] = []
        self.variants: list[tuple[str, str]] = []

    def ensure_all(self, resolution: tuple[int, int], variant: str = "",
                   mask_variant: str = "") -> EnsureDataResult:
        self.calls.append(resolution)
        self.variants.append((variant, mask_variant))
        time.sleep(self.delay)
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
    bus = EventBus()
    service = _SlowService()
    runner = ThreadDataInstallRunner(service, bus)  # type: ignore[arg-type]
    seen, arrived = _listen(bus)
    try:
        start = time.perf_counter()
        runner.submit((320, 240))
        elapsed = time.perf_counter() - start

        assert elapsed < _IMMEDIATE_S, (
            f"submit blocked for {elapsed:.2f}s — the install is back on the "
            "caller's thread, which is exactly the #275 startup hang"
        )
        assert arrived.wait(timeout=10.0), "install never completed"
        assert seen[0].resolution == (320, 240)
        assert seen[0].ok is True
    finally:
        runner.shutdown()


def test_connect_does_not_wait_for_the_download(tmp_path: Path) -> None:
    """The end-to-end guard: a slow install must not delay ConnectDevice.

    The GUI splash blocks on connect, so any time spent here is time the
    main window does not exist.
    """
    app = App(MockPlatform(_SPECS, tmp_path))
    service = _SlowService()
    # BOTH seams point at the slow install: the runner (where the work belongs)
    # and app.data_install (where it used to happen inline).  Without the
    # second, conftest's noop ``ensure_all`` makes an inline call free and this
    # test passes even with the bug reintroduced -- verified by mutation.
    app.data_install = service                  # type: ignore[assignment]
    app.data_install_runner.shutdown()          # drop the fixture's runner
    app.data_install_runner = ThreadDataInstallRunner(
        service, app.events,                    # type: ignore[arg-type]
    )
    try:
        start = time.perf_counter()
        result = app.dispatch(ConnectDevice(key=_SCSI))
        elapsed = time.perf_counter() - start

        assert result.ok is True
        assert elapsed < _IMMEDIATE_S, (
            f"ConnectDevice took {elapsed:.2f}s with a {_SLOW_S}s install — "
            "connect is waiting on the download again (#275)"
        )
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


def test_shutdown_stops_the_worker() -> None:
    bus = EventBus()
    runner = ThreadDataInstallRunner(_Service(), bus)  # type: ignore[arg-type]
    _, arrived = _listen(bus)
    runner.submit((320, 240))
    assert arrived.wait(timeout=10.0)

    runner.shutdown()

    assert not any(
        t.name == "trcc-data-install" and t.is_alive()
        for t in threading.enumerate()
    )


def test_submitting_after_shutdown_is_ignored() -> None:
    bus = EventBus()
    service = _Service()
    runner = ThreadDataInstallRunner(service, bus)  # type: ignore[arg-type]
    runner.shutdown()

    runner.submit((320, 240))

    assert service.calls == []
