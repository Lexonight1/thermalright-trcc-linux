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
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
) -> None:
    """Wire the root logger to log_file + stderr.  Idempotent.

    Removing existing TRCC handlers first lets a daemon reconfigure
    logging mid-run (e.g. when log_file moves after a config reload)
    without piling up duplicate handlers.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Drop any handlers we installed previously — leave foreign handlers
    # (pytest's capture handler, e.g.) untouched.
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    context_filter = ClassContextFilter()

    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
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
    try:
        latest_file.parent.mkdir(parents=True, exist_ok=True)
        latest_file.write_bytes(b"")
    except OSError as e:      # read-only dir / permissions — keep logging
        truncate_error = e
    latest_handler = RotatingFileHandler(
        latest_file, maxBytes=latest_max_bytes, backupCount=1,
        encoding="utf-8",
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

    log.info(
        "configure_logging: file=%s latest=%s level=%s rotate=%d×%d stderr=%s",
        log_file, latest_file, logging.getLevelName(level),
        max_bytes, backup_count, logging.getLevelName(stderr_level),
    )
    if truncate_error is not None:
        # Deliberately loud: a latest-log that still holds a previous run is
        # exactly what makes a diagnosis read the wrong window.
        log.warning(
            "configure_logging: could not truncate %s (%s) — it still holds "
            "EARLIER runs; check timestamps before trusting any line in it",
            latest_file, truncate_error,
        )


def tail_log(log_file: Path, n_lines: int = 1000) -> list[str]:
    """Return the last *n_lines* of *log_file* (or fewer if smaller).

    Lightweight implementation — reads the whole file then slices.
    Acceptable because log rotation caps the file at ~1 MB and we only
    ever call this when generating a debug report.
    """
    log.info("tail_log: file=%s n_lines=%d", log_file, n_lines)
    if not log_file.is_file():
        return []
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-n_lines:]


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

    Streams the file with a bounded ``deque`` rather than reading it whole,
    so it stays flat in memory on a rotated 10 MB ``latest`` log.  Same
    shape as ``_scrape_handshake_lines``, which solved this for one line
    type after the tail window ate it.

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
    if not log_file.is_file():
        log.debug("tail_log_actions: %s does not exist", log_file)
        return []
    kept: deque[str] = deque(maxlen=n_lines)
    keeping_record = False
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                level = _log_level_of(line)
                if level is None:
                    # Continuation — rides along iff its record was kept.
                    if keeping_record:
                        kept.append(line)
                    continue
                keeping_record = level in _SIGNIFICANT_LEVELS
                if keeping_record:
                    kept.append(line)
    except OSError as e:
        log.warning("tail_log_actions: could not read %s — %s", log_file, e)
        return []
    log.info("tail_log_actions: kept %d significant line(s)", len(kept))
    return list(kept)
