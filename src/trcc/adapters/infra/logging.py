"""Structured logging setup — rotating file + stderr.

Replaces legacy ``adapters/infra/diagnostics.py``'s 200-line logging
block with a focused configurator that:

* writes to ``Paths.log_file()`` with rotation at 1 MB × 5 backups so
  long-lived daemons don't fill the disk;
* also writes a sibling ``<stem>.latest.log`` truncated fresh on every
  process start, so "what did THIS launch do" is always the whole file
  with no rotation/offset math — the rotating log keeps cross-run
  history, the latest log isolates the current run;
* mirrors WARNING+ to stderr so terminal users see issues without
  digging into the log file;
* uses a single timestamped format every TRCC logger inherits.

Idempotent — calling ``configure_logging`` twice is safe (we tag our
handlers so the second call clears and re-installs them rather than
stacking duplicates).
"""
from __future__ import annotations

import inspect
import logging
import logging.handlers
import os
import sys
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from ...core.logs import PER_FRAME_ROOT, levels_for

if TYPE_CHECKING:
    from ...core.ports import Platform

log = logging.getLogger(__name__)

_HANDLER_TAG = "_trcc_handler"
# ``name`` is the module logger; ``classname`` is injected by
# ClassContextFilter; ``funcName``/``lineno`` come free on every record.
# Result per line: ``trcc.core.commands.device:SetBrightness.execute:208``
# — so any log line (including a bare error) pins the exact class +
# method + line that emitted it, with no per-method annotation.
_LOG_FORMAT = (
    "%(asctime)s %(levelname)-7s "
    "%(name)s:%(classname)s%(funcName)s:%(lineno)d: %(message)s"
)
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"


class RenderOnceRotatingFileHandler(RotatingFileHandler):
    """A rotating handler that renders each record ONCE.

    ``logging`` renders the same record four times in this app, and three of
    those are waste.  CPython's ``RotatingFileHandler.shouldRollover`` calls
    ``self.format(record)`` solely to take ``len()`` of the result and then
    discards it -- and that happens once per rotating handler, of which two are
    attached (the rolling history and the per-run ``latest``).  Each render
    re-runs ``strftime`` as well.  Counted, not inferred: four
    ``Formatter.format`` and four ``Formatter.formatTime`` calls per record.

    That was affordable when it was assumed to be a disk question.  Measured on
    the real device, logging is 82-90%% of the CPU regression since v9.9.2, so
    rendering the same string four times is not free.

    The rendered text is a function of (record, formatter) alone, so it is
    cached on the record and keyed by the formatter's IDENTITY -- a handler
    carrying a different formatter still renders its own text rather than
    inheriting someone else's.  Nothing about WHAT gets logged changes; only
    how many times it is turned into a string.

    Deliberately carries no log line: ``logging`` calls this while formatting a
    record, so emitting one here would recurse forever.  The coverage ratchet
    exempts it for that reason, qualified by this base class rather than by the
    method name.
    """

    _CACHE = "_trcc_rendered"

    def format(self, record: logging.LogRecord) -> str:
        cached = record.__dict__.get(self._CACHE)
        if cached is not None and cached[0] is self.formatter:
            return cached[1]
        text = super().format(record)
        record.__dict__[self._CACHE] = (self.formatter, text)
        return text


#: Sidecar holding the cross-process rollover lock for a log file.  Its
#: suffix is deliberately non-numeric so :func:`log_chain` never mistakes it
#: for a segment.
LOCK_SUFFIX = ".lock"


