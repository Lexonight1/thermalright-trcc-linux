"""Which executable actually provides a tool on this machine.

The app used to name its external tools literally — ``shutil.which("ffmpeg")``,
``["7z", "x", …]`` — at ten call sites.  That works on the machines where the
distro happens to use those exact names, and fails everywhere else, in a way
no install hint can repair.

Measured 2026-08-21, both ends of the same defect:

* **Fedora.**  ``dnf install p7zip`` installs a working 7-Zip whose binary is
  ``7za``.  Every one of our sites looks for ``7z``, so the tool is present and
  unusable, and the doctor prints "install p7zip" again.  Verified: ``7za``
  lists and extracts our ``.7z`` archives perfectly — the program was fine, the
  *name* was the bug.
* **NetBSD.**  There is no ``ffmpeg`` package at all: pkgsrc ships ``ffmpeg4``
  through ``ffmpeg7``, each installing a *versioned* binary (``bin/ffmpeg7``).
  So even the correct install leaves every site failing, and pointing the hint
  at ``ffmpeg7`` would make the command succeed while the check still failed —
  the Fedora pathology again.

Resolving the name removes both, and removes the whole class: no per-distro
package knowledge is needed to find a program that is already installed.

**An alias here is a claim that must be proven, not a list someone typed.**
The first draft of this table excluded ``7za`` as "a reduced build" on the
strength of its shorter format list.  It supports 7z/zip/xz/tar/gzip/bzip2 —
every format we touch — and the app only ever handles ``.7z``.  Running it
settled in seconds what reading about it got wrong.
``tests/test_toolchain.py`` runs each alias present on the box against a real
archive, with the exact commands ``data_install`` uses.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

#: Logical tool -> executables that genuinely provide it, best first.
#:
#: Order is preference, not fallback-in-desperation: the unversioned name is
#: what a normal install gives, and the rest are what specific distros ship.
#:
#: ``7zr`` is included deliberately.  It is the smallest build and supports
#: little beyond the 7z format — which is the only format we use, and it was
#: verified to list and extract our archives.  Excluding it on reputation
#: would leave a machine that has it reporting "7z not on PATH".
TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    # pkgsrc versions the binary (bin/ffmpeg7); everyone else does not.
    "ffmpeg": ("ffmpeg", "ffmpeg7", "ffmpeg6", "ffmpeg5", "ffmpeg4"),
    "ffprobe": ("ffprobe", "ffprobe7", "ffprobe6", "ffprobe5", "ffprobe4"),
    # 7z: Debian/Arch/openSUSE.  7zz: modern upstream, Alpine + openSUSE.
    # 7za: what Fedora's p7zip installs.  7zr: minimal, 7z-format only.
    "7z": ("7z", "7zz", "7za", "7zr"),
}


def resolve(tool: str) -> str | None:
    """The executable name to invoke for *tool*, or None if none is present.

    Returns a NAME rather than a path: callers pass it as ``argv[0]`` and the
    OS resolves it again, which keeps behaviour identical to the hardcoded
    strings this replaced.

    An unknown tool falls back to its own name, so adding a
    ``resolve("newtool")`` call site cannot silently return None just because
    nobody added a row here.
    """
    for candidate in TOOL_ALIASES.get(tool, (tool,)):
        if shutil.which(candidate):
            if candidate != tool:
                log.info("toolchain.resolve: %s -> %s (alias)", tool, candidate)
            else:
                log.debug("toolchain.resolve: %s -> %s", tool, candidate)
            return candidate
    log.warning("toolchain.resolve: no executable for %r — tried %s",
                tool, ", ".join(TOOL_ALIASES.get(tool, (tool,))))
    return None


#: Directories a binary plausibly lives in while PATH does not include it.
#:
#: This is the difference between "install it" and "you already have it".  A
#: user whose PATH is minimal -- a systemd unit, a cron job, a stripped login
#: shell -- gets told to install software that is sitting on their disk, runs
#: the command, and lands back on the same message.  Same dead end as the
#: Fedora 7za bug, from a different direction.
#:
#: /usr/pkg/bin is pkgsrc (NetBSD); /snap/bin and /var/lib/flatpak/exports/bin
#: are the sandboxed installs that most often miss a service's PATH.
_OFF_PATH_DIRS: tuple[str, ...] = (
    "/usr/bin", "/usr/local/bin", "/usr/local/sbin", "/bin", "/sbin",
    "/usr/pkg/bin", "/opt/bin", "/snap/bin",
    "/var/lib/flatpak/exports/bin", "/usr/local/opt/bin",
)


def locate(tool: str) -> tuple[str | None, Path | None]:
    """``(name on PATH, path found off PATH)`` — at most one is set.

    Answers the question the health checks could not: is this missing, or
    merely unreachable?  Both used to render as "not on PATH" with an install
    hint, which is wrong advice for the second and wastes a round-trip.

    Pure filesystem, no subprocess and no package manager: asking rpm/dpkg
    which package owns it is a different, heavier question, and this one is
    answerable with ``is_file()``.
    """
    on_path = resolve(tool)
    if on_path is not None:
        return on_path, None
    for candidate in TOOL_ALIASES.get(tool, (tool,)):
        for directory in _OFF_PATH_DIRS:
            found = Path(directory) / candidate
            if found.is_file():
                log.warning("toolchain.locate: %s is at %s but not on PATH",
                            tool, found)
                return None, found
    log.debug("toolchain.locate: %s not found on or off PATH", tool)
    return None, None


def tried(tool: str) -> str:
    """The names looked for, so a report says what was searched.

    "ffmpeg not on PATH" does not tell a NetBSD reporter that ffmpeg7 was
    tried too, and that is exactly who needs to know.
    """
    names = ", ".join(TOOL_ALIASES.get(tool, (tool,)))
    log.debug("toolchain.tried: %s -> %s", tool, names)
    return names


def installed_elsewhere(distribution: str) -> str | None:
    """Version of *distribution*, if it is installed but not importable here.

    The module equivalent of "on disk but not on PATH", and it bites the same
    way: a reporter installs ``nvidia-ml-py`` into one interpreter, runs trcc
    under another, and is told to install it again (#207, #216).

    ``importlib.metadata`` sees distributions on ``sys.path``, so a hit here
    while the import fails means the package is visible and broken -- a wrong
    build, a partial install, a native dependency missing -- rather than
    absent.  Returns None when it genuinely is not there.
    """
    try:
        import importlib.metadata as md

        version = md.version(distribution)
    except Exception:                          # PackageNotFound and friends
        log.debug("toolchain.installed_elsewhere: %s not found", distribution)
        return None
    log.warning("toolchain.installed_elsewhere: %s %s is installed but did "
                "not import", distribution, version)
    return version


def present(tool: str) -> bool:
    """Whether *tool* is usable under any of its names."""
    found = resolve(tool) is not None
    log.debug("toolchain.present: %s -> %s", tool, found)
    return found
