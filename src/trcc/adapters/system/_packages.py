"""Per-manager :class:`PackageManager` implementations.

``Rpm`` and ``Pacman`` are verified.  ``Rpm`` is written against the box this
was developed on (Fedora 44, dnf5 + rpm), measured rather than documented
(``Pacman``'s measurement is in its own docstring):

    rpm -q --whatprovides /usr/bin/7z    23 ms   local, no network
    rpm -q 7zip                          17 ms   local
    dnf -C provides /usr/bin/7z         717 ms   CACHE ONLY, no network

The others are **not written yet, on purpose**.  Coding apt/zypper/apk
from documentation is exactly what produced four wrong package names in the
static tables this port exists to replace, and a manager that answers
confidently and wrongly is worse than one that says it cannot tell.  Each
arrives when it can be run: on a real machine, or against a container, and
its tests replay what was measured (``tests/test_package_managers.py``).  This
said "with a case added to ``dev/tools/check_program_deps.py``" until
2026-11-04; that tool checks the static hints against the repos, never a
manager's queries, and ``Rpm`` had no case there nor any test.

Until then those OSes get :class:`NoPackageManager`, which answers "cannot
determine" to everything and leaves the caller on the static hint — the
behaviour they have today, stated instead of implied.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from ...core.ports import PackageManager
from ._elevate import run_steps

log = logging.getLogger(__name__)

#: Every query is bounded.  The doctor runs on a machine that is already
#: misbehaving; a package manager waiting on a stale lock must not hang it.
_TIMEOUT_S = 5.0


def _run(argv: list[str]) -> str | None:
    """stdout, or None when the command could not answer.

    None covers every "we do not know" — missing binary, non-zero exit, empty
    output, timeout — because the caller must not tell a user to install
    something on the strength of a failed lookup.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("_run: %s failed (%s)", argv[0], e)
        return None
    if proc.returncode != 0:
        log.debug("_run: %s exited %d", argv[0], proc.returncode)
        return None
    out = proc.stdout.strip()
    return out or None


class _InTerminal(PackageManager):
    """What every manager shares: running steps it was handed, in the user's
    terminal (``_elevate.run_steps``) -- written once, not per manager."""

    def run(self, steps: tuple[tuple[str, ...], ...]) -> int:
        log.info("%s.run: %d step(s)", type(self).__name__, len(steps))
        return run_steps(steps)

    def remove_line(self, package: str) -> str:
        """None to give -- a manager that has one says (``Pacman``)."""
        log.debug("%s.remove_line: none for %s", type(self).__name__, package)
        return ""

    def page(self, package: str) -> str:
        """No third-party collection with a page per package."""
        log.debug("%s.page: none for %s", type(self).__name__, package)
        return ""


class NoPackageManager(_InTerminal):
    """The honest default: this OS cannot be asked.

    Returns None everywhere, so callers fall back to the static install hint.
    Named rather than represented by ``None`` so the absence is a decision a
    reader can find, not a branch they have to infer.
    """

    def owns(self, path: str) -> str | None:
        log.debug("NoPackageManager.owns: not queryable on this OS (%s)", path)
        return None

    def provides(self, path: str) -> str | None:
        log.debug("NoPackageManager.provides: not queryable on this OS (%s)",
                  path)
        return None

    def installed(self, package: str) -> bool:
        log.debug("NoPackageManager.installed: not queryable (%s)", package)
        return False

    def install_argv(self, package: str) -> tuple[str, ...]:
        log.debug("NoPackageManager.install_argv: none for %s", package)
        return ()