class _SharedRotatingFileHandler(RenderOnceRotatingFileHandler, ABC):
    """A rotating handler several PROCESSES may write to at once.

    The daemon flip makes concurrent writers permanent — the daemon owns USB
    and rendering while a CLI or GUI client dispatches into it, and both log to
    ``~/.trcc/trcc.log``.  ``RotatingFileHandler`` cannot survive that, because
    every writer rotates independently:

    * **It crashes.**  Two processes shift the chain at once, so one renames a
      file the other already moved.  ``FileNotFoundError`` inside ``emit``,
      reproduced on every multi-writer run (3–18 per run at 2–4 writers).
    * **It loses records.**  At a FIXED total volume, measured against a
      single-writer control: 1 writer keeps 47.8%, 2 keep 47.7%, **3 keep
      30.2%**.  Two writers cost nothing; the chain over-advances from three.
    * **It orphans writers.**  After a peer rotates, our stream still points at
      the renamed inode, so we keep appending to a file nothing reads.

    Two changes fix all three, and they share one syscall:

    1. ``emit`` runs under an advisory lock on a sidecar file, so rotation is
       serialised.  The lock is on a SIDECAR, not the log stream, because
       ``doRollover`` closes that stream — there would be no fd left to unlock.
    2. :meth:`shouldRollover` asks ``os.stat`` for the SHARED file rather than
       ``self.stream.tell()`` for our private position.  One stat answers both
       questions: the size every writer sees, and — via ``st_ino`` — whether
       the file we hold is still the file at ``baseFilename``.

    Measured on these classes, at a FIXED total volume, percentage of records
    surviving anywhere in the chain:

        writers      1       2       3       4     rollover crashes
        before    47.8%   47.7%   30.2%   30.2%          3-18
        after     47.8%   47.8%   47.7%   47.7%             0

    Three writers now retain exactly what one does — concurrency costs nothing
    — and the crash is gone.  (47.8% is not a defect: it is what a 6 MB chain
    does to 12 MB of records, single writer included.)

    The price is one ``stat`` replacing one ``tell()``, plus the lock:
    126,675 -> 137,900 instructions per record, **+8.9%** (P-cores pinned —
    ``cpu_atom`` reports pure extrapolation on a hybrid CPU — three replicates
    within 0.6%).  A leaner inline version measured +6.7%; the 2.2 points buy
    the two hooks below being the ONLY place each OS's primitive is named.
    Paying even that per FRAME would not be worth it, which is exactly why
    per-frame lines are off unless ``-vvv``.

    **Nothing under the lock may log.**  ``flock`` keeps no recursion count, so
    a log line inside ``emit`` / ``shouldRollover`` / ``doRollover`` re-enters
    ``emit``, whose ``finally`` drops the lock while a rotation is half-done.
    The coverage ratchet exempts exactly those methods for that reason.
    """

    #: May this handler hold the log file open BETWEEN records?
    #:
    #: On POSIX, yes — renaming a file someone else has open is an inode swap
    #: and simply works, which is what makes the lock sufficient there.
    #:
    #: On Windows it is not.  ``os.rename`` needs exclusive access to the
    #: source, and CPython's ``open(path, "a")`` does not request
    #: ``FILE_SHARE_DELETE``, so a PEER's open handle fails the rename with
    #: **WinError 32** — and serialising rotation does nothing about it,
    #: because the peer is not the process rotating.  The consequence is not a
    #: lost rotation but a dead log: ``RotatingFileHandler.emit`` catches the
    #: error, the file stays over ``maxBytes``, and every record afterwards
    #: takes the same path and is dropped.
    #:
    #: So Windows sets this ``False`` and closes after every write, which is
    #: what ``concurrent-log-handler`` — the reference implementation — does:
    #: its ``keep_file_open`` defaults ``True`` but "on Windows, the log file
    #: will always be closed after writes to allow for rotation".
    #:
    #: A plain attribute rather than a ``sys.platform`` test, so the policy is
    #: exercisable on any OS.  A Windows-only code path that only Windows can
    #: run is as unverifiable as the bug it fixes.
    _keep_open: ClassVar[bool] = True

    def __init__(self, filename: Path, **kwargs: object) -> None:
        if not self._keep_open:
            # Nothing should hold the file between records, including the gap
            # between construction and the first one.
            kwargs.setdefault("delay", True)
        # Both before super().__init__: FileHandler opens the stream during it,
        # which calls our _open, which needs these to already exist.
        self._inode: int | None = None
        # Kept as a Path so the per-record stat costs no object construction.
        self._base_path = Path(filename)
        self._lock_fd = os.open(
            f"{filename}{LOCK_SUFFIX}", os.O_CREAT | os.O_RDWR, 0o644,
        )
        super().__init__(filename, **kwargs)  # type: ignore[arg-type]
        # Prove the lock works HERE rather than discovering it per record on a
        # filesystem that cannot lock (some NFS mounts).  configure_logging
        # catches the OSError and falls back with a warning naming the cost.
        self._acquire()
        self._release()
        log.info("%s: sharing %s across processes (lock=%s%s)",
                 type(self).__name__, filename, filename, LOCK_SUFFIX)

    # ── The lock — one primitive, named by the OS that has it ───────────────
    #
    # Bound in the subclass constructor rather than imported at module scope:
    # ``fcntl`` does not exist on Windows and ``msvcrt`` does not exist
    # anywhere else, so a module-level import of either breaks the OTHER OS at
    # import time — and this module is the one that reports such failures.

    @abstractmethod
    def _acquire(self) -> None:
        """Take the cross-process lock.  Runs per record — must not log."""

    @abstractmethod
    def _release(self) -> None:
        """Drop the cross-process lock.  Runs per record — must not log."""

    @staticmethod
    @abstractmethod
    def _claim_run(fd: int) -> None:
        """Take an EXCLUSIVE NON-BLOCKING lock on *fd*, or raise ``OSError``.

        The other lifetime this class knows about: held for the whole process
        rather than for one record, and used by :class:`_RunOwnership` to ask
        "is a peer's session still live?".  Lives here so each OS's locking
        primitive is named in exactly one place.  Off the record path, so
        unlike ``_acquire`` it may log.
        """

    def emit(self, record: logging.LogRecord) -> None:
        # A closed handler stays closed.  ``close()`` does not detach, so a
        # closed-but-attached handler still receives records -- and stdlib
        # answers those by silently RE-OPENING its stream, which leaks the file
        # object (the suite's logging fixture catches it as a ResourceWarning)
        # and would write without the lock this class exists to hold.  Records
        # are not lost by dropping them here: in production ``close()`` only
        # follows ``removeHandler``, so the live handlers already took them.
        if self._lock_fd < 0:
            return
        # A logging call must never raise into application code.  Every stdlib
        # ``emit`` wraps its whole body and routes failure to ``handleError``;
        # the lock calls sat OUTSIDE that protection, so a lock that could not
        # be taken propagated out of ``log.info(...)`` into whatever called it.
        # Measured: ``BlockingIOError`` escaped a plain ``lg.info(...)``.
        #
        # Not hypothetical on Windows.  ``msvcrt.locking(LK_LOCK)`` retries at
        # one-second intervals, ten times, and then RAISES ``OSError`` — so
        # sustained contention turns an ordinary log line into an exception
        # about ten seconds in.  ``flock`` blocks indefinitely instead, which
        # is why POSIX never showed it.
        try:
            self._acquire()
            try:
                super().emit(record)
            finally:
                # Close BEFORE releasing, never after: the whole point is that
                # nobody holds a handle at the moment a peer may take the lock
                # and rename.  Releasing first would leave exactly the window
                # this exists to close.
                if not self._keep_open and self.stream is not None:
                    self.stream.close()
                    # typeshed types ``stream`` non-optional, but ``FileHandler``
                    # itself leaves it None under ``delay=True`` and CPython's own
                    # ``doRollover`` assigns None here.  The stub is wrong, not
                    # the assignment.
                    self.stream = None  # pyright: ignore[reportAttributeAccessIssue]
                self._release()
        except Exception:
            self.handleError(record)

    def _open(self):
        stream = super()._open()
        self._inode = os.fstat(stream.fileno()).st_ino
        return stream

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.stream is None:
            self.stream = self._open()
        try:
            stat = self._base_path.stat()
        except FileNotFoundError:
            # A peer rotated us away and nothing has replaced the file yet.
            return True
        if stat.st_ino != self._inode:
            # We are holding a segment a peer already renamed.  Not a size
            # rollover — doRollover re-opens instead of rotating again.
            return True
        if self.maxBytes <= 0 or not stat.st_size:
            return False        # gh-116263: never rollover an empty file
        return stat.st_size + len(f"{self.format(record)}\n") >= self.maxBytes

    def doRollover(self) -> None:
        # Runs under the lock.  A peer may have rotated while we waited for it,
        # in which case rotating AGAIN would shift the chain a second time for
        # one overflow — which is how three writers lost 17.6 points of
        # retention.  Re-open onto the current file instead.
        if self.stream:
            self.stream.close()
            self.stream = None  # pyright: ignore[reportAttributeAccessIssue]
        try:
            rotated_by_peer = self._base_path.stat().st_ino != self._inode
        except FileNotFoundError:
            rotated_by_peer = False
        if not rotated_by_peer:
            super().doRollover()
        if self.stream is None:
            self.stream = self._open()

    def close(self) -> None:
        """Release the sidecar fd as well as the stream.

        Off the record path, so this one logs.  It matters that it closes:
        ``configure_logging`` reconfigures ~141 times in one suite run, and an
        fd per reconfigure is the leak its handler-closing loop exists to stop.

        The fd is then marked spent, because ``close()`` does NOT detach a
        handler — and its own log line proves why that matters.  Closing two
        handlers in turn (which the suite's logging fixture does, before it
        restores ``root.handlers``) means the second one's message is emitted
        through the FIRST, already closed.  Stdlib survives that by silently
        re-opening its stream in ``emit``; a closed lock fd cannot re-open
        itself, so without the marker it raised ``OSError: Bad file
        descriptor`` from inside logging.  A resurrected handler therefore
        writes unlocked — the same tolerance stdlib already grants, and only
        reachable once someone has closed a handler they left attached.
        """
        log.info("%s.close: releasing %s%s",
                 type(self).__name__, self.baseFilename, LOCK_SUFFIX)
        if self._lock_fd >= 0:
            os.close(self._lock_fd)
            self._lock_fd = -1
        super().close()


