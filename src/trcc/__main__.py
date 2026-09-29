#!/usr/bin/env python3
"""Allow running as: python -m trcc

Sets up crash logging BEFORE any imports — ensures every OS gets
a log file at ~/.trcc/trcc.log even if the app crashes on startup.
"""

import logging
import os
import sys
from pathlib import Path

# Early logging — catches import failures, DI errors, platform issues.
#
# This BUFFERS; it does not open a file.  Where the log lives is a question
# only the platform layer can answer, and that layer is 48 modules including
# pyusb, psutil and pynvml — the very imports whose failure this exists to
# record.  So the records are held until ``configure_logging`` knows the real
# destination, and replayed through the real handlers.
#
# It used to install its own RotatingFileHandler on a hardcoded path, which is
# the same fact written twice.  Both copies had drifted: the path was
# ``~/.trcc/trcc.log`` on every OS while the report reads ``%APPDATA%\trcc``
# on Windows and ``~/Library/.../Logs`` on macOS — and BOTH frozen builds are
# entry-pointed at THIS file, so every shipped Windows and macOS binary wrote
# its startup records where nothing would read them.  The format drifted too:
# ``tail_log_actions`` returned zero lines for a startup CRITICAL, because a
# space-separated date and a bracketed level made every early record parse as a
# continuation.  See ``start_early_logging``.
from trcc.adapters.infra.logging import ensure_configured, start_early_logging

start_early_logging()
log = logging.getLogger('trcc.main')
log.info("Starting TRCC — platform=%s, executable=%s", sys.platform, sys.executable)

# Windows: stdout/stderr default to cp1252, which can't encode common
# Unicode chars used in log messages (e.g. ``→``, ``°``).  ``reconfigure``
# is a silent no-op on some Python deployments (Windows Store), so on
# top of that we monkey-patch ``logging.StreamHandler.emit`` to wrap the
# write call in its own ``except UnicodeEncodeError`` that re-encodes
# with ``errors='replace'``.  This catches EVERY StreamHandler — ours,
# stdlib lastResort, third-party — at the class level, no matter when
# or where they're instantiated.
if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            try:
                _stream.reconfigure(encoding='utf-8', errors='replace')
            except (AttributeError, OSError, ValueError):
                pass

    # No log line in here, ever: this IS ``emit`` for every handler, the file
    # handlers included.  One (e078aadd) re-entered the handler holding the
    # msvcrt lock — ~9 s per record on Windows, v9.10.0 through v9.10.4.
    # ``tests/test_logging_coverage.py::test_nothing_on_the_record_path_logs``.
    def _safe_stream_emit(self: logging.StreamHandler, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                encoding = getattr(stream, 'encoding', 'ascii') or 'ascii'
                safe = msg.encode(encoding, errors='replace').decode(encoding)
                stream.write(safe + self.terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)

    logging.StreamHandler.emit = _safe_stream_emit  # type: ignore[method-assign]

# Windows: ensure libusb-1.0.dll is findable by pyusb (ctypes).
# PyInstaller bundles the DLL next to the exe, but Python 3.8+ on Windows
# doesn't search the exe's directory for ctypes DLLs unless explicitly told.
# Without this, pyusb raises ``NoBackendError: No backend available``.
if sys.platform == 'win32':
    _app_dir = Path(sys.executable).parent
    try:
        os.add_dll_directory(str(_app_dir))
        log.debug("Added DLL search directory: %s", _app_dir)
    except (OSError, AttributeError):
        pass  # add_dll_directory requires Python 3.8+ and a valid dir

try:
    # trcc-gui.exe is the WINDOWED twin of trcc.exe: same CLI, ``gui`` when no
    # subcommand is given.  It used to call ``gui()`` directly, which dropped
    # every argument — so the sign-in task's ``--resume`` could never reach it
    # — and skipped the shared entry's bundled-tools PATH step.
    if Path(sys.executable).name.lower().startswith('trcc-gui') and (
            len(sys.argv) < 2 or sys.argv[1].startswith('-')):
        sys.argv.insert(1, 'gui')
        log.info("trcc-gui: no subcommand — defaulting to gui (argv=%s)",
                 sys.argv[1:])
    # Everything goes through the shared entry so python -m trcc, the `trcc`
    # console script and both frozen exes dispatch the same way.
    from trcc._entry import main
    sys.exit(main() or 0)
except Exception:
    log.critical("Fatal startup error", exc_info=True)
    # The buffer still holds this CRITICAL and everything before it, and
    # nothing has written a file yet — the crash is why.  Configure now so the
    # records reach disk; ``ensure_configured`` falls back to a named path if
    # asking the platform is itself what broke.
    try:
        ensure_configured()
    except Exception:
        log.exception("Fatal startup error: could not write the log either")
    raise
