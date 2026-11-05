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
import re
import site
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _import_paths() -> list[str]:
    """Every dir ``trcc`` might be importable from, for the root re-exec."""
    log.debug("_import_paths")
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
    try:
        result = subprocess.run(["sudo", sys.executable, "-c", code], check=False)
    except (OSError, subprocess.SubprocessError):
        log.exception("reexec_as_root: sudo re-exec failed")
        return 1
    return result.returncode


#: An rpm macro written as the shell would expand it, as RPM Fusion's own
#: instructions spell it: ``$(rpm -E %fedora)``.  Steps run without a shell,
#: so the runner expands it by asking rpm.
_RPM_MACRO = re.compile(r"\$\(rpm -E (%\w+)\)")


def _rpm_macro(found: re.Match[str]) -> str:
    """``rpm -E %fedora`` -> ``44``; the text left as it was if rpm cannot say."""
    try:
        ran = subprocess.run(["rpm", "-E", found.group(1)], capture_output=True,
                             text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("_rpm_macro: could not ask rpm for %s (%s)", found.group(1), e)
        return found.group(0)
    value = ran.stdout.strip()
    log.debug("_rpm_macro: %s -> %r", found.group(1), value)
    return value or found.group(0)


def run_steps(steps: tuple[tuple[str, ...], ...]) -> int:
    """Run *steps* in order, each an argument list -- no shell -- with the
    terminal, so ``sudo`` and the package manager can ask; the first non-zero
    exit stops them and is returned, 0 when all ran.  Already root (the
    install script runs setup as root), a leading ``sudo`` is dropped."""
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    for step in steps:
        argv = [_RPM_MACRO.sub(_rpm_macro, arg) for arg in step]
        if root and argv[:1] == ["sudo"]:
            argv = argv[1:]
        log.info("run_steps: %s", " ".join(argv))
        try:
            code = subprocess.run(argv, check=False).returncode
        except OSError as e:
            log.warning("run_steps: could not run %s (%s)", argv[0], e)
            return 127
        if code:
            log.warning("run_steps: %s exited %d -- the rest are not run", argv[0], code)
            return code
    log.info("run_steps: %d step(s) done", len(steps))
    return 0