class PosixSharedLogHandler(_SharedRotatingFileHandler):
    """Linux / macOS / BSD — ``fcntl.flock`` advisory locking."""

    def __init__(self, filename: Path, **kwargs: object) -> None:
        import fcntl
        log.debug("PosixSharedLogHandler: locking %s with fcntl.flock", filename)
        self._flock = fcntl.flock
        self._lock_ex = fcntl.LOCK_EX
        self._lock_un = fcntl.LOCK_UN
        super().__init__(filename, **kwargs)

    def _acquire(self) -> None:
        self._flock(self._lock_fd, self._lock_ex)

    def _release(self) -> None:
        self._flock(self._lock_fd, self._lock_un)

    @staticmethod
    def _claim_run(fd: int) -> None:
        import fcntl
        log.debug("PosixSharedLogHandler._claim_run: trying fd=%d", fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


class WindowsSharedLogHandler(_SharedRotatingFileHandler):
    """Windows — ``msvcrt.locking`` byte-range locking, file closed between writes.

    ``msvcrt`` locks a range starting at the CURRENT file position, so each
    call seeks to 0 first; one byte is enough, and every process locks the
    same one.  ``LK_LOCK`` blocks (retrying for ~10s) rather than failing, so
    it is the ``LOCK_EX`` equivalent.  Same primitive ``ipc._msvcrt_acquire``
    already uses for single-instance.

    Also the OS that cannot rotate a file a peer holds open — see
    :attr:`_SharedRotatingFileHandler._keep_open`.
    """

    _keep_open: ClassVar[bool] = False

    def __init__(self, filename: Path, **kwargs: object) -> None:
        import msvcrt  # pyright: ignore[reportMissingImports]
        self._locking = msvcrt.locking  # pyright: ignore[reportAttributeAccessIssue]
        self._lk_lock = msvcrt.LK_LOCK  # pyright: ignore[reportAttributeAccessIssue]
        self._lk_unlck = msvcrt.LK_UNLCK  # pyright: ignore[reportAttributeAccessIssue]
        log.debug("WindowsSharedLogHandler: locking %s with msvcrt.locking",
                  filename)
        super().__init__(filename, **kwargs)

    def _acquire(self) -> None:
        os.lseek(self._lock_fd, 0, os.SEEK_SET)
        self._locking(self._lock_fd, self._lk_lock, 1)

    def _release(self) -> None:
        os.lseek(self._lock_fd, 0, os.SEEK_SET)
        self._locking(self._lock_fd, self._lk_unlck, 1)

    @staticmethod
    def _claim_run(fd: int) -> None:
        import msvcrt  # pyright: ignore[reportMissingImports]
        log.debug("WindowsSharedLogHandler._claim_run: trying fd=%d", fd)
        os.lseek(fd, 0, os.SEEK_SET)
        # LK_NBLCK is the non-blocking exclusive lock; msvcrt has no shared
        # lock at all, which is why the run claim is exclusive-and-held rather
        # than a shared-lock probe.
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]


