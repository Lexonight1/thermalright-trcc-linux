"""Windows: how long does a log record wait for the shared log's lock, old vs new?

    python log_lock_bench.py <src dir> [workers] [records]

WORKERS processes each write RECORDS records through the real
``WindowsSharedLogHandler`` into one file -- once with ``LockFileEx`` (the
handler as it is), once with ``msvcrt.locking(LK_LOCK)`` (how it was until
2026-10-20).  ``LK_LOCK`` sleeps a whole second whenever a peer holds the byte,
which froze the GUI behind a busy App.  Prints per-record latency and checks
every record reached the file.  Touches nothing of TRCC's: its own temp file.
"""
import logging
import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


if sys.platform != "win32":
    raise SystemExit("log_lock_bench runs on Windows (the VM): msvcrt and LockFileEx are Windows-only")


def _msvcrt_handler_class() -> type:
    from trcc.adapters.infra.logging import WindowsSharedLogHandler

    class MsvcrtHandler(WindowsSharedLogHandler):
        def _acquire(self) -> None:
            import msvcrt
            os.lseek(self._lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(self._lock_fd, msvcrt.LK_LOCK, 1)

        def _release(self) -> None:
            import msvcrt
            os.lseek(self._lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(self._lock_fd, msvcrt.LK_UNLCK, 1)

    return MsvcrtHandler


def worker(src: str, kind: str, path: str, records: int, out: "mp.Queue",
           ready: "mp.Queue", go: Any) -> None:
    sys.path.insert(0, src)
    from trcc.adapters.infra.logging import WindowsSharedLogHandler
    cls = WindowsSharedLogHandler if kind == "LockFileEx" else _msvcrt_handler_class()
    handler = cls(Path(path), maxBytes=200_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(process)d %(message)s"))
    waits = []
    # All start together: a Windows process takes longer to spawn than 600
    # records take to write, so ungated workers never met (first run).
    ready.put(os.getpid())
    go.wait()
    for i in range(records):
        record = logging.LogRecord("bench", logging.INFO, __file__, 1, "record %d", (i,), None)
        start = time.perf_counter()
        handler.handle(record)
        waits.append(time.perf_counter() - start)
    handler.close()
    out.put(waits)


def run(src: str, kind: str, workers: int, records: int) -> None:
    path = Path(tempfile.mkdtemp()) / "bench.log"
    out: mp.Queue = mp.Queue()
    ready: mp.Queue = mp.Queue()
    go = mp.Event()
    procs = [mp.Process(target=worker, args=(src, kind, str(path), records, out, ready, go))
             for _ in range(workers)]
    for p in procs:
        p.start()
    for _ in procs:
        ready.get()
    began = time.perf_counter()
    go.set()
    waits = sorted(w for _ in procs for w in out.get())
    for p in procs:
        p.join()
    took = time.perf_counter() - began
    lines = len(path.read_text(encoding="utf-8").splitlines())

    def q(fraction: float) -> float:
        return waits[int(fraction * (len(waits) - 1))] * 1000

    print(f"{kind:10} {workers}x{records}: {took:6.1f} s, {lines}/{workers * records} lines | "
          f"ms p50 {q(.5):.2f} p99 {q(.99):.2f} max {waits[-1] * 1000:.0f} | "
          f">0.5 s: {sum(1 for w in waits if w > 0.5)}")


if __name__ == "__main__":
    source = sys.argv[1]
    n_workers = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    n_records = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
    # One writer first: what a record costs with nobody to wait for.  The
    # contended figure alone mixes that cost with taking turns.
    for writers in (1, n_workers):
        for lock in ("LockFileEx", "msvcrt"):
            run(source, lock, writers, n_records)
