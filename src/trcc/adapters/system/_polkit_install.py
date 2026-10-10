"""TRCC's polkit policy and RAM-lighting helpers, for an install no package made.

The distro packages install ``/usr/bin/trcc-ram-access``,
``/usr/bin/trcc-ram-access-off`` and TRCC's polkit policy.  A pip, pipx or
source install has none of them, so its RAM-lighting password prompt was
polkit's generic "run python as administrator" dialog -- the whole command
line, some 700 px wide -- and turning off asked for a password too.
``trcc system setup`` installs the same three files from the package, so every
install gets TRCC's own short prompt and a password-free turn-off.

Only those three.  The memory-info helpers (``trcc-dmi`` / ``trcc-imc``) stay
package-only: their actions read as root with no password, and a pip install
gets no silent privileged path.  The policy still names them; with no file at
those paths, those actions run nothing.

A file a distro package owns is never overwritten -- the package keeps it.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from ._elevate import reexec_as_root
from ._ram_access import HELPER, HELPER_OFF

log = logging.getLogger(__name__)

POLKIT_POLICY = Path("/usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy")
_ASSETS = Path(__file__).resolve().parents[2] / "assets"
#: Where each file goes, what it is copied from, and its mode.
FILES: dict[Path, tuple[Path, int]] = {
    Path(HELPER): (_ASSETS / "trcc-ram-access", 0o755),
    Path(HELPER_OFF): (_ASSETS / "trcc-ram-access-off", 0o755),
    POLKIT_POLICY: (_ASSETS / "com.github.lexonight1.trcc.policy", 0o644),
}


def _owned_by_nothing(path: str) -> str | None:
    """No package manager to ask: nothing is known to own *path*."""
    log.debug("_owned_by_nothing: %s", path)
    return None


def install(dry_run: bool = False,
            owns: Callable[[str], str | None] = _owned_by_nothing,
            files: dict[Path, tuple[Path, int]] = FILES) -> int:
    """Copy the RAM-lighting helpers and the policy into place; 0 on success.

    *owns* names the package owning a path (``PackageManager.owns``).
    Re-runs itself as root via sudo when it is not root already.
    """
    log.info("install: dry_run=%s", dry_run)
    plan = [(dest, src, mode) for dest, (src, mode) in files.items()
            if _needs(dest, src, owns)]
    if dry_run:
        for dest, src, mode in plan:
            print(f"--- would install {src} -> {dest} ({oct(mode)}) ---")
        return 0
    if not plan:
        log.info("install: TRCC's polkit files are current")
        return 0
    if os.geteuid() != 0:
        return reexec_as_root(
            "from trcc.adapters.system._polkit_install import install;"
            " from trcc.adapters.system import current_platform;"
            " sys.exit(install(owns=current_platform().packages().owns))")
    try:
        for dest, src, mode in plan:
            _copy(src, dest, mode)
    except OSError:
        log.exception("install: could not install TRCC's polkit files")
        return 1
    return 0


def _needs(dest: Path, src: Path, owns: Callable[[str], str | None]) -> bool:
    """Whether *dest* should be written: missing or ours, and out of date."""
    try:
        current = dest.read_bytes()
    except OSError:
        log.info("_needs: %s missing", dest)
        return True
    if current == src.read_bytes():
        log.debug("_needs: %s current", dest)
        return False
    if (package := owns(str(dest))) is not None:
        log.info("_needs: %s belongs to %s -- left to the package", dest,
                 package)
        return False
    log.info("_needs: %s out of date", dest)
    return True


def _copy(src: Path, dest: Path, mode: int) -> None:
    """*src* to *dest* atomically, root-owned *mode*."""
    tmp = dest.with_name(f".{dest.name}.trcc-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(src.read_bytes())
        f.flush()
        os.fsync(f.fileno())
    Path(tmp).chmod(mode)
    Path(tmp).replace(dest)
    log.info("_copy: %s -> %s (%s)", src, dest, oct(mode))