def shared_handler_class() -> type[_SharedRotatingFileHandler]:
    """The multi-process handler for this OS, chosen by CAPABILITY.

    Asks which lock primitive exists rather than which ``sys.platform`` string
    this is — the same shape ``ipc.SingleInstance`` uses for ``AF_UNIX``.
    """
    try:
        import fcntl  # noqa: F401
    except ImportError:
        log.info("shared_handler_class: no fcntl — using msvcrt locking")
        return WindowsSharedLogHandler
    log.info("shared_handler_class: using fcntl.flock")
    return PosixSharedLogHandler


#: Sidecar marking which process owns the CURRENT run.  Held for that
#: process's lifetime, unlike the per-record rollover lock.
RUN_SUFFIX = ".run"


class _RunOwnership:
    """Which process gets to say ``latest`` is ITS run.

    ``<stem>.latest.log`` answers "what did THIS launch do", and it earns that
    by being truncated at startup — it used to be append-only, so it
    accumulated days of launches and a log that looked like it described your
    current run could be showing you a previous one.  That was shipped as a
    user-visible fix.

    The daemon flip breaks the assumption underneath it: "this launch" stops
    being one process.  Measured with three concurrent writers, the file became
    a BLEND of three runs *and* lost the earlier ones mid-write — one lost
    1,501 of 9,000 records, another 3,356 — which is the stale-window confusion
    the fix was for, now with data loss on top.  Nothing caught it: the gate is
    sequential (configure, marker, configure, marker) and cannot see a peer.

    So the unit becomes the SESSION rather than the process.  The first live
    writer truncates and owns the file; anyone joining while it is still alive
    appends.  When the last one exits the OS drops the lock and the next launch
    owns a fresh run again.

    An EXCLUSIVE non-blocking lock held for the process lifetime, deliberately
    not a shared one: ``msvcrt.locking`` has no read lock, so a shared-lock
    probe would not port to Windows, while ``LK_NBLCK`` is exactly this.
    """

    def __init__(self) -> None:
        self._fd = -1
        log.debug("_RunOwnership: no run claimed yet")

    def claim(self, path: Path) -> bool:
        """True if this process now owns the run for *path*.

        Releases our own previous claim FIRST.  ``flock`` is per open file
        DESCRIPTION, not per process — verified rather than assumed: a second
        ``os.open`` of the same file in the same process is refused while the
        first fd holds the lock.  Since ``configure_logging`` may run again in
        one process (the suite does it ~141 times), claiming before releasing
        would make every call after the first believe a peer was live and stop
        truncating — silently retiring the per-run guarantee.
        """
        self.release()
        fd = os.open(path.with_name(f"{path.name}{RUN_SUFFIX}"),
                     os.O_CREAT | os.O_RDWR, 0o644)
        handler = shared_handler_class()
        try:
            handler._claim_run(fd)
        except OSError as e:
            os.close(fd)
            log.info("_RunOwnership.claim: %s is owned by a live peer (%s) — "
                     "appending to it rather than truncating a running "
                     "session's log", path, e)
            return False
        self._fd = fd
        log.info("_RunOwnership.claim: this process owns the run for %s", path)
        return True

    def release(self) -> None:
        """Drop the claim, if we hold one.  The OS drops it on exit anyway."""
        if self._fd < 0:
            log.debug("_RunOwnership.release: nothing held")
            return
        log.info("_RunOwnership.release: dropping the run claim")
        os.close(self._fd)
        self._fd = -1


#: One per process, because a process has exactly one run.
_RUN_OWNERSHIP = _RunOwnership()


def _make_file_handler(
    path: Path, *, max_bytes: int, backup_count: int,
) -> RenderOnceRotatingFileHandler:
    """A rotating handler for *path*, multi-process safe where it can be.

    Degrades LOUDLY, never silently: a filesystem that cannot lock (some NFS
    mounts) still gets a log, and the warning names what is lost so a report
    from one is read with the right expectations.
    """
    log.debug("_make_file_handler: %s (%d x %d)", path, max_bytes, backup_count)
    try:
        return shared_handler_class()(
            path, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError as e:
        log.warning(
            "_make_file_handler: cannot lock %s%s (%s) — falling back to an "
            "UNSHARED handler; if two TRCC processes run at once this log may "
            "lose records and its rotation may fail",
            path, LOCK_SUFFIX, e,
        )
        return RenderOnceRotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8",
        )


