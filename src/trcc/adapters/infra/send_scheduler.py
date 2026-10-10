"""SendScheduler implementations — the execution behind the send worker.

``ThreadSendScheduler`` runs one daemon thread per :class:`SendTask` (the
production driver — single consumer per device ⇒ serialized writes).
``SyncSendScheduler`` does nothing on its own; tests call :meth:`tick` to drive
every task deterministically with a controlled clock — no threads, no sleeps.

Both are injected at the composition root; the tasks (``DeviceSender``) never
name a thread.  See ``doc/SEND_FOUNDATION.md``.
"""
from __future__ import annotations

import logging
import threading
import time

from ...core.ports import SendScheduler, SendTask

log = logging.getLogger(__name__)


class _TaskThread:
    """A daemon thread that drives one task: ``wait(delay) → run_once``."""

    __slots__ = ("_stop", "_task", "_thread")

    def __init__(self, task: SendTask) -> None:
        log.debug("__init__: task=%s", task)
        self._task = task
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"trcc-send-{task.key}", daemon=True,
        )

    def start(self) -> None:
        log.debug("start")
        self._thread.start()

    def _run(self) -> None:
        # First pass establishes the initial delay (e.g. a volatile task wants
        # to keepalive even if no frame has been submitted yet).
        log.debug("_run")
        delay = self._task.run_once(time.monotonic())
        while not self._stop.is_set():
            self._task.wait(delay)
            if self._stop.is_set():
                break
            delay = self._task.run_once(time.monotonic())

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._stop.set()
        if threading.current_thread() is self._thread:
            # A task ending itself from inside ``run_once`` (the screencast
            # driver finding its session gone).  Joining would raise "cannot
            # join current thread"; the loop exits on the flag when it returns.
            log.debug("stop: %s stopped from its own thread", self._task.key)
            return
        self._task.wake()   # interrupt a long-idle wait so the join is prompt
        self._thread.join(timeout=join_timeout)
        if self._thread.is_alive():
            log.warning("ThreadSendScheduler: task %s did not stop within %.1fs",
                        self._task.key, join_timeout)


class ThreadSendScheduler(SendScheduler):
    """One daemon thread per task — the production execution model."""

    def __init__(self) -> None:
        log.debug("__init__")
        self._threads: dict[str, _TaskThread] = {}
        self._lock = threading.Lock()

    def add(self, task: SendTask) -> None:
        log.info("ThreadSendScheduler: add %s", task.key)
        with self._lock:
            existing = self._threads.pop(task.key, None)
            if existing is not None:
                # Replacing IS the contract (every driver Command is idempotent
                # by it), so this is not a warning: re-issuing a screencast or a
                # slideshow toggle wrote one into the report every time.
                log.info("ThreadSendScheduler: replacing existing task %s",
                         task.key)
                existing.stop()
            worker = _TaskThread(task)
            self._threads[task.key] = worker
        worker.start()

    def remove(self, key: str) -> None:
        log.info("ThreadSendScheduler: remove %s", key)
        with self._lock:
            worker = self._threads.pop(key, None)
        if worker is not None:
            worker.stop()

    def shutdown(self) -> None:
        with self._lock:
            workers = list(self._threads.values())
            self._threads.clear()
        log.info("ThreadSendScheduler: shutdown (%d task(s))", len(workers))
        for worker in workers:
            worker.stop()


class SyncSendScheduler(SendScheduler):
    """Deterministic driver — tests call :meth:`tick(now)`; no threads."""

    def __init__(self) -> None:
        log.debug("__init__")
        self._tasks: dict[str, SendTask] = {}

    def add(self, task: SendTask) -> None:
        log.debug("SyncSendScheduler: add %s", task.key)
        task.runs_on_caller()           # no thread here: a waiter runs it
        self._tasks[task.key] = task

    def remove(self, key: str) -> None:
        log.debug("SyncSendScheduler: remove %s", key)
        self._tasks.pop(key, None)

    def shutdown(self) -> None:
        log.debug("shutdown")
        self._tasks.clear()

    def tick(self, now: float) -> None:
        """Drive every registered task once with clock *now* (seconds)."""
        log.debug("tick: now=%s", now)
        for task in list(self._tasks.values()):
            task.run_once(now)