class Rpm(_InTerminal):
    """Fedora / RHEL / CentOS Stream / Rocky / AlmaLinux.

    ``owns`` uses rpm rather than dnf: it reads the local rpmdb directly, needs
    no repository state at all, and is an order of magnitude faster.

    ``provides`` uses ``dnf -C`` — cache only.  Without ``-C``, dnf refreshes
    metadata, which on a slow or offline link turns a diagnostic into a hang.
    That is not hypothetical for this project: an inline 30 MB download on the
    connect path once delayed the main window by minutes (#275).
    """

    def owns(self, path: str) -> str | None:
        if not shutil.which("rpm"):
            return None
        out = _run(["rpm", "-q", "--whatprovides", path])
        # rpm answers "no package provides X" on stdout with a zero exit in
        # some versions, so the text has to be inspected, not just the code.
        if out is None or "no package" in out.lower():
            log.debug("Rpm.owns: nothing installed provides %s", path)
            return None
        package = out.splitlines()[0].strip()
        log.info("Rpm.owns: %s -> %s", path, package)
        return package

    def provides(self, path: str) -> str | None:
        if not shutil.which("dnf"):
            return None
        out = _run(["dnf", "-C", "--quiet", "repoquery", "--whatprovides",
                    path, "--qf", "%{name}\n"])
        if out is None:
            log.debug("Rpm.provides: dnf cache cannot answer for %s", path)
            return None
        name = out.splitlines()[0].strip()
        log.info("Rpm.provides: %s -> %s", path, name)
        return name

    def installed(self, package: str) -> bool:
        """``rpm -q`` — local rpmdb, no repository state, ~17 ms."""
        if not shutil.which("rpm"):
            return False
        present = _run(["rpm", "-q", package]) is not None
        log.info("Rpm.installed: %s -> %s", package, present)
        return present

    def install_argv(self, package: str) -> tuple[str, ...]:
        argv = ("sudo", "dnf", "install", package)
        log.debug("Rpm.install_argv: %s", " ".join(argv))
        return argv


class RpmOstree(Rpm):
    """Fedora Atomic -- Silverblue, Kinoite, Bazzite, Bluefin, Aurora.

    The rpmdb and dnf's cache answer as on Fedora; installing does not: the
    root is an image, so a package is LAYERED onto the next one with
    ``rpm-ostree install`` and arrives at the next boot (#338).
    """

    def install_argv(self, package: str) -> tuple[str, ...]:
        argv = ("rpm-ostree", "install", package)
        log.debug("RpmOstree.install_argv: %s", " ".join(argv))
        return argv


class Pacman(_InTerminal):
    """Arch and its family (CachyOS, Manjaro, EndeavourOS) -- ``pacman``.

    Measured 2026-11-04 in ``docker.io/library/archlinux`` (pacman 7.1.0):

        pacman -Qqo /usr/bin/pacman   "pacman", rc 0, ~11 ms, local db only
        pacman -Qqo <no owner>        rc 1, "error: No package owns <path>"
        pacman -Qq pacman             "pacman", rc 0
        pacman -Qq <no such name>     rc 1, "error: package '<name>' was not found"
        pacman -Fq /usr/bin/7z        "extra/7zip", rc 0 -- from the FILES db,
                                      which only ``pacman -Fy`` fetches; never
                                      fetched here.  Without it: rc 1, nothing.

    A missing sync database only adds a warning on stderr; the exit code is
    the answer, which is what :func:`_run` reads.  Needed first by #333: on
    pacman every file read as unowned, so the update hint could not tell an
    AUR package from TRCC's own.
    """

    def remove_line(self, package: str) -> str:
        """``pacman -R``: the package only, what depends on it untouched."""
        line = f"sudo pacman -R {package}"
        log.debug("Pacman.remove_line: %s", line)
        return line

    def page(self, package: str) -> str:
        """The AUR page -- where a package that is not TRCC's own (#337,
        honjow's rename of our PKGBUILD) and its maintainer are."""
        page = f"https://aur.archlinux.org/packages/{package}"
        log.debug("Pacman.page: %s", page)
        return page

    def owns(self, path: str) -> str | None:
        """``pacman -Qqo`` -- the local package database, ~11 ms."""
        if not shutil.which("pacman"):
            return None
        out = _run(["pacman", "-Qqo", path])
        if out is None:
            log.debug("Pacman.owns: no package owns %s", path)
            return None
        package = out.splitlines()[0].strip()
        log.info("Pacman.owns: %s -> %s", path, package)
        return package

    def provides(self, path: str) -> str | None:
        """``pacman -Fq`` -- the files database as it is; never synced here."""
        if not shutil.which("pacman"):
            return None
        out = _run(["pacman", "-Fq", path])
        if out is None:
            log.debug("Pacman.provides: the files db cannot answer for %s", path)
            return None
        name = out.splitlines()[0].strip().rsplit("/", 1)[-1]   # "extra/7zip"
        log.info("Pacman.provides: %s -> %s", path, name)
        return name

    def installed(self, package: str) -> bool:
        """``pacman -Qq`` -- the local package database."""
        if not shutil.which("pacman"):
            return False
        present = _run(["pacman", "-Qq", package]) is not None
        log.info("Pacman.installed: %s -> %s", package, present)
        return present

    def install_argv(self, package: str) -> tuple[str, ...]:
        argv = ("sudo", "pacman", "-S", package)
        log.debug("Pacman.install_argv: %s", " ".join(argv))
        return argv