class ClassContextFilter(logging.Filter):
    """Inject the emitting method's class name into every record.

    Python's ``LogRecord`` carries ``module`` / ``funcName`` / ``lineno``
    for free but never the class.  This filter walks up to the frame
    that actually called the logger (matched by ``funcName`` +
    filename, exactly as ``logging`` itself locates the caller) and
    reads ``self`` / ``cls`` from its locals, exposing the class as
    ``record.classname`` (``"Class."`` or ``""`` for module-level
    functions).  The formatter prints ``Class.method:line`` on every
    line — the single-source alternative to annotating every method by
    hand.

    Cost is one short frame-walk per *emitted* record (records below the
    active level are never created, so disabled DEBUG costs nothing).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.classname = ""
        frame = inspect.currentframe()
        while frame is not None:
            code = frame.f_code
            if (code.co_name == record.funcName
                    and code.co_filename == record.pathname):
                obj = frame.f_locals.get("self")
                if obj is not None:
                    record.classname = f"{type(obj).__name__}."
                else:
                    cls = frame.f_locals.get("cls")
                    if isinstance(cls, type):
                        record.classname = f"{cls.__name__}."
                break
            frame = frame.f_back
        return True


def configure_logging(
    log_file: Path,
    *,
    level: int = logging.INFO,
    max_bytes: int = 1_000_000,
    backup_count: int = 5,
    latest_max_bytes: int = 10_000_000,
    stderr_level: int = logging.WARNING,
    per_frame: bool = False,
) -> None:
    """Wire the root logger to log_file + stderr.  Idempotent.

    Removing existing TRCC handlers first lets a daemon reconfigure
    logging mid-run (e.g. when log_file moves after a config reload)
    without piling up duplicate handlers.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Per-frame lines are gated as ONE family (see core.logs).  At INFO their
    # ``.debug()`` calls short-circuit in ``isEnabledFor``, so the LogRecord is
    # never constructed — that is where the saving is, not in the writing.
    # Everything else still keeps DEBUG in the file, which is what a report is
    # read for.  Set explicitly in both directions so a second call can turn
    # the firehose back off as well as on.
    #
    # WARNING, not INFO, when silenced.  INFO silences only DEBUG, so a future
    # ``frame_log.info(...)`` on a per-frame path would write a record EVERY
    # frame and nothing would catch it -- not the family, not
    # ``record_rate.py``, not the sensor-tick gate.  All 170 frame calls are
    # ``.debug`` today, which made that hole empty by luck rather than by
    # construction.  WARNING closes it while still letting a genuine per-frame
    # warning through: a warning on the frame path is a problem, not noise, and
    # the warn-once pattern (``BaselineSensors._read``) is what keeps one from
    # flooding.
    logging.getLogger(PER_FRAME_ROOT).setLevel(
        logging.DEBUG if per_frame else logging.WARNING,
    )

    # Drop any handlers we installed previously — leave foreign handlers
    # (pytest's capture handler, e.g.) untouched.
    #
    # CLOSED, not merely detached.  Removing a FileHandler from the logger
    # drops the last reference to it, and the OS file descriptor lives until
    # the GC finalises the object — surfacing as
    # ``PytestUnraisableExceptionWarning: Exception ignored in <_io.FileIO
    # name='.../trcc.log' mode='ab'>`` at whatever unrelated moment that
    # happens to be.  Every reconfigure leaked two descriptors (the rotating
    # log and ``latest``), which matters for the daemon this loop exists to
    # support: the comment above says it is here so a daemon can reconfigure
    # without piling up duplicate handlers.
    #
    # Safe for all three of ours, verified rather than assumed:
    # ``StreamHandler.close()`` does NOT close the underlying stream, so the
    # stderr handler leaves stderr writable; ``Handler.close()`` is idempotent,
    # so the test fixture that also closes them cannot double-free; and all
    # three are attached to ``root`` alone, so nothing else is still using one.
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()

    # Before ANY new handler is attached — see _detach_early_logging.
    early_records = _detach_early_logging(root)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    context_filter = ClassContextFilter()

    file_handler = _make_file_handler(
        log_file, max_bytes=max_bytes, backup_count=backup_count,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)
    setattr(file_handler, _HANDLER_TAG, True)
    root.addHandler(file_handler)

    # Per-run log: a SECOND file holding THIS run alone.
    # ``configure_logging`` runs once per process (CLI root callback /
    # launch entry point), so the truncate happens exactly once per app
    # init.  Still a RotatingFileHandler so a long-lived ``-v`` session
    # (video DEBUG can emit ~30–90 lines/s) can't grow the file without
    # bound — it rolls at ``latest_max_bytes`` keeping one backup (worst
    # case 2× the cap on disk).
    #
    # The truncate is done HERE, explicitly, and not via ``mode="w"``:
    # ``RotatingFileHandler`` SILENTLY discards the mode whenever
    # ``maxBytes > 0`` (CPython forces ``"a"`` so a rollover has something to
    # append to).  Passing ``mode="w"`` looked right and did nothing, so the
    # per-run file was append-only and spanned days.  Reading a stale window
    # as if it were the current run caused three separate misdiagnoses — the
    # file said what you expected because a PREVIOUS run had put it there.
    latest_file = log_file.with_name(f"{log_file.stem}.latest{log_file.suffix}")
    truncate_error: OSError | None = None
    latest_file.parent.mkdir(parents=True, exist_ok=True)
    # Truncate only if no LIVE peer is already writing this run.  Truncating
    # unconditionally is what the flip turns into a defect: a client starting
    # while the daemon runs would wipe the daemon's session out from under it,
    # leaving a file that is part one run and part another — the exact stale
    # window the per-run log was introduced to remove.
    if _RUN_OWNERSHIP.claim(latest_file):
        try:
            latest_file.write_bytes(b"")
        except OSError as e:  # read-only dir / permissions — keep logging
            truncate_error = e
    latest_handler = _make_file_handler(
        latest_file, max_bytes=latest_max_bytes, backup_count=1,
    )
    latest_handler.setLevel(level)
    latest_handler.setFormatter(formatter)
    latest_handler.addFilter(context_filter)
    setattr(latest_handler, _HANDLER_TAG, True)
    root.addHandler(latest_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(stderr_level)
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(context_filter)
    setattr(stderr_handler, _HANDLER_TAG, True)
    root.addHandler(stderr_handler)

    _replay_early_logging(root, early_records)
    log.info(
        "configure_logging: file=%s latest=%s level=%s rotate=%d×%d stderr=%s",
        log_file, latest_file, logging.getLevelName(level),
        max_bytes, backup_count, logging.getLevelName(stderr_level),
    )
    # Do NOT spell the rung here as a literal.  It said "one -v enables it"
    # long after the ladder moved the frame path to -vvv, so the one line a
    # reporter reads to find the flag named the wrong flag.  ``levels_for`` is
    # the single definition; ask it which rung is the first to turn this on.
    log.info("configure_logging: per-frame logging %s (%s enables it)",
             "ON" if per_frame else "OFF",
             "-" + "v" * next(v for v in range(10) if levels_for(v).per_frame))
    if truncate_error is not None:
        # Deliberately loud: a latest-log that still holds a previous run is
        # exactly what makes a diagnosis read the wrong window.
        log.warning(
            "configure_logging: could not truncate %s (%s) — it still holds "
            "EARLIER runs; check timestamps before trusting any line in it",
            latest_file, truncate_error,
        )


def log_chain(log_file: Path) -> list[Path]:
    """Every segment of *log_file*'s rotation set, OLDEST first.

    ``RotatingFileHandler`` renames ``trcc.log`` -> ``.1`` -> ``.2`` ... so a
    HIGHER suffix is OLDER and the live file is last.  Reading the chain rather
    than the head is the difference between a report that describes the session
    and one that describes whatever happened since the last rotation: measured
    on a single writer, right after a rollover, 5,300 significant records
    emitted gave ``tail_log(1000)`` -> **423 lines** and
    ``tail_log_actions(500)`` -> **424**, with the other 4,876 sitting unread in
    ``trcc.log.1``.  Concurrent writers make it worse — records scatter across
    the chain, so a reader of the head alone sees one process and silently omits
    the rest.

    Only NUMERIC suffixes are segments.  The sidecar lock file
    (``trcc.log.lock``) shares the prefix and is deliberately not log content.
    """
    log.info("log_chain: base=%s", log_file)
    backups: list[tuple[int, Path]] = []
    try:
        for sibling in log_file.parent.iterdir():
            suffix = sibling.name[len(log_file.name) + 1:]
            if sibling.name.startswith(f"{log_file.name}.") and suffix.isdigit():
                backups.append((int(suffix), sibling))
    except OSError as e:
        log.warning("log_chain: cannot list %s — %s", log_file.parent, e)
    # Highest suffix first == oldest first; the live file closes the chain.
    chain = [path for _n, path in sorted(backups, reverse=True)]
    if log_file.is_file():
        chain.append(log_file)
    log.debug("log_chain: %d segment(s) — %s",
              len(chain), [p.name for p in chain])
    return chain


def _stream_chain(log_file: Path) -> Iterator[tuple[bool, str]]:
    """Every line of the rotation set, oldest first, as ``(is_first, line)``.

    ``is_first`` marks the first line of a segment so a caller tracking
    multi-line records can reset its state at the boundary: one ``emit`` writes
    a whole record (traceback included) in a single call and rollover happens
    between records, so a record never spans two segments.
    """
    log.debug("_stream_chain: base=%s", log_file)
    for segment in log_chain(log_file):
        first = True
        try:
            with segment.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    yield first, raw.rstrip("\n")
                    first = False
        except OSError as e:
            log.warning("_stream_chain: could not read %s — %s", segment, e)


def tail_log(log_file: Path, n_lines: int = 1000) -> list[str]:
    """Return the last *n_lines* of *log_file*'s rotation set.

    Streams the chain with a bounded ``deque`` rather than reading it whole:
    the set is up to ``max_bytes x (backup_count + 1)`` — 6 MB at the shipped
    1 MB x 5 — so slicing a single ``read_text`` would hold all of it in memory
    to keep a thousand lines.  Same shape as :func:`tail_log_actions`.
    """
    log.info("tail_log: file=%s n_lines=%d", log_file, n_lines)
    kept: deque[str] = deque(maxlen=n_lines)
    for _first, line in _stream_chain(log_file):
        kept.append(line)
    log.info("tail_log: kept %d line(s)", len(kept))
    return list(kept)


# Every level name our own formatter can emit, and the subset worth keeping
# as an action history.  ``_LOG_FORMAT`` puts the level second, so a line
# whose second token is one of these is a record START; anything else is a
# continuation (a traceback body) belonging to the record above it.
_ALL_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_SIGNIFICANT_LEVELS = frozenset({"INFO", "WARNING", "ERROR", "CRITICAL"})


def _log_level_of(line: str) -> str | None:
    """The level name of *line*, or None when it is a continuation line."""
    parts = line.split(maxsplit=2)
    if len(parts) >= 2 and parts[1] in _ALL_LEVELS:
        return parts[1]
    return None


def tail_log_actions(log_file: Path, n_lines: int = 500) -> list[str]:
    """The last *n_lines* non-DEBUG records from the WHOLE log.

    ``tail_log`` selects by recency, which is the wrong axis for a bug
    report.  Every function in this project logs, so one rendered frame
    costs ~43 DEBUG lines and one overlay click ~125 — meaning a 1000-line
    tail remembers roughly twenty frames.  A reporter who hits a problem and
    then keeps using the app for another minute sends us a file where what
    they DID has already scrolled out, and we spend a round trip asking them
    to do it again.

    So this selects by significance instead: the user-action lines
    (INFO), the silent-skip warnings and the failures, however far back they
    are.  Tracebacks are kept with the record they belong to — an
    ``ERROR`` whose stack was dropped is the half of the answer that
    matters least.

    Streams the whole rotation set (see :func:`log_chain`) with a bounded
    ``deque`` rather than reading it whole, so it stays flat in memory across
    a 6 MB chain.  Reading the head alone was what capped its reach: the
    docstring above promises tens of thousands of lines back, and a rollover
    silently cut that to whatever the live segment held — measured at **424 of
    5,300** significant records.  Same shape as ``_scrape_handshake_lines``,
    which solved this for one line type after the tail window ate it.

    The default is deliberately HALF the tail's budget, not a tenth.  The
    ratio of DEBUG to significant lines swings enormously with what the app
    is doing — measured on two real logs, one was 980 DEBUG / 20 INFO per
    thousand and the other 562 / 438.  A small window is a clear win against
    the first and a REGRESSION against the second, handing the reporter
    fewer significant lines than the plain tail already contained.  500
    keeps this section at least comparable to the tail's significant content
    in the worst case, while still reaching tens of thousands of raw lines
    back in the render-loop case, and keeps the pasted report a sane size.
    """
    log.info("tail_log_actions: file=%s n_lines=%d", log_file, n_lines)
    kept: deque[str] = deque(maxlen=n_lines)
    keeping_record = False
    for first_of_segment, line in _stream_chain(log_file):
        if first_of_segment:
            # A record never spans a rollover, so nothing carries across.
            keeping_record = False
        level = _log_level_of(line)
        if level is None:
            # Continuation — rides along iff its record was kept.
            if keeping_record:
                kept.append(line)
            continue
        keeping_record = level in _SIGNIFICANT_LEVELS
        if keeping_record:
            kept.append(line)
    log.info("tail_log_actions: kept %d significant line(s)", len(kept))
    return list(kept)


#: Where logging goes when the platform layer cannot say — see
#: :func:`ensure_configured`.  Correct on Linux and BSD, wrong on macOS and
#: Windows, and named ONCE so no entry point has to guess it a second time.
LAST_RESORT_LOG = Path.home() / ".trcc" / "trcc.log"

#: Marks the pre-configuration buffer.  Deliberately NOT ``_HANDLER_TAG``:
#: ``ensure_configured`` no-ops when it sees a tagged handler, so tagging the
#: buffer would convince ``trcc-gui`` and ``trcc-lcd`` that logging was already
#: set up and leave them with no file handler at all — the exact defect
#: ``ensure_configured`` was written to fix.
_EARLY_TAG = "_trcc_early"

#: Startup emits a handful of records, not thousands.  A cap only matters
#: because ``MemoryHandler`` flushes to its target when full, and ours has no
#: target — a flush would DISCARD.  Generous enough never to reach it.
_EARLY_CAPACITY = 10_000


def start_early_logging() -> bool:
    """Buffer log records until the real destination is known.  Idempotent.

    Startup crashes are the ones we can least afford to lose: an import that
    fails, a DI error, a platform that will not initialise.  They happen BEFORE
    anything knows where the log file is, because that answer lives behind
    ``adapters.system`` — 48 modules and pyusb / psutil / pynvml.

    ``__main__.py`` used to solve this by installing its OWN
    ``RotatingFileHandler`` on a hardcoded path before importing anything.  It
    is the same fact written twice, and both copies had drifted:

    * the path was ``~/.trcc/trcc.log`` on every OS, while the report reads
      ``%APPDATA%\\trcc`` on Windows and ``~/Library/.../Logs`` on macOS — and
      BOTH frozen builds run this file, so every shipped Windows and macOS
      binary wrote its startup records where nothing would ever read them;
    * the format used ``'%Y-%m-%d %H:%M:%S'`` and ``[LEVEL]``, so
      ``_log_level_of`` read the time-of-day as the level and classified every
      early record as a CONTINUATION line.  ``tail_log_actions`` returned **0
      lines** for a startup ``CRITICAL`` — the one record the shim existed to
      produce;
    * ``backupCount=3`` against ``configure_logging``'s 5 left ``.4`` and
      ``.5`` frozen forever once the shim owned a rotation, stranding a third
      of the history.

    Buffering instead of writing fixes all three at once, because the records
    are only ever rendered by the REAL handlers.  The cost is that a hard crash
    — a segfault, not an exception — loses the buffer where a file would have
    kept it.  On macOS and Windows that file was unreadable by the report
    anyway; on Linux it is a genuine trade, taken because an exception is the
    shape every startup failure in this project's history has actually had.
    """
    root = logging.getLogger()
    if any(getattr(h, _EARLY_TAG, False) for h in root.handlers):
        log.debug("start_early_logging: already buffering")
        return False
    buffer = logging.handlers.MemoryHandler(
        capacity=_EARLY_CAPACITY, flushLevel=logging.CRITICAL + 1,
    )
    setattr(buffer, _EARLY_TAG, True)
    root.setLevel(logging.DEBUG)
    root.addHandler(buffer)
    log.debug("start_early_logging: buffering until the log path is known")
    return True


def _detach_early_logging(root: logging.Logger) -> list[logging.LogRecord]:
    """Take the buffered startup records and remove the buffer.

    Detaching FIRST is what keeps the replay honest.  Left attached while
    ``configure_logging`` installs the real handlers, it goes on collecting —
    so every record the configuration itself emits after the first handler is
    added lands in the file directly AND is replayed afterwards.  Measured: the
    ``shared_handler_class`` line appeared **5 times** in a 51-line log, and the
    file opened with the configuration banner ahead of the startup it describes.
    """
    records: list[logging.LogRecord] = []
    for buffer in [h for h in root.handlers if getattr(h, _EARLY_TAG, False)]:
        root.removeHandler(buffer)
        held = getattr(buffer, "buffer", [])
        records.extend(held)
        held.clear()
        buffer.close()
    log.debug("_detach_early_logging: took %d buffered record(s)", len(records))
    return records


def _replay_early_logging(
    root: logging.Logger, records: list[logging.LogRecord],
) -> None:
    """Write the startup records through the now-real handlers, oldest first."""
    if not records:
        return
    log.debug("_replay_early_logging: replaying %d record(s)", len(records))
    for record in records:
        for handler in root.handlers:
            if record.levelno >= handler.level:
                handler.handle(record)


def ensure_configured(verbose: int = 0, *, force: bool = False,
                      platform: Platform | None = None) -> bool:
    """Configure logging unless this process already did.  Returns True if it did.

    The CLI root callback owns verbosity and always configures (``force``).
    Everything else calls this bare, and the guard is what makes that safe:
    a second ``configure_logging`` with ``verbose=0`` would silently downgrade
    a user's ``-v`` back to the default, which is why CLAUDE.md forbids launch
    entry points from calling it directly.

    It exists because that file also said *"none exist today"* — and two did.
    ``trcc-gui`` and ``trcc-lcd`` are console-script entry points bound
    straight to the typer command (``pyproject.toml`` ``[project.scripts]``),
    so they never run the root callback.  Measured before this landed: at the
    moment the GUI actually started, the root logger had **0 handlers, level
    WARNING, and no file** — a whole launch path that produced no diagnostics
    at all, and a ``trcc report`` with nothing in it.

    *platform* is where the log lives: the one the caller builds its App on.
    ``None`` is the host's, which is every production caller.  The CLI passes
    its override, so ``dev/mock_cli.py`` logs into the mock's tree -- it used to
    write the user's real ``~/.trcc`` log, and truncate ``latest`` when no
    TRCC was running.
    """
    root = logging.getLogger()
    if not force and any(getattr(h, _HANDLER_TAG, False) for h in root.handlers):
        log.debug("ensure_configured: already configured — leaving it alone")
        return False

    from ...core.logs import levels_for

    # Asking the platform where the log goes is itself importable code, and the
    # startup crashes this whole path exists to record are exactly the ones
    # that break it (``adapters.system`` pulls in pyusb, psutil and pynvml).
    # Losing the diagnosis because the diagnostic machinery shares the broken
    # import is the one outcome worth guarding against.
    platform_error: Exception | None = None
    try:
        if platform is None:
            from ..system import current_platform
            platform = current_platform()
        log.debug("ensure_configured: logging for %s", type(platform).__name__)
        # Windows consoles default to cp1252 and crash on non-ASCII log output
        # — wrap stdout/stderr UTF-8 BEFORE the StreamHandler is attached.
        platform.configure_stdout()
        log_path = platform.paths().log_file()
    except Exception as e:          # logging must never be what fails
        platform_error = e
        log_path = LAST_RESORT_LOG

    levels = levels_for(verbose)
    configure_logging(
        log_path,
        level=levels.file,
        stderr_level=levels.terminal,
        per_frame=levels.per_frame,
    )
    if platform_error is not None:
        log.warning(
            "ensure_configured: could not ask the platform where the log "
            "lives (%s) — falling back to %s, which is this OS's real "
            "location only on Linux and BSD.  On macOS and Windows the "
            "report reads a different path, so quote this one when asking "
            "for the file.", platform_error, LAST_RESORT_LOG,
        )
    return True
