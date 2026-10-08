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
import threading

from ...core.events import DataInstalled, ErrorOccurred, EventBus
from ...core.ports import DataInstallRunner
from ...services.data_install import DataInstallService
from ._worker import QueueWorker

log = logging.getLogger(__name__)

#: (resolution, variant, mask_variant) — one queued install.
_Job = tuple[tuple[int, int], str, str]

#: Seconds before each automatic retry of a failed install, then give up
#: until the panel reconnects.  The common failure is an App started at
#: login before the network is up, which the first retry already outlives.
_RETRY_DELAYS_S: tuple[float, ...] = (30.0, 120.0, 600.0)

_ALL_PARTS = ("themes", "cloud backgrounds", "masks")


def _install_and_publish(
    service: DataInstallService,
    events: EventBus,
    resolution: tuple[int, int],
    variant: str = "",
    mask_variant: str = "",
) -> tuple[str, ...]:
    """Run one install and announce the outcome.  Never raises.

    Best-effort by contract: a download failure leaves the grids empty and
    the app fully usable, so it must not propagate into whatever submitted
    it (a device connect) or kill the worker thread.  Returns the parts that
    failed — empty when everything landed.
    """
    log.info("_install_and_publish: %dx%d variant=%r mask=%r",
             *resolution, variant, mask_variant)
    try:
        result = service.ensure_all(resolution, variant, mask_variant)
        failed = tuple(part for part, ok in zip(
            _ALL_PARTS, (result.themes_ok, result.web_ok, result.masks_ok),
            strict=True,
        ) if not ok)
    except Exception:
        log.exception("_install_and_publish: ensure_all(%s) failed", resolution)
        failed = _ALL_PARTS
    events.publish(DataInstalled(resolution=resolution, ok=not failed))
    log.info("_install_and_publish: %dx%d done failed=%s", *resolution, failed)
    return failed


def _report_failure(events: EventBus, resolution: tuple[int, int],
                    failed: tuple[str, ...], retry_in: float | None) -> None:
    """Tell every open UI the download failed, and what happens next (#309).

    It used to reach the log only, so a user saw empty theme and mask grids
    with nothing saying why or whether it would come back.
    """
    then = (f"Retrying in {retry_in:.0f} s." if retry_in is not None else
            "It will be tried again when the panel reconnects.")
    message = (f"Could not download the {', '.join(failed)} for the "
               f"{resolution[0]}x{resolution[1]} panel. "
               f"Check the network connection. {then}")
    log.warning("_report_failure: %s", message)
    events.publish(ErrorOccurred(message=message, kind="download"))


class _SubmitOnce:
    """Remembers which resolutions were already accepted.

    ``DiscoverDevices`` and ``ConnectDevice`` both submit the same panel's
    resolution, and a re-plug submits it again.  ``ensure_all`` is itself
    idempotent, but re-running it re-walks six directories for nothing.
    """

    __slots__ = ("_lock", "_seen")

    def __init__(self) -> None:
        log.info("_SubmitOnce.__init__")
        self._seen: set[tuple[int, int, str, str]] = set()
        self._lock = threading.Lock()

    def claim(self, resolution: tuple[int, int],
              variant: str = "", mask_variant: str = "") -> bool:
        """True if this exact request is newly claimed by this caller.

        Keyed on the SUFFIXES as well as the size: two coolers can share a
        panel and want different artwork libraries (1600x720 at SUB 3 wants
        ``1600720l``, SUB 5 wants ``1600720``).  Claiming on resolution alone
        would let whichever connected first suppress the other's download and
        leave that device with an empty grid.
        """
        key = (*resolution, variant, mask_variant)
        with self._lock:
            if key in self._seen:
                log.debug("claim: %dx%d variant=%r mask=%r already submitted "
                          "— skipping", *resolution, variant, mask_variant)
                return False
            self._seen.add(key)
        log.info("claim: %dx%d variant=%r mask=%r accepted",
                 *resolution, variant, mask_variant)
        return True

    def release(self, resolution: tuple[int, int],
                variant: str = "", mask_variant: str = "") -> None:
        """Forget a claim, so the next submit of it installs again.

        Called when an install FAILED.  Without it a failed download was
        never retried for the App's lifetime — and the App outlives every
        window, so reopening the gui did not retry either (#309).
        """
        log.info("release: %dx%d variant=%r mask=%r",
                 *resolution, variant, mask_variant)
        with self._lock:
            self._seen.discard((*resolution, variant, mask_variant))


