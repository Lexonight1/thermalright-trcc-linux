"""Structured logging setup — rotating file + stderr.

Replaces legacy ``adapters/infra/diagnostics.py``'s 200-line logging
block with a focused configurator that:

* writes to ``Paths.log_file()`` with rotation at 1 MB × 5 backups so
  long-lived daemons don't fill the disk;
* mirrors WARNING+ to stderr so terminal users see issues without
  digging into the log file;
* uses a single timestamped format every TRCC logger inherits.

Idempotent — calling ``configure_logging`` twice is safe (we tag our
handlers so the second call clears and re-installs them rather than
stacking duplicates).
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

log = logging.getLogger(__name__)

_HANDLER_TAG = "_trcc_next_handler"
_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(
    log_file: Path,
    *,
    level: int = logging.INFO,
    max_bytes: int = 1_000_000,
    backup_count: int = 5,
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

    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_TAG, True)
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(stderr_level)
    stderr_handler.setFormatter(formatter)
    setattr(stderr_handler, _HANDLER_TAG, True)
    root.addHandler(stderr_handler)

    log.info(
        "configure_logging: file=%s level=%s rotate=%d×%d stderr=%s",
        log_file, logging.getLevelName(level), max_bytes, backup_count,
        logging.getLevelName(stderr_level),
    )


def tail_log(log_file: Path, n_lines: int = 1000) -> list[str]:
    """Return the last *n_lines* of *log_file* (or fewer if smaller).

    Lightweight implementation — reads the whole file then slices.
    Acceptable because log rotation caps the file at ~1 MB and we only
    ever call this when generating a debug report.
    """
    if not log_file.is_file():
        return []
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-n_lines:]
