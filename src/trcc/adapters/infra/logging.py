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
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger(__name__)

_HANDLER_TAG = "_trcc_next_handler"
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

    # Per-run log: a SECOND file truncated fresh on open (mode="w").
    # ``configure_logging`` runs once per process (CLI root callback /
    # launch entry point), so the truncate happens exactly once per app
    # init and the file holds this run alone.  Still a RotatingFileHandler
    # so a long-lived ``-v`` session (video DEBUG can emit ~30–90 lines/s)
    # can't grow the file without bound — it rolls at ``latest_max_bytes``
    # keeping one backup (worst case 2× the cap on disk).
    latest_file = log_file.with_name(f"{log_file.stem}.latest{log_file.suffix}")
    latest_handler = RotatingFileHandler(
        latest_file, mode="w", maxBytes=latest_max_bytes, backupCount=1,
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