class ThreadDataInstallRunner(DataInstallRunner):
    """One daemon thread draining submitted resolutions — production."""

    def __init__(
        self,
        service: DataInstallService,
        events: EventBus,
        *,
        join_timeout: float = 2.0,
        retry_delays_s: tuple[float, ...] = _RETRY_DELAYS_S,
    ) -> None:
        log.info("ThreadDataInstallRunner.__init__: join_timeout=%.1fs "
                 "retry_delays_s=%s", join_timeout, retry_delays_s)
        self._service = service
        self._events = events
        self._once = _SubmitOnce()
        self._retry_delays_s = retry_delays_s
        self._attempts: dict[_Job, int] = {}
        self._timers: set[threading.Timer] = set()
        self._timers_lock = threading.Lock()
        self._worker: QueueWorker[_Job] = QueueWorker(
            "trcc-data-install", self._install,
            stall_hint="mid-download", join_timeout=join_timeout,
        )

    def submit(self, resolution: tuple[int, int],
               variant: str = "", mask_variant: str = "") -> None:
        log.info("submit: %dx%d variant=%r mask=%r",
                 *resolution, variant, mask_variant)
        if self._once.claim(resolution, variant, mask_variant):
            self._worker.submit((resolution, variant, mask_variant))

    def _install(self, job: _Job) -> None:
        """Run one queued job; on failure, release it and schedule a retry."""
        log.debug("_install: %dx%d variant=%r mask=%r", *job[0], job[1], job[2])
        failed = _install_and_publish(self._service, self._events, *job)
        with self._timers_lock:
            if not failed:
                self._attempts.pop(job, None)
                return
            attempt = self._attempts.get(job, 0)
            retry_in = (self._retry_delays_s[attempt]
                        if attempt < len(self._retry_delays_s) else None)
            if retry_in is None:
                self._attempts.pop(job, None)
            else:
                self._attempts[job] = attempt + 1
                timer = threading.Timer(retry_in, self._retry, args=(job,))
                timer.daemon = True
                timer.name = "trcc-data-install-retry"
                self._timers.add(timer)
                timer.start()
        self._once.release(*job)
        log.info("_install: %dx%d attempt %d failed — retry in %s s",
                 *job[0], attempt + 1, retry_in)
        _report_failure(self._events, job[0], failed, retry_in)

    def _retry(self, job: _Job) -> None:
        """A retry timer fired: submit the job again like a reconnect would."""
        log.info("_retry: %dx%d variant=%r mask=%r", *job[0], job[1], job[2])
        with self._timers_lock:
            # A Timer runs its callback on its own thread, so this is it.
            self._timers.discard(threading.current_thread())
        self.submit(*job)

    def shutdown(self) -> None:
        log.info("shutdown: stopping data-install worker")
        with self._timers_lock:
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
        self._worker.shutdown()


class SyncDataInstallRunner(DataInstallRunner):
    """Installs inline on the caller's thread — deterministic tests."""

    def __init__(self, service: DataInstallService, events: EventBus) -> None:
        log.info("SyncDataInstallRunner.__init__")
        self._service = service
        self._events = events
        self._once = _SubmitOnce()

    def submit(self, resolution: tuple[int, int],
               variant: str = "", mask_variant: str = "") -> None:
        log.info("submit: %dx%d variant=%r mask=%r (inline)",
                 *resolution, variant, mask_variant)
        if self._once.claim(resolution, variant, mask_variant):
            failed = _install_and_publish(self._service, self._events,
                                          resolution, variant, mask_variant)
            if failed:
                # No timers here — tests stay deterministic.  The claim is
                # still released, so the next submit retries.
                self._once.release(resolution, variant, mask_variant)
                _report_failure(self._events, resolution, failed, None)

    def shutdown(self) -> None:
        log.info("shutdown: nothing to stop (inline runner)")
