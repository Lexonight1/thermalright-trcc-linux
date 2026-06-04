"""DeviceSender + SendScheduler — the per-device send worker (foundation incr 1).

Policy is tested deterministically through ``SyncSendScheduler`` (a controlled
clock, no sleeps); one thread test proves the single-consumer serialization
that the whole design rests on.  See ``doc/SEND_FOUNDATION.md``.
"""
from __future__ import annotations

import threading
import time

import pytest

from trcc.adapters.infra.send_scheduler import (
    SyncSendScheduler,
    ThreadSendScheduler,
)
from trcc.services.device_sender import DeviceSender


class FakeDevice:
    """Minimal device double — records sends, detects concurrent entry."""

    def __init__(self, key: str = "dead:beef") -> None:
        self.key = key
        self.sent: list[bytes] = []
        self.fail = False
        self._in_send = 0
        self.max_concurrent = 0
        self._lk = threading.Lock()

    def send(self, payload: bytes) -> bool:
        with self._lk:
            self._in_send += 1
            self.max_concurrent = max(self.max_concurrent, self._in_send)
        try:
            if self.fail:
                return False
            self.sent.append(payload)
            return True
        finally:
            with self._lk:
                self._in_send -= 1


# ── Policy (deterministic, no threads) ───────────────────────────────────


def test_submit_writes_latest_on_run_once() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=False)
    sender.submit(b"A")
    sender.run_once(0.0)
    assert dev.sent == [b"A"]
    assert sender.last() == b"A"


def test_latest_wins_coalescing() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=False)
    sender.submit(b"A")
    sender.submit(b"B")          # no run_once between — A is superseded
    sender.run_once(0.0)
    assert dev.sent == [b"B"]


def test_volatile_keepalive_resends_when_idle() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=True, keepalive_interval=0.150)
    sender.submit(b"F")
    sender.run_once(0.0)         # initial frame at t=0
    sender.run_once(0.0)         # idle 0s → no resend
    sender.run_once(0.150)       # idle 150ms → resend
    sender.run_once(0.300)       # → resend again
    assert dev.sent == [b"F", b"F", b"F"]


def test_volatile_keepalive_resets_on_new_frame() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=True, keepalive_interval=0.150)
    sender.submit(b"F1")
    sender.run_once(0.0)
    sender.submit(b"F2")
    sender.run_once(0.100)       # new frame (idle gate bypassed by pending)
    sender.run_once(0.200)       # 100ms since F2 → not yet
    sender.run_once(0.250)       # 150ms since F2 → keepalive F2
    assert dev.sent == [b"F1", b"F2", b"F2"]


def test_nonvolatile_never_keepalives() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=False)
    sender.submit(b"F")
    sender.run_once(0.0)
    for t in (0.150, 10.0, 100.0):
        sender.run_once(t)
    assert dev.sent == [b"F"]


def test_no_frame_no_last_does_nothing() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=True)
    sender.run_once(0.0)
    sender.run_once(0.5)
    assert dev.sent == []


def test_failed_send_not_cached_as_last() -> None:
    dev = FakeDevice()
    dev.fail = True
    sender = DeviceSender(dev, volatile=True)
    sender.submit(b"F")
    sender.run_once(0.0)
    assert sender.last() is None
    sender.run_once(0.5)         # nothing to keepalive (no successful last)
    assert dev.sent == []


def test_run_once_returns_keepalive_interval_for_volatile() -> None:
    assert DeviceSender(
        FakeDevice(), volatile=True, keepalive_interval=0.2,
    ).run_once(0.0) == 0.2


def test_run_once_returns_long_idle_for_nonvolatile() -> None:
    assert DeviceSender(FakeDevice(), volatile=False).run_once(0.0) >= 60.0


# ── SyncSendScheduler registry ───────────────────────────────────────────


def test_sync_scheduler_drives_and_removes() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=False)
    sched = SyncSendScheduler()
    sched.add(sender)
    sender.submit(b"X")
    sched.tick(0.0)
    assert dev.sent == [b"X"]
    sched.remove(sender.key)
    sender.submit(b"Y")
    sched.tick(1.0)
    assert dev.sent == [b"X"]     # removed → no longer driven


# ── ThreadSendScheduler: the single-consumer guarantee ───────────────────


def test_thread_scheduler_serializes_concurrent_producers() -> None:
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=True, keepalive_interval=0.01)
    sched = ThreadSendScheduler()
    sched.add(sender)
    try:
        def hammer(tag: str) -> None:
            for i in range(200):
                sender.submit(f"{tag}-{i}".encode())

        producers = [threading.Thread(target=hammer, args=(t,))
                     for t in ("a", "b", "c", "d")]
        for p in producers:
            p.start()
        for p in producers:
            p.join()
        time.sleep(0.1)           # let the single consumer drain + keepalive
    finally:
        sched.shutdown()
    # The whole design rests on this: one scheduler thread per task ⇒ the
    # device write is never re-entered, no matter how many producers submit.
    assert dev.max_concurrent == 1
    assert len(dev.sent) >= 1


def test_submit_wait_returns_device_result() -> None:
    # wait=True needs a running scheduler thread to process + report back.
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=False)
    sched = ThreadSendScheduler()
    sched.add(sender)
    try:
        assert sender.submit(b"X", wait=True, timeout=2.0) is True
        assert dev.sent == [b"X"]
        dev.fail = True
        assert sender.submit(b"Y", wait=True, timeout=2.0) is False
    finally:
        sched.shutdown()


def test_submit_wait_propagates_device_exception() -> None:
    # A wire exception must reach the wait=True caller's thread so the
    # dispatching Command's `except TransportError` keeps working unchanged.
    class Boom(FakeDevice):
        def send(self, payload: bytes) -> bool:
            raise RuntimeError("wire boom")

    sender = DeviceSender(Boom(), volatile=False)
    sched = ThreadSendScheduler()
    sched.add(sender)
    try:
        with pytest.raises(RuntimeError, match="wire boom"):
            sender.submit(b"X", wait=True, timeout=2.0)
    finally:
        sched.shutdown()


def test_exclusive_blocks_worker_writes() -> None:
    # A multi-frame upload (boot anim) holds the wire exclusively; the
    # keepalive worker must not interleave a write into it.
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=True, keepalive_interval=0.005)
    sched = ThreadSendScheduler()
    sched.add(sender)
    try:
        sender.submit(b"F")          # establish a frame to keepalive
        time.sleep(0.03)             # let the worker start keepaliving
        with sender.exclusive():
            for i in range(30):      # simulate the upload's direct wire writes
                dev.send(f"boot-{i}".encode())
        assert dev.max_concurrent == 1
    finally:
        sched.shutdown()


def test_thread_scheduler_shutdown_is_prompt_for_idle_nonvolatile() -> None:
    # Non-volatile task waits ~forever; wake() must let shutdown join fast.
    dev = FakeDevice()
    sender = DeviceSender(dev, volatile=False)
    sched = ThreadSendScheduler()
    sched.add(sender)
    start = time.monotonic()
    sched.shutdown()
    assert time.monotonic() - start < 1.0
