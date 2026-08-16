"""DataInstallRunner implementations — the execution behind the data install.

``ThreadDataInstallRunner`` drains a queue of resolutions on one daemon
thread (production).  ``SyncDataInstallRunner`` installs inline so tests
stay deterministic — no threads, no sleeps, no network timing.

Both are injected at the composition root; the Commands that submit a
resolution never name a thread.  Mirrors ``send_scheduler.py``, the other
background worker in this package.
"""
from __future__ import annotations

import logging
import queue
import threading

from ...core.events import DataInstalled, EventBus
from ...core.ports import DataInstallRunner
from ...services.data_install import DataInstallService

log = logging.getLogger(__name__)

# Wakes the worker out of a blocking ``get()`` at shutdown.
_STOP = object()


def _install_and_publish(
    service: DataInstallService,
    events: EventBus,
    resolution: tuple[int, int],
) -> bool:
    """Run one install and announce the outcome.  Never raises.

    Best-effort by contract: a download failure leaves the grids empty and
    the app fully usable, so it must not propagate into whatever submitted
    it (a device connect) or kill the worker thread.
    """
    log.info("_install_and_publish: %dx%d", *resolution)
    try:
        ok = service.ensure_all(resolution).ok
    except Exception:
        log.exception("_install_and_publish: ensure_all(%s) failed", resolution)
        ok = False
    events.publish(DataInstalled(resolution=resolution, ok=ok))
    log.info("_install_and_publish: %dx%d done ok=%s", *resolution, ok)
    return ok


class _SubmitOnce:
    """Remembers which resolutions were already accepted.

    ``DiscoverDevices`` and ``ConnectDevice`` both submit the same panel's
    resolution, and a re-plug submits it again.  ``ensure_all`` is itself
    idempotent, but re-running it re-walks six directories for nothing.
    """

    __slots__ = ("_lock", "_seen")

    def __init__(self) -> None:
        log.info("_SubmitOnce.__init__")
        self._seen: set[tuple[int, int]] = set()
        self._lock = threading.Lock()

    def claim(self, resolution: tuple[int, int]) -> bool:
        """True if *resolution* is newly claimed by this caller."""
        with self._lock:
            if resolution in self._seen:
                log.debug("claim: %dx%d already submitted — skipping",
                          *resolution)
                return False
            self._seen.add(resolution)
        log.info("claim: %dx%d accepted", *resolution)
        return True


class ThreadDataInstallRunner(DataInstallRunner):
    """One daemon thread draining submitted resolutions — production."""

    def __init__(
        self,
        service: DataInstallService,
        events: EventBus,
        *,
        join_timeout: float = 2.0,
    ) -> None:
        log.info("ThreadDataInstallRunner.__init__: join_timeout=%.1fs",
                 join_timeout)
        self._service = service
        self._events = events
        self._join_timeout = join_timeout
        self._queue: queue.Queue = queue.Queue()
        self._once = _SubmitOnce()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, resolution: tuple[int, int]) -> None:
        log.info("submit: %dx%d", *resolution)
        if self._stop.is_set():
            log.warning("submit: %dx%d after shutdown — ignored", *resolution)
            return
        if not self._once.claim(resolution):
            return
        self._start()
        self._queue.put(resolution)

    def _start(self) -> None:
        """Spawn the worker on first use — a headless one-shot never does."""
        with self._lock:
            if self._thread is not None:
                return
            log.info("_start: spawning data-install worker")
            self._thread = threading.Thread(
                target=self._run, name="trcc-data-install", daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        log.info("_run: data-install worker started")
        while not self._stop.is_set():
            item = self._queue.get()
            if item is _STOP or self._stop.is_set():
                break
            _install_and_publish(self._service, self._events, item)
        log.info("_run: data-install worker stopped")

    def shutdown(self) -> None:
        log.info("shutdown: stopping data-install worker")
        self._stop.set()
        thread = self._thread
        if thread is None:
            log.debug("shutdown: worker was never started")
            return
        self._queue.put(_STOP)   # break a blocking get() so the join is prompt
        thread.join(timeout=self._join_timeout)
        if thread.is_alive():
            # Mid-download: the socket read owns the thread until its stall
            # timeout.  It is a daemon, so it cannot hold the process open.
            log.warning(
                "shutdown: data-install worker did not stop within %.1fs "
                "(likely mid-download) — abandoning it as a daemon",
                self._join_timeout,
            )
        self._thread = None


class SyncDataInstallRunner(DataInstallRunner):
    """Installs inline on the caller's thread — deterministic tests."""

    def __init__(self, service: DataInstallService, events: EventBus) -> None:
        log.info("SyncDataInstallRunner.__init__")
        self._service = service
        self._events = events
        self._once = _SubmitOnce()

    def submit(self, resolution: tuple[int, int]) -> None:
        log.info("submit: %dx%d (inline)", *resolution)
        if self._once.claim(resolution):
            _install_and_publish(self._service, self._events, resolution)

    def shutdown(self) -> None:
        log.info("shutdown: nothing to stop (inline runner)")
