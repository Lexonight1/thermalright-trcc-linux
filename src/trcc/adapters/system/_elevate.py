"""Re-exec a setup snippet as root via sudo, with ``trcc`` importable.

``pip install --user`` puts ``trcc`` in the *user's* site-packages, which
root's interpreter doesn't see — so a bare ``sudo python -c "import trcc"``
dies with ``ModuleNotFoundError`` (the failure seen running ``trcc system
setup`` after a ``--user`` install).  We inject every site-packages dir + the
``trcc`` package root onto ``sys.path`` *inside* the ``-c`` snippet, so the
elevated interpreter imports ``trcc`` regardless of how it was installed
(``--user`` / venv / system / source checkout).  Injecting the path in-process
is more robust than a ``PYTHONPATH=`` env var, which sudo's ``env_reset`` /
``secure_path`` can strip.  (Same shape as legacy ``linux_platform.sudo_reexec``.)
"""
from __future__ import annotations

import logging
import os
import site
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _import_paths() -> list[str]:
    """Every dir ``trcc`` might be importable from, for the root re-exec."""
    paths: list[str] = list(site.getsitepackages())
    paths.append(site.getusersitepackages())
    # .../trcc/adapters/system/_elevate.py → parents[3] holds the `trcc` package
    # (the site-packages dir, or `src/` in a source checkout).
    paths.append(str(Path(__file__).resolve().parents[3]))
    return paths


def reexec_as_root(snippet: str) -> int:
    """Run ``snippet`` (a ``python -c`` body) as root via sudo, ``trcc`` on path.

    ``snippet`` may reference ``sys`` (already imported).  Returns the child's
    exit code, or 1 if sudo couldn't be spawned.
    """
    paths = _import_paths()
    code = f"import sys; sys.path[:0] = {paths!r}; {snippet}"
    log.info("reexec_as_root: re-running as root via sudo")
    fd: int = -1
    tmp_path: str = ""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".py")
        os.write(fd, code.encode("utf-8"))
        os.close(fd)
        fd = -1
        result = subprocess.run(["sudo", sys.executable, tmp_path], check=False)
    except (OSError, subprocess.SubprocessError):
        log.exception("reexec_as_root: sudo re-exec failed")
        return 1
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return result.returncode
