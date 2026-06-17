"""SELinux policy install — load the ``trcc_usb`` module so the bulk/SCSI USB
ioctls aren't blocked on enforcing systems.

RPM packages compile + load this in their ``%post`` (the compiled ``.pp`` is
shipped).  This module is the source/pip path that ``trcc system setup`` runs —
it builds the module from the bundled ``trcc_usb.te`` (needs ``checkpolicy``) and
loads it with ``semodule``.  It's a NO-OP off SELinux, and mirrors
``_udev.install``'s root re-exec (needs root → re-runs itself via ``sudo``).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

log = logging.getLogger(__name__)

_MODULE = "trcc_usb"
# ``/sys/fs/selinux`` is mounted only when SELinux is present on the system.
_SELINUX_FS = Path("/sys/fs/selinux")


def _selinux_active() -> bool:
    """True only on a system actually running SELinux (rules would matter)."""
    return _SELINUX_FS.is_dir() and shutil.which("semodule") is not None


def _already_loaded() -> bool:
    try:
        out = subprocess.run(
            ["semodule", "-l"], capture_output=True, text=True,
            timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return _MODULE in out.stdout.split()


def install(dry_run: bool = False) -> int:
    """Build + load the ``trcc_usb`` SELinux module.  Returns 0 on success or
    no-op, non-zero on a real failure.  Requires root; re-execs via sudo when
    not root.  No-op off SELinux."""
    log.info("selinux.install: dry_run=%s", dry_run)
    if not _selinux_active():
        log.info("selinux.install: SELinux not active (no /sys/fs/selinux or "
                 "semodule) — skipping")
        return 0
    if _already_loaded():
        log.info("selinux.install: %s already loaded — nothing to do", _MODULE)
        return 0

    te = files("trcc.data") / "trcc_usb.te"
    if not te.is_file():
        log.warning("selinux.install: trcc/data/trcc_usb.te not bundled — "
                    "cannot build the policy (install the trcc-linux RPM, which "
                    "ships the compiled module)")
        return 0

    if dry_run:
        print(f"--- would build + load SELinux module '{_MODULE}' "
              "from trcc_usb.te ---")
        return 0

    if os.geteuid() != 0:
        return _sudo_reexec()

    if not (shutil.which("checkmodule") and shutil.which("semodule_package")):
        log.warning("selinux.install: checkpolicy/policycoreutils not installed "
                    "— cannot build the SELinux module.  Install them (e.g. "
                    "'dnf install checkpolicy policycoreutils'), or use the "
                    "trcc-linux RPM (loads it automatically).")
        return 1

    te_bytes = te.read_bytes()
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "trcc_usb.te").write_bytes(te_bytes)
        try:
            subprocess.run(
                ["checkmodule", "-M", "-m", "-o",
                 str(dp / "trcc_usb.mod"), str(dp / "trcc_usb.te")],
                check=True,
            )
            subprocess.run(
                ["semodule_package", "-o",
                 str(dp / "trcc_usb.pp"), "-m", str(dp / "trcc_usb.mod")],
                check=True,
            )
            subprocess.run(
                ["semodule", "-i", str(dp / "trcc_usb.pp")], check=True,
            )
        except (OSError, subprocess.SubprocessError):
            log.exception("selinux.install: failed to build/load %s", _MODULE)
            return 1

    log.info("selinux.install: loaded the %s SELinux module", _MODULE)
    return 0


def _sudo_reexec() -> int:
    """Re-run as root via sudo to load the policy (mirrors _udev._sudo_reexec).

    Uses ``python -c`` so it doesn't depend on a console script being on root's
    PATH.
    """
    log.info("selinux._sudo_reexec: re-running as root via sudo to load %s",
             _MODULE)
    code = (
        "from trcc.adapters.system._selinux import install; "
        "import sys; sys.exit(install())"
    )
    try:
        result = subprocess.run(
            ["sudo", sys.executable, "-c", code], check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.exception("selinux._sudo_reexec: sudo re-exec failed")
        return 1
    return result.returncode
